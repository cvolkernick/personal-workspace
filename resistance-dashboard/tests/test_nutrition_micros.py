"""#241 / #537: surface fiber/sodium/sugar only when nutrients{} already has them."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.dashboard import _today_consumed  # noqa: E402
from rt_dashboard.models import FoodLogEntry, HealthSnapshot, NutritionDay  # noqa: E402
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


class TestDashboardTodayConsumedMicros(unittest.TestCase):
    """Vercel ``_today_consumed`` must merge day micros like Pi (#537)."""

    def test_food_logs_attach_fiber_sodium_sugar(self):
        health = HealthSnapshot(
            nutrition=[
                NutritionDay(
                    date="2026-09-08",
                    calories=900,
                    protein_g=80,
                    carbs_g=60,
                    fat_g=20,
                )
            ],
            food_logs=[
                FoodLogEntry(
                    date="2026-09-08",
                    name="Oats",
                    calories=300,
                    protein_g=10,
                    nutrients={"DIETARY_FIBER": 8, "SODIUM": 0.04, "SUGAR": 6},
                ),
                FoodLogEntry(
                    date="2026-09-08",
                    name="Chicken",
                    calories=280,
                    protein_g=52,
                    nutrients={"DIETARY_FIBER": 0, "SODIUM": 0.12},
                ),
            ],
        )
        consumed = _today_consumed(health, "2026-09-08")
        self.assertEqual(consumed["calories"], 900)
        self.assertEqual(consumed["protein_g"], 80)
        self.assertEqual(consumed["carbs_g"], 60)
        self.assertEqual(consumed["fat_g"], 20)
        self.assertEqual(consumed["micros"]["fiber_g"], 8)
        self.assertEqual(consumed["micros"]["sodium_mg"], 160)
        self.assertEqual(consumed["micros"]["sugar_g"], 6)

    def test_logs_only_still_merge_micros(self):
        health = HealthSnapshot(
            food_logs=[
                FoodLogEntry(
                    date="2026-09-08",
                    name="Yogurt",
                    calories=150,
                    protein_g=20,
                    nutrients={"DIETARY_FIBER": 0, "SUGAR": 9},
                )
            ]
        )
        consumed = _today_consumed(health, "2026-09-08")
        self.assertEqual(consumed["calories"], 150)
        self.assertEqual(consumed["source"], "food_logs")
        self.assertEqual(consumed["micros"]["fiber_g"], 0)
        self.assertEqual(consumed["micros"]["sugar_g"], 9)
        self.assertNotIn("sodium_g", consumed["micros"])
        self.assertNotIn("sodium_mg", consumed["micros"])

    def test_absent_keys_omitted_empty_day_is_empty(self):
        health = HealthSnapshot(
            nutrition=[
                NutritionDay(date="2026-09-08", calories=400, protein_g=30, carbs_g=20, fat_g=10)
            ],
            food_logs=[
                FoodLogEntry(date="2026-09-08", name="Whey", calories=120, nutrients={})
            ],
        )
        consumed = _today_consumed(health, "2026-09-08")
        self.assertEqual(consumed["calories"], 400)
        self.assertNotIn("micros", consumed)
        self.assertNotIn("nutrients", consumed)
        self.assertEqual(_today_consumed(HealthSnapshot(), "2026-09-08"), {})

    def test_dashboard_food_logs_for_day_attaches_meal_micros(self):
        src = (ROOT / "api" / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn("today_consumed_from_nutrition", src)
        self.assertIn("merge_day_micros", src)
        self.assertIn("food_logs_for_day", src)
        logs = [
            FoodLogEntry(
                date="2026-09-08",
                name="Oats",
                calories=300,
                nutrients={"DIETARY_FIBER": 8, "SODIUM": 0.04, "SUGAR": 6},
            )
        ]
        rows = food_logs_for_day(logs, as_of="2026-09-08")
        self.assertEqual(rows[0]["micros"]["fiber_g"], 8)
        self.assertEqual(rows[0]["micros"]["sodium_mg"], 40)
        self.assertEqual(rows[0]["micros"]["sugar_g"], 6)


class TestMicrosLineFromStoreJs(unittest.TestCase):
    def test_fallback_from_food_logs_when_consumed_empty(self):
        script = ROOT / "tests" / "nutrition_micros_line.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok nutrition-micros-line", proc.stdout)

    def test_render_uses_store_fallback(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        render = js.split("function renderNutritionMicros", 1)[1].split(
            "function fmtNumShort", 1
        )[0]
        self.assertIn("microsFromStore(store)", render)
        self.assertIn("microChipHtml", render)
        self.assertIn("Fiber · Sodium · Sugar", render)
        self.assertNotIn("<details open", render)
        self.assertNotIn("microsLine(c)", render)


if __name__ == "__main__":
    unittest.main()
