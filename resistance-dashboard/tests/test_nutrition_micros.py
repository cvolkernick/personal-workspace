"""#241: surface fiber/sodium/sugar only when nutrients{} already has them."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.models import FoodLogEntry, NutritionDay  # noqa: E402
from rt_dashboard.nutrition_micros import (  # noqa: E402
    merge_day_micros,
    micros_from_nutrients,
    sum_micros,
)
from rt_dashboard.nutrition_planner import (  # noqa: E402
    food_logs_for_day,
    today_consumed_from_nutrition,
)


class TestMicrosFromNutrients(unittest.TestCase):
    def test_dict_present_keys_only(self):
        m = micros_from_nutrients(
            {"DIETARY_FIBER": 8, "SODIUM": 0.12, "SUGAR": 4, "IRON": 0.001}
        )
        self.assertEqual(m["fiber_g"], 8)
        self.assertEqual(m["sugar_g"], 4)
        self.assertEqual(m["sodium_g"], 0.12)
        self.assertEqual(m["sodium_mg"], 120)
        self.assertNotIn("iron_g", m)

    def test_absent_keys_omitted_never_invented(self):
        self.assertEqual(micros_from_nutrients({}), {})
        self.assertEqual(micros_from_nutrients(None), {})
        self.assertEqual(micros_from_nutrients({"PROTEIN": 30}), {})
        self.assertNotIn("fiber_g", micros_from_nutrients({"SODIUM": 0.2}))
        self.assertNotIn("sugar_g", micros_from_nutrients({"SODIUM": 0.2}))

    def test_logged_zero_is_kept(self):
        m = micros_from_nutrients({"DIETARY_FIBER": 0, "SUGAR": 0})
        self.assertEqual(m["fiber_g"], 0)
        self.assertEqual(m["sugar_g"], 0)

    def test_gh_list_shape(self):
        m = micros_from_nutrients(
            [
                {"nutrient": "DIETARY_FIBER", "quantity": {"grams": 5}},
                {"nutrient": "SODIUM", "quantity": {"grams": 0.074}},
                {"nutrient": "PROTEIN", "quantity": {"grams": 31}},
            ]
        )
        self.assertEqual(m["fiber_g"], 5)
        self.assertEqual(m["sodium_mg"], 74)
        self.assertNotIn("sugar_g", m)

    def test_sodium_mg_scale_heuristic(self):
        m = micros_from_nutrients({"SODIUM": 2300})
        self.assertEqual(m["sodium_g"], 2.3)
        self.assertEqual(m["sodium_mg"], 2300)

    def test_added_sugars_is_not_sugar(self):
        self.assertEqual(micros_from_nutrients({"ADDED_SUGARS": 12}), {})

    def test_fiber_alias(self):
        self.assertEqual(micros_from_nutrients({"FIBER": 6})["fiber_g"], 6)

    def test_sum_skips_missing_keys_not_zero(self):
        s = sum_micros(
            [
                {"DIETARY_FIBER": 5, "SODIUM": 0.1},
                {"SUGAR": 8},
            ]
        )
        self.assertEqual(s["fiber_g"], 5)
        self.assertEqual(s["sugar_g"], 8)
        self.assertEqual(s["sodium_mg"], 100)

    def test_day_rollup_wins_over_meal_sum(self):
        merged = merge_day_micros(
            {"DIETARY_FIBER": 20},
            [{"DIETARY_FIBER": 5}, {"DIETARY_FIBER": 5}],
        )
        self.assertEqual(merged["fiber_g"], 20)


class TestTodayConsumedMicros(unittest.TestCase):
    def test_day_payload_micros_when_nutrients_present(self):
        days = [
            NutritionDay(
                date="2026-07-11",
                calories=500,
                protein_g=40,
                carbs_g=30,
                fat_g=10,
                nutrients={"DIETARY_FIBER": 12, "SODIUM": 0.8, "SUGAR": 18},
            )
        ]
        consumed = today_consumed_from_nutrition(days, as_of="2026-07-11")
        self.assertEqual(consumed["micros"]["fiber_g"], 12)
        self.assertEqual(consumed["micros"]["sodium_mg"], 800)
        self.assertEqual(consumed["micros"]["sugar_g"], 18)
        self.assertIn("DIETARY_FIBER", consumed["nutrients"])

    def test_no_micros_when_keys_absent(self):
        days = [
            NutritionDay(date="2026-07-11", calories=500, protein_g=40, carbs_g=30, fat_g=10)
        ]
        consumed = today_consumed_from_nutrition(days, as_of="2026-07-11")
        self.assertNotIn("micros", consumed)
        self.assertNotIn("nutrients", consumed)

    def test_food_logs_fill_when_day_lacks_keys(self):
        logs = [
            FoodLogEntry(
                date="2026-07-11",
                name="Chicken",
                calories=280,
                protein_g=52,
                nutrients={"DIETARY_FIBER": 0, "SODIUM": 0.12},
            ),
            FoodLogEntry(
                date="2026-07-11",
                name="Yogurt",
                calories=150,
                protein_g=20,
                nutrients={"CALCIUM": 0.2},
            ),
        ]
        consumed = today_consumed_from_nutrition([], as_of="2026-07-11", food_logs=logs)
        self.assertEqual(consumed["micros"]["fiber_g"], 0)
        self.assertEqual(consumed["micros"]["sodium_mg"], 120)
        self.assertNotIn("sugar_g", consumed["micros"])

    def test_food_logs_for_day_adds_micros_only_when_present(self):
        logs = [
            FoodLogEntry(
                date="2026-07-11",
                name="Chicken",
                calories=280,
                nutrients={"DIETARY_FIBER": 0, "SODIUM": 0.12},
            ),
            FoodLogEntry(date="2026-07-11", name="Yogurt", calories=150, nutrients={}),
        ]
        rows = food_logs_for_day(logs, as_of="2026-07-11")
        self.assertEqual(rows[0]["micros"]["fiber_g"], 0)
        self.assertEqual(rows[0]["micros"]["sodium_mg"], 120)
        self.assertNotIn("micros", rows[1])


if __name__ == "__main__":
    unittest.main()
