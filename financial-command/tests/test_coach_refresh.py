"""Bills & cash coach Refresh: in-flight state, toast, timestamp (#686)."""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "financial-command" / "index.html"


def _fn_body(html: str, name: str) -> str:
    """Extract a top-level function body from index.html (brace-matched)."""
    m = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", html)
    if not m:
        raise AssertionError(f"function {name} not found")
    start = html.find("{", m.end())
    if start < 0:
        raise AssertionError(f"function {name} has no body")
    depth = 0
    for i, ch in enumerate(html[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]
    raise AssertionError(f"function {name} unclosed")


class TestCoachRefreshFeedback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        cls.load = _fn_body(cls.html, "loadCoach")
        cls.live = _fn_body(cls.html, "refreshLive")

    def test_markup_has_refresh_control_and_updated_hint(self):
        self.assertIn('id="btn-coach-refresh"', self.html)
        self.assertIn('id="coach-updated"', self.html)
        self.assertIn("Bills &amp; cash coach", self.html)

    def test_force_path_disables_button_and_labels_refreshing(self):
        self.assertIn("coachRefreshInFlight", self.load)
        self.assertIn("if (coachRefreshInFlight) return;", self.load)
        self.assertIn('btn.textContent = "Refreshing…"', self.load)
        self.assertIn("btn.disabled = true", self.load)
        self.assertIn("btn.disabled = false", self.load)
        self.assertIn('btn.textContent = "Refresh"', self.load)
        self.assertIn("finally", self.load)

    def test_success_and_failure_toasts_are_force_only(self):
        self.assertIn('toast("Coach refreshed")', self.load)
        self.assertIn('toast("Coach refresh failed")', self.load)
        # Non-force loadCoach() (poll / refreshLive) must not toast or
        # steal #btn-coach-refresh — both feedback calls sit under `if (force)`.
        force_blocks = re.findall(r"if \(force\) \{.*?\n      \}", self.load, re.S)
        joined = "\n".join(force_blocks)
        self.assertIn('toast("Coach refreshed")', joined)
        self.assertIn('toast("Coach refresh failed")', joined)
        self.assertIn("stampCoachUpdated", joined)
        self.assertNotIn('toast("Coach refreshed")', self.load.split("if (force)")[0])

    def test_refresh_live_does_not_force_coach_or_own_coach_button(self):
        self.assertIn("loadCoach()", self.live)
        self.assertNotIn("loadCoach({ refresh: true })", self.live)
        self.assertNotIn("btn-coach-refresh", self.live)
        self.assertNotIn("coachRefreshInFlight", self.live)

    def test_click_handler_requests_force_refresh(self):
        self.assertIn("loadCoach({ refresh: true })", self.html)

    def test_updated_stamp_format(self):
        stamp = _fn_body(self.html, "stampCoachUpdated")
        self.assertIn('" · Updated "', stamp)
        self.assertIn("padStart(2, \"0\")", stamp)

    def test_load_coach_behavior_under_mocked_dom(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH")
        sim = Path(__file__).with_name("coach_refresh_sim.js")
        proc = subprocess.run(
            [node, str(sim), str(INDEX)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
