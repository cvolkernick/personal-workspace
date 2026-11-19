"""Quest copy uses prescribed_match_count. Lateral raises credit side delts.

On 11/18 the quest read "(1 lifts as prescribed)" while prescribed_match_count
was 4: the header counted the one lift still on the plan. A lateral raise
tagged only "shoulders" credited side delts on the tile and left the body
map grey, because the map asked which groups the catalog programs and
"shoulders" is front delts.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from rt_dashboard.coach import build_today_board
from rt_dashboard.daily_plan_tasks import format_session_quest, plan_from_today_board
from rt_dashboard.models import ExerciseEntry, RecoveryStatus, Session, SetEntry
from rt_dashboard.muscle_sets import build_muscle_set_report, volume_groups
from rt_dashboard.workout_planner import (
    default_catalog,
    generate_workout_plan,
    normalize_goals,
    prescribed_log_matches,
)

ET = ZoneInfo("America/New_York")
GAP = "gap"


def _board(plan):
    return build_today_board(
        as_of="2026-11-18",
        recovery=RecoveryStatus(label="Ready", score=80),
        workout_plan=plan,
        meal_plan={},
        consumed={},
        targets={},
        adherence={},
    )


def _train(plan) -> str:
    board = _board(plan)
    return next(a["text"] for a in board["actions"] if a.get("id") == "train-session")


class QuestCopy(unittest.TestCase):
    def test_open_session_uses_matched_of_planned(self):
        text = _train(
            {
                "session_type": "push",
                "already_trained_today": False,
                "prescribed_match_count": 4,
                "prescribed_planned_count": 5,
                "exercises": [
                    {"name": "Lateral Raises", "prescription": {"sets": 3, "reps": 12, "weight_lbs": 15}}
                ],
            }
        )
        self.assertEqual(text, "Complete today's Push session (4 of 5 lifts as prescribed)")
        self.assertNotIn("SESSION", text)
        self.assertNotIn("(1 lift", text)
        self.assertNotIn("PUSH", text)

    def test_done_session_and_plural(self):
        done = _train(
            {
                "session_type": "pull",
                "already_trained_today": True,
                "prescribed_match_count": 4,
                "prescribed_planned_count": 4,
                "exercises": [],
            }
        )
        self.assertEqual(done, "Pull session complete (4 of 4 lifts as prescribed)")
        one = format_session_quest("legs", 1, 1, done=False)
        self.assertEqual(one, "Complete today's Legs session (1 of 1 lift as prescribed)")
        self.assertNotIn("1 lifts", one)
        self.assertEqual(
            format_session_quest("session", 0, 2, done=True),
            "Session complete (0 of 2 lifts as prescribed)",
        )
        self.assertNotIn("SESSION", format_session_quest("", 2, 2, done=True))

    def test_header_keeps_the_match_count_when_one_leaf_remains(self):
        board = _board(
            {
                "session_type": "push",
                "already_trained_today": False,
                "prescribed_match_count": 4,
                "prescribed_planned_count": 5,
                "exercises": [{"name": "Lateral Raises", "sets": 3, "reps": 12}],
            }
        )
        groups = plan_from_today_board(board, day="2026-11-18")
        training = next(g for g in groups if g.group == "training")
        title = next(it.title for it in training.items if it.slug == "train-session")
        self.assertEqual(title, "Complete today's Push session (4 of 5 lifts as prescribed)")


class PrescribedMatchCount(unittest.TestCase):
    def test_swap_counts_and_off_plan_extra_does_not(self):
        catalog = {
            "exercises": [
                {
                    "id": "db-flat-press",
                    "name": "DB Flat Press",
                    "primary_muscles": ["chest"],
                    "movement": "compound",
                    "movement_pattern": "horizontal_press",
                },
                {
                    "id": "smith-flat-bench",
                    "name": "Smith Flat Bench",
                    "primary_muscles": ["chest"],
                    "movement": "compound",
                    "movement_pattern": "horizontal_press",
                },
                {
                    "id": "db-curl",
                    "name": "DB Curl",
                    "primary_muscles": ["biceps"],
                    "movement": "isolation",
                },
            ]
        }
        morning = [
            {"id": "db-flat-press", "name": "DB Flat Press"},
            {"id": "lateral-raises", "name": "Lateral Raises"},
        ]
        logged = Session(
            date="2026-11-18",
            session_type="push",
            exercises=[
                ExerciseEntry(name="Smith Flat Bench", sets=[SetEntry(weight_lbs=50, sets=3, reps=8)]),
                ExerciseEntry(name="DB Curl", sets=[SetEntry(weight_lbs=20, sets=3, reps=10)]),
            ],
            substitutions=[{"planned_name": "Lateral Raises", "logged_name": "DB Curl", "disposition": "substitution"}],
        )
        # The curl is an off-plan extra. The recorded substitution is what
        # covers Lateral Raises; the smith bench covers the flat press by slot.
        # Drop the substitution and the curl must not become a second match
        # for the press, and must not match Lateral Raises by muscle.
        bare = Session(
            date="2026-11-18",
            session_type="push",
            exercises=[
                ExerciseEntry(name="Smith Flat Bench", sets=[SetEntry(weight_lbs=50, sets=3, reps=8)]),
                ExerciseEntry(name="DB Curl", sets=[SetEntry(weight_lbs=20, sets=3, reps=10)]),
            ],
        )
        swapped = prescribed_log_matches(morning, [bare], catalog, {})
        self.assertEqual([row["name"] for row in swapped], ["DB Flat Press"])
        both = prescribed_log_matches(morning, [logged], catalog, {})
        self.assertEqual(
            [row["name"] for row in both],
            ["DB Flat Press", "Lateral Raises"],
        )

    def test_one_logged_lift_is_one_of_the_plan(self):
        history = [
            Session(
                date="2026-11-16",
                session_type="pull",
                exercises=[ExerciseEntry(name="Pulldowns", sets=[SetEntry(weight_lbs=100, sets=3, reps=8)])],
                status="complete",
                closed_at="2026-11-16T23:00:00-05:00",
            )
        ]
        when = datetime(2026, 11, 17, 18, 0, tzinfo=ET)
        wake = "2026-11-17T07:00:00-05:00"
        before = generate_workout_plan(
            default_catalog(),
            normalize_goals({}),
            history,
            recovery_score=80,
            session_type="legs",
            as_of="2026-11-17",
            now=when,
            last_wake_at=wake,
        )
        self.assertGreaterEqual(len(before.get("exercises") or []), 2)
        first = before["exercises"][0]["name"]
        logged = Session(
            date="2026-11-17",
            session_type="legs",
            exercises=[ExerciseEntry(name=first, sets=[SetEntry(weight_lbs=100, sets=2, reps=8)])],
            status="in_progress",
            closed_at="2026-11-17T20:00:00-05:00",
        )
        after = generate_workout_plan(
            default_catalog(),
            normalize_goals({}),
            history + [logged],
            recovery_score=80,
            session_type="legs",
            as_of="2026-11-17",
            now=datetime(2026, 11, 17, 21, 0, tzinfo=ET),
            last_wake_at=wake,
            train_parent_completed=False,
        )
        matched = int(after.get("prescribed_match_count") or 0)
        planned = int(after.get("prescribed_planned_count") or 0)
        self.assertEqual(matched, 1)
        self.assertGreaterEqual(planned, matched)
        self.assertNotIn(first, [row["name"] for row in after.get("exercises") or []])
        text = _train(after)
        self.assertEqual(
            text,
            f"Complete today's Legs session ({matched} of {planned} lifts as prescribed)",
        )
        self.assertNotIn("SESSION", text)


def _raise_session(name: str) -> dict:
    return {
        "date": "2026-11-17",
        "session_type": "push",
        "exercises": [
            {"name": name, "sets": [{"weight_lbs": 15, "sets": 3, "reps": 12}]}
        ],
    }


class SideDeltCatalog(unittest.TestCase):
    def test_shoulders_tagged_lateral_raise_is_not_grey(self):
        catalog = {
            "exercises": [
                {
                    "id": "lateral-raise",
                    "name": "Lateral Raise",
                    "session_types": ["push"],
                    "primary_muscles": ["shoulders"],
                    "movement": "isolation",
                    "equipment": ["dumbbells"],
                    "available": True,
                }
            ]
        }
        primary, _secondary = volume_groups(catalog["exercises"][0])
        self.assertEqual(primary, ["side_delts"])
        report = build_muscle_set_report(
            {
                "sessions": [_raise_session("Lateral Raise")],
                "workout_store": {"catalog": catalog, "goals": {}, "plan": {"exercises": []}},
            },
            as_of="2026-11-18",
        )
        side = next(row for row in report["body_map"] if row["id"] == "side_delts")
        self.assertNotEqual(side["state"], GAP)
        self.assertGreater(side["sets"], 0)
        self.assertNotIn("no programmed lift", side["detail"])
        self.assertGreater(report["muscle_sets"]["side_delts"]["proper_sets"], 0)

    def test_other_lateral_names_credit_side_delts_on_the_real_catalog(self):
        catalog = default_catalog()
        for name in (
            "Lateral Raise",
            "Side Delt Raise",
            "Side Laterals",
            "Cable Lateral Raise",
        ):
            report = build_muscle_set_report(
                {
                    "sessions": [_raise_session(name)],
                    "workout_store": {"catalog": catalog, "goals": {}, "plan": {"exercises": []}},
                },
                as_of="2026-11-18",
            )
            side = next(row for row in report["body_map"] if row["id"] == "side_delts")
            self.assertNotEqual(side["state"], GAP, name)
            self.assertGreater(side["sets"], 0, name)
            self.assertNotIn("no programmed lift", side["detail"])


if __name__ == "__main__":
    unittest.main()
