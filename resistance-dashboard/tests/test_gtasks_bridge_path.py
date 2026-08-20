"""Vercel loads in-repo google_tasks.py. Nest path still wins on Pi."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard import gtasks_bridge as gtb
from rt_dashboard.gtasks_bridge import (
    _MOD_NAME,
    _google_tasks_candidates,
    credentials_status,
    load_google_tasks,
)

ROOT = Path(__file__).resolve().parents[1]
NEST = Path(__file__).resolve().parents[2] / "projects-dashboard" / "google_tasks.py"
BUNDLE = ROOT / "projects-dashboard" / "google_tasks.py"
VERCEL_JSON = ROOT / "vercel.json"


def _drop_cached_mod() -> None:
    sys.modules.pop(_MOD_NAME, None)


class InRepoGoogleTasksBundle(unittest.TestCase):
    def test_bundle_copy_matches_nest_sot(self):
        self.assertTrue(NEST.is_file(), NEST)
        self.assertTrue(BUNDLE.is_file(), BUNDLE)
        self.assertEqual(BUNDLE.read_bytes(), NEST.read_bytes())

    def test_bundle_has_no_secrets(self):
        raw = BUNDLE.read_text(encoding="utf-8")
        self.assertNotIn("1//", raw)
        self.assertNotIn("ya29.", raw)
        self.assertNotIn("~/.config/google-tasks-mcp", raw.split("Uses the same")[0])

    def test_dashboard_include_files_lists_google_tasks(self):
        raw = VERCEL_JSON.read_text(encoding="utf-8")
        self.assertIn("projects-dashboard/google_tasks.py", raw)
        cfg = json.loads(raw)
        dash = cfg["functions"]["api/dashboard.py"]["includeFiles"]
        self.assertIn("projects-dashboard/google_tasks.py", dash)
        # No new function — daily-tasks stay rewritten onto dashboard.
        self.assertNotIn("api/daily-tasks.py", raw)
        self.assertEqual(
            cfg.get("ignoreCommand"),
            "python3 scripts/vercel_ignore.py || exit 1",
        )


class PathFallback(unittest.TestCase):
    def tearDown(self) -> None:
        _drop_cached_mod()
        sys.modules.pop("google_tasks", None)

    def test_nest_candidate_wins_when_present(self):
        cands = _google_tasks_candidates()
        nest = NEST.resolve()
        bundle = BUNDLE.resolve()
        self.assertIn(nest, cands)
        self.assertIn(bundle, cands)
        self.assertLess(cands.index(nest), cands.index(bundle))

    def test_vercel_layout_loads_in_repo_bundle(self):
        missing = Path("/var/projects-dashboard/google_tasks.py")
        self.assertFalse(missing.is_file())
        _drop_cached_mod()
        with mock.patch.object(
            gtb,
            "_google_tasks_candidates",
            return_value=[missing, BUNDLE.resolve()],
        ):
            gt = load_google_tasks()
        self.assertTrue(hasattr(gt, "credentials_status"))
        self.assertTrue(hasattr(gt, "complete_task"))

    def test_pythonpath_import_when_files_missing(self):
        _drop_cached_mod()
        sys.modules.pop("google_tasks", None)
        nest_dir = str(NEST.parent)
        with mock.patch.object(gtb, "_google_tasks_candidates", return_value=[]):
            with mock.patch.object(sys, "path", [nest_dir, *sys.path]):
                gt = load_google_tasks()
        self.assertTrue(hasattr(gt, "credentials_status"))

    def test_missing_all_paths_is_honest_not_var_only(self):
        _drop_cached_mod()
        sys.modules.pop("google_tasks", None)
        missing = Path("/var/projects-dashboard/google_tasks.py")
        with mock.patch.object(
            gtb, "_google_tasks_candidates", return_value=[missing]
        ), mock.patch.object(gtb, "_import_google_tasks_from_sys_path", return_value=None):
            with self.assertRaises(FileNotFoundError) as ctx:
                load_google_tasks()
        msg = str(ctx.exception)
        self.assertNotEqual(
            msg, "google_tasks.py not found at /var/projects-dashboard/google_tasks.py"
        )
        self.assertIn("FitDash bundle", msg)
        self.assertNotIn("/var/projects-dashboard", msg)


class HonestCredentialsAfterImport(unittest.TestCase):
    def test_no_env_is_not_ok_and_not_a_fake_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "GOOGLE_TASKS_CONFIG_DIR": tmp,
                    "GOOGLE_TASKS_TOKEN_JSON": "",
                    "GOOGLE_TASKS_REFRESH_TOKEN": "",
                    "GOOGLE_TASKS_CLIENT_ID": "",
                    "GOOGLE_TASKS_CLIENT_SECRET": "",
                },
                clear=False,
            ):
                os.environ.pop("GOOGLE_TASKS_TOKEN_JSON", None)
                os.environ.pop("GOOGLE_TASKS_REFRESH_TOKEN", None)
                status = credentials_status()
        self.assertFalse(status["ok"])
        err = str(status.get("error") or "")
        self.assertNotIn("/var/projects-dashboard", err)
        self.assertNotIn("google_tasks.py not found", err)
        self.assertTrue(err)
        dumped = json.dumps(status)
        self.assertNotIn("1//", dumped)
        self.assertNotIn("ya29.", dumped)


if __name__ == "__main__":
    unittest.main()
