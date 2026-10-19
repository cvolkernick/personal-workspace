"""Reload shows frozen plan minus logged sets and volume (#1109).

A session closes only when that remainder has no lifts and no volume.
One logged set does not finish a lift. An empty frozen plan is never done.
Quest-seed-only rows still do not count (#999).
"""

import unittest

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.quest_workout_log import seed_exercise
from rt_dashboard.training_day import (
    closed_ppl_for_planning,
    frozen_plan_remainder,
    lift_leaf_key,
)
from rt_dashboard.workout_planner import generate_workout_plan
from rt_dashboard.workout_store import stamp_today_session

DAY = "2026-10-10"


def _objective(leaves):
    return {
        "frozen": True,
        "target_n": len(leaves),
        "leaves": leaves,
    }


def _leaf(name, sets, reps, weight):
    volume = float(weight) * int(sets) * int(reps)
    return {
        "slug": lift_leaf_key(name),
        "title": f"{name} ({weight:g} lb {sets}×{reps})",
        "notes_extra": "",
        "sets": sets,
        "reps": reps,
        "weight_lbs": weight,
        "volume": volume,
    }


def _lift(name, sets, reps, weight):
    return ExerciseEntry(
        name=name,
        sets=[SetEntry(weight_lbs=weight, sets=sets, reps=reps)],
    )


def _session(exercises, *, letter="legs", closed_at=f"{DAY}T18:00:00-04:00"):
    return Session(
        date=DAY,
        session_type=letter,
        closed_at=closed_at,
        exercises=exercises,
    )


def _catalog():
    rows = []
    for name, movement, letter in (
        ("Leg Press", "compound", "legs"),
        ("RDL", "compound", "legs"),
        ("DB Flat Press", "compound", "push"),
    ):
        rows.append(
            {
                "id": lift_leaf_key(name)[3:],
                "name": name,
                "session_types": [letter],
                "primary_muscles": ["quads"],
                "secondary_muscles": [],
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

PRESS = _leaf("Leg Press", 2, 10, 100)
RDL = _leaf("RDL", 2, 8, 80)


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


def _by_name(plan):
    return {row["name"]: row for row in plan["exercises"]}


class RemainderClose1109(unittest.TestCase):
    def test_partial_lift_regenerates_sets_and_volume_and_is_not_done(self):
        # 1×10 @ 50 against 2×10 @ 100: one set and 1500 lb of tonnage remain.
        # Remaining volume is not weight × remaining sets × reps (that would be 1000).
        objective = _objective([PRESS, RDL])
        session = _session([_lift("Leg Press", 1, 10, 50)])
        rem = frozen_plan_remainder(objective, [session])
        self.assertFalse(rem["done"])
        self.assertGreater(rem["volume"], 0)
        plan = _plan([session], objective)
        self.assertFalse(plan["already_trained_today"])
        self.assertFalse(plan.get("session_closed_today"))
        self.assertEqual(plan["next_session_type"], "legs")
        self.assertFalse(plan["remainder"]["done"])
        press = _by_name(plan)["Leg Press"]
        self.assertEqual(press["prescription"]["sets"], 1)
        self.assertEqual(press["prescription"]["volume"], 1500)
        self.assertEqual(press["sets"], 1)
        self.assertEqual(press["volume"], 1500)
        rdl = _by_name(plan)["RDL"]
        self.assertEqual(rdl["prescription"]["sets"], 2)
        self.assertEqual(rdl["prescription"]["volume"], 1280)
        self.assertNotIn("DB Flat Press", _by_name(plan))
        slot = stamp_today_session(
            plan,
            [session],
            GOALS,
            {"score": 80, "sparse": True},
            as_of=DAY,
            train_parent_completed=True,
            training_objective=objective,
        )
        self.assertFalse(slot["already_trained_today"])
        self.assertFalse(slot.get("session_closed_today"))
        self.assertEqual(slot["next_session_type"], "legs")

    def test_fully_logged_lift_drops_out_and_session_is_not_done(self):
        objective = _objective([PRESS, RDL])
        session = _session([_lift("Leg Press", 2, 10, 100)])
        plan = _plan([session], objective)
        names = list(_by_name(plan))
        self.assertNotIn("Leg Press", names)
        self.assertIn("RDL", names)
        rdl = _by_name(plan)["RDL"]
        self.assertEqual(rdl["prescription"]["sets"], 2)
        self.assertEqual(rdl["prescription"]["volume"], 1280)
        self.assertFalse(plan["already_trained_today"])
        self.assertFalse(plan.get("session_closed_today"))
        self.assertEqual(plan["next_session_type"], "legs")
        self.assertFalse(plan["remainder"]["done"])
        self.assertGreater(plan["remainder"]["volume"], 0)

    def test_every_lift_done_leaves_empty_remainder_and_closes(self):
        objective = _objective([PRESS, RDL])
        session = _session(
            [_lift("Leg Press", 2, 10, 100), _lift("RDL", 2, 8, 80)]
        )
        rem = frozen_plan_remainder(objective, [session])
        self.assertTrue(rem["done"])
        self.assertEqual(rem["lifts"], [])
        self.assertEqual(rem["volume"], 0)
        plan = _plan([session], objective)
        self.assertEqual(plan["remainder"]["lifts"], [])
        self.assertEqual(plan["remainder"]["volume"], 0)
        self.assertTrue(plan["remainder"]["done"])
        self.assertTrue(plan["already_trained_today"])
        self.assertTrue(plan["session_closed_today"])
        self.assertEqual(plan["session_type"], "legs")
        self.assertEqual(plan["next_session_type"], "push")
        names = list(_by_name(plan))
        self.assertNotIn("Leg Press", names)
        self.assertNotIn("RDL", names)
        self.assertIn("DB Flat Press", names)
        self.assertEqual(
            closed_ppl_for_planning(
                [session], as_of=DAY, training_objective=objective
            ),
            "legs",
        )
        slot = stamp_today_session(
            {"exercises": [], "empty": True},
            [session],
            GOALS,
            {"score": 80, "sparse": True},
            as_of=DAY,
            training_objective=objective,
        )
        self.assertTrue(slot["already_trained_today"])
        self.assertTrue(slot.get("session_closed_today"))
        self.assertEqual(slot["session_type"], "legs")
        self.assertEqual(slot["next_session_type"], "push")

    def test_empty_plan_is_not_done_and_invents_nothing(self):
        objective = {"frozen": True, "target_n": 0, "leaves": []}
        session = _session(
            [_lift("Leg Press", 2, 10, 100), _lift("RDL", 2, 8, 80)]
        )
        rem = frozen_plan_remainder(objective, [session])
        self.assertTrue(rem["empty_plan"])
        self.assertFalse(rem["done"])
        self.assertEqual(rem["lifts"], [])
        self.assertEqual(rem["volume"], 0)
        plan = _plan([session], objective)
        self.assertEqual(plan["exercises"], [])
        self.assertEqual(plan["remainder"]["lifts"], [])
        self.assertFalse(plan["remainder"]["done"])
        self.assertTrue(plan["remainder"]["empty_plan"])
        self.assertFalse(plan["already_trained_today"])
        self.assertFalse(plan.get("session_closed_today"))
        self.assertEqual(plan["next_session_type"], "legs")
        self.assertNotIn("DB Flat Press", [row.get("name") for row in plan["exercises"]])
        self.assertIsNone(
            closed_ppl_for_planning(
                [session], as_of=DAY, training_objective=objective
            )
        )

    def test_quest_seed_only_does_not_count_as_logged_work(self):
        objective = _objective([PRESS, RDL])
        seeded = _session([])
        seeded.exercises = [
            seed_exercise(
                "Leg Press",
                title_rx={"weight_lbs": 100, "sets": 2, "reps": 10},
            ),
            seed_exercise(
                "RDL",
                title_rx={"weight_lbs": 80, "sets": 2, "reps": 8},
            ),
        ]
        rem = frozen_plan_remainder(objective, [seeded])
        self.assertFalse(rem["done"])
        self.assertEqual(
            sorted(lift["name"] for lift in rem["lifts"]),
            ["Leg Press", "RDL"],
        )
        press = next(lift for lift in rem["lifts"] if lift["name"] == "Leg Press")
        self.assertEqual(press["sets"], 2)
        self.assertEqual(press["volume"], 2000)
        plan = _plan([seeded], objective)
        self.assertFalse(plan["already_trained_today"])
        self.assertFalse(plan.get("session_closed_today"))
        self.assertEqual(plan["next_session_type"], "legs")
        press_row = _by_name(plan)["Leg Press"]
        self.assertEqual(press_row["prescription"]["sets"], 2)
        self.assertEqual(press_row["prescription"]["volume"], 2000)
        self.assertIn("RDL", _by_name(plan))
        self.assertNotIn("DB Flat Press", _by_name(plan))
        self.assertIsNone(
            closed_ppl_for_planning(
                [seeded], as_of=DAY, training_objective=objective
            )
        )


if __name__ == "__main__":
    unittest.main()
