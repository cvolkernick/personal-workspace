"""Tests for named Workflow Management session open."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

import open_grok as og  # noqa: E402
import workspace as ws  # noqa: E402


class TestOpenGrok(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="open-grok-")
        self.ws = Path(self._td.name)
        self.backlog = self.ws / "ops" / "backlog"
        self.backlog.mkdir(parents=True)
        self.grok_home = self.ws / "grok-home"
        sess = (
            self.grok_home
            / "sessions"
            / "%2Ftmp%2Fws"
            / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )
        sess.mkdir(parents=True)
        (sess / "summary.json").write_text(
            json.dumps(
                {
                    "info": {
                        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        "cwd": "/tmp/ws",
                    },
                    "generated_title": "Workflow Management",
                    "session_summary": "Workflow Management",
                    "session_kind": "parent",
                    "updated_at": "2026-07-20T12:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self._patches = [
            mock.patch.object(og, "WORKSPACE_ROOT", self.ws),
            mock.patch.object(og, "LAUNCH_DIR", self.backlog),
            mock.patch.object(og, "LAUNCH_SCRIPT", self.backlog / "open-workflow-grok.launch.sh"),
            mock.patch.object(og, "PROMPT_FILE", self.backlog / "open-workflow-grok.prompt.txt"),
            mock.patch.object(og, "CONFIG_PATH", self.backlog / "workflow-session.json"),
            mock.patch.object(og, "_grok_home", return_value=self.grok_home),
            mock.patch.object(ws, "WORKSPACE_ROOT", self.ws),
        ]
        for p in self._patches:
            p.start()
        og.save_config(
            {
                "session_name": "Workflow Management",
                "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "match_titles": ["Workflow Management"],
            }
        )

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._td.cleanup()

    def test_resolve_by_pinned_id(self) -> None:
        r = og.resolve_workflow_session()
        self.assertTrue(r["ok"])
        self.assertEqual(r["session_id"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        self.assertEqual(r["source"], "pinned_id")

    def test_resolve_by_title_when_id_missing(self) -> None:
        og.save_config(
            {
                "session_name": "Workflow Management",
                "session_id": "",
                "match_titles": ["Workflow Management"],
            }
        )
        r = og.resolve_workflow_session()
        self.assertTrue(r["ok"])
        self.assertEqual(r["session_id"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        self.assertEqual(r["source"], "title_match")

    def test_write_launch_uses_resume(self) -> None:
        r = og.write_launch_script(
            session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            cwd="/tmp/ws",
            session_name="Workflow Management",
        )
        script = (self.ws / r["launch_script"]).read_text(encoding="utf-8")
        self.assertIn("--resume", script)
        self.assertIn("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", script)
        self.assertNotIn("--continue", script)

    def test_open_without_grok(self) -> None:
        with mock.patch.object(og, "_which_grok", return_value=None):
            r = og.open_workflow_grok(mode="named")
        self.assertFalse(r["ok"])
        self.assertIn("grok", (r.get("error") or "").lower())

    def test_open_calls_terminal_with_named(self) -> None:
        with mock.patch.object(og, "_which_grok", return_value="/usr/bin/grok"), mock.patch(
            "open_grok.subprocess.run"
        ) as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            r = og.open_workflow_grok(mode="named")
        self.assertTrue(r["ok"])
        self.assertIn("Workflow Management", r.get("message") or "")
        run.assert_called_once()
        args = run.call_args[0][0]
        self.assertEqual(args[:3], ["open", "-a", "Terminal"])


if __name__ == "__main__":
    unittest.main()
