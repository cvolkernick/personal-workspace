"""Edition store, schema, bucketing, Mac tasks builder, publish CLI (old and new paths)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date
from unittest import mock

from research.daily_brief.tests._common import FIXTURES, ROOT, TmpStore, sample, store
from research.daily_brief import cli


class TestSchema(unittest.TestCase):
    def test_fixtures_validate(self):
        for n in ("sample-am", "sample-pm", "sample-empty-todos"):
            self.assertEqual(store.validate(store.normalize(sample(n))), [], n)

    def test_bad_edition(self):
        probs = store.validate({"date": "2026-13-01", "edition": "noon", "published_at": "x"})
        self.assertTrue(any(p.startswith("date") for p in probs))
        self.assertTrue(any(p.startswith("edition") for p in probs))
        self.assertTrue(any(p.startswith("front_page") for p in probs))

    def test_done_todos_dropped_at_normalize(self):
        ed = sample("sample-am")
        ed["todos"].append({"title": "x", "status": "Done"})
        n = store.normalize(ed)
        self.assertFalse(any(t["status"] == "Done" for t in n["todos"]))

    def test_mac_tasks_validation(self):
        self.assertEqual(store.validate_mac_tasks(None), [])
        self.assertTrue(store.validate_mac_tasks({"source_url": "http://x"}))
        self.assertTrue(store.validate_mac_tasks({"page_found": "yes"}))
        self.assertTrue(store.validate_mac_tasks({"items": [{"text": " "}]}))


class TestStore(TmpStore):
    def test_default_store_path_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DAILY_BRIEF_DIR", None)
            os.environ.pop("FCC_BRIEF_DIR", None)
            self.assertTrue(str(store.store_dir()).endswith(".local/share/fcc/brief-editions"))

    def test_env_overrides(self):
        with mock.patch.dict(os.environ, {"FCC_BRIEF_DIR": str(self.root / "legacy")}):
            os.environ.pop("DAILY_BRIEF_DIR", None)
            self.assertEqual(store.store_dir(), self.root / "legacy")
            os.environ["DAILY_BRIEF_DIR"] = str(self.root / "new")
            self.assertEqual(store.store_dir(), self.root / "new")

    def test_eastern_filename_and_latest(self):
        ed = sample("sample-am")
        for k in ("date", "edition"):
            ed.pop(k)
        ed["published_at"] = "2026-10-10T02:30:00Z"  # 22:30 ET on Oct 9
        dest = store.publish(ed, root=self.root)
        self.assertEqual(dest.name, "2026-10-09-pm.json")
        self.pub(sample("sample-empty-todos"))
        self.assertEqual(store.latest_key(self.root), ("2026-10-09", "pm"))
        self.assertEqual(store.list_editions(self.root)[-1], ("2026-10-08", "pm"))

    def test_neighbors(self):
        self.pub_all()
        self.assertEqual(store.neighbors(("2026-10-09", "am"), self.root),
                         (("2026-10-08", "pm"), ("2026-10-09", "pm")))

    def test_edition_url_on_horizon_host(self):
        self.assertEqual(store.edition_url(("2026-10-09", "am")), "/daily-brief/2026-10-09/am")


class TestBucketing(unittest.TestCase):
    def test_buckets(self):
        today = date(2026, 10, 9)
        self.assertEqual(store.bucket_for("2026-10-06", today), "overdue")
        self.assertEqual(store.bucket_for("2026-10-09", today), "today")
        self.assertEqual(store.bucket_for("2026-10-13", today), "week")
        self.assertEqual(store.bucket_for("2026-11-15", today), "later")
        self.assertEqual(store.bucket_for(None, today), "nodate")
        self.assertEqual(store.bucket_for("2026-10-10T02:00:00Z", today), "today")


class TestMacTasksBuilder(unittest.TestCase):
    def test_from_notion_fixture(self):
        md = (FIXTURES / "notion-mac-tasks-page.md").read_text(encoding="utf-8")
        block = store.mac_tasks_from_markdown(md, fetched_at="2026-10-09T08:01:00-04:00")
        texts = [i["text"] for i in block["items"]]
        self.assertEqual(len(texts), 5)
        self.assertNotIn("SAMPLE: Already done, must not appear", texts)
        self.assertIn("SAMPLE nested: sub-step under the item above", texts)
        self.assertTrue(block["page_found"])

    def test_missing_page(self):
        block = store.mac_tasks_from_markdown(None, fetched_at="2026-10-09T08:01:00-04:00")
        self.assertFalse(block["page_found"])
        self.assertEqual(block["items"], [])


class TestPublishCli(TmpStore):
    def _env(self):
        return mock.patch.dict(os.environ, {"DAILY_BRIEF_DIR": str(self.root)})

    def test_cli_publish_writes_edition(self):
        with self._env():
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["publish", str(FIXTURES / "sample-am.json")])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertTrue(out["ok"])
        self.assertEqual(out["url"], "/daily-brief/2026-10-09/am")
        self.assertTrue(out["public_url"].endswith("/daily-brief/2026-10-09/am"))
        written = json.loads((self.root / "2026-10-09-am.json").read_text(encoding="utf-8"))
        self.assertEqual(written["edition"], "am")

    def test_cli_rejects_bad_edition(self):
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"date": "2026-10-09"}), encoding="utf-8")
        with self._env():
            with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(cli.main(["publish", str(bad)]), 2)
        self.assertFalse((self.root / "2026-10-09-am.json").exists())

    def _run(self, argv, stdin_path=None):
        env = dict(os.environ, DAILY_BRIEF_DIR=str(self.root))
        env.pop("FCC_BRIEF_DIR", None)
        with open(stdin_path, "rb") if stdin_path else open(os.devnull, "rb") as fh:
            return subprocess.run([sys.executable, *argv], stdin=fh, capture_output=True,
                                  env=env, cwd=str(ROOT), timeout=60)

    def test_legacy_shim_publish_from_stdin(self):
        # The routine's command shape: python3 financial-command/brief.py publish - < edition.json
        r = self._run([str(ROOT / "financial-command" / "brief.py"), "publish", "-"],
                      FIXTURES / "sample-pm.json")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertTrue(json.loads(r.stdout)["ok"])
        self.assertTrue((self.root / "2026-10-09-pm.json").is_file())

    def test_module_publish_and_list(self):
        r = self._run(["-m", "research.daily_brief", "publish", str(FIXTURES / "sample-empty-todos.json")])
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        r = self._run(["-m", "research.daily_brief", "list"])
        self.assertIn(b"2026-10-08 pm /daily-brief/2026-10-08/pm", r.stdout)

    def test_mac_tasks_cli(self):
        r = self._run([str(ROOT / "financial-command" / "brief.py"), "mac-tasks", "--missing"])
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertFalse(json.loads(r.stdout)["page_found"])


if __name__ == "__main__":
    unittest.main()
