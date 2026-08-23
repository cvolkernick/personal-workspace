"""Prove Vercel pantry is inventory.json + Turso seed/persist. No invented items."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.dashboard import dashboard_body
from api.workout._util import dispatch_client_route, inventory_write
from rt_dashboard.grok_planner import clamp_meal_to_stock, generate_grok_plans
from rt_dashboard.inventory_store import (
    INVENTORY_PATH,
    load_preview_inventory,
    load_workspace_inventory,
    save_preview_inventory,
)
from rt_dashboard.nutrition_planner import generate_meal_plan, stocked_ingredients
from rt_dashboard.models import HealthSnapshot

REPO_INV = Path(__file__).resolve().parents[2] / INVENTORY_PATH
BUNDLE_INV = Path(__file__).resolve().parents[1] / INVENTORY_PATH
DASHBOARD_PY = Path(__file__).resolve().parents[1] / "api" / "dashboard.py"
VERCEL_JSON = Path(__file__).resolve().parents[1] / "vercel.json"
ROOT = Path(__file__).resolve().parents[1]


class BundledInventoryFile(unittest.TestCase):
    def test_repo_file_has_15_real_ingredients(self):
        raw = json.loads(REPO_INV.read_text(encoding="utf-8"))
        self.assertEqual(raw["updated_at"], "2026-08-12")
        ings = raw["ingredients"]
        self.assertEqual(len(ings), 15)
        names = [i["name"] for i in ings]
        self.assertIn("Tilapia", names)
        self.assertIn("Egg whites", names)
        self.assertIn("Boneless skinless chicken breast", names)
        self.assertNotIn("unset", json.dumps(raw))

    def test_bundle_copy_matches_repo_file(self):
        self.assertTrue(BUNDLE_INV.is_file(), BUNDLE_INV)
        self.assertEqual(BUNDLE_INV.read_bytes(), REPO_INV.read_bytes())

    def test_loader_reads_file_not_empty_stub(self):
        inv, source = load_workspace_inventory()
        raw = json.loads(REPO_INV.read_text(encoding="utf-8"))
        self.assertEqual(source, INVENTORY_PATH)
        self.assertEqual(len(inv["ingredients"]), 15)
        self.assertEqual(inv["updated_at"], raw["updated_at"])
        self.assertEqual(
            [i["name"] for i in inv["ingredients"]],
            [i["name"] for i in raw["ingredients"]],
        )

    def test_vercel_bundle_path_alone_is_enough(self):
        from rt_dashboard import inventory_store as ins

        with mock.patch.object(ins, "_inventory_file_candidates", return_value=[BUNDLE_INV]):
            inv, source = ins.load_workspace_inventory()
        self.assertEqual(source, INVENTORY_PATH)
        self.assertEqual(len(inv["ingredients"]), 15)

    def test_dashboard_empty_inventory_stub_gone(self):
        text = DASHBOARD_PY.read_text(encoding="utf-8")
        self.assertNotIn('"inventory": {"ingredients": []}', text)
        self.assertNotIn("'inventory': {'ingredients': []}", text)
        self.assertNotIn('"inventory": "unset"', text)
        self.assertNotIn("unset", text.split("nutrition_store")[1].split("meal_plan")[0] if "nutrition_store" in text else "")
        self.assertIn("load_preview_inventory", text)

    def test_include_files_lists_inventory(self):
        raw = VERCEL_JSON.read_text(encoding="utf-8")
        self.assertIn("fitness/nutrition/inventory.json", raw)
        self.assertIn("/api/inventory/add", raw)
        self.assertIn("/api/inventory/update", raw)
        self.assertIn("/api/inventory/remove", raw)
        self.assertIn("/api/inventory/stock", raw)
        self.assertIn("/api/dashboard?_r=inv_add", raw)
        self.assertIn("/api/dashboard?_r=inv_update", raw)
        self.assertNotIn("api/inventory.py", raw)
        self.assertFalse((ROOT / "api" / "inventory.py").exists())
        self.assertFalse((ROOT / "api" / "inventory").is_dir())
        self.assertFalse((ROOT / "api" / "inventory" / "update.py").exists())


class EmptyTursoSeedsFromFile(unittest.TestCase):
    def test_empty_turso_seeds_and_source_is_turso(self):
        puts = []

        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled",
            return_value=True,
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_get_inventory",
            return_value=None,
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_put_inventory",
            side_effect=lambda uid, inv: puts.append((uid, inv)),
        ):
            inv, src = load_preview_inventory("sub-1")
        self.assertEqual(src, "turso")
        self.assertNotEqual(src, "unset")
        self.assertEqual(len(inv["ingredients"]), 15)
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][0], "sub-1")
        self.assertEqual(len(puts[0][1]["ingredients"]), 15)

    def test_existing_turso_row_is_sot_not_reseeded(self):
        stored = {
            "updated_at": "2026-08-19",
            "ingredients": [
                {
                    "id": "egg-whites",
                    "name": "Egg whites",
                    "in_stock": False,
                    "calories": 125,
                    "protein_g": 26,
                    "carbs_g": 2,
                    "fat_g": 0,
                }
            ],
        }
        puts = []
        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_get_inventory",
            return_value=stored,
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_put_inventory",
            side_effect=lambda uid, inv: puts.append((uid, inv)),
        ):
            inv, src = load_preview_inventory("sub-1")
        self.assertEqual(src, "turso")
        self.assertEqual(len(inv["ingredients"]), 1)
        self.assertFalse(inv["ingredients"][0]["in_stock"])
        self.assertEqual(puts, [])


class KitchenWrites(unittest.TestCase):
    def _headers(self):
        token = make_session(
            {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
        )
        return {"Cookie": f"{SESSION_COOKIE}={token}"}

    def test_cookie_less_write_is_401_json(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = inventory_write({}, "inv_stock", {"id": "oats", "in_stock": False})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertFalse(body.get("ok", True))
        self.assertNotIn("<html", json.dumps(body).lower())
        self.assertNotIn("inventory", body)

    def test_dispatch_cookie_less_401_on_inventory_paths(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for path, route in (
                ("/api/inventory/add", "inv_add"),
                ("/api/inventory/update", "inv_update"),
                ("/api/inventory/remove", "inv_remove"),
                ("/api/inventory/stock", "inv_stock"),
            ):
                status, body = dispatch_client_route({}, "", "POST", payload={}, path=path)
                self.assertEqual(status, 401, route)
                self.assertEqual(body["error"], "auth_required")
                status, body = dispatch_client_route({}, f"_r={route}", "POST", payload={})
                self.assertEqual(status, 401, route)

    def test_signed_in_stock_toggle_persists_to_turso(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        store = {}

        def get(uid):
            return store.get(uid)

        def put(uid, inv):
            store[uid] = inv

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_get_inventory",
                side_effect=get,
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_put_inventory",
                side_effect=put,
            ):
                status, body = inventory_write(
                    self._headers(),
                    "inv_stock",
                    {"id": "oats", "in_stock": False},
                )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        oats = next(i for i in body["inventory"]["ingredients"] if i["id"] == "oats")
        self.assertFalse(oats["in_stock"])
        self.assertIn("sub-1", store)
        self.assertTrue(body["write"]["ok"])
        self.assertEqual(body["write"]["source"], "turso")

    def test_write_fails_honest_when_turso_down(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_get_inventory",
                return_value=None,
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_put_inventory",
                side_effect=RuntimeError("turso HTTP 500"),
            ):
                status, body = inventory_write(
                    self._headers(),
                    "inv_stock",
                    {"id": "oats", "in_stock": False},
                )
        self.assertEqual(status, 500)
        self.assertFalse(body["ok"])
        self.assertIn("turso", str(body.get("error") or "").lower())
        self.assertNotIn("inventory", body)

    def test_write_fails_honest_when_turso_unset(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=False
            ):
                status, body = inventory_write(
                    self._headers(),
                    "inv_add",
                    {
                        "name": "Test only",
                        "category": "protein",
                        "calories": 1,
                        "protein_g": 1,
                        "carbs_g": 0,
                        "fat_g": 0,
                        "in_stock": True,
                    },
                )
        self.assertIn(status, (500, 503))
        self.assertFalse(body["ok"])
        self.assertTrue(body.get("error"))

    def _seeded_store(self):
        return {
            "updated_at": "2026-08-19",
            "ingredients": [
                {
                    "id": "oats",
                    "name": "Oats",
                    "category": "carb",
                    "serving_g": 40,
                    "serving_label": "40g dry",
                    "calories": 150,
                    "protein_g": 5,
                    "carbs_g": 27,
                    "fat_g": 3,
                    "in_stock": True,
                },
                {
                    "id": "egg-whites",
                    "name": "Egg whites",
                    "category": "protein",
                    "calories": 125,
                    "protein_g": 26,
                    "carbs_g": 2,
                    "fat_g": 0,
                    "in_stock": True,
                },
            ],
        }

    def test_signed_in_update_persists_to_turso(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        store = {"sub-1": self._seeded_store()}
        puts = []

        def get(uid):
            return store.get(uid)

        def put(uid, inv):
            puts.append((uid, inv))
            store[uid] = inv

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_get_inventory",
                side_effect=get,
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_put_inventory",
                side_effect=put,
            ):
                status, body = inventory_write(
                    self._headers(),
                    "inv_update",
                    {
                        "id": "oats",
                        "name": "Rolled oats",
                        "category": "carb",
                        "serving_g": 80,
                        "serving_label": "dry",
                        "calories": 300,
                        "protein_g": 10,
                        "carbs_g": 54,
                        "fat_g": 6,
                    },
                )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        oats = next(i for i in body["inventory"]["ingredients"] if i["id"] == "oats")
        self.assertEqual(oats["name"], "Rolled oats")
        self.assertEqual(oats["calories"], 300.0)
        self.assertEqual(oats["serving_g"], 80.0)
        self.assertEqual(len(body["inventory"]["ingredients"]), 2)
        self.assertTrue(puts)
        self.assertEqual(puts[-1][0], "sub-1")
        self.assertTrue(body["write"]["ok"])
        self.assertEqual(body["write"]["source"], "turso")

    def test_update_unknown_id_is_honest_error_not_invented(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        seed = self._seeded_store()
        store = {"sub-1": seed}
        puts = []

        def get(uid):
            return store.get(uid)

        def put(uid, inv):
            puts.append((uid, inv))
            store[uid] = inv

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_get_inventory",
                side_effect=get,
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_put_inventory",
                side_effect=put,
            ):
                status, body = inventory_write(
                    self._headers(),
                    "inv_update",
                    {
                        "id": "unicorn-steak",
                        "name": "Unicorn steak",
                        "calories": 900,
                        "protein_g": 80,
                        "carbs_g": 0,
                        "fat_g": 40,
                    },
                )
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("not found", str(body.get("error") or "").lower())
        self.assertNotIn("inventory", body)
        self.assertEqual(puts, [])
        self.assertEqual(len(store["sub-1"]["ingredients"]), 2)
        names = [i["name"] for i in store["sub-1"]["ingredients"]]
        self.assertNotIn("Unicorn steak", names)

    def test_dispatch_update_path_uses_existing_dashboard_function(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            status, body = dispatch_client_route(
                {}, "", "POST", payload={"id": "oats"}, path="/api/inventory/update"
            )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")

    def test_logged_serving_add_without_grams_is_400(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        store = {"sub-1": self._seeded_store()}
        puts = []

        def get(uid):
            return store.get(uid)

        def put(uid, inv):
            puts.append((uid, inv))
            store[uid] = inv

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_get_inventory",
                side_effect=get,
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_put_inventory",
                side_effect=put,
            ):
                status, body = inventory_write(
                    self._headers(),
                    "inv_add",
                    {
                        "name": "Chicken breast",
                        "serving_label": "1 logged serving (avg)",
                        "calories": 389.2,
                        "protein_g": 72.8,
                        "carbs_g": 0,
                        "fat_g": 7.3,
                        "in_stock": True,
                    },
                )
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("serving grams required", str(body.get("error") or "").lower())
        self.assertNotIn("inventory", body)
        self.assertEqual(puts, [])
        names = [i["name"] for i in store["sub-1"]["ingredients"]]
        self.assertNotIn("Chicken breast", names)

    def test_logged_serving_add_with_user_grams_persists(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        store = {"sub-1": self._seeded_store()}
        puts = []

        def get(uid):
            return store.get(uid)

        def put(uid, inv):
            puts.append((uid, inv))
            store[uid] = inv

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_get_inventory",
                side_effect=get,
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_put_inventory",
                side_effect=put,
            ):
                status, body = inventory_write(
                    self._headers(),
                    "inv_add",
                    {
                        "name": "Chicken breast",
                        "serving_label": "1 logged serving (avg)",
                        "serving_g": 100,
                        "calories": 110,
                        "protein_g": 23,
                        "carbs_g": 0,
                        "fat_g": 1.2,
                        "in_stock": True,
                    },
                )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        row = next(
            i for i in body["inventory"]["ingredients"] if i["name"] == "Chicken breast"
        )
        self.assertEqual(row["serving_g"], 100.0)
        self.assertTrue(str(row["serving_label"]).startswith("100g"))
        self.assertTrue(puts)


class InventoryEditUi(unittest.TestCase):
    def test_row_has_edit_and_cancel_does_not_write(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-action="edit"', js)
        self.assertIn(">Edit</button>", js)
        self.assertIn('data-action="edit-save"', js)
        self.assertIn('data-action="edit-cancel"', js)
        self.assertIn('fetch("/api/inventory/update"', js)
        self.assertIn("function cancelInventoryEdit", js)
        cancel = js.split("function cancelInventoryEdit()", 1)[1].split("function ", 1)[0]
        self.assertNotIn("fetch(", cancel)
        self.assertNotIn("/api/inventory/update", cancel)
        self.assertIn("inventoryEditId = null", cancel)
        self.assertIn('ev.key !== "Escape"', js)
        escape = js.split('ev.key !== "Escape"', 1)[1].split("root.addEventListener", 1)[0]
        self.assertIn("cancelInventoryEdit()", escape)
        self.assertNotIn("fetch(", escape)
        self.assertIn("Ingredient Inventory", html)
        self.assertIn(".inv-edit-form", css)
        self.assertIn(".btn-edit", css)
        self.assertIn("function servingGramsRequired", js)
        self.assertIn("function itemNeedsServingGrams", js)
        self.assertIn("function showSuggestionGramsPrompt", js)
        self.assertIn("suggest-grams-save", js)
        self.assertIn("logged serving", js)
        self.assertIn("serving_grams_nudge", js)
        self.assertIn("ing-serving-g", html)
        self.assertIn('id="ing-serving-g"', html)
        self.assertIn("required", html.split('id="ing-serving-g"', 1)[1].split(">", 1)[0])
        self.assertIn(".inv-grams-prompt", css)
        self.assertIn(".meal-grams-nudge", css)
        self.assertNotIn("CIC", js)
        self.assertNotIn("CIC", html)


class DashboardInventoryPayload(unittest.TestCase):
    def test_signed_in_payload_has_file_or_turso_inventory(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ), mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=False
            ):
                status, body = dashboard_body(headers)
        self.assertEqual(status, 200)
        nut = body["nutrition_store"]
        self.assertEqual(nut["sources"]["inventory"], INVENTORY_PATH)
        self.assertNotEqual(nut["sources"]["inventory"], "unset")
        self.assertEqual(len(nut["inventory"]["ingredients"]), 15)


class MealPlanInStockOnly(unittest.TestCase):
    def test_local_planner_skips_out_of_stock_from_file(self):
        inv, src = load_workspace_inventory()
        self.assertEqual(src, INVENTORY_PATH)
        plan = generate_meal_plan(
            inv,
            {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        )
        self.assertTrue(plan.get("in_stock_only"))
        stocked_names = {i["name"] for i in stocked_ingredients(inv)}
        out_names = {i["name"] for i in inv["ingredients"] if i.get("in_stock") is False}
        for it in plan["items"]:
            self.assertIn(it["name"], stocked_names)
            self.assertNotIn(it["name"], out_names)
        self.assertNotIn("Tilapia", {i["name"] for i in plan["items"]})

    def test_clamp_drops_invented_and_off_stock_items(self):
        inv, _ = load_workspace_inventory()
        meal = {
            "items": [
                {"name": "Tilapia", "calories": 220},
                {"name": "Unicorn steak", "calories": 900},
                {"name": "Egg whites", "calories": 125},
            ],
            "meals": [
                {
                    "name": "Dinner",
                    "items": [
                        {"name": "Tilapia"},
                        {"name": "Egg whites"},
                    ],
                }
            ],
        }
        out = clamp_meal_to_stock(meal, inv)
        names = {i["name"] for i in out["items"]}
        self.assertIn("Egg whites", names)
        self.assertNotIn("Tilapia", names)
        self.assertNotIn("Unicorn steak", names)
        self.assertTrue(out["in_stock_only"])
        dinner = out["meals"][0]["items"]
        self.assertEqual([i["name"] for i in dinner], ["Egg whites"])

    def test_clamp_scales_continuous_grams_from_inventory(self):
        inv = {
            "ingredients": [
                {
                    "id": "chicken",
                    "name": "Chicken",
                    "serving_g": 100,
                    "serving_label": "100g",
                    "calories": 110,
                    "protein_g": 23,
                    "carbs_g": 0,
                    "fat_g": 1.2,
                    "in_stock": True,
                }
            ]
        }
        meal = {
            "items": [
                {
                    "name": "Chicken",
                    "portion_g": 250,
                    "calories": 999,
                    "protein_g": 1,
                }
            ],
            "meals": [
                {
                    "label": "Next meal",
                    "items": [{"name": "Chicken", "portion_g": 250, "calories": 999}],
                }
            ],
        }
        out = clamp_meal_to_stock(meal, inv)
        row = out["items"][0]
        self.assertEqual(row["portion_g"], 250)
        self.assertEqual(row["servings"], 2.5)
        self.assertEqual(row["calories"], 275.0)
        self.assertEqual(row["protein_g"], 57.5)
        self.assertEqual(row["serving_label"], "250g")
        slot = out["meals"][0]["items"][0]
        self.assertEqual(slot["portion_g"], 250)
        self.assertEqual(slot["calories"], 275.0)

    def test_clamp_does_not_invent_grams_without_serving_g(self):
        inv = {
            "ingredients": [
                {
                    "id": "eggs",
                    "name": "Whole eggs",
                    "serving_label": "3 eggs",
                    "calories": 210,
                    "protein_g": 18,
                    "carbs_g": 2,
                    "fat_g": 15,
                    "in_stock": True,
                }
            ]
        }
        out = clamp_meal_to_stock(
            {
                "items": [{"name": "Whole eggs", "portion_g": 250, "calories": 400}],
                "meals": [],
            },
            inv,
        )
        row = out["items"][0]
        self.assertNotIn("portion_g", row)
        self.assertNotIn("serving_g", row)
        self.assertIn("egg", str(row.get("serving_label") or "").lower())

    def test_grok_generate_clamps_off_stock(self):
        inv, _ = load_workspace_inventory()

        def fake_chat(messages, **kwargs):
            return {
                "answer": json.dumps(
                    {
                        "meal": {
                            "message": "invented",
                            "items": [
                                {"name": "Unicorn steak", "calories": 900},
                                {"name": "Egg whites", "calories": 125},
                            ],
                            "meals": [],
                        },
                        "workout": {
                            "session_type": "pull",
                            "is_rest_day": False,
                            "message": "ok",
                            "exercises": [],
                        },
                    }
                ),
                "model": "grok-test",
                "auth_source": "supergrok_session",
            }

        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={
                "token": "user-token-must-not-leak",
                "source": "supergrok_session",
                "expired": False,
            },
        ), mock.patch(
            "rt_dashboard.grok_ask.chat_completions",
            side_effect=fake_chat,
        ):
            out = generate_grok_plans("user-1", inventory=inv)
        names = {i["name"] for i in out["meal"]["items"]}
        self.assertIn("Egg whites", names)
        self.assertNotIn("Unicorn steak", names)
        self.assertTrue(out["meal"]["in_stock_only"])
        self.assertGreater(out["meal"]["stocked_count"], 0)


class HobbyCountUnchanged(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
