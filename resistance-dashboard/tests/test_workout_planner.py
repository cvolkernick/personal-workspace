"""Tests for exercise catalog workout planner."""

from __future__ import annotations

import unittest

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import (
    generate_workout_plan,
    last_performance,
    next_session_type,
    prescribe,
)


def _session(date, st, name, weight, sets=3, reps=10):
    return Session(
        date=date,
        session_type=st,
        exercises=[
            ExerciseEntry(
                name=name,
                sets=[SetEntry(weight_lbs=weight, sets=sets, reps=reps)],
            )
        ],
    )


class TestWorkoutPlanner(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "exercises": [
                {
                    "id": "db-flat-press",
                    "name": "DB Flat Press",
                    "session_types": ["push"],
                    "primary_muscles": ["chest"],
                    "secondary_muscles": ["triceps"],
                    "movement": "compound",
                    "default_sets": 3,
                    "default_reps": 10,
                    "rep_range": [8, 12],
                    "priority": 10,
                    "available": True,
                },
                {
                    "id": "lateral-raises",
                    "name": "Lateral Raises",
                    "session_types": ["push"],
                    "primary_muscles": ["shoulders"],
                    "movement": "isolation",
                    "default_sets": 3,
                    "default_reps": 12,
                    "rep_range": [10, 15],
                    "priority": 5,
                    "available": True,
                },
                {
                    "id": "seated-cable-row",
                    "name": "Seated Cable Row",
                    "session_types": ["pull"],
                    "primary_muscles": ["back"],
                    "movement": "compound",
                    "default_sets": 3,
                    "default_reps": 10,
                    "rep_range": [8, 12],
                    "priority": 10,
                    "available": True,
                },
            ]
        }
        self.goals = {
            "rotation": ["push", "pull", "legs"],
            "exercises_per_session": 4,
            "rest_if_recovery_below": 40,
            "prefer_compounds_first": True,
        }

    def test_next_session_type(self):
        sessions = [_session("2026-07-10", "push", "DB Flat Press", 50)]
        self.assertEqual(next_session_type(sessions, self.goals), "pull")

    def test_last_performance(self):
        sessions = [
            _session("2026-07-01", "push", "DB Flat Press", 45, 3, 10),
            _session("2026-07-10", "push", "DB Flat Press", 50, 3, 12),
        ]
        last = last_performance(sessions, "DB Flat Press")
        self.assertIsNotNone(last)
        self.assertEqual(last["date"], "2026-07-10")
        self.assertEqual(last["weight_lbs"], 50)

    def test_prescribe_progress_load(self):
        ex = {
            "default_sets": 3,
            "default_reps": 10,
            "rep_range": [8, 12],
        }
        rx = prescribe(ex, {"weight_lbs": 50, "sets": 3, "reps": 12, "date": "2026-07-10"})
        self.assertEqual(rx["weight_lbs"], 55.0)
        self.assertEqual(rx["reps"], 8)

    def test_generate_plan_uses_history(self):
        sessions = [
            _session("2026-07-10", "push", "DB Flat Press", 50, 3, 10),
        ]
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            sessions,
            recovery_label="Ready",
            recovery_score=80,
        )
        self.assertEqual(plan["session_type"], "pull")  # next after push
        self.assertFalse(plan["is_rest_day"])
        self.assertTrue(len(plan["exercises"]) >= 1)

    def test_rest_when_recovery_low(self):
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            [],
            recovery_score=20,
            recovery_label="Needs Rest",
        )
        self.assertTrue(plan["is_rest_day"])
        self.assertEqual(plan["session_type"], "rest")


if __name__ == "__main__":
    unittest.main()
