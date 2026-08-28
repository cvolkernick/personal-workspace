"""Sleep battery (partial-charge-at-wake / drain-awake) for FitDash recovery."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from rt_dashboard.models import SleepSample
from rt_dashboard.sleep_battery import (
    compute_sleep_battery,
    intervals_from_daily_sleep,
    sleep_battery_from_fitdash_sleep,
    start_charge_fraction,
)

NY = ZoneInfo("America/New_York")


class TestStartChargeFraction(unittest.TestCase):
    def test_full_night_is_full_charge(self):
        ch = start_charge_fraction(8.0, sleep_target_hours=8.0, awake_budget_hours=16.0)
        self.assertEqual(ch["start_frac"], 1.0)
        self.assertEqual(ch["budget_hours_at_start"], 16.0)

    def test_short_night_soft_capped(self):
        # 5h → proportional 62.5%, but 2h earlier cap → floor 87.5%
        ch = start_charge_fraction(
            5.0,
            sleep_target_hours=8.0,
            awake_budget_hours=16.0,
            max_earlier_hours=2.0,
        )
        self.assertAlmostEqual(ch["proportional_frac"], 5.0 / 8.0)
        self.assertAlmostEqual(ch["floor_frac"], 14.0 / 16.0)
        self.assertAlmostEqual(ch["start_frac"], 14.0 / 16.0)
        self.assertAlmostEqual(ch["budget_hours_at_start"], 14.0)

    def test_uncapped_allows_full_proportional(self):
        ch = start_charge_fraction(
            5.0,
            sleep_target_hours=8.0,
            awake_budget_hours=16.0,
            max_earlier_hours=0.0,
        )
        self.assertAlmostEqual(ch["start_frac"], 5.0 / 8.0)
        self.assertAlmostEqual(ch["budget_hours_at_start"], 10.0)

    def test_mild_shortfall_uses_proportional_above_floor(self):
        # 7.5h of 8 → 93.75% > 87.5% floor
        ch = start_charge_fraction(
            7.5,
            sleep_target_hours=8.0,
            awake_budget_hours=16.0,
            max_earlier_hours=2.0,
        )
        self.assertAlmostEqual(ch["start_frac"], 7.5 / 8.0)


class TestSleepBattery(unittest.TestCase):
    def test_full_night_reserves_9h_around_sleep(self):
        tz = timezone.utc
        # 8h sleep + 1h (30m wind-down + 30m onset) → 15h awake budget
        wake = datetime(2026, 7, 20, 7, 0, 0, tzinfo=tz)
        intervals = [
            {
                "start": (wake - timedelta(hours=8)).isoformat(),
                "end": wake.isoformat(),
                "source": "test",
            }
        ]
        bat = compute_sleep_battery(intervals, now=wake, sleep_target_hours=8.0)
        self.assertEqual(bat["awake_budget_hours"], 15.0)
        self.assertEqual(bat["onset_buffer_hours"], 1.0)
        self.assertEqual(bat["sleep_around_hours"], 9.0)
        self.assertEqual(bat["start_pct_charged"], 100.0)
        empty = datetime.fromisoformat(bat["empty_at"])
        self.assertEqual(empty, wake + timedelta(hours=15))

    def test_full_just_after_wake(self):
        tz = timezone.utc
        # Woke at 07:00 after 8h, now 08:00 → 14/15 ≈ 93.3% of 15h budget
        wake = datetime(2026, 7, 20, 7, 0, 0, tzinfo=tz)
        sleep_start = wake - timedelta(hours=8)
        intervals = [
            {
                "start": sleep_start.isoformat(),
                "end": wake.isoformat(),
                "source": "test",
            }
        ]
        now = wake + timedelta(hours=1)
        bat = compute_sleep_battery(intervals, now=now, sleep_target_hours=8.0)
        self.assertEqual(bat["mode"], "awake")
        self.assertEqual(bat["model"], "wake_partial_drain_awake")
        self.assertAlmostEqual(bat["awake_budget_hours"], 15.0)
        self.assertGreaterEqual(bat["pct_charged"], 90)
        self.assertAlmostEqual(bat["pct_charged"], 100.0 * 14.0 / 15.0, places=0)
        self.assertEqual(bat["start_pct_charged"], 100.0)
        self.assertEqual(bat["level"], "full")

    def test_short_night_starts_partial_and_empties_earlier(self):
        tz = timezone.utc
        wake = datetime(2026, 7, 20, 7, 0, 0, tzinfo=tz)
        # 5h sleep → floor 13/15 ≈ 86.7%, empty at wake+13h (not +15h)
        intervals = [
            {
                "start": (wake - timedelta(hours=5)).isoformat(),
                "end": wake.isoformat(),
                "source": "test",
            }
        ]
        now = wake  # just woke
        bat = compute_sleep_battery(
            intervals, now=now, sleep_target_hours=8.0, max_earlier_hours=2.0
        )
        self.assertEqual(bat["mode"], "awake")
        self.assertAlmostEqual(bat["start_pct_charged"], 100.0 * 13.0 / 15.0, places=1)
        self.assertAlmostEqual(bat["proportional_start_pct"], 62.5)
        self.assertAlmostEqual(bat["charge_budget_hours"], 13.0)
        self.assertAlmostEqual(bat["pct_charged"], 100.0 * 13.0 / 15.0, places=1)
        self.assertAlmostEqual(bat["hours_until_empty"], 13.0)
        empty = datetime.fromisoformat(bat["empty_at"])
        self.assertEqual(empty, wake + timedelta(hours=13))

    def test_short_night_midday_pct(self):
        tz = timezone.utc
        wake = datetime(2026, 7, 20, 7, 0, 0, tzinfo=tz)
        intervals = [
            {
                "start": (wake - timedelta(hours=5)).isoformat(),
                "end": wake.isoformat(),
                "source": "test",
            }
        ]
        # 7h awake: remaining_frac = 13/15 - 7/15 = 6/15 → 40%
        now = wake + timedelta(hours=7)
        bat = compute_sleep_battery(
            intervals, now=now, sleep_target_hours=8.0, max_earlier_hours=2.0
        )
        self.assertAlmostEqual(bat["pct_charged"], 40.0, places=0)
        self.assertAlmostEqual(bat["hours_until_empty"], 6.0)

    def test_empty_after_long_awake(self):
        tz = timezone.utc
        wake = datetime(2026, 7, 20, 7, 0, 0, tzinfo=tz)
        intervals = [
            {
                "start": (wake - timedelta(hours=8)).isoformat(),
                "end": wake.isoformat(),
                "source": "test",
            }
        ]
        now = wake + timedelta(hours=17)
        bat = compute_sleep_battery(intervals, now=now, sleep_target_hours=8.0)
        self.assertEqual(bat["mode"], "awake")
        self.assertEqual(bat["pct_charged"], 0.0)
        self.assertEqual(bat["level"], "critical")

    def test_from_daily_samples_skips_zeros(self):
        sleep = [
            SleepSample(date="2026-07-20", sleep_hours=0.0, source="implied_zero"),
            SleepSample(date="2026-07-21", sleep_hours=8.0, source="google_health"),
        ]
        iv = intervals_from_daily_sleep(sleep)
        self.assertEqual(len(iv), 1)
        bat = sleep_battery_from_fitdash_sleep(sleep)
        self.assertIn(bat["data_source"], ("daily_sleep_approx", "none"))

    def test_prefers_timed_intervals_over_daily_approx(self):
        tz = timezone.utc
        wake = datetime(2026, 7, 25, 16, 26, 0, tzinfo=tz)  # 12:26 EDT
        intervals = [
            {
                "start": (wake - timedelta(hours=8.23)).isoformat(),
                "end": wake.isoformat(),
                "source": "google_health",
            }
        ]
        # Misleading daily approx would assume 07:00 wake
        sleep = [SleepSample(date="2026-07-25", sleep_hours=8.23)]
        now = wake + timedelta(hours=1.24)
        bat = sleep_battery_from_fitdash_sleep(
            sleep, now=now, sleep_intervals=intervals
        )
        self.assertEqual(bat["data_source"], "sleep_intervals")
        self.assertGreaterEqual(bat["pct_charged"], 85)
        self.assertLess(bat["hours_awake"], 2.0)

    def test_daily_fill_when_intervals_lag_newer_night(self):
        """Stale timed intervals + newer daily total → fill so wake advances."""
        tz = timezone.utc
        old_wake = datetime(2026, 7, 28, 16, 0, 0, tzinfo=tz)
        intervals = [
            {
                "start": (old_wake - timedelta(hours=8)).isoformat(),
                "end": old_wake.isoformat(),
                "source": "google_health",
            }
        ]
        # Newer night only in daily totals (intervals lag / partial API)
        sleep = [
            SleepSample(date="2026-07-28", sleep_hours=8.0, source="google_health"),
            SleepSample(date="2026-07-29", sleep_hours=8.0, source="google_health"),
        ]
        now = datetime(2026, 7, 29, 12, 0, 0, tzinfo=tz)
        bat = sleep_battery_from_fitdash_sleep(
            sleep, now=now, sleep_intervals=intervals, sleep_target_hours=8.0
        )
        self.assertEqual(bat["data_source"], "sleep_intervals+daily_fill")
        self.assertIsNotNone(bat["last_wake_at"])
        self.assertGreater(bat["last_wake_at"], old_wake.isoformat())

    def test_sleeping_does_not_claim_future_planned_wake(self):
        """In-progress night ending at 7am must not set last_wake_at to 7am."""
        now = datetime(2026, 8, 28, 2, 51, tzinfo=NY)
        planned = datetime(2026, 8, 28, 7, 0, tzinfo=NY)
        start = now - timedelta(hours=4.6)
        intervals = [
            {
                "start": start.isoformat(),
                "end": planned.isoformat(),
                "source": "google_health",
            }
        ]
        bat = compute_sleep_battery(intervals, now=now, sleep_target_hours=8.0)
        self.assertEqual(bat["mode"], "sleeping")
        self.assertIsNone(bat["last_wake_at"])
        self.assertEqual(
            datetime.fromisoformat(bat["planned_wake_at"]),
            planned,
        )
        self.assertAlmostEqual(bat["last_sleep_hours"], 4.6, places=1)
        self.assertEqual(bat["hours_awake"], 0.0)

    def test_daily_fill_skips_future_7am_before_wake(self):
        """Partial Friday hours must not become 'woke Fri 7am' at 2:51am."""
        now = datetime(2026, 8, 28, 2, 51, tzinfo=NY)
        timed_wake = datetime(2026, 8, 27, 23, 34, tzinfo=NY)
        intervals = [
            {
                "start": (timed_wake - timedelta(hours=8.72)).isoformat(),
                "end": timed_wake.isoformat(),
                "source": "google_health",
            }
        ]
        sleep = [
            SleepSample(date="2026-08-27", sleep_hours=8.72, source="google_health"),
            SleepSample(date="2026-08-28", sleep_hours=4.6, source="google_health"),
        ]
        bat = sleep_battery_from_fitdash_sleep(
            sleep,
            now=now,
            tz_name="America/New_York",
            sleep_intervals=intervals,
            sleep_target_hours=8.0,
        )
        self.assertEqual(bat["data_source"], "sleep_intervals")
        wake = datetime.fromisoformat(bat["last_wake_at"])
        self.assertEqual(wake, timed_wake)
        self.assertNotEqual(wake.strftime("%H:%M"), "07:00")
        self.assertIsNone(bat["planned_wake_at"])

    def test_daily_approx_omits_today_until_assumed_wake(self):
        now = datetime(2026, 8, 28, 2, 51, tzinfo=NY)
        sleep = [
            SleepSample(date="2026-08-27", sleep_hours=8.72, source="google_health"),
            SleepSample(date="2026-08-28", sleep_hours=4.6, source="google_health"),
        ]
        iv = intervals_from_daily_sleep(sleep, tz=NY, now=now)
        ends = [row["end"] for row in iv]
        self.assertTrue(any("2026-08-27T07:00:00" in e for e in ends))
        self.assertFalse(any("2026-08-28T07:00:00" in e for e in ends))
        bat = sleep_battery_from_fitdash_sleep(
            sleep, now=now, tz_name="America/New_York"
        )
        wake = datetime.fromisoformat(bat["last_wake_at"])
        self.assertEqual(wake.strftime("%Y-%m-%d %H:%M"), "2026-08-27 07:00")


if __name__ == "__main__":
    unittest.main()
