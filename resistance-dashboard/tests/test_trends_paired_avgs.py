"""FitDash #254/#258: Trends avgs on the Σ 60d paired window.

Overlay only — same pairDays set as the Σ chips (days with both series).
Avg deficit = mean(burned_i − intake_i). No invented food. No extra function.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
OVERLAY = (ROOT / "static" / "trends-paired-avgs.js").read_text(encoding="utf-8")
GH = (ROOT / "rt_dashboard" / "google_health.py").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")


class TrendsPairedAvgMarkup(unittest.TestCase):
    def test_overlay_wired_under_trends_card(self):
        self.assertIn("Calories intake vs burned · 60d", HTML)
        self.assertIn('id="nutrition-note"', HTML)
        self.assertIn("/trends-paired-avgs.js?v=paired-avgs-3", HTML)
        self.assertNotIn("/trends-paired-avgs.js?v=paired-avgs-2", HTML)
        self.assertIn("Avg intake", OVERLAY)
        self.assertIn("Avg burned", OVERLAY)
        self.assertIn("Avg deficit", OVERLAY)
        self.assertIn("kcal/day", OVERLAY)
        self.assertIn("+deficit", OVERLAY)
        self.assertIn("−surplus", OVERLAY)
        self.assertIn('row.id = "trends-avg-row"', OVERLAY)
        self.assertIn("trends-avg-intake", OVERLAY)
        self.assertIn("trends-avg-burned", OVERLAY)
        self.assertIn("trends-avg-delta", OVERLAY)

    def test_same_60d_paired_window_as_sigma_chips(self):
        self.assertIn("const CAL_IN_OUT_SPAN_DAYS = 60;", APP_JS)
        self.assertIn("var SPAN_DAYS = 60;", OVERLAY)
        self.assertNotIn("const CAL_IN_OUT_SPAN_DAYS = 45;", APP_JS)
        self.assertNotIn("var SPAN_DAYS = 45;", OVERLAY)
        self.assertIn("pairDays", APP_JS)
        self.assertIn("pairDays", OVERLAY)
        self.assertIn("sumIn += vin;", APP_JS)
        self.assertIn("sumOut += vout;", APP_JS)
        self.assertIn("sumIn += vin;", OVERLAY)
        self.assertIn("sumOut += vout;", OVERLAY)
        self.assertIn("Number.isNaN(vin)", APP_JS)
        self.assertIn("Number.isNaN(vout)", APP_JS)
        self.assertIn("Number.isNaN(vin)", OVERLAY)
        self.assertIn("Number.isNaN(vout)", OVERLAY)
        self.assertIn("days with both in", APP_JS)
        self.assertIn("health.nutrition", OVERLAY)
        self.assertIn("health.calories_burned", OVERLAY)
        self.assertIn("avgIn: pairDays > 0 ? sumIn / pairDays : null", OVERLAY)
        self.assertIn("avgOut: pairDays > 0 ? sumOut / pairDays : null", OVERLAY)
        self.assertIn("sumDelta += vout - vin;", OVERLAY)
        self.assertIn(
            "avgDelta: pairDays > 0 ? sumDelta / pairDays : null", OVERLAY
        )
        self.assertIn("chip-deficit", OVERLAY)
        self.assertIn("chip-surplus", OVERLAY)

    def test_empty_pair_days_is_em_dash(self):
        self.assertIn('if (!pairDays) return "—";', OVERLAY)
        self.assertIn("Need paired intake + burned days", OVERLAY)
        self.assertIn("function formatAvgDelta", OVERLAY)

    def test_does_not_invent_nutrition_rows(self):
        lowered = OVERLAY.lower()
        for needle in (
            "chicken",
            "oats",
            "salmon",
            "fake meal",
            "placeholder food",
        ):
            self.assertNotIn(needle, lowered, needle)
        self.assertNotIn("nutrition.push", OVERLAY)
        self.assertNotIn("calories_burned.push", OVERLAY)

    def test_does_not_rewrite_or_shrink_app_js(self):
        app = ROOT / "static" / "app.js"
        self.assertGreaterEqual(app.stat().st_size, 180_000)
        self.assertIn("Σ intake", APP_JS)
        self.assertIn("Σ burned", APP_JS)


class TrendsPairedAvgSourceLock(unittest.TestCase):
    def test_google_health_not_stubbed(self):
        self.assertIn("def fetch_nutrition_bundle", GH)
        self.assertIn("def fetch_calories_burned", GH)
        self.assertIn("parse_nutrition_log_points", GH)
        self.assertNotIn("return []  # stub", GH)
        self.assertNotIn("invented food", GH.lower())

    def test_not_hidrate_bottle_charge(self):
        self.assertNotIn("bottle charge", OVERLAY.lower())
        self.assertNotIn("#238", OVERLAY)

    def test_node_math_and_paint(self):
        script = ROOT / "tests" / "trends_paired_avgs.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok trends-paired-avgs", proc.stdout)


class HobbyAndIgnoreLock(unittest.TestCase):
    def test_no_new_serverless_function(self):
        api = ROOT / "api"
        fns = []
        for path in api.rglob("*.py"):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            src = path.read_text(encoding="utf-8")
            if "class handler" in src or "\ndef app(" in src or "\napp = " in src:
                fns.append(path)
        self.assertEqual(len(fns), 12, [str(x.relative_to(ROOT)) for x in fns])

    def test_ignore_build_unchanged(self):
        self.assertIn('"ignoreCommand": "python3 scripts/vercel_ignore.py || exit 1"', VERCEL)
        paths = (ROOT / "vercel-ignore-paths.txt").read_text(encoding="utf-8")
        self.assertIn("resistance-dashboard/", paths)


if __name__ == "__main__":
    unittest.main()
