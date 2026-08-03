"""Unlogged sleep nights count as 0h on the civil calendar."""

from __future__ import annotations

import unittest

from rt_dashboard.models import SleepSample
from rt_dashboard.recovery import compute_recovery_status
from rt_dashboard.sleep_series import calendar_avg_sleep_hours, expand_sleep_calendar


class TestSleepSeries(unittest.TestCase):
    def test_expand_fills_gaps_with_zero(self):
        sleep = [
            SleepSample(date="2026-07-20", sleep_hours=8.0, source="google_health"),
            SleepSample(date="2026-07-24", sleep_hours=7.0, source="google_health"),
        ]
        filled = expand_sleep_calendar(sleep, as_of="2026-07-24", window_days=5)
        self.assertEqual(len(filled), 5)
        by = {s.date: s.sleep_hours for s in filled}
        self.assertEqual(by["2026-07-20"], 8.0)
        self.assertEqual(by["2026-07-21"], 0.0)
        self.assertEqual(by["2026-07-22"], 0.0)
        self.assertEqual(by["2026-07-23"], 0.0)
        self.assertEqual(by["2026-07-24"], 7.0)
        self.assertEqual(
            [s.source for s in filled if s.date == "2026-07-21"][0],
            "implied_zero",
        )

    def test_calendar_avg_includes_zero_nights(self):
        sleep = [
            SleepSample(date="2026-07-24", sleep_hours=7.0),
        ]
        # 6 zeros + 7h → mean 1.0 over 7 days
        avg = calendar_avg_sleep_hours(sleep, as_of="2026-07-24", days=7)
        self.assertEqual(avg, 1.0)

    def test_recovery_penalizes_missing_nights(self):
        # One good night only in last week → low calendar avg
        sleep = [SleepSample(date="2026-07-24", sleep_hours=8.0)]
        status = compute_recovery_status(
            weight=[],
            sleep=sleep,
            sessions=[],
            as_of="2026-07-24",
        )
        self.assertIsNotNone(status.inputs.get("avg_sleep_hours_7d"))
        self.assertLess(status.inputs["avg_sleep_hours_7d"], 2.0)
        self.assertTrue(
            any("0h" in r or "unlogged" in r.lower() or "no sleep log" in r.lower() for r in status.reasons)
        )


if __name__ == "__main__":
    unittest.main()
