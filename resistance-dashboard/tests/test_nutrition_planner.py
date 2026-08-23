"""Unit tests for inventory + remaining-day meal planner."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.models import FoodLogEntry, NutritionDay  # noqa: E402
from rt_dashboard.nutrition_planner import (  # noqa: E402
    add_ingredient,
    format_portion_label,
    generate_meal_plan,
    normalize_ingredient,
    remaining_macros,
    remove_ingredient,
    update_ingredient,
    suggest_inventory_removals,
    suggest_inventory_staples,
    today_consumed_from_nutrition,
)

ET = ZoneInfo("America/New_York")

STOCKED_CUTTING = {
    "ingredients": [
        {
            "id": "chicken",
            "name": "Chicken",
            "serving_g": 170,
            "serving_label": "170g cooked",
            "calories": 280,
            "protein_g": 52,
            "carbs_g": 0,
            "fat_g": 6,
            "in_stock": True,
        },
        {
            "id": "rice",
            "name": "Rice",
            "serving_g": 195,
            "serving_label": "195g cooked",
            "calories": 215,
            "protein_g": 5,
            "carbs_g": 45,
            "fat_g": 2,
            "in_stock": True,
        },
        {
            "id": "yogurt",
            "name": "Greek yogurt",
            "serving_g": 200,
            "serving_label": "200g",
            "calories": 130,
            "protein_g": 20,
            "carbs_g": 8,
            "fat_g": 0,
            "in_stock": True,
        },
        {
            "id": "broccoli",
            "name": "Broccoli",
            "serving_g": 180,
            "serving_label": "180g",
            "calories": 60,
            "protein_g": 5,
            "carbs_g": 12,
            "fat_g": 0.5,
            "in_stock": True,
        },
        {
            "id": "candy",
            "name": "Candy",
            "serving_g": 50,
            "calories": 250,
            "protein_g": 1,
            "carbs_g": 40,
            "fat_g": 10,
            "in_stock": False,
        },
    ]
}
FULL_TARGETS = {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55}
EMPTY_CONSUMED = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}


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

    def test_gram_portions_on_plan_and_normalize(self):
        ing = normalize_ingredient(
            {
                "name": "Tilapia",
                "serving_g": 170,
                "serving_label": "cooked",
                "calories": 220,
                "protein_g": 45,
                "carbs_g": 0,
                "fat_g": 4,
                "in_stock": True,
            }
        )
        self.assertEqual(ing["serving_g"], 170.0)
        self.assertIn("170g", ing["serving_label"])
        # oz label → grams without explicit serving_g
        oz = normalize_ingredient(
            {
                "name": "Turkey",
                "serving_label": "6 oz cooked",
                "calories": 250,
                "protein_g": 50,
                "carbs_g": 0,
                "fat_g": 4,
            }
        )
        self.assertEqual(oz["serving_g"], 170.0)
        self.assertTrue(str(oz["serving_label"]).startswith("170g"))
        self.assertEqual(format_portion_label(serving_g=170, servings=2), "340g")

        inv = {
            "ingredients": [
                {
                    "id": "tilapia",
                    "name": "Tilapia",
                    "serving_g": 170,
                    "serving_label": "170g cooked",
                    "calories": 220,
                    "protein_g": 45,
                    "carbs_g": 0,
                    "fat_g": 4,
                    "in_stock": True,
                }
            ]
        }
        plan = generate_meal_plan(
            inv,
            {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            {"calories": 1800, "protein_g": 100, "carbs_g": 150, "fat_g": 40},
        )
        self.assertTrue(plan["items"])
        first = plan["items"][0]
        self.assertEqual(first["id"], "tilapia")
        self.assertIn("portion_g", first)
        self.assertTrue(str(first["serving_label"]).endswith("g") or "g" in str(first["serving_label"]))
        # Multi-pick collapses to total grams (not "2 × 170g")
        from rt_dashboard.nutrition_planner import _collapse_plan_items, _plan_item_from_ingredient

        rows = [
            _plan_item_from_ingredient(ing, 1),
            _plan_item_from_ingredient(ing, 1),
        ]
        collapsed = _collapse_plan_items(rows)
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["servings"], 2)
        self.assertEqual(collapsed[0]["portion_g"], 340)
        self.assertEqual(collapsed[0]["serving_label"], "340g")

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

    def test_update_existing_ingredient(self):
        inv = add_ingredient(
            {"ingredients": []},
            {
                "id": "oats",
                "name": "Oats",
                "category": "carb",
                "serving_g": 40,
                "calories": 150,
                "protein_g": 5,
                "carbs_g": 27,
                "fat_g": 3,
                "in_stock": True,
            },
        )
        source = inv
        updated = update_ingredient(
            inv,
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
        self.assertEqual(len(updated["ingredients"]), 1)
        row = updated["ingredients"][0]
        self.assertEqual(row["id"], "oats")
        self.assertEqual(row["name"], "Rolled oats")
        self.assertEqual(row["serving_g"], 80.0)
        self.assertEqual(row["calories"], 300.0)
        self.assertEqual(row["protein_g"], 10.0)
        self.assertTrue(row["in_stock"])
        # Source inventory is unchanged until the caller persists (cancel analog).
        self.assertEqual(source["ingredients"][0]["name"], "Oats")
        self.assertEqual(source["ingredients"][0]["calories"], 150.0)

    def test_update_unknown_id_does_not_invent(self):
        inv = add_ingredient(
            {"ingredients": []},
            {
                "id": "oats",
                "name": "Oats",
                "calories": 150,
                "protein_g": 5,
                "carbs_g": 27,
                "fat_g": 3,
            },
        )
        with self.assertRaises(ValueError) as ctx:
            update_ingredient(
                inv,
                {
                    "id": "unicorn-steak",
                    "name": "Unicorn steak",
                    "calories": 900,
                    "protein_g": 80,
                    "carbs_g": 0,
                    "fat_g": 40,
                },
            )
        self.assertIn("not found", str(ctx.exception).lower())
        self.assertEqual(len(inv["ingredients"]), 1)
        self.assertEqual(inv["ingredients"][0]["id"], "oats")

    def test_update_missing_id_is_required(self):
        inv = {"ingredients": [{"id": "oats", "name": "Oats", "calories": 1}]}
        with self.assertRaises(ValueError) as ctx:
            update_ingredient(inv, {"name": "Oats", "calories": 9})
        self.assertIn("id required", str(ctx.exception).lower())
        self.assertEqual(inv["ingredients"][0]["calories"], 1)

    def test_multi_slot_times_and_grams(self):
        now = datetime(2026, 8, 22, 10, 0, tzinfo=ET)
        plan = generate_meal_plan(
            STOCKED_CUTTING,
            FULL_TARGETS,
            EMPTY_CONSUMED,
            now=now,
            tz_name="America/New_York",
        )
        meals = plan["meals"]
        self.assertGreaterEqual(len(meals), 2)
        self.assertLessEqual(len(meals), 4)
        labels = [m["label"] for m in meals]
        self.assertIn("Next meal", labels)
        self.assertEqual(labels.count("Next meal"), 1)
        stocked_names = {"Chicken", "Rice", "Greek yogurt", "Broccoli"}
        eat_hours = []
        for meal in meals:
            self.assertTrue(meal.get("items"), "do not force empty timed slots")
            self.assertTrue(meal.get("eat_at"))
            self.assertTrue(meal.get("eat_at_label"))
            self.assertEqual(meal.get("timezone"), "America/New_York")
            eat = datetime.fromisoformat(meal["eat_at"])
            self.assertEqual(eat.utcoffset(), ET.utcoffset(eat))
            eat_hours.append((eat.hour, eat.minute))
            for it in meal["items"]:
                self.assertIn(it["name"], stocked_names)
                self.assertNotEqual(it["name"], "Candy")
                self.assertIn("portion_g", it)
                self.assertGreater(it["portion_g"], 0)
                self.assertIn("g", str(it["serving_label"]))
        # Morning plan uses the default lunch / afternoon / dinner hinges.
        self.assertTrue(
            any(hm in ((12, 0), (15, 30), (19, 0), (21, 0)) for hm in eat_hours)
        )
        next_meal = next(m for m in meals if m["label"] == "Next meal")
        next_eat = datetime.fromisoformat(next_meal["eat_at"])
        self.assertGreaterEqual(next_eat, now)
        for meal in meals:
            eat = datetime.fromisoformat(meal["eat_at"])
            if meal["label"] != "Next meal":
                self.assertTrue(eat >= next_eat or eat < now)

    def test_next_meal_is_soonest_upcoming(self):
        now = datetime(2026, 8, 22, 16, 0, tzinfo=ET)
        plan = generate_meal_plan(
            STOCKED_CUTTING,
            FULL_TARGETS,
            EMPTY_CONSUMED,
            now=now,
            tz_name="America/New_York",
        )
        meals = plan["meals"]
        self.assertGreaterEqual(len(meals), 2)
        next_meal = next(m for m in meals if m["label"] == "Next meal")
        next_eat = datetime.fromisoformat(next_meal["eat_at"])
        self.assertGreaterEqual(next_eat, now)
        upcoming = [
            datetime.fromisoformat(m["eat_at"])
            for m in meals
            if datetime.fromisoformat(m["eat_at"]) >= now
        ]
        self.assertEqual(next_eat, min(upcoming))
        self.assertEqual(next_eat.hour, 19)
        self.assertEqual(next_eat.minute, 0)

    def test_eat_slots_override_defaults_without_inventing_food(self):
        now = datetime(2026, 8, 22, 9, 0, tzinfo=ET)
        plan = generate_meal_plan(
            STOCKED_CUTTING,
            FULL_TARGETS,
            {"calories": 1600, "protein_g": 150, "carbs_g": 140, "fat_g": 40},
            now=now,
            tz_name="America/New_York",
            eat_slots=["13:15", "18:45"],
        )
        meals = plan["meals"]
        self.assertGreaterEqual(len(meals), 1)
        self.assertLessEqual(len(meals), 2)
        times = [datetime.fromisoformat(m["eat_at"]) for m in meals]
        allowed = {
            (13, 15),
            (18, 45),
        }
        for t in times:
            self.assertIn((t.hour, t.minute), allowed)
        names = {it["name"] for m in meals for it in m["items"]}
        self.assertTrue(names <= {"Chicken", "Rice", "Greek yogurt", "Broccoli"})
        self.assertNotIn("Candy", names)

    def test_no_invented_grams_when_row_has_no_mass(self):
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
        plan = generate_meal_plan(
            inv,
            FULL_TARGETS,
            {"calories": 1800, "protein_g": 180, "carbs_g": 160, "fat_g": 40},
            now=datetime(2026, 8, 22, 11, 0, tzinfo=ET),
            tz_name="America/New_York",
        )
        self.assertTrue(plan["items"])
        row = plan["items"][0]
        self.assertEqual(row["name"], "Whole eggs")
        self.assertNotIn("portion_g", row)
        self.assertNotIn("serving_g", row)
        self.assertIn("egg", str(row["serving_label"]).lower())
        self.assertNotIn("g", str(row["serving_label"]).lower().replace("egg", ""))
        for meal in plan["meals"]:
            self.assertTrue(meal.get("eat_at"))
            for it in meal["items"]:
                self.assertNotIn("portion_g", it)

    def test_empty_pantry_has_no_timed_slots_or_food(self):
        plan = generate_meal_plan(
            {"ingredients": []},
            FULL_TARGETS,
            EMPTY_CONSUMED,
            now=datetime(2026, 8, 22, 12, 0, tzinfo=ET),
        )
        self.assertEqual(plan["meals"], [])
        self.assertEqual(plan["items"], [])
        self.assertEqual(plan["stocked_count"], 0)
        self.assertTrue(plan.get("pantry_dark"))
        self.assertEqual(plan["message"], "Pantry unavailable")
        self.assertFalse(any((m or {}).get("eat_at") for m in plan.get("meals") or []))

    def test_low_remaining_does_not_force_four_slots(self):
        inv = {
            "ingredients": [
                {
                    "id": "yogurt",
                    "name": "Greek yogurt",
                    "serving_g": 200,
                    "calories": 130,
                    "protein_g": 20,
                    "carbs_g": 8,
                    "fat_g": 0,
                    "in_stock": True,
                }
            ]
        }
        plan = generate_meal_plan(
            inv,
            FULL_TARGETS,
            {"calories": 1950, "protein_g": 190, "carbs_g": 170, "fat_g": 50},
            now=datetime(2026, 8, 22, 17, 0, tzinfo=ET),
            tz_name="America/New_York",
        )
        self.assertLessEqual(len(plan["meals"]), 2)
        for meal in plan["meals"]:
            self.assertTrue(meal["items"])
            self.assertTrue(meal.get("eat_at"))


if __name__ == "__main__":
    unittest.main()
