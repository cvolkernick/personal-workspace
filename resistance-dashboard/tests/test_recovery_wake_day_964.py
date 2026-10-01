"""Recovery stays on the wake day after midnight (#964).

A new civil day is not a 0h night. The 7-day window ends on the last
overnight wake until 24h plus the sync lag. A nap does not replace that
night. Google Health and Google Fit share the viewer-local wake date.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from rt_dashboard.google_health import (
    parse_health_api_sleep,
    parse_sleep_from_activity_buckets,
    parse_sleep_sessions,
    sleep_samples_from_intervals,
)
from rt_dashboard.models import SleepSample
from rt_dashboard.recovery import (
    RECOVERY_NIGHT_DUE_HOURS,
    compute_recovery_status,
)
from rt_dashboard.sleep_quest import _completed_rows

NY = ZoneInfo("America/New_York")
DEBT = "no sleep log counted as 0h"
ROOT = Path(__file__).resolve().parents[1]


def _et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY)


def _week() -> tuple[list[SleepSample], list[dict], str]:
    """Seven 8h nights, 3:00–11:00 ET, wake dates 2026-09-25 through 10-01."""
    first = date(2026, 9, 25)
    days = [first + timedelta(days=i) for i in range(7)]
    sleep: list[SleepSample] = []
    intervals: list[dict] = []
    for day in days:
        sleep.append(
            SleepSample(
                date=day.isoformat(),
                sleep_hours=8.0,
                source="google_health",
            )
        )
        intervals.append(
            {
                "start": _et(day, 3).isoformat(),
                "end": _et(day, 11).isoformat(),
                "source": "google_health",
            }
        )
    return sleep, intervals, _et(days[-1], 11).isoformat()


def _score(
    now: datetime,
    *,
    sleep: list[SleepSample],
    intervals: list[dict],
    last_wake: str,
):
    civil = now.astimezone(NY).strftime("%Y-%m-%d")
    return compute_recovery_status(
        weight=[],
        sleep=sleep,
        sessions=[],
        as_of=civil,
        now=now,
        last_wake_at=last_wake,
        tz_name="America/New_York",
        sleep_intervals=intervals,
        sleep_battery={
            "mode": "awake",
            "last_wake_at": last_wake,
            "last_night_hours": 8.0,
            "last_sleep_hours": 8.0,
        },
    )


class TestRecoveryWakeDay(unittest.TestCase):
    def setUp(self):
        self.sleep, self.intervals, self.wake = _week()
        self.evening = _et(date(2026, 10, 1), 23, 30)
        self.evening_score = _score(
            self.evening,
            sleep=self.sleep,
            intervals=self.intervals,
            last_wake=self.wake,
        )

    def test_midnight_matches_the_evening_score(self):
        # AC1. Civil as_of is D+1. The window stays on the 11:00 wake day.
        status = _score(
            _et(date(2026, 10, 2), 0, 30),
            sleep=self.sleep,
            intervals=self.intervals,
            last_wake=self.wake,
        )
        self.assertEqual(status.score, self.evening_score.score)
        self.assertEqual(status.score, 85.0)
        self.assertEqual(status.label, "Ready")
        self.assertEqual(status.inputs["as_of"], "2026-10-01")
        self.assertEqual(status.inputs["avg_sleep_hours_7d"], 8.0)
        self.assertFalse(any(DEBT in r for r in status.reasons))

    def test_late_morning_before_sync_stays_unpenalized(self):
        # AC2. 24.5h after the 11:00 wake, new night not synced.
        status = _score(
            _et(date(2026, 10, 2), 11, 30),
            sleep=self.sleep,
            intervals=self.intervals,
            last_wake=self.wake,
        )
        self.assertEqual(status.score, self.evening_score.score)
        self.assertEqual(status.inputs["as_of"], "2026-10-01")
        self.assertEqual(status.inputs["avg_sleep_hours_7d"], 8.0)
        self.assertFalse(any(DEBT in r for r in status.reasons))

    def test_missing_night_counts_after_the_lag(self):
        # AC3. More than 24h + lag with no new sleep record → 0h and -5.
        wake = _et(date(2026, 10, 1), 11)
        still_open = wake + timedelta(hours=RECOVERY_NIGHT_DUE_HOURS)
        open_status = _score(
            still_open,
            sleep=self.sleep,
            intervals=self.intervals,
            last_wake=self.wake,
        )
        self.assertEqual(open_status.score, 85.0)
        self.assertEqual(open_status.inputs["as_of"], "2026-10-01")

        due = still_open + timedelta(minutes=1)
        status = _score(
            due,
            sleep=self.sleep,
            intervals=self.intervals,
            last_wake=self.wake,
        )
        self.assertEqual(status.inputs["as_of"], "2026-10-02")
        self.assertEqual(status.inputs["avg_sleep_hours_7d"], 6.86)
        self.assertEqual(status.score, 55.0)
        self.assertTrue(
            any("1 night(s) with no sleep log counted as 0h" in r for r in status.reasons)
        )

    def test_daytime_nap_is_not_the_night(self):
        # AC4. Battery last_wake follows the nap. Recovery does not.
        nap_end = _et(date(2026, 10, 2), 15, 30)
        intervals = list(self.intervals) + [
            {
                "start": _et(date(2026, 10, 2), 14).isoformat(),
                "end": nap_end.isoformat(),
                "source": "google_health",
            }
        ]
        sleep = list(self.sleep) + [
            SleepSample(date="2026-10-02", sleep_hours=1.5, source="google_health")
        ]
        status = _score(
            _et(date(2026, 10, 2), 16),
            sleep=sleep,
            intervals=intervals,
            last_wake=nap_end.isoformat(),
        )
        self.assertEqual(status.score, 85.0)
        self.assertEqual(status.inputs["as_of"], "2026-10-01")
        self.assertEqual(status.inputs["avg_sleep_hours_7d"], 8.0)
        self.assertFalse(any(DEBT in r for r in status.reasons))

    def test_in_progress_interval_stays_out(self):
        now = _et(date(2026, 10, 2), 4)
        intervals = list(self.intervals) + [
            {
                "start": _et(date(2026, 10, 2), 3).isoformat(),
                "end": _et(date(2026, 10, 2), 11).isoformat(),
                "source": "google_health",
            }
        ]
        done = _completed_rows(intervals, now)
        self.assertTrue(all(row["end"] <= now for row in done))
        status = _score(
            now,
            sleep=self.sleep,
            intervals=intervals,
            last_wake=self.wake,
        )
        self.assertEqual(status.score, 85.0)
        self.assertEqual(status.inputs["as_of"], "2026-10-01")


class TestSleepWakeDateKey(unittest.TestCase):
    """AC5. Late-evening wake: GH and Fit share the viewer-local end date."""

    def _interval(self):
        # 19:30–23:30 ET on Oct 1 == 23:30Z Oct 1 through 03:30Z Oct 2.
        return {
            "start": "2026-10-01T23:30:00+00:00",
            "end": "2026-10-02T03:30:00+00:00",
        }

    def _fit_payload(self):
        start = datetime(2026, 10, 1, 23, 30, tzinfo=timezone.utc)
        end = datetime(2026, 10, 2, 3, 30, tzinfo=timezone.utc)
        return {
            "session": [
                {
                    "activityType": 72,
                    "startTimeMillis": int(start.timestamp() * 1000),
                    "endTimeMillis": int(end.timestamp() * 1000),
                }
            ]
        }

    def test_health_and_fit_share_the_local_wake_date(self):
        gh = sleep_samples_from_intervals(
            [self._interval()], tz_name="America/New_York"
        )
        fit = parse_sleep_sessions(
            self._fit_payload(), tz_name="America/New_York"
        )
        api = parse_health_api_sleep(
            {
                "dataPoints": [
                    {
                        "interval": {
                            "startTime": "2026-10-01T23:30:00Z",
                            "endTime": "2026-10-02T03:30:00Z",
                        }
                    }
                ]
            },
            tz_name="America/New_York",
        )
        self.assertEqual(gh[0].date, "2026-10-01")
        self.assertEqual(fit[0].date, "2026-10-01")
        self.assertEqual(api[0].date, "2026-10-01")
        self.assertEqual(gh[0].date, fit[0].date)

        # The parameter is the zone. UTC is the next day; New York is not.
        # A process-local astimezone() cannot satisfy both.
        gh_utc = sleep_samples_from_intervals([self._interval()], tz_name="UTC")
        fit_utc = parse_sleep_sessions(self._fit_payload(), tz_name="UTC")
        self.assertEqual(gh_utc[0].date, "2026-10-02")
        self.assertEqual(fit_utc[0].date, "2026-10-02")

        # Default resolver is America/New_York, including when TZ=UTC.
        bare = sleep_samples_from_intervals([self._interval()])
        self.assertEqual(bare[0].date, "2026-10-01")

    def test_fit_bucket_uses_point_end_not_utc_bucket_start(self):
        start = datetime(2026, 10, 1, 23, 30, tzinfo=timezone.utc)
        end = datetime(2026, 10, 2, 3, 30, tzinfo=timezone.utc)
        bucket_start = datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc)
        payload = {
            "bucket": [
                {
                    "startTimeMillis": int(bucket_start.timestamp() * 1000),
                    "dataset": [
                        {
                            "point": [
                                {
                                    "startTimeNanos": int(start.timestamp() * 1_000_000_000),
                                    "endTimeNanos": int(end.timestamp() * 1_000_000_000),
                                    "value": [{"intVal": 72}],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
        samples = parse_sleep_from_activity_buckets(
            payload, tz_name="America/New_York"
        )
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].date, "2026-10-01")
        self.assertAlmostEqual(samples[0].sleep_hours, 4.0, places=2)


class TestRecoveryCallSites(unittest.TestCase):
    def test_training_day_is_passed_before_recovery(self):
        for rel in (
            "server.py",
            "api/dashboard.py",
            "api/workout/_util.py",
            "rt_dashboard/agent_plan.py",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            rec = text.find("compute_recovery_status(")
            train = text.rfind("training_day_iso(", 0, rec)
            self.assertGreater(train, -1, rel)
            call = text[rec : rec + 700]
            self.assertIn("as_of=train_day", call, rel)
            self.assertIn("last_wake_at=last_wake", call, rel)
            self.assertIn("tz_name=tz_name", call, rel)


if __name__ == "__main__":
    unittest.main()
