"""Cookie-less 401 JSON on client workout routes. Option B rewrites. No extra functions."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import (
    available_body,
    available_write,
    dispatch_client_route,
    generate_body,
    goals_body,
    goals_write,
    workouts_body,
    workouts_write,
)
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import CATALOG_PATH, GOALS_PATH

ROOT = Path(__file__).resolve().parents[1]


class ClientRouteLayout(unittest.TestCase):
    def test_no_extra_functions_or_workout_py_collision(self):
        self.assertFalse((ROOT / "api" / "workout.py").exists())
        self.assertFalse((ROOT / "api" / "workouts.py").exists())
        self.assertFalse((ROOT / "api" / "workout-plan.py").exists())
        self.assertFalse((ROOT / "api" / "workout_plan_generate.py").exists())
        self.assertFalse((ROOT / "api" / "workout" / "goals.py").exists())
        self.assertFalse((ROOT / "api" / "meal-plan.py").exists())
        self.assertFalse((ROOT / "api" / "refresh.py").exists())
        self.assertFalse((ROOT / "api" / "daily-tasks.py").exists())
        self.assertTrue((ROOT / "api" / "workout" / "_util.py").is_file())
        self.assertTrue((ROOT / "api" / "dashboard.py").is_file())
        self.assertTrue((ROOT / "api" / "ask" / "plan.py").is_file())

    def test_vercel_json_rewrites_onto_existing_functions(self):
        raw = (ROOT / "vercel.json").read_text(encoding="utf-8")
        self.assertIn("/api/workout-plan/generate", raw)
        self.assertIn("/api/ask/plan?_r=generate", raw)
        self.assertIn("/api/workout/goals", raw)
        self.assertIn("/api/dashboard?_r=goals", raw)
        self.assertIn("/api/workout/exercise/available", raw)
        self.assertIn("/api/dashboard?_r=available", raw)
        self.assertIn("/api/workouts", raw)
        self.assertIn("/api/dashboard?_r=workouts", raw)
        self.assertIn("/api/meal-plan/generate", raw)
        self.assertIn("/api/dashboard?_r=meal_generate", raw)
        self.assertIn("/api/refresh", raw)
        self.assertIn("/api/dashboard?_r=refresh", raw)
        self.assertIn("/api/daily-tasks", raw)
        self.assertIn("/api/dashboard?_r=daily_tasks", raw)
        self.assertIn("/api/daily-tasks/complete", raw)
        self.assertIn("/api/dashboard?_r=daily_tasks_complete", raw)
        self.assertIn("/api/agent/today", raw)
        self.assertIn("/api/dashboard?_r=agent_today", raw)
        self.assertIn("/api/agent/generate-plan", raw)
        self.assertIn("/api/ask/plan?_r=agent_generate", raw)
        self.assertNotIn("api/agent/today.py", raw)
        self.assertNotIn("api/agent/generate-plan.py", raw)
        self.assertFalse((ROOT / "api" / "agent").exists())
        self.assertNotIn("api/workout-plan/generate.py", raw)
        self.assertNotIn("api/workouts.py", raw)
        self.assertNotIn("api/workout_plan_generate.py", raw)
        self.assertIn("fitness/exercises/goals.json", raw)
        self.assertIn("fitness/exercises/catalog.json", raw)
        self.assertIn("fitness/exercises/equipment.json", raw)
        self.assertIn("/api/equipment/add", raw)
        self.assertIn("/api/dashboard?_r=eq_add", raw)
        self.assertIn("/api/labs/upload", raw)
        self.assertIn("/api/dashboard?_r=labs_upload", raw)
        self.assertIn("/api/labs/delete", raw)
        self.assertIn("/api/dashboard?_r=labs_delete", raw)
        self.assertIn("/api/labs", raw)
        self.assertIn("/api/dashboard?_r=labs", raw)
        self.assertNotIn("api/labs.py", raw)
        self.assertFalse((ROOT / "api" / "labs.py").exists())
        self.assertFalse((ROOT / "api" / "labs").is_dir())

    def test_hobby_function_count_stays_at_12(self):
        api = ROOT / "api"
        fns = []
        for path in api.rglob("*.py"):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            src = path.read_text(encoding="utf-8")
            if "class handler" in src or "\ndef app(" in src or "\napp = " in src:
                fns.append(path)
        self.assertEqual(len(fns), 12, [str(x.relative_to(ROOT)) for x in fns])


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

    def test_dispatch_cookie_less_401(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for route in (
                "goals",
                "available",
                "workouts",
                "generate",
                "meal_plan",
                "meal_generate",
                "refresh",
                "daily_tasks",
                "daily_tasks_complete",
                "agent_today",
                "labs",
            ):
                status, body = dispatch_client_route({}, f"_r={route}", "GET")
                self.assertEqual(status, 401, route)
                self.assertEqual(body["error"], "auth_required")
                self.assertNotIn("<html", json.dumps(body).lower())

    def test_dispatch_uses_original_path_without_query(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            pairs = (
                ("/api/workout/goals", "goals"),
                ("/api/workout/exercise/available", "available"),
                ("/api/workouts", "workouts"),
                ("/api/workout-plan/generate", "generate"),
                ("/api/meal-plan", "meal_plan"),
                ("/api/meal-plan/generate", "meal_generate"),
                ("/api/refresh", "refresh"),
                ("/api/daily-tasks", "daily_tasks"),
                ("/api/daily-tasks/complete", "daily_tasks_complete"),
                ("/api/agent/today", "agent_today"),
                ("/api/labs", "labs"),
            )
            for path, route in pairs:
                status, body = dispatch_client_route({}, "", "GET", path=path)
                self.assertEqual(status, 401, route)
                self.assertEqual(body["error"], "auth_required")
            status, body = dispatch_client_route(
                {}, "", "GET", path="/api/agent/generate-plan"
            )
            self.assertEqual(status, 405)
            self.assertEqual(body["error"], "method_not_allowed")
            status, body = dispatch_client_route(
                {}, "", "POST", path="/api/agent/generate-plan"
            )
            self.assertEqual(status, 401)
            self.assertEqual(body["error"], "auth_required")

    def test_dispatch_labs_post_cookie_less_401(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for path, route in (
                ("/api/labs/upload", "labs_upload"),
                ("/api/labs/delete", "labs_delete"),
            ):
                status, body = dispatch_client_route(
                    {}, "", "POST", payload={}, path=path
                )
                self.assertEqual(status, 401, route)
                self.assertEqual(body["error"], "auth_required")
                self.assertNotIn("<html", json.dumps(body).lower())
                status, body = dispatch_client_route(
                    {}, f"_r={route}", "POST", payload={}
                )
                self.assertEqual(status, 401, route)


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


class PreviewWriteIsReadOnly(unittest.TestCase):
    def _headers(self):
        token = make_session(
            {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
        )
        return {"Cookie": f"{SESSION_COOKIE}={token}"}

    def test_cookie_less_writes_still_401(self):
        for fn in (goals_write, available_write, workouts_write):
            status, body = _cookie_less(fn)
            self.assertEqual(status, 401, fn.__name__)
            self.assertEqual(body["error"], "auth_required")
            self.assertNotIn("<html", json.dumps(body).lower())

    def test_signed_in_goals_still_403_json(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            headers = self._headers()
            status, body = goals_write(headers)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "preview_read_only")
        self.assertTrue(body["readonly"])
        self.assertNotIn("<html", json.dumps(body).lower())

    def test_signed_in_available_write_is_not_preview_read_only(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            headers = self._headers()
            status, body = available_write(headers, {})
        self.assertNotEqual(body.get("error"), "preview_read_only")
        self.assertEqual(status, 400)
        self.assertIn("exercise id", str(body.get("error") or "").lower())

    def test_signed_in_workouts_write_is_not_preview_read_only(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            status, body = workouts_write(self._headers(), {})
        self.assertNotEqual(body.get("error"), "preview_read_only")
        self.assertNotIn("Pi FitDash", str(body.get("message") or ""))
        self.assertIn(status, (400, 503))
        self.assertFalse(body.get("ok"))


if __name__ == "__main__":
    unittest.main()
