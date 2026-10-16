"""A persisted session closes only when the frozen target is delta-dry (#1109)."""

import unittest

from rt_dashboard.gym_calendar import civil_closed_letter
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.quest_workout_log import seed_exercise
from rt_dashboard.training_day import (
    closed_ppl_for_planning,
    day_complete_for_planning,
    lift_leaf_key,
    objective_delta_dry,
)
from rt_dashboard.workout_planner import generate_workout_plan
from rt_dashboard.workout_store import stamp_today_session

DAY = "2026-10-10"
LEGS = (
    "Leg Press",
    "RDL",
    "Seated Leg Curls",
    "Calf Raises",
    "Goblet Squat",
)


def _objective(names=LEGS):
    return {
        "frozen": True,
        "target_n": len(names),
        "leaves": [
            {"slug": lift_leaf_key(name), "title": name} for name in names
        ],
    }


def _lift(name, sets=1, reps=8):
    return ExerciseEntry(
        name=name,
        sets=[SetEntry(weight_lbs=40, sets=sets, reps=reps)],
    )


def _session(names, *, letter="legs", closed_at=f"{DAY}T18:00:00-04:00"):
    return Session(
        date=DAY,
        session_type=letter,
        closed_at=closed_at,
        exercises=[_lift(name) for name in names],
    )


def _catalog():
    rows = []
    for name, movement in (
        ("Leg Press", "compound"),
        ("RDL", "compound"),
        ("Seated Leg Curls", "isolation"),
        ("Calf Raises", "isolation"),
        ("Goblet Squat", "compound"),
        ("DB Flat Press", "compound"),
    ):
        letter = "push" if name == "DB Flat Press" else "legs"
        rows.append(
            {
                "id": lift_leaf_key(name)[3:],
                "name": name,
                "session_types": [letter],
                "primary_muscles": ["quads"],
                "movement": movement,
                "default_sets": 2,
                "default_reps": 8,
                "rep_range": [6, 10],
                "priority": 10,
                "available": True,
            }
        )
    return {"exercises": rows}


GOALS = {
    "rotation": ["push", "pull", "legs"],
    "exercises_per_session": 5,
    "rest_if_recovery_below": 40,
    "prefer_compounds_first": True,
}


class SubstantiveClose1109(unittest.TestCase):
    def test_two_of_five_is_not_dry_and_does_not_close(self):
        session = _session(LEGS[:2])
        objective = _objective()
        self.assertFalse(objective_delta_dry([session], objective))
        self.assertIsNone(
            closed_ppl_for_planning(
                [session], as_of=DAY, training_objective=objective
            )
        )
        self.assertIsNone(
            civil_closed_letter([session], DAY, training_objective=objective)
        )

    def test_full_target_is_dry_and_closes(self):
        session = _session(LEGS)
        objective = _objective()
        self.assertTrue(objective_delta_dry([session], objective))
        self.assertEqual(
            closed_ppl_for_planning(
                [session], as_of=DAY, training_objective=objective
            ),
            "legs",
        )
        self.assertEqual(
            civil_closed_letter([session], DAY, training_objective=objective),
            "legs",
        )

    def test_split_real_work_is_dry_together(self):
        first = _session(LEGS[:2])
        second = _session(LEGS[2:], closed_at=f"{DAY}T19:00:00-04:00")
        objective = _objective()
        self.assertTrue(objective_delta_dry([first, second], objective))
        self.assertEqual(
            closed_ppl_for_planning(
                [first, second], as_of=DAY, training_objective=objective
            ),
            "legs",
        )

    def test_quest_seed_only_still_does_not_close(self):
        seeded = _session([])
        seeded.exercises = [
            seed_exercise(
                "RDL",
                title_rx={"weight_lbs": 40, "sets": 2, "reps": 8},
            )
        ]
        objective = _objective()
        self.assertIsNone(
            closed_ppl_for_planning(
                [seeded], as_of=DAY, training_objective=objective
            )
        )

    def test_no_objective_keeps_persist_close(self):
        session = _session(LEGS[:1])
        self.assertEqual(
            closed_ppl_for_planning([session], as_of=DAY),
            "legs",
        )

    def test_parent_complete_below_bar_is_not_day_complete(self):
        self.assertFalse(
            day_complete_for_planning(
                True,
                ppl_logged_today="legs",
                below_substantive_bar=True,
            )
        )

    def test_two_by_one_plan_stays_open_and_suggests_the_rest(self):
        plan = generate_workout_plan(
            _catalog(),
            GOALS,
            [_session(LEGS[:2])],
            recovery_score=80,
            recovery_sparse=True,
            as_of=DAY,
            training_objective=_objective(),
        )
        self.assertEqual(plan["session_type"], "legs")
        self.assertFalse(plan["already_trained_today"])
        self.assertFalse(plan.get("session_closed_today"))
        self.assertEqual(plan["next_session_type"], "legs")
        self.assertEqual(
            [row["name"] for row in plan["exercises"]],
            list(LEGS[2:]),
        )
        self.assertNotIn("DB Flat Press", [row["name"] for row in plan["exercises"]])

    def test_dry_plan_rolls_to_the_next_letter(self):
        plan = generate_workout_plan(
            _catalog(),
            GOALS,
            [_session(LEGS)],
            recovery_score=80,
            recovery_sparse=True,
            as_of=DAY,
            training_objective=_objective(),
        )
        self.assertEqual(plan["session_type"], "legs")
        self.assertTrue(plan["already_trained_today"])
        self.assertTrue(plan["session_closed_today"])
        self.assertEqual(plan["next_session_type"], "push")
        self.assertTrue(plan["exercises"])
        self.assertIn("DB Flat Press", [row["name"] for row in plan["exercises"]])

    def test_stamp_two_by_one_does_not_advance_rotation(self):
        slot = stamp_today_session(
            {"exercises": [], "empty": True},
            [_session(LEGS[:2])],
            GOALS,
            {"score": 80, "sparse": True},
            as_of=DAY,
            train_parent_completed=True,
            training_objective=_objective(),
        )
        self.assertEqual(slot["session_type"], "legs")
        self.assertEqual(slot["next_session_type"], "legs")
        self.assertFalse(slot["already_trained_today"])
        self.assertFalse(slot.get("session_closed_today"))
        self.assertEqual(slot["ppl_logged_today"], "legs")

    def test_stamp_dry_session_rolls(self):
        slot = stamp_today_session(
            {"exercises": [], "empty": True},
            [_session(LEGS)],
            GOALS,
            {"score": 80, "sparse": True},
            as_of=DAY,
            training_objective=_objective(),
        )
        self.assertTrue(slot["already_trained_today"])
        self.assertTrue(slot.get("session_closed_today"))
        self.assertEqual(slot["session_type"], "legs")
        self.assertEqual(slot["next_session_type"], "push")


if __name__ == "__main__":
    unittest.main()
