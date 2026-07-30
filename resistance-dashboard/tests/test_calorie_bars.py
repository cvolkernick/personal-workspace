"""Unit tests for calorie pacing + in/out delta helpers (shipped functions)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from rt_dashboard.calorie_bars import (
    build_calorie_bars_payload,
    calorie_in_out_delta,
    calorie_pacing,
    eating_window_fraction,
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


if __name__ == "__main__":
    unittest.main()
