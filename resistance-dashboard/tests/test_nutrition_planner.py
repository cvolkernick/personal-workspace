"""Unit tests for inventory + remaining-day meal planner."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.models import NutritionDay  # noqa: E402
from rt_dashboard.nutrition_planner import (  # noqa: E402
    add_ingredient,
    generate_meal_plan,
    remaining_macros,
    remove_ingredient,
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
        targets = {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55}
        rem = remaining_macros(targets, consumed)
        self.assertEqual(rem["calories"], 1600)
        self.assertEqual(rem["protein_g"], 170)

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
