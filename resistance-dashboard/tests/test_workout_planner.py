"""Tests for exercise catalog workout planner."""

from __future__ import annotations

import unittest

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import (
    credit_sets_for_exercise,
    generate_workout_plan,
    last_performance,
    next_session_type,
    prescribe,
    resolve_focus_for_plan,
    scale_muscle_targets_for_continuity,
    training_continuity,
    weekly_set_tally,
    volume_balance_report,
    muscle_targets,
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
        self.assertIn("volume", plan)

    def test_compound_overlap_credits(self):
        credits = credit_sets_for_exercise(
            ["hamstrings", "glutes"], ["back"], 2, secondary_fraction=0.5
        )
        self.assertAlmostEqual(credits["hamstrings"], 1.0)
        self.assertAlmostEqual(credits["glutes"], 1.0)
        self.assertAlmostEqual(credits["mid_upper_back"], 1.0)  # back → mid_upper_back * 0.5 * 2

    def test_weekly_tally_and_plan_volume(self):
        sessions = [
            _session("2026-07-20", "push", "DB Flat Press", 50, 3, 10),
            _session("2026-07-21", "push", "DB Flat Press", 50, 3, 10),
        ]
        tally = weekly_set_tally(
            sessions, self.catalog, as_of="2026-07-22", window_days=7
        )
        self.assertGreaterEqual(tally["by_muscle"].get("chest", 0), 5.0)
        plan = generate_workout_plan(
            self.catalog,
            {**self.goals, "sets_per_muscle_week_max": 8, "default_hard_sets": 2},
            sessions,
            recovery_score=80,
            session_type="push",
            as_of="2026-07-22",
        )
        self.assertFalse(plan["is_rest_day"])
        self.assertIn("volume", plan)
        self.assertEqual(plan["volume"]["framework"]["id"], "dean_t_balanced_4_8")
        # Plan should not explode set counts past framework
        total_sets = sum(
            int((e.get("prescription") or {}).get("sets") or 0)
            for e in plan["exercises"]
        )
        self.assertLessEqual(total_sets, 14)
        for e in plan["exercises"]:
            self.assertLessEqual(int((e.get("prescription") or {}).get("sets") or 0), 4)

    def test_volume_balance_status(self):
        tally = {
            "by_muscle": {"chest": 2, "delts": 6, "triceps": 12},
            "window_days": 7,
            "start": "2026-07-16",
            "end": "2026-07-22",
            "as_of": "2026-07-22",
            "total_set_credits": 20,
        }
        rep = volume_balance_report(tally, self.goals)
        statuses = {r["muscle"]: r["status"] for r in rep["muscles"]}
        self.assertIn(statuses["chest"], ("under", "low"))
        self.assertEqual(statuses["delts"], "ok")
        self.assertIn(statuses["triceps"], ("high", "over"))

    def test_auto_focus_applied_during_plan(self):
        """Coach logic picks focus from volume gaps without an Ask command."""
        sessions = [
            # Heavy pull/legs history, almost no push → chest/delts lag
            _session("2026-07-18", "pull", "Seated Cable Row", 100, 3, 10),
            _session("2026-07-19", "pull", "Seated Cable Row", 100, 3, 10),
            _session("2026-07-20", "legs", "Leg Press", 200, 3, 10),
        ]
        goals = {
            **self.goals,
            "auto_focus_muscles": True,
            "focus_muscles": [],
        }
        plan = generate_workout_plan(
            self.catalog,
            goals,
            sessions,
            recovery_score=80,
            session_type="push",
            as_of="2026-07-22",
        )
        focus = (plan.get("volume") or {}).get("focus") or {}
        self.assertEqual(focus.get("source"), "auto")
        # Chest should be among auto-focus (zero credits this week)
        self.assertIn("chest", focus.get("muscles") or [])
        self.assertIn("chest", plan.get("goals", {}).get("focus_muscles") or [])

    def test_manual_focus_pin_when_auto_off(self):
        tally = {
            "by_muscle": {"chest": 0, "glutes": 0, "delts": 8},
            "window_days": 7,
        }
        res = resolve_focus_for_plan(
            {"auto_focus_muscles": False, "focus_muscles": ["glutes"]},
            tally,
        )
        self.assertEqual(res["source"], "manual")
        self.assertEqual(res["muscles"], ["glutes"])

    def test_training_continuity_phases(self):
        self.assertEqual(training_continuity(0)["phase"], "normal")
        self.assertEqual(training_continuity(6)["phase"], "normal")
        self.assertEqual(training_continuity(7)["phase"], "rusty")
        self.assertEqual(training_continuity(20)["phase"], "return")
        self.assertEqual(training_continuity(40)["phase"], "reentry")
        self.assertEqual(training_continuity(90)["phase"], "restart")
        none_c = training_continuity(None)
        self.assertEqual(none_c["phase"], "restart")
        self.assertLess(none_c["load_multiplier"], 1.0)
        self.assertFalse(training_continuity(40)["allow_load_progression"])
        self.assertTrue(training_continuity(3)["allow_load_progression"])

    def test_prescribe_reentry_cuts_load_no_progression(self):
        ex = {
            "default_sets": 3,
            "default_reps": 10,
            "rep_range": [8, 12],
        }
        cont = training_continuity(40)
        # Last hit top of range — normal mode would add weight; re-entry must not
        rx = prescribe(
            ex,
            {"weight_lbs": 50, "sets": 3, "reps": 12, "date": "2026-06-01"},
            recovery_score=80,
            continuity=cont,
        )
        self.assertAlmostEqual(rx["weight_lbs"], round(50 * cont["load_multiplier"], 1))
        self.assertLess(rx["weight_lbs"], 50)
        self.assertEqual(rx["reps"], 8)  # bottom of range for technique
        self.assertLessEqual(rx["sets"], 2)
        self.assertIn("Re-entry", rx["rationale"])

    def test_scale_bands_for_continuity(self):
        bands = muscle_targets(self.goals)
        cont = training_continuity(40)
        scaled = scale_muscle_targets_for_continuity(bands, cont)
        for m in ("chest", "quads"):
            self.assertLess(scaled[m]["min"], bands[m]["min"])
            self.assertLess(scaled[m]["max"], bands[m]["max"])

    def test_plan_after_long_layoff_uses_continuity(self):
        # Last session ~40 days before as_of
        sessions = [
            _session("2026-06-10", "push", "DB Flat Press", 100, 3, 10),
        ]
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            sessions,
            recovery_score=80,
            session_type="push",
            as_of="2026-07-20",
        )
        self.assertFalse(plan["is_rest_day"])
        cont = (plan.get("context") or {}).get("training_continuity") or {}
        self.assertEqual(cont.get("phase"), "reentry")
        self.assertEqual(cont.get("days_since"), 40)
        # Press history should be cut, not progressed
        press = next(
            (e for e in plan["exercises"] if "Flat Press" in (e.get("name") or "")),
            None,
        )
        self.assertIsNotNone(press)
        w = float((press.get("prescription") or {}).get("weight_lbs") or 0)
        self.assertAlmostEqual(w, round(100 * cont["load_multiplier"], 1))
        self.assertLess(
            int((plan.get("context") or {}).get("session_working_set_cap") or 99),
            14,
        )
        self.assertIn("Re-entry", plan.get("message") or "")


if __name__ == "__main__":
    unittest.main()
