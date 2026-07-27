"""Unit tests for calorie pacing + in/out delta helpers (shipped functions)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from rt_dashboard.calorie_bars import (
    build_calorie_bars_payload,
    calorie_in_out_delta,
    calorie_pacing,
    eating_window_fraction,
)


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

    def test_surplus_right_green(self):
        d = calorie_in_out_delta(intake=2200, burned=1800)
        self.assertEqual(d["delta"], 400.0)
        self.assertEqual(d["side"], "surplus")
        self.assertEqual(d["color"], "green")
        self.assertGreater(d["bar_pct"], 0)

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


if __name__ == "__main__":
    unittest.main()
