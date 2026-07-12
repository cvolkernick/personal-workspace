"""Nutrition rollup/log parsing (Google Health field shapes)."""

from __future__ import annotations

import unittest

from rt_dashboard.google_health import parse_nutrition_log_points, parse_nutrition_rollup


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


if __name__ == "__main__":
    unittest.main()
