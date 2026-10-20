"""Weekly grocery list: qty, purchase units, and a failed batch write (#1288)."""

from __future__ import annotations

import unittest
from unittest import mock

from api.workout._util import meal_grocery_body, recipes_batch_body
from rt_dashboard.grocery_list import (
    CHECK_STAPLES,
    CHECK_UNKNOWN,
    assemble_week,
    lines_from_plans,
    sum_recipe_grams,
)
from rt_dashboard.recipe_store import load_nourish_recipes


def _meal(ingredient_id, grams, name=None):
    line = {"ingredient_id": ingredient_id, "grams_batch": grams}
    if name:
        line["name"] = name
    rec = {
        "id": "bowl",
        "name": "Bowl",
        "yield_servings": 1,
        "ingredients": [line],
    }
    return rec, {"meals": [{"recipe": rec, "recipe_id": "bowl", "servings": 1}]}


def _line(lines, ingredient_id):
    return next(row for row in lines if row["id"] == ingredient_id)


class StockRules(unittest.TestCase):
    def test_numeric_qty_is_subtracted_before_purchase_units(self):
        rec, plan = _meal("brown-rice", 250, "Brown rice")
        inventory = {
            "ingredients": [
                {"id": "brown-rice", "name": "Brown rice", "in_stock": True, "qty": 100}
            ]
        }
        row = _line(lines_from_plans([plan], inventory, [rec]), "brown-rice")
        self.assertEqual(row["section"], "buy")
        self.assertEqual(row["recipe_grams"], 250)
        self.assertEqual(row["grams"], 60)
        self.assertIn("dry", row["purchase_label"])
        self.assertEqual(row["purchase_unit"], "bag")

    def test_null_qty_is_a_check_not_covered(self):
        rec, plan = _meal("brown-rice", 250, "Brown rice")
        inventory = {
            "ingredients": [
                {"id": "brown-rice", "name": "Brown rice", "in_stock": True, "qty": None}
            ]
        }
        lines = lines_from_plans([plan], inventory, [rec])
        row = _line(lines, "brown-rice")
        self.assertEqual(row["section"], "check")
        self.assertEqual(row["reason"], CHECK_UNKNOWN)
        self.assertNotIn("buy", [line["section"] for line in lines])

    def test_out_of_stock_and_missing_buy_the_full_amount(self):
        rec, plan = _meal("tilapia", 150, "Tilapia")
        out = lines_from_plans(
            [plan],
            {"ingredients": [{"id": "tilapia", "name": "Tilapia", "in_stock": False, "qty": 999}]},
            [rec],
        )
        missing = lines_from_plans([plan], {"ingredients": []}, [rec])
        for lines in (out, missing):
            row = _line(lines, "tilapia")
            self.assertEqual(row["section"], "buy")
            self.assertEqual(row["grams"], 200)
            self.assertIn("raw", row["purchase_label"])

    def test_egg_whites_become_cartons(self):
        rec, plan = _meal("egg-whites", 500, "Egg whites")
        row = _line(lines_from_plans([plan], {"ingredients": []}, [rec]), "egg-whites")
        self.assertEqual(row["purchase_unit"], "carton")
        self.assertEqual(row["purchase_count"], 2)
        self.assertIn("carton", row["purchase_label"])

    def test_whole_eggs_become_a_count(self):
        rec, plan = _meal("eggs-whole", 120, "Whole eggs")
        row = _line(lines_from_plans([plan], {"ingredients": []}, [rec]), "eggs-whole")
        self.assertEqual(row["purchase_count"], 3)
        self.assertEqual(row["purchase_unit"], "egg")
        self.assertIn("3 eggs", row["purchase_label"])

    def test_staples_are_not_buy_lines(self):
        rec, plan = _meal("olive-oil", 18, "Olive oil")
        inventory = {
            "ingredients": [
                {"id": "olive-oil", "name": "Olive oil", "in_stock": False}
            ]
        }
        lines = lines_from_plans([plan], inventory, [rec])
        row = _line(lines, "olive-oil")
        self.assertEqual(row["section"], "staples")
        self.assertEqual(row["reason"], CHECK_STAPLES)
        self.assertFalse(any(line["section"] == "buy" for line in lines))

    def test_pick_cheapest_without_client_offers(self):
        rec, plan = _meal("brown-rice", 250, "Brown rice")
        row = _line(lines_from_plans([plan], {"ingredients": []}, [rec]), "brown-rice")
        self.assertEqual(row["offer_id"], "costco:brown-rice")
        self.assertEqual(row["unit_price"], 0.009)
        self.assertEqual(row["venue"], "costco")

    def test_client_offers_use_the_cheaper_unit_cost(self):
        rec, plan = _meal("tilapia", 150, "Tilapia")
        offers = {
            "tilapia": [
                {"offer_id": "walmart-pack", "name": "Walmart", "price": 8.0, "venue": "walmart"},
                {"offer_id": "costco-pack", "name": "Costco", "price": 3.5, "venue": "costco"},
            ]
        }
        row = _line(lines_from_plans([plan], {"ingredients": []}, [rec], offers), "tilapia")
        self.assertEqual(row["offer_id"], "costco-pack")
        self.assertEqual(row["unit_price"], 3.5)
        self.assertEqual(row["venue"], "costco")


class WeekRotation(unittest.TestCase):
    def test_seven_days_do_not_repeat_inside_two_days(self):
        recipes = load_nourish_recipes(None)
        self.assertGreaterEqual(len(recipes), 9)
        week = assemble_week([], {"ingredients": []}, recipes)
        self.assertEqual(week["days"], 7)
        self.assertEqual(len(week["plans"]), 7)
        last = {}
        for day, plan in enumerate(week["plans"]):
            self.assertTrue(plan["meals"])
            for meal in plan["meals"]:
                rid = meal["recipe_id"]
                if rid in last:
                    self.assertGreater(day - last[rid], 2)
                last[rid] = day
        week_grams = sum(row["recipe_grams"] for row in sum_recipe_grams(week["plans"]).values())
        day_grams = sum(row["recipe_grams"] for row in sum_recipe_grams(week["plans"][:1]).values())
        self.assertGreater(week_grams, day_grams)
        self.assertTrue(week["staples"])
        self.assertTrue(any(row["id"] == "olive-oil" for row in week["staples"]))
        self.assertFalse(week["writes_stock"])


class GroceryEndpoint(unittest.TestCase):
    def test_loads_inventory_and_does_not_write_stock(self):
        rec, plan = _meal("brown-rice", 250, "Brown rice")
        inventory = {
            "ingredients": [
                {"id": "brown-rice", "name": "Brown rice", "in_stock": True, "qty": 100}
            ]
        }
        body = {"meals": plan["meals"], "recipes": [rec]}
        with mock.patch(
            "api.workout._util.require_user",
            return_value=({"id": "user-1"}, None),
        ), mock.patch(
            "rt_dashboard.inventory_store.load_preview_inventory",
            return_value=(inventory, "test"),
        ) as load, mock.patch(
            "rt_dashboard.inventory_store.persist_inventory",
        ) as persist, mock.patch(
            "rt_dashboard.restock_cart.save_hold",
        ) as save_hold:
            status, result = meal_grocery_body({}, body)
        self.assertEqual(status, 200)
        load.assert_called_once_with("user-1")
        persist.assert_not_called()
        save_hold.assert_not_called()
        self.assertFalse(result["writes_stock"])
        self.assertFalse(result["tasks_saved"])
        self.assertEqual(result["days"], 7)
        row = _line(result["buy"], "brown-rice")
        self.assertGreater(row["recipe_grams"], 250)
        self.assertEqual(row["grams"], round((row["recipe_grams"] - 100) / 2.5, 1))

    def test_create_tasks_does_not_write_stock(self):
        rec, plan = _meal("brown-rice", 250, "Brown rice")
        body = {
            "create_tasks": True,
            "meals": plan["meals"],
            "recipes": [rec],
        }
        with mock.patch(
            "api.workout._util.require_user",
            return_value=({"id": "user-1"}, None),
        ), mock.patch(
            "rt_dashboard.inventory_store.load_preview_inventory",
            return_value=({"ingredients": []}, "test"),
        ), mock.patch(
            "rt_dashboard.inventory_store.persist_inventory",
        ) as persist, mock.patch(
            "rt_dashboard.restock_cart.load_hold",
            return_value={"items": [], "note_title": "FitDash restock hold"},
        ), mock.patch(
            "rt_dashboard.restock_cart.save_hold",
            return_value={"items": [{"id": "brown-rice"}]},
        ) as save_hold:
            status, result = meal_grocery_body({}, body)
        self.assertEqual(status, 200)
        self.assertTrue(result["tasks_saved"])
        self.assertEqual(result["hold_count"], 1)
        persist.assert_not_called()
        save_hold.assert_called_once()
        held = save_hold.call_args[0][0]["items"]
        self.assertTrue(any(row.get("id") == "brown-rice" for row in held))


class BatchPersist(unittest.TestCase):
    def test_persist_error_is_not_ok(self):
        recipe = {"id": "bowl", "name": "Bowl", "ingredients": [], "yield_servings": 1}
        with mock.patch(
            "api.workout._util.require_user",
            return_value=({"id": "user-1"}, None),
        ), mock.patch(
            "rt_dashboard.inventory_store.load_preview_inventory",
            return_value=({"ingredients": []}, "test"),
        ), mock.patch(
            "rt_dashboard.recipe_store.get_recipe",
            return_value=recipe,
        ), mock.patch(
            "rt_dashboard.recipe_planner.cooked_batch",
            return_value={"ingredients": []},
        ), mock.patch(
            "rt_dashboard.inventory_store.persist_inventory_result",
            return_value={"ok": False, "error": "turso env missing"},
        ):
            status, body = recipes_batch_body({}, {"recipe_id": "bowl", "servings": 1})
        self.assertEqual(status, 500)
        self.assertFalse(body["ok"])
        self.assertIn("turso env missing", body["error"])
        self.assertNotIn("inventory", body)

    def test_persist_ok_returns_the_saved_pantry(self):
        recipe = {"id": "bowl", "name": "Bowl"}
        saved = {"ingredients": [{"id": "prepared-bowl"}]}
        with mock.patch(
            "api.workout._util.require_user",
            return_value=({"id": "user-1"}, None),
        ), mock.patch(
            "rt_dashboard.inventory_store.load_preview_inventory",
            return_value=({"ingredients": []}, "test"),
        ), mock.patch(
            "rt_dashboard.recipe_store.get_recipe",
            return_value=recipe,
        ), mock.patch(
            "rt_dashboard.recipe_planner.cooked_batch",
            return_value=saved,
        ), mock.patch(
            "rt_dashboard.inventory_store.persist_inventory_result",
            return_value={"ok": True, "inventory": saved},
        ):
            status, body = recipes_batch_body({}, {"recipe_id": "bowl", "servings": 2})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["inventory"], saved)


if __name__ == "__main__":
    unittest.main()
