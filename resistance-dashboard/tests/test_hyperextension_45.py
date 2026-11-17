"""Free-weight 45-degree hyperextension shares the back-extension slot.

The machine row stays. The new row is a library lift (available, not a
suggestion). Legs keeps six exercises and pull stays at or under five.
The calf / press pattern family rotates which station fills the slot.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from rt_dashboard.library_groom import suggest_library_additions
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.muscle_sets import VOLUME_GROUPS, build_muscle_set_report
from rt_dashboard.workout_planner import (
    BACK_EXTENSION_FAMILY,
    NAME_ALIASES,
    credit_sets_for_exercise,
    default_catalog,
    generate_workout_plan,
    movement_feasible,
    pattern_family,
)

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent

HINGE_IDS = ("back-extension", "45-degree-hyperextension")

# Live scoreboard. Lats and upper back stay separate. monday_to_date
# is still the Monday-week key beside the rolling proper_sets count.
LIVE_GROUPS = (
    "chest",
    "front_delts",
    "side_delts",
    "rear_delts",
    "lats",
    "mid_upper_back",
    "traps",
    "biceps",
    "triceps",
    "quads",
    "hamstrings",
    "glutes",
    "calves",
    "abs",
    "lower_back",
    "adductors",
    "abductors",
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


def _session(day, kind, name, sets=2, reps=12, weight=40):
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


def _plan(catalog, sessions, letter, day="2026-11-11"):
    return generate_workout_plan(
        catalog,
        dict(RAMP),
        sessions,
        recovery_score=80,
        recovery_label="Ready",
        session_type=letter,
        as_of=day,
    )


def _hinge_ids(plan):
    return [row["id"] for row in plan["exercises"] if row.get("id") in HINGE_IDS]


def _hard_sets(plan):
    return sum(
        int((row.get("prescription") or {}).get("sets") or 0)
        for row in plan.get("exercises") or []
    )


def _compounds_before_isolations(plan):
    seen_isolation = False
    for row in plan["exercises"]:
        if row.get("movement") == "compound":
            if seen_isolation:
                return False
        else:
            seen_isolation = True
    return True


class HyperextensionCatalogTests(unittest.TestCase):
    def test_row_is_available_lower_back_on_legs_and_pull(self):
        bundled = ROOT / "fitness" / "exercises" / "catalog.json"
        root = REPO / "fitness" / "exercises" / "catalog.json"
        self.assertEqual(bundled.read_text(encoding="utf-8"), root.read_text(encoding="utf-8"))
        catalog = default_catalog()
        by_id = {ex["id"]: ex for ex in catalog["exercises"]}
        row = by_id["45-degree-hyperextension"]
        machine = by_id["back-extension"]
        self.assertTrue(row["available"])
        self.assertTrue(machine["available"])
        self.assertEqual(row["name"], "45-Degree Hyperextension")
        self.assertNotEqual(row["name"], machine["name"])
        self.assertEqual(row["primary_muscles"][0], "lower_back")
        self.assertIn("lower_back", row["volume_primary"])
        self.assertEqual(row["session_types"], ["legs", "pull"])
        self.assertEqual(row["movement"], "isolation")
        self.assertEqual(row["equipment_any"], ["barbell", "dumbbells"])
        self.assertEqual(
            pattern_family(row),
            pattern_family(machine),
        )
        self.assertEqual(pattern_family(row), BACK_EXTENSION_FAMILY)
        self.assertEqual(NAME_ALIASES["45 degree hyperextension"], "45-degree-hyperextension")
        self.assertEqual(NAME_ALIASES["back extension machine"], "back-extension")
        suggestions = suggest_library_additions(
            catalog,
            {"items": [{"id": "dumbbells", "tag": "dumbbells", "name": "Dumbbells"}]},
        )
        suggested = {item["id"] for item in suggestions["suggestions"]}
        self.assertNotIn("45-degree-hyperextension", suggested)
        self.assertNotIn("back-extension", suggested)

    def test_plate_or_dumbbell_can_load_it(self):
        catalog = default_catalog()
        row = next(ex for ex in catalog["exercises"] if ex["id"] == "45-degree-hyperextension")
        dumbbells = {"items": [{"id": "dumbbells", "tag": "dumbbells", "name": "Dumbbells"}]}
        plates = {"items": [{"id": "barbell", "tag": "barbell", "name": "Barbell"}]}
        neither = {"items": [{"id": "machine", "tag": "machine", "name": "Machine"}]}
        self.assertTrue(movement_feasible(row, dumbbells))
        self.assertTrue(movement_feasible(row, plates))
        self.assertFalse(movement_feasible(row, neither))

    def test_credits_lower_back_not_upper_back(self):
        catalog = default_catalog()
        row = next(ex for ex in catalog["exercises"] if ex["id"] == "45-degree-hyperextension")
        credits = credit_sets_for_exercise(
            row["primary_muscles"],
            row["secondary_muscles"],
            2,
            secondary_fraction=0.5,
        )
        self.assertGreater(credits["lower_back"], 0)
        self.assertNotIn("mid_upper_back", credits)
        self.assertNotIn("lats", credits)


class HyperextensionRotationTests(unittest.TestCase):
    def test_repeated_default_plans_do_not_always_pick_the_machine(self):
        catalog = default_catalog()
        pull = _session("2026-11-10", "pull", "Pulldowns", sets=2, reps=8, weight=100)
        cold = _plan(catalog, [pull], "legs")
        after_free = _plan(
            catalog,
            [_session("2026-10-15", "legs", "45-Degree Hyperextension"), pull],
            "legs",
        )
        after_machine = _plan(
            catalog,
            [_session("2026-10-15", "legs", "Back Extension Machine"), pull],
            "legs",
        )
        plans = (cold, after_free, after_machine)
        slots = []
        for plan in plans:
            hinge = _hinge_ids(plan)
            self.assertEqual(len(hinge), 1, [row["name"] for row in plan["exercises"]])
            slots.append(hinge[0])
            self.assertEqual(len(plan["exercises"]), 6)
            self.assertLessEqual(_hard_sets(plan), 14)
            self.assertTrue(_compounds_before_isolations(plan))
            self.assertNotIn("Did not add", plan.get("message") or "")
            self.assertNotIn("lowering", (plan.get("message") or "").lower())
            for row in plan["exercises"]:
                self.assertLessEqual(int((row.get("prescription") or {}).get("sets") or 0), 4)
        self.assertEqual(slots[0], "45-degree-hyperextension")
        self.assertEqual(slots[1], "back-extension")
        self.assertEqual(slots[2], "45-degree-hyperextension")
        self.assertIn("45-degree-hyperextension", slots)
        self.assertIn("back-extension", slots)
        self.assertNotEqual(set(slots), {"back-extension"})
        cold_names = [row["name"] for row in cold["exercises"]]
        self.assertEqual(
            cold_names,
            [
                "Trap-Bar Deadlift",
                "Leg Press",
                "RDL",
                "45-Degree Hyperextension",
                "Lying Leg Curl",
                "Calf Extensions",
            ],
        )
        self.assertEqual(
            [row["movement"] for row in cold["exercises"][:3]],
            ["compound", "compound", "compound"],
        )

    def test_pull_stays_at_or_under_five_when_the_hinge_is_included(self):
        catalog = default_catalog()
        legs = _session("2026-11-10", "legs", "Leg Press", sets=2, reps=10, weight=200)
        cold = _plan(catalog, [legs], "pull")
        after_free = _plan(
            catalog,
            [_session("2026-10-15", "pull", "45-Degree Hyperextension"), legs],
            "pull",
        )
        after_machine = _plan(
            catalog,
            [_session("2026-10-15", "pull", "Back Extension Machine"), legs],
            "pull",
        )
        seen = set()
        for plan in (cold, after_free, after_machine):
            hinge = _hinge_ids(plan)
            self.assertLessEqual(len(hinge), 1)
            self.assertLessEqual(len(plan["exercises"]), RAMP["exercises_per_session"])
            self.assertLessEqual(_hard_sets(plan), 14)
            self.assertTrue(_compounds_before_isolations(plan))
            self.assertNotIn("Did not add", plan.get("message") or "")
            self.assertNotIn("lowering", (plan.get("message") or "").lower())
            for row in plan["exercises"]:
                self.assertLessEqual(int((row.get("prescription") or {}).get("sets") or 0), 4)
            if hinge:
                seen.add(hinge[0])
                self.assertIn(plan["exercises"][0]["movement"], ("compound",))
        self.assertEqual(
            seen,
            {"45-degree-hyperextension", "back-extension"},
        )
        self.assertLessEqual(len(cold["exercises"]), 5)
        self.assertIn("45-degree-hyperextension", _hinge_ids(cold))


class ScoreboardUnchangedTests(unittest.TestCase):
    def test_monday_to_date_and_muscle_split_stay(self):
        self.assertEqual(VOLUME_GROUPS, LIVE_GROUPS)
        self.assertIn("lats", VOLUME_GROUPS)
        self.assertIn("mid_upper_back", VOLUME_GROUPS)
        self.assertNotIn("lats_upper_back", VOLUME_GROUPS)
        report = build_muscle_set_report(
            {
                "sessions": [
                    {
                        "date": "2026-11-09",
                        "session_type": "pull",
                        "exercises": [
                            {
                                "name": "Pulldowns",
                                "sets": [{"weight_lbs": 100, "sets": 2, "reps": 8}],
                            }
                        ],
                    }
                ],
                "workout_store": {"catalog": default_catalog(), "goals": dict(RAMP)},
            },
            as_of="2026-11-11",
        )
        for group in LIVE_GROUPS:
            self.assertIn("monday_to_date", report["muscle_sets"][group])
            self.assertIn("proper_sets", report["muscle_sets"][group])
        self.assertIsNot(
            report["muscle_sets"]["lats"],
            report["muscle_sets"]["mid_upper_back"],
        )


if __name__ == "__main__":
    unittest.main()
