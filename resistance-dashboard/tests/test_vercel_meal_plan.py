"""Vercel Today meal plan uses Pi generate_meal_plan. Empty pantry stays empty."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.dashboard import dashboard_body, preview_inventory_carousels, preview_meal_plan
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
        self.assertGreaterEqual(len(meal["meals"]), 1)
        self.assertLessEqual(len(meal["meals"]), 4)
        stocked_names = {i["name"] for i in stocked}
        for it in meal["items"]:
            self.assertIn(it["name"], stocked_names)
        for bucket in meal["meals"]:
            self.assertTrue(bucket.get("items"))
            self.assertTrue(bucket.get("eat_at"))
            self.assertTrue(bucket.get("eat_at_label"))
            self.assertIn("T", str(bucket["eat_at"]))
            for it in bucket["items"]:
                self.assertIn(it["name"], stocked_names)
                if it.get("portion_g") is not None:
                    self.assertGreater(it["portion_g"], 0)
        self.assertNotIn("Connect SuperGrok", meal.get("message") or "")
        nut = body.get("nutrition_store") or {}
        self.assertIn("inventory_suggestions", nut)
        self.assertIn("inventory_removals", nut)
        self.assertIsInstance((nut["inventory_suggestions"] or {}).get("suggestions"), list)
        self.assertIsInstance((nut["inventory_removals"] or {}).get("suggestions"), list)

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
        self.assertFalse(any((m or {}).get("eat_at") for m in meal.get("meals") or []))
        self.assertNotIn("Connect SuperGrok", meal.get("message") or "")
        nut = body.get("nutrition_store") or {}
        self.assertEqual((nut.get("inventory_suggestions") or {}).get("suggestions"), [])
        self.assertEqual((nut.get("inventory_removals") or {}).get("suggestions"), [])
        self.assertEqual((nut.get("inventory_suggestions") or {}).get("count"), 0)
        self.assertEqual((nut.get("inventory_removals") or {}).get("count"), 0)

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


class InventoryCarouselsOnDashboard(unittest.TestCase):
    def test_helper_empty_pantry_stays_empty_even_with_food_logs(self):
        logs = [
            {"date": "2026-08-22", "name": "Unicorn steak", "calories": 900, "protein_g": 80},
            {"date": "2026-08-21", "name": "Unicorn steak", "calories": 900, "protein_g": 80},
        ]
        sug, rem = preview_inventory_carousels(
            {"ingredients": []},
            {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            food_logs=logs,
            consumed={"calories": 0, "protein_g": 0},
        )
        self.assertEqual(sug.get("suggestions"), [])
        self.assertEqual(rem.get("suggestions"), [])
        self.assertEqual(sug.get("count"), 0)
        self.assertEqual(rem.get("count"), 0)
        blob = json.dumps({"suggestions": sug, "removals": rem}).lower()
        self.assertNotIn("unicorn", blob)
        self.assertNotIn("chicken breast", blob)
        self.assertNotIn("greek yogurt", blob)

    def test_helper_restocks_existing_oos_and_does_not_fabricate_removals(self):
        inv = {
            "ingredients": [
                {
                    "id": "chicken",
                    "name": "Chicken",
                    "category": "protein",
                    "serving_label": "6 oz",
                    "calories": 280,
                    "protein_g": 52,
                    "carbs_g": 0,
                    "fat_g": 6,
                    "in_stock": False,
                },
                {
                    "id": "chicken-dup",
                    "name": "Chicken breast",
                    "category": "protein",
                    "serving_label": "8 oz",
                    "calories": 300,
                    "protein_g": 50,
                    "carbs_g": 0,
                    "fat_g": 7,
                    "in_stock": True,
                },
            ]
        }
        pantry_names = {i["name"].lower() for i in inv["ingredients"]}
        sug, rem = preview_inventory_carousels(
            inv,
            {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            food_logs=[],
            consumed={"calories": 0, "protein_g": 0},
        )
        self.assertTrue(sug.get("suggestions"))
        restocks = [s for s in sug["suggestions"] if s.get("action") == "restock"]
        self.assertTrue(restocks)
        for s in restocks:
            self.assertIn(str(s.get("name") or "").lower(), pantry_names)
        for s in rem.get("suggestions") or []:
            self.assertIn(str(s.get("name") or "").lower(), pantry_names)
            self.assertEqual(s.get("action"), "remove")
        for s in sug["suggestions"]:
            self.assertNotEqual((s.get("name") or "").lower(), "unicorn steak")

    def test_signed_in_dashboard_sets_both_carousel_keys(self):
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
        nut = body.get("nutrition_store") or {}
        sug = nut.get("inventory_suggestions") or {}
        rem = nut.get("inventory_removals") or {}
        self.assertIsInstance(sug.get("suggestions"), list)
        self.assertIsInstance(rem.get("suggestions"), list)
        self.assertIn("count", sug)
        self.assertIn("count", rem)
        pantry = {
            str(i.get("name") or "").lower()
            for i in (nut.get("inventory") or {}).get("ingredients") or []
        }
        self.assertGreaterEqual(len(pantry), 1)
        for s in rem.get("suggestions") or []:
            self.assertIn(str(s.get("name") or "").lower(), pantry)
        for s in sug.get("suggestions") or []:
            if s.get("action") == "restock":
                self.assertIn(str(s.get("name") or "").lower(), pantry)

    def test_empty_pantry_dashboard_does_not_invent_staples(self):
        empty = {"updated_at": "2026-08-22", "ingredients": []}
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
        nut = body.get("nutrition_store") or {}
        sug = nut.get("inventory_suggestions") or {}
        rem = nut.get("inventory_removals") or {}
        self.assertEqual(sug.get("suggestions"), [])
        self.assertEqual(rem.get("suggestions"), [])
        today = ((body.get("coach") or {}).get("today") or {})
        purchases = today.get("purchases") or today.get("inventory_purchases") or []
        names = " ".join(str(p.get("name") or "") for p in purchases).lower()
        self.assertNotIn("chicken", names)
        self.assertNotIn("unicorn", names)
        self.assertNotIn("high-protein staples", names)


if __name__ == "__main__":
    unittest.main()
