"""Cookie-less 401 JSON on client workout routes. No HTML 404. No api/workout.py."""

from __future__ import annotations

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout.goals import goals_body
from api.workout.exercise.available import available_body
from api.workouts import workouts_body
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import CATALOG_PATH, GOALS_PATH

ROOT = Path(__file__).resolve().parents[1]


def _load_generate():
    path = ROOT / "api" / "workout-plan" / "generate.py"
    spec = importlib.util.spec_from_file_location("_fitdash_workout_plan_generate", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class ClientRouteLayout(unittest.TestCase):
    def test_no_workout_py_package_collision(self):
        self.assertFalse((ROOT / "api" / "workout.py").exists())
        self.assertTrue((ROOT / "api" / "workout" / "goals.py").is_file())
        self.assertTrue((ROOT / "api" / "workout" / "exercise" / "available.py").is_file())
        self.assertTrue((ROOT / "api" / "workout-plan" / "generate.py").is_file())
        self.assertTrue((ROOT / "api" / "workouts.py").is_file())
        self.assertFalse((ROOT / "api" / "workout-plan.py").exists())

    def test_vercel_json_lists_new_functions(self):
        raw = (ROOT / "vercel.json").read_text(encoding="utf-8")
        self.assertIn("api/workout-plan/generate.py", raw)
        self.assertIn("api/workout/goals.py", raw)
        self.assertIn("api/workout/exercise/available.py", raw)
        self.assertIn("api/workouts.py", raw)
        self.assertIn("fitness/exercises/goals.json", raw)
        self.assertIn("fitness/exercises/catalog.json", raw)


def _cookie_less(fn):
    with mock.patch.dict(os.environ, {}, clear=True):
        return fn({})


class CookieLessClientRoutes(unittest.TestCase):
    def test_goals_401_json(self):
        status, body = _cookie_less(goals_body)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("<html", json.dumps(body).lower())
        self.assertNotIn("goals", body)

    def test_available_401_json(self):
        status, body = _cookie_less(available_body)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("<html", json.dumps(body).lower())
        self.assertNotIn("catalog", body)

    def test_generate_401_json(self):
        generate_body = _load_generate().generate_body
        status, body = _cookie_less(generate_body)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("<html", json.dumps(body).lower())
        self.assertNotIn("plan", body)

    def test_workouts_401_json(self):
        status, body = _cookie_less(workouts_body)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("<html", json.dumps(body).lower())
        self.assertNotIn("sessions", body)


class SignedInClientRoutes(unittest.TestCase):
    def _headers(self):
        token = make_session(
            {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
        )
        return {"Cookie": f"{SESSION_COOKIE}={token}"}

    def test_goals_returns_file_plus_caps(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            status, body = goals_body(self._headers())
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["readonly"])
        self.assertEqual(body["source"], GOALS_PATH)
        self.assertEqual(body["goals"]["split"], "ppl")
        self.assertEqual(body["goals"]["rest_if_recovery_below"], 40)
        self.assertEqual(body["goals"]["default_hard_sets"], 2)
        self.assertEqual(body["goals"]["session_working_set_cap"], 14)
        self.assertFalse(body["write"]["ok"])

    def test_available_returns_capped_catalog(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            status, body = available_body(self._headers())
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["sources"]["catalog"], CATALOG_PATH)
        self.assertIn("DB Flat Press", body["names"])
        for ex in (body["catalog"] or {}).get("exercises") or []:
            self.assertEqual(ex.get("default_sets"), 2, ex.get("name"))
            self.assertNotEqual(ex.get("default_sets"), 3)

    def test_workouts_returns_turso_sessions_readonly(self):
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
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([session], [], "turso"),
            ):
                status, body = workouts_body(self._headers())
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["readonly"])
        self.assertEqual(body["session_count"], 1)
        self.assertEqual(body["sessions"][0]["exercises"][0]["name"], "DB Flat Press")
        self.assertEqual(
            body["sessions"][0]["exercises"][0]["sets"][0]["weight_lbs"], 45
        )
        self.assertFalse(body["write"]["ok"])

    def test_generate_rest_gate_via_ask_plan(self):
        generate_body = _load_generate().generate_body
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        rest_plan = {
            "session_type": "rest",
            "is_rest_day": True,
            "exercises": [],
            "message": "Recovery score 35 is below threshold (40).",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.ask.plan.ask_plan_body",
                return_value=(200, {"ok": True, "workout": rest_plan, "meal": {}}),
            ):
                status, body = generate_body(self._headers())
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["plan"]["is_rest_day"])
        self.assertEqual(body["plan"]["session_type"], "rest")
        self.assertEqual(body["plan"]["exercises"], [])


if __name__ == "__main__":
    unittest.main()
