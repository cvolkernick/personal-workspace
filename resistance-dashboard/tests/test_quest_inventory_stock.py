"""Shopping quest complete marks matching pantry rows in stock."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import daily_tasks_complete_body
from rt_dashboard.quest_inventory_stock import (
    apply_shopping_quest_stock,
    attach_shopping_quest_stock,
    ingredient_id_from_slug,
    looks_like_shopping_quest,
    match_inventory_ingredient,
    shopping_name_from_title,
)

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
UTIL = (ROOT / "api" / "workout" / "_util.py").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")


def _headers():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _pantry(*rows):
    return {
        "ingredients": [
            {"id": iid, "name": name, "in_stock": in_stock}
            for iid, name, in_stock in rows
        ]
    }


class TitleAndSlug(unittest.TestCase):
    def test_restock_get_add_titles(self):
        self.assertEqual(shopping_name_from_title("Restock: Sweet potato"), "Sweet potato")
        self.assertEqual(shopping_name_from_title("Get: Roasted Almonds with Sea Salt"), "Roasted Almonds with Sea Salt")
        self.assertEqual(
            shopping_name_from_title(
                "Restock Tilapia — Out of stock and high protein density — restock for meal plans.."
            ),
            "Tilapia",
        )

    def test_meal_title_is_not_shopping_name(self):
        self.assertEqual(shopping_name_from_title("Next meal · 8:31 PM: Chicken Burrito Bowl"), "")

    def test_buy_slug_strips_prefix(self):
        self.assertEqual(
            ingredient_id_from_slug("buy-boneless-skinless-chicken-breast"),
            "boneless-skinless-chicken-breast",
        )
        self.assertEqual(ingredient_id_from_slug("shop-top"), "")

    def test_looks_like_shopping(self):
        self.assertTrue(looks_like_shopping_quest(group="shopping", title="Restock: Broccoli"))
        self.assertTrue(looks_like_shopping_quest(slug="buy-sweet-potato"))
        self.assertFalse(
            looks_like_shopping_quest(
                group="nutrition",
                title="Next meal: Oats · 80g",
                slug="meal-0-oats-0",
            )
        )


class MatchExisting(unittest.TestCase):
    def test_exact_id(self):
        inv = _pantry(("sweet-potato", "Sweet potato", False))
        hit = match_inventory_ingredient(inv, "Sweet potato", "sweet-potato")
        self.assertEqual(hit["id"], "sweet-potato")

    def test_branded_yogurt_name_hits_greek_yogurt(self):
        inv = _pantry(("greek-yogurt", "Great Value Plain Greek Nonfat Yogurt", False))
        hit = match_inventory_ingredient(
            inv,
            "Great Value Vanilla Greek Nonfat Yogurt",
            "great-value-vanilla-greek-nonfat-yogurt",
        )
        self.assertEqual(hit["id"], "greek-yogurt")

    def test_whey_slug_hits_shorter_id(self):
        inv = _pantry(("whey-protein", "Chocolate Whey Protein", False))
        hit = match_inventory_ingredient(inv, "Chocolate Whey Protein", "chocolate-whey-protein")
        self.assertEqual(hit["id"], "whey-protein")


class ApplyStock(unittest.TestCase):
    def test_restock_sets_in_stock(self):
        inv = _pantry(("broccoli", "Broccoli", False))
        updated, info = apply_shopping_quest_stock(
            completed=True,
            group="shopping",
            title="Restock: Broccoli",
            slug="buy-broccoli",
            inventory=inv,
        )
        self.assertTrue(info["wrote"])
        self.assertEqual(info["action"], "restock")
        self.assertTrue(
            next(i for i in updated["ingredients"] if i["id"] == "broccoli")["in_stock"]
        )

    def test_already_in_stock_is_dedupe(self):
        inv = _pantry(("broccoli", "Broccoli", True))
        updated, info = apply_shopping_quest_stock(
            completed=True,
            group="shopping",
            title="Restock: Broccoli",
            slug="buy-broccoli",
            inventory=inv,
        )
        self.assertIsNone(updated)
        self.assertEqual(info["action"], "dedupe")
        self.assertFalse(info["wrote"])

    def test_uncheck_does_not_mark_out(self):
        inv = _pantry(("broccoli", "Broccoli", True))
        updated, info = apply_shopping_quest_stock(
            completed=False,
            group="shopping",
            title="Restock: Broccoli",
            slug="buy-broccoli",
            inventory=inv,
        )
        self.assertIsNone(updated)
        self.assertEqual(info["reason"], "uncheck_noop")
        self.assertTrue(inv["ingredients"][0]["in_stock"])

    def test_missing_logged_food_is_added_in_stock(self):
        inv = _pantry(("broccoli", "Broccoli", False))
        updated, info = apply_shopping_quest_stock(
            completed=True,
            group="shopping",
            title="Get: Roasted Almonds with Sea Salt",
            slug="buy-roasted-almonds-with-sea-salt",
            inventory=inv,
        )
        self.assertEqual(info["action"], "add")
        self.assertTrue(info["wrote"])
        almonds = next(
            i for i in updated["ingredients"] if i["id"] == "roasted-almonds-with-sea-salt"
        )
        self.assertTrue(almonds["in_stock"])
        self.assertEqual(almonds["name"], "Roasted Almonds with Sea Salt")
        self.assertEqual(len(inv["ingredients"]), 1)

    def test_catalog_gap_uses_staple_macros(self):
        inv = {"ingredients": []}
        updated, info = apply_shopping_quest_stock(
            completed=True,
            group="shopping",
            title="Get: Whey protein",
            slug="buy-whey-protein",
            inventory=inv,
        )
        self.assertEqual(info["action"], "add")
        row = next(i for i in updated["ingredients"] if i["id"] == "whey-protein")
        self.assertTrue(row["in_stock"])
        self.assertGreater(row["protein_g"], 0)

    def test_generic_staples_prompt_is_not_added(self):
        inv = {"ingredients": []}
        updated, info = apply_shopping_quest_stock(
            completed=True,
            group="shopping",
            title="Get: High-protein staples",
            slug="buy-high-protein-staples",
            inventory=inv,
        )
        self.assertIsNone(updated)
        self.assertEqual(info["reason"], "not_found")

    def test_meal_ignored(self):
        inv = _pantry(("oats", "Oats", False))
        updated, info = apply_shopping_quest_stock(
            completed=True,
            group="nutrition",
            title="Next meal: Oats · 80g",
            slug="meal-0-oats-0",
            inventory=inv,
        )
        self.assertIsNone(updated)
        self.assertEqual(info["reason"], "not_shopping")
        self.assertFalse(inv["ingredients"][0]["in_stock"])


class Attach(unittest.TestCase):
    def test_writes_chris_row(self):
        store = {"sub-1": _pantry(("tilapia", "Tilapia", False))}
        saved = {}

        def load(uid):
            return store[uid], "turso"

        def save(inv, uid):
            saved["uid"] = uid
            store[uid] = inv
            return inv

        result = attach_shopping_quest_stock(
            {"ok": True, "task": {"title": "Restock Tilapia — Out of stock."}},
            {"group": "shopping", "slug": "shop-top"},
            True,
            user_id="sub-1",
            load_inventory=load,
            save_inventory=save,
        )
        self.assertTrue(result["inventory_stock"]["wrote"])
        self.assertEqual(saved["uid"], "sub-1")
        self.assertTrue(store["sub-1"]["ingredients"][0]["in_stock"])

    def test_default_uid_blocked(self):
        result = attach_shopping_quest_stock(
            {"ok": True},
            {"group": "shopping", "title": "Restock: Broccoli", "slug": "buy-broccoli"},
            True,
            user_id="default",
        )
        self.assertEqual(result["inventory_stock"]["reason"], "user_id_required")
        self.assertFalse(result["inventory_stock"]["wrote"])

    def test_save_failure_keeps_quest_ok(self):
        def load(_uid):
            return _pantry(("broccoli", "Broccoli", False)), "turso"

        def save(_inv, _uid):
            raise RuntimeError("turso env missing")

        result = attach_shopping_quest_stock(
            {"ok": True},
            {"group": "shopping", "title": "Restock: Broccoli", "slug": "buy-broccoli"},
            True,
            user_id="sub-1",
            load_inventory=load,
            save_inventory=save,
        )
        self.assertFalse(result["inventory_stock"]["ok"])
        self.assertFalse(result["inventory_stock"]["wrote"])
        self.assertIn("turso", result["inventory_stock"]["error"])


class CompleteRoute(unittest.TestCase):
    def test_shopping_complete_marks_stock(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        pantry = _pantry(("sweet-potato", "Sweet potato", False))

        def load(_uid):
            return pantry, "turso"

        def save(inv, uid):
            self.assertEqual(uid, "sub-1")
            pantry["ingredients"] = inv["ingredients"]
            return inv

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.daily_plan_tasks.complete_leaf",
                return_value={"ok": True, "task": {"id": "t-shop"}},
            ), mock.patch(
                "rt_dashboard.quest_inventory_stock.load_preview_inventory",
                load,
            ), mock.patch(
                "rt_dashboard.quest_inventory_stock.save_preview_inventory",
                save,
            ):
                status, body = daily_tasks_complete_body(
                    _headers(),
                    {
                        "list_id": "L1",
                        "task_id": "t-shop",
                        "completed": True,
                        "group": "shopping",
                        "title": "Restock: Sweet potato",
                        "slug": "buy-sweet-potato",
                    },
                )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertTrue(body["inventory_stock"]["wrote"])
        self.assertEqual(body["inventory_stock"]["action"], "restock")
        self.assertTrue(pantry["ingredients"][0]["in_stock"])

    def test_shopping_complete_adds_missing_food(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        pantry = {"ingredients": []}

        def load(_uid):
            return pantry, "turso"

        def save(inv, uid):
            self.assertEqual(uid, "sub-1")
            pantry["ingredients"] = inv["ingredients"]
            return inv

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.daily_plan_tasks.complete_leaf",
                return_value={"ok": True, "task": {"id": "t-shop"}},
            ), mock.patch(
                "rt_dashboard.quest_inventory_stock.load_preview_inventory",
                load,
            ), mock.patch(
                "rt_dashboard.quest_inventory_stock.save_preview_inventory",
                save,
            ):
                status, body = daily_tasks_complete_body(
                    _headers(),
                    {
                        "list_id": "L1",
                        "task_id": "t-shop",
                        "completed": True,
                        "group": "shopping",
                        "title": "Get: Roasted Almonds with Sea Salt",
                        "slug": "buy-roasted-almonds-with-sea-salt",
                    },
                )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["inventory_stock"]["action"], "add")
        self.assertTrue(body["inventory_stock"]["wrote"])
        self.assertEqual(len(pantry["ingredients"]), 1)
        self.assertTrue(pantry["ingredients"][0]["in_stock"])

    def test_uncheck_shopping_does_not_mark_out(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        pantry = _pantry(("sweet-potato", "Sweet potato", True))
        save = mock.Mock()
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.daily_plan_tasks.complete_leaf",
                return_value={"ok": True, "task": {"id": "t-shop"}},
            ), mock.patch(
                "rt_dashboard.quest_inventory_stock.load_preview_inventory",
                return_value=(pantry, "turso"),
            ), mock.patch(
                "rt_dashboard.quest_inventory_stock.save_preview_inventory",
                save,
            ):
                status, body = daily_tasks_complete_body(
                    _headers(),
                    {
                        "list_id": "L1",
                        "task_id": "t-shop",
                        "completed": False,
                        "group": "shopping",
                        "title": "Restock: Sweet potato",
                        "slug": "buy-sweet-potato",
                    },
                )
        self.assertEqual(status, 200, body)
        save.assert_not_called()
        self.assertEqual(body["inventory_stock"]["reason"], "uncheck_noop")
        self.assertTrue(pantry["ingredients"][0]["in_stock"])


class Wiring(unittest.TestCase):
    def test_complete_route_calls_attach(self):
        complete = UTIL.split("def daily_tasks_complete_body", 1)[1].split(
            "def inventory_write", 1
        )[0]
        self.assertIn("attach_shopping_quest_stock", complete)
        self.assertIn("attach_shopping_quest_stock", SERVER)
        self.assertIn("data.inventory_stock", JS)
        self.assertIn("applyInventoryUpdate(stock.inventory)", JS)
        self.assertIn("Quest checked, but pantry was not updated", JS)
