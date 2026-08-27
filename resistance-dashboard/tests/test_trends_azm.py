"""FitDash #299: Trends weekly AZM (7d trailing sum) overlay.

UI only. Reads existing HealthSnapshot.active_zone_minutes (#304/#318).
Missing → honest —. Never invents from steps / burned kcal.
Does not gate Generate workout, meal generate, or rest_gate.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
OVERLAY = (ROOT / "static" / "trends-azm.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")
REST_GATE = (ROOT / "rt_dashboard" / "workout_store.py").read_text(encoding="utf-8")
GH = (ROOT / "rt_dashboard" / "google_health.py").read_text(encoding="utf-8")


def _fn(name: str, until: str) -> str:
    return APP_JS.split(name, 1)[1].split(until, 1)[0]


class TrendsAzmMarkup(unittest.TestCase):
    def test_overlay_wired_under_trends(self):
        self.assertIn('id="azm-trend-card"', HTML)
        self.assertIn('data-m-panel="trends"', HTML)
        self.assertIn('id="azm-week-value"', HTML)
        self.assertIn('id="azm-sparkline"', HTML)
        self.assertIn('id="azm-trend-note"', HTML)
        self.assertIn("Active Zone Minutes · 7d", HTML)
        self.assertIn("/trends-azm.js?v=azm-week-1", HTML)
        sleep_idx = HTML.find('id="sleep-trend-card"')
        azm_idx = HTML.find('id="azm-trend-card"')
        cal_idx = HTML.find('id="calories-macros-charts"')
        self.assertLess(sleep_idx, azm_idx)
        self.assertLess(azm_idx, cal_idx)

    def test_weekly_is_7d_trailing_sum(self):
        self.assertIn("var SPAN_DAYS = 7;", OVERLAY)
        self.assertIn("7d trailing sum", OVERLAY)
        self.assertIn("total_minutes", OVERLAY)
        self.assertIn("health.active_zone_minutes", OVERLAY)
        self.assertIn('return "—";', OVERLAY)
        self.assertIn("weeklySum", OVERLAY)
        self.assertIn("sparklineSvg", OVERLAY)

    def test_honest_empty_not_invented(self):
        self.assertIn("No Active Zone Minutes in the last 7 days.", OVERLAY)
        lowered = OVERLAY.lower()
        for needle in (
            "heart_minutes",
            "active_minutes",
            "vo2",
            "steps",
            "calories_burned",
        ):
            self.assertNotIn(needle, lowered, needle)
        self.assertNotIn("calories_burned.push", OVERLAY)
        self.assertNotIn("steps.push", OVERLAY)

    def test_does_not_gate_generate_or_rest(self):
        for needle in (
            "rest_gate",
            "force_rest",
            "rest_if_recovery_below",
            "generateWorkoutPlan",
            "generateMealPlan",
            "btn-generate-workout",
            "btn-generate-meal",
            "next_session_type",
        ):
            self.assertNotIn(needle, OVERLAY, needle)
        gen_wo = _fn(
            "async function generateWorkoutPlan",
            "async function submitEquipmentInventory",
        )
        self.assertNotIn("active_zone", gen_wo)
        self.assertNotIn("azm", gen_wo.lower())
        meal_idx = APP_JS.find("async function generatePlan")
        self.assertGreater(meal_idx, 0)
        meal = APP_JS[meal_idx : meal_idx + 1800]
        self.assertNotIn("active_zone", meal)
        self.assertNotIn("azm", meal.lower())
        self.assertNotIn("active_zone_minutes", REST_GATE)
        self.assertIn("def rest_gate(", REST_GATE)
        self.assertIn("score_f < threshold and not bool(sparse)", REST_GATE)

    def test_sleep_battery_unchanged(self):
        self.assertIn('id="sleep-battery-panel"', HTML)
        self.assertIn("same-wake decision gate → Today (not Trends)", HTML)
        self.assertNotIn("sleep-battery-panel", OVERLAY)
        self.assertNotIn("empty_at", OVERLAY)
        self.assertNotIn("pct_charged", OVERLAY)


class TrendsAzmSourceLock(unittest.TestCase):
    def test_reuses_existing_backend(self):
        self.assertIn("def fetch_active_zone_minutes", GH)
        self.assertIn('daily_rollup("active-zone-minutes"', GH)
        self.assertIn("parse_active_zone_minutes_rollup", GH)
        self.assertNotIn("return []  # stub", GH)

    def test_no_secrets(self):
        blob = OVERLAY + HTML
        for needle in (
            "GOOGLE_TASKS_",
            "FITDASH_SERVICE_TOKEN",
            "BEGIN RSA",
            "pi_secret",
        ):
            self.assertNotIn(needle, blob)

    def test_node_math_and_paint(self):
        script = ROOT / "tests" / "trends_azm.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok trends-azm", proc.stdout)


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
        self.assertIn(
            '"ignoreCommand": "python3 scripts/vercel_ignore.py || exit 1"',
            VERCEL,
        )
        paths = (ROOT / "vercel-ignore-paths.txt").read_text(encoding="utf-8")
        self.assertIn("resistance-dashboard/", paths)

    def test_cache_bumped(self):
        self.assertIn("/trends-azm.js?v=azm-week-1", HTML)
        self.assertIn("styles.css?v=calorie-meta-bottom-1", HTML)
        self.assertIn('const CACHE = "fitdash-shell-v68"', SW)
        self.assertIn("/styles.css?v=calorie-meta-bottom-1", SW)
        self.assertNotIn("fitdash-shell-v60", SW)
        self.assertNotIn("fitdash-shell-v61", SW)
        self.assertNotIn("fitdash-shell-v62", SW)
        self.assertNotIn("fitdash-shell-v63", SW)
        self.assertNotIn("fitdash-shell-v64", SW)
        self.assertNotIn("fitdash-shell-v65", SW)
        self.assertNotIn("fitdash-shell-v66", SW)
        self.assertNotIn("fitdash-shell-v67", SW)


if __name__ == "__main__":
    unittest.main()
