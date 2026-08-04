"""Unit tests for inventory + remaining-day meal planner."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.models import FoodLogEntry, NutritionDay  # noqa: E402
from rt_dashboard.nutrition_planner import (  # noqa: E402
    add_ingredient,
    generate_meal_plan,
    remaining_macros,
    remove_ingredient,
    suggest_inventory_removals,
    suggest_inventory_staples,
    today_consumed_from_nutrition,
)


class TestNutritionPlanner(unittest.TestCase):
    def test_remaining_and_today_consumed(self):
        days = [
            NutritionDay(date="2026-07-11", calories=500, protein_g=40, carbs_g=30, fat_g=10),
            NutritionDay(date="2026-07-10", calories=2000, protein_g=200, carbs_g=100, fat_g=50),
        ]
        consumed = today_consumed_from_nutrition(days, as_of="2026-07-11")
        self.assertEqual(consumed["calories"], 500)
        self.assertEqual(consumed["protein_g"], 40)
        self.assertEqual(consumed["source"], "daily_rollup")
        targets = {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55}
        rem = remaining_macros(targets, consumed)
        self.assertEqual(rem["calories"], 1600)
        self.assertEqual(rem["protein_g"], 170)

    def test_suggest_removals_duplicate_and_supplement(self):
        inv = {
            "ingredients": [
                {
                    "id": "chicken-a",
                    "name": "Chicken breast",
                    "category": "protein",
                    "serving_label": "6 oz",
                    "calories": 280,
                    "protein_g": 52,
                    "carbs_g": 0,
                    "fat_g": 6,
                    "in_stock": True,
                },
                {
                    "id": "chicken-b",
                    "name": "Chicken Breast",
                    "category": "protein",
                    "serving_label": "8 oz",
                    "calories": 300,
                    "protein_g": 50,
                    "carbs_g": 0,
                    "fat_g": 7,
                    "in_stock": True,
                },
                {
                    "id": "mens-vitamins",
                    "name": "Men's Vitamins, Natural Berry Flavor",
                    "category": "carb",
                    "serving_label": "1 serving",
                    "calories": 15,
                    "protein_g": 0,
                    "carbs_g": 4,
                    "fat_g": 0,
                    "in_stock": True,
                },
            ]
        }
        out = suggest_inventory_removals(
            inv,
            targets={"calories": 2100, "protein_g": 210},
            food_logs=[],
            max_suggestions=6,
        )
        self.assertTrue(out["suggestions"])
        names = " ".join(s["name"].lower() for s in out["suggestions"])
        self.assertTrue("vitamin" in names or "chicken" in names)
        for s in out["suggestions"]:
            self.assertEqual(s["action"], "remove")
            self.assertTrue(s.get("reason"))

    def test_suggest_restock_and_catalog(self):
        inv = {
            "ingredients": [
                {
                    "id": "sweet-potato",
                    "name": "Sweet potato",
                    "category": "carb",
                    "serving_label": "1 medium",
                    "calories": 110,
                    "protein_g": 2,
                    "carbs_g": 26,
                    "fat_g": 0,
                    "in_stock": False,
                },
                {
                    "id": "eggs-whole",
                    "name": "Whole eggs",
                    "category": "protein",
                    "serving_label": "3 eggs",
                    "calories": 210,
                    "protein_g": 18,
                    "carbs_g": 2,
                    "fat_g": 15,
                    "in_stock": True,
                },
            ]
        }
        logs = [
            FoodLogEntry(
                date="2026-07-11",
                name="Chicken breast",
                calories=280,
                protein_g=52,
                carbs_g=0,
                fat_g=6,
            ),
            FoodLogEntry(
                date="2026-07-10",
                name="Chicken breast",
                calories=280,
                protein_g=52,
                carbs_g=0,
                fat_g=6,
            ),
        ]
        out = suggest_inventory_staples(
            inv,
            targets={"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            food_logs=logs,
            consumed={"calories": 500, "protein_g": 40},
            max_suggestions=10,
        )
        self.assertTrue(out["suggestions"])
        actions = {s["action"] for s in out["suggestions"]}
        self.assertIn("restock", actions)
        names = [s["name"].lower() for s in out["suggestions"]]
        self.assertTrue(any("sweet potato" in n for n in names))
        # Chicken logged 2x and not in inventory → add
        self.assertTrue(any("chicken" in n for n in names))

    def test_today_consumed_falls_back_to_food_logs(self):
        logs = [
            FoodLogEntry(
                date="2026-07-11",
                name="Eggs",
                calories=140,
                protein_g=12,
                carbs_g=1,
                fat_g=10,
            ),
            FoodLogEntry(
                date="2026-07-11",
                name="Oats",
                calories=150,
                protein_g=5,
                carbs_g=27,
                fat_g=3,
            ),
        ]
        consumed = today_consumed_from_nutrition([], as_of="2026-07-11", food_logs=logs)
        self.assertEqual(consumed["calories"], 290.0)
        self.assertEqual(consumed["protein_g"], 17.0)
        self.assertEqual(consumed["source"], "food_logs")
        self.assertEqual(consumed["food_log_count"], 2)

    def test_meal_plan_excludes_out_of_stock(self):
        inv = {
            "ingredients": [
                {
                    "id": "chicken",
                    "name": "Chicken",
                    "serving_label": "6oz",
                    "calories": 280,
                    "protein_g": 52,
                    "carbs_g": 0,
                    "fat_g": 6,
                    "in_stock": False,
                },
                {
                    "id": "yogurt",
                    "name": "Greek yogurt",
                    "serving_label": "1 cup",
                    "calories": 150,
                    "protein_g": 20,
                    "carbs_g": 8,
                    "fat_g": 2,
                    "in_stock": True,
                },
            ]
        }
        plan = generate_meal_plan(
            inv,
            {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        )
        names = {i["name"] for i in plan["items"]}
        self.assertNotIn("Chicken", names)
        self.assertTrue(plan.get("in_stock_only"))
        for it in plan["items"]:
            self.assertTrue(it.get("in_stock", True))
        for meal in plan["meals"]:
            for it in meal["items"]:
                self.assertNotEqual(it["name"], "Chicken")

    def test_plan_collapses_repeat_servings(self):
        from rt_dashboard.nutrition_planner import _collapse_plan_items

        collapsed = _collapse_plan_items(
            [
                {
                    "id": "chicken",
                    "name": "Chicken",
                    "servings": 1,
                    "serving_label": "6 oz",
                    "calories": 280,
                    "protein_g": 52,
                    "carbs_g": 0,
                    "fat_g": 6,
                },
                {
                    "id": "chicken",
                    "name": "Chicken",
                    "servings": 1,
                    "serving_label": "6 oz",
                    "calories": 280,
                    "protein_g": 52,
                    "carbs_g": 0,
                    "fat_g": 6,
                },
                {
                    "id": "chicken",
                    "name": "Chicken",
                    "servings": 1,
                    "serving_label": "6 oz",
                    "calories": 280,
                    "protein_g": 52,
                    "carbs_g": 0,
                    "fat_g": 6,
                },
            ]
        )
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["servings"], 3)
        self.assertEqual(collapsed[0]["protein_g"], 156.0)
        self.assertEqual(collapsed[0]["calories"], 840.0)

    def test_generate_plan_fills_protein_from_stock(self):
        inv = {
            "ingredients": [
                {
                    "id": "chicken",
                    "name": "Chicken",
                    "serving_label": "8oz",
                    "calories": 370,
                    "protein_g": 70,
                    "carbs_g": 0,
                    "fat_g": 8,
                    "in_stock": True,
                },
                {
                    "id": "rice",
                    "name": "Rice",
                    "serving_label": "1 cup",
                    "calories": 215,
                    "protein_g": 5,
                    "carbs_g": 45,
                    "fat_g": 2,
                    "in_stock": True,
                },
                {
                    "id": "candy",
                    "name": "Candy",
                    "serving_label": "1 bar",
                    "calories": 250,
                    "protein_g": 1,
                    "carbs_g": 40,
                    "fat_g": 10,
                    "in_stock": False,
                },
            ]
        }
        targets = {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55}
        consumed = {"calories": 500, "protein_g": 40, "carbs_g": 30, "fat_g": 10}
        plan = generate_meal_plan(inv, targets, consumed)
        self.assertTrue(plan["items"])
        names = {i["name"] for i in plan["items"]}
        self.assertIn("Chicken", names)
        self.assertNotIn("Candy", names)
        self.assertGreater(plan["planned_totals"]["protein_g"], 0)
        # remaining protein should drop
        self.assertLess(
            plan["remaining_after_plan"]["protein_g"],
            plan["remaining_before_plan"]["protein_g"],
        )

    def test_add_remove_ingredient(self):
        inv = {"ingredients": []}
        inv = add_ingredient(
            inv,
            {
                "name": "Greek yogurt",
                "calories": 100,
                "protein_g": 17,
                "carbs_g": 6,
                "fat_g": 0,
            },
        )
        self.assertEqual(len(inv["ingredients"]), 1)
        iid = inv["ingredients"][0]["id"]
        inv = remove_ingredient(inv, ingredient_id=iid)
        self.assertEqual(len(inv["ingredients"]), 0)


if __name__ == "__main__":
    unittest.main()
