"""Unit tests for hydration wake-window pacing."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from rt_dashboard.hydration_bars import (
    DEFAULT_HYDRATION_GOAL_ML,
    build_hydration_bars_payload,
    hydration_pacing,
    hydration_target_ml_from_lbs,
    latest_weight_lbs,
    water_ml_for_day,
)
from rt_dashboard.models import HydrationDay, WeightSample


class TestHydrationBars(unittest.TestCase):
    def test_target_from_lbs(self):
        # 200 lb → ~90.72 kg × 35 ≈ 3175 ml
        t = hydration_target_ml_from_lbs(200)
        self.assertIsNotNone(t)
        self.assertAlmostEqual(t, 3175, delta=5)
        self.assertIsNone(hydration_target_ml_from_lbs(0))
        self.assertIsNone(hydration_target_ml_from_lbs(None))

    def test_mid_window_on_pace(self):
        pac = hydration_pacing(
            consumed_ml=1500, target_ml=3000, window_fraction=0.5
        )
        self.assertAlmostEqual(pac["paced_budget_ml"], 1500.0, places=0)
        self.assertEqual(pac["status"], "on_pace")
        self.assertAlmostEqual(pac["fill_pct"], 50.0, places=0)
        self.assertAlmostEqual(pac["expected_pct"], 50.0, places=0)

    def test_behind_and_ahead(self):
        behind = hydration_pacing(
            consumed_ml=500, target_ml=3000, window_fraction=0.5
        )
        self.assertEqual(behind["status"], "behind")
        self.assertLess(behind["delta_vs_pace"], 0)
        ahead = hydration_pacing(
            consumed_ml=2500, target_ml=3000, window_fraction=0.5
        )
        self.assertEqual(ahead["status"], "ahead")
        self.assertGreater(ahead["delta_vs_pace"], 0)

    def test_water_ml_for_day(self):
        series = [
            HydrationDay(date="2026-08-09", water_ml=2000, source="hidrate"),
            HydrationDay(date="2026-08-10", water_ml=1200, source="hidrate"),
        ]
        got = water_ml_for_day(series, as_of="2026-08-10")
        self.assertEqual(got["water_ml"], 1200.0)
        self.assertEqual(got["source"], "hidrate")
        missing = water_ml_for_day(series, as_of="2026-08-11")
        self.assertEqual(missing["water_ml"], 0.0)
        self.assertEqual(missing["source"], "none")

    def test_latest_weight_as_of(self):
        w = [
            WeightSample(date="2026-08-01", weight_lbs=205),
            WeightSample(date="2026-08-08", weight_lbs=202),
            WeightSample(date="2026-08-12", weight_lbs=200),  # after as_of
        ]
        got = latest_weight_lbs(w, as_of="2026-08-10")
        self.assertIsNotNone(got)
        self.assertEqual(got["date"], "2026-08-08")
        self.assertEqual(got["weight_lbs"], 202.0)

    def test_payload_mid_window(self):
        wake = datetime(2026, 8, 10, 7, 0, 0, tzinfo=timezone.utc)
        now = wake + timedelta(hours=8)
        payload = build_hydration_bars_payload(
            hydration=[
                HydrationDay(date="2026-08-10", water_ml=1600, source="hidrate")
            ],
            weight=[WeightSample(date="2026-08-09", weight_lbs=200)],
            sleep_battery={
                "last_wake_at": wake.isoformat(),
                "empty_at": (wake + timedelta(hours=16)).isoformat(),
                "awake_budget_hours": 16,
            },
            as_of="2026-08-10",
            now=now,
        )
        self.assertIn("pacing", payload)
        pac = payload["pacing"]
        self.assertAlmostEqual(pac["window_fraction"], 0.5, places=2)
        self.assertEqual(pac["consumed_ml"], 1600.0)
        self.assertEqual(pac["target_source"], "weight_35ml_kg")
        # 200 lb → ~3175; paced ~1588; consumed 1600 → on pace
        self.assertEqual(pac["status"], "on_pace")
        self.assertEqual(pac["intake_source"], "hidrate")
        self.assertIn(pac.get("band"), ("green", "yellow", "red", "muted"))

    def test_default_target_without_weight(self):
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
        payload = build_hydration_bars_payload(
            hydration=[],
            weight=[],
            sleep_battery={},
            as_of="2026-08-10",
            now=now,
        )
        self.assertEqual(payload["target_source"], "default")
        self.assertEqual(payload["target_ml"], DEFAULT_HYDRATION_GOAL_ML)
        self.assertEqual(payload["pacing"]["consumed_ml"], 0.0)


if __name__ == "__main__":
    unittest.main()
