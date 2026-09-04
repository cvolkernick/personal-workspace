"""Programmed exercise library vs equipment access + coach suggestions."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import available_write
from rt_dashboard.library_groom import (
    suggest_library_additions,
    suggest_library_removals,
)
from rt_dashboard.library_store import apply_library_overlay, set_library_available
from rt_dashboard.workout_planner import generate_workout_plan
from rt_dashboard.workout_store import load_workspace_catalog


def _eq(*tags):
    items = []
    for tag in tags:
        items.append(
            {
                "id": tag,
                "name": tag.replace("_", " ").title(),
                "tag": tag,
                "max_weight_lbs": 50 if tag == "dumbbells" else None,
                "source": "owned" if tag == "dumbbells" else "gym",
            }
        )
    return {"items": items}


class LibraryVsDump(unittest.TestCase):
    def setUp(self):
        self.catalog, _ = load_workspace_catalog()
        self.goals = {
            "rotation": ["push", "pull", "legs"],
            "exercises_per_session": 5,
            "default_hard_sets": 2,
            "rest_if_recovery_below": 40,
        }

    def test_floor_press_is_off_library(self):
        ids = {
            e["id"]: e
            for e in self.catalog["exercises"]
            if isinstance(e, dict)
        }
        self.assertFalse(ids["db-floor-press"]["available"])
        self.assertFalse(ids["db-row"]["available"])
        self.assertFalse(ids["goblet-squat"]["available"])
        self.assertTrue(ids["db-flat-press"]["available"])

    def test_seed_access_plans_library_not_hidden_home_lifts(self):
        equipment = _eq(
            "dumbbells",
            "bench",
            "incline_bench",
            "smith_machine",
            "cable",
            "lat_pulldown",
            "assisted_pullup",
            "machine",
            "leg_press",
            "barbell",
        )
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            [],
            recovery_score=80,
            session_type="push",
            as_of="2026-09-03",
            equipment=equipment,
        )
        names = [e["name"] for e in plan["exercises"]]
        self.assertTrue(names)
        self.assertNotIn("DB Floor Press", names)
        self.assertTrue(
            any(n in names for n in ("DB Flat Press", "Smith Bench", "DB Shoulder Press")),
            names,
        )

    def test_adding_smith_does_not_enable_hidden_row(self):
        catalog = {
            "exercises": [
                {
                    "id": "smith-bench",
                    "name": "Smith Bench",
                    "session_types": ["push"],
                    "primary_muscles": ["chest"],
                    "movement": "compound",
                    "equipment": ["smith_machine"],
                    "available": False,
                    "priority": 8,
                    "default_sets": 2,
                    "default_reps": 8,
                    "rep_range": [6, 10],
                },
                {
                    "id": "db-shoulder-press",
                    "name": "DB Shoulder Press",
                    "session_types": ["push"],
                    "primary_muscles": ["shoulders"],
                    "movement": "compound",
                    "equipment": ["dumbbells"],
                    "available": True,
                    "priority": 9,
                    "default_sets": 2,
                    "default_reps": 10,
                    "rep_range": [8, 12],
                },
            ]
        }
        equipment = _eq("dumbbells", "smith_machine")
        plan = generate_workout_plan(
            catalog,
            self.goals,
            [],
            recovery_score=80,
            session_type="push",
            as_of="2026-09-03",
            equipment=equipment,
        )
        names = [e["name"] for e in plan["exercises"]]
        self.assertIn("DB Shoulder Press", names)
        self.assertNotIn("Smith Bench", names)
        adds = suggest_library_additions(catalog, equipment)["suggestions"]
        self.assertIn("smith-bench", {s["id"] for s in adds})


class Suggestions(unittest.TestCase):
    def test_home_transition_adds(self):
        catalog, _ = load_workspace_catalog()
        equipment = _eq(
            "dumbbells",
            "bench",
            "incline_bench",
            "smith_machine",
            "cable",
            "lat_pulldown",
            "assisted_pullup",
            "machine",
            "leg_press",
            "barbell",
        )
        block = suggest_library_additions(catalog, equipment)
        ids = [s["id"] for s in block["suggestions"]]
        self.assertIn("db-floor-press", ids)
        self.assertIn("db-row", ids)
        self.assertIn("goblet-squat", ids)
        self.assertTrue(all(s["action"] == "add" for s in block["suggestions"]))

    def test_infeasible_library_row_is_removal(self):
        catalog = {
            "exercises": [
                {
                    "id": "smith-bench",
                    "name": "Smith Bench",
                    "session_types": ["push"],
                    "primary_muscles": ["chest"],
                    "movement": "compound",
                    "equipment": ["smith_machine"],
                    "available": True,
                    "priority": 8,
                    "default_sets": 2,
                    "default_reps": 8,
                    "rep_range": [6, 10],
                }
            ]
        }
        block = suggest_library_removals(catalog, _eq("dumbbells"))
        self.assertEqual(block["suggestions"][0]["id"], "smith-bench")
        self.assertEqual(block["suggestions"][0]["action"], "remove")


class OverlayApply(unittest.TestCase):
    def test_overlay_flips_available(self):
        catalog, _ = load_workspace_catalog()
        stamped = apply_library_overlay(
            catalog, {"enabled": ["db-floor-press"], "disabled": ["smith-shrugs"]}
        )
        by = {e["id"]: e for e in stamped["exercises"]}
        self.assertTrue(by["db-floor-press"]["available"])
        self.assertFalse(by["smith-shrugs"]["available"])
        self.assertTrue(by["db-flat-press"]["available"])

    def test_set_library_available_exclusive(self):
        ov = set_library_available({"enabled": [], "disabled": ["db-row"]}, "db-row", True)
        self.assertIn("db-row", ov["enabled"])
        self.assertNotIn("db-row", ov["disabled"])

    def test_available_write_persists_overlay(self):
        store = {}

        def get(uid):
            return store.get(uid)

        def put(uid, ov):
            store[uid] = ov

        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.library_store._turso_get", side_effect=get
            ), mock.patch(
                "rt_dashboard.library_store._turso_put", side_effect=put
            ), mock.patch(
                "rt_dashboard.equipment_store.load_preview_equipment",
                return_value=(_eq("dumbbells", "cable", "bench"), "turso"),
            ):
                status, body = available_write(
                    headers, {"id": "db-floor-press", "available": True}
                )
        self.assertEqual(status, 200, body)
        self.assertIn("db-floor-press", body["library"]["enabled"])
        by = {e["id"]: e for e in body["catalog"]["exercises"]}
        self.assertTrue(by["db-floor-press"]["available"])

    def test_available_write_rejects_infeasible(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.equipment_store.load_preview_equipment",
                return_value=(_eq("dumbbells"), "turso"),
            ):
                status, body = available_write(
                    headers, {"id": "smith-bench", "available": True}
                )
        self.assertEqual(status, 400, body)
        self.assertIn("equipment", str(body.get("error") or "").lower())


if __name__ == "__main__":
    unittest.main()
