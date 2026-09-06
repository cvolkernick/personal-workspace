"""#491 pantry stock: in | low | out."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.auth.session_util import SESSION_COOKIE, make_session  # noqa: E402
from api.workout._util import inventory_write  # noqa: E402
from rt_dashboard.coach_actions import (  # noqa: E402
    format_action_reply,
    try_parse_coach_action,
)
from rt_dashboard.nutrition_planner import (  # noqa: E402
    STOCK_IN,
    STOCK_LOW,
    STOCK_OUT,
    generate_meal_plan,
    is_in_stock,
    needs_restock,
    normalize_ingredient,
    normalize_stock,
    set_in_stock,
    set_stock,
    stock_from_write_payload,
    stocked_ingredients,
    suggest_inventory_staples,
)
from rt_dashboard.quest_inventory_stock import apply_shopping_quest_stock  # noqa: E402

JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")
UTIL = (ROOT / "api" / "workout" / "_util.py").read_text(encoding="utf-8")


def _row(iid, name, *, stock=None, in_stock=None, **extra):
    row = {
        "id": iid,
        "name": name,
        "serving_g": 100,
        "serving_label": "100g",
        "calories": 120,
        "protein_g": 20,
        "carbs_g": 4,
        "fat_g": 2,
    }
    if stock is not None:
        row["stock"] = stock
    if in_stock is not None:
        row["in_stock"] = in_stock
    row.update(extra)
    return row


class NormalizeStock(unittest.TestCase):
    def test_compat_missing_and_bool(self):
        self.assertEqual(normalize_stock({}), STOCK_IN)
        self.assertEqual(normalize_stock({"in_stock": True}), STOCK_IN)
        self.assertEqual(normalize_stock({"in_stock": False}), STOCK_OUT)
        self.assertEqual(normalize_stock({"in_stock": 0}), STOCK_OUT)
        self.assertEqual(normalize_stock({"in_stock": "false"}), STOCK_OUT)

    def test_explicit_stock_wins(self):
        self.assertEqual(normalize_stock({"stock": "low"}), STOCK_LOW)
        self.assertEqual(normalize_stock({"stock": "low", "in_stock": False}), STOCK_LOW)
        self.assertEqual(normalize_stock({"stock": "out", "in_stock": True}), STOCK_OUT)

    def test_meals_use_low_restock_uses_low_and_out(self):
        low = {"stock": "low"}
        out = {"in_stock": False}
        inn = {"in_stock": True}
        self.assertTrue(is_in_stock(low))
        self.assertTrue(needs_restock(low))
        self.assertFalse(is_in_stock(out))
        self.assertTrue(needs_restock(out))
        self.assertTrue(is_in_stock(inn))
        self.assertFalse(needs_restock(inn))

    def test_normalize_ingredient_derives_in_stock(self):
        low = normalize_ingredient(_row("yogurt", "Yogurt", stock="low"))
        self.assertEqual(low["stock"], "low")
        self.assertTrue(low["in_stock"])
        out = normalize_ingredient(_row("candy", "Candy", in_stock=False))
        self.assertEqual(out["stock"], "out")
        self.assertFalse(out["in_stock"])

    def test_write_payload_stock_and_bool(self):
        self.assertEqual(stock_from_write_payload({"stock": "low"}), STOCK_LOW)
        self.assertEqual(stock_from_write_payload({"in_stock": False}), STOCK_OUT)
        self.assertEqual(stock_from_write_payload({"in_stock": True}), STOCK_IN)
        self.assertEqual(stock_from_write_payload({}), STOCK_IN)
        with self.assertRaises(ValueError):
            stock_from_write_payload({"stock": "grams"})


class MealAndRestock(unittest.TestCase):
    def test_stocked_ingredients_include_low_exclude_out(self):
        inv = {
            "ingredients": [
                _row("chicken", "Chicken", stock="in"),
                _row("yogurt", "Yogurt", stock="low"),
                _row("candy", "Candy", stock="out"),
            ]
        }
        names = {i["name"] for i in stocked_ingredients(inv)}
        self.assertEqual(names, {"Chicken", "Yogurt"})

    def test_meal_plan_uses_low_not_out(self):
        inv = {
            "ingredients": [
                _row("chicken", "Chicken", stock="low", calories=280, protein_g=52, carbs_g=0, fat_g=6),
                _row("candy", "Candy", stock="out", calories=250, protein_g=1, carbs_g=40, fat_g=10),
            ]
        }
        plan = generate_meal_plan(
            inv,
            {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        )
        names = {i["name"] for i in plan["items"]}
        self.assertIn("Chicken", names)
        self.assertNotIn("Candy", names)

    def test_suggestions_restock_low_and_out_not_in(self):
        inv = {
            "ingredients": [
                _row("chicken", "Chicken", stock="in", calories=280, protein_g=52),
                _row("yogurt", "Yogurt", stock="low", calories=130, protein_g=20),
                _row("eggs", "Eggs", stock="out", calories=140, protein_g=12),
            ]
        }
        out = suggest_inventory_staples(
            inv,
            targets={"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            food_logs=[],
            consumed={"calories": 0, "protein_g": 0},
            max_suggestions=10,
        )
        restocks = [s for s in out["suggestions"] if s.get("action") == "restock"]
        names = {s["name"] for s in restocks}
        self.assertIn("Yogurt", names)
        self.assertIn("Eggs", names)
        self.assertNotIn("Chicken", names)
        yogurt = next(s for s in restocks if s["name"] == "Yogurt")
        self.assertNotIn("out of stock", yogurt["reason"].lower())
        self.assertIn("low", yogurt["reason"].lower())
        eggs = next(s for s in restocks if s["name"] == "Eggs")
        self.assertIn("out of stock", eggs["reason"].lower())

    def test_set_stock_and_bool_compat(self):
        inv = {"ingredients": [_row("oats", "Oats", in_stock=True)]}
        low = set_stock(inv, "oats", "low")
        oats = low["ingredients"][0]
        self.assertEqual(oats["stock"], "low")
        self.assertTrue(oats["in_stock"])
        out = set_in_stock(low, "oats", False)
        oats = out["ingredients"][0]
        self.assertEqual(oats["stock"], "out")
        self.assertFalse(oats["in_stock"])
        inn = set_in_stock(out, "oats", True)
        oats = inn["ingredients"][0]
        self.assertEqual(oats["stock"], "in")
        self.assertTrue(oats["in_stock"])


class QuestComplete(unittest.TestCase):
    def test_restock_low_marks_in(self):
        inv = {"ingredients": [_row("broccoli", "Broccoli", stock="low")]}
        updated, info = apply_shopping_quest_stock(
            completed=True,
            group="shopping",
            title="Restock: Broccoli",
            slug="buy-broccoli",
            inventory=inv,
        )
        self.assertTrue(info["wrote"])
        self.assertEqual(info["action"], "restock")
        row = next(i for i in updated["ingredients"] if i["id"] == "broccoli")
        self.assertEqual(row["stock"], "in")
        self.assertTrue(row["in_stock"])

    def test_already_in_is_still_dedupe(self):
        inv = {"ingredients": [_row("broccoli", "Broccoli", stock="in")]}
        updated, info = apply_shopping_quest_stock(
            completed=True,
            group="shopping",
            title="Restock: Broccoli",
            slug="buy-broccoli",
            inventory=inv,
        )
        self.assertIsNone(updated)
        self.assertEqual(info["action"], "dedupe")


class CoachParse(unittest.TestCase):
    def test_mark_low_phrases(self):
        for q in (
            "mark yogurt low",
            "mark yogurt running low",
            "mark yogurt low inventory",
            "set stock yogurt low",
            "yogurt is running low",
        ):
            a = try_parse_coach_action(q)
            self.assertIsNotNone(a, q)
            self.assertEqual(a["action"], "set_stock", q)
            self.assertEqual(a["stock"], "low", q)
            self.assertTrue(a["in_stock"], q)
            self.assertIn("yogurt", a["id_or_name"])

    def test_mark_out_still_works(self):
        a = try_parse_coach_action("mark chicken out of stock")
        self.assertEqual(a["stock"], "out")
        self.assertFalse(a["in_stock"])

    def test_reply_says_low(self):
        msg = format_action_reply(
            {"ok": True, "action": "set_stock", "name": "Yogurt", "stock": "low"}
        )
        self.assertIn("low", msg.lower())
        self.assertNotIn("out of stock", msg.lower())


class KitchenApi(unittest.TestCase):
    def _headers(self):
        token = make_session(
            {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
        )
        return {"Cookie": f"{SESSION_COOKIE}={token}"}

    def test_post_stock_low_persists(self):
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
                    {"id": "oats", "stock": "low"},
                )
        self.assertEqual(status, 200)
        oats = next(i for i in body["inventory"]["ingredients"] if i["id"] == "oats")
        self.assertEqual(oats["stock"], "low")
        self.assertTrue(oats["in_stock"])

    def test_invalid_stock_is_400(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_get_inventory",
                return_value={"ingredients": [_row("oats", "Oats", in_stock=True)]},
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_put_inventory",
                side_effect=lambda uid, inv: inv,
            ):
                status, body = inventory_write(
                    self._headers(),
                    "inv_stock",
                    {"id": "oats", "stock": "grams"},
                )
        self.assertEqual(status, 400)
        self.assertFalse(body.get("ok", True))


class Surfaces(unittest.TestCase):
    def test_kitchen_three_way_control(self):
        self.assertIn("inv-stock-seg", JS)
        self.assertIn('data-stock="in"', JS)
        self.assertIn('data-stock="low"', JS)
        self.assertIn('data-stock="out"', JS)
        self.assertIn("(low)", JS)
        self.assertIn("(out)", JS)
        self.assertNotIn("Mark out", JS)
        self.assertIn(".inv-stock-seg", CSS)
        self.assertIn("stock_from_write_payload", SERVER)
        self.assertIn("stock_from_write_payload", UTIL)

    def test_restock_filter_keeps_low(self):
        self.assertIn('if (match.stock === "in") return false', JS)
        self.assertIn("ingredientStock", JS)


if __name__ == "__main__":
    unittest.main()
