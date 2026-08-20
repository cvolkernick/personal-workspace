"""Vercel Today meal plan uses Pi generate_meal_plan. Empty pantry stays empty."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.dashboard import dashboard_body, preview_meal_plan
from api.workout._util import dispatch_client_route, meal_plan_body, refresh_body
from rt_dashboard.inventory_store import INVENTORY_PATH, load_workspace_inventory
from rt_dashboard.models import HealthSnapshot
from rt_dashboard.nutrition_planner import stocked_ingredients
from rt_dashboard.nutrition_store import load_workspace_targets

ROOT = Path(__file__).resolve().parents[1]
VERCEL_JSON = ROOT / "vercel.json"


def _headers():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


class MealPlanRewrites(unittest.TestCase):
    def test_vercel_json_rewrites_onto_dashboard(self):
        raw = VERCEL_JSON.read_text(encoding="utf-8")
        self.assertIn("/api/meal-plan/generate", raw)
        self.assertIn("/api/dashboard?_r=meal_generate", raw)
        self.assertIn("/api/meal-plan", raw)
        self.assertIn("/api/dashboard?_r=meal_plan", raw)
        self.assertIn("/api/refresh", raw)
        self.assertIn("/api/dashboard?_r=refresh", raw)
        self.assertIn("/api/daily-tasks", raw)
        self.assertIn("/api/dashboard?_r=daily_tasks", raw)
        self.assertNotIn("api/meal-plan.py", raw)
        self.assertNotIn("api/meal_plan.py", raw)
        self.assertNotIn("api/refresh.py", raw)
        self.assertFalse((ROOT / "api" / "meal-plan.py").exists())
        self.assertFalse((ROOT / "api" / "meal_plan.py").exists())
        self.assertFalse((ROOT / "api" / "refresh.py").exists())
        self.assertFalse((ROOT / "api" / "meal-plan").is_dir())

    def test_ignore_command_kept(self):
        cfg = json.loads(VERCEL_JSON.read_text(encoding="utf-8"))
        self.assertEqual(
            cfg.get("ignoreCommand"),
            "python3 scripts/vercel_ignore.py || exit 1",
        )

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


class CookieLessMealRoutes(unittest.TestCase):
    def test_meal_plan_401_json(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = meal_plan_body({})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("<html", json.dumps(body).lower())
        self.assertNotIn("plan", body)

    def test_refresh_401_json(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = refresh_body({})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("<html", json.dumps(body).lower())
        self.assertNotIn("nutrition_store", body)

    def test_dispatch_cookie_less_401_on_paths(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            pairs = (
                ("/api/meal-plan", "meal_plan"),
                ("/api/meal-plan/generate", "meal_generate"),
                ("/api/refresh", "refresh"),
            )
            for path, route in pairs:
                status, body = dispatch_client_route({}, "", "GET", path=path)
                self.assertEqual(status, 401, route)
                self.assertEqual(body["error"], "auth_required")
                self.assertNotIn("<html", json.dumps(body).lower())
                status, body = dispatch_client_route({}, f"_r={route}", "POST")
                self.assertEqual(status, 401, route)


class GenerateOnGet(unittest.TestCase):
    def test_signed_in_dashboard_generates_plan_from_in_stock(self):
        inv, src = load_workspace_inventory()
        self.assertEqual(src, INVENTORY_PATH)
        stocked = stocked_ingredients(inv)
        self.assertGreaterEqual(len(stocked), 1)

        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ), mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=False
            ):
                status, body = dashboard_body(_headers())
        self.assertEqual(status, 200)
        meal = (body.get("nutrition_store") or {}).get("meal_plan") or {}
        self.assertTrue(meal.get("in_stock_only"))
        self.assertGreaterEqual(meal.get("stocked_count") or 0, 1)
        self.assertGreater(len(meal.get("items") or []), 0)
        self.assertGreater(len(meal.get("meals") or []), 0)
        labels = [m.get("label") for m in meal["meals"]]
        self.assertIn("Next meal", labels)
        stocked_names = {i["name"] for i in stocked}
        for it in meal["items"]:
            self.assertIn(it["name"], stocked_names)
        self.assertNotIn("Connect SuperGrok", meal.get("message") or "")
        self.assertNotIn("inventory_suggestions", body.get("nutrition_store") or {})

    def test_empty_in_stock_is_honest_empty(self):
        empty = {"updated_at": "2026-08-20", "ingredients": []}
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ), mock.patch(
                "rt_dashboard.inventory_store.load_preview_inventory",
                return_value=(empty, "test-empty"),
            ):
                status, body = dashboard_body(_headers())
        self.assertEqual(status, 200)
        meal = (body.get("nutrition_store") or {}).get("meal_plan") or {}
        self.assertEqual(meal.get("items"), [])
        self.assertEqual(meal.get("meals"), [])
        self.assertEqual(meal.get("stocked_count"), 0)
        self.assertTrue(meal.get("in_stock_only"))
        self.assertIn("No in-stock", meal.get("message") or "")
        self.assertNotIn("Connect SuperGrok", meal.get("message") or "")

    def test_all_out_of_stock_is_honest_empty(self):
        oos = {
            "updated_at": "2026-08-20",
            "ingredients": [
                {
                    "id": "chicken",
                    "name": "Chicken",
                    "calories": 280,
                    "protein_g": 52,
                    "carbs_g": 0,
                    "fat_g": 6,
                    "in_stock": False,
                }
            ],
        }
        plan = preview_meal_plan(
            oos,
            {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        )
        self.assertEqual(plan["items"], [])
        self.assertEqual(plan["meals"], [])
        self.assertEqual(plan["stocked_count"], 0)
        self.assertIn("No in-stock", plan["message"])

    def test_meal_plan_route_returns_same_planner(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ), mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=False
            ):
                status, body = meal_plan_body(_headers())
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["action"], "refresh_meal_plan")
        plan = body["plan"]
        self.assertTrue(plan.get("in_stock_only"))
        self.assertGreater(len(plan.get("items") or []), 0)

    def test_refresh_returns_dashboard_with_generated_plan(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ), mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=False
            ):
                status, body = refresh_body(_headers())
        self.assertEqual(status, 200)
        meal = (body.get("nutrition_store") or {}).get("meal_plan") or {}
        self.assertGreater(len(meal.get("items") or []), 0)
        self.assertTrue(meal.get("in_stock_only"))

    def test_preview_helper_matches_pi_planner(self):
        inv, _ = load_workspace_inventory()
        targets, _ = load_workspace_targets()
        from rt_dashboard.nutrition_planner import generate_meal_plan

        expected = generate_meal_plan(
            inv, targets, {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
        )
        got = preview_meal_plan(
            inv, targets, {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
        )
        self.assertEqual(got["items"], expected["items"])
        self.assertEqual(
            [m["label"] for m in got["meals"]],
            [m["label"] for m in expected["meals"]],
        )


if __name__ == "__main__":
    unittest.main()
