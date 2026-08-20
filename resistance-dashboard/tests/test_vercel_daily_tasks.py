"""Daily quests from generated workout/meal plans. /api/daily-tasks is not 404."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.dashboard import dashboard_body
from api.workout._util import daily_tasks_body, dispatch_client_route
from rt_dashboard.models import (
    ExerciseEntry,
    HealthSnapshot,
    Session,
    SetEntry,
)

ROOT = Path(__file__).resolve().parents[1]
VERCEL_JSON = ROOT / "vercel.json"


def _headers():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


class DailyTasksRewrites(unittest.TestCase):
    def test_vercel_json_rewrites_onto_dashboard(self):
        raw = VERCEL_JSON.read_text(encoding="utf-8")
        self.assertIn("/api/daily-tasks", raw)
        self.assertIn("/api/dashboard?_r=daily_tasks", raw)
        self.assertNotIn("api/daily-tasks.py", raw)
        self.assertNotIn("api/daily_tasks.py", raw)
        self.assertFalse((ROOT / "api" / "daily-tasks.py").exists())
        self.assertFalse((ROOT / "api" / "daily_tasks.py").exists())
        self.assertFalse((ROOT / "api" / "daily-tasks").is_dir())

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

    def test_ignore_command_kept(self):
        cfg = json.loads(VERCEL_JSON.read_text(encoding="utf-8"))
        self.assertEqual(
            cfg.get("ignoreCommand"),
            "python3 scripts/vercel_ignore.py || exit 1",
        )


class CookieLessDailyTasks(unittest.TestCase):
    def test_daily_tasks_401_json(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = daily_tasks_body({})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("<html", json.dumps(body).lower())
        self.assertNotIn("daily_tasks", body)

    def test_dispatch_cookie_less_401(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for method in ("GET", "POST"):
                status, body = dispatch_client_route(
                    {}, "", method, path="/api/daily-tasks"
                )
                self.assertEqual(status, 401, method)
                self.assertEqual(body["error"], "auth_required")
                self.assertNotIn("<html", json.dumps(body).lower())
                status, body = dispatch_client_route({}, "_r=daily_tasks", method)
                self.assertEqual(status, 401, method)


class QuestsFromGeneratedPlans(unittest.TestCase):
    def _signed_dashboard(self):
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
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([session], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ), mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=False
            ):
                return dashboard_body(_headers())

    def test_get_dashboard_quests_include_workout_and_meal_leaves(self):
        status, body = self._signed_dashboard()
        self.assertEqual(status, 200)
        today = (body.get("coach") or {}).get("today") or {}
        self.assertGreater(len((today.get("workout") or {}).get("exercises") or []), 0)
        self.assertGreater(len((today.get("meal") or {}).get("meals") or []), 0)
        daily = body.get("daily_tasks") or {}
        self.assertEqual(daily.get("source"), "plan_preview")
        self.assertTrue(daily.get("needs_sync"))
        groups = {g["group"]: g for g in daily.get("groups") or []}
        self.assertIn("training", groups)
        self.assertIn("nutrition", groups)
        self.assertGreater(groups["training"]["total"], 0)
        self.assertGreater(groups["nutrition"]["total"], 0)
        for item in groups["training"]["items"]:
            self.assertFalse(item.get("completed"))
            self.assertIsNone(item.get("task_id"))
        for item in groups["nutrition"]["items"]:
            self.assertFalse(item.get("completed"))
            self.assertIsNone(item.get("task_id"))
            self.assertTrue(item.get("meal_label") or item.get("title"))

    def test_daily_tasks_route_fails_honest_without_gt_creds(self):
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
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([session], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ), mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=False
            ), mock.patch(
                "rt_dashboard.gtasks_bridge.credentials_status",
                return_value={"ok": False, "error": "Google Tasks not configured"},
            ):
                status, body = daily_tasks_body(_headers())
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("Google Tasks", body.get("error") or "")
        self.assertNotIn("<html", json.dumps(body).lower())
        daily = body["daily_tasks"]
        self.assertFalse(daily.get("ok"))
        self.assertIn("Google Tasks", daily.get("error") or "")
        self.assertIsNone(daily.get("list_id"))
        groups = {g["group"]: g for g in daily.get("groups") or []}
        self.assertIn("training", groups)
        self.assertIn("nutrition", groups)
        for g in daily.get("groups") or []:
            self.assertIsNone(g.get("task_id"))
            self.assertFalse(g.get("completed"))
            for item in g.get("items") or []:
                self.assertFalse(item.get("completed"))
                self.assertIsNone(item.get("task_id"))
                self.assertNotEqual(item.get("task_id"), "invented")


if __name__ == "__main__":
    unittest.main()
