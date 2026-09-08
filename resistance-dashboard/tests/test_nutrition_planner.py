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
    DEFAULT_TARGETS,
    DIVERSITY_EPS,
    DIVERSITY_EPS_KCAL,
    DIVERSITY_EPS_PROTEIN_G,
    SERVING_GRAMS_REQUIRED_MSG,
    SHAKE_MAX_POWDER_PROTEIN_G,
    SHAKE_MAX_SERVINGS,
    SOFT_FIBER_TARGET_G,
    STAPLE_CATALOG,
    add_ingredient,
    food_logs_fingerprint,
    format_plan_portion,
    format_portion_label,
    generate_meal_plan,
    colocate_egg_pair,
    egg_role,
    ensure_egg_pair,
    inventory_gap_role,
    is_shake_or_powder,
    is_veg_or_fruit,
    needs_serving_grams,
    normalize_ingredient,
    remaining_macros,
    remove_ingredient,
    scale_plan_item_to_inventory,
    serving_grams_nudge_text,
    serving_grams_required,
    suggested_qty_for_item,
    update_ingredient,
    suggest_inventory_removals,
    suggest_inventory_staples,
    today_consumed_from_nutrition,
    within_diversity_eps,
    _macros_for_portion,
    _pick_continuous_portion,
    _plan_item_from_ingredient,
    _round_portion_g,
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
        # Need-based catalog add is allowed; log-frequency must not be the reason.
        for s in out["suggestions"]:
            reason = (s.get("reason") or "").lower()
            self.assertNotIn("logged", reason)
            self.assertNotRegex(reason, r"\d+\s*[×x]|times")
        self.assertEqual(out.get("ranking"), "need")
        self.assertFalse(out.get("log_frequency_positive"))

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

    def test_food_logs_fingerprint_changes_when_logs_change(self):
        logs = [
            FoodLogEntry(
                date="2026-08-24",
                name="Eggs",
                calories=140,
                protein_g=12,
                carbs_g=1,
                fat_g=10,
                time="08:10",
            )
        ]
        empty = food_logs_fingerprint([], consumed={"calories": 0}, day="2026-08-24")
        first = food_logs_fingerprint(
            logs,
            consumed={"calories": 140, "protein_g": 12, "food_log_count": 1},
            day="2026-08-24",
        )
        again = food_logs_fingerprint(
            [logs[0].to_dict()],
            consumed={"calories": 140, "protein_g": 12, "food_log_count": 1},
            day="2026-08-24",
        )
        more = food_logs_fingerprint(
            logs
            + [
                FoodLogEntry(
                    date="2026-08-24",
                    name="Oats",
                    calories=150,
                    protein_g=5,
                    time="09:00",
                )
            ],
            consumed={"calories": 290, "protein_g": 17, "food_log_count": 2},
            day="2026-08-24",
        )
        self.assertEqual(len(first), 16)
        self.assertEqual(first, again)
        self.assertNotEqual(empty, first)
        self.assertNotEqual(first, more)

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

    def test_continuous_portion_scale_250g_from_100g_serving(self):
        """100g serving → 250g line is 2.5× macros (not 2× or 3× whole servings)."""
        chicken = {
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
        inv = {"ingredients": [chicken]}
        # Remaining exactly 2.5 servings: 275 kcal / 57.5g protein.
        consumed = {
            "calories": 2100 - 275,
            "protein_g": 210 - 57.5,
            "carbs_g": 180,
            "fat_g": 55,
        }
        plan = generate_meal_plan(
            inv,
            FULL_TARGETS,
            consumed,
            now=datetime(2026, 8, 23, 11, 0, tzinfo=ET),
            tz_name="America/New_York",
        )
        self.assertTrue(plan["items"])
        row = plan["items"][0]
        self.assertEqual(row["id"], "chicken")
        self.assertEqual(row["portion_g"], 250)
        self.assertEqual(row["servings"], 2.5)
        self.assertEqual(row["serving_label"], "250g")
        self.assertEqual(row["calories"], 275.0)
        self.assertEqual(row["protein_g"], 57.5)
        self.assertEqual(row["carbs_g"], 0.0)
        self.assertEqual(row["fat_g"], 3.0)
        # Whole-serving-only fill would be 2× (220/46) or 3× (330/69).
        self.assertNotEqual(row["servings"], 2)
        self.assertNotEqual(row["servings"], 3)
        self.assertNotEqual(row["portion_g"], 200)
        self.assertNotEqual(row["portion_g"], 300)

    def test_partial_25g_fits_better_than_whole_serving(self):
        chicken = {
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
        # ~0.25 serving leftover protein; calories still open so we don't early-exit.
        plan = generate_meal_plan(
            {"ingredients": [chicken]},
            FULL_TARGETS,
            {
                "calories": 1600,
                "protein_g": 210 - 5.75,
                "carbs_g": 160,
                "fat_g": 50,
            },
            now=datetime(2026, 8, 23, 11, 0, tzinfo=ET),
            tz_name="America/New_York",
        )
        self.assertTrue(plan["items"])
        row = plan["items"][0]
        self.assertEqual(row["portion_g"], 25)
        self.assertEqual(row["servings"], 0.25)
        self.assertEqual(row["calories"], 27.5)
        self.assertEqual(row["protein_g"], 5.8)
        self.assertLess(row["portion_g"], 100)

    def test_fill_is_not_whole_serving_only_when_serving_g_present(self):
        ing = {
            "id": "chicken",
            "name": "Chicken",
            "serving_g": 100,
            "calories": 110,
            "protein_g": 23,
            "carbs_g": 0,
            "fat_g": 1.2,
        }
        rem = {"calories": 275, "protein_g": 57.5, "carbs_g": 0, "fat_g": 0}
        pick = _pick_continuous_portion(
            ing, rem, cal_ceiling=275 + 80, totals={"calories": 0}
        )
        self.assertIsNotNone(pick)
        servings, portion_g = pick
        self.assertEqual(portion_g, 250)
        self.assertAlmostEqual(servings, 2.5)
        self.assertNotEqual(servings, 1.0)
        self.assertNotEqual(servings, 2.0)
        self.assertNotEqual(servings, 3.0)
        scaled = _macros_for_portion(ing, portion_g=250)
        self.assertEqual(scaled["calories"], 275.0)
        self.assertEqual(scaled["protein_g"], 57.5)

    def test_plan_item_fractional_portion_macros(self):
        ing = {
            "id": "chicken",
            "name": "Chicken",
            "serving_g": 100,
            "serving_label": "100g",
            "calories": 110,
            "protein_g": 23,
            "carbs_g": 0,
            "fat_g": 1.2,
        }
        row = _plan_item_from_ingredient(ing, portion_g=250)
        self.assertEqual(row["portion_g"], 250)
        self.assertEqual(row["servings"], 2.5)
        self.assertEqual(row["calories"], 275.0)
        self.assertEqual(row["protein_g"], 57.5)
        self.assertEqual(format_plan_portion(row), "250g")

    def test_scale_plan_item_does_not_invent_grams(self):
        ing = {
            "id": "eggs",
            "name": "Whole eggs",
            "serving_label": "3 eggs",
            "calories": 210,
            "protein_g": 18,
            "carbs_g": 2,
            "fat_g": 15,
        }
        row = scale_plan_item_to_inventory(
            {"name": "Whole eggs", "portion_g": 250, "calories": 400},
            ing,
        )
        self.assertNotIn("portion_g", row)
        self.assertNotIn("serving_g", row)
        self.assertIn("egg", str(row["serving_label"]).lower())

    def test_format_plan_portion_prefers_grams(self):
        self.assertEqual(
            format_plan_portion({"portion_g": 250, "serving_label": "1 serving"}),
            "250g",
        )
        self.assertEqual(
            format_plan_portion({"serving_label": "3 eggs"}),
            "3 eggs",
        )

    def test_nourish_round_5g_min_25g(self):
        """Nourish AC: continuous portion_g rounds ~5g, min ~25g."""
        self.assertEqual(_round_portion_g(27, serving_g=100), 25)
        self.assertEqual(_round_portion_g(28, serving_g=100), 30)
        self.assertEqual(_round_portion_g(12, serving_g=100), 0)
        self.assertEqual(_round_portion_g(23, serving_g=100), 25)
        self.assertEqual(_round_portion_g(250.4, serving_g=100), 250)
        self.assertEqual(_round_portion_g(252.6, serving_g=100), 255)
        # Far below min is not a pick. Near-min snaps up to 25g, not 10/15/20.
        self.assertEqual(_round_portion_g(10, serving_g=100), 0)
        self.assertEqual(_round_portion_g(15, serving_g=100), 25)
        self.assertEqual(_round_portion_g(20, serving_g=100), 25)

    def test_nourish_per_gram_scale_and_soft_ceiling(self):
        ing = {
            "id": "chicken",
            "name": "Chicken",
            "serving_g": 100,
            "calories": 110,
            "protein_g": 23,
            "carbs_g": 0,
            "fat_g": 1.2,
        }
        # Protein leftover wants ~348g; calorie room is only ~170 kcal → ~155g.
        rem = {"calories": 90, "protein_g": 80, "carbs_g": 0, "fat_g": 0}
        pick = _pick_continuous_portion(
            ing, rem, cal_ceiling=170, totals={"calories": 0}
        )
        self.assertIsNotNone(pick)
        _servings, portion_g = pick
        self.assertGreaterEqual(portion_g, 25)
        self.assertEqual(portion_g % 5, 0)
        self.assertLess(portion_g, 200)
        macros = _macros_for_portion(ing, portion_g=portion_g)
        self.assertLessEqual(macros["calories"], 170 + 40)
        self.assertAlmostEqual(macros["calories"], 110 * (portion_g / 100), places=1)
        self.assertAlmostEqual(macros["protein_g"], 23 * (portion_g / 100), places=1)

    def test_nourish_plan_portions_are_5g_min_25_in_stock(self):
        now = datetime(2026, 8, 23, 10, 0, tzinfo=ET)
        plan = generate_meal_plan(
            STOCKED_CUTTING,
            FULL_TARGETS,
            EMPTY_CONSUMED,
            now=now,
            tz_name="America/New_York",
        )
        self.assertTrue(plan["items"])
        stocked = {"Chicken", "Rice", "Greek yogurt", "Broccoli"}
        for it in plan["items"]:
            self.assertIn(it["name"], stocked)
            self.assertNotEqual(it["name"], "Candy")
            self.assertIn("portion_g", it)
            self.assertGreaterEqual(it["portion_g"], 25)
            self.assertEqual(it["portion_g"] % 5, 0)
            sg = float(it["serving_g"])
            scale = it["portion_g"] / sg
            base = next(
                x for x in STOCKED_CUTTING["ingredients"] if x["id"] == it["id"]
            )
            self.assertAlmostEqual(it["calories"], float(base["calories"]) * scale, places=1)
            self.assertAlmostEqual(it["protein_g"], float(base["protein_g"]) * scale, places=1)
        for meal in plan["meals"]:
            for it in meal["items"]:
                self.assertIn(it["name"], stocked)
                self.assertGreaterEqual(it["portion_g"], 25)

    def test_ui_keeps_portion_g_primary(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = js.find("function formatPlanPortion")
        self.assertGreater(start, 0)
        fn = js[start : start + 450]
        pg_at = fn.find("portion_g")
        label_at = fn.find("serving_label")
        self.assertGreater(pg_at, 0)
        self.assertGreater(label_at, pg_at)
        self.assertIn("Math.round(pg)", fn)

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

    def test_logged_serving_needs_and_requires_grams(self):
        logged = {
            "name": "Chicken breast",
            "serving_label": "1 logged serving (avg)",
            "calories": 389.2,
            "protein_g": 72.8,
            "carbs_g": 0,
            "fat_g": 7.3,
        }
        self.assertTrue(needs_serving_grams(logged))
        self.assertTrue(serving_grams_required(logged))
        self.assertFalse(
            serving_grams_required({**logged, "serving_g": 170})
        )
        self.assertFalse(
            needs_serving_grams({**logged, "serving_label": "170g"})
        )
        self.assertFalse(
            serving_grams_required(
                {
                    "name": "Whole eggs",
                    "serving_label": "3 eggs",
                    "calories": 210,
                    "protein_g": 18,
                    "carbs_g": 2,
                    "fat_g": 15,
                }
            )
        )

    def test_logged_serving_add_blocked_without_grams(self):
        with self.assertRaises(ValueError) as ctx:
            add_ingredient(
                {"ingredients": []},
                {
                    "name": "Chicken breast",
                    "serving_label": "1 logged serving (avg)",
                    "calories": 389.2,
                    "protein_g": 72.8,
                    "carbs_g": 0,
                    "fat_g": 7.3,
                },
            )
        self.assertIn("serving grams required", str(ctx.exception).lower())
        self.assertIn("logged serving", str(ctx.exception).lower())
        self.assertEqual(str(ctx.exception), SERVING_GRAMS_REQUIRED_MSG)

    def test_logged_serving_update_blocked_without_grams(self):
        inv = {
            "ingredients": [
                {
                    "id": "chicken-breast",
                    "name": "Chicken breast",
                    "serving_label": "1 logged serving (avg)",
                    "calories": 389.2,
                    "protein_g": 72.8,
                    "carbs_g": 0,
                    "fat_g": 7.3,
                    "in_stock": True,
                }
            ]
        }
        with self.assertRaises(ValueError) as ctx:
            update_ingredient(
                inv,
                {
                    "id": "chicken-breast",
                    "name": "Chicken breast",
                    "serving_label": "1 logged serving (avg)",
                    "calories": 389.2,
                    "protein_g": 72.8,
                },
            )
        self.assertIn("serving grams required", str(ctx.exception).lower())

    def test_logged_serving_add_with_user_grams_biases_label(self):
        inv = add_ingredient(
            {"ingredients": []},
            {
                "id": "chicken-breast",
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
        row = inv["ingredients"][0]
        self.assertEqual(row["serving_g"], 100.0)
        self.assertTrue(str(row["serving_label"]).startswith("100g"))
        # User-supplied mass only — no invented pantry grams.
        self.assertNotIn("170", str(row["serving_label"]))

    def test_logged_serving_with_grams_plan_emits_250g(self):
        inv = add_ingredient(
            {"ingredients": []},
            {
                "id": "chicken-breast",
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
        plan = generate_meal_plan(
            inv,
            FULL_TARGETS,
            {
                "calories": 2100 - 275,
                "protein_g": 210 - 57.5,
                "carbs_g": 180,
                "fat_g": 55,
            },
            now=datetime(2026, 8, 23, 11, 0, tzinfo=ET),
            tz_name="America/New_York",
        )
        self.assertTrue(plan["items"])
        row = plan["items"][0]
        self.assertEqual(row["id"], "chicken-breast")
        self.assertEqual(row["portion_g"], 250)
        self.assertEqual(row["serving_label"], "250g")
        self.assertEqual(format_plan_portion(row), "250g")
        self.assertEqual(plan["serving_grams_nudge"], "")

    def test_plan_nudge_when_stocked_logged_serving_lacks_grams(self):
        inv = {
            "ingredients": [
                {
                    "id": "chicken",
                    "name": "Chicken",
                    "serving_label": "1 logged serving (avg)",
                    "calories": 110,
                    "protein_g": 23,
                    "carbs_g": 0,
                    "fat_g": 1.2,
                    "in_stock": True,
                }
            ]
        }
        plan = generate_meal_plan(
            inv,
            FULL_TARGETS,
            {"calories": 1800, "protein_g": 160, "carbs_g": 160, "fat_g": 40},
            now=datetime(2026, 8, 23, 11, 0, tzinfo=ET),
            tz_name="America/New_York",
        )
        self.assertTrue(plan["items"])
        self.assertNotIn("portion_g", plan["items"][0])
        self.assertNotIn("250g", str(plan["items"][0].get("serving_label") or ""))
        self.assertIn("logged serving", plan["items"][0]["serving_label"].lower())
        self.assertIn("Set serving grams on Chicken", plan["serving_grams_nudge"])
        self.assertIn("weighable portions", plan["serving_grams_nudge"])
        self.assertEqual(
            serving_grams_nudge_text(plan["items"]),
            plan["serving_grams_nudge"],
        )


def _ing(iid, name, **kwargs):
    row = {
        "id": iid,
        "name": name,
        "category": kwargs.pop("category", "other"),
        "serving_label": kwargs.pop("serving_label", "1 serving"),
        "calories": kwargs.pop("calories", 100),
        "protein_g": kwargs.pop("protein_g", 0),
        "carbs_g": kwargs.pop("carbs_g", 0),
        "fat_g": kwargs.pop("fat_g", 0),
        "in_stock": kwargs.pop("in_stock", True),
    }
    row.update(kwargs)
    return row


class TestMealPlanFoodQuality(unittest.TestCase):
    """#501: empty-plan regen, veg-before-shake, soft fiber, shake cap, 210P."""

    def test_protein_floor_stays_210(self):
        self.assertEqual(DEFAULT_TARGETS["protein_g"], 210)
        self.assertEqual(DEFAULT_TARGETS["calories"], 2100)
        plan = generate_meal_plan({"ingredients": []}, FULL_TARGETS, EMPTY_CONSUMED)
        self.assertEqual(plan["targets"]["protein_g"], 210)
        self.assertEqual(plan["targets"]["calories"], 2100)

    def test_stocked_pantry_yields_non_empty_plan(self):
        inv = {
            "ingredients": [
                _ing(
                    "chicken",
                    "Chicken breast",
                    category="protein",
                    calories=280,
                    protein_g=52,
                    fat_g=6,
                ),
                _ing(
                    "rice",
                    "Brown rice",
                    category="carb",
                    calories=215,
                    protein_g=5,
                    carbs_g=45,
                    fat_g=2,
                ),
                _ing(
                    "broccoli",
                    "Broccoli",
                    category="veg",
                    calories=55,
                    protein_g=4,
                    carbs_g=11,
                    fiber_g=5,
                ),
                _ing(
                    "candy",
                    "Candy",
                    category="other",
                    calories=250,
                    protein_g=1,
                    carbs_g=40,
                    fat_g=10,
                    in_stock=False,
                ),
            ]
        }
        plan = generate_meal_plan(inv, FULL_TARGETS, EMPTY_CONSUMED)
        self.assertTrue(plan["items"], msg=plan.get("message"))
        self.assertTrue(plan["meals"])
        self.assertFalse(plan["notes"]["empty_plan"])
        names = {i["name"] for i in plan["items"]}
        self.assertNotIn("Candy", names)
        self.assertTrue(names <= {"Chicken breast", "Brown rice", "Broccoli"})
        self.assertGreater(plan["planned_totals"]["protein_g"], 0)

    def test_veg_slot_before_shake_fill(self):
        inv = {
            "ingredients": [
                _ing(
                    "whey",
                    "Chocolate whey protein",
                    category="protein",
                    calories=120,
                    protein_g=30,
                    carbs_g=3,
                    fat_g=1,
                ),
                _ing(
                    "broccoli",
                    "Broccoli",
                    category="veg",
                    calories=55,
                    protein_g=4,
                    carbs_g=11,
                    fiber_g=5,
                ),
            ]
        }
        plan = generate_meal_plan(inv, FULL_TARGETS, EMPTY_CONSUMED)
        self.assertTrue(plan["items"])
        names = [i["name"] for i in plan["items"]]
        self.assertIn("Broccoli", names)
        self.assertTrue(plan["notes"]["veg_slot_filled"])
        veg_idx = names.index("Broccoli")
        shake_idxs = [i for i, n in enumerate(names) if "whey" in n.lower()]
        self.assertTrue(shake_idxs)
        self.assertLessEqual(veg_idx, min(shake_idxs))
        kinds = {h["kind"] for h in plan["honesty"]}
        self.assertNotIn("veg_slot", kinds)

    def test_soft_fiber_biases_fill_without_inventing(self):
        inv = {
            "ingredients": [
                _ing(
                    "chicken",
                    "Chicken breast",
                    category="protein",
                    calories=280,
                    protein_g=52,
                    fat_g=6,
                ),
                _ing(
                    "broccoli",
                    "Broccoli",
                    category="veg",
                    calories=55,
                    protein_g=4,
                    carbs_g=11,
                    fiber_g=8,
                ),
                _ing(
                    "oats",
                    "Oats",
                    category="carb",
                    calories=150,
                    protein_g=5,
                    carbs_g=27,
                    fat_g=3,
                    fiber_g=4,
                ),
            ]
        }
        plan = generate_meal_plan(inv, FULL_TARGETS, EMPTY_CONSUMED)
        names = {i["name"] for i in plan["items"]}
        self.assertTrue({"Broccoli", "Oats"} & names)
        self.assertLessEqual(SOFT_FIBER_TARGET_G, 30)
        self.assertGreaterEqual(SOFT_FIBER_TARGET_G, 25)
        blocked = {
            "ingredients": [
                _ing(
                    "chicken",
                    "Chicken breast",
                    category="protein",
                    calories=280,
                    protein_g=52,
                    fat_g=6,
                ),
                _ing(
                    "spinach",
                    "Spinach",
                    category="veg",
                    calories=20,
                    protein_g=2,
                    carbs_g=3,
                    fiber_g=2,
                    in_stock=False,
                ),
            ]
        }
        miss = generate_meal_plan(blocked, FULL_TARGETS, EMPTY_CONSUMED)
        miss_names = {i["name"] for i in miss["items"]}
        self.assertNotIn("Spinach", miss_names)
        self.assertTrue(miss["notes"]["fiber_miss"])
        self.assertTrue(any(h["kind"] == "fiber" for h in miss["honesty"]))

    def test_shake_cap_when_whole_food_protein_exists(self):
        inv = {
            "ingredients": [
                _ing(
                    "whey",
                    "Whey isolate",
                    category="protein",
                    calories=110,
                    protein_g=40,
                    carbs_g=2,
                    fat_g=1,
                ),
                _ing(
                    "chicken",
                    "Chicken breast",
                    category="protein",
                    calories=280,
                    protein_g=20,
                    fat_g=6,
                ),
            ]
        }
        plan = generate_meal_plan(inv, FULL_TARGETS, EMPTY_CONSUMED)
        shake_servings = sum(
            int(i.get("servings") or 1) for i in plan["items"] if is_shake_or_powder(i)
        )
        shake_p = sum(
            float(i.get("protein_g") or 0) for i in plan["items"] if is_shake_or_powder(i)
        )
        self.assertLessEqual(shake_servings, SHAKE_MAX_SERVINGS)
        self.assertLessEqual(shake_p, SHAKE_MAX_POWDER_PROTEIN_G + 0.05)
        self.assertTrue(any(i["name"] == "Chicken breast" for i in plan["items"]))
        self.assertFalse(plan["notes"]["shake_cap_escaped"])
        self.assertTrue(
            plan["notes"]["shake_cap_applied"] or shake_servings <= SHAKE_MAX_SERVINGS
        )

    def test_shake_cap_escape_when_pantry_cannot_hit_210p(self):
        inv = {
            "ingredients": [
                _ing(
                    "whey",
                    "Chocolate whey protein",
                    category="protein",
                    calories=120,
                    protein_g=30,
                    carbs_g=3,
                    fat_g=1,
                )
            ]
        }
        plan = generate_meal_plan(inv, FULL_TARGETS, EMPTY_CONSUMED)
        self.assertTrue(plan["items"])
        self.assertTrue(plan["notes"]["shake_cap_escaped"])
        shake_servings = sum(int(i.get("servings") or 1) for i in plan["items"])
        self.assertGreater(shake_servings, SHAKE_MAX_SERVINGS)
        self.assertTrue(any(h["kind"] == "shake_cap" for h in plan["honesty"]))
        self.assertTrue(all(is_shake_or_powder(i) for i in plan["items"]))

    def test_pantry_blocked_empty_plan_is_honest_not_silent(self):
        inv = {
            "ingredients": [
                _ing(
                    "dessert",
                    "Giant dessert",
                    category="other",
                    calories=900,
                    protein_g=2,
                    carbs_g=120,
                    fat_g=40,
                )
            ]
        }
        consumed = {"calories": 2010, "protein_g": 20, "carbs_g": 160, "fat_g": 50}
        plan = generate_meal_plan(inv, FULL_TARGETS, consumed)
        self.assertFalse(plan["items"])
        self.assertFalse(plan["meals"])
        self.assertTrue(plan["notes"]["empty_plan"])
        self.assertEqual(plan["notes"]["empty_plan_reason"], "pantry_blocked")
        self.assertTrue(plan["notes"]["regen_attempted"])
        self.assertTrue(any(h["kind"] == "empty_plan" for h in plan["honesty"]))
        self.assertIn("No plan", plan["message"])
        self.assertTrue(
            any("invent" in (h.get("text") or "").lower() for h in plan["honesty"])
            or "invent" in (plan.get("message") or "").lower()
        )
        self.assertEqual({i["name"] for i in plan["items"]}, set())

    def test_empty_stock_is_pantry_unavailable_not_no_stock(self):
        """Empty ingredients list is a dark pantry — never no_stock."""
        plan = generate_meal_plan({"ingredients": []}, FULL_TARGETS, EMPTY_CONSUMED)
        self.assertFalse(plan["items"])
        self.assertTrue(plan.get("pantry_dark"))
        self.assertEqual(plan["notes"]["empty_plan_reason"], "pantry_unavailable")
        self.assertNotEqual(plan["notes"]["empty_plan_reason"], "no_stock")
        self.assertTrue(any(h["kind"] == "empty_plan" for h in plan["honesty"]))

    def test_oos_stock_is_no_stock_not_dark(self):
        """Known pantry with nothing in stock is no_stock, not pantry_unavailable."""
        inv = {
            "ingredients": [
                _ing(
                    "chicken",
                    "Chicken",
                    category="protein",
                    calories=280,
                    protein_g=52,
                    fat_g=6,
                    in_stock=False,
                )
            ]
        }
        plan = generate_meal_plan(inv, FULL_TARGETS, EMPTY_CONSUMED)
        self.assertFalse(plan["items"])
        self.assertFalse(plan.get("pantry_dark"))
        self.assertEqual(plan["notes"]["empty_plan_reason"], "no_stock")
        self.assertTrue(any(h["kind"] == "empty_plan" for h in plan["honesty"]))


class TestMealPlanDiversity(unittest.TestCase):
    """#513: distinct ingredients secondary under target closeness + ε."""

    def _stocked_two_veg(self):
        return {
            "ingredients": [
                _ing(
                    "chicken",
                    "Chicken breast",
                    category="protein",
                    calories=280,
                    protein_g=52,
                    fat_g=6,
                    serving_g=170,
                ),
                _ing(
                    "rice",
                    "Brown rice",
                    category="carb",
                    calories=215,
                    protein_g=5,
                    carbs_g=45,
                    fat_g=2,
                    serving_g=195,
                ),
                _ing(
                    "broccoli",
                    "Broccoli",
                    category="veg",
                    calories=55,
                    protein_g=4,
                    carbs_g=11,
                    fiber_g=5,
                    serving_g=180,
                ),
                _ing(
                    "spinach",
                    "Spinach",
                    category="veg",
                    calories=20,
                    protein_g=2,
                    carbs_g=3,
                    fiber_g=2,
                    serving_g=85,
                ),
                _ing(
                    "yogurt",
                    "Greek yogurt",
                    category="protein",
                    calories=130,
                    protein_g=20,
                    carbs_g=8,
                    fat_g=0,
                    serving_g=200,
                ),
                _ing(
                    "candy",
                    "Candy",
                    category="other",
                    calories=250,
                    protein_g=1,
                    carbs_g=40,
                    fat_g=10,
                    in_stock=False,
                ),
            ]
        }

    def test_primary_closeness_secondary_diversity_eps_documented(self):
        """AC1: primary = closeness, secondary = distinct ingredients + ε."""
        self.assertEqual(DIVERSITY_EPS["calories"], 80.0)
        self.assertEqual(DIVERSITY_EPS["protein_g"], 8.0)
        self.assertEqual(DIVERSITY_EPS_KCAL, 80.0)
        self.assertEqual(DIVERSITY_EPS_PROTEIN_G, 8.0)
        doc = generate_meal_plan.__doc__ or ""
        self.assertIn("primary", doc.lower())
        self.assertIn("secondary", doc.lower())
        self.assertIn("DIVERSITY_EPS", doc)
        plan = generate_meal_plan(self._stocked_two_veg(), FULL_TARGETS, EMPTY_CONSUMED)
        self.assertEqual(plan["notes"]["diversity_primary"], "target_closeness")
        self.assertEqual(plan["notes"]["diversity_secondary"], "distinct_ingredients")
        self.assertEqual(plan["notes"]["diversity_eps_kcal"], 80.0)
        self.assertEqual(plan["notes"]["diversity_eps_protein_g"], 8.0)
        best = {"calories": 100.0, "protein_g": 10.0, "carbs_g": 20.0, "fat_g": 5.0}
        self.assertTrue(
            within_diversity_eps(
                best,
                {"calories": 180.0, "protein_g": 18.0, "carbs_g": 35.0, "fat_g": 13.0},
            )
        )
        self.assertFalse(
            within_diversity_eps(
                best,
                {"calories": 181.0, "protein_g": 10.0, "carbs_g": 20.0, "fat_g": 5.0},
            )
        )
        self.assertFalse(
            within_diversity_eps(
                best,
                {"calories": 100.0, "protein_g": 18.1, "carbs_g": 20.0, "fat_g": 5.0},
            )
        )

    def test_stocked_two_veg_uses_both(self):
        """AC2: ≥2 distinct veg when both close the gap; no off-pantry foods."""
        plan = generate_meal_plan(self._stocked_two_veg(), FULL_TARGETS, EMPTY_CONSUMED)
        self.assertTrue(plan["items"], msg=plan.get("message"))
        names = {i["name"] for i in plan["items"]}
        self.assertNotIn("Candy", names)
        veg_names = {
            i["name"]
            for i in plan["items"]
            if i.get("is_veg_or_fruit") or is_veg_or_fruit(i)
        }
        self.assertGreaterEqual(len(veg_names), 2, msg=f"veg={veg_names} items={names}")
        self.assertTrue({"Broccoli", "Spinach"} <= veg_names)
        self.assertGreaterEqual(plan["notes"]["distinct_ingredients"], 2)
        self.assertFalse(plan["notes"]["diversity_limited"])
        self.assertFalse(any(h["kind"] == "diversity" for h in plan["honesty"]))
        meal_veg = set()
        for meal in plan["meals"]:
            for it in meal.get("items") or []:
                if it.get("is_veg_or_fruit") or is_veg_or_fruit(it):
                    meal_veg.add(it["name"])
        self.assertGreaterEqual(len(meal_veg), 2)

    def test_thin_pantry_repeats_without_inventing(self):
        """AC3: thin pantry may repeat; honest limited-diversity note; no invented food."""
        inv = {
            "ingredients": [
                _ing(
                    "chicken",
                    "Chicken breast",
                    category="protein",
                    calories=280,
                    protein_g=52,
                    fat_g=6,
                    serving_g=170,
                ),
                _ing(
                    "spinach",
                    "Spinach",
                    category="veg",
                    calories=20,
                    protein_g=2,
                    carbs_g=3,
                    fiber_g=2,
                    serving_g=85,
                    in_stock=False,
                ),
            ]
        }
        plan = generate_meal_plan(inv, FULL_TARGETS, EMPTY_CONSUMED)
        names = {i["name"] for i in plan["items"]}
        self.assertEqual(names, {"Chicken breast"})
        self.assertNotIn("Spinach", names)
        self.assertTrue(plan["items"])
        chicken = plan["items"][0]
        self.assertGreater(float(chicken.get("servings") or 1), 1.05)
        self.assertEqual(plan["notes"]["distinct_ingredients"], 1)
        self.assertTrue(plan["notes"]["diversity_limited"])
        self.assertTrue(any(h["kind"] == "diversity" for h in plan["honesty"]))
        self.assertTrue(
            any(
                "not inventing" in (h.get("text") or "").lower()
                for h in plan["honesty"]
                if h.get("kind") == "diversity"
            )
        )

    def test_protein_not_worsened_beyond_eps(self):
        """AC4: 210P closeness not worse than ε on the happy-path fixture."""
        plan = generate_meal_plan(self._stocked_two_veg(), FULL_TARGETS, EMPTY_CONSUMED)
        self.assertEqual(plan["targets"]["protein_g"], 210)
        rem_p = float(plan["remaining_after_plan"]["protein_g"])
        planned_p = float(plan["planned_totals"]["protein_g"])
        self.assertLessEqual(rem_p, DIVERSITY_EPS_PROTEIN_G)
        self.assertGreaterEqual(planned_p, 210 - DIVERSITY_EPS_PROTEIN_G)
        rem_cal = float(plan["remaining_after_plan"]["calories"])
        self.assertLessEqual(rem_cal, DIVERSITY_EPS_KCAL + 50)

    def test_veg_before_shake_and_shake_cap_still_hold(self):
        """AC5: #501 veg-before-shake + shake cap with escape still hold."""
        before_shake = {
            "ingredients": [
                _ing(
                    "whey",
                    "Chocolate whey protein",
                    category="protein",
                    calories=120,
                    protein_g=30,
                    carbs_g=3,
                    fat_g=1,
                ),
                _ing(
                    "broccoli",
                    "Broccoli",
                    category="veg",
                    calories=55,
                    protein_g=4,
                    carbs_g=11,
                    fiber_g=5,
                ),
            ]
        }
        plan = generate_meal_plan(before_shake, FULL_TARGETS, EMPTY_CONSUMED)
        names = [i["name"] for i in plan["items"]]
        self.assertIn("Broccoli", names)
        self.assertTrue(plan["notes"]["veg_slot_filled"])
        veg_idx = names.index("Broccoli")
        shake_idxs = [i for i, n in enumerate(names) if "whey" in n.lower()]
        self.assertTrue(shake_idxs)
        self.assertLessEqual(veg_idx, min(shake_idxs))
        cap = {
            "ingredients": [
                _ing(
                    "whey",
                    "Whey isolate",
                    category="protein",
                    calories=110,
                    protein_g=40,
                    carbs_g=2,
                    fat_g=1,
                ),
                _ing(
                    "chicken",
                    "Chicken breast",
                    category="protein",
                    calories=280,
                    protein_g=20,
                    fat_g=6,
                ),
            ]
        }
        capped = generate_meal_plan(cap, FULL_TARGETS, EMPTY_CONSUMED)
        shake_servings = sum(
            float(i.get("servings") or 1)
            for i in capped["items"]
            if is_shake_or_powder(i)
        )
        shake_p = sum(
            float(i.get("protein_g") or 0)
            for i in capped["items"]
            if is_shake_or_powder(i)
        )
        self.assertLessEqual(shake_servings, SHAKE_MAX_SERVINGS + 0.05)
        self.assertLessEqual(shake_p, SHAKE_MAX_POWDER_PROTEIN_G + 0.05)
        self.assertTrue(any(i["name"] == "Chicken breast" for i in capped["items"]))
        self.assertFalse(capped["notes"]["shake_cap_escaped"])
        escape = generate_meal_plan(
            {
                "ingredients": [
                    _ing(
                        "whey",
                        "Chocolate whey protein",
                        category="protein",
                        calories=120,
                        protein_g=30,
                        carbs_g=3,
                        fat_g=1,
                    )
                ]
            },
            FULL_TARGETS,
            EMPTY_CONSUMED,
        )
        self.assertTrue(escape["notes"]["shake_cap_escaped"])
        self.assertTrue(any(h["kind"] == "shake_cap" for h in escape["honesty"]))

    def test_stored_targets_unchanged(self):
        """AC7: no stored target number change (2100 / 210P / 180C / 55F)."""
        self.assertEqual(DEFAULT_TARGETS["calories"], 2100)
        self.assertEqual(DEFAULT_TARGETS["protein_g"], 210)
        self.assertEqual(DEFAULT_TARGETS["carbs_g"], 180)
        self.assertEqual(DEFAULT_TARGETS["fat_g"], 55)
        import json

        checked = 0
        for path in (
            ROOT / "fitness" / "nutrition" / "targets.json",
            ROOT.parents[0] / "fitness" / "nutrition" / "targets.json",
        ):
            if not path.is_file():
                continue
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(float(stored["calories"]), 2100, msg=str(path))
            self.assertEqual(float(stored["protein_g"]), 210, msg=str(path))
            self.assertEqual(float(stored["carbs_g"]), 180, msg=str(path))
            self.assertEqual(float(stored["fat_g"]), 55, msg=str(path))
            checked += 1
        self.assertGreaterEqual(checked, 1)
        plan = generate_meal_plan({"ingredients": []}, FULL_TARGETS, EMPTY_CONSUMED)
        self.assertEqual(plan["targets"]["protein_g"], 210)
        self.assertEqual(plan["targets"]["calories"], 2100)


class TestInventoryNeedSuggestions(unittest.TestCase):
    """#502 need-based add/remove · #503 portion_g optional · #504 Nourish deltas."""

    def test_protein_floor_stays_210(self):
        self.assertEqual(DEFAULT_TARGETS["protein_g"], 210)
        self.assertEqual(DEFAULT_TARGETS["calories"], 2100)

    def test_high_log_junk_does_not_outrank_need_produce(self):
        """AC1: log-frequency is not a positive add signal."""
        inv = {
            "ingredients": [
                _ing(
                    "whey",
                    "Whey protein",
                    category="protein",
                    calories=120,
                    protein_g=24,
                    carbs_g=3,
                    fat_g=1,
                    serving_g=30,
                ),
            ]
        }
        logs = [
            FoodLogEntry(
                date="2026-09-01",
                name="Candy bar",
                calories=250,
                protein_g=2,
                carbs_g=40,
                fat_g=10,
            )
        ] * 12
        out = suggest_inventory_staples(
            inv,
            targets=FULL_TARGETS,
            food_logs=logs,
            consumed=EMPTY_CONSUMED,
            max_suggestions=10,
        )
        self.assertEqual(out.get("ranking"), "need")
        self.assertFalse(out.get("log_frequency_positive"))
        names = [s["name"].lower() for s in out["suggestions"]]
        self.assertFalse(any("candy" in n for n in names))
        for s in out["suggestions"]:
            blob = f"{s.get('reason') or ''} {s.get('need') or ''}".lower()
            self.assertNotIn("logged", blob)
            self.assertNotRegex(blob, r"\d+\s*[×x]|times")
        veg = [
            s
            for s in out["suggestions"]
            if s.get("action") == "add"
            and (
                (s.get("category") or "") == "veg"
                or "broccoli" in (s.get("name") or "").lower()
                or "spinach" in (s.get("name") or "").lower()
            )
        ]
        self.assertTrue(veg, msg=names)

    def test_add_includes_catalog_food_absent_from_logs(self):
        """AC2: catalog produce never logged still surfaces for a stated need."""
        inv = {
            "ingredients": [
                _ing(
                    "eggs",
                    "Whole eggs",
                    category="protein",
                    calories=210,
                    protein_g=18,
                    carbs_g=2,
                    fat_g=15,
                    serving_g=150,
                ),
            ]
        }
        logs = [
            FoodLogEntry(
                date="2026-09-02",
                name="Whey protein",
                calories=120,
                protein_g=24,
                carbs_g=3,
                fat_g=1,
            )
        ] * 8
        logged_names = {str(f.name).lower() for f in logs}
        out = suggest_inventory_staples(
            inv,
            targets=FULL_TARGETS,
            food_logs=logs,
            consumed=EMPTY_CONSUMED,
            max_suggestions=10,
        )
        adds = [s for s in out["suggestions"] if s.get("action") == "add"]
        self.assertTrue(adds)
        absent = [
            s
            for s in adds
            if not any(
                tok in logged_names for tok in (s.get("name") or "").lower().split()
            )
        ]
        self.assertTrue(absent, msg=[s.get("name") for s in adds])
        veg_or_fiber = [
            s
            for s in absent
            if inventory_gap_role(s) == "veg_fiber"
            or (s.get("category") or "") == "veg"
            or "broccoli" in (s.get("name") or "").lower()
        ]
        self.assertTrue(veg_or_fiber, msg=[s.get("name") for s in absent])
        pick = veg_or_fiber[0]
        self.assertTrue(pick.get("need") or pick.get("reason"))
        self.assertNotIn("logged", (pick.get("reason") or "").lower())

    def test_each_add_has_need_reason(self):
        """AC3: human-readable need reason, not log count."""
        inv = {"ingredients": [_ing("eggs", "Eggs", category="protein", protein_g=18, calories=210)]}
        out = suggest_inventory_staples(
            inv, targets=FULL_TARGETS, food_logs=[], consumed=EMPTY_CONSUMED
        )
        adds = [s for s in out["suggestions"] if s.get("action") == "add"]
        self.assertTrue(adds)
        for s in adds:
            reason = (s.get("need") or s.get("reason") or "").strip()
            self.assertTrue(reason)
            self.assertNotIn("logged", reason.lower())
            self.assertTrue(s.get("proposal"))

    def test_rare_log_alone_does_not_propose_remove(self):
        """AC4: in-stock chicken never logged is not a removal candidate."""
        inv = {
            "ingredients": [
                _ing(
                    "chicken",
                    "Chicken breast",
                    category="protein",
                    calories=280,
                    protein_g=52,
                    fat_g=6,
                    serving_g=170,
                ),
                _ing(
                    "broccoli",
                    "Broccoli",
                    category="veg",
                    calories=55,
                    protein_g=4,
                    carbs_g=11,
                    fiber_g=5,
                    serving_g=180,
                ),
            ]
        }
        out = suggest_inventory_removals(
            inv, targets=FULL_TARGETS, food_logs=[], max_suggestions=6
        )
        names = [s["name"].lower() for s in out["suggestions"]]
        self.assertFalse(any("chicken" in n for n in names))
        self.assertFalse(any("broccoli" in n for n in names))
        for s in out["suggestions"]:
            self.assertNotIn("logged", (s.get("reason") or "").lower())

    def test_remove_suggestions_are_need_based(self):
        """AC4: supplements / low-utility OOS still propose remove."""
        inv = {
            "ingredients": [
                _ing(
                    "chicken",
                    "Chicken breast",
                    category="protein",
                    calories=280,
                    protein_g=52,
                    fat_g=6,
                ),
                _ing(
                    "mens-vitamins",
                    "Men's Vitamins, Natural Berry Flavor",
                    category="carb",
                    calories=15,
                    protein_g=0,
                    carbs_g=4,
                    fat_g=0,
                ),
            ]
        }
        out = suggest_inventory_removals(inv, targets=FULL_TARGETS, food_logs=[])
        names = " ".join(s["name"].lower() for s in out["suggestions"])
        self.assertIn("vitamin", names)
        self.assertNotIn("chicken", names)
        for s in out["suggestions"]:
            self.assertEqual(s["action"], "remove")
            self.assertTrue(s.get("reason") or s.get("need"))
            self.assertTrue(s.get("proposal"))

    def test_catalog_blocked_is_honest_empty(self):
        """AC6: empty catalog → no fake staples."""
        inv = {"ingredients": [_ing("eggs", "Eggs", category="protein", protein_g=18, calories=210)]}
        out = suggest_inventory_staples(
            inv,
            targets=FULL_TARGETS,
            food_logs=[],
            consumed=EMPTY_CONSUMED,
            catalog=[],
        )
        self.assertEqual(out["suggestions"], [])
        self.assertEqual(out["count"], 0)
        kinds = {h["kind"] for h in out.get("honesty") or []}
        self.assertIn("catalog_blocked", kinds)
        self.assertIn("invent", (out.get("summary") or "").lower())

    def test_serving_only_add_without_portion_g(self):
        """#503 AC1/AC4: serving-based item saves with null grams."""
        banana = {
            "name": "Banana",
            "category": "carb",
            "serving_label": "1 medium",
            "calories": 105,
            "protein_g": 1.3,
            "carbs_g": 27,
            "fat_g": 0.4,
        }
        self.assertTrue(needs_serving_grams(banana))
        self.assertFalse(serving_grams_required(banana))
        inv = add_ingredient({"ingredients": []}, banana)
        row = inv["ingredients"][0]
        self.assertIsNone(row.get("serving_g"))
        self.assertEqual(row["serving_label"], "1 medium")
        qty = suggested_qty_for_item(row)
        self.assertEqual(qty["unit"], "servings")
        self.assertIsNone(qty["portion_g"])

    def test_grams_item_uses_partial_portions(self):
        """#503 AC2: serving_g present → planner may use portion_g; absent → servings."""
        with_g = _ing(
            "chicken",
            "Chicken breast",
            category="protein",
            calories=280,
            protein_g=52,
            fat_g=6,
            serving_g=170,
        )
        pick = _pick_continuous_portion(
            with_g,
            {"calories": 800, "protein_g": 80, "carbs_g": 0, "fat_g": 0},
            900,
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        )
        self.assertIsNotNone(pick)
        self.assertIsNotNone(pick[1])
        no_g = {
            "name": "Banana",
            "category": "carb",
            "serving_label": "1 medium",
            "calories": 105,
            "protein_g": 1.3,
            "carbs_g": 27,
            "fat_g": 0.4,
        }
        pick2 = _pick_continuous_portion(
            no_g,
            {"calories": 800, "protein_g": 80, "carbs_g": 180, "fat_g": 0},
            900,
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        )
        self.assertIsNotNone(pick2)
        self.assertIsNone(pick2[1])

    def test_generic_one_serving_does_not_require_grams(self):
        """#503 AC1: generic 1 serving is allowed (logged serving still blocked)."""
        generic = {
            "name": "Cottage cheese",
            "serving_label": "1 serving",
            "calories": 180,
            "protein_g": 28,
            "carbs_g": 8,
            "fat_g": 2.5,
        }
        self.assertFalse(serving_grams_required(generic))
        inv = add_ingredient({"ingredients": []}, generic)
        self.assertIsNone(inv["ingredients"][0].get("serving_g"))

    def test_suggested_qty_servings_or_grams(self):
        """#504 AC3 / #503: qty may be grams or servings."""
        gram_item = next(s for s in STAPLE_CATALOG if s.get("id") == "chicken-breast")
        gqty = suggested_qty_for_item(gram_item)
        self.assertEqual(gqty["unit"], "g")
        self.assertGreater(gqty["portion_g"], 0)
        banana = next(s for s in STAPLE_CATALOG if s.get("id") == "banana")
        sqty = suggested_qty_for_item(banana)
        self.assertEqual(sqty["unit"], "servings")
        self.assertIsNone(sqty["portion_g"])
        inv = {"ingredients": [_ing("eggs", "Eggs", category="protein", protein_g=18, calories=210)]}
        out = suggest_inventory_staples(
            inv, targets=FULL_TARGETS, food_logs=[], consumed=EMPTY_CONSUMED, max_suggestions=12
        )
        units = { (s.get("suggested_qty") or {}).get("unit") for s in out["suggestions"] if s.get("action") == "add" }
        self.assertTrue(units & {"g", "servings"})
        banana_s = [s for s in out["suggestions"] if "banana" in (s.get("name") or "").lower()]
        if banana_s:
            self.assertEqual(banana_s[0]["suggested_qty"]["unit"], "servings")

    def test_last_unique_gap_filler_blocked_without_paired_add(self):
        """#504 AC1: sole veg-fiber with empty macros cannot drop unless a covering add exists."""
        inv = {
            "ingredients": [
                _ing(
                    "chicken",
                    "Chicken breast",
                    category="protein",
                    calories=280,
                    protein_g=52,
                    fat_g=6,
                    serving_g=170,
                ),
                _ing(
                    "spinach-empty",
                    "Spinach",
                    category="veg",
                    calories=0,
                    protein_g=0,
                    carbs_g=0,
                    fat_g=0,
                ),
            ]
        }
        blocked = suggest_inventory_removals(
            inv, targets=FULL_TARGETS, food_logs=[], paired_adds=[]
        )
        names = [s["name"].lower() for s in blocked["suggestions"]]
        self.assertFalse(any("spinach" in n for n in names))
        self.assertFalse(any("chicken" in n for n in names))
        covering = {
            "id": "broccoli",
            "name": "Broccoli",
            "category": "veg",
            "action": "add",
            "calories": 60,
            "protein_g": 5,
            "carbs_g": 12,
            "fiber_g": 5,
            "reason": "Fills soft fiber / veg gap.",
        }
        allowed = suggest_inventory_removals(
            inv, targets=FULL_TARGETS, food_logs=[], paired_adds=[covering]
        )
        spin = [s for s in allowed["suggestions"] if "spinach" in s["name"].lower()]
        self.assertTrue(spin)
        self.assertEqual((spin[0].get("paired_add") or {}).get("name"), "Broccoli")

    def test_fiber_veg_prefers_whole_food_over_shake(self):
        """#504 AC2: fiber/veg gap ranks produce above powder."""
        inv = {
            "ingredients": [
                _ing(
                    "eggs",
                    "Whole eggs",
                    category="protein",
                    calories=210,
                    protein_g=18,
                    carbs_g=2,
                    fat_g=15,
                    serving_g=150,
                ),
            ]
        }
        catalog = [
            {
                "id": "whey-protein",
                "name": "Whey protein",
                "category": "protein",
                "serving_g": 30,
                "calories": 120,
                "protein_g": 24,
                "carbs_g": 3,
                "fat_g": 1,
            },
            {
                "id": "broccoli",
                "name": "Broccoli",
                "category": "veg",
                "serving_g": 180,
                "calories": 60,
                "protein_g": 5,
                "carbs_g": 12,
                "fiber_g": 5,
            },
        ]
        out = suggest_inventory_staples(
            inv,
            targets=FULL_TARGETS,
            food_logs=[
                FoodLogEntry(
                    date="2026-09-03",
                    name="Whey protein",
                    calories=120,
                    protein_g=24,
                    carbs_g=3,
                    fat_g=1,
                )
            ]
            * 10,
            consumed=EMPTY_CONSUMED,
            catalog=catalog,
            max_suggestions=8,
        )
        by_name = {s["name"].lower(): s for s in out["suggestions"]}
        self.assertIn("broccoli", by_name)
        broc = by_name["broccoli"]
        whey = by_name.get("whey protein")
        self.assertGreater(float(broc["score"]), float((whey or {"score": -99})["score"]))
        self.assertIn("fiber", (broc.get("reason") or "").lower() + (broc.get("need") or "").lower())

    def test_ui_marks_grams_optional(self):
        """#503 AC3."""
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        attrs = html.split('id="ing-serving-g"', 1)[1].split(">", 1)[0]
        self.assertNotIn("required", attrs)
        self.assertIn("optional", html.split("Portion (g)", 1)[1][:80].lower())
        self.assertIn("preferred, not required", js)
        self.assertIn("data-action=\"suggest-dismiss\"", js)

    def test_accept_path_is_explicit(self):
        """#502 AC5: dismiss does not write; apply uses POST."""
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        dismiss = js.split('action === "suggest-dismiss"', 1)[1].split("if (action ===", 1)[0]
        self.assertNotIn("fetch(", dismiss)
        self.assertNotIn("/api/inventory/", dismiss)
        self.assertIn('fetch("/api/inventory/add"', js)
        self.assertIn("dropAppliedSuggestion", js)


def _egg_whole(**kwargs):
    row = _ing(
        "eggs-whole",
        "Whole eggs",
        category="protein",
        serving_g=150,
        serving_label="150g",
        calories=210,
        protein_g=18,
        carbs_g=2,
        fat_g=15,
        in_stock=True,
    )
    row.update(kwargs)
    return row


def _egg_whites(**kwargs):
    row = _ing(
        "egg-whites",
        "Egg whites",
        category="protein",
        serving_g=243,
        serving_label="243g",
        calories=125,
        protein_g=26,
        carbs_g=2,
        fat_g=0,
        in_stock=True,
    )
    row.update(kwargs)
    return row


class TestEggPairing(unittest.TestCase):
    """#532: whole eggs + egg whites are one grouped meal component."""

    def test_egg_role_ids_and_names(self):
        self.assertEqual(egg_role({"id": "eggs-whole"}), "eggs-whole")
        self.assertEqual(egg_role({"id": "egg-whites"}), "egg-whites")
        self.assertEqual(egg_role({"name": "Whole eggs"}), "eggs-whole")
        self.assertEqual(egg_role({"name": "Egg whites"}), "egg-whites")
        self.assertIsNone(egg_role({"id": "eggplant", "name": "Eggplant"}))
        self.assertIsNone(egg_role({"id": "chicken"}))

    def test_whites_intent_pairs_wholes_when_both_stocked(self):
        whites = _plan_item_from_ingredient(_egg_whites(), servings=1)
        items = [whites]
        rem = {"calories": 400, "protein_g": 40, "carbs_g": 20, "fat_g": 10}
        totals = {"calories": 125, "protein_g": 26, "carbs_g": 2, "fat_g": 0, "fiber_g": 0}
        honesty, note = ensure_egg_pair(
            items, [_egg_whole(), _egg_whites()], rem, totals
        )
        ids = {it["id"] for it in items}
        self.assertIn("egg-whites", ids)
        self.assertIn("eggs-whole", ids)
        self.assertEqual(note, "complete")
        self.assertFalse(honesty)
        self.assertTrue(all(it.get("group_id") == "eggs" for it in items if egg_role(it)))

    def test_wholes_intent_pairs_whites_when_both_stocked(self):
        wholes = _plan_item_from_ingredient(_egg_whole(), servings=1)
        items = [wholes]
        rem = {"calories": 400, "protein_g": 40, "carbs_g": 20, "fat_g": 10}
        totals = {"calories": 210, "protein_g": 18, "carbs_g": 2, "fat_g": 15, "fiber_g": 0}
        honesty, note = ensure_egg_pair(
            items, [_egg_whole(), _egg_whites()], rem, totals
        )
        ids = {it["id"] for it in items}
        self.assertEqual(ids, {"eggs-whole", "egg-whites"})
        self.assertEqual(note, "complete")
        self.assertFalse(honesty)

    def test_neither_does_not_force_eggs(self):
        chicken = _ing(
            "chicken",
            "Chicken",
            category="protein",
            serving_g=170,
            calories=280,
            protein_g=52,
            fat_g=6,
        )
        inv = {"ingredients": [chicken, _egg_whole(in_stock=False), _egg_whites(in_stock=False)]}
        plan = generate_meal_plan(inv, FULL_TARGETS, EMPTY_CONSUMED)
        ids = {it.get("id") for it in plan["items"]}
        self.assertNotIn("eggs-whole", ids)
        self.assertNotIn("egg-whites", ids)
        self.assertIsNone(plan["notes"].get("egg_pair"))

    def test_whites_stocked_wholes_oos_notes_no_invent(self):
        inv = {
            "ingredients": [
                _egg_whites(),
                _egg_whole(in_stock=False),
                _ing(
                    "rice",
                    "Rice",
                    category="carb",
                    serving_g=195,
                    calories=215,
                    protein_g=5,
                    carbs_g=45,
                    fat_g=2,
                ),
            ]
        }
        plan = generate_meal_plan(inv, FULL_TARGETS, EMPTY_CONSUMED)
        ids = {it.get("id") for it in plan["items"]}
        self.assertIn("egg-whites", ids)
        self.assertNotIn("eggs-whole", ids)
        self.assertEqual(plan["notes"]["egg_pair"], "whites_only")
        kinds = [h.get("kind") for h in plan["honesty"]]
        self.assertIn("egg_pair", kinds)
        text = " ".join(h.get("text") or "" for h in plan["honesty"] if h.get("kind") == "egg_pair")
        self.assertIn("not inventing", text.lower())

    def test_wholes_stocked_whites_oos_notes_no_invent(self):
        inv = {
            "ingredients": [
                _egg_whole(),
                _egg_whites(in_stock=False),
            ]
        }
        plan = generate_meal_plan(inv, FULL_TARGETS, EMPTY_CONSUMED)
        ids = {it.get("id") for it in plan["items"]}
        self.assertIn("eggs-whole", ids)
        self.assertNotIn("egg-whites", ids)
        self.assertEqual(plan["notes"]["egg_pair"], "wholes_only")
        self.assertTrue(any(h.get("kind") == "egg_pair" for h in plan["honesty"]))

    def test_generate_pairs_on_same_meal_grouped(self):
        inv = {
            "ingredients": [
                _egg_whole(),
                _egg_whites(),
                _ing(
                    "rice",
                    "Rice",
                    category="carb",
                    serving_g=195,
                    calories=215,
                    protein_g=5,
                    carbs_g=45,
                    fat_g=2,
                ),
            ]
        }
        plan = generate_meal_plan(inv, FULL_TARGETS, EMPTY_CONSUMED)
        ids = {it.get("id") for it in plan["items"]}
        self.assertIn("eggs-whole", ids)
        self.assertIn("egg-whites", ids)
        self.assertEqual(plan["notes"]["egg_pair"], "complete")
        egg_meals = [
            m
            for m in plan["meals"]
            if any(egg_role(it) for it in m.get("items") or [])
        ]
        self.assertEqual(len(egg_meals), 1)
        meal = egg_meals[0]
        egg_items = [it for it in meal["items"] if egg_role(it)]
        self.assertGreaterEqual(len(egg_items), 2)
        self.assertEqual(egg_items[0].get("group_id"), "eggs")
        self.assertEqual(egg_items[1].get("group_id"), "eggs")
        self.assertTrue(meal.get("egg_pair", {}).get("complete"))
        roles = [egg_role(it) for it in meal["items"]]
        egg_n = sum(1 for r in roles if r)
        self.assertGreaterEqual(egg_n, 2)
        self.assertTrue(all(roles[:egg_n]), msg=roles)
        self.assertIn("eggs-whole", roles[:egg_n])
        self.assertIn("egg-whites", roles[:egg_n])

    def test_colocate_moves_split_eggs_onto_one_meal(self):
        meals = [
            {
                "label": "Lunch",
                "items": [_plan_item_from_ingredient(_egg_whole(), servings=1)],
                "totals": {},
            },
            {
                "label": "Dinner",
                "items": [_plan_item_from_ingredient(_egg_whites(), servings=1)],
                "totals": {},
            },
        ]
        out = colocate_egg_pair(meals)
        egg_meals = [m for m in out if any(egg_role(it) for it in m["items"])]
        self.assertEqual(len(egg_meals), 1)
        ids = [it["id"] for it in egg_meals[0]["items"] if egg_role(it)]
        self.assertEqual(ids, ["eggs-whole", "egg-whites"])
        self.assertTrue(egg_meals[0]["egg_pair"]["complete"])


if __name__ == "__main__":
    unittest.main()
