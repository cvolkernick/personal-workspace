"""Structural + entry-point tests for dashboard and run_treasury."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestDashboardArtifact(unittest.TestCase):
    def test_index_has_dual_venue_and_actions(self):
        html = (ROOT / "financial-command" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Financial Command Center", html)
        self.assertIn("Do now", html)
        self.assertIn("At a glance", html)
        self.assertIn("kpi-grid", html)
        self.assertIn("Cash &amp; credit", html)
        self.assertIn("Upcoming bills", html)
        self.assertIn("bill-row", html)
        self.assertIn("dueUrgency", html)
        self.assertIn("Brokerage", html)
        self.assertIn("Actual spend", html)
        self.assertIn("Capital targets", html)
        self.assertIn("Settings", html)
        self.assertIn("/api/refresh", html)
        self.assertIn("show-all-actions", html)
        self.assertIn("actorLabel", html)
        self.assertGreater(len(html), 8000)

    def test_action_items_doc(self):
        p = ROOT / "investment" / "treasury-action-items.md"
        text = p.read_text(encoding="utf-8")
        for needle in (
            "loan protection",
            "autopay",
            "bridge",
            "DCA",
            "BP floor",
        ):
            self.assertIn(needle.lower(), text.lower())


class TestRunTreasuryEntry(unittest.TestCase):
    def test_run_offline_writes_evaluation(self):
        out = ROOT / "treasury" / "snapshots" / "treasury_test_out.json"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "treasury" / "run_treasury.py"), "--offline", "--out", str(out)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(out.is_file())
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("evaluation", data)
        self.assertIn("snapshot", data)
        self.assertIn("stress", data["evaluation"])
        self.assertIn("actions", data["evaluation"])
        self.assertIn("overall", data["evaluation"]["stress"])
        # Dashboard copy
        dash = ROOT / "financial-command" / "treasury_latest.json"
        self.assertTrue(dash.is_file())
        dash_data = json.loads(dash.read_text(encoding="utf-8"))
        self.assertTrue(dash_data["evaluation"]["actions"] is not None)


if __name__ == "__main__":
    unittest.main()
