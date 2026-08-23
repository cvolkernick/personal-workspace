"""Unit tests for hydration wake-window pacing."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from rt_dashboard.hydration_bars import (
    DEFAULT_HYDRATION_GOAL_ML,
    build_hydration_bars_payload,
    hydration_pacing,
    hydration_target_ml_from_lbs,
    latest_weight_lbs,
    timed_sip_samples,
    water_ml_for_day,
    water_ml_for_window,
)
from rt_dashboard.models import HydrationDay, WeightSample

ET = ZoneInfo("America/New_York")


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
            samples=[
                {
                    "logged_at": (wake + timedelta(hours=1)).isoformat(),
                    "water_ml": 1600,
                    "source": "hidrate",
                }
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
        self.assertTrue(pac["sip_aware"])
        self.assertEqual(pac["sip_count"], 1)
        self.assertIn(pac.get("band"), ("green", "yellow", "red", "muted"))

    def test_pacing_without_sips_is_unknown_not_on_pace(self):
        pac = hydration_pacing(
            consumed_ml=0, target_ml=3000, window_fraction=0.03, sip_aware=False
        )
        self.assertEqual(pac["status"], "unknown")
        self.assertIsNone(pac["consumed"])
        self.assertIsNone(pac["delta_vs_pace"])
        self.assertFalse(pac["sip_aware"])
        self.assertNotEqual(pac["status"], "on_pace")

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
        self.assertIsNone(payload["pacing"]["consumed_ml"])
        self.assertEqual(payload["pacing"]["status"], "unknown")
        self.assertFalse(payload["pacing"]["sip_aware"])

    def test_civil_day_row_alone_is_not_wake_actual(self):
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
        self.assertEqual(payload["day"]["water_ml"], 1600.0)
        self.assertIsNone(payload["pacing"]["consumed_ml"])
        self.assertEqual(payload["pacing"]["intake_source"], "none")
        self.assertEqual(payload["pacing"]["status"], "unknown")
        self.assertFalse(payload["pacing"]["sip_aware"])
        self.assertNotEqual(payload["pacing"]["status"], "on_pace")
        self.assertNotEqual(payload["pacing"].get("band"), "green")

    def test_civil_only_series_does_not_fake_midnight_split(self):
        """Date-only Day totals must not be split or summed across midnight."""
        wake = datetime(2026, 8, 22, 22, 0, 0, tzinfo=ET)
        now = datetime(2026, 8, 23, 0, 30, 0, tzinfo=ET)
        payload = build_hydration_bars_payload(
            hydration=[
                HydrationDay(date="2026-08-22", water_ml=700, source="hidrate"),
                HydrationDay(date="2026-08-23", water_ml=150, source="hidrate"),
            ],
            samples=[],
            sleep_battery={
                "last_wake_at": wake.isoformat(),
                "empty_at": (wake + timedelta(hours=16)).isoformat(),
                "awake_budget_hours": 16,
            },
            as_of="2026-08-23",
            now=now,
            tz_name="America/New_York",
        )
        self.assertIsNone(payload["pacing"]["consumed_ml"])
        self.assertNotEqual(payload["pacing"]["consumed_ml"], 850.0)
        self.assertNotEqual(payload["pacing"]["consumed_ml"], 150.0)
        self.assertEqual(payload["day"]["water_ml"], 150.0)
        self.assertEqual(payload["pacing"]["status"], "unknown")
        self.assertIsNone(payload["pacing"]["paced_budget_ml"])
        self.assertNotEqual(payload["pacing"]["status"], "on_pace")

    def test_cross_midnight_retains_prior_evening_ml(self):
        wake = datetime(2026, 8, 22, 22, 0, 0, tzinfo=ET)
        now = datetime(2026, 8, 23, 0, 30, 0, tzinfo=ET)
        samples = [
            {
                "logged_at": datetime(2026, 8, 22, 21, 0, tzinfo=ET).isoformat(),
                "water_ml": 300,
                "source": "hidrate",
            },
            {
                "logged_at": datetime(2026, 8, 22, 22, 30, tzinfo=ET).isoformat(),
                "water_ml": 400,
                "source": "hidrate",
            },
            {
                "logged_at": datetime(2026, 8, 23, 0, 15, tzinfo=ET).isoformat(),
                "water_ml": 150,
                "source": "hidrate",
            },
        ]
        payload = build_hydration_bars_payload(
            hydration=[
                HydrationDay(date="2026-08-22", water_ml=700, source="hidrate"),
                HydrationDay(date="2026-08-23", water_ml=150, source="hidrate"),
            ],
            samples=samples,
            weight=[WeightSample(date="2026-08-20", weight_lbs=200)],
            sleep_battery={
                "last_wake_at": wake.isoformat(),
                "empty_at": (wake + timedelta(hours=16)).isoformat(),
                "awake_budget_hours": 16,
            },
            as_of="2026-08-23",
            now=now,
            tz_name="America/New_York",
        )
        pac = payload["pacing"]
        self.assertEqual(pac["window"]["source"], "sleep_battery")
        self.assertEqual(pac["consumed_ml"], 550.0)
        self.assertTrue(pac["sip_aware"])
        self.assertEqual(pac["sip_count"], 2)
        self.assertIn(pac["status"], ("on_pace", "ahead", "behind", "start"))
        self.assertEqual(payload["day"]["water_ml"], 150.0)
        self.assertEqual(payload["day"]["date"], "2026-08-23")
        self.assertNotEqual(pac["consumed_ml"], payload["day"]["water_ml"])

    def test_civil_day_flip_alone_does_not_zero_wake_actual(self):
        wake = datetime(2026, 8, 22, 22, 0, 0, tzinfo=ET)
        just_after_midnight = datetime(2026, 8, 23, 0, 5, 0, tzinfo=ET)
        samples = [
            {
                "logged_at": datetime(2026, 8, 22, 23, 10, tzinfo=ET).isoformat(),
                "water_ml": 500,
                "source": "hidrate",
            }
        ]
        payload = build_hydration_bars_payload(
            hydration=[
                HydrationDay(date="2026-08-22", water_ml=500, source="hidrate"),
            ],
            samples=samples,
            sleep_battery={
                "last_wake_at": wake.isoformat(),
                "empty_at": (wake + timedelta(hours=16)).isoformat(),
                "awake_budget_hours": 16,
            },
            as_of="2026-08-23",
            now=just_after_midnight,
            tz_name="America/New_York",
        )
        self.assertEqual(payload["pacing"]["consumed_ml"], 500.0)
        self.assertEqual(payload["day"]["water_ml"], 0.0)

    def test_new_wake_excludes_prior_window_ml(self):
        old_wake = datetime(2026, 8, 22, 22, 0, 0, tzinfo=ET)
        new_wake = datetime(2026, 8, 23, 7, 0, 0, tzinfo=ET)
        now = datetime(2026, 8, 23, 8, 0, 0, tzinfo=ET)
        samples = [
            {
                "logged_at": datetime(2026, 8, 22, 22, 30, tzinfo=ET).isoformat(),
                "water_ml": 400,
                "source": "hidrate",
            },
            {
                "logged_at": datetime(2026, 8, 23, 7, 20, tzinfo=ET).isoformat(),
                "water_ml": 180,
                "source": "hidrate",
            },
        ]
        payload = build_hydration_bars_payload(
            hydration=[
                HydrationDay(date="2026-08-23", water_ml=180, source="hidrate"),
            ],
            samples=samples,
            sleep_battery={
                "last_wake_at": new_wake.isoformat(),
                "empty_at": (new_wake + timedelta(hours=16)).isoformat(),
                "awake_budget_hours": 16,
            },
            as_of="2026-08-23",
            now=now,
            tz_name="America/New_York",
        )
        self.assertEqual(payload["pacing"]["consumed_ml"], 180.0)
        self.assertNotEqual(payload["pacing"]["consumed_ml"], 580.0)
        still_in_old = water_ml_for_window(
            samples,
            window_start=old_wake,
            window_end=old_wake + timedelta(hours=16),
            now=datetime(2026, 8, 23, 6, 50, tzinfo=ET),
        )
        self.assertEqual(still_in_old["water_ml"], 400.0)

    def test_no_wake_is_honest_empty(self):
        now = datetime(2026, 8, 23, 0, 30, 0, tzinfo=ET)
        payload = build_hydration_bars_payload(
            hydration=[
                HydrationDay(date="2026-08-23", water_ml=900, source="hidrate"),
            ],
            samples=[
                {
                    "logged_at": datetime(2026, 8, 22, 23, 0, tzinfo=ET).isoformat(),
                    "water_ml": 400,
                    "source": "hidrate",
                }
            ],
            sleep_battery={},
            as_of="2026-08-23",
            now=now,
            tz_name="America/New_York",
        )
        self.assertIsNone(payload["pacing"]["consumed_ml"])
        self.assertEqual(payload["pacing"]["intake_source"], "none")
        self.assertEqual(payload["pacing"]["status"], "unknown")
        self.assertFalse(payload["pacing"]["sip_aware"])
        self.assertEqual(payload["day"]["water_ml"], 900.0)

    def test_window_helper_skips_date_only_and_missing_amount(self):
        wake = datetime(2026, 8, 22, 22, 0, 0, tzinfo=ET)
        now = datetime(2026, 8, 23, 0, 30, 0, tzinfo=ET)
        got = water_ml_for_window(
            [
                HydrationDay(date="2026-08-22", water_ml=700, source="hidrate"),
                {"logged_at": (wake + timedelta(minutes=10)).isoformat()},
                {
                    "logged_at": (wake + timedelta(minutes=20)).isoformat(),
                    "water_ml": 120,
                    "source": "hidrate",
                },
            ],
            window_start=wake,
            window_end=wake + timedelta(hours=16),
            now=now,
        )
        self.assertEqual(got["water_ml"], 120.0)
        self.assertEqual(got["sample_count"], 1)

    def test_early_window_civil_only_is_not_green_on_pace(self):
        """Civil midnight total + just-awake must not call pace green."""
        wake = datetime(2026, 8, 10, 7, 0, 0, tzinfo=ET)
        now = wake + timedelta(minutes=20)
        payload = build_hydration_bars_payload(
            hydration=[
                HydrationDay(date="2026-08-10", water_ml=1800, source="hidrate")
            ],
            samples=[],
            weight=[WeightSample(date="2026-08-09", weight_lbs=200)],
            sleep_battery={
                "last_wake_at": wake.isoformat(),
                "empty_at": (wake + timedelta(hours=16)).isoformat(),
                "awake_budget_hours": 16,
            },
            as_of="2026-08-10",
            now=now,
            tz_name="America/New_York",
        )
        pac = payload["pacing"]
        self.assertEqual(pac["status"], "unknown")
        self.assertFalse(pac["sip_aware"])
        self.assertIsNone(pac["consumed_ml"])
        self.assertNotEqual(pac["status"], "on_pace")
        self.assertNotEqual(pac.get("band"), "green")
        self.assertEqual(payload["day"]["water_ml"], 1800.0)
        self.assertEqual(len(timed_sip_samples([], payload.get("hydration"))), 0)

    def test_sips_outside_window_still_sip_aware(self):
        """Timestamps exist but none in this wake → paced 0, not unknown."""
        wake = datetime(2026, 8, 23, 7, 0, 0, tzinfo=ET)
        now = datetime(2026, 8, 23, 8, 0, 0, tzinfo=ET)
        payload = build_hydration_bars_payload(
            samples=[
                {
                    "logged_at": datetime(2026, 8, 22, 22, 30, tzinfo=ET).isoformat(),
                    "water_ml": 400,
                    "source": "hidrate",
                }
            ],
            sleep_battery={
                "last_wake_at": wake.isoformat(),
                "empty_at": (wake + timedelta(hours=16)).isoformat(),
                "awake_budget_hours": 16,
            },
            as_of="2026-08-23",
            now=now,
            tz_name="America/New_York",
        )
        pac = payload["pacing"]
        self.assertTrue(pac["sip_aware"])
        self.assertEqual(pac["consumed_ml"], 0.0)
        self.assertEqual(pac["sip_count"], 0)
        self.assertIn(pac["status"], ("on_pace", "ahead", "behind", "start"))


if __name__ == "__main__":
    unittest.main()
