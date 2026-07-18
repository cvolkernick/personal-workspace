"""Tests for personal-workspace monorepo dashboard + Grok area matching."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

from workspace import (  # noqa: E402
    area_for_workspace_file,
    collect_repo_status,
    collect_workspace_dashboard,
    path_is_dirty,
    work_area_for_tld,
    work_branch_for_tld,
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


class TestWorkAreaMap(unittest.TestCase):
    def test_finance_tlds_share_treasury_branch(self):
        for tld in ("treasury", "financial-command", "investment", "research"):
            self.assertEqual(work_area_for_tld(tld), "treasury")
            self.assertEqual(work_branch_for_tld(tld), "work/treasury")

    def test_fitness_aliases_resistance(self):
        self.assertEqual(work_area_for_tld("fitness"), "resistance-dashboard")
        self.assertEqual(work_branch_for_tld("fitness"), "work/resistance-dashboard")

    def test_meta_has_no_work_branch(self):
        self.assertEqual(work_area_for_tld("strategy"), "_meta")
        self.assertIsNone(work_branch_for_tld("strategy"))


class TestWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="ws-dash-")
        self.root = Path(self._td.name)
        self.ws = self.root / "personal-workspace"
        self.ws.mkdir()
        _git(self.ws, "init", "-b", "master")
        # monorepo areas
        (self.ws / "resistance-dashboard").mkdir()
        (self.ws / "resistance-dashboard" / "server.py").write_text("# rd\n", encoding="utf-8")
        (self.ws / "financial-command").mkdir()
        (self.ws / "financial-command" / "index.html").write_text("<html/>\n", encoding="utf-8")
        (self.ws / "treasury").mkdir()
        (self.ws / "README.md").write_text("hi\n", encoding="utf-8")
        _git(self.ws, "add", ".")
        _git(self.ws, "commit", "-m", "init")
        _git(self.ws, "remote", "add", "origin", "https://github.com/example/personal-workspace.git")

        # Grok home + session that edited resistance-dashboard
        self.grok = self.root / "grok"
        sid = "019f0000-aaaa-bbbb-cccc-ddddeeeeffff"
        sdir = self.grok / "sessions" / "%2Fworkspace" / sid
        sdir.mkdir(parents=True)
        (sdir / "summary.json").write_text(
            json.dumps(
                {
                    "generated_title": "Build Resistance Dashboard",
                    "last_active_at": "2026-07-17T12:00:00Z",
                    "current_model_id": "grok-4.5",
                    "num_chat_messages": 20,
                    "info": {"id": sid, "cwd": str(self.ws)},
                }
            ),
            encoding="utf-8",
        )
        hunks = [
            {"filePath": str(self.ws / "resistance-dashboard" / "server.py")},
            {"filePath": str(self.ws / "README.md")},  # _root
            {"filePath": str(self.grok / "noise.md")},  # outside ws
        ]
        with (sdir / "hunk_records.jsonl").open("w", encoding="utf-8") as f:
            for h in hunks:
                f.write(json.dumps(h) + "\n")
        (self.grok / "active_sessions.json").write_text(
            json.dumps([{"session_id": sid, "pid": 1, "cwd": str(self.ws)}]),
            encoding="utf-8",
        )

        # orphan session
        oid = "019f0000-orphan-0000-0000-000000000001"
        odir = self.grok / "sessions" / "%2Fworkspace" / oid
        odir.mkdir(parents=True)
        (odir / "summary.json").write_text(
            json.dumps(
                {
                    "generated_title": "Unrelated Chat",
                    "last_active_at": "2026-07-10T00:00:00Z",
                    "info": {"id": oid, "cwd": "/tmp"},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_area_mapping(self) -> None:
        self.assertEqual(
            area_for_workspace_file(
                str(self.ws / "resistance-dashboard" / "server.py"), self.ws
            ),
            "resistance-dashboard",
        )
        self.assertEqual(
            area_for_workspace_file(str(self.ws / "README.md"), self.ws),
            "_root",
        )
        self.assertIsNone(
            area_for_workspace_file(str(self.grok / "noise.md"), self.ws)
        )

    def test_strategy_is_meta_not_project(self) -> None:
        from workspace import META_CONTENT_DIRS, known_project_dirs, load_strategy_focus

        (self.ws / "strategy").mkdir(exist_ok=True)
        (self.ws / "strategy" / "today.md").write_text(
            "# Today\n- [ ] Ship something useful\n- [x] Already done\n",
            encoding="utf-8",
        )
        (self.ws / "initiatives").mkdir(exist_ok=True)
        names = {p.name for p in known_project_dirs(self.ws)}
        self.assertNotIn("strategy", names)
        self.assertNotIn("initiatives", names)
        self.assertIn("resistance-dashboard", names)
        self.assertEqual(
            area_for_workspace_file(str(self.ws / "strategy" / "today.md"), self.ws),
            "_meta",
        )
        focus = load_strategy_focus(self.ws)
        self.assertEqual(focus["open_count"], 1)
        self.assertEqual(focus["done_count"], 1)
        self.assertIn("Ship something", focus["open_items"][0])
        self.assertIn("strategy", META_CONTENT_DIRS)

    def test_path_is_dirty(self) -> None:
        paths = ["resistance-dashboard/server.py", "README.md"]
        self.assertTrue(path_is_dirty("resistance-dashboard", paths))
        self.assertFalse(path_is_dirty("treasury", paths))

    def test_repo_status_clean(self) -> None:
        st = collect_repo_status(self.ws)
        self.assertTrue(st["is_git"])
        self.assertEqual(st["branch"], "master")
        self.assertFalse(st["dirty"])
        self.assertEqual(st["remotes"][0]["name"], "origin")

    def test_repo_status_dirty(self) -> None:
        (self.ws / "financial-command" / "x.txt").write_text("x\n", encoding="utf-8")
        st = collect_repo_status(self.ws)
        self.assertTrue(st["dirty"])
        self.assertTrue(any("financial-command" in p for p in st["dirty_paths"]))

    def test_dashboard_maps_sessions_to_areas(self) -> None:
        payload = collect_workspace_dashboard(
            workspace=self.ws, grok_home=self.grok
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "workflow-management")
        self.assertEqual(payload["workspace"]["name"], "personal-workspace")
        names = {p["name"]: p for p in payload["projects"]}
        self.assertIn("resistance-dashboard", names)
        self.assertIn("financial-command", names)
        rd = names["resistance-dashboard"]
        self.assertGreaterEqual(rd["edit_count"], 1)
        self.assertEqual(rd["session_count"], 1)
        self.assertTrue(rd["sessions"][0]["active"])
        self.assertEqual(rd["sessions"][0]["title"], "Build Resistance Dashboard")
        self.assertTrue(rd["sessions"][0].get("persisted"))
        self.assertIn("grok --resume", rd["sessions"][0].get("resume_cmd", ""))
        self.assertTrue(rd.get("exit_ready"))  # clean tree
        # financial-command exists but no grok edits
        self.assertEqual(names["financial-command"]["edit_count"], 0)
        # only_touched filters
        touched = collect_workspace_dashboard(
            workspace=self.ws, grok_home=self.grok, only_touched=True
        )
        tnames = {p["name"] for p in touched["projects"]}
        self.assertIn("resistance-dashboard", tnames)
        self.assertNotIn("financial-command", tnames)
        # orphans
        self.assertTrue(
            any(s["title"] == "Unrelated Chat" for s in payload["orphan_sessions"])
        )
        # readiness + resume kit
        self.assertIn(payload["readiness"]["verdict"], ("ready", "caution", "blocked"))
        self.assertTrue(payload["readiness"]["checks"])
        self.assertTrue(payload["readiness"]["exit_steps"])
        self.assertTrue(payload["resume_kit"]["sessions"])
        self.assertIn("sessions_disk", {c["id"] for c in payload["readiness"]["checks"]})

    def test_readiness_warns_on_dirty(self) -> None:
        (self.ws / "financial-command" / "x.txt").write_text("x\n", encoding="utf-8")
        payload = collect_workspace_dashboard(
            workspace=self.ws, grok_home=self.grok
        )
        self.assertEqual(payload["readiness"]["verdict"], "caution")
        uncommitted = next(
            c for c in payload["readiness"]["checks"] if c["id"] == "uncommitted"
        )
        self.assertEqual(uncommitted["level"], "warn")
        fc = next(p for p in payload["projects"] if p["name"] == "financial-command")
        self.assertFalse(fc["exit_ready"])


if __name__ == "__main__":
    unittest.main()
