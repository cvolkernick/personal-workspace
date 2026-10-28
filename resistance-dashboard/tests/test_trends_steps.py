"""Trends steps card (plan 2026-10-26): placement (AC12), chart model (AC13), pins (AC14)."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
PINS = json.loads((ROOT / "tests" / "static_asset_pins.json").read_text())


class TrendsStepsCard(unittest.TestCase):
    def test_trends_steps_card_placement(self):
        steps_at = HTML.find('id="steps-trend-card"')
        row_at = HTML.find('id="azm-rhr-row"')
        self.assertGreater(steps_at, -1)
        self.assertLess(steps_at, row_at)
        # Nothing but the closing of the steps section sits between card and the AZM/RHR row.
        between = HTML[HTML.find("</article>", steps_at):row_at]
        self.assertEqual(between.count("<article"), 0)
        self.assertEqual(between.count("<section"), 1)  # the azm-rhr-row opening tag itself
        sec = HTML[HTML.rfind("<section", 0, steps_at):steps_at]
        self.assertIn('data-m-panel="trends"', sec)
        self.assertNotIn("equal-cards", sec)  # full width, not a 3-up row
        for s in ("Daily steps · 8 wk", "Steps are the lever; AZM and resting HR below show the response.",
                  'id="chart-steps"', 'data-steps-weeks="4"', 'data-steps-weeks="8"', 'data-steps-weeks="13"'):
            self.assertIn(s, HTML)

    def test_azm_rhr_row_markup_unchanged(self):
        base = ROOT / "tests" / "fixtures" / "azm_rhr_row_pre_steps.html"
        if not base.exists():
            self.skipTest("baseline fixture missing")
        start = HTML.find('<section class="grid two equal-cards" style="margin-top: 1rem" id="azm-rhr-row"')
        end = HTML.find("</section>", start) + len("</section>")
        self.assertEqual(HTML[start:end], base.read_text(encoding="utf-8"))

    def test_assets_pinned_and_sw_bumped(self):
        self.assertIn('/trends-steps.js?v=steps-2', HTML)
        self.assertIn('/app.js?v=backport-steps-guard-1', HTML)
        self.assertLess(HTML.find("/trends-steps.js?v="), HTML.find("/app.js?v="))
        self.assertIn('const CACHE = "fitdash-shell-v152"', SW)
        self.assertIn('"/trends-steps.js?v=steps-2"', SW)
        self.assertIn('"/app.js?v=backport-steps-guard-1"', SW)
        self.assertEqual(PINS["trends-steps.js"]["v"], "steps-2")
        self.assertEqual(PINS["app.js"]["v"], "backport-steps-guard-1")
        self.assertIn("FitDashTrendsSteps", APP_JS)
        self.assertNotIn("steps_history", (ROOT / "vercel.json").read_text())

    def test_node_chart_model(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node missing")
        proc = subprocess.run([node, str(ROOT / "tests" / "trends_steps.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
