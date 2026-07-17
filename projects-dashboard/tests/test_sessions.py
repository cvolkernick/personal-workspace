"""Tests for Grok session → project matching (real fixture layout)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DASH_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH_ROOT))

from sessions import (  # noqa: E402
    collect_grok_projects,
    collect_session_project_map,
    project_root_for_file,
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
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )


class TestSessions(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="projdash-sess-")
        self.root = Path(self._td.name)
        # Fake grok home + a real git project
        self.grok = self.root / "grok-home"
        self.proj = self.root / "my-app"
        self.proj.mkdir(parents=True)
        _git(self.proj, "init", "-b", "main")
        (self.proj / "src").mkdir()
        (self.proj / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
        _git(self.proj, "add", ".")
        _git(self.proj, "commit", "-m", "init")
        _git(self.proj, "remote", "add", "origin", "https://github.com/example/my-app.git")

        # Session layout: sessions/%2Ftmp.../sid/
        enc = "%2Fworkspace"
        self.sdir = self.grok / "sessions" / enc / "019f0000-aaaa-bbbb-cccc-ddddeeeeffff"
        self.sdir.mkdir(parents=True)
        summary = {
            "generated_title": "Build My App Feature",
            "session_summary": "Build My App Feature",
            "created_at": "2026-07-01T00:00:00Z",
            "last_active_at": "2026-07-17T12:00:00Z",
            "updated_at": "2026-07-17T12:00:00Z",
            "agent_name": "grok-build",
            "current_model_id": "grok-4.5",
            "num_chat_messages": 12,
            "info": {"id": "019f0000-aaaa-bbbb-cccc-ddddeeeeffff", "cwd": str(self.root)},
        }
        (self.sdir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        hunks = [
            {
                "filePath": str(self.proj / "src" / "app.py"),
                "eventType": "modified",
                "sessionId": summary["info"]["id"],
            },
            {
                "filePath": str(self.grok / "sessions" / "noise.md"),  # should skip
                "eventType": "added",
            },
        ]
        with (self.sdir / "hunk_records.jsonl").open("w", encoding="utf-8") as f:
            for h in hunks:
                f.write(json.dumps(h) + "\n")

        # Active session marker
        (self.grok / "active_sessions.json").write_text(
            json.dumps([{"session_id": summary["info"]["id"], "pid": 12345, "cwd": str(self.root)}]),
            encoding="utf-8",
        )

        # Orphan session (no hunks / no project)
        odir = self.grok / "sessions" / enc / "019f0000-orphan-0000-0000-000000000001"
        odir.mkdir(parents=True)
        (odir / "summary.json").write_text(
            json.dumps(
                {
                    "generated_title": "Casual Chat",
                    "last_active_at": "2026-07-10T00:00:00Z",
                    "info": {"id": "019f0000-orphan-0000-0000-000000000001", "cwd": str(self.root)},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_project_root_for_file(self) -> None:
        cache: dict = {}
        root = project_root_for_file(
            str(self.proj / "src" / "app.py"),
            grok_home=self.grok,
            cache=cache,
        )
        self.assertEqual(root, str(self.proj.resolve()))

    def test_map_session_to_project(self) -> None:
        mapped = collect_session_project_map(self.grok)
        self.assertTrue(mapped["ok"])
        proj_key = str(self.proj.resolve())
        self.assertIn(proj_key, mapped["projects"])
        p = mapped["projects"][proj_key]
        self.assertGreaterEqual(p["edit_count"], 1)
        self.assertEqual(p["session_count"], 1)
        self.assertTrue(p["sessions"][0]["active"])
        self.assertEqual(p["sessions"][0]["title"], "Build My App Feature")
        self.assertTrue(any(s["title"] == "Casual Chat" for s in mapped["orphan_sessions"]))

    def test_collect_grok_projects_includes_git_status(self) -> None:
        payload = collect_grok_projects(self.grok)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "grok-sessions")
        self.assertGreaterEqual(payload["count"], 1)
        p = payload["projects"][0]
        self.assertEqual(p["name"], "my-app")
        self.assertTrue(p["is_git"])
        self.assertEqual(p["branch"], "main")
        self.assertFalse(p["dirty"])
        self.assertEqual(p["remotes"][0]["name"], "origin")
        self.assertGreaterEqual(p["session_count"], 1)
        self.assertIn("src", [a["name"] for a in p["areas"]])


if __name__ == "__main__":
    unittest.main()
