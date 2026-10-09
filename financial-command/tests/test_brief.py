"""Daily Brief newspaper on FCC (#1091): schema, store, bucketing, routes."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FCC = ROOT / "financial-command"
SAMPLES = FCC / "brief-samples"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


brief = _load("fcc_brief_under_test", FCC / "brief.py")


def sample(name: str) -> dict:
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))


class TmpStore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def pub(self, ed: dict) -> Path:
        return brief.publish(copy.deepcopy(ed), root=self.root)


class TestSchema(unittest.TestCase):
    def test_samples_valid(self):
        for n in ("sample-am", "sample-pm", "sample-empty-todos"):
            self.assertEqual(brief.validate(brief.normalize(sample(n))), [], n)

    def test_required_fields(self):
        probs = brief.validate({"date": "2026-13-01", "edition": "noon", "published_at": "x"})
        joined = " ".join(probs)
        for needle in ("date:", "edition:", "published_at:", "front_page:", "sections:", "todos:"):
            self.assertIn(needle, joined)

    def test_todos_schema(self):
        ed = brief.normalize(sample("sample-am"))
        ed["todos"] = [
            {"title": "", "status": "Not started"},
            {"title": "x", "status": "Blocked"},
            {"title": "y", "status": "In progress", "due": "next week"},
            {"title": "z", "status": "Not started", "notion_url": "javascript:alert(1)"},
            {"title": "w", "status": "Not started", "owner": 5},
        ]
        probs = brief.validate(ed)
        self.assertTrue(any("todos[0].title" in p for p in probs))
        self.assertTrue(any("todos[1].status" in p for p in probs))
        self.assertTrue(any("todos[2].due" in p for p in probs))
        self.assertTrue(any("todos[3].notion_url" in p for p in probs))
        self.assertTrue(any("todos[4].owner" in p for p in probs))

    def test_todos_accept_datetime_due_and_null_fields(self):
        ed = brief.normalize(sample("sample-am"))
        ed["todos"] = [
            {"title": "a", "status": "Not started", "due": "2026-10-10T09:00:00.000-04:00",
             "owner": None, "priority": None, "notion_url": "https://www.notion.so/a"},
            {"title": "b", "status": "In progress", "due": None},
        ]
        self.assertEqual(brief.validate(ed), [])

    def test_done_rows_dropped_by_normalize(self):
        ed = sample("sample-am")
        ed["todos"].append({"title": "done", "status": "Done"})
        n = brief.normalize(ed)
        self.assertNotIn("done", [t["title"] for t in n["todos"]])
        self.assertEqual(brief.validate(n), [])
        raw = brief.normalize(sample("sample-am"))
        raw["todos"].append({"title": "done", "status": "Done"})
        self.assertTrue(any("Done" in p for p in brief.validate(raw)))

    def test_non_http_links_rejected(self):
        ed = brief.normalize(sample("sample-am"))
        ed["sections"][0]["items"].append({"text": "bad", "link": "file:///etc/passwd"})
        ed["front_page"]["image"] = "data:image/png;base64,AAA"
        probs = brief.validate(ed)
        self.assertTrue(any("items" in p and "link" in p for p in probs))
        self.assertTrue(any("front_page.image" in p for p in probs))

    def test_duplicate_section_ids(self):
        ed = brief.normalize(sample("sample-am"))
        ed["sections"].append({"id": "fleet", "title": "Fleet 2", "items": []})
        self.assertTrue(any("duplicate" in p for p in brief.validate(ed)))


class TestEasternFilenames(TmpStore):
    def test_filename_format(self):
        self.assertEqual(brief.edition_filename("2026-10-09", "am"), "2026-10-09-am.json")
        with self.assertRaises(brief.BriefError):
            brief.edition_filename("2026-10-9", "am")
        with self.assertRaises(brief.BriefError):
            brief.edition_filename("2026-10-09", "noon")
        self.assertEqual(brief.parse_filename("2026-10-09-pm.json"), ("2026-10-09", "pm"))
        self.assertIsNone(brief.parse_filename("2026-02-30-am.json"))
        self.assertIsNone(brief.parse_filename("notes.json"))

    def test_late_evening_utc_maps_to_eastern_date(self):
        # 01:30 UTC on the 10th is 21:30 EDT on the 9th: pm edition of 10-09.
        ed = sample("sample-pm")
        del ed["date"], ed["edition"]
        ed["published_at"] = "2026-10-10T01:30:00Z"
        dest = self.pub(ed)
        self.assertEqual(dest.name, "2026-10-09-pm.json")

    def test_morning_derivation_and_winter_offset(self):
        ed = sample("sample-am")
        del ed["date"], ed["edition"]
        ed["published_at"] = "2026-12-15T13:05:00Z"  # 08:05 EST
        self.assertEqual(self.pub(ed).name, "2026-12-15-am.json")

    def test_missing_published_at_uses_now_eastern(self):
        ed = sample("sample-am")
        del ed["date"], ed["edition"], ed["published_at"]
        now = datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc)  # 23:00 ET on 10-09
        dest = brief.publish(ed, root=self.root, now=now)
        self.assertEqual(dest.name, "2026-10-09-pm.json")

    def test_store_dir_env_override(self):
        old = os.environ.get("FCC_BRIEF_DIR")
        try:
            os.environ["FCC_BRIEF_DIR"] = str(self.root / "x")
            self.assertEqual(brief.store_dir(), self.root / "x")
            del os.environ["FCC_BRIEF_DIR"]
            self.assertTrue(str(brief.store_dir()).endswith(".local/share/fcc/brief-editions"))
        finally:
            if old is not None:
                os.environ["FCC_BRIEF_DIR"] = old


class TestStoreAndLatest(TmpStore):
    def test_latest_prefers_pm_same_day_then_newest_date(self):
        self.pub(sample("sample-empty-todos"))  # 10-08 pm
        self.pub(sample("sample-am"))  # 10-09 am
        self.assertEqual(brief.latest_key(self.root), ("2026-10-09", "am"))
        self.pub(sample("sample-pm"))  # 10-09 pm
        self.assertEqual(brief.latest_key(self.root), ("2026-10-09", "pm"))
        self.assertEqual(
            brief.list_editions(self.root),
            [("2026-10-09", "pm"), ("2026-10-09", "am"), ("2026-10-08", "pm")],
        )

    def test_latest_ignores_junk_files(self):
        self.pub(sample("sample-am"))
        (self.root / "2099-01-01-noon.json").write_text("{}")
        (self.root / "README").write_text("x")
        self.assertEqual(brief.latest_key(self.root), ("2026-10-09", "am"))

    def test_empty_store(self):
        self.assertIsNone(brief.latest_key(self.root / "missing"))

    def test_republish_overwrites(self):
        self.pub(sample("sample-am"))
        ed = sample("sample-am")
        ed["front_page"]["headline"] = "Corrected headline"
        self.pub(ed)
        self.assertEqual(len(list(self.root.glob("*.json"))), 1)
        self.assertEqual(
            brief.load_edition("2026-10-09", "am", self.root)["front_page"]["headline"],
            "Corrected headline",
        )

    def test_invalid_publish_writes_nothing(self):
        ed = sample("sample-am")
        ed["edition"] = "noon"
        with self.assertRaises(brief.BriefError):
            self.pub(ed)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_neighbors(self):
        for n in ("sample-empty-todos", "sample-am", "sample-pm"):
            self.pub(sample(n))
        self.assertEqual(
            brief.neighbors(("2026-10-09", "am"), self.root),
            (("2026-10-08", "pm"), ("2026-10-09", "pm")),
        )
        self.assertEqual(brief.neighbors(("2026-10-08", "pm"), self.root)[0], None)
        self.assertEqual(brief.neighbors(("2026-10-09", "pm"), self.root)[1], None)


class TestBucketing(unittest.TestCase):
    today = date(2026, 10, 9)

    def test_buckets(self):
        b = brief.bucket_for
        self.assertEqual(b("2026-10-08", self.today), "overdue")
        self.assertEqual(b("2026-10-09", self.today), "today")
        self.assertEqual(b("2026-10-10", self.today), "week")
        self.assertEqual(b("2026-10-16", self.today), "week")
        self.assertEqual(b("2026-10-17", self.today), "later")
        self.assertEqual(b(None, self.today), "nodate")
        self.assertEqual(b("", self.today), "nodate")

    def test_datetime_due_uses_eastern_date(self):
        # 02:00 UTC on 10-10 is 22:00 ET on 10-09: due today, not this week.
        self.assertEqual(brief.bucket_for("2026-10-10T02:00:00Z", self.today), "today")
        self.assertEqual(brief.bucket_for("2026-10-09T03:59:00Z", self.today), "overdue")

    def test_sample_has_one_in_each_bucket(self):
        groups = brief.bucket_todos(sample("sample-am")["todos"], self.today)
        self.assertEqual({k: len(v) for k, v in groups.items()},
                         {"overdue": 1, "today": 1, "week": 1, "later": 1, "nodate": 1})

    def test_done_excluded_and_sorted(self):
        todos = [
            {"title": "b", "status": "Not started", "due": "2026-10-12"},
            {"title": "a", "status": "Not started", "due": "2026-10-11"},
            {"title": "x", "status": "Done", "due": "2026-10-11"},
        ]
        g = brief.bucket_todos(todos, self.today)
        self.assertEqual([t["title"] for t in g["week"]], ["a", "b"])


class TestRender(TmpStore):
    def setUp(self):
        super().setUp()
        for n in ("sample-empty-todos", "sample-am", "sample-pm"):
            self.pub(sample(n))

    def get(self, path):
        return brief.route(path, root=self.root, now=datetime(2026, 10, 9, 21, 0, tzinfo=brief.ET))

    def test_latest_route_shows_newest(self):
        code, ctype, body = self.get("/brief")
        self.assertEqual(code, 200)
        self.assertIn("Evening Edition", body)
        self.assertIn("SAMPLE: Evening edition", body)
        self.assertNotIn('class="stale"', body)

    def test_permalinks(self):
        for path, needle in (("/brief/2026-10-09/am", "Morning Edition"),
                             ("/brief/2026-10-09/pm", "Evening Edition"),
                             ("/brief/2026-10-08/pm/", "Nothing open")):
            code, _, body = self.get(path)
            self.assertEqual(code, 200, path)
            self.assertIn(needle, body, path)
        self.assertEqual(self.get("/brief/2026-01-01/am")[0], 404)
        self.assertEqual(self.get("/brief/nope")[0], 404)
        self.assertIsNone(self.get("/fleet/"))

    def test_layout_parts(self):
        _, _, body = self.get("/brief/2026-10-09/am")
        for needle in ('class="masthead"', 'class="dateline"', "Vol. 1", "No. 2",
                       "Friday, October 9, 2026", "Fort Myers, FL", 'class="cards"',
                       "Reminders", 'class="pills"', 'class="front"', "The Day Ahead",
                       'id="sec-todo"', "Business", "Fleet", "Shipped", "Drafted Replies",
                       "prefers-color-scheme:dark", 'name="viewport"'):
            self.assertIn(needle, body)
        # To-Do lands right after The Day Ahead
        self.assertLess(body.index('id="sec-day-ahead"'), body.index('id="sec-todo"'))
        self.assertLess(body.index('id="sec-todo"'), body.index('id="sec-business"'))

    def test_todo_grouping_overdue_red_and_notion_links(self):
        _, _, body = self.get("/brief/2026-10-09/am")
        order = [body.index(f'data-bucket="{b}"') for b in brief.BUCKETS]
        self.assertEqual(order, sorted(order))
        self.assertIn('<li class="todo-item overdue"><a class="todo-title" href="https://www.notion.so/sample-overdue" target="_blank"', body)
        self.assertEqual(body.count("todo-item overdue"), 1)
        for slug in ("today", "week", "later", "nodate"):
            self.assertIn(f'href="https://www.notion.so/sample-{slug}"', body)
        self.assertIn("2 due", body)  # reminders: overdue + due today
        self.assertIn("High", body)
        self.assertIn("In progress", body)

    def test_pm_todo_title(self):
        _, _, body = self.get("/brief/2026-10-09/pm")
        self.assertIn("To-Do (coming due)", body)
        self.assertIn("Tomorrow", body)

    def test_empty_todos_nothing_open(self):
        _, _, body = self.get("/brief/2026-10-08/pm")
        self.assertIn('<p class="empty">Nothing open</p>', body)

    def test_prev_next_links(self):
        _, _, body = self.get("/brief/2026-10-09/am")
        self.assertIn('rel="prev" href="/brief/2026-10-08/pm"', body)
        self.assertIn('rel="next" href="/brief/2026-10-09/pm"', body)
        _, _, first = self.get("/brief/2026-10-08/pm")
        self.assertNotIn('rel="prev"', first)

    def test_archive_by_eastern_date(self):
        code, _, body = self.get("/brief/archive")
        self.assertEqual(code, 200)
        self.assertIn("October 2026", body)
        self.assertLess(body.index("Friday, October 9, 2026"), body.index("Thursday, October 8, 2026"))
        for href in ("/brief/2026-10-09/am", "/brief/2026-10-09/pm", "/brief/2026-10-08/pm"):
            self.assertIn(f'href="{href}"', body)

    def test_html_escaped(self):
        ed = sample("sample-am")
        ed["date"] = "2026-10-10"
        ed["front_page"]["headline"] = "<script>alert(1)</script>"
        ed["todos"][0]["title"] = '"><img src=x onerror=alert(1)>'
        self.pub(ed)
        _, _, body = self.get("/brief/2026-10-10/am")
        self.assertNotIn("<script>alert", body)
        self.assertNotIn("<img src=x", body)

    def test_stale_banner_when_publisher_down(self):
        _, _, body = brief.route("/brief", root=self.root,
                                 now=datetime(2026, 10, 11, 9, 0, tzinfo=brief.ET))
        self.assertIn('class="stale"', body)

    def test_empty_store_page(self):
        code, _, body = brief.route("/brief", root=self.root / "none")
        self.assertEqual(code, 200)
        self.assertIn("No edition published yet", body)

    def test_api_latest(self):
        code, ctype, body = self.get("/api/brief/latest")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["url"], "/brief/2026-10-09/pm")
        self.assertEqual(data["edition"]["edition"], "pm")


class TestCli(TmpStore):
    def test_publish_cli(self):
        old = os.environ.get("FCC_BRIEF_DIR")
        os.environ["FCC_BRIEF_DIR"] = str(self.root)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = brief.main(["publish", str(SAMPLES / "sample-am.json")])
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertEqual(out["url"], "/brief/2026-10-09/am")
            self.assertTrue((self.root / "2026-10-09-am.json").is_file())
            bad = self.root / "bad.json"
            bad.write_text('{"edition": "am"}')
            with redirect_stdout(io.StringIO()):
                import contextlib
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(brief.main(["publish", str(bad)]), 2)
        finally:
            if old is None:
                os.environ.pop("FCC_BRIEF_DIR", None)
            else:
                os.environ["FCC_BRIEF_DIR"] = old


class TestServerIntegration(TmpStore):
    def test_fcc_server_serves_brief_routes(self):
        old = os.environ.get("FCC_BRIEF_DIR")
        os.environ["FCC_BRIEF_DIR"] = str(self.root)
        for n in ("sample-empty-todos", "sample-am", "sample-pm"):
            self.pub(sample(n))
        try:
            server_mod = _load("fcc_server_brief_test", FCC / "server.py")
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.FCCHandler)
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            try:
                for path in ("/brief", "/brief/2026-10-09/am", "/brief/2026-10-09/pm", "/brief/archive"):
                    with urllib.request.urlopen(base + path, timeout=10) as r:
                        self.assertEqual(r.status, 200, path)
                        self.assertIn("text/html", r.headers["Content-Type"])
                        self.assertIn(b"The Daily Brief", r.read())
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(base + "/brief/2020-01-01/am", timeout=10)
                self.assertEqual(cm.exception.code, 404)
                req = urllib.request.Request(base + "/brief", method="HEAD")
                with urllib.request.urlopen(req, timeout=10) as r:
                    self.assertEqual(r.status, 200)
            finally:
                httpd.shutdown()
                httpd.server_close()
        finally:
            if old is None:
                os.environ.pop("FCC_BRIEF_DIR", None)
            else:
                os.environ["FCC_BRIEF_DIR"] = old


class TestNavEntry(unittest.TestCase):
    def test_brief_nav_wired_by_shared_nav_script(self):
        # index.html (232 KB) is untouched; nav-horizon.js, loaded by every FCC
        # surface, injects the header link and the tab-bar entry.
        js = (FCC / "nav-horizon.js").read_text(encoding="utf-8")
        self.assertIn('"/brief"', js)
        self.assertIn('getElementById("mobile-tabbar")', js)
        self.assertIn('[data-m-tab="more"]', js)
        self.assertIn('"nav-brief"', js)
        self.assertIn("Brief", js)
        self.assertNotIn('data-m-tab="brief', js)
        self.assertNotIn("http://", js)
        html = (FCC / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="mobile-tabbar"', html)
        self.assertIn('data-m-tab="more"', html)
        self.assertIn('nav-horizon.js', html)


if __name__ == "__main__":
    unittest.main()
