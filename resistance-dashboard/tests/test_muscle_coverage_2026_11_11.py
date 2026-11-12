"""Muscle-coverage scoreboard, body map, and Today plan agreement.

The planner and the Trends tile share one group list, unsplit primary
credit, and the Monday–Sunday ET week. A push of presses does not fill
side or rear delts. Abs has a programmed lift. Hip adduction can be
ranked. Back extension credits lower back.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.agent_plan import prefer_rendered_session
from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.muscle_sets import build_muscle_set_report, coverage_state
from rt_dashboard.priority_ramp import this_week_credits
from rt_dashboard.custom_movements import merge_custom_universe
from rt_dashboard.workout_planner import (
    credit_sets_for_exercise,
    default_catalog,
    expand_planner_muscles,
    generate_workout_plan,
    programmed_groups,
)

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

RAMP = {
    "rotation": ["push", "pull", "legs"],
    "exercises_per_session": 5,
    "prefer_compounds_first": True,
    "auto_focus_muscles": True,
    "focus_muscles": [],
    "default_hard_sets": 2,
    "sets_per_muscle_week_min": 4,
    "sets_per_muscle_week_max": 8,
    "sets_per_muscle_week_priority_max": 10,
    "maintenance_sets_per_muscle_week": 3,
    "session_working_set_cap": 14,
    "secondary_set_fraction": 0.5,
    "volume_sets_target": 10,
    "volume_ramp_increment": 2,
    "volume_ramp_started_on": "2026-11-09",
    "volume_ramp_equal": True,
    "pinned_lifts": {},
}


def _session(day, kind, name, sets, reps=8, weight=100):
    return Session(
        date=day,
        session_type=kind,
        exercises=[
            ExerciseEntry(
                name=name,
                sets=[SetEntry(weight_lbs=weight, sets=sets, reps=reps)],
            )
        ],
    )


def _hard_sets(plan):
    return sum(
        int((row.get("prescription") or {}).get("sets") or 0)
        for row in plan.get("exercises") or []
    )


def _lift(eid, name, letter, primary, secondary, movement, equipment, priority, pattern=""):
    row = {
        "id": eid,
        "name": name,
        "session_types": [letter],
        "primary_muscles": primary,
        "secondary_muscles": secondary,
        "volume_primary": primary,
        "volume_secondary": secondary,
        "movement": movement,
        "equipment": equipment,
        "default_sets": 2,
        "default_reps": 8,
        "rep_range": [8, 15] if movement == "isolation" else [5, 9],
        "priority": priority,
        "available": True,
    }
    if pattern:
        row["movement_pattern"] = pattern
    return row


def _credit(plan, muscle):
    total = 0.0
    for row in plan.get("exercises") or []:
        total += float((row.get("set_credits") or {}).get(muscle) or 0)
    return total


class ScoreboardTests(unittest.TestCase):
    def test_planner_and_tile_agree_on_lats_for_the_same_week(self):
        # 2026-11-02–08. Pulldowns are lats. Rows are upper back, with lats
        # only as a secondary. The old split reported planner lats at ~2
        # while the tile's combined lats/upper back sat near 10.
        catalog = default_catalog()
        sessions = [
            _session("2026-11-03", "pull", "Pulldowns", 4),
            _session("2026-11-05", "pull", "Seated Cable Row", 6, weight=80),
        ]
        planner = this_week_credits(sessions, catalog, "2026-11-08", 0.5)
        report = build_muscle_set_report(
            {
                "sessions": [
                    {
                        "date": s.date,
                        "session_type": s.session_type,
                        "exercises": [
                            {
                                "name": s.exercises[0].name,
                                "sets": [
                                    {
                                        "weight_lbs": s.exercises[0].sets[0].weight_lbs,
                                        "sets": s.exercises[0].sets[0].sets,
                                        "reps": s.exercises[0].sets[0].reps,
                                    }
                                ],
                            }
                        ],
                    }
                    for s in sessions
                ],
                "workout_store": {"catalog": catalog, "goals": dict(RAMP)},
            },
            as_of="2026-11-08",
        )
        self.assertNotIn("lats_upper_back", report["muscle_sets"])
        self.assertEqual(report["window"]["type"], "rolling_7d")
        # Ramp credit stays split. The tile's proper_sets stay unsplit.
        self.assertGreater(report["muscle_sets"]["lats"]["proper_sets"], planner["lats"])
        self.assertGreater(planner["lats"], 0)
        self.assertGreater(planner["mid_upper_back"], 0)
        split = credit_sets_for_exercise(
            ["lats", "back"], [], 4, secondary_fraction=0.5
        )
        self.assertAlmostEqual(split["lats"], 2.0)
        self.assertAlmostEqual(split["mid_upper_back"], 2.0)

    def test_presses_alone_leave_side_and_rear_delts_under_target(self):
        catalog = {
            "exercises": [
                {
                    "id": "db-flat-press",
                    "name": "DB Flat Press",
                    "session_types": ["push"],
                    "primary_muscles": ["chest"],
                    "secondary_muscles": ["triceps", "front_delts"],
                    "volume_primary": ["chest"],
                    "volume_secondary": ["triceps", "front_delts"],
                    "movement": "compound",
                    "equipment": ["dumbbells"],
                    "default_sets": 2,
                    "default_reps": 8,
                    "rep_range": [5, 9],
                    "priority": 10,
                    "available": True,
                },
                {
                    "id": "db-shoulder-press",
                    "name": "DB Shoulder Press",
                    "session_types": ["push"],
                    "primary_muscles": ["front_delts"],
                    "secondary_muscles": ["triceps", "side_delts"],
                    "volume_primary": ["front_delts"],
                    "volume_secondary": ["triceps", "side_delts"],
                    "movement": "compound",
                    "equipment": ["dumbbells"],
                    "default_sets": 2,
                    "default_reps": 8,
                    "rep_range": [5, 9],
                    "priority": 9,
                    "available": True,
                },
            ]
        }
        # Prior week already has the press muscles near the ramp, so a
        # press day cannot satisfy side or rear delts by overlap.
        with mock.patch(
            "rt_dashboard.priority_ramp.prior_week_credits",
            return_value={"chest": 6, "front_delts": 6, "side_delts": 8, "triceps": 4},
        ):
            plan = generate_workout_plan(
                catalog,
                dict(RAMP),
                [],
                recovery_score=80,
                recovery_label="Ready",
                session_type="push",
                as_of="2026-11-11",
                equipment={
                "items": [
                    {
                        "id": "dumbbells",
                        "name": "Dumbbells",
                        "tag": "dumbbells",
                        "source": "owned",
                        "max_weight_lbs": 50,
                    }
                ]
            },
            )
        bands = plan["volume"]["bands"]
        self.assertGreater(_hard_sets(plan), 0)
        self.assertLess(_credit(plan, "side_delts"), bands["side_delts"]["min"])
        self.assertLess(_credit(plan, "rear_delts"), bands["rear_delts"]["min"])
        self.assertLessEqual(_hard_sets(plan), 14)
        self.assertEqual(plan["goals"].get("focus_muscles"), [])

    def test_catalog_programs_an_abs_lift_and_a_gap_stays_gray(self):
        self.assertIn("abs", programmed_groups(default_catalog()))
        bare = {
            "exercises": [
                {
                    "id": "db-flat-press",
                    "name": "DB Flat Press",
                    "session_types": ["push"],
                    "primary_muscles": ["chest"],
                    "volume_primary": ["chest"],
                    "movement": "compound",
                    "equipment": ["dumbbells"],
                    "available": True,
                }
            ]
        }
        self.assertNotIn("abs", programmed_groups(bare))
        report = build_muscle_set_report(
            {"sessions": [], "workout_store": {"catalog": bare, "goals": dict(RAMP)}},
            as_of="2026-11-11",
        )
        abs_row = next(row for row in report["body_map"] if row["id"] == "abs")
        self.assertEqual(abs_row["state"], "gap")
        self.assertIn("no programmed lift", abs_row["detail"])
        self.assertIsNone(abs_row["target"])
        self.assertEqual(coverage_state(0, 4, 4, False), "gap")

    def test_machine_hip_adduction_is_rankable_when_adductors_are_at_zero(self):
        catalog = {
            "exercises": [
                {
                    "id": "leg-press",
                    "name": "Leg Press",
                    "session_types": ["legs"],
                    "primary_muscles": ["quads"],
                    "secondary_muscles": ["glutes"],
                    "volume_primary": ["quads"],
                    "volume_secondary": ["glutes"],
                    "movement": "compound",
                    "equipment": ["leg_press"],
                    "default_sets": 2,
                    "default_reps": 8,
                    "rep_range": [6, 10],
                    "priority": 10,
                    "available": True,
                },
                {
                    "id": "machine-adduction",
                    "name": "Machine Hip Adduction",
                    "session_types": ["legs"],
                    "primary_muscles": ["adductors"],
                    "secondary_muscles": [],
                    "volume_primary": ["adductors"],
                    "volume_secondary": [],
                    "movement": "isolation",
                    "movement_pattern": "hip_adduction",
                    "equipment": ["machine"],
                    "default_sets": 2,
                    "default_reps": 12,
                    "rep_range": [8, 15],
                    "priority": 5,
                    "available": True,
                },
            ]
        }
        plan = generate_workout_plan(
            catalog,
            dict(RAMP),
            [],
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of="2026-11-11",
            equipment={
                "items": [
                    {"id": "machine", "name": "Machine", "tag": "machine", "source": "gym"},
                    {"id": "leg_press", "name": "Leg press", "tag": "leg_press", "source": "gym"},
                ]
            },
        )
        names = [row["name"] for row in plan["exercises"]]
        self.assertIn("Machine Hip Adduction", names)
        self.assertIn("Leg Press", names)
        self.assertLessEqual(_hard_sets(plan), 14)
        self.assertEqual(plan["goals"].get("focus_muscles"), [])
        # The equal ramp does not drop adductors to the maintenance band.
        self.assertGreaterEqual(plan["volume"]["bands"]["adductors"]["max"], 2)
        self.assertNotEqual(plan["volume"]["bands"]["adductors"]["max"], 3)

    def test_back_extension_credits_lower_back_not_mid_back(self):
        credits = credit_sets_for_exercise(
            ["lower_back", "glutes"], ["hamstrings"], 2, secondary_fraction=0.5
        )
        self.assertEqual(credits["lower_back"], 1.0)
        self.assertEqual(credits["glutes"], 1.0)
        self.assertNotIn("mid_upper_back", credits)
        catalog = default_catalog()
        sessions = [_session("2026-11-08", "legs", "Back Extension Machine", 2, reps=12, weight=70)]
        tally = this_week_credits(sessions, catalog, "2026-11-08", 0.5)
        self.assertGreater(tally["lower_back"], 0)
        self.assertEqual(tally.get("mid_upper_back") or 0, 0)

    def test_half_credit_does_not_flip_the_map_border(self):
        self.assertEqual(coverage_state(10.5, 8, 10, True), "on")
        self.assertEqual(coverage_state(10.6, 8, 10, True), "over")
        self.assertEqual(coverage_state(7.5, 8, 10, True), "on")
        self.assertEqual(coverage_state(7.4, 8, 10, True), "under")

    def test_legs_day_keeps_curl_and_calves_and_one_hip_each(self):
        catalog = {
            "exercises": [
                _lift("leg-press", "Leg Press", "legs", ["quads"], ["glutes"], "compound", ["leg_press"], 10),
                _lift("rdl", "RDL", "legs", ["hamstrings", "glutes"], ["lower_back"], "compound", ["barbell"], 9),
                _lift("lying-leg-curls", "Lying Leg Curl", "legs", ["hamstrings"], [], "isolation", ["machine"], 8),
                _lift("calf-raises", "Calf Extensions", "legs", ["calves"], [], "isolation", ["machine"], 7),
                _lift("cable-hip-abduction", "Cable Hip Abduction", "legs", ["abductors"], ["glutes"], "isolation", ["cable"], 6, pattern="hip_abduction"),
                _lift("machine-abduction", "Machine Hip Abduction", "legs", ["abductors"], ["glutes"], "isolation", ["machine"], 5, pattern="hip_abduction"),
                _lift("cable-hip-adduction", "Cable Hip Adduction", "legs", ["adductors"], [], "isolation", ["cable"], 6, pattern="hip_adduction"),
                _lift("machine-adduction", "Machine Hip Adduction", "legs", ["adductors"], [], "isolation", ["machine"], 5, pattern="hip_adduction"),
                _lift("roman-chair-raise", "Roman Chair Knee Raise", "legs", ["abs"], [], "isolation", ["roman_chair"], 4),
            ]
        }
        gear = {
            "items": [
                {"id": "machine", "name": "Machine", "tag": "machine", "source": "gym"},
                {"id": "cable", "name": "Cable", "tag": "cable", "source": "gym"},
                {"id": "barbell", "name": "Barbell", "tag": "barbell", "source": "gym"},
                {"id": "leg_press", "name": "Leg press", "tag": "leg_press", "source": "gym"},
                {"id": "roman_chair", "name": "Roman chair", "tag": "roman_chair", "source": "gym"},
            ]
        }
        plan = generate_workout_plan(
            catalog,
            dict(RAMP),
            [_session("2026-11-10", "push", "Walk", 1)],
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of="2026-11-11",
            equipment=gear,
        )
        names = [row["name"] for row in plan["exercises"]]
        self.assertIn("Lying Leg Curl", names)
        self.assertIn("Calf Extensions", names)
        self.assertIn("Roman Chair Knee Raise", names)
        self.assertGreater(_credit(plan, "hamstrings"), 0)
        self.assertLessEqual(len(names), 6)
        self.assertLessEqual(_hard_sets(plan), 14)
        patterns = [str(row.get("movement_pattern") or "") for row in plan["exercises"]]
        self.assertLessEqual(patterns.count("hip_abduction"), 1)
        self.assertLessEqual(patterns.count("hip_adduction"), 1)
        self.assertGreater(_credit(plan, "calves"), 0)
        self.assertGreater(_credit(plan, "hamstrings"), 0)

    def test_pull_day_covers_rear_delts_without_passing_the_exercise_cap(self):
        catalog = {
            "exercises": [
                _lift("assisted-pullups", "Assisted Pullups", "pull", ["lats"], ["biceps"], "compound", ["pullup"], 10),
                _lift("machine-row", "Machine Row", "pull", ["mid_upper_back"], ["lats"], "compound", ["machine"], 9),
                _lift("hammer-curls", "Hammer Curls", "pull", ["biceps"], [], "isolation", ["dumbbell"], 8),
                _lift("db-curls", "DB Curls", "pull", ["biceps"], [], "isolation", ["dumbbell"], 7),
                _lift("face-pulls", "Face Pulls", "pull", ["rear_delts"], [], "isolation", ["cable"], 4),
            ]
        }
        gear = {
            "items": [
                {"id": "pullup", "name": "Pull-up", "tag": "pullup", "source": "gym"},
                {"id": "machine", "name": "Machine", "tag": "machine", "source": "gym"},
                {"id": "dumbbell", "name": "Dumbbell", "tag": "dumbbell", "source": "gym"},
                {"id": "cable", "name": "Cable", "tag": "cable", "source": "gym"},
            ]
        }
        goals = dict(RAMP)
        goals["exercises_per_session"] = 4
        plan = generate_workout_plan(
            catalog,
            goals,
            [_session("2026-11-10", "push", "Walk", 1)],
            recovery_score=80,
            recovery_label="Ready",
            session_type="pull",
            as_of="2026-11-11",
            equipment=gear,
        )
        names = [row["name"] for row in plan["exercises"]]
        self.assertIn("Face Pulls", names)
        self.assertIn("Assisted Pullups", names)
        self.assertIn("Machine Row", names)
        self.assertLessEqual(names.count("Hammer Curls") + names.count("DB Curls"), 1)
        self.assertLessEqual(len(names), goals["exercises_per_session"])
        self.assertLessEqual(_hard_sets(plan), 14)
        self.assertGreater(_credit(plan, "rear_delts"), 0)

    def test_legs_day_with_abs_under_target_gets_an_abs_lift(self):
        catalog = {
            "exercises": [
                _lift("leg-press", "Leg Press", "legs", ["quads"], ["glutes"], "compound", ["leg_press"], 10),
                _lift("rdl", "RDL", "legs", ["hamstrings", "glutes"], ["lower_back"], "compound", ["barbell"], 9),
                _lift("lying-leg-curls", "Lying Leg Curl", "legs", ["hamstrings"], [], "isolation", ["machine"], 8),
                _lift("calf-raises", "Calf Extensions", "legs", ["calves"], [], "isolation", ["machine"], 7),
                _lift("leg-extension", "Leg Extension", "legs", ["quads"], [], "isolation", ["machine"], 3),
                _lift("hip-thrust", "Hip Thrust", "legs", ["glutes"], ["hamstrings"], "compound", ["machine"], 9),
                _lift("roman-chair-raise", "Roman Chair Knee Raise", "legs", ["abs"], [], "isolation", ["roman_chair"], 4),
            ]
        }
        gear = {
            "items": [
                {"id": "machine", "name": "Machine", "tag": "machine", "source": "gym"},
                {"id": "barbell", "name": "Barbell", "tag": "barbell", "source": "gym"},
                {"id": "leg_press", "name": "Leg press", "tag": "leg_press", "source": "gym"},
                {"id": "roman_chair", "name": "Roman chair", "tag": "roman_chair", "source": "gym"},
            ]
        }
        plan = generate_workout_plan(
            catalog,
            dict(RAMP),
            [_session("2026-11-10", "push", "Walk", 1)],
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of="2026-11-11",
            equipment=gear,
        )
        names = [row["name"] for row in plan["exercises"]]
        self.assertIn("Roman Chair Knee Raise", names)
        self.assertIn("Calf Extensions", names)
        self.assertIn("Lying Leg Curl", names)
        self.assertNotIn("Leg Extension", names)
        self.assertLessEqual(len(names), 6)
        self.assertLessEqual(_hard_sets(plan), 14)
        for row in plan["exercises"]:
            self.assertLessEqual(int((row.get("prescription") or {}).get("sets") or 0), 4)

    def test_default_catalog_keeps_leg_press_and_curl_when_under_target(self):
        catalog = default_catalog()
        self.assertTrue(any(ex.get("id") == "barbell-deadlift" for ex in catalog["exercises"]))
        history = [
            Session(
                date="2026-11-04",
                session_type="legs",
                exercises=[
                    ExerciseEntry(
                        name="Barbell Deadlift",
                        sets=[SetEntry(weight_lbs=185, sets=2, reps=5)],
                    ),
                    ExerciseEntry(
                        name="Seated Leg Curls",
                        sets=[SetEntry(weight_lbs=50, sets=2, reps=12)],
                    ),
                    ExerciseEntry(
                        name="Leg Press",
                        sets=[SetEntry(weight_lbs=200, sets=2, reps=10)],
                    ),
                ],
            ),
            _session("2026-11-10", "legs", "Back Extension Machine", 4, reps=12, weight=40),
        ]
        plan = generate_workout_plan(
            catalog,
            dict(RAMP),
            history,
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of="2026-11-11",
        )
        names = [row["name"] for row in plan["exercises"]]
        bands = plan["volume"]["planning_bands"]
        self.assertIn("Leg Press", names)
        self.assertIn("Lying Leg Curl", names)
        self.assertIn("Roman Chair Knee Raise", names)
        self.assertLessEqual(len(names), 6)
        self.assertLessEqual(_hard_sets(plan), 14)
        quads = _credit(plan, "quads")
        hamstrings = _credit(plan, "hamstrings")
        self.assertLess(quads, bands["quads"]["min"])
        self.assertLess(hamstrings, bands["hamstrings"]["min"])
        curl = next(row for row in plan["exercises"] if row["name"] == "Lying Leg Curl")
        without_curl = hamstrings - float(curl["set_credits"]["hamstrings"])
        self.assertLess(without_curl, bands["hamstrings"]["min"])
        press = next(row for row in plan["exercises"] if row["name"] == "Leg Press")
        self.assertEqual(press["movement"], "compound")
        chair = next(row for row in plan["exercises"] if row["name"] == "Roman Chair Knee Raise")
        self.assertEqual(chair["movement"], "isolation")

    def test_pinned_back_extension_survives_a_full_legs_day(self):
        goals = dict(RAMP)
        goals["pinned_lifts"] = {"legs": ["back-extension"]}
        plan = generate_workout_plan(
            default_catalog(),
            goals,
            [_session("2026-11-10", "pull", "Pulldowns", 2)],
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of="2026-11-11",
        )
        names = [row["name"] for row in plan["exercises"]]
        self.assertIn("Back Extension Machine", names)
        self.assertEqual(len(names), 6)
        self.assertLessEqual(_hard_sets(plan), 14)

    def test_back_extension_stays_while_lower_back_is_under_target(self):
        goals = dict(RAMP)
        goals["default_hard_sets"] = 1
        goals["pinned_lifts"] = {}
        plan = generate_workout_plan(
            default_catalog(),
            goals,
            [_session("2026-11-10", "pull", "Pulldowns", 2)],
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of="2026-11-11",
        )
        names = [row["name"] for row in plan["exercises"]]
        self.assertIn("Back Extension Machine", names)
        lower = _credit(plan, "lower_back")
        self.assertLess(lower, plan["volume"]["planning_bands"]["lower_back"]["min"])
        self.assertLessEqual(len(names), 6)
        self.assertEqual(goals["pinned_lifts"], {})

    def test_a_set_logged_today_does_not_remove_another_lift(self):
        history = [_session("2026-11-10", "pull", "Pulldowns", 2)]
        before = generate_workout_plan(
            default_catalog(),
            dict(RAMP),
            history,
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of="2026-11-11",
        )
        after = generate_workout_plan(
            default_catalog(),
            dict(RAMP),
            history + [_session("2026-11-11", "legs", "Calf Extensions", 1, reps=12, weight=80)],
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of="2026-11-11",
        )
        self.assertIn("Back Extension Machine", [row["name"] for row in before["exercises"]])
        self.assertIn("Back Extension Machine", [row["name"] for row in after["exercises"]])
        self.assertNotIn("Did not add", after["message"])
        for note in after["held_compounds"]:
            self.assertIn("Did not add", note)
        self.assertLessEqual(len(after["exercises"]), 6)
        self.assertLessEqual(_hard_sets(after), 14)

    def test_logged_calf_set_keeps_the_morning_list_minus_that_lift(self):
        history = [_session("2026-11-10", "pull", "Pulldowns", 2)]
        morning = generate_workout_plan(
            default_catalog(),
            dict(RAMP),
            history,
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of="2026-11-11",
        )
        after = generate_workout_plan(
            default_catalog(),
            dict(RAMP),
            history + [_session("2026-11-11", "legs", "Calf Extensions", 1, reps=12, weight=80)],
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of="2026-11-11",
        )
        morning_names = [row["name"] for row in morning["exercises"]]
        after_names = [row["name"] for row in after["exercises"]]
        self.assertIn("Calf Extensions", morning_names)
        self.assertIn("Leg Press", morning_names)
        self.assertEqual(
            after_names,
            [name for name in morning_names if name != "Calf Extensions"],
        )
        self.assertNotIn("DB Calf Raises", after_names)
        self.assertIn("Leg Press", after_names)
        self.assertLessEqual(_hard_sets(after), 14)
        for row in after["exercises"]:
            self.assertLessEqual(int((row.get("prescription") or {}).get("sets") or 0), 4)

    def test_default_legs_day_is_the_six_lifts(self):
        plan = generate_workout_plan(
            default_catalog(),
            dict(RAMP),
            [_session("2026-11-10", "pull", "Pulldowns", 2)],
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of="2026-11-11",
        )
        names = [row["name"] for row in plan["exercises"]]
        self.assertEqual(
            names,
            [
                "Trap-Bar Deadlift",
                "Leg Press",
                "RDL",
                "Lying Leg Curl",
                "Calf Extensions",
                "Back Extension Machine",
            ],
        )
        self.assertEqual(
            [row["movement"] for row in plan["exercises"][:3]],
            ["compound", "compound", "compound"],
        )
        self.assertLessEqual(_hard_sets(plan), 14)
        for row in plan["exercises"]:
            self.assertLessEqual(int(row["prescription"]["sets"]), 4)
        push = generate_workout_plan(
            default_catalog(),
            dict(RAMP),
            [_session("2026-11-10", "pull", "Pulldowns", 2)],
            recovery_score=80,
            recovery_label="Ready",
            session_type="push",
            as_of="2026-11-11",
        )
        self.assertEqual(len(push["exercises"]), RAMP["exercises_per_session"])

    def test_did_not_add_note_stays_out_of_served_messages(self):
        from rt_dashboard.agent_plan import overlay_workout_on_payload, stamp_and_fill_workout
        from rt_dashboard.agent_read import build_plan
        from rt_dashboard.coach import build_today_board
        from rt_dashboard.models import RecoveryStatus
        from rt_dashboard.priority_ramp import present_served_workout

        note = (
            "Did not add Leg Press: a set is already logged today, "
            "so the remaining lifts stay."
        )
        message = f"Suggested LEGS session (5 exercises, 8 hard sets). · {note}"
        saved = {
            "message": message,
            "date": "2026-11-11",
            "session_type": "legs",
            "is_rest_day": False,
            "exercises": [{"name": "RDL", "prescription": {"sets": 2}}],
        }
        direct = present_served_workout(
            dict(saved),
            goals=dict(RAMP),
            sessions=[],
            as_of="2026-11-11",
            recovery={"label": "Ready"},
        )
        bare = present_served_workout({"message": note, "date": "2026-11-11"})
        slot = {
            "session_type": "legs",
            "is_rest_day": False,
            "exercises": [],
            "next_session_type": "push",
        }
        with mock.patch(
            "rt_dashboard.agent_plan.load_last_good_workout_plan",
            return_value=dict(saved),
        ), mock.patch(
            "rt_dashboard.grok_planner.dashboard_plan_slots",
            return_value=({}, dict(slot)),
        ):
            stamped = stamp_and_fill_workout(
                "u",
                day="2026-11-11",
                sessions=[],
                goals=dict(RAMP),
                recovery={"label": "Ready", "score": 80},
                read_only=True,
            )
        payload = {
            "meta": {"local_today": "2026-11-11"},
            "coach": {"today": {"date": "2026-11-11"}},
            "sessions": [],
            "workout_store": {"goals": dict(RAMP), "plan": dict(saved)},
            "recovery": {"label": "Ready", "score": 80},
        }
        overlaid = overlay_workout_on_payload(payload, dict(saved))
        exported = export_agent_today(payload)
        plan_body, _as_of = build_plan(payload, {"today": {"date": "2026-11-11"}, "week": {}})
        board = build_today_board(
            as_of="2026-11-11",
            recovery=RecoveryStatus(label="Ready", score=80.0),
            workout_plan=dict(saved, goals=dict(RAMP)),
            meal_plan={"message": "ok", "items": [], "meals": []},
            consumed={"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
            targets={"calories": 2100, "protein_g": 180, "carbs_g": 180, "fat_g": 60},
            adherence={},
        )
        served = [
            direct.get("message"),
            bare.get("message"),
            stamped.get("message"),
            overlaid["workout"]["message"],
            overlaid["workout_store"]["plan"]["message"],
            overlaid["coach"]["today"]["workout"]["message"],
            exported["today"]["workout"]["message"],
            plan_body["week"]["workout_plan"]["message"],
            board["workout"]["message"],
        ]
        for text in served:
            self.assertNotIn("Did not add", text or "")

    def test_behind_the_neck_press_stays_at_four_sets(self):
        goals = dict(RAMP)
        goals["pinned_lifts"] = {"push": ["smith-btn-press"]}
        plan = generate_workout_plan(
            default_catalog(),
            goals,
            [
                _session("2026-11-04", "push", "Behind-the-Neck Smith Press", 4, reps=8, weight=60),
                _session("2026-11-10", "pull", "Pulldowns", 2),
            ],
            recovery_score=80,
            recovery_label="Ready",
            session_type="push",
            as_of="2026-11-11",
        )
        btn = next(row for row in plan["exercises"] if row["name"] == "Behind-the-Neck Smith Press")
        self.assertLessEqual(int(btn["prescription"]["sets"]), 4)
        self.assertLessEqual(len(plan["exercises"]), goals["exercises_per_session"])
        self.assertLessEqual(_hard_sets(plan), 14)
        for row in plan["exercises"]:
            self.assertLessEqual(int((row.get("prescription") or {}).get("sets") or 0), 4)

    def test_zero_group_gets_a_one_set_coverage_slot(self):
        catalog = {
            "exercises": [
                _lift("leg-press", "Leg Press", "legs", ["quads"], ["glutes"], "compound", ["leg_press"], 10),
                _lift("machine-adduction", "Machine Hip Adduction", "legs", ["adductors"], [], "isolation", ["machine"], 5, pattern="hip_adduction"),
            ]
        }
        plan = generate_workout_plan(
            catalog,
            dict(RAMP),
            [],
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of="2026-11-11",
            equipment={
                "items": [
                    {"id": "machine", "name": "Machine", "tag": "machine", "source": "gym"},
                    {"id": "leg_press", "name": "Leg press", "tag": "leg_press", "source": "gym"},
                ]
            },
        )
        adduction = next(row for row in plan["exercises"] if row["name"] == "Machine Hip Adduction")
        self.assertEqual(int(adduction["prescription"]["sets"]), 1)
        self.assertLessEqual(len(plan["exercises"]), RAMP["exercises_per_session"])
        self.assertLessEqual(_hard_sets(plan), 14)

    def test_custom_lats_upper_back_credits_on_planner_and_tile(self):
        """A Turso custom row can still say lats_upper_back. Credit must see it.

        No database write. The row is merged in memory the same way a
        custom id replaces the catalog row.
        """
        base = {
            "exercises": [
                _lift(
                    "barbell-deadlift",
                    "Barbell Deadlift",
                    "legs",
                    ["glutes", "hamstrings", "lower_back"],
                    ["quads", "lats", "mid_upper_back", "traps"],
                    "compound",
                    ["barbell"],
                    6,
                )
            ]
        }
        custom = {
            "exercises": [
                {
                    "id": "barbell-deadlift",
                    "name": "Barbell Deadlift",
                    "session_types": ["legs"],
                    "primary_muscles": ["glutes", "hamstrings", "lower_back"],
                    "secondary_muscles": ["quads", "lats_upper_back", "traps", "forearms"],
                    "volume_primary": ["glutes", "hamstrings", "lower_back"],
                    "volume_secondary": ["quads", "lats_upper_back", "traps"],
                    "movement": "compound",
                    "equipment": ["barbell"],
                    "available": True,
                    "default_sets": 1,
                    "default_reps": 5,
                    "rep_range": [5, 8],
                    "priority": 6,
                },
                {
                    "id": "smith-barbell-row",
                    "name": "Smith Barbell Row",
                    "session_types": ["pull"],
                    "primary_muscles": ["mid_upper_back"],
                    "secondary_muscles": ["biceps"],
                    "volume_primary": ["mid_upper_back"],
                    "volume_secondary": ["biceps"],
                    "movement": "compound",
                    "equipment": ["smith_machine"],
                    "available": True,
                    "default_sets": 1,
                    "default_reps": 8,
                    "rep_range": [8, 12],
                    "priority": 8,
                },
            ]
        }
        merged = merge_custom_universe(base, custom)
        dead = next(ex for ex in merged["exercises"] if ex["id"] == "barbell-deadlift")
        self.assertIn("lats_upper_back", dead["secondary_muscles"])
        self.assertEqual(
            expand_planner_muscles(dead["secondary_muscles"]),
            ["quads", "lats", "mid_upper_back", "traps", "biceps"],
        )
        sessions = [_session("2026-11-08", "legs", "Barbell Deadlift", 2, reps=5, weight=185)]
        planner = this_week_credits(sessions, merged, "2026-11-08", 0.5)
        self.assertGreater(planner["lats"], 0)
        self.assertGreater(planner["mid_upper_back"], 0)
        self.assertIn("mid_upper_back", programmed_groups(merged) | set(planner))
        report = build_muscle_set_report(
            {
                "sessions": [
                    {
                        "date": "2026-11-08",
                        "session_type": "legs",
                        "exercises": [
                            {
                                "name": "Barbell Deadlift",
                                "sets": [{"weight_lbs": 185, "sets": 2, "reps": 5}],
                            }
                        ],
                    }
                ],
                "workout_store": {"catalog": merged, "goals": dict(RAMP)},
            },
            as_of="2026-11-08",
        )
        self.assertGreater(report["muscle_sets"]["lats"]["proper_sets"], 0)
        self.assertGreater(report["muscle_sets"]["mid_upper_back"]["proper_sets"], 0)
        lats_region = next(row for row in report["body_map"] if row["id"] == "lats")
        upper = next(row for row in report["body_map"] if row["id"] == "mid_upper_back")
        self.assertNotEqual(lats_region["state"], "gap")
        self.assertNotEqual(upper["state"], "gap")
        self.assertGreater(lats_region["sets"], 0)
        self.assertGreater(upper["sets"], 0)
        row_plan = this_week_credits(
            [_session("2026-11-08", "pull", "Smith Barbell Row", 3, reps=8, weight=95)],
            merged,
            "2026-11-08",
            0.5,
        )
        self.assertGreater(row_plan["mid_upper_back"], 0)


class BodyMapPlacementTests(unittest.TestCase):
    def test_map_is_on_the_trends_muscle_card_only(self):
        card = HTML[HTML.find('id="muscle-sets-card"') : HTML.find('id="sleep-trend-card"')]
        self.assertIn('data-m-panel="trends"', card)
        self.assertIn('id="muscle-body-map"', card)
        self.assertIn('data-muscle="side_delts"', card)
        self.assertIn('data-muscle="adductors"', card)
        self.assertIn('data-muscle="lower_back"', card)
        today = HTML[HTML.find('id="today-hub"') : HTML.find('id="muscle-sets-card"')]
        self.assertNotIn("muscle-body-map", today)
        self.assertNotIn("data-muscle=", today)
        self.assertNotIn("muscle-sets-list", today)

    def test_wide_layout_puts_the_list_beside_the_figures(self):
        card = HTML[HTML.find('id="muscle-sets-card"') : HTML.find('id="sleep-trend-card"')]
        map_at = card.find('id="muscle-body-map"')
        list_at = card.find('id="muscle-sets-list"')
        warn_at = card.find('id="muscle-sets-warning"')
        self.assertGreater(map_at, -1)
        self.assertLess(map_at, list_at)
        self.assertLess(list_at, warn_at)
        inside = card[map_at:list_at]
        self.assertLess(inside.find("muscle-body-map-legend"), inside.find("muscle-body-map-figures"))
        figures = inside[inside.find("muscle-body-map-figures") :]
        self.assertIn("muscle-body-map-views", figures)
        self.assertIn('id="muscle-map-detail"', figures)
        self.assertIn("Tap a muscle", figures)
        self.assertNotIn("display:none", card)
        self.assertNotIn("display: none", card)

        wide = _media_block(CSS, "#muscle-sets-list {\n    grid-column: 2;")
        self.assertIn("min-width: 720px", wide)
        self.assertIn("display: grid", wide)
        self.assertIn("grid-template-columns: minmax(0, 420px) minmax(0, 1fr)", wide)
        self.assertIn("align-items: start", wide)
        self.assertIn(".muscle-body-map-legend { grid-column: 1; grid-row: 1;", wide)
        self.assertIn(".muscle-body-map-figures { grid-column: 1; grid-row: 2;", wide)
        self.assertIn("grid-row: 2", wide)
        self.assertIn("margin-top: 0", wide)
        self.assertNotIn("display: none", wide)
        # Front/back stay side by side inside the left column. The section
        # itself is not a two-column grid until the 720px query.
        narrow = CSS[: CSS.find("@media (min-width: 720px)")]
        self.assertIn(".muscle-body-map { margin-top: 0.75rem; }", narrow)
        self.assertNotIn("grid-template-columns: minmax(0, 420px)", narrow)
        self.assertIn("grid-template-columns: 1fr 1fr", narrow)
        self.assertIn("#muscle-sets-list { margin-top: 0.6rem; }", narrow)

    def test_list_dots_use_the_map_fill(self):
        color = APP[APP.find("function muscleMapColor") : APP.find("function paintMuscleBodyMap")]
        paint = APP[APP.find("function paintMuscleBodyMap") : APP.find("function renderMuscleSets")]
        listed = APP[APP.find("function renderMuscleSets") : APP.find("function phaseBaroAct")]
        for fill in ("#f0b429", "#5ce1a8", "#f07178", "#5c6b82"):
            self.assertIn(fill, color)
            self.assertIn(fill, CSS)
        self.assertIn("muscleMapColor(row)", paint)
        self.assertIn("color.fill", paint)
        self.assertIn("map-\" + color.state", paint)
        self.assertIn("report.body_map", listed)
        self.assertIn("muscleMapColor(mapById[id])", listed)
        self.assertIn("muscle-map-dot map-", listed)
        self.assertNotIn("tierColor", listed)
        self.assertNotIn("#3b82f6", listed)
        self.assertNotIn("guardrail-dot", listed)
        self.assertNotIn("row.tier", listed)
        node = shutil.which("node")
        if not node:
            self.skipTest("node missing")
        proc = subprocess.run(
            [node, str(ROOT / "tests" / "muscle_map_colors.js")],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


def _media_block(css: str, needle: str) -> str:
    start = css.find(needle)
    if start < 0:
        raise AssertionError("missing " + needle)
    media = css.rfind("@media", 0, start)
    brace = css.find("{", media)
    depth = 0
    for i in range(brace, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[media : i + 1]
    raise AssertionError("unclosed media query")


class TodayPlanAgreementTests(unittest.TestCase):
    def test_agent_today_matches_the_dashboard_session(self):
        from server import _overlay_agent_workout

        rows = [
            {"name": "Assisted Pullups", "sets": 2, "movement": "compound", "prescription": {"sets": 2, "reps": 8}},
            {"name": "Machine Row", "sets": 2, "movement": "compound", "prescription": {"sets": 2, "reps": 10}},
            {"name": "Hammer Curls", "sets": 1, "movement": "isolation", "prescription": {"sets": 1, "reps": 12}},
            {"name": "DB Curls", "sets": 1, "movement": "isolation", "prescription": {"sets": 1, "reps": 12}},
            {"name": "Back Extension Machine", "sets": 1, "movement": "isolation", "prescription": {"sets": 1, "reps": 12}},
        ]
        plan = {
            "session_type": "pull",
            "is_rest_day": False,
            "message": "Suggested PULL session.",
            "exercises": rows,
            "date": "2026-11-11",
        }
        short = {
            "session_type": "pull",
            "is_rest_day": False,
            "source": "grok",
            "message": "Suggested PULL session.",
            "exercises": rows[:2],
            "date": "2026-11-11",
        }
        payload = {
            "meta": {"local_today": "2026-11-11"},
            "coach": {"today": {"date": "2026-11-11", "workout": {"session_type": "pull", "exercises": list(rows)}}},
            "workout_store": {"plan": plan, "goals": {"rotation": ["push", "pull", "legs"]}},
            "sessions": [],
            "workout": {},
        }

        def pairs(exercises):
            out = []
            for ex in exercises:
                rx = ex.get("prescription") if isinstance(ex.get("prescription"), dict) else {}
                sets = ex.get("sets") if ex.get("sets") is not None else rx.get("sets")
                out.append((ex["name"], int(sets)))
            return out

        with mock.patch(
            "rt_dashboard.agent_plan.stamp_and_fill_workout",
            return_value=short,
        ):
            served = _overlay_agent_workout(payload, "user-1", read_only=True)
        exported = export_agent_today(served)
        dash = pairs(plan["exercises"])
        self.assertEqual(pairs(served["workout_store"]["plan"]["exercises"]), dash)
        self.assertEqual(pairs(served["coach"]["today"]["workout"]["exercises"]), dash)
        self.assertEqual(pairs(exported["today"]["workout"]["plan_exercises"]), dash)
        self.assertEqual(len(dash), 5)
        self.assertEqual(sum(sets for _name, sets in dash), 7)
        self.assertEqual(served["workout_store"]["plan"]["source"], "local")
        self.assertNotEqual(served["workout_store"]["plan"]["source"], "grok")

    def test_wrong_letter_keeps_the_saved_plan(self):
        rendered = {
            "date": "2026-11-11",
            "session_type": "legs",
            "exercises": [{"name": "Leg Press", "sets": 2}, {"name": "RDL", "sets": 2}],
            "source": "planner",
        }
        served = {
            "date": "2026-11-11",
            "session_type": "pull",
            "exercises": [{"name": "Assisted Pullups", "sets": 2}],
            "source": "grok",
        }
        kept = prefer_rendered_session(rendered, served, today="2026-11-11", sessions=[])
        self.assertEqual(kept["source"], "grok")
        self.assertEqual(kept["exercises"][0]["name"], "Assisted Pullups")

    def test_equal_length_still_swaps_when_date_and_letter_match(self):
        rendered = {
            "date": "2026-11-11",
            "session_type": "pull",
            "exercises": [
                {"name": "Assisted Pullups", "sets": 2},
                {"name": "Machine Row", "sets": 2},
            ],
            "source": "planner",
        }
        served = {
            "date": "2026-11-11",
            "session_type": "pull",
            "exercises": [
                {"name": "Pulldowns", "sets": 2},
                {"name": "Seated Cable Row", "sets": 2},
            ],
            "source": "grok",
        }
        kept = prefer_rendered_session(rendered, served, today="2026-11-11", sessions=[])
        self.assertEqual(kept["source"], "local")
        self.assertEqual(
            [ex["name"] for ex in kept["exercises"]],
            ["Assisted Pullups", "Machine Row"],
        )

    def test_logged_set_today_does_not_swap_mid_session(self):
        rendered = {
            "date": "2026-11-11",
            "session_type": "pull",
            "exercises": [
                {"name": "Assisted Pullups", "sets": 2},
                {"name": "Machine Row", "sets": 2},
                {"name": "DB Curls", "sets": 1},
            ],
        }
        served = {
            "date": "2026-11-11",
            "session_type": "pull",
            "exercises": [{"name": "Assisted Pullups", "sets": 2}],
            "source": "grok",
        }
        sessions = [
            {
                "date": "2026-11-11",
                "session_type": "pull",
                "exercises": [
                    {"name": "Assisted Pullups", "sets": [{"weight_lbs": 40, "sets": 1, "reps": 8}]}
                ],
            }
        ]
        kept = prefer_rendered_session(
            rendered, served, today="2026-11-11", sessions=sessions
        )
        self.assertEqual(kept["source"], "grok")
        self.assertEqual(len(kept["exercises"]), 1)

    def test_empty_saved_plan_does_not_replace_a_fail_loud_slot(self):
        rendered = {
            "session_type": "push",
            "exercises": [{"name": "Canned Bench", "sets": 2}],
        }
        served = {
            "session_type": "push",
            "exercises": [],
            "generate_error": "Connect SuperGrok",
        }
        kept = prefer_rendered_session(rendered, served)
        self.assertEqual(kept["exercises"], [])


if __name__ == "__main__":
    unittest.main()
