"""Unlogged sleep nights count as 0h on the civil calendar."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from rt_dashboard.models import SleepSample
from rt_dashboard.recovery import compute_recovery_status
from rt_dashboard.sleep_quest import score_sleep
from rt_dashboard.sleep_series import calendar_avg_sleep_hours, expand_sleep_calendar

AS_OF = "2026-09-22"
NOW = datetime(2026, 9, 22, 13, 0, tzinfo=timezone.utc)
DEBT = "no sleep log counted as 0h"


def _nights(*pairs: tuple[str, float]) -> list[SleepSample]:
    return [
        SleepSample(date=day, sleep_hours=hours, source="google_health")
        for day, hours in pairs
    ]


def _prior_eights() -> list[SleepSample]:
    """Six logged 8h nights, 2026-09-16 through 2026-09-21. Today is empty."""
    return _nights(
        *[
            (f"2026-09-{day:02d}", 8.0)
            for day in range(16, 22)
        ]
    )


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


class TestPendingOvernightDebt(unittest.TestCase):
    """#870: open GH-lag night is pending, not 0h. A closed miss still is."""

    def test_quest_pending_still_does_not_invent_zero(self):
        # #514 stays: missing overnight is pending, not a 0h fail.
        pending = score_sleep(last_sleep_hours=None, intervals=[], now=NOW)
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["last_night_hours"])
        short = score_sleep(
            last_sleep_hours=5.5,
            intervals=[
                {
                    "start": "2026-09-22T01:30:00+00:00",
                    "end": "2026-09-22T07:00:00+00:00",
                }
            ],
            now=NOW,
        )
        self.assertEqual(short["status"], "short")
        self.assertAlmostEqual(short["last_night_hours"], 5.5, places=2)

    def test_pending_omits_open_night_from_mean_and_note(self):
        status = compute_recovery_status(
            weight=[],
            sleep=_prior_eights(),
            sessions=[],
            as_of=AS_OF,
            pending_overnight=True,
        )
        self.assertEqual(status.inputs["avg_sleep_hours_7d"], 8.0)
        self.assertEqual(status.inputs["pending_overnight_date"], AS_OF)
        self.assertTrue(any("6 calendar days" in r for r in status.reasons))
        self.assertFalse(any(DEBT in r for r in status.reasons))

    def test_real_miss_still_counts_as_zero_debt(self):
        status = compute_recovery_status(
            weight=[],
            sleep=_prior_eights(),
            sessions=[],
            as_of=AS_OF,
            pending_overnight=False,
        )
        self.assertEqual(status.inputs["avg_sleep_hours_7d"], 6.86)
        self.assertIsNone(status.inputs["pending_overnight_date"])
        self.assertTrue(
            any("1 night(s) with no sleep log counted as 0h" in r for r in status.reasons)
        )

    def test_completed_short_night_uses_real_hours(self):
        sleep = _prior_eights() + _nights((AS_OF, 5.5))
        status = compute_recovery_status(
            weight=[],
            sleep=sleep,
            sessions=[],
            as_of=AS_OF,
            pending_overnight=False,
        )
        self.assertEqual(status.inputs["avg_sleep_hours_7d"], 7.64)
        self.assertIsNone(status.inputs["pending_overnight_date"])
        self.assertFalse(any(DEBT in r for r in status.reasons))

    def test_pending_flag_does_not_drop_a_logged_night(self):
        sleep = _prior_eights() + _nights((AS_OF, 5.5))
        status = compute_recovery_status(
            weight=[],
            sleep=sleep,
            sessions=[],
            as_of=AS_OF,
            pending_overnight=True,
        )
        self.assertEqual(status.inputs["avg_sleep_hours_7d"], 7.64)
        self.assertIsNone(status.inputs["pending_overnight_date"])

    def test_older_miss_counts_while_today_is_pending(self):
        # 16–20 logged, 21 missing, 22 open and pending → only 21 is debt.
        sleep = _nights(
            *[(f"2026-09-{day:02d}", 8.0) for day in range(16, 21)]
        )
        status = compute_recovery_status(
            weight=[],
            sleep=sleep,
            sessions=[],
            as_of=AS_OF,
            pending_overnight=True,
        )
        self.assertEqual(status.inputs["avg_sleep_hours_7d"], 6.67)
        self.assertEqual(status.inputs["pending_overnight_date"], AS_OF)
        self.assertTrue(
            any("1 night(s) with no sleep log counted as 0h" in r for r in status.reasons)
        )
        self.assertFalse(
            any(r.startswith("2 night") for r in status.reasons)
        )

    def test_derives_pending_from_sleep_quest(self):
        status = compute_recovery_status(
            weight=[],
            sleep=_prior_eights(),
            sessions=[],
            as_of=AS_OF,
            sleep_battery={"mode": "no_data"},
            sleep_intervals=[],
            now=NOW,
        )
        self.assertEqual(status.inputs["pending_overnight_date"], AS_OF)
        self.assertEqual(status.inputs["avg_sleep_hours_7d"], 8.0)
        self.assertFalse(any(DEBT in r for r in status.reasons))

    def test_scored_overnight_without_sample_is_real_miss(self):
        status = compute_recovery_status(
            weight=[],
            sleep=_prior_eights(),
            sessions=[],
            as_of=AS_OF,
            sleep_battery={
                "mode": "awake",
                "last_sleep_hours": 5.5,
                "last_night_hours": 5.5,
                "last_wake_at": "2026-09-22T07:00:00+00:00",
            },
            sleep_intervals=[
                {
                    "start": "2026-09-22T01:30:00+00:00",
                    "end": "2026-09-22T07:00:00+00:00",
                }
            ],
            now=NOW,
        )
        self.assertIsNone(status.inputs["pending_overnight_date"])
        self.assertEqual(status.inputs["avg_sleep_hours_7d"], 6.86)
        self.assertTrue(
            any("1 night(s) with no sleep log counted as 0h" in r for r in status.reasons)
        )


if __name__ == "__main__":
    unittest.main()
