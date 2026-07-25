"""Sleep battery (wake-full / drain-awake) for FitDash recovery."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from rt_dashboard.models import SleepSample
from rt_dashboard.sleep_battery import (
    compute_sleep_battery,
    intervals_from_daily_sleep,
    sleep_battery_from_fitdash_sleep,
)


class TestSleepBattery(unittest.TestCase):
    def test_full_just_after_wake(self):
        tz = timezone.utc
        # Woke at 07:00, now 08:00 → ~94% left of 16h budget
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
        self.assertGreaterEqual(bat["pct_charged"], 90)
        self.assertEqual(bat["level"], "full")

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


if __name__ == "__main__":
    unittest.main()
