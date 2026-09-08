"""Tests for exercise catalog workout planner."""

from __future__ import annotations

import unittest

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import (
    CALF_FAMILY,
    HAMSTRING_CURL_FAMILY,
    HORIZONTAL_PRESS_FAMILY,
    INCLINE_PRESS_FAMILY,
    NAME_ALIASES,
    VERTICAL_PRESS_FAMILY,
    credit_sets_for_exercise,
    generate_workout_plan,
    last_pattern_family_ids,
    last_performance,
    next_letter_after,
    next_session_type,
    pattern_family,
    ppl_logged_on_day,
    prescribe,
    session_types_for_lift_name,
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
                {
                    "id": "rdl",
                    "name": "RDL",
                    "session_types": ["legs"],
                    "primary_muscles": ["hamstrings", "glutes"],
                    "movement": "compound",
                    "default_sets": 3,
                    "default_reps": 8,
                    "rep_range": [6, 10],
                    "priority": 10,
                    "available": True,
                },
                {
                    "id": "seated-leg-curls",
                    "name": "Seated Leg Curls",
                    "session_types": ["legs"],
                    "primary_muscles": ["hamstrings"],
                    "movement": "isolation",
                    "default_sets": 3,
                    "default_reps": 10,
                    "rep_range": [8, 12],
                    "priority": 8,
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

    def test_ppl_logged_on_day(self):
        sessions = [
            _session("2026-08-29", "legs", "RDL", 40, 2, 7),
            _session("2026-08-24", "push", "DB Flat Press", 50),
        ]
        self.assertEqual(ppl_logged_on_day(sessions, "2026-08-29"), "legs")
        self.assertIsNone(ppl_logged_on_day(sessions, "2026-08-28"))

    def test_logged_today_pins_letter_does_not_seed_next_ppl(self):
        sessions = [_session("2026-08-29", "legs", "RDL", 40, 2, 7)]
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            sessions,
            recovery_label="Caution",
            recovery_score=35,
            recovery_sparse=False,
            as_of="2026-08-29",
        )
        self.assertEqual(plan["session_type"], "legs")
        self.assertFalse(plan["already_trained_today"])
        self.assertFalse(plan["is_rest_day"])
        self.assertEqual(plan["ppl_logged_today"], "legs")
        self.assertEqual(plan["next_session_type"], "legs")
        names = [e.get("name") for e in plan["exercises"]]
        self.assertNotIn("DB Flat Press", names)
        self.assertNotIn("RDL", names)
        self.assertIn("Seated Leg Curls", names)

    def test_train_parent_completed_is_day_complete_so_t(self):
        sessions = [_session("2026-08-29", "legs", "RDL", 40, 2, 7)]
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            sessions,
            recovery_label="Caution",
            recovery_score=35,
            recovery_sparse=False,
            as_of="2026-08-29",
            train_parent_completed=True,
        )
        self.assertEqual(plan["session_type"], "legs")
        self.assertTrue(plan["already_trained_today"])
        self.assertFalse(plan["is_rest_day"])
        self.assertEqual(plan["exercises"], [])
        self.assertEqual(plan["next_session_type"], "push")
        self.assertEqual(next_letter_after("legs", self.goals), "push")
        self.assertIn("Already trained today", plan["message"])

    def test_explicit_session_type_still_generates_after_log(self):
        sessions = [_session("2026-08-29", "legs", "RDL", 40, 2, 7)]
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            sessions,
            recovery_score=80,
            session_type="push",
            as_of="2026-08-29",
        )
        self.assertEqual(plan["session_type"], "push")
        self.assertFalse(plan.get("already_trained_today"))
        self.assertTrue(any(e.get("name") == "DB Flat Press" for e in plan["exercises"]))

    def test_session_types_for_lift_name(self):
        types = session_types_for_lift_name("DB Flat Press", self.catalog)
        self.assertEqual(types, ("push",))
        self.assertEqual(session_types_for_lift_name("unknown lift", self.catalog), ())

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

    def test_caution_35_not_sparse_is_rest(self):
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            [],
            recovery_score=35,
            recovery_label="Caution",
            recovery_sparse=False,
        )
        self.assertTrue(plan["is_rest_day"])
        self.assertEqual(plan["session_type"], "rest")
        self.assertEqual(plan["exercises"], [])

    def test_caution_35_sparse_sleep_is_not_rest(self):
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            [_session("2026-07-10", "push", "DB Flat Press", 50)],
            recovery_score=35,
            recovery_label="Caution",
            recovery_sparse=True,
            as_of="2026-07-11",
        )
        self.assertFalse(plan["is_rest_day"])
        self.assertNotEqual(plan["session_type"], "rest")

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

    def test_prescribe_ignores_catalog_default_sets_three(self):
        ex = {
            "default_sets": 3,
            "default_reps": 10,
            "rep_range": [8, 12],
        }
        rx = prescribe(ex, None, default_hard_sets=2)
        self.assertEqual(rx["sets"], 2)
        rx_blind = prescribe(ex, None)
        self.assertEqual(rx_blind["sets"], 2)
        self.assertNotEqual(rx_blind["sets"], 3)

    def test_plan_no_history_uses_goals_hard_sets_not_catalog_three(self):
        catalog = {
            "exercises": [
                {
                    "id": "leg-press",
                    "name": "Leg Press",
                    "session_types": ["legs"],
                    "primary_muscles": ["quads"],
                    "movement": "compound",
                    "default_sets": 3,
                    "default_reps": 10,
                    "rep_range": [8, 12],
                    "priority": 10,
                    "available": True,
                }
            ]
        }
        sessions = [_session("2026-07-21", "pull", "Seated Cable Row", 100, 2, 10)]
        plan = generate_workout_plan(
            catalog,
            {**self.goals, "default_hard_sets": 2, "session_working_set_cap": 14},
            sessions,
            recovery_score=80,
            as_of="2026-07-22",
        )
        self.assertEqual(plan["session_type"], "legs")
        self.assertTrue(plan["exercises"])
        for e in plan["exercises"]:
            self.assertEqual(int((e.get("prescription") or {}).get("sets") or 0), 2)

    def test_lying_and_seated_curl_share_hamstring_family(self):
        self.assertEqual(
            pattern_family(
                {
                    "id": "seated-leg-curls",
                    "name": "Seated Leg Curls",
                    "movement": "isolation",
                    "primary_muscles": ["hamstrings"],
                }
            ),
            HAMSTRING_CURL_FAMILY,
        )
        self.assertEqual(
            pattern_family(
                {
                    "id": "lying-leg-curls",
                    "name": "Lying Leg Curl",
                    "movement": "isolation",
                    "primary_muscles": ["hamstrings"],
                }
            ),
            HAMSTRING_CURL_FAMILY,
        )

    def test_laying_alias_maps_to_lying_leg_curls(self):
        self.assertEqual(NAME_ALIASES["laying leg curl"], "lying-leg-curls")
        self.assertEqual(NAME_ALIASES["lying leg curl"], "lying-leg-curls")
        self.assertEqual(NAME_ALIASES["prone leg curls"], "lying-leg-curls")
        self.assertEqual(
            session_types_for_lift_name(
                "Laying Leg Curl",
                {
                    "exercises": [
                        {
                            "id": "lying-leg-curls",
                            "name": "Lying Leg Curl",
                            "session_types": ["legs"],
                        }
                    ]
                },
            ),
            ("legs",),
        )

    def test_legs_caps_one_hamstring_curl_isolation(self):
        catalog = {
            "exercises": [
                {
                    "id": "leg-press",
                    "name": "Leg Press",
                    "session_types": ["legs"],
                    "primary_muscles": ["quads"],
                    "secondary_muscles": ["glutes", "hamstrings"],
                    "movement": "compound",
                    "equipment": ["leg_press"],
                    "default_sets": 3,
                    "default_reps": 10,
                    "rep_range": [8, 12],
                    "priority": 10,
                    "available": True,
                },
                {
                    "id": "rdl",
                    "name": "RDL",
                    "session_types": ["legs"],
                    "primary_muscles": ["hamstrings", "glutes"],
                    "secondary_muscles": ["back"],
                    "movement": "compound",
                    "equipment": ["barbell"],
                    "default_sets": 3,
                    "default_reps": 8,
                    "rep_range": [6, 10],
                    "priority": 10,
                    "available": True,
                },
                {
                    "id": "seated-leg-curls",
                    "name": "Seated Leg Curls",
                    "session_types": ["legs"],
                    "primary_muscles": ["hamstrings"],
                    "movement": "isolation",
                    "equipment": ["machine"],
                    "default_sets": 3,
                    "default_reps": 12,
                    "rep_range": [10, 15],
                    "priority": 7,
                    "available": True,
                },
                {
                    "id": "lying-leg-curls",
                    "name": "Lying Leg Curl",
                    "session_types": ["legs"],
                    "primary_muscles": ["hamstrings"],
                    "movement": "isolation",
                    "equipment": ["machine"],
                    "default_sets": 3,
                    "default_reps": 12,
                    "rep_range": [10, 15],
                    "priority": 7,
                    "available": True,
                },
                {
                    "id": "calf-raises",
                    "name": "Calf Raises",
                    "session_types": ["legs"],
                    "primary_muscles": ["calves"],
                    "movement": "isolation",
                    "equipment": ["machine"],
                    "default_sets": 3,
                    "default_reps": 12,
                    "rep_range": [10, 15],
                    "priority": 5,
                    "available": True,
                },
            ]
        }
        plan = generate_workout_plan(
            catalog,
            {
                **self.goals,
                "exercises_per_session": 5,
                "default_hard_sets": 2,
            },
            [],
            recovery_score=80,
            session_type="legs",
            as_of="2026-09-08",
        )
        ids = [e["id"] for e in plan["exercises"]]
        curls = [i for i in ids if i in ("seated-leg-curls", "lying-leg-curls")]
        self.assertEqual(len(curls), 1, ids)
        self.assertIn("seated-leg-curls", {e["id"] for e in catalog["exercises"]})
        self.assertIn("lying-leg-curls", {e["id"] for e in catalog["exercises"]})

    def test_calf_extension_and_db_raise_share_family(self):
        self.assertEqual(
            pattern_family(
                {
                    "id": "calf-raises",
                    "name": "Calf Extensions",
                    "movement": "isolation",
                    "primary_muscles": ["calves"],
                }
            ),
            CALF_FAMILY,
        )
        self.assertEqual(
            pattern_family(
                {
                    "id": "db-calf-raises",
                    "name": "DB Calf Raises",
                    "movement": "isolation",
                    "primary_muscles": ["calves"],
                }
            ),
            CALF_FAMILY,
        )

    def test_historical_calf_raises_alias_to_machine_extensions(self):
        self.assertEqual(NAME_ALIASES["calf raises"], "calf-raises")
        self.assertEqual(NAME_ALIASES["calf extensions"], "calf-raises")
        self.assertEqual(NAME_ALIASES["db calf raises"], "db-calf-raises")
        self.assertEqual(NAME_ALIASES["standing calf raise"], "db-calf-raises")
        catalog = {
            "exercises": [
                {
                    "id": "calf-raises",
                    "name": "Calf Extensions",
                    "session_types": ["legs"],
                },
                {
                    "id": "db-calf-raises",
                    "name": "DB Calf Raises",
                    "session_types": ["legs"],
                },
            ]
        }
        self.assertEqual(session_types_for_lift_name("Calf Raises", catalog), ("legs",))
        self.assertEqual(session_types_for_lift_name("Calf Extensions", catalog), ("legs",))
        self.assertEqual(session_types_for_lift_name("DB Calf Raises", catalog), ("legs",))

    def test_legs_caps_one_calf_isolation(self):
        catalog = {
            "exercises": [
                {
                    "id": "leg-press",
                    "name": "Leg Press",
                    "session_types": ["legs"],
                    "primary_muscles": ["quads"],
                    "movement": "compound",
                    "priority": 10,
                    "available": True,
                },
                {
                    "id": "rdl",
                    "name": "RDL",
                    "session_types": ["legs"],
                    "primary_muscles": ["hamstrings", "glutes"],
                    "movement": "compound",
                    "priority": 10,
                    "available": True,
                },
                {
                    "id": "calf-raises",
                    "name": "Calf Extensions",
                    "session_types": ["legs"],
                    "primary_muscles": ["calves"],
                    "movement": "isolation",
                    "priority": 5,
                    "available": True,
                },
                {
                    "id": "db-calf-raises",
                    "name": "DB Calf Raises",
                    "session_types": ["legs"],
                    "primary_muscles": ["calves"],
                    "movement": "isolation",
                    "priority": 5,
                    "available": True,
                },
            ]
        }
        plan = generate_workout_plan(
            catalog,
            {
                **self.goals,
                "exercises_per_session": 5,
                "default_hard_sets": 2,
            },
            [],
            recovery_score=80,
            session_type="legs",
            as_of="2026-09-08",
        )
        ids = [e["id"] for e in plan["exercises"]]
        calves = [i for i in ids if i in ("calf-raises", "db-calf-raises")]
        self.assertEqual(len(calves), 1, ids)

    def test_historical_calf_raises_keeps_machine_extensions_on_plan(self):
        """Last lift named Calf Raises must not rotate Tonight onto DB Calf Raises."""
        catalog = _legs_calf_catalog()
        sessions = [
            _session("2026-09-01", "legs", "Calf Raises", 105, 3, 10),
            _session("2026-09-02", "push", "DB Flat Press", 50, 2, 10),
            _session("2026-09-03", "pull", "Seated Cable Row", 100, 2, 10),
        ]
        plan = generate_workout_plan(
            catalog,
            {
                **self.goals,
                "exercises_per_session": 5,
                "default_hard_sets": 2,
            },
            sessions,
            recovery_score=80,
            session_type="legs",
            as_of="2026-09-08",
        )
        ids = [e["id"] for e in plan["exercises"]]
        names = [e["name"] for e in plan["exercises"]]
        calves = [i for i in ids if i in ("calf-raises", "db-calf-raises")]
        self.assertEqual(calves, ["calf-raises"], ids)
        self.assertIn("Calf Extensions", names)
        self.assertNotIn("DB Calf Raises", names)
        calf = next(e for e in plan["exercises"] if e["id"] == "calf-raises")
        self.assertEqual(calf["prescription"]["weight_lbs"], 105.0)

    def test_last_performance_matches_catalog_id_not_substring(self):
        sessions = [
            _session("2026-09-01", "legs", "Calf Raises", 105, 3, 10),
            _session("2026-09-08", "legs", "DB Calf Raises", 40, 3, 12),
        ]
        machine = last_performance(sessions, "Calf Extensions")
        historic = last_performance(sessions, "Calf Raises")
        db = last_performance(sessions, "DB Calf Raises")
        self.assertIsNotNone(machine)
        self.assertEqual(machine["date"], "2026-09-01")
        self.assertEqual(machine["weight_lbs"], 105)
        self.assertEqual(historic["weight_lbs"], 105)
        self.assertEqual(db["date"], "2026-09-08")
        self.assertEqual(db["weight_lbs"], 40)

    def test_calf_rotates_to_db_only_after_db_has_own_log(self):
        catalog = _legs_calf_catalog()
        sessions = [
            _session("2026-08-20", "legs", "DB Calf Raises", 40, 3, 10),
            _session("2026-09-01", "legs", "Calf Raises", 105, 3, 10),
            _session("2026-09-02", "push", "DB Flat Press", 50, 2, 10),
            _session("2026-09-03", "pull", "Seated Cable Row", 100, 2, 10),
        ]
        plan = generate_workout_plan(
            catalog,
            {
                **self.goals,
                "exercises_per_session": 5,
                "default_hard_sets": 2,
            },
            sessions,
            recovery_score=80,
            session_type="legs",
            as_of="2026-09-08",
        )
        ids = [e["id"] for e in plan["exercises"]]
        calves = [i for i in ids if i in ("calf-raises", "db-calf-raises")]
        self.assertEqual(calves, ["db-calf-raises"], ids)

    def test_pattern_family_maps_press_variants(self):
        self.assertEqual(
            pattern_family({"id": "db-flat-press", "name": "DB Flat Press", "movement": "compound", "primary_muscles": ["chest"]}),
            HORIZONTAL_PRESS_FAMILY,
        )
        self.assertEqual(
            pattern_family({"id": "smith-bench", "name": "Smith Bench", "movement": "compound", "primary_muscles": ["chest"]}),
            HORIZONTAL_PRESS_FAMILY,
        )
        self.assertEqual(
            pattern_family({"id": "db-incline-press", "name": "DB Incline Press", "movement": "compound", "primary_muscles": ["chest"]}),
            INCLINE_PRESS_FAMILY,
        )
        self.assertEqual(
            pattern_family({"id": "db-shoulder-press", "name": "DB Shoulder Press", "movement": "compound", "primary_muscles": ["shoulders"]}),
            VERTICAL_PRESS_FAMILY,
        )
        self.assertIsNone(
            pattern_family({"id": "lateral-raises", "name": "Lateral Raises", "movement": "isolation", "primary_muscles": ["shoulders"]})
        )
        self.assertEqual(
            pattern_family({"id": "bb-bench", "name": "Barbell Bench", "movement": "compound", "primary_muscles": ["chest"]}),
            HORIZONTAL_PRESS_FAMILY,
        )

    def test_last_pattern_family_ids_from_logs(self):
        sessions = [
            _session("2026-09-01", "push", "Smith Bench", 135, 2, 8),
            _session("2026-09-04", "push", "DB Flat Press", 50, 2, 10),
        ]
        last = last_pattern_family_ids(sessions, {})
        self.assertEqual(last[HORIZONTAL_PRESS_FAMILY], "db-flat-press")

    def test_push_caps_one_horizontal_press_when_chest_lags(self):
        """Chest lag used to stack DB Flat + Incline + Smith. Cap at 1 horizontal."""
        catalog = _push_press_catalog()
        sessions = [
            _session("2026-09-01", "pull", "Seated Cable Row", 100, 3, 10),
            _session("2026-09-02", "pull", "Seated Cable Row", 100, 3, 10),
            _session("2026-09-03", "legs", "Leg Press", 200, 3, 10),
        ]
        plan = generate_workout_plan(
            catalog,
            {
                **self.goals,
                "exercises_per_session": 5,
                "auto_focus_muscles": True,
                "default_hard_sets": 2,
            },
            sessions,
            recovery_score=80,
            session_type="push",
            as_of="2026-09-05",
        )
        ids = [e["id"] for e in plan["exercises"]]
        horizontals = [i for i in ids if i in ("db-flat-press", "smith-bench")]
        self.assertEqual(len(horizontals), 1, ids)
        self.assertIn("db-incline-press", ids)

    def test_horizontal_press_rotates_off_last_implement(self):
        catalog = _push_press_catalog()
        after_smith = generate_workout_plan(
            catalog,
            {**self.goals, "exercises_per_session": 5, "auto_focus_muscles": True},
            [
                _session("2026-09-01", "push", "Smith Bench", 135, 2, 8),
                _session("2026-09-02", "pull", "Seated Cable Row", 100, 2, 10),
                _session("2026-09-03", "legs", "Leg Press", 200, 2, 10),
            ],
            recovery_score=80,
            session_type="push",
            as_of="2026-09-05",
        )
        after_smith_ids = [e["id"] for e in after_smith["exercises"]]
        self.assertIn("db-flat-press", after_smith_ids)
        self.assertNotIn("smith-bench", after_smith_ids)

        after_flat = generate_workout_plan(
            catalog,
            {**self.goals, "exercises_per_session": 5, "auto_focus_muscles": True},
            [
                _session("2026-09-01", "push", "DB Flat Press", 50, 2, 10),
                _session("2026-09-02", "pull", "Seated Cable Row", 100, 2, 10),
                _session("2026-09-03", "legs", "Leg Press", 200, 2, 10),
            ],
            recovery_score=80,
            session_type="push",
            as_of="2026-09-05",
        )
        after_flat_ids = [e["id"] for e in after_flat["exercises"]]
        self.assertIn("smith-bench", after_flat_ids)
        self.assertNotIn("db-flat-press", after_flat_ids)


def _legs_calf_catalog():
    return {
        "exercises": [
            {
                "id": "leg-press",
                "name": "Leg Press",
                "session_types": ["legs"],
                "primary_muscles": ["quads"],
                "movement": "compound",
                "priority": 10,
                "available": True,
            },
            {
                "id": "rdl",
                "name": "RDL",
                "session_types": ["legs"],
                "primary_muscles": ["hamstrings", "glutes"],
                "movement": "compound",
                "priority": 10,
                "available": True,
            },
            {
                "id": "calf-raises",
                "name": "Calf Extensions",
                "session_types": ["legs"],
                "primary_muscles": ["calves"],
                "movement": "isolation",
                "priority": 5,
                "available": True,
            },
            {
                "id": "db-calf-raises",
                "name": "DB Calf Raises",
                "session_types": ["legs"],
                "primary_muscles": ["calves"],
                "movement": "isolation",
                "priority": 5,
                "available": True,
            },
        ]
    }


def _push_press_catalog():
    return {
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
                "id": "db-incline-press",
                "name": "DB Incline Press",
                "session_types": ["push"],
                "primary_muscles": ["chest", "shoulders"],
                "movement": "compound",
                "default_sets": 3,
                "default_reps": 10,
                "rep_range": [8, 12],
                "priority": 9,
                "available": True,
            },
            {
                "id": "smith-bench",
                "name": "Smith Bench",
                "session_types": ["push"],
                "primary_muscles": ["chest"],
                "secondary_muscles": ["triceps"],
                "movement": "compound",
                "default_sets": 3,
                "default_reps": 10,
                "rep_range": [8, 12],
                "priority": 8,
                "available": True,
            },
            {
                "id": "db-shoulder-press",
                "name": "DB Shoulder Press",
                "session_types": ["push"],
                "primary_muscles": ["shoulders"],
                "movement": "compound",
                "default_sets": 3,
                "default_reps": 10,
                "rep_range": [8, 12],
                "priority": 9,
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
                "priority": 6,
                "available": True,
            },
            {
                "id": "tricep-pushdowns",
                "name": "Tricep Pushdowns",
                "session_types": ["push"],
                "primary_muscles": ["triceps"],
                "movement": "isolation",
                "default_sets": 3,
                "default_reps": 12,
                "rep_range": [10, 15],
                "priority": 7,
                "available": True,
            },
        ]
    }


if __name__ == "__main__":
    unittest.main()
