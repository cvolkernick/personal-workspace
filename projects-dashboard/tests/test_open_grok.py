"""Tests for open_workflow_grok launch script generation."""

from __future__ import annotations

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
        self._patches = [
            mock.patch.object(og, "WORKSPACE_ROOT", self.ws),
            mock.patch.object(og, "LAUNCH_DIR", self.backlog),
            mock.patch.object(og, "LAUNCH_SCRIPT", self.backlog / "open-workflow-grok.launch.sh"),
            mock.patch.object(og, "PROMPT_FILE", self.backlog / "open-workflow-grok.prompt.txt"),
            mock.patch.object(ws, "WORKSPACE_ROOT", self.ws),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._td.cleanup()

    def test_write_launch_continue(self) -> None:
        r = og.write_launch_script(mode="continue")
        self.assertEqual(r["mode"], "continue")
        script = (self.ws / r["launch_script"]).read_text(encoding="utf-8")
        self.assertIn("--continue", script)
        self.assertIn("ROOT=", script)
        self.assertTrue((self.ws / r["prompt_file"]).is_file())

    def test_write_launch_new(self) -> None:
        r = og.write_launch_script(mode="new")
        self.assertEqual(r["mode"], "new")
        script = (self.ws / r["launch_script"]).read_text(encoding="utf-8")
        self.assertIn('MODE=', script)
        self.assertIn("new", script)

    def test_open_without_grok(self) -> None:
        with mock.patch.object(og, "_which_grok", return_value=None):
            r = og.open_workflow_grok(mode="continue")
        self.assertFalse(r["ok"])
        self.assertIn("grok", (r.get("error") or "").lower())

    def test_open_calls_terminal(self) -> None:
        with mock.patch.object(og, "_which_grok", return_value="/usr/bin/grok"), mock.patch(
            "open_grok.subprocess.run"
        ) as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            r = og.open_workflow_grok(mode="continue")
        self.assertTrue(r["ok"])
        run.assert_called_once()
        args = run.call_args[0][0]
        self.assertEqual(args[0], "open")
        self.assertEqual(args[1], "-a")
        self.assertEqual(args[2], "Terminal")


if __name__ == "__main__":
    unittest.main()
