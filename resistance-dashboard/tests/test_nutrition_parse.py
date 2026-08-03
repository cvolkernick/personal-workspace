"""Nutrition rollup/log parsing (Google Health field shapes)."""

from __future__ import annotations

import unittest

from rt_dashboard.google_health import (
    parse_food_log_entries,
    parse_nutrition_log_points,
    parse_nutrition_rollup,
)


class TestNutritionParse(unittest.TestCase):
    def test_rollup_macros_grams_sum(self):
        payload = {
            "rollupDataPoints": [
                {
                    "civilStartTime": {"date": {"year": 2026, "month": 7, "day": 11}},
                    "nutritionLog": {
                        "nutrients": [
                            {"nutrient": "PROTEIN", "quantity": {"gramsSum": 218.0}},
                        ],
                        "energy": {"kcalSum": 2191.0},
                        "energyFromFat": {"kcalSum": 320.0},
                        "totalCarbohydrate": {"gramsSum": 185.8},
                        "totalFat": {"gramsSum": 58.7},
                    },
                }
            ]
        }
        days = parse_nutrition_rollup(payload)
        self.assertEqual(len(days), 1)
        self.assertEqual(days[0].date, "2026-07-11")
        self.assertEqual(days[0].calories, 2191.0)  # not energyFromFat 320
        self.assertEqual(days[0].protein_g, 218.0)
        self.assertEqual(days[0].carbs_g, 185.8)
        self.assertEqual(days[0].fat_g, 58.7)

    def test_meal_log_aggregates(self):
        payload = {
            "dataPoints": [
                {
                    "nutritionLog": {
                        "interval": {
                            "civilStartTime": {
                                "date": {"year": 2026, "month": 7, "day": 11}
                            }
                        },
                        "energy": {"kcal": 100},
                        "totalCarbohydrate": {"grams": 10},
                        "totalFat": {"grams": 2},
                        "nutrients": [
                            {"nutrient": "PROTEIN", "quantity": {"grams": 20}},
                        ],
                    }
                },
                {
                    "nutritionLog": {
                        "interval": {
                            "civilStartTime": {
                                "date": {"year": 2026, "month": 7, "day": 11}
                            }
                        },
                        "energy": {"kcal": 50},
                        "totalCarbohydrate": {"grams": 5},
                        "totalFat": {"grams": 1},
                        "nutrients": [
                            {"nutrient": "PROTEIN", "quantity": {"grams": 10}},
                        ],
                    }
                },
            ]
        }
        days = parse_nutrition_log_points(payload, days=60)
        self.assertEqual(len(days), 1)
        self.assertEqual(days[0].calories, 150.0)
        self.assertEqual(days[0].protein_g, 30.0)
        self.assertEqual(days[0].carbs_g, 15.0)
        self.assertEqual(days[0].fat_g, 3.0)

    def test_food_log_entries_meal_level(self):
        payload = {
            "dataPoints": [
                {
                    "nutritionLog": {
                        "foodDisplayName": "Chicken breast",
                        "mealType": "LUNCH",
                        "serving": {
                            "amount": 6,
                            "foodMeasurementUnitDisplayName": "oz",
                        },
                        "interval": {
                            "civilStartTime": {
                                "date": {"year": 2026, "month": 7, "day": 11},
                                "time": {"hours": 12, "minutes": 30},
                            }
                        },
                        "energy": {"kcal": 280},
                        "totalCarbohydrate": {"grams": 0},
                        "totalFat": {"grams": 6},
                        "nutrients": [
                            {"nutrient": "PROTEIN", "quantity": {"grams": 52}},
                            {"nutrient": "DIETARY_FIBER", "quantity": {"grams": 0}},
                            {"nutrient": "SODIUM", "quantity": {"grams": 0.12}},
                            {"nutrient": "IRON", "quantity": {"grams": 0.001}},
                        ],
                    }
                },
                {
                    "nutritionLog": {
                        "foodDisplayName": "Greek yogurt",
                        "mealType": "SNACK",
                        "interval": {
                            "civilStartTime": {
                                "date": {"year": 2026, "month": 7, "day": 11},
                                "time": {"hours": 15, "minutes": 0},
                            }
                        },
                        "energy": {"kcal": 150},
                        "totalCarbohydrate": {"grams": 8},
                        "totalFat": {"grams": 2},
                        "nutrients": [
                            {"nutrient": "PROTEIN", "quantity": {"grams": 20}},
                            {"nutrient": "CALCIUM", "quantity": {"grams": 0.2}},
                        ],
                    }
                },
            ]
        }
        entries = parse_food_log_entries(payload, days=60)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].name, "Chicken breast")
        self.assertEqual(entries[0].meal_type, "Lunch")
        self.assertEqual(entries[0].time, "12:30")
        self.assertEqual(entries[0].serving_label, "6 oz")
        self.assertEqual(entries[0].protein_g, 52.0)
        self.assertIn("SODIUM", entries[0].nutrients)
        self.assertEqual(entries[1].name, "Greek yogurt")
        self.assertEqual(entries[1].calories, 150.0)


if __name__ == "__main__":
    unittest.main()
