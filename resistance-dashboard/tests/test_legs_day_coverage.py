"""Legs-day coverage: one hinge, quad isolation, hips, abs twice a week."""

from __future__ import annotations

import copy
import unittest

from rt_dashboard.catalog_guard import guard_plan
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import (
    default_catalog,
    generate_workout_plan,
    progression_band,
)

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

TARGET = [
    ("Leg Press", 2),
    ("RDL", 2),
    ("Leg Extension Machine", 2),
    ("Seated Leg Curls", 2),
    ("Machine Hip Adduction", 1),
    ("Machine Hip Abduction", 1),
    ("Calf Extensions", 2),
    ("Ab Crunch Machine", 2),
]
HINGES = {"rdl", "barbell-deadlift", "trap-bar-deadlift"}
NEW_IDS = ("leg-extension", "ab-crunch-machine", "cable-crunch", "back-extension-45")


def _session(day, kind, name, sets=2, weight=100, reps=8):
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


def _shape(plan):
    return [
        (row["name"], int(row["prescription"]["sets"]))
        for row in plan["exercises"]
    ]


def _planned(plan, muscle):
    for row in plan["volume"]["muscles"]:
        if row["muscle"] == muscle:
            return row["planned"]
    raise AssertionError(muscle)


class LegsDayCoverage(unittest.TestCase):
    def setUp(self):
        self.catalog = default_catalog()
        # A pull the day before keeps continuity normal so the 14-set cap is live.
        self.history = [_session("2026-11-10", "pull", "Pulldowns")]

    def _legs(self, catalog=None, goals=None, history=None, day="2026-11-11"):
        return generate_workout_plan(
            catalog if catalog is not None else self.catalog,
            dict(goals or RAMP),
            list(self.history if history is None else history),
            recovery_score=80,
            recovery_label="Ready",
            session_type="legs",
            as_of=day,
        )

    def test_target_legs_shape_fits_the_cap(self):
        plan = self._legs()
        shape = _shape(plan)
        self.assertEqual(shape, TARGET)
        self.assertLessEqual(sum(sets for _, sets in shape), 14)
        self.assertEqual([row["movement"] for row in plan["exercises"][:2]], ["compound", "compound"])
        hinges = [row["id"] for row in plan["exercises"] if row["id"] in HINGES]
        self.assertEqual(hinges, ["rdl"])
        self.assertNotIn("Barbell Deadlift", [name for name, _ in shape])
        self.assertNotIn("Trap-Bar Deadlift", [name for name, _ in shape])
        self.assertNotIn("Back Extension Machine", [name for name, _ in shape])
        self.assertEqual(_planned(plan, "adductors"), 1.0)
        self.assertEqual(_planned(plan, "abductors"), 1.0)
        self.assertEqual(_planned(plan, "abs"), 2.0)
        self.assertGreaterEqual(_planned(plan, "quads"), 2.0)

    def test_hack_squat_replaces_leg_press_when_available(self):
        catalog = copy.deepcopy(self.catalog)
        for row in catalog["exercises"]:
            if row["id"] == "hack-squat":
                row["available"] = True
        plan = self._legs(catalog)
        shape = _shape(plan)
        self.assertEqual(shape[0], ("Hack Squat", 2))
        self.assertNotIn("Leg Press", [name for name, _ in shape])
        self.assertEqual(shape[1:], TARGET[1:])
        self.assertLessEqual(sum(sets for _, sets in shape), 14)

    def test_never_programs_deadlift_and_rdl_together(self):
        plan = self._legs()
        names = {row["name"] for row in plan["exercises"]}
        self.assertIn("RDL", names)
        self.assertFalse({"Barbell Deadlift", "Trap-Bar Deadlift"} & names)
        # Both hinges available and no RDL: still one hinge.
        catalog = {
            "exercises": [
                row for row in self.catalog["exercises"]
                if row["id"] in ("barbell-deadlift", "trap-bar-deadlift", "leg-press", "seated-leg-curls")
            ]
        }
        for row in catalog["exercises"]:
            row["available"] = True
        bare = self._legs(catalog)
        hinges = [row["id"] for row in bare["exercises"] if row["id"] in HINGES]
        self.assertEqual(len(hinges), 1, [row["name"] for row in bare["exercises"]])
        self.assertNotIn("rdl", hinges)

    def test_back_extension_only_while_the_hinge_stays_low(self):
        # A 2-set RDL leaves no back-extension slot. The target plan covers that.
        full = dict(_shape(self._legs()))
        self.assertGreaterEqual(full["RDL"], 2)
        self.assertNotIn("Back Extension Machine", full)
        # No primary hinge in the pool: hinge sets stay at 0, so the slot opens.
        kept = {
            "leg-press", "seated-leg-curls", "calf-raises",
            "back-extension", "back-extension-45",
        }
        catalog = {
            "exercises": [
                row for row in self.catalog["exercises"] if row["id"] in kept
            ]
        }
        goals = dict(RAMP)
        goals["volume_ramp_equal"] = False
        low = self._legs(catalog, goals)
        names = [row["name"] for row in low["exercises"]]
        self.assertNotIn("RDL", names)
        self.assertIn("Back Extension Machine", names)
        self.assertNotIn("45-Degree Back Extension", names)
        self.assertLessEqual(sum(sets for _, sets in _shape(low)), 14)
        for row in catalog["exercises"]:
            if row["id"] == "back-extension":
                row["available"] = False
        swapped = self._legs(catalog, goals)
        swapped_names = [row["name"] for row in swapped["exercises"]]
        self.assertIn("45-Degree Back Extension", swapped_names)
        self.assertNotIn("Back Extension Machine", swapped_names)

    def test_adductor_and_abductor_when_the_cap_has_room(self):
        plan = self._legs()
        by_pattern = {}
        for row in plan["exercises"]:
            pattern = str(row.get("movement_pattern") or "")
            if pattern in ("hip_adduction", "hip_abduction"):
                by_pattern[pattern] = row
        self.assertEqual(int(by_pattern["hip_adduction"]["prescription"]["sets"]), 1)
        self.assertEqual(int(by_pattern["hip_abduction"]["prescription"]["sets"]), 1)
        self.assertLessEqual(sum(sets for _, sets in _shape(plan)), 14)

    def test_abs_twice_a_week_two_sets_crunch_machine_primary(self):
        goals = dict(RAMP)
        history = [_session("2026-11-08", "pull", "Pulldowns")]
        days = [
            ("2026-11-09", "push"),
            ("2026-11-10", "pull"),
            ("2026-11-11", "legs"),
            ("2026-11-12", "push"),
            ("2026-11-13", "pull"),
        ]
        abs_days = []
        for day, letter in days:
            plan = generate_workout_plan(
                self.catalog,
                goals,
                history,
                recovery_score=80,
                recovery_label="Ready",
                session_type=letter,
                as_of=day,
            )
            abs_rows = [
                row for row in plan["exercises"]
                if "abs" in (row.get("primary_muscles") or [])
            ]
            self.assertLessEqual(
                sum(int(row["prescription"]["sets"]) for row in plan["exercises"]),
                14,
            )
            if abs_rows:
                abs_days.append((day, letter, abs_rows))
            logged = []
            for row in plan["exercises"][:1] + abs_rows:
                logged.append(ExerciseEntry(
                    name=row["name"],
                    sets=[SetEntry(
                        weight_lbs=100,
                        sets=int(row["prescription"]["sets"]),
                        reps=8,
                    )],
                ))
            history.append(Session(date=day, session_type=letter, exercises=logged))
        self.assertEqual([item[1] for item in abs_days], ["push", "legs"])
        for _day, _letter, rows in abs_days:
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name"], "Ab Crunch Machine")
            self.assertEqual(int(rows[0]["prescription"]["sets"]), 2)

    def test_abs_falls_back_to_cable_then_roman_chair(self):
        def with_unavailable(*ids):
            catalog = copy.deepcopy(self.catalog)
            for row in catalog["exercises"]:
                if row["id"] in ids:
                    row["available"] = False
            return catalog

        cable = self._legs(with_unavailable("ab-crunch-machine"))
        self.assertIn("Cable Crunch", [row["name"] for row in cable["exercises"]])
        self.assertNotIn("Ab Crunch Machine", [row["name"] for row in cable["exercises"]])
        crunch = next(row for row in cable["exercises"] if row["name"] == "Cable Crunch")
        self.assertEqual(int(crunch["prescription"]["sets"]), 2)

        chair = self._legs(with_unavailable("ab-crunch-machine", "cable-crunch"))
        names = [row["name"] for row in chair["exercises"]]
        self.assertIn("Roman Chair Knee Raise", names)
        self.assertNotIn("Ab Crunch Machine", names)
        self.assertNotIn("Cable Crunch", names)
        roman = next(row for row in chair["exercises"] if row["name"] == "Roman Chair Knee Raise")
        self.assertEqual(int(roman["prescription"]["sets"]), 2)

    def test_new_rows_have_no_starting_load_and_lock_10_15(self):
        by_id = {row["id"]: row for row in self.catalog["exercises"]}
        for eid in NEW_IDS:
            row = by_id[eid]
            self.assertIs(row["starting_load"], False, eid)
            self.assertEqual(row["rep_range"], [10, 15], eid)
            self.assertEqual(row["movement"], "isolation", eid)
            self.assertTrue(row["available"], eid)
            self.assertEqual(progression_band(row), (10, 15), eid)
            guarded = guard_plan(
                {
                    "exercises": [{
                        "id": eid,
                        "name": row["name"],
                        "weight_lbs": None,
                        "prescription": {"weight_lbs": None, "sets": 2, "reps": 12},
                    }]
                },
                self.catalog,
                [],
                as_of="2026-11-17",
            )
            out = guarded["exercises"][0]
            self.assertIsNone(out.get("weight_lbs"), eid)
            self.assertIsNone(out["prescription"].get("weight_lbs"), eid)
            self.assertEqual(out.get("load_source"), "unlogged", eid)
            self.assertNotIn("weight_text", out)
        self.assertEqual(by_id["leg-extension"]["primary_muscles"], ["quads"])
        self.assertEqual(by_id["leg-extension"]["equipment"], ["machine"])
        self.assertEqual(by_id["ab-crunch-machine"]["primary_muscles"], ["abs"])
        self.assertEqual(by_id["ab-crunch-machine"]["equipment"], ["machine"])
        self.assertEqual(by_id["cable-crunch"]["equipment"], ["cable"])
        self.assertEqual(by_id["back-extension-45"]["primary_muscles"], ["lower_back", "glutes"])
        self.assertEqual(by_id["back-extension-45"]["secondary_muscles"], ["hamstrings"])
        self.assertEqual(by_id["back-extension-45"]["equipment"], ["barbell"])
        plan = self._legs()
        for name in ("Leg Extension Machine", "Ab Crunch Machine"):
            row = next(item for item in plan["exercises"] if item["name"] == name)
            self.assertIsNone(row["prescription"]["weight_lbs"], name)
            self.assertIsNone(row.get("load"), name)
            self.assertEqual(row.get("rep_range"), "10-15", name)
