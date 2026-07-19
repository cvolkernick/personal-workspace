"""Rolling 24h sleep battery tests."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.sleep_battery import (  # noqa: E402
    compute_sleep_battery,
    sleep_battery_for_state,
)


class SleepBatteryTests(unittest.TestCase):
    def test_full_night_still_in_window_at_evening(self) -> None:
        # Slept 22:00 day0 → 06:00 day1. At 18:00 day1, full 8h still in last 24h.
        tz = timezone(timedelta(hours=-4))
        day0 = datetime(2026, 7, 17, 22, 0, tzinfo=tz)
        day1_wake = datetime(2026, 7, 18, 6, 0, tzinfo=tz)
        now = datetime(2026, 7, 18, 18, 0, tzinfo=tz)
        intervals = [
            {"start": day0.isoformat(), "end": day1_wake.isoformat(), "source": "test"}
        ]
        bat = compute_sleep_battery(intervals, now=now, target_hours=8.0)
        self.assertAlmostEqual(bat["asleep_hours"], 8.0, places=2)
        self.assertEqual(bat["level"], "full")

    def test_hours_discharge_after_trailing_edge_passes_onset(self) -> None:
        # Same night; at 23:00 day1 (1h after 22:00), 1h has left the window → 7h left.
        tz = timezone(timedelta(hours=-4))
        intervals = [
            {
                "start": datetime(2026, 7, 17, 22, 0, tzinfo=tz).isoformat(),
                "end": datetime(2026, 7, 18, 6, 0, tzinfo=tz).isoformat(),
                "source": "test",
            }
        ]
        now = datetime(2026, 7, 18, 23, 0, tzinfo=tz)
        bat = compute_sleep_battery(intervals, now=now, target_hours=8.0)
        self.assertAlmostEqual(bat["asleep_hours"], 7.0, places=2)
        self.assertGreater(bat["discharge_next_hour_hours"], 0.9)

    def test_mid_window_partial(self) -> None:
        # At 02:00 during sleep 22:00–06:00 → 4h so far in window
        tz = timezone(timedelta(hours=-4))
        intervals = [
            {
                "start": datetime(2026, 7, 17, 22, 0, tzinfo=tz).isoformat(),
                "end": datetime(2026, 7, 18, 6, 0, tzinfo=tz).isoformat(),
                "source": "test",
            }
        ]
        now = datetime(2026, 7, 18, 2, 0, tzinfo=tz)
        bat = compute_sleep_battery(intervals, now=now, target_hours=8.0)
        self.assertAlmostEqual(bat["asleep_hours"], 4.0, places=2)

    def test_state_fallback_daily_logs(self) -> None:
        state = {
            "targets": [{"id": "sleep", "target": 8.0}],
            "logs": [{"date": "2026-07-18", "target_id": "sleep", "value": 7.5}],
            "sleep_intervals": [],
        }
        bat = sleep_battery_for_state(
            state, now=datetime(2026, 7, 18, 12, 0, tzinfo=timezone(timedelta(hours=-4)))
        )
        self.assertEqual(bat["data_source"], "daily_log_approx")
        self.assertGreater(bat["asleep_hours"], 0)


if __name__ == "__main__":
    unittest.main()
