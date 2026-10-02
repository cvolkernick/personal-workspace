"""Manual-log Last line and the next target both use the first working set (#977)."""

from __future__ import annotations

import unittest

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import generate_workout_plan, last_performance, prescribe


def _compound():
    return {
        "id": "db-flat-press",
        "name": "DB Flat Press",
        "movement": "compound",
        "equipment": ["dumbbells"],
        "primary_muscles": ["chest"],
        "secondary_muscles": [],
        "session_types": ["push"],
        "default_sets": 2,
        "default_reps": 8,
        "rep_range": [8, 12],
        "priority": 10,
        "available": True,
    }


def _session(sets):
    return Session(
        date="2026-09-29",
        session_type="push",
        exercises=[ExerciseEntry(name="DB Flat Press", sets=sets)],
    )


class FirstWorkingSet977(unittest.TestCase):
    def test_later_heavier_set_does_not_pick_last_or_next_target(self):
        session = _session(
            [
                SetEntry(weight_lbs=100, sets=1, reps=8),
                SetEntry(weight_lbs=110, sets=1, reps=5),
            ]
        )
        last = last_performance([session], "DB Flat Press")
        self.assertEqual(last["weight_lbs"], 100.0)
        self.assertEqual(last["reps"], 8)
        self.assertEqual(last["first_weight_lbs"], 100.0)
        self.assertEqual(last["first_reps"], 8)
        self.assertEqual(last["sets"], 2)
        self.assertEqual(last["top_weight_lbs"], 110.0)

        rx = prescribe(_compound(), last, recovery_score=80)
        self.assertEqual(rx["progression_reason"], "hold_plus1")
        self.assertEqual(rx["weight_lbs"], 100.0)
        self.assertEqual(rx["target_reps"], 9)
        self.assertNotEqual(rx["weight_lbs"], 110.0)

        plan = generate_workout_plan(
            {"exercises": [_compound()]},
            {
                "rotation": ["push", "pull", "legs"],
                "exercises_per_session": 5,
                "default_hard_sets": 2,
                "auto_focus_muscles": False,
                "focus_muscles": ["chest"],
                "maintenance_sets_per_muscle_week": 20,
                "sets_per_muscle_week_max": 20,
                "sets_per_muscle_week_priority_max": 20,
            },
            [session],
            recovery_score=80,
            session_type="push",
            as_of="2026-09-30",
        )
        self.assertFalse(plan["is_rest_day"])
        named = [ex for ex in plan["exercises"] if ex["name"] == "DB Flat Press"]
        self.assertEqual(len(named), 1)
        prescription = named[0]["prescription"]
        self.assertEqual(prescription["weight_lbs"], 100.0)
        self.assertEqual(prescription["reps"], 9)
        self.assertEqual(named[0]["last"]["weight_lbs"], 100.0)
        self.assertEqual(named[0]["last"]["reps"], 8)

    def test_one_set_log_stays(self):
        session = _session([SetEntry(weight_lbs=110, sets=4, reps=8)])
        last = last_performance([session], "DB Flat Press")
        self.assertEqual(last["weight_lbs"], 110.0)
        self.assertEqual(last["sets"], 4)
        self.assertEqual(last["reps"], 8)
        rx = prescribe(_compound(), last, recovery_score=80)
        self.assertEqual(rx["weight_lbs"], 110.0)
        self.assertEqual(rx["target_reps"], 9)
        self.assertEqual(rx["progression_reason"], "hold_plus1")


if __name__ == "__main__":
    unittest.main()
