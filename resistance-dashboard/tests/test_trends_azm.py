"""FitDash #392: Trends AZM (90d + 7d rolling avg + trendline) overlay.

UI + fetch window. Reads existing HealthSnapshot.active_zone_minutes (#304/#318).
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
        self.assertIn("Active Zone Minutes · 90d", HTML)
        self.assertIn("7d rolling avg", HTML)
        self.assertIn("/trends-azm.js?v=azm-90d-3", HTML)
        self.assertNotIn("Active Zone Minutes · 7d", HTML)
        self.assertNotIn("7d trailing sum", HTML)
        self.assertNotIn("Weekly AZM", HTML)
        sleep_idx = HTML.find('id="sleep-trend-card"')
        cal_idx = HTML.find('id="calories-macros-charts"')
        vol_idx = HTML.find('id="charts-volume-strength"')
        azm_idx = HTML.find('id="azm-trend-card"')
        conn_idx = HTML.find('id="connections-card"')
        self.assertLess(sleep_idx, cal_idx)
        self.assertLess(cal_idx, vol_idx)
        self.assertLess(vol_idx, azm_idx, "AZM sits under daily volume + strength")
        self.assertLess(azm_idx, conn_idx)
        azm_card = HTML[azm_idx:conn_idx]
        self.assertIn("chart-box", azm_card)
        self.assertNotIn("<canvas", azm_card)
        self.assertIn('id="azm-sparkline"', azm_card)
        self.assertIn('id="azm-legend"', azm_card)
        self.assertIn('id="azm-target-chip"', azm_card)
        self.assertIn('id="azm-target-value"', azm_card)
        self.assertIn("azm-swatch-daily", azm_card)
        self.assertIn("azm-swatch-roll", azm_card)
        self.assertIn("azm-swatch-trend", azm_card)
        self.assertIn("azm-swatch-target", azm_card)
        self.assertIn("Daily", azm_card)
        self.assertIn("Trendline", azm_card)
        self.assertIn("Target", azm_card)
        self.assertIn("14d median", azm_card)
        self.assertIn("height: 240px", CSS)
        self.assertIn("azm-swatch-daily", CSS)
        self.assertIn("#8b9bb4", CSS.split(".azm-swatch-daily", 1)[1].split("}", 1)[0])
        self.assertIn("#3d9cf0", CSS.split(".azm-swatch-roll", 1)[1].split("}", 1)[0])
        self.assertIn("#f07178", CSS.split(".azm-swatch-trend", 1)[1].split("}", 1)[0])
        self.assertIn("#5ce1a8", CSS.split(".azm-swatch-target", 1)[1].split("}", 1)[0])
        self.assertNotIn("height: 80px", CSS.split(".azm-spark-svg", 1)[1].split("}", 1)[0])

    def test_series_is_90d_rolling_avg_and_trendline(self):
        self.assertIn("var SPAN_DAYS = 90;", OVERLAY)
        self.assertIn("var ROLL_DAYS = 7;", OVERLAY)
        self.assertIn("7d rolling avg", OVERLAY)
        self.assertIn("total_minutes", OVERLAY)
        self.assertIn("health.active_zone_minutes", OVERLAY)
        self.assertIn('return "—";', OVERLAY)
        self.assertIn("lastRolling7", OVERLAY)
        self.assertIn("linearTrend", OVERLAY)
        self.assertIn("rollingAverage", OVERLAY)
        self.assertIn("azmTargetMinutes", OVERLAY)
        self.assertIn("var TARGET_LOOKBACK_DAYS = 14;", OVERLAY)
        self.assertIn("var AZM_TARGET_FLOOR = 10;", OVERLAY)
        self.assertIn("var AZM_TARGET_CAP = 45;", OVERLAY)
        self.assertIn("sparklineSvg", OVERLAY)
        self.assertIn("azm-target", OVERLAY)
        self.assertIn('data-y-min="0"', OVERLAY)
        self.assertIn("monthTickIndexes", OVERLAY)
        self.assertIn("azm-trend", OVERLAY)
        self.assertIn("azm-roll", OVERLAY)
        self.assertIn("SERIES_COLORS", OVERLAY)
        self.assertIn('height="100%"', OVERLAY)
        self.assertIn('preserveAspectRatio="none"', OVERLAY)
        self.assertNotIn('height="80"', OVERLAY)
        self.assertNotIn("7d trailing sum", OVERLAY)
        self.assertNotIn("var SPAN_DAYS = 7;", OVERLAY)
        self.assertNotIn("weeklySum", OVERLAY)
        self.assertNotIn("(v - min)", OVERLAY)

    def test_honest_empty_not_invented(self):
        self.assertIn("No Active Zone Minutes in the last 90 days.", OVERLAY)
        self.assertNotIn("No Active Zone Minutes in the last 7 days.", OVERLAY)
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
        self.assertIn(".sb-shell", CSS)
        sleep_wrap = CSS.split(".sb-fill-wrap {", 1)[1].split("}", 1)[0]
        self.assertIn("height: 44px", sleep_wrap)


class TrendsAzmTargetLock(unittest.TestCase):
    def test_same_formula_as_cardio_quest(self):
        from rt_dashboard.cardio_quest import (
            AZM_LOOKBACK_DAYS,
            AZM_TARGET_CAP,
            AZM_TARGET_FLOOR,
            DEFAULT_AZM_TARGET,
            cardio_target_minutes,
            recent_azm_minutes,
        )

        self.assertEqual(AZM_LOOKBACK_DAYS, 14)
        self.assertEqual(AZM_TARGET_FLOOR, 10)
        self.assertEqual(AZM_TARGET_CAP, 45)
        self.assertEqual(DEFAULT_AZM_TARGET, 20)
        days = [
            {"date": "2026-08-16", "total_minutes": 10},
            {"date": "2026-08-17", "total_minutes": 22},
            {"date": "2026-08-18", "total_minutes": 18},
            {"date": "2026-08-19", "total_minutes": 24},
            {"date": "2026-08-20", "total_minutes": 12},
            {"date": "2026-08-21", "total_minutes": 30},
            {"date": "2026-08-22", "total_minutes": 16},
            {"date": "2026-08-23", "total_minutes": 20},
            {"date": "2026-08-24", "total_minutes": 28},
            {"date": "2026-08-25", "total_minutes": 14},
            {"date": "2026-08-26", "total_minutes": 26},
            {"date": "2026-08-27", "total_minutes": 19},
            {"date": "2026-08-28", "total_minutes": 21},
            {"date": "2026-08-29", "total_minutes": 17},
            {"date": "2026-08-30", "total_minutes": 23},
            {"date": "2026-08-31", "total_minutes": 400},
        ]
        recent = recent_azm_minutes(days, as_of="2026-08-31")
        self.assertEqual(cardio_target_minutes(recent, easy=False), 20)
        self.assertNotIn(400.0, recent)
        self.assertIn("cardio_target_minutes(easy=False)", OVERLAY)
        self.assertIn("var TARGET_LOOKBACK_DAYS = 14;", OVERLAY)


class TrendsAzmSourceLock(unittest.TestCase):
    def test_reuses_existing_backend(self):
        self.assertIn("def fetch_active_zone_minutes", GH)
        self.assertIn('daily_rollup("active-zone-minutes"', GH)
        self.assertIn("parse_active_zone_minutes_rollup", GH)
        self.assertIn("min(int(days), 90)", GH)
        self.assertNotIn("min(int(days), 14)", GH)
        self.assertNotIn("return []  # stub", GH)
        self.assertNotIn("Proof-first window is 7–14d", GH)

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
        self.assertIn("/trends-azm.js?v=azm-90d-3", HTML)
        self.assertIn("styles.css?v=library-1", HTML)
        self.assertIn('const CACHE = "fitdash-shell-v80"', SW)
        self.assertIn("/styles.css?v=library-1", SW)
        self.assertNotIn("fitdash-shell-v60", SW)
        self.assertNotIn("fitdash-shell-v61", SW)
        self.assertNotIn("fitdash-shell-v62", SW)
        self.assertNotIn("fitdash-shell-v63", SW)
        self.assertNotIn("fitdash-shell-v64", SW)
        self.assertNotIn("fitdash-shell-v65", SW)
        self.assertNotIn("fitdash-shell-v66", SW)
        self.assertNotIn("fitdash-shell-v67", SW)
        self.assertNotIn("fitdash-shell-v68", SW)
        self.assertNotIn("fitdash-shell-v69", SW)
        self.assertNotIn("fitdash-shell-v70", SW)
        self.assertNotIn("fitdash-shell-v71", SW)
        self.assertNotIn("fitdash-shell-v72", SW)
        self.assertNotIn("fitdash-shell-v73", SW)
        self.assertNotIn("fitdash-shell-v74", SW)
        self.assertNotIn("fitdash-shell-v75", SW)
        self.assertNotIn("fitdash-shell-v77", SW)
        self.assertNotIn("fitdash-shell-v78", SW)
        self.assertNotIn("fitdash-shell-v79", SW)
        self.assertNotIn("azm-week-1", HTML)
        self.assertNotIn("azm-week-2", HTML)
        self.assertNotIn("azm-spark-1", HTML)
        self.assertNotIn("azm-90d-1", HTML)
        self.assertNotIn("azm-90d-2", HTML)
        self.assertNotIn("calorie-meta-bottom-1", HTML)
        self.assertNotIn("calorie-meta-bottom-1", SW)


if __name__ == "__main__":
    unittest.main()
