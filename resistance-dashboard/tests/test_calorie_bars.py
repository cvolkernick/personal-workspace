"""Unit tests for calorie pacing + in/out delta helpers (shipped functions)."""

from __future__ import annotations

import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rt_dashboard.calorie_bars import (
    build_calorie_bars_payload,
    calorie_in_out_delta,
    calorie_pacing,
    civil_day_macros,
    eating_window_fraction,
    pace_clock_copy,
    pace_vs_expected,
    sum_intake_in_window,
)
from rt_dashboard.models import FoodLogEntry


class TestCalorieBars(unittest.TestCase):
    def test_mid_window_paced_budget(self):
        # 50% through eating window, target 2000 → paced ~1000
        win = eating_window_fraction(
            now=datetime(2026, 7, 26, 15, 0, 0, tzinfo=timezone.utc),
            last_wake_at=datetime(2026, 7, 26, 7, 0, 0, tzinfo=timezone.utc),
            empty_at=datetime(2026, 7, 26, 23, 0, 0, tzinfo=timezone.utc),
            awake_budget_hours=16.0,
        )
        self.assertAlmostEqual(win["fraction"], 0.5, places=2)
        pac = calorie_pacing(
            consumed=1000, target=2000, window_fraction=win["fraction"]
        )
        self.assertAlmostEqual(pac["paced_budget"], 1000.0, delta=20.0)
        self.assertAlmostEqual(pac["fill_pct"], 50.0, places=0)
        self.assertAlmostEqual(pac["expected_pct"], 50.0, delta=1.0)
        self.assertEqual(pac["status"], "on_pace")

    def test_half_window_half_target_explicit(self):
        pac = calorie_pacing(consumed=1000, target=2000, window_fraction=0.5)
        self.assertEqual(pac["paced_budget"], 1000.0)
        self.assertEqual(pac["fill_pct"], 50.0)
        self.assertEqual(pac["expected_pct"], 50.0)

    def test_deficit_left_red(self):
        d = calorie_in_out_delta(intake=1500, burned=2000)
        self.assertEqual(d["delta"], -500.0)
        self.assertEqual(d["side"], "deficit")
        self.assertEqual(d["color"], "red")
        self.assertGreater(d["bar_pct"], 0)
        # Fixed scale ≥1000 → 500/1000 = 50% (not forced to 100%)
        self.assertAlmostEqual(d["bar_pct"], 50.0, places=0)

    def test_surplus_right_green(self):
        d = calorie_in_out_delta(intake=2200, burned=1800)
        self.assertEqual(d["delta"], 400.0)
        self.assertEqual(d["side"], "surplus")
        self.assertEqual(d["color"], "green")
        self.assertGreater(d["bar_pct"], 0)
        self.assertLess(d["bar_pct"], 100.0)

    def test_larger_delta_larger_bar_until_cap(self):
        """bar_pct must grow with |delta|; scale must not absorb |delta|."""
        small = calorie_in_out_delta(intake=1500, burned=2000)  # −500
        mid = calorie_in_out_delta(intake=1000, burned=2000)  # −1000
        huge = calorie_in_out_delta(intake=0, burned=2500)  # −2500
        self.assertEqual(small["side"], "deficit")
        self.assertEqual(mid["side"], "deficit")
        self.assertEqual(huge["side"], "deficit")
        self.assertLess(small["bar_pct"], mid["bar_pct"])
        # mid at −1000 on scale 1000 → 100%; huge also capped at 100
        self.assertAlmostEqual(mid["bar_pct"], 100.0, places=0)
        self.assertEqual(huge["bar_pct"], 100.0)
        # With explicit larger scale, huge stays strictly above mid
        mid_s = calorie_in_out_delta(intake=1000, burned=2000, scale_kcal=3000)
        huge_s = calorie_in_out_delta(intake=0, burned=2500, scale_kcal=3000)
        self.assertLess(mid_s["bar_pct"], huge_s["bar_pct"])
        self.assertLess(huge_s["bar_pct"], 100.0)

    def test_missing_burned(self):
        d = calorie_in_out_delta(intake=1500, burned=None)
        self.assertIsNone(d["delta"])
        self.assertEqual(d["side"], "none")
        self.assertEqual(d["status"], "no_burned")

    def test_civil_day_fallback_without_wake(self):
        now = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc).astimezone()
        win = eating_window_fraction(now=now, last_wake_at=None, empty_at=None)
        self.assertEqual(win["source"], "civil_day_fallback")
        self.assertGreaterEqual(win["fraction"], 0.0)
        self.assertLessEqual(win["fraction"], 1.0)

    def test_future_wake_uses_civil_day_not_7am_window(self):
        now = datetime(2026, 8, 28, 2, 51, 0, tzinfo=timezone.utc)
        future_wake = datetime(2026, 8, 28, 7, 0, 0, tzinfo=timezone.utc)
        win = eating_window_fraction(
            now=now,
            last_wake_at=future_wake,
            empty_at=future_wake + timedelta(hours=15),
        )
        self.assertEqual(win["source"], "civil_day_before_wake")
        self.assertTrue(win["window_start"].startswith("2026-08-28T00:00:00"))

    def test_payload_builder(self):
        wake = datetime(2026, 7, 26, 8, 0, 0, tzinfo=timezone.utc)
        now = wake + timedelta(hours=8)
        payload = build_calorie_bars_payload(
            today_consumed={"calories": 1100},
            targets={"calories": 2000},
            sleep_battery={
                "last_wake_at": wake.isoformat(),
                "empty_at": (wake + timedelta(hours=16)).isoformat(),
                "awake_budget_hours": 16,
            },
            calories_burned_today=1600,
            now=now,
        )
        self.assertIn("pacing", payload)
        self.assertIn("delta", payload)
        self.assertAlmostEqual(payload["pacing"]["window_fraction"], 0.5, places=2)
        self.assertEqual(payload["delta"]["side"], "deficit")
        self.assertEqual(payload["delta"]["color"], "red")

    def test_sum_intake_spans_midnight_window(self):
        """Logs on wake day still count after civil midnight if inside wake→bed."""
        # Use the process local TZ so FoodLog HH:MM aligns with window bounds.
        local = datetime.now().astimezone().tzinfo or timezone.utc
        wake = datetime(2026, 7, 29, 12, 7, 0, tzinfo=local)
        bed = wake + timedelta(hours=16)
        now = datetime(2026, 7, 30, 0, 14, 0, tzinfo=local)
        logs = [
            FoodLogEntry(
                date="2026-07-29",
                name="Lunch",
                calories=800,
                protein_g=50,
                carbs_g=60,
                fat_g=20,
                time="14:00",
            ),
            FoodLogEntry(
                date="2026-07-29",
                name="Dinner",
                calories=600,
                protein_g=40,
                carbs_g=40,
                fat_g=25,
                time="21:13",
            ),
            # Outside window (before wake)
            FoodLogEntry(
                date="2026-07-29",
                name="Early snack",
                calories=200,
                protein_g=5,
                carbs_g=20,
                fat_g=10,
                time="08:00",
            ),
        ]
        got = sum_intake_in_window(
            logs, window_start=wake, window_end=bed, now=now
        )
        self.assertEqual(got["log_count"], 2)
        self.assertEqual(got["calories"], 1400.0)
        self.assertEqual(got["source"], "eating_window_logs")

        # Pacing uses window logs even when civil-day today is 0
        payload = build_calorie_bars_payload(
            today_consumed={"calories": 0},
            targets={"calories": 2000},
            sleep_battery={
                "last_wake_at": wake.isoformat(),
                "empty_at": bed.isoformat(),
                "awake_budget_hours": 16,
            },
            food_logs=logs,
            now=now,
        )
        self.assertEqual(payload["pacing"]["intake_source"], "eating_window_logs")
        self.assertEqual(payload["pacing"]["consumed"], 1400.0)
        # Civil-day in/out still uses today_consumed
        self.assertEqual(payload["delta"]["intake"], 0.0)

    def test_expired_wake_window_falls_back_to_civil_day(self):
        """After empty_at, pacing must not stay pinned to the finished cycle."""
        local = datetime.now().astimezone().tzinfo or timezone.utc
        wake = datetime(2026, 7, 29, 12, 7, 0, tzinfo=local)
        empty = wake + timedelta(hours=16)  # ~04:07 next day
        # Noon next day — well past empty
        now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=local)
        win = eating_window_fraction(
            now=now,
            last_wake_at=wake,
            empty_at=empty,
            awake_budget_hours=16.0,
        )
        self.assertEqual(win["source"], "civil_day_after_empty")
        self.assertLess(win["fraction"], 1.0)
        # Window is today's midnight→midnight, not yesterday's wake
        self.assertTrue(str(win["window_start"]).startswith("2026-07-30"))

        logs = [
            FoodLogEntry(
                date="2026-07-29",
                name="Yesterday dinner",
                calories=2000,
                protein_g=100,
                carbs_g=100,
                fat_g=50,
                time="21:00",
            ),
        ]
        payload = build_calorie_bars_payload(
            today_consumed={"calories": 0},
            targets={"calories": 2000},
            sleep_battery={
                "last_wake_at": wake.isoformat(),
                "empty_at": empty.isoformat(),
                "awake_budget_hours": 16,
            },
            food_logs=logs,
            now=now,
        )
        # Yesterday's meals must not count as today's pacing intake
        self.assertEqual(payload["pacing"]["window"]["source"], "civil_day_after_empty")
        self.assertEqual(payload["pacing"]["consumed"], 0.0)
        self.assertEqual(
            payload["pacing"]["pace_clock"],
            "pace clock = calendar day (after bedtime)",
        )
        self.assertEqual(
            payload["macro_pace"]["pace_clock"],
            "pace clock = calendar day (after bedtime)",
        )

        hydro_win = eating_window_fraction(
            now=now,
            last_wake_at=wake,
            empty_at=empty,
            awake_budget_hours=16.0,
            mode="hydration",
        )
        self.assertEqual(hydro_win["source"], "sleep_battery_after_empty")
        self.assertEqual(hydro_win["fraction"], 1.0)
        self.assertTrue(str(hydro_win["window_start"]).startswith("2026-07-29"))

    def test_pace_vs_expected_green_yellow_red(self):
        # Mid-window, day target 2000 → paced 1000
        on = pace_vs_expected(
            consumed=1000, target=2000, window_fraction=0.5, kind="calories"
        )
        self.assertEqual(on["band"], "green")
        self.assertEqual(on["side"], "on")
        self.assertAlmostEqual(on["paced_expected"], 1000.0, places=0)

        # 12% ahead of paced → yellow (between 5% and 20%)
        yel = pace_vs_expected(
            consumed=1120, target=2000, window_fraction=0.5, kind="calories"
        )
        self.assertEqual(yel["band"], "yellow")
        self.assertEqual(yel["side"], "ahead")

        # 25% behind → red
        red = pace_vs_expected(
            consumed=750, target=2000, window_fraction=0.5, kind="calories"
        )
        self.assertEqual(red["band"], "red")
        self.assertEqual(red["side"], "behind")

    def test_protein_over_looser_than_calories(self):
        # +12% over paced protein stays green; same relative over on cals is yellow
        p = pace_vs_expected(
            consumed=112, target=200, window_fraction=0.5, kind="protein"
        )
        c = pace_vs_expected(
            consumed=1120, target=2000, window_fraction=0.5, kind="calories"
        )
        self.assertEqual(p["band"], "green")
        self.assertEqual(c["band"], "yellow")
        # Under protein still tight
        under = pace_vs_expected(
            consumed=750, target=2000, window_fraction=0.5, kind="protein"
        )
        # paced 1000, consumed 750 → 25% under → red
        self.assertEqual(under["band"], "red")

    def test_payload_includes_macro_pace(self):
        wake = datetime(2026, 7, 26, 8, 0, 0, tzinfo=timezone.utc)
        now = wake + timedelta(hours=8)
        payload = build_calorie_bars_payload(
            today_consumed={
                "calories": 1000,
                "protein_g": 100,
                "carbs_g": 90,
                "fat_g": 30,
            },
            targets={
                "calories": 2000,
                "protein_g": 200,
                "carbs_g": 180,
                "fat_g": 55,
            },
            sleep_battery={
                "last_wake_at": wake.isoformat(),
                "empty_at": (wake + timedelta(hours=16)).isoformat(),
                "awake_budget_hours": 16,
            },
            now=now,
        )
        self.assertIn("macro_pace", payload)
        mp = payload["macro_pace"]
        self.assertIn("calories", mp)
        self.assertIn("protein_g", mp)
        self.assertEqual(mp["calories"]["band"], "green")
        self.assertEqual(payload["pacing"].get("band"), "green")
        self.assertEqual(mp["civil_day"]["calories"], 1000.0)
        self.assertEqual(mp["window_macros"]["calories"], 1000.0)
        self.assertEqual(payload["pacing"]["civil_day"]["protein_g"], 100.0)
        self.assertEqual(payload["pacing"]["pace_clock"], "pace clock = wake window")
        self.assertEqual(mp["pace_clock"], "pace clock = wake window")


class TestCivilDayVsPaceClocks(unittest.TestCase):
    def test_civil_day_macros_zero_missing_never_invent(self):
        self.assertEqual(
            civil_day_macros(None),
            {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0},
        )
        self.assertEqual(civil_day_macros({"calories": 800})["protein_g"], 0.0)
        self.assertEqual(civil_day_macros({"calories": "bad"})["calories"], 0.0)

    def test_pace_clock_copy_after_empty_and_fallbacks(self):
        self.assertEqual(
            pace_clock_copy("civil_day_after_empty"),
            "pace clock = calendar day (after bedtime)",
        )
        self.assertEqual(
            pace_clock_copy("civil_day_fallback"),
            "pace clock = calendar day (no wake yet)",
        )
        self.assertEqual(
            pace_clock_copy("civil_day_before_wake"),
            "pace clock = calendar day (no wake yet)",
        )
        self.assertEqual(pace_clock_copy("sleep_battery"), "pace clock = wake window")
        self.assertEqual(pace_clock_copy(""), "")

    def test_window_macros_differ_from_civil_day_and_delta_stays_civil(self):
        """Pace comparison uses wake-window logs; in/out delta stays calendar day."""
        local = datetime.now().astimezone().tzinfo or timezone.utc
        wake = datetime(2026, 7, 29, 12, 7, 0, tzinfo=local)
        bed = wake + timedelta(hours=16)
        now = datetime(2026, 7, 30, 0, 14, 0, tzinfo=local)
        logs = [
            FoodLogEntry(
                date="2026-07-29",
                name="Dinner",
                calories=900,
                protein_g=60,
                carbs_g=50,
                fat_g=30,
                time="21:00",
            ),
        ]
        payload = build_calorie_bars_payload(
            today_consumed={
                "calories": 200,
                "protein_g": 10,
                "carbs_g": 20,
                "fat_g": 5,
            },
            targets={
                "calories": 2000,
                "protein_g": 200,
                "carbs_g": 180,
                "fat_g": 55,
            },
            sleep_battery={
                "last_wake_at": wake.isoformat(),
                "empty_at": bed.isoformat(),
                "awake_budget_hours": 16,
            },
            calories_burned_today=1600,
            food_logs=logs,
            now=now,
        )
        mp = payload["macro_pace"]
        self.assertEqual(mp["intake_source"], "eating_window_logs")
        self.assertEqual(mp["window_macros"]["calories"], 900.0)
        self.assertEqual(mp["window_macros"]["protein_g"], 60.0)
        self.assertEqual(mp["civil_day"]["calories"], 200.0)
        self.assertEqual(mp["civil_day"]["protein_g"], 10.0)
        self.assertEqual(mp["calories"]["consumed"], 900.0)
        self.assertNotEqual(mp["window_macros"]["calories"], mp["civil_day"]["calories"])
        # In/out SoT is civil day, not wake-window pace
        self.assertEqual(payload["delta"]["intake"], 200.0)
        self.assertEqual(payload["delta"]["burned"], 1600.0)
        self.assertEqual(payload["pacing"]["pace_clock"], "pace clock = wake window")


class TestCalorieBarCardLayout(unittest.TestCase):
    def test_pacing_and_delta_meta_are_card_footers(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "static" / "index.html").read_text(encoding="utf-8")
        css = (root / "static" / "styles.css").read_text(encoding="utf-8")

        pace = html[
            html.find('id="calorie-pacing-section"') : html.find(
                'id="calorie-delta-section"'
            )
        ]
        delta = html[
            html.find('id="calorie-delta-section"') : html.find('id="nutrition-stats"')
        ]
        pace_header = pace[pace.find("calorie-bar-header") : pace.find("pace-track")]
        delta_header = delta[delta.find("calorie-bar-header") : delta.find("delta-track")]

        self.assertNotIn("calorie-pacing-meta", pace_header)
        self.assertNotIn("calorie-delta-meta", delta_header)
        self.assertGreater(
            pace.find('id="calorie-pacing-meta"'),
            pace.find('id="calorie-pacing-summary"'),
        )
        self.assertGreater(
            delta.find('id="calorie-delta-meta"'),
            delta.find('id="calorie-delta-summary"'),
        )
        self.assertIn("calorie-bar-footer", pace)
        self.assertIn("calorie-bar-footer", delta)
        self.assertIn(".calorie-bar-footer", css)
        # Hydration footer still last in its own card.
        self.assertGreater(
            html.find('id="hydration-pacing-meta"'),
            html.find('id="hydration-pacing-summary"'),
        )

    def test_today_so_far_copy_separates_pace_from_calendar_day(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "static" / "index.html").read_text(encoding="utf-8")
        js = (root / "static" / "app.js").read_text(encoding="utf-8")
        so_far = html[
            html.find('id="today-so-far-card"') : html.find('id="meal-plan-card"')
        ]
        tiles = html[
            html.find('id="nutrition-stats"') : html.find('id="nutrition-micros"')
        ]
        delta = html[
            html.find('id="calorie-delta-section"') : html.find('id="nutrition-stats"')
        ]
        self.assertIn("Logged today (calendar day)", so_far)
        self.assertIn("After bedtime, pace falls back to the calendar day", so_far)
        self.assertIn("target hit %", so_far)
        self.assertIn("not the pace score", so_far)
        self.assertIn("Logged today", tiles)
        self.assertIn("calendar day", tiles)
        self.assertNotIn('macro-split-k">Today<', tiles)
        self.assertIn("calendar day", delta)
        self.assertIn("paceRowIntake", js)
        self.assertIn("formatLoggedTodayCalendarLine", js)
        self.assertIn("logged today (calendar day)", js)
        self.assertIn("target hit", js)
        # Pace rows must paint wake-window consumed, not civil today_consumed
        progress = js.split("function progressRow", 1)[1].split(
            "function fillMacroSplit", 1
        )[0]
        self.assertIn("paceRowIntake(pace, consumed)", progress)
        self.assertIn("fmtNum(intake)", progress)
        self.assertNotIn("fmtNum(consumed)", progress)
        legend = js.split("function renderTargetsAndRemaining", 1)[1].split(
            "function renderFoodLogsToday", 1
        )[0]
        self.assertIn("wake-window intake", legend)
        self.assertIn("After bedtime, pace uses the calendar day", legend)
        self.assertIn("formatLoggedTodayCalendarLine", legend)
        self.assertIn("civil_day", legend)
        # In/out delta stays civil day and is labeled as such
        bars = js.split("function renderCalorieBars", 1)[1].split(
            "function renderTargetsAndRemaining", 1
        )[0]
        self.assertIn("loggedTodayCalendarLabel()", bars)
        self.assertIn("delta.intake", bars)
        self.assertIn("pace_clock", bars)

    def test_js_helpers_keep_window_intake_off_civil_totals(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "tests" / "pace_civil_day_labels.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(root),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok pace-civil-day-labels", proc.stdout)


if __name__ == "__main__":
    unittest.main()
