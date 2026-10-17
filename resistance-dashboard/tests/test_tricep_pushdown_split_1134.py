"""Rope and T-bar are separate lifts. History stays one trend and one PR line."""

from __future__ import annotations

import unittest
from pathlib import Path

from rt_dashboard.analytics import dashboard_payload
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.pr_detect import apply_auto_prs
from rt_dashboard.workout_planner import (
    NAME_ALIASES,
    TRICEP_PUSHDOWN_FAMILY,
    available_exercises,
    generate_workout_plan,
    last_performance,
    load_json_file,
    pattern_family,
    session_types_for_lift_name,
)
from rt_dashboard.workout_store import load_workspace_catalog


def _set(weight, sets=3, reps=12):
    return SetEntry(weight_lbs=weight, sets=sets, reps=reps)


def _session(day, name, weight, sets=3, reps=12):
    return Session(
        date=day,
        session_type="push",
        exercises=[ExerciseEntry(name=name, sets=[_set(weight, sets, reps)])],
    )


def _lift(eid, name, movement, muscles, equipment):
    return {
        "id": eid,
        "name": name,
        "session_types": ["push"],
        "primary_muscles": muscles,
        "secondary_muscles": [],
        "movement": movement,
        "equipment": equipment,
        "default_sets": 3,
        "default_reps": 10,
        "rep_range": [8, 12],
        "priority": 7,
        "available": True,
    }


class Catalog(unittest.TestCase):
    def test_picker_lists_two_variants_and_keeps_the_anchor(self):
        root = Path(__file__).resolve().parents[2] / "fitness" / "exercises" / "catalog.json"
        bundled = Path(__file__).resolve().parents[1] / "fitness" / "exercises" / "catalog.json"
        self.assertEqual(root.read_text(encoding="utf-8"), bundled.read_text(encoding="utf-8"))
        catalog = load_json_file(root, {})
        by_id = {ex["id"]: ex for ex in catalog["exercises"]}
        anchor = by_id["tricep-pushdowns"]
        rope = by_id["tricep-pushdowns-rope"]
        bar = by_id["tricep-pushdowns-t-bar"]
        self.assertEqual(anchor["name"], "Tricep Pushdowns")
        self.assertFalse(anchor["available"])
        self.assertEqual(rope["name"], "Tricep Pushdowns (Rope)")
        self.assertEqual(bar["name"], "Tricep Pushdowns (T-Bar)")
        for ex in (anchor, rope, bar):
            self.assertEqual(ex["session_types"], ["push"])
            self.assertEqual(ex["primary_muscles"], ["triceps"])
            self.assertEqual(ex["movement"], "isolation")
            self.assertEqual(ex["equipment"], ["cable"])
        self.assertTrue(rope["available"])
        self.assertTrue(bar["available"])
        picker = [ex["name"] for ex in available_exercises(catalog)]
        self.assertIn("Tricep Pushdowns (Rope)", picker)
        self.assertIn("Tricep Pushdowns (T-Bar)", picker)
        self.assertNotIn("Tricep Pushdowns", picker)
        loaded, _src = load_workspace_catalog()
        loaded_ids = {ex["id"] for ex in loaded["exercises"]}
        self.assertIn("tricep-pushdowns", loaded_ids)
        self.assertIn("tricep-pushdowns-rope", loaded_ids)
        self.assertIn("tricep-pushdowns-t-bar", loaded_ids)

    def test_bare_names_resolve_to_the_group_not_an_attachment(self):
        self.assertEqual(NAME_ALIASES["tricep pushdowns"], "tricep-pushdowns")
        self.assertEqual(NAME_ALIASES["tricep pushdown"], "tricep-pushdowns")
        self.assertEqual(NAME_ALIASES["tricep pushdowns (rope)"], "tricep-pushdowns-rope")
        self.assertEqual(NAME_ALIASES["tricep pulldowns t bar"], "tricep-pushdowns-t-bar")
        self.assertEqual(session_types_for_lift_name("tricep pushdowns"), ("push",))
        self.assertEqual(session_types_for_lift_name("tricep pushdown"), ("push",))
        self.assertEqual(session_types_for_lift_name("tricep pulldowns rope"), ("push",))
        self.assertEqual(
            pattern_family({"id": "tricep-pushdowns-rope", "movement": "isolation"}),
            TRICEP_PUSHDOWN_FAMILY,
        )
        self.assertEqual(
            pattern_family({"id": "tricep-pushdowns-t-bar", "movement": "isolation"}),
            TRICEP_PUSHDOWN_FAMILY,
        )

    def test_historical_fixture_is_not_renamed(self):
        text = (
            Path(__file__).resolve().parent / "fixtures" / "sample_push.md"
        ).read_text(encoding="utf-8")
        self.assertIn("- Tricep Pushdowns:", text)
        self.assertNotIn("Tricep Pushdowns (Rope)", text)
        self.assertNotIn("Tricep Pushdowns (T-Bar)", text)


class History(unittest.TestCase):
    def setUp(self):
        catalog, _src = load_workspace_catalog()
        self.by_id = {ex["id"]: ex for ex in available_exercises(catalog)}

    def test_variant_seeds_from_bare_history_not_its_sibling(self):
        sessions = [
            _session("2026-08-01", "Tricep Pushdowns", 47.5),
            _session("2026-08-08", "Tricep Pushdown", 45),
            _session("2026-08-15", "Tricep Pushdowns (T-Bar)", 30, sets=2, reps=10),
        ]
        rope = last_performance(sessions, "Tricep Pushdowns (Rope)", self.by_id)
        self.assertEqual(rope["date"], "2026-08-08")
        self.assertEqual(rope["weight_lbs"], 45.0)
        own = last_performance(
            sessions + [_session("2026-08-22", "Tricep Pushdowns (Rope)", 40)],
            "Tricep Pushdowns (Rope)",
            self.by_id,
        )
        self.assertEqual(own["date"], "2026-08-22")
        self.assertEqual(own["weight_lbs"], 40.0)
        bare = last_performance(sessions, "Tricep Pushdowns", self.by_id)
        self.assertEqual(bare["date"], "2026-08-08")
        self.assertEqual(bare["weight_lbs"], 45.0)

    def test_trend_and_pr_share_one_group_and_do_not_rename(self):
        history = [_session("2026-08-01", "Tricep Pushdowns", 47.5)]
        rope = _session("2026-08-08", "Tricep Pushdowns (Rope)", 40)
        tagged = apply_auto_prs(rope, history)
        self.assertEqual(tagged.exercises[0].name, "Tricep Pushdowns (Rope)")
        self.assertFalse(tagged.exercises[0].is_pr)
        heavier = apply_auto_prs(_session("2026-08-15", "Tricep Pushdowns (T-Bar)", 50), history + [rope])
        self.assertTrue(heavier.exercises[0].is_pr)
        self.assertEqual(heavier.exercises[0].name, "Tricep Pushdowns (T-Bar)")
        same = apply_auto_prs(
            _session("2026-08-22", "Tricep Pushdowns (Rope)", 50),
            history + [rope, heavier],
        )
        self.assertFalse(same.exercises[0].is_pr)

        payload = dashboard_payload(history + [rope, heavier])
        self.assertIn("Tricep Pushdowns", payload["top_exercises"])
        self.assertNotIn("Tricep Pushdowns (Rope)", payload["top_exercises"])
        self.assertNotIn("Tricep Pushdowns (T-Bar)", payload["top_exercises"])
        series = payload["strength_trends"]["Tricep Pushdowns"]
        self.assertEqual(len(series), 3)
        self.assertEqual({point["exercise"] for point in series}, {"Tricep Pushdowns"})


class Planner(unittest.TestCase):
    def test_prescribes_one_variant_and_can_rotate(self):
        catalog = {
            "exercises": [
                _lift("db-flat-press", "DB Flat Press", "compound", ["chest"], ["dumbbells"]),
                _lift(
                    "tricep-pushdowns-rope",
                    "Tricep Pushdowns (Rope)",
                    "isolation",
                    ["triceps"],
                    ["cable"],
                ),
                _lift(
                    "tricep-pushdowns-t-bar",
                    "Tricep Pushdowns (T-Bar)",
                    "isolation",
                    ["triceps"],
                    ["cable"],
                ),
            ]
        }
        goals = {
            "rotation": ["push", "pull", "legs"],
            "exercises_per_session": 5,
            "prefer_compounds_first": True,
            "default_hard_sets": 2,
            "rest_if_recovery_below": 0,
            "auto_focus_muscles": False,
        }
        first = generate_workout_plan(
            catalog,
            goals,
            [_session("2026-09-01", "Tricep Pushdowns", 47.5)],
            recovery_score=80,
            session_type="push",
            as_of="2026-10-02",
        )
        names = [ex["name"] for ex in first["exercises"]]
        self.assertIn("Tricep Pushdowns (Rope)", names)
        self.assertNotIn("Tricep Pushdowns (T-Bar)", names)
        self.assertNotIn("Tricep Pushdowns", names)
        second = generate_workout_plan(
            catalog,
            goals,
            [
                _session("2026-09-01", "Tricep Pushdowns", 47.5),
                _session("2026-09-15", "Tricep Pushdowns (Rope)", 40),
            ],
            recovery_score=80,
            session_type="push",
            as_of="2026-10-09",
        )
        rotated = [ex["name"] for ex in second["exercises"]]
        self.assertIn("Tricep Pushdowns (T-Bar)", rotated)
        self.assertNotIn("Tricep Pushdowns (Rope)", rotated)


if __name__ == "__main__":
    unittest.main()
