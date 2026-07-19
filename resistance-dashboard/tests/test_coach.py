"""Coach adherence / today board tests."""

from __future__ import annotations

import unittest

from rt_dashboard.coach import (
    build_coach_brief,
    build_food_commentary,
    build_today_board,
    compute_adherence_7d,
)
from rt_dashboard.models import (
    FoodLogEntry,
    HydrationDay,
    NutritionDay,
    RecoveryStatus,
    SleepSample,
)


class TestCoach(unittest.TestCase):
    def test_adherence_protein_hits(self):
        nutrition = [
            NutritionDay(date="2026-07-11", calories=2000, protein_g=200, carbs_g=180, fat_g=50),
            NutritionDay(date="2026-07-10", calories=1500, protein_g=100, carbs_g=180, fat_g=50),
        ]
        adh = compute_adherence_7d(
            targets={"calories": 2100, "protein_g": 200},
            nutrition=nutrition,
            sleep=[SleepSample(date="2026-07-11", sleep_hours=8.0)],
            hydration=[HydrationDay(date="2026-07-11", water_ml=3000)],
            as_of="2026-07-11",
        )
        self.assertEqual(adh["protein"]["hits"], 1)
        self.assertEqual(adh["protein"]["days_logged"], 2)
        self.assertEqual(adh["sleep"]["hits"], 1)

    def test_today_board_rest(self):
        rec = RecoveryStatus(label="Needs Rest", score=25.0, reasons=["low sleep"])
        board = build_today_board(
            as_of="2026-07-11",
            recovery=rec,
            workout_plan={"is_rest_day": True, "session_type": "rest", "exercises": []},
            meal_plan={"remaining_before_plan": {"calories": 500, "protein_g": 40}},
            consumed={"calories": 1000, "protein_g": 80},
            targets={"calories": 2100, "protein_g": 200},
            adherence={"protein": {"pct": 50}, "sleep": {"pct": 40}},
        )
        self.assertEqual(board["recommendation"], "rest")
        brief = build_coach_brief(today=board, weekly={"bullets": ["Training: 3 sessions"]}, recovery=rec)
        self.assertIn("rest", brief["markdown"].lower())

    def test_food_commentary_protein_gap(self):
        logs = [
            FoodLogEntry(
                date="2026-07-11",
                name="Chicken breast",
                calories=280,
                protein_g=52,
                carbs_g=0,
                fat_g=6,
                nutrients={"DIETARY_FIBER": 0, "SODIUM": 0.1},
            ),
            FoodLogEntry(
                date="2026-07-11",
                name="Chips",
                calories=400,
                protein_g=4,
                carbs_g=40,
                fat_g=22,
                nutrients={"DIETARY_FIBER": 2, "SODIUM": 0.5},
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
        adh = {
            "protein": {"pct": 40, "hits": 1, "days_logged": 3},
            "calories": {"pct": 50, "days_logged": 3},
        }
        fc = build_food_commentary(
            food_logs=logs,
            nutrition=[
                NutritionDay(date="2026-07-11", calories=680, protein_g=56, carbs_g=40, fat_g=28),
            ],
            targets={"calories": 2100, "protein_g": 210},
            consumed={"calories": 680, "protein_g": 56},
            adherence=adh,
            labs={
                "panels": [
                    {
                        "date": "2026-01-15",
                        "lab": "Quest",
                        "markers": {"vitamin_d_ng_ml": 18, "ldl_mg_dl": 90},
                        "notes": "",
                    }
                ]
            },
            as_of="2026-07-11",
        )
        self.assertGreaterEqual(fc["today_log_count"], 2)
        self.assertTrue(fc["can_improve"])
        self.assertTrue(any("protein" in x.lower() for x in fc["can_improve"]))
        self.assertTrue(fc["labs"]["has_labs"])
        self.assertTrue(any(f["marker"] == "vitamin_d_ng_ml" for f in fc["labs"]["flags"]))
        self.assertIn("Coach commentary", fc["markdown"])


if __name__ == "__main__":
    unittest.main()
