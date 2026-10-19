"""Close only when the frozen target has no sets and no volume left (#1109)."""

import unittest

from rt_dashboard.gym_calendar import civil_closed_letter
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.quest_workout_log import seed_exercise
from rt_dashboard.training_day import (
    closed_ppl_for_planning,
    day_complete_for_planning,
    lift_leaf_key,
    session_remainder,
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
PLANNED_SETS = 3
PLANNED_REPS = 8
PLANNED_WEIGHT = 40


def _leaf(name, sets=PLANNED_SETS, reps=PLANNED_REPS, weight=PLANNED_WEIGHT):
    return {
        "slug": lift_leaf_key(name),
        "name": name,
        "title": f"{name} ({weight:g} lb {sets}×{reps})",
        "sets": sets,
        "reps": reps,
        "weight_lbs": weight,
    }


def _objective(names=LEGS, *, frozen=True):
    leaves = [_leaf(name) for name in names]
    return {"frozen": frozen, "target_n": len(leaves), "leaves": leaves}


def _planned_volume(sets=PLANNED_SETS, reps=PLANNED_REPS, weight=PLANNED_WEIGHT):
    return weight * sets * reps


def _lift(name, sets=1, reps=PLANNED_REPS, weight=PLANNED_WEIGHT):
    return ExerciseEntry(
        name=name,
        sets=[SetEntry(weight_lbs=weight, sets=sets, reps=reps)],
    )


def _session(rows, *, letter="legs", closed_at=f"{DAY}T18:00:00-04:00"):
    return Session(
        date=DAY,
        session_type=letter,
        closed_at=closed_at,
        exercises=list(rows),
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
                "secondary_muscles": [],
                "movement": movement,
                "equipment": [],
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


def _plan(sessions, objective):
    return generate_workout_plan(
        _catalog(),
        GOALS,
        sessions,
        recovery_score=80,
        recovery_sparse=True,
        as_of=DAY,
        training_objective=objective,
    )


class RemainderClose1109(unittest.TestCase):
    def test_partial_lift_regenerates_sets_and_volume_and_stays_open(self):
        session = _session([_lift("Leg Press", sets=1)])
        objective = _objective()
        rem = session_remainder(objective, [session])
        by_name = {row["name"]: row for row in rem["lifts"]}
        self.assertIn("Leg Press", by_name)
        self.assertEqual(by_name["Leg Press"]["sets_remaining"], 2)
        self.assertEqual(by_name["Leg Press"]["volume_remaining"], 640)
        self.assertEqual(len(rem["lifts"]), 5)
        self.assertFalse(rem["done"])
        self.assertIsNone(
            closed_ppl_for_planning([session], as_of=DAY, training_objective=objective)
        )
        plan = _plan([session], objective)
        planned = {row["name"]: row for row in plan["exercises"]}
        self.assertEqual(planned["Leg Press"]["sets"], 2)
        self.assertEqual(planned["Leg Press"]["volume"], 640)
        self.assertEqual(len(plan["exercises"]), 5)
        self.assertFalse(plan["already_trained_today"])
        self.assertFalse(plan["session_closed_today"])
        self.assertFalse(plan["remainder"]["done"])
        self.assertEqual(plan["next_session_type"], "legs")

    def test_volume_short_of_a_full_set_count_stays_open(self):
        session = _session([_lift("Leg Press", sets=PLANNED_SETS, reps=4)])
        plan = _plan([session], _objective())
        row = next(item for item in plan["exercises"] if item["name"] == "Leg Press")
        self.assertEqual(row["sets_remaining"], 0)
        self.assertEqual(row["volume_remaining"], 480)
        self.assertFalse(plan["already_trained_today"])
        self.assertFalse(plan["remainder"]["empty"])

    def test_fully_logged_lift_drops_out_while_others_remain(self):
        session = _session([_lift("Leg Press", sets=PLANNED_SETS)])
        objective = _objective()
        plan = _plan([session], objective)
        names = [row["name"] for row in plan["exercises"]]
        self.assertNotIn("Leg Press", names)
        self.assertEqual(names, list(LEGS[1:]))
        self.assertFalse(plan["already_trained_today"])
        self.assertFalse(plan["session_closed_today"])
        self.assertEqual(plan["remainder"]["volume"], _planned_volume() * 4)

    def test_two_by_one_against_five_does_not_close(self):
        session = _session([_lift(name, sets=1) for name in LEGS[:2]])
        objective = _objective()
        self.assertIsNone(
            closed_ppl_for_planning([session], as_of=DAY, training_objective=objective)
        )
        self.assertIsNone(
            civil_closed_letter([session], DAY, training_objective=objective)
        )
        plan = _plan([session], objective)
        self.assertEqual(
            [row["name"] for row in plan["exercises"]],
            list(LEGS),
        )
        self.assertFalse(plan["already_trained_today"])
        self.assertFalse(plan["session_closed_today"])
        self.assertEqual(plan["next_session_type"], "legs")

    def test_every_lift_fully_logged_closes_and_rolls(self):
        session = _session([_lift(name, sets=PLANNED_SETS) for name in LEGS])
        objective = _objective()
        rem = session_remainder(objective, [session])
        self.assertEqual(rem["lifts"], [])
        self.assertEqual(rem["volume"], 0.0)
        self.assertTrue(rem["done"])
        self.assertEqual(
            closed_ppl_for_planning([session], as_of=DAY, training_objective=objective),
            "legs",
        )
        plan = _plan([session], objective)
        self.assertTrue(plan["remainder"]["empty"])
        self.assertTrue(plan["remainder"]["done"])
        self.assertEqual(plan["remainder"]["lifts"], [])
        self.assertTrue(plan["already_trained_today"])
        self.assertTrue(plan["session_closed_today"])
        self.assertEqual(plan["session_type"], "legs")
        self.assertEqual(plan["next_session_type"], "push")
        slot = stamp_today_session(
            {"exercises": [], "empty": True},
            [session],
            GOALS,
            {"score": 80, "sparse": True},
            as_of=DAY,
            training_objective=objective,
        )
        self.assertTrue(slot["already_trained_today"])
        self.assertTrue(slot["session_closed_today"])
        self.assertEqual(slot["next_session_type"], "push")

    def test_empty_frozen_target_is_not_done_and_invents_nothing(self):
        objective = {"frozen": True, "target_n": 0, "leaves": []}
        session = _session([_lift("Leg Press", sets=PLANNED_SETS)])
        rem = session_remainder(objective, [session])
        self.assertTrue(rem["empty_plan"])
        self.assertFalse(rem["done"])
        self.assertEqual(rem["lifts"], [])
        self.assertIsNone(
            closed_ppl_for_planning([session], as_of=DAY, training_objective=objective)
        )
        plan = _plan([session], objective)
        self.assertEqual(plan["exercises"], [])
        self.assertFalse(plan["already_trained_today"])
        self.assertFalse(plan["session_closed_today"])
        self.assertTrue(plan["remainder"]["empty_plan"])
        self.assertFalse(plan["remainder"]["done"])
        self.assertNotIn("generate_error", plan)
        self.assertFalse(
            day_complete_for_planning(
                True,
                ppl_logged_today="legs",
                below_substantive_bar=True,
            )
        )

    def test_quest_seed_only_does_not_count_or_close(self):
        seeded = _session([])
        seeded.exercises = [
            seed_exercise(
                "Leg Press",
                title_rx={"weight_lbs": 40, "sets": PLANNED_SETS, "reps": PLANNED_REPS},
            )
        ]
        objective = _objective()
        rem = session_remainder(objective, [seeded])
        self.assertEqual(len(rem["lifts"]), 5)
        self.assertFalse(rem["done"])
        self.assertIsNone(
            closed_ppl_for_planning([seeded], as_of=DAY, training_objective=objective)
        )

    def test_title_only_leaf_is_the_cache_shape(self):
        objective = {
            "frozen": True,
            "target_n": 1,
            "leaves": [
                {
                    "slug": lift_leaf_key("Leg Press"),
                    "title": "Leg Press (40 lb 3×8)",
                    "notes_extra": "",
                }
            ],
        }
        session = _session([_lift("Leg Press", sets=1)])
        rem = session_remainder(objective, [session])
        self.assertEqual(rem["lifts"][0]["sets_remaining"], 2)
        self.assertEqual(rem["lifts"][0]["volume_remaining"], 640)
        self.assertFalse(rem["done"])

    def test_no_objective_keeps_persist_close(self):
        session = _session([_lift("Leg Press", sets=1)])
        self.assertEqual(closed_ppl_for_planning([session], as_of=DAY), "legs")

    def test_split_logs_that_meet_the_prescription_close_together(self):
        first = _session([_lift(name, sets=PLANNED_SETS) for name in LEGS[:2]])
        second = _session(
            [_lift(name, sets=PLANNED_SETS) for name in LEGS[2:]],
            closed_at=f"{DAY}T19:00:00-04:00",
        )
        objective = _objective()
        self.assertTrue(session_remainder(objective, [first, second])["done"])
        self.assertEqual(
            closed_ppl_for_planning(
                [first, second], as_of=DAY, training_objective=objective
            ),
            "legs",
        )


if __name__ == "__main__":
    unittest.main()
