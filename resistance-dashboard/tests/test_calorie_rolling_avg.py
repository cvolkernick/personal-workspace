"""FitDash #946: calories in vs out chart uses trailing 7-day means.

Raw daily stays on the same 90d chart, muted. Missing food logs are
excluded from the intake mean. Fewer than 4 logged days suppresses
the point. Same nutrition and calories_burned sources. No new ingest.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
ROLL = (ROOT / "static" / "calorie-rolling-avg.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


class CalorieRollingAvgMarkup(unittest.TestCase):
    def test_same_chart_same_sources(self):
        self.assertIn("Calories intake vs burned · 90d", HTML)
        self.assertIn('id="chart-calories"', HTML)
        card = HTML.split("Calories intake vs burned · 90d", 1)[1][:400]
        self.assertIn('id="chart-calories"', card)
        self.assertIn("const CAL_IN_OUT_SPAN_DAYS = 90;", APP_JS)
        self.assertIn("data.health && data.health.nutrition", APP_JS)
        self.assertIn("data.health.calories_burned", APP_JS)
        self.assertNotIn("/api/calorie-rolling", APP_JS)
        self.assertNotIn("fetch(", ROLL)

    def test_script_before_app_and_cache(self):
        self.assertIn("/calorie-rolling-avg.js?v=cal-7d-946-1", HTML)
        self.assertIn("/app.js?v=weekly-review-1033-1", HTML)
        self.assertLess(
            HTML.find("/calorie-rolling-avg.js?v=cal-7d-946-1"),
            HTML.find("/app.js?v=weekly-review-1033-1"),
        )
        self.assertIn('const CACHE = "fitdash-shell-v131"', SW)
        self.assertIn("/app.js?v=weekly-review-1033-1", SW)
        self.assertNotIn("/app.js?v=last-perf-920-1", HTML)
        self.assertNotIn("/app.js?v=last-perf-920-1", SW)
        self.assertNotIn("fitdash-shell-v115", SW)

    def test_lines_are_seven_day_avg_and_points_are_daily(self):
        self.assertIn('label: "7-day avg in"', APP_JS)
        self.assertIn('label: "7-day avg out"', APP_JS)
        self.assertIn('label: "daily in"', APP_JS)
        self.assertIn('label: "daily out"', APP_JS)
        self.assertNotIn('label: "Intake (kcal)"', APP_JS)
        self.assertNotIn('label: "Burned (kcal)"', APP_JS)
        self.assertIn("lines = 7-day avg", APP_JS)
        self.assertIn("points = daily", APP_JS)
        self.assertIn('pointStyle: "circle"', APP_JS)
        self.assertIn('pointStyle: "triangle"', APP_JS)
        self.assertIn('matchMedia("(max-width: 720px)")', APP_JS)
        self.assertIn("rgba(92, 225, 168, 0.4)", APP_JS)
        self.assertIn("rgba(240, 113, 120, 0.4)", APP_JS)
        self.assertIn('d.label === "7-day avg in"', APP_JS)
        self.assertIn('d.label === "7-day avg out"', APP_JS)
        self.assertIn("const vin = avgInSeries[i];", APP_JS)
        self.assertIn("const vburn = avgOutSeries[i];", APP_JS)

    def test_tooltip_and_window_rules(self):
        self.assertIn("globalThis.FitDashCalorieRollingAvg", APP_JS)
        self.assertIn("calRoll.kcalByDate", APP_JS)
        self.assertIn("calRoll.trailingMeans", APP_JS)
        self.assertIn("calRoll.valuesOnLabels", APP_JS)
        self.assertIn("calRoll.tooltipLines", APP_JS)
        self.assertIn("var WINDOW_DAYS = 7;", ROLL)
        self.assertIn("var MIN_LOGGED = 4;", ROLL)
        self.assertIn("7-day avg out − in:", ROLL)
        self.assertIn("not stored as 0", ROLL)
        self.assertNotIn("nutrition.push", ROLL)
        self.assertNotIn("calories_burned.push", ROLL)

    def test_node_math(self):
        script = ROOT / "tests" / "calorie_rolling_avg.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok calorie-rolling-avg", proc.stdout)


if __name__ == "__main__":
    unittest.main()
