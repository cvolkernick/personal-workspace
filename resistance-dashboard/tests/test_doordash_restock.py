"""DoorDash meal restock: shopping list + dry-run order plan."""

from __future__ import annotations

import unittest

from rt_dashboard.coach_actions import format_action_reply, try_parse_coach_action
from rt_dashboard.doordash_restock import (
    build_meal_restock_list,
    execute_restock_order,
    plan_dd_cli_commands,
)


class TestDoorDashRestock(unittest.TestCase):
    def test_out_of_stock_becomes_shopping_list(self):
        inv = {
            "ingredients": [
                {
                    "id": "chicken",
                    "name": "Chicken breast",
                    "category": "protein",
                    "in_stock": False,
                    "calories": 200,
                    "protein_g": 40,
                },
                {
                    "id": "rice",
                    "name": "Rice",
                    "category": "carb",
                    "in_stock": True,
                    "calories": 150,
                    "protein_g": 3,
                },
            ]
        }
        plan = {
            "items": [],
            "meals": [],
            "stocked_count": 1,
            "message": "Thin plan",
        }
        sug = {
            "suggestions": [
                {
                    "id": "yogurt",
                    "name": "Greek yogurt",
                    "action": "add",
                    "reason": "Protein gap",
                    "category": "protein",
                }
            ]
        }
        out = build_meal_restock_list(inv, plan, sug)
        self.assertTrue(out["needs_order"])
        names = [i["name"] for i in out["items"]]
        self.assertIn("Chicken breast", names)
        self.assertIn("Greek yogurt", names)
        # In-stock rice should not appear as a new add
        self.assertNotIn("Rice", names)

    def test_fully_stocked_no_order(self):
        inv = {
            "ingredients": [
                {
                    "id": "eggs",
                    "name": "Eggs",
                    "in_stock": True,
                    "category": "protein",
                    "calories": 140,
                    "protein_g": 12,
                }
            ]
        }
        plan = {
            "items": [{"name": "Eggs", "in_stock": True}],
            "meals": [{"label": "Next", "items": []}],
            "stocked_count": 1,
        }
        out = build_meal_restock_list(inv, plan, {"suggestions": []})
        self.assertFalse(out["needs_order"])
        self.assertEqual(out["count"], 0)

    def test_empty_inventory_seeds_default_staples(self):
        out = build_meal_restock_list(
            {"ingredients": []},
            {"items": [], "meals": [], "stocked_count": 0, "message": "No stock"},
            {"suggestions": []},
        )
        self.assertTrue(out["needs_order"])
        self.assertGreaterEqual(out["count"], 3)
        self.assertTrue(any("chicken" in i["name"].lower() for i in out["items"]))

    def test_dry_run_execute_false_no_submit(self):
        restock = build_meal_restock_list(
            {
                "ingredients": [
                    {"id": "c", "name": "Chicken", "in_stock": False, "category": "protein"}
                ]
            },
            {"items": [], "meals": [], "stocked_count": 0},
            {},
        )
        out = execute_restock_order(restock, execute=False, confirm=False)
        self.assertTrue(out["ok"])
        self.assertFalse(out["execute"])
        self.assertIn("Preview only", out["message"])
        self.assertTrue(out.get("dry_run_commands"))
        self.assertFalse(out.get("submitted"))

    def test_plan_commands_include_grocery_and_checkout(self):
        steps = plan_dd_cli_commands(
            [{"name": "Eggs", "query": "Eggs"}], store_query="grocery"
        )
        joined = " ".join(" ".join(s["args"]) for s in steps)
        self.assertIn("build-grocery-list", joined)
        self.assertIn("checkout-url", joined)
        self.assertTrue(any(s.get("requires_confirm") for s in steps))

    def test_coach_action_parse_preview_and_confirm(self):
        a = try_parse_coach_action("order missing groceries")
        self.assertIsNotNone(a)
        self.assertEqual(a["action"], "doordash_restock")
        self.assertFalse(a["execute"])

        b = try_parse_coach_action("build the doordash cart")
        self.assertTrue(b["execute"])
        self.assertFalse(b["confirm"])

        c = try_parse_coach_action("confirm and place the doordash order")
        self.assertTrue(c["execute"])
        self.assertTrue(c["confirm"])

    def test_format_action_reply_doordash(self):
        text = format_action_reply(
            {
                "ok": True,
                "action": "doordash_restock",
                "execute": False,
                "restock": {
                    "items": [{"name": "Chicken breast"}, {"name": "Eggs"}],
                },
                "message": "Preview only",
            }
        )
        self.assertIn("DoorDash restock", text)
        self.assertIn("Chicken breast", text)


if __name__ == "__main__":
    unittest.main()
