"""Today must follow the America/New_York calendar plan.

Monday 2026-11-16: the last log is a pull closed on 2026-11-15, nothing is
logged on the 16th, and the dashboard plan is legs. Agent today used to
keep yesterday's closed pull (empty exercises, session_closed_today true).
Plan and training already served the legs list.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rt_dashboard.agent_today import export_agent_today  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY = "2026-11-16"
NOW = datetime(2026, 11, 16, 9, 50, tzinfo=ET)
WAKE = "2026-11-15T07:10:00-05:00"
LEGS = [
    {"name": "Hack Squat", "sets": 2, "reps": "8-10", "weight_lbs": 180},
    {"name": "Calf Raises", "sets": 2, "reps": "10-12", "weight_lbs": 90},
    {"name": "Barbell Deadlift", "sets": 2, "reps": "5-8", "weight_lbs": 225},
    {"name": "Seated Leg Curls", "sets": 2, "reps": "10-12", "weight_lbs": 70},
    {"name": "Back Extension Machine", "sets": 1, "reps": "12-15", "weight_lbs": 50},
    {"name": "RDL", "sets": 1, "reps": "8-10", "weight_lbs": 135},
]
GOALS = {"rotation": ["push", "pull", "legs"]}


def _pull_yesterday() -> dict:
    return {
        "session_type": "pull",
        "date": "2026-11-15",
        "closed_at": "2026-11-15T18:40:00-05:00",
        "status": "complete",
        "exercises": [
            {
                "name": "Lat Pulldown",
                "sets": [{"weight_lbs": 120, "sets": 3, "reps": 8}],
            }
        ],
    }


def _legs_plan() -> dict:
    return {
        "date": DAY,
        "session_type": "legs",
        "is_rest_day": False,
        "exercises": [dict(ex) for ex in LEGS],
        "message": "Suggested LEGS session (6 exercises, 10 hard sets).",
        "session_closed_today": False,
        "already_trained_today": False,
        "next_session_type": "push",
    }


def _assert_open_legs(test: unittest.TestCase, workout: dict) -> None:
    test.assertEqual(workout["session_type"], "legs")
    test.assertFalse(workout["session_closed_today"])
    test.assertEqual(
        [ex["name"] for ex in workout["plan_exercises"]],
        [ex["name"] for ex in LEGS],
    )


class CalendarDayPlanTests(unittest.TestCase):
    def test_overlay_keeps_todays_legs_plan(self) -> None:
        from server import _overlay_agent_workout

        plan = _legs_plan()
        payload = {
            "meta": {"local_today": DAY},
            "coach": {"today": {"date": DAY, "workout": dict(plan)}},
            "workout_store": {"plan": plan, "goals": GOALS},
            "sessions": [_pull_yesterday()],
            "recovery": {
                "sparse": True,
                "score": 80,
                "sleep_battery": {"last_wake_at": WAKE},
            },
            "sleep_battery": {"last_wake_at": WAKE},
        }
        with mock.patch(
            "rt_dashboard.timeutil.local_now", return_value=NOW
        ), mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={
                "token": "t",
                "expired": False,
                "source": "supergrok_session",
            },
        ), mock.patch(
            "rt_dashboard.agent_plan.load_last_good_workout_plan",
            return_value=None,
        ):
            served = _overlay_agent_workout(payload, "user-1", read_only=True)
        workout = export_agent_today(served)["today"]["workout"]
        _assert_open_legs(self, workout)

    def test_stale_closed_pull_stamp_does_not_close_today(self) -> None:
        """The observed today slot: pull, empty, closed, supergrok source.

        The calendar day's legs plan is still on the store. Nothing was
        logged on 2026-11-16.
        """
        stamp = {
            "session_type": "pull",
            "is_rest_day": False,
            "exercises": [],
            "session_closed_today": True,
            "already_trained_today": True,
            "ppl_logged_today": "pull",
            "source": "supergrok_session",
            "message": "SuperGrok connected. Generate today's meal/workout plan.",
            "next_session_type": "legs",
            "context": {"session_closed_today": True, "already_trained_today": True},
        }
        payload = {
            "meta": {"local_today": DAY},
            "coach": {
                "today": {
                    "date": DAY,
                    "workout": {
                        **stamp,
                        "session_closed_today": True,
                    },
                }
            },
            "workout": stamp,
            "workout_store": {"plan": _legs_plan(), "goals": GOALS},
            "sessions": [_pull_yesterday()],
            "recovery": {
                "sparse": True,
                "sleep_battery": {"last_wake_at": WAKE},
            },
        }
        workout = export_agent_today(payload)["today"]["workout"]
        _assert_open_legs(self, workout)

    def test_close_on_the_calendar_day_stays_closed(self) -> None:
        logged = _pull_yesterday()
        logged["date"] = DAY
        logged["closed_at"] = "2026-11-16T08:10:00-05:00"
        payload = {
            "meta": {"local_today": DAY},
            "coach": {"today": {"date": DAY}},
            "workout": {
                "session_type": "pull",
                "is_rest_day": False,
                "exercises": [],
                "session_closed_today": True,
            },
            "workout_store": {
                "plan": {
                    "session_type": "pull",
                    "is_rest_day": False,
                    "exercises": [],
                    "session_closed_today": True,
                },
                "goals": GOALS,
            },
            "sessions": [logged],
            "recovery": {
                "sleep_battery": {"last_wake_at": "2026-11-16T06:40:00-05:00"}
            },
        }
        workout = export_agent_today(payload)["today"]["workout"]
        self.assertTrue(workout["session_closed_today"])
        self.assertEqual(workout["session_type"], "pull")


if __name__ == "__main__":
    unittest.main()
