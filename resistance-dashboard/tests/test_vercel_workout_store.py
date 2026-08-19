"""Prove Vercel dashboard reads goals.json + catalog.json, not unset stubs."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.dashboard import dashboard_body
from rt_dashboard.grok_ask import build_fitness_context
from rt_dashboard.models import HealthSnapshot, Session, ExerciseEntry, SetEntry
from rt_dashboard.workout_planner import CATALOG_PATH, GOALS_PATH
from rt_dashboard.workout_store import (
    apply_goals_volume_caps,
    load_workspace_catalog,
    load_workspace_goals,
)


REPO_GOALS = Path(__file__).resolve().parents[2] / GOALS_PATH
REPO_CATALOG = Path(__file__).resolve().parents[2] / CATALOG_PATH
BUNDLE_GOALS = Path(__file__).resolve().parents[1] / GOALS_PATH
BUNDLE_CATALOG = Path(__file__).resolve().parents[1] / CATALOG_PATH
DASHBOARD_PY = Path(__file__).resolve().parents[1] / "api" / "dashboard.py"
VERCEL_JSON = Path(__file__).resolve().parents[1] / "vercel.json"


class VercelGoalsCatalogFromFile(unittest.TestCase):
    def test_repo_goals_has_real_ppl_numbers(self):
        raw = json.loads(REPO_GOALS.read_text(encoding="utf-8"))
        self.assertEqual(raw["split"], "ppl")
        self.assertEqual(raw["rotation"], ["push", "pull", "legs"])
        self.assertEqual(raw["sessions_per_week_target"], 5)
        self.assertEqual(raw["progression"], "double_progression")
        self.assertEqual(raw["rest_if_recovery_below"], 40)
        self.assertEqual(raw["volume_framework"], "dean_t_balanced_4_8")
        self.assertEqual(raw["sets_per_muscle_week_min"], 4)
        self.assertEqual(raw["sets_per_muscle_week_max"], 8)
        self.assertEqual(raw["default_hard_sets"], 2)
        self.assertEqual(raw["session_working_set_cap"], 14)
        self.assertEqual(raw["updated_at"], "2026-07-26")

    def test_bundle_copies_match_repo_files(self):
        self.assertTrue(BUNDLE_GOALS.is_file(), BUNDLE_GOALS)
        self.assertTrue(BUNDLE_CATALOG.is_file(), BUNDLE_CATALOG)
        self.assertEqual(BUNDLE_GOALS.read_bytes(), REPO_GOALS.read_bytes())
        self.assertEqual(BUNDLE_CATALOG.read_bytes(), REPO_CATALOG.read_bytes())

    def test_loader_reads_file_not_unset(self):
        goals, goals_src = load_workspace_goals()
        catalog, cat_src = load_workspace_catalog()
        self.assertEqual(goals_src, GOALS_PATH)
        self.assertEqual(cat_src, CATALOG_PATH)
        self.assertEqual(goals["split"], "ppl")
        self.assertEqual(goals["sessions_per_week_target"], 5)
        self.assertEqual(goals["rest_if_recovery_below"], 40)
        self.assertIsInstance(catalog.get("exercises"), list)
        self.assertGreater(len(catalog["exercises"]), 0)
        names = {e.get("name") for e in catalog["exercises"] if isinstance(e, dict)}
        self.assertIn("DB Flat Press", names)

    def test_vercel_bundle_path_alone_is_enough(self):
        from rt_dashboard import workout_store as ws

        with mock.patch.object(ws, "_workspace_file_candidates", side_effect=lambda rel: [
            BUNDLE_GOALS if rel == GOALS_PATH else BUNDLE_CATALOG
        ]):
            goals, goals_src = ws.load_workspace_goals()
            catalog, cat_src = ws.load_workspace_catalog()
        self.assertEqual(goals_src, GOALS_PATH)
        self.assertEqual(cat_src, CATALOG_PATH)
        self.assertEqual(goals["split"], "ppl")
        self.assertGreater(len(catalog.get("exercises") or []), 0)

    def test_dashboard_unset_stubs_gone(self):
        text = DASHBOARD_PY.read_text(encoding="utf-8")
        self.assertNotIn('"catalog": None', text)
        self.assertNotIn("'catalog': None", text)
        self.assertNotIn('"goals": None', text)
        self.assertNotIn("'goals': None", text)
        self.assertNotIn('"catalog": "unset"', text)
        self.assertNotIn('"goals": "unset"', text)
        self.assertIn("load_workspace_goals", text)
        self.assertIn("load_workspace_catalog", text)
        self.assertIn("apply_goals_volume_caps", text)

    def test_include_files_lists_goals_and_catalog(self):
        raw = VERCEL_JSON.read_text(encoding="utf-8")
        self.assertIn("fitness/exercises/goals.json", raw)
        self.assertIn("fitness/exercises/catalog.json", raw)
        self.assertIn("fitness/nutrition/targets.json", raw)


class VercelDashboardWorkoutStore(unittest.TestCase):
    def test_cookie_less_still_401_json(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = dashboard_body({})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("workout_store", body)
        self.assertNotIn("sessions", body)

    def test_signed_in_payload_has_ppl_goals_and_catalog(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        session = Session(
            date="2026-08-17",
            session_type="push",
            exercises=[
                ExerciseEntry(
                    name="DB Flat Press",
                    sets=[SetEntry(weight_lbs=45, sets=3, reps=10)],
                )
            ],
        )
        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([session], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ):
                status, body = dashboard_body(headers)
        self.assertEqual(status, 200)
        wo = body["workout_store"]
        self.assertEqual(wo["goals"]["split"], "ppl")
        self.assertEqual(wo["sources"]["goals"], GOALS_PATH)
        self.assertEqual(wo["sources"]["catalog"], CATALOG_PATH)
        self.assertIsNotNone(wo["catalog"])
        self.assertGreater(len((wo["catalog"] or {}).get("exercises") or []), 0)
        self.assertEqual(body["nutrition_store"]["sources"]["inventory"], "unset")
        self.assertEqual(len(body["sessions"]), 1)
        self.assertEqual(body["sessions"][0]["exercises"][0]["name"], "DB Flat Press")
        ctx = build_fitness_context(body)
        self.assertEqual(ctx["workout_store"]["goals"]["split"], "ppl")
        self.assertGreater(ctx["workout_store"]["catalog_count"], 0)
        self.assertEqual(ctx["sessions"][0]["exercises"][0]["name"], "DB Flat Press")
        sets = ctx["sessions"][0]["exercises"][0]["sets"]
        self.assertTrue(sets)
        if isinstance(sets, list):
            self.assertEqual(sets[0]["weight_lbs"], 45)
            self.assertEqual(sets[0]["reps"], 10)
        # Frankenfit: catalog default_sets=3 must not survive the load path
        for ex in (wo["catalog"] or {}).get("exercises") or []:
            self.assertEqual(ex.get("default_sets"), 2, ex.get("name"))
            self.assertNotEqual(ex.get("default_sets"), 3)
        self.assertEqual(wo["goals"]["default_hard_sets"], 2)
        self.assertEqual(wo["goals"]["session_working_set_cap"], 14)
        self.assertEqual(wo["next_session_type"], "pull")
        self.assertIn("Next session: PULL", wo["plan"]["message"])
        pack = wo["training_pack"]
        self.assertEqual(pack["next_session_type"], "pull")
        self.assertIn("DB Flat Press", pack["catalog_names"])
        self.assertEqual(len(pack["sessions"]), 1)
        self.assertEqual(pack["sessions"][0]["exercises"][0]["name"], "DB Flat Press")
        self.assertEqual(pack["sessions"][0]["exercises"][0]["weight_lbs"], 45)
        self.assertEqual(ctx["workout_store"]["next_session_type"], "pull")
        self.assertIn("DB Flat Press", ctx["workout_store"]["catalog_names"])
        self.assertIn("Ignore catalog default_sets=3", ctx["workout_store"]["volume_framework"]["notes"])
        self.assertEqual(ctx["workout_store"]["volume_framework"]["default_hard_sets"], 2)

    def test_apply_goals_volume_caps_ignores_catalog_three(self):
        catalog, _ = load_workspace_catalog()
        goals, _ = load_workspace_goals()
        raw_sets = {
            e.get("name"): e.get("default_sets")
            for e in (catalog.get("exercises") or [])
            if isinstance(e, dict)
        }
        self.assertTrue(raw_sets)
        self.assertTrue(all(v == 3 for v in raw_sets.values()), raw_sets)
        capped = apply_goals_volume_caps(catalog, goals)
        for ex in capped.get("exercises") or []:
            self.assertEqual(ex.get("default_sets"), 2, ex.get("name"))
            self.assertEqual(ex.get("volume_from"), "goals")
        # SoT file itself is unchanged
        self.assertTrue(all(v == 3 for v in raw_sets.values()))


if __name__ == "__main__":
    unittest.main()
