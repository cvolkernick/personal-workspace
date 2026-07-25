"""Wake-full / 16h-drain sleep battery tests."""

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
    def test_full_at_wake(self) -> None:
        tz = timezone(timedelta(hours=-4))
        wake = datetime(2026, 7, 18, 6, 0, tzinfo=tz)
        intervals = [
            {
                "start": datetime(2026, 7, 17, 22, 0, tzinfo=tz).isoformat(),
                "end": wake.isoformat(),
                "source": "test",
            }
        ]
        # Just after wake
        bat = compute_sleep_battery(
            intervals, now=wake + timedelta(minutes=5), sleep_target_hours=8.0
        )
        self.assertEqual(bat["model"], "wake_full_drain_awake")
        self.assertEqual(bat["mode"], "awake")
        self.assertGreaterEqual(bat["pct_charged"], 99.0)
        self.assertEqual(bat["level"], "full")
        self.assertAlmostEqual(bat["awake_budget_hours"], 16.0)

    def test_half_after_eight_hours_awake(self) -> None:
        tz = timezone(timedelta(hours=-4))
        wake = datetime(2026, 7, 18, 6, 0, tzinfo=tz)
        intervals = [
            {
                "start": (wake - timedelta(hours=8)).isoformat(),
                "end": wake.isoformat(),
                "source": "test",
            }
        ]
        bat = compute_sleep_battery(
            intervals, now=wake + timedelta(hours=8), sleep_target_hours=8.0
        )
        self.assertAlmostEqual(bat["hours_awake"], 8.0, places=2)
        self.assertAlmostEqual(bat["pct_charged"], 50.0, places=0)
        self.assertAlmostEqual(bat["hours_until_empty"], 8.0, places=1)

    def test_empty_after_sixteen_hours_awake(self) -> None:
        tz = timezone(timedelta(hours=-4))
        wake = datetime(2026, 7, 18, 6, 0, tzinfo=tz)
        intervals = [
            {
                "start": (wake - timedelta(hours=8)).isoformat(),
                "end": wake.isoformat(),
                "source": "test",
            }
        ]
        bat = compute_sleep_battery(
            intervals, now=wake + timedelta(hours=16), sleep_target_hours=8.0
        )
        self.assertAlmostEqual(bat["pct_charged"], 0.0, places=1)
        self.assertEqual(bat["level"], "critical")
        self.assertAlmostEqual(bat["hours_until_empty"], 0.0, places=1)

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
        self.assertEqual(bat["mode"], "awake")
        self.assertGreater(bat["pct_charged"], 0)


if __name__ == "__main__":
    unittest.main()
