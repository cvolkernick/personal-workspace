"""Training day SoT: wake window / session-close, not civil midnight (#542)."""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.training_day import (
    ppl_logged_for_planning,
    ppl_logged_in_wake,
    resolve_log_date,
    session_in_wake,
    training_day_iso,
)
from rt_dashboard.workout_log import parse_log_body
from rt_dashboard.workout_store import stamp_today_session


ET = ZoneInfo("America/New_York")


def _session(date, st="legs", closed_at=None):
    s = Session(
        date=date,
        session_type=st,
        exercises=[
            ExerciseEntry(
                name="RDL",
                sets=[SetEntry(weight_lbs=40, sets=2, reps=7)],
            )
        ],
        closed_at=closed_at,
    )
    return s


class TrainingDayIso(unittest.TestCase):
    def test_after_midnight_before_sleep_is_prior_wake_date(self):
        day = training_day_iso(
            now=datetime(2026, 9, 9, 0, 30, tzinfo=ET),
            last_wake_at="2026-09-08T07:00:00-04:00",
            tz_name="America/New_York",
        )
        self.assertEqual(day, "2026-09-08")

    def test_after_real_sleep_is_new_wake_date(self):
        day = training_day_iso(
            now=datetime(2026, 9, 9, 9, 0, tzinfo=ET),
            last_wake_at="2026-09-09T08:00:00-04:00",
            tz_name="America/New_York",
        )
        self.assertEqual(day, "2026-09-09")

    def test_missing_wake_falls_back_to_civil(self):
        day = training_day_iso(
            now=datetime(2026, 9, 9, 0, 30, tzinfo=ET),
            last_wake_at=None,
            tz_name="America/New_York",
        )
        self.assertEqual(day, "2026-09-09")


class ResolveLogDate(unittest.TestCase):
    def test_empty_date_uses_training_day(self):
        day = resolve_log_date(
            "",
            now=datetime(2026, 9, 9, 0, 30, tzinfo=ET),
            last_wake_at="2026-09-08T07:00:00-04:00",
            tz_name="America/New_York",
        )
        self.assertEqual(day, "2026-09-08")

    def test_civil_today_after_midnight_remaps(self):
        day = resolve_log_date(
            "2026-09-09",
            now=datetime(2026, 9, 9, 0, 30, tzinfo=ET),
            last_wake_at="2026-09-08T07:00:00-04:00",
            tz_name="America/New_York",
        )
        self.assertEqual(day, "2026-09-08")

    def test_explicit_backdate_kept(self):
        day = resolve_log_date(
            "2026-09-07",
            now=datetime(2026, 9, 9, 9, 0, tzinfo=ET),
            last_wake_at="2026-09-09T08:00:00-04:00",
            tz_name="America/New_York",
        )
        self.assertEqual(day, "2026-09-07")


class WakeMembership(unittest.TestCase):
    def test_after_midnight_close_is_prior_wake(self):
        s = _session("2026-09-09", closed_at="2026-09-09T00:30:00-04:00")
        self.assertTrue(
            session_in_wake(
                s,
                last_wake_at="2026-09-08T07:00:00-04:00",
                now=datetime(2026, 9, 9, 0, 45, tzinfo=ET),
                tz_name="America/New_York",
            )
        )
        self.assertFalse(
            session_in_wake(
                s,
                last_wake_at="2026-09-09T08:00:00-04:00",
                now=datetime(2026, 9, 9, 9, 0, tzinfo=ET),
                tz_name="America/New_York",
            )
        )
        self.assertEqual(
            ppl_logged_in_wake(
                [s],
                last_wake_at="2026-09-08T07:00:00-04:00",
                now=datetime(2026, 9, 9, 0, 45, tzinfo=ET),
                tz_name="America/New_York",
            ),
            "legs",
        )
        self.assertIsNone(
            ppl_logged_in_wake(
                [s],
                last_wake_at="2026-09-09T08:00:00-04:00",
                now=datetime(2026, 9, 9, 9, 0, tzinfo=ET),
                tz_name="America/New_York",
            )
        )

    def test_planning_falls_back_to_civil_without_wake(self):
        s = _session("2026-09-09")
        self.assertEqual(
            ppl_logged_for_planning([s], as_of="2026-09-09", last_wake_at=None),
            "legs",
        )
        self.assertIsNone(
            ppl_logged_for_planning([s], as_of="2026-09-08", last_wake_at=None)
        )

    def test_stale_last_wake_does_not_pin_old_session(self):
        s = _session("2026-08-17", st="push", closed_at="2026-08-17T12:00:00-04:00")
        self.assertIsNone(
            ppl_logged_for_planning(
                [s],
                as_of="2026-09-09",
                last_wake_at="2026-08-17T07:00:00-04:00",
                now=datetime(2026, 9, 9, 9, 0, tzinfo=ET),
                tz_name="America/New_York",
            )
        )


class StampUsesWake(unittest.TestCase):
    def test_after_wake_does_not_pin_prior_close(self):
        from rt_dashboard.workout_store import load_workspace_goals

        goals, _ = load_workspace_goals()
        s = _session("2026-09-09", closed_at="2026-09-09T00:30:00-04:00")
        slot = stamp_today_session(
            {"exercises": [], "empty": True},
            [s],
            goals,
            {
                "score": 80,
                "sparse": False,
                "sleep_battery": {"last_wake_at": "2026-09-09T08:00:00-04:00"},
            },
            as_of="2026-09-09",
            now=datetime(2026, 9, 9, 9, 0, tzinfo=ET),
        )
        self.assertFalse(slot.get("ppl_logged_today"))
        self.assertEqual(slot["session_type"], "push")
        self.assertEqual(slot["next_session_type"], "push")


class ParseLogRemap(unittest.TestCase):
    def test_after_midnight_body_lands_on_training_day(self):
        session = parse_log_body(
            {
                "session_type": "legs",
                "date": "2026-09-09",
                "last_wake_at": "2026-09-08T07:00:00-04:00",
                "tz": "America/New_York",
                "exercises": [
                    {"name": "RDL", "weight_lbs": 40, "sets": 2, "reps": 7}
                ],
            },
            now=datetime(2026, 9, 9, 0, 30, tzinfo=ET),
        )
        self.assertEqual(session.date, "2026-09-08")
        self.assertEqual(session.session_type, "legs")
        self.assertTrue(session.closed_at)
        self.assertEqual(session.exercises[0].name, "RDL")


if __name__ == "__main__":
    unittest.main()
