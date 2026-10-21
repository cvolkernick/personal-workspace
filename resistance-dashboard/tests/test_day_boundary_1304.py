"""#1304 day boundary + session header + token-path saved plan.

10/21: two pull lifts showed under a 'PUSH session (5 lifts)' header because
the push lifts were logged after midnight, and the token path returned
plan_exercises [] for a valid saved plan.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, time
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rt_dashboard import training_day as td  # noqa: E402
from rt_dashboard.agent_plan import fill_stamped_workout, overlay_workout_on_payload  # noqa: E402
from rt_dashboard.daily_plan_tasks import (  # noqa: E402
    TRAIN_SESSION_SLUG,
    plan_from_today_board,
    session_header_from_lifts,
)
from rt_dashboard.workout_planner import last_session_type, ppl_logged_on_day  # noqa: E402

ET = ZoneInfo("America/New_York")
NY = "America/New_York"


def _iso(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET).isoformat()


def _session(letter, date, closed, names=("Lift",)):
    return {
        "session_type": letter,
        "date": date,
        "closed_at": closed,
        "exercises": [
            {"name": n, "sets": [{"weight_lbs": 50, "reps": 8}] * 3} for n in names
        ],
    }


class CutoffConstantTests(unittest.TestCase):
    def test_single_named_cutoff_defaults_to_4am(self) -> None:
        self.assertEqual(td.TRAINING_DAY_CUTOFF, time(4, 0))
        self.assertEqual(td._AFTER_MIDNIGHT_END_HOUR, td.TRAINING_DAY_CUTOFF.hour)

    def test_cutoff_is_easy_to_change(self) -> None:
        with mock.patch.object(td, "TRAINING_DAY_CUTOFF", time(5, 0)):
            self.assertEqual(td.training_day_for(_iso(2026, 10, 21, 4, 30), tz_name=NY), "2026-10-20")


class BoundaryTests(unittest.TestCase):
    def test_0030_log_is_previous_day(self) -> None:
        self.assertEqual(td.training_day_for(_iso(2026, 10, 21, 0, 30), tz_name=NY), "2026-10-20")

    def test_0359_is_previous_day(self) -> None:
        self.assertEqual(td.training_day_for(_iso(2026, 10, 21, 3, 59), tz_name=NY), "2026-10-20")

    def test_0400_is_new_day(self) -> None:
        self.assertEqual(td.training_day_for(_iso(2026, 10, 21, 4, 0), tz_name=NY), "2026-10-21")

    def test_0430_log_is_new_day(self) -> None:
        self.assertEqual(td.training_day_for(_iso(2026, 10, 21, 4, 30), tz_name=NY), "2026-10-21")

    def test_utc_stamp_uses_new_york_clock(self) -> None:
        # 07:59Z = 03:59 EDT; 08:00Z = 04:00 EDT.
        self.assertEqual(td.training_day_for("2026-10-21T07:59:00Z", tz_name=NY), "2026-10-20")
        self.assertEqual(td.training_day_for("2026-10-21T08:00:00Z", tz_name=NY), "2026-10-21")

    def test_session_close_boundaries(self) -> None:
        for clock, want in (
            ((0, 30), "2026-10-20"),
            ((3, 59), "2026-10-20"),
            ((4, 0), "2026-10-21"),
            ((4, 30), "2026-10-21"),
        ):
            row = _session("push", "2026-10-21", _iso(2026, 10, 21, *clock))
            self.assertEqual(td.session_training_day(row, tz_name=NY), want, clock)

    def test_date_only_row_keeps_its_date(self) -> None:
        self.assertEqual(
            td.session_training_day({"session_type": "push", "date": "2026-10-21"}),
            "2026-10-21",
        )

    def test_training_day_iso_without_wake(self) -> None:
        self.assertEqual(
            td.training_day_iso(now=datetime(2026, 10, 21, 3, 59, tzinfo=ET), tz_name=NY),
            "2026-10-20",
        )
        self.assertEqual(
            td.training_day_iso(now=datetime(2026, 10, 21, 4, 0, tzinfo=ET), tz_name=NY),
            "2026-10-21",
        )

    def test_log_with_no_date_at_0030_lands_on_previous_day(self) -> None:
        self.assertEqual(
            td.resolve_log_date("", now=datetime(2026, 10, 21, 0, 30, tzinfo=ET), tz_name=NY),
            "2026-10-20",
        )
        self.assertEqual(
            td.resolve_log_date("", now=datetime(2026, 10, 21, 4, 30, tzinfo=ET), tz_name=NY),
            "2026-10-21",
        )


class RotationAndQuestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pull = _session("pull", "2026-10-20", _iso(2026, 10, 20, 21, 0))

    def test_push_at_0030_counts_toward_previous_day(self) -> None:
        push = _session("push", "2026-10-21", _iso(2026, 10, 21, 0, 30))
        sessions = [push, self.pull]
        self.assertIsNone(ppl_logged_on_day(sessions, "2026-10-21"))
        self.assertIn(ppl_logged_on_day(sessions, "2026-10-20"), ("pull", "push"))
        # Rotation: the later close on the same training day is the last letter.
        self.assertEqual(last_session_type(sessions), "push")

    def test_push_at_0430_is_the_new_day(self) -> None:
        push = _session("push", "2026-10-21", _iso(2026, 10, 21, 4, 30))
        self.assertEqual(ppl_logged_on_day([push, self.pull], "2026-10-21"), "push")
        self.assertEqual(last_session_type([push, self.pull]), "push")

    def test_rotation_does_not_reorder_by_civil_date_alone(self) -> None:
        # A legs close at 02:40 on 10/21 and a push logged on the evening of
        # 10/20: legs is still the later close on training day 10/20.
        legs = _session("legs", "2026-10-21", _iso(2026, 10, 21, 2, 40))
        push = _session("push", "2026-10-20", _iso(2026, 10, 20, 19, 0))
        self.assertEqual(last_session_type([push, legs]), "legs")

    def test_legs_closed_at_0240_belongs_to_previous_day(self) -> None:
        legs = _session(
            "legs", "2026-10-21", _iso(2026, 10, 21, 2, 40), names=("Leg Press", "Leg Curl")
        )
        self.assertEqual(td.session_training_day(legs, tz_name=NY), "2026-10-20")
        self.assertEqual(
            td.closed_ppl_for_planning(
                [legs], as_of="2026-10-20", now=datetime(2026, 10, 21, 3, 0, tzinfo=ET), tz_name=NY
            ),
            "legs",
        )
        # The new training day (after 04:00) is not already closed by it.
        self.assertIsNone(
            td.closed_ppl_for_planning(
                [legs], as_of="2026-10-21", now=datetime(2026, 10, 21, 9, 0, tzinfo=ET), tz_name=NY
            )
        )
        self.assertIsNone(ppl_logged_on_day([legs], "2026-10-21"))
        self.assertEqual(ppl_logged_on_day([legs], "2026-10-20"), "legs")
        self.assertEqual(
            td.planning_window_sessions(
                [legs], as_of="2026-10-21", now=datetime(2026, 10, 21, 9, 0, tzinfo=ET), tz_name=NY
            ),
            [],
        )


class SessionHeaderTests(unittest.TestCase):
    def _board(self, names, letter="push", stale_n=5):
        return {
            "date": "2026-10-21",
            "actions": [
                {
                    "id": "train-session",
                    "kind": "training",
                    "priority": 1,
                    "text": f"Complete today's {letter.upper()} session ({stale_n} lifts as prescribed).",
                }
            ],
            "workout": {
                "session_type": letter,
                "is_rest_day": False,
                "exercises": [{"name": n, "sets": 3, "reps": 8} for n in names],
            },
        }

    def _header(self, board):
        groups = plan_from_today_board(board, day="2026-10-21")
        training = [g for g in groups if g.group == "training"][0]
        heads = [it for it in training.items if it.slug == TRAIN_SESSION_SLUG]
        self.assertEqual(len(heads), 1)
        return heads[0].title

    def test_header_matches_the_lifts_in_the_plan(self) -> None:
        title = self._header(self._board(["Assisted Pullups", "Machine Row"]))
        self.assertEqual(title, "Complete today's PULL session (2 lifts as prescribed).")

    def test_header_count_follows_plan(self) -> None:
        title = self._header(
            self._board(["Smith Flat Bench", "Smith Incline Bench", "DB Shoulder Press"])
        )
        self.assertEqual(title, "Complete today's PUSH session (3 lifts as prescribed).")

    def test_unknown_lifts_fall_back_to_plan_letter(self) -> None:
        self.assertEqual(session_header_from_lifts(["Mystery Lift"], "legs"), ("LEGS", 1))


class TokenPathSavedPlanTests(unittest.TestCase):
    SAVED = {
        "session_type": "push",
        "is_rest_day": False,
        "exercises": [
            {"name": "Smith Flat Bench", "sets": 3, "reps": 8},
            {"name": "DB Shoulder Press", "sets": 3, "reps": 10},
            {"name": "Tricep Pushdowns (Rope)", "sets": 2, "reps": 12},
        ],
    }

    def test_valid_saved_plan_returns_exercises_on_token_path(self) -> None:
        slot = {
            "session_type": "push",
            "is_rest_day": False,
            "exercises": [],
            "session_closed_today": True,
        }
        with mock.patch(
            "rt_dashboard.agent_plan.load_last_good_workout_plan", return_value=self.SAVED
        ), mock.patch("rt_dashboard.agent_plan.ensure_today_grok_plan") as generate, mock.patch(
            "rt_dashboard.agent_plan.save_last_good_workout_plan"
        ) as persist:
            out = fill_stamped_workout("u", day="2026-10-21", workout=slot, read_only=True)
        generate.assert_not_called()
        persist.assert_not_called()
        self.assertEqual(out["exercises"][0]["name"], "Smith Flat Bench")
        payload = overlay_workout_on_payload({"coach": {"today": {}}}, out)
        self.assertEqual(payload["workout"]["exercises"][0]["name"], "Smith Flat Bench")
        self.assertFalse(payload["workout"]["empty"])

    def test_export_carries_plan_exercises(self) -> None:
        from rt_dashboard.agent_today import export_agent_today

        payload = overlay_workout_on_payload(
            {
                "meta": {"local_today": "2026-10-21"},
                "coach": {"today": {"date": "2026-10-21"}},
                "sessions": [],
                "workout_store": {"goals": {}, "plan": {}},
                "recovery": {},
            },
            dict(self.SAVED),
        )
        body = export_agent_today(payload)
        names = [r.get("name") for r in body["today"]["workout"]["plan_exercises"]]
        self.assertEqual(names[0], "Smith Flat Bench")


if __name__ == "__main__":
    unittest.main()
