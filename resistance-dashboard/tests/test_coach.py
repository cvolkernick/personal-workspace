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
from rt_dashboard.nutrition_planner import generate_meal_plan


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
        self.assertTrue(any(a.get("kind") == "training" for a in board.get("actions") or []))
        brief = build_coach_brief(today=board, weekly={"bullets": ["Training: 3 sessions"]}, recovery=rec)
        self.assertIn("rest", brief["markdown"].lower())

    def test_caution_score_without_rest_plan_is_not_rest(self):
        """Sparse / rest-gate-off: score 35 must not print Rest next to a lift slot."""
        rec = RecoveryStatus(label="Caution", score=35.0, reasons=["low sleep"])
        board = build_today_board(
            as_of="2026-07-11",
            recovery=rec,
            workout_plan={"is_rest_day": False, "session_type": "pull", "exercises": []},
            meal_plan={"remaining_before_plan": {"calories": 500, "protein_g": 40}},
            consumed={"calories": 1000, "protein_g": 80},
            targets={"calories": 2100, "protein_g": 200},
            adherence={"protein": {"pct": 50}, "sleep": {"pct": 40}},
        )
        self.assertNotEqual(board["recommendation"], "rest")
        self.assertEqual(board["workout"]["session_type"], "pull")
        self.assertFalse(board["workout"]["is_rest_day"])

    def test_already_trained_today_does_not_ask_for_next_ppl(self):
        rec = RecoveryStatus(label="Caution", score=45.0, reasons=["unit"])
        board = build_today_board(
            as_of="2026-08-29",
            recovery=rec,
            workout_plan={
                "is_rest_day": False,
                "already_trained_today": True,
                "session_type": "legs",
                "next_session_type": "push",
                "exercises": [],
                "message": "Already trained today (LEGS). Next session: PUSH tomorrow.",
            },
            meal_plan={"remaining_before_plan": {"calories": 500, "protein_g": 40}},
            consumed={"calories": 1000, "protein_g": 80},
            targets={"calories": 2100, "protein_g": 200},
            adherence={"protein": {"pct": 50}, "sleep": {"pct": 40}},
        )
        self.assertEqual(board["recommendation"], "done")
        self.assertTrue(board["workout"]["already_trained_today"])
        self.assertEqual(board["workout"]["session_type"], "legs")
        train = [a for a in board["actions"] if a.get("id") == "train-session"]
        self.assertEqual(len(train), 1)
        self.assertIn("Already trained today", train[0]["text"])
        self.assertIn("PUSH", train[0]["text"])
        self.assertNotIn("Easy PUSH", train[0]["text"])

    def test_today_guide_stock_only_meal_and_purchases(self):
        """Shipped meal planner + today board: only stocked ids; restock when OOS."""
        inv = {
            "ingredients": [
                {
                    "id": "chicken-breast",
                    "name": "Chicken breast",
                    "in_stock": True,
                    "calories": 280,
                    "protein_g": 52,
                    "carbs_g": 0,
                    "fat_g": 6,
                    "serving_label": "6 oz",
                },
                {
                    "id": "rice",
                    "name": "Rice",
                    "in_stock": True,
                    "calories": 200,
                    "protein_g": 4,
                    "carbs_g": 45,
                    "fat_g": 0,
                    "serving_label": "1 cup",
                },
                {
                    "id": "eggs",
                    "name": "Eggs",
                    "in_stock": False,
                    "calories": 140,
                    "protein_g": 12,
                    "carbs_g": 1,
                    "fat_g": 10,
                    "serving_label": "2 eggs",
                },
            ]
        }
        targets = {
            "calories": 2100,
            "protein_g": 200,
            "carbs_g": 180,
            "fat_g": 55,
        }
        consumed = {"calories": 600, "protein_g": 40, "carbs_g": 50, "fat_g": 20}
        plan = generate_meal_plan(inv, targets, consumed)
        stocked_ids = {"chicken-breast", "rice"}
        for it in plan.get("items") or []:
            self.assertIn(it.get("id"), stocked_ids)
        self.assertNotIn("eggs", {it.get("id") for it in (plan.get("items") or [])})

        from rt_dashboard.nutrition_planner import suggest_inventory_staples

        sug = suggest_inventory_staples(inv, targets=targets, food_logs=[], consumed=consumed)
        rec = RecoveryStatus(label="Ready", score=80.0, reasons=["ok"])
        board = build_today_board(
            as_of="2026-07-11",
            recovery=rec,
            workout_plan={
                "is_rest_day": False,
                "session_type": "push",
                "exercises": [
                    {
                        "name": "DB Flat Press",
                        "prescription": {"weight_lbs": 50, "sets": 3, "reps": 10},
                        "primary_muscles": ["chest"],
                    }
                ],
                "message": "Suggested PUSH",
            },
            meal_plan=plan,
            consumed=consumed,
            targets=targets,
            adherence={"protein": {"pct": 60}, "sleep": {"pct": 70}},
            inventory_suggestions=sug,
            food_logs_today=[],
        )
        self.assertEqual(board["recommendation"], "train")
        self.assertTrue(board.get("targets"))
        self.assertTrue(any(t.get("motivation") for t in board["targets"]))
        self.assertTrue(board["nutrition"].get("food_logs_fp"))
        self.assertEqual(len(board["nutrition"]["food_logs_fp"]), 16)
        self.assertEqual(board["meal"].get("food_logs_fp"), board["nutrition"]["food_logs_fp"])
        meal_ids = {it.get("id") for it in (board.get("meal") or {}).get("items") or []}
        self.assertTrue(meal_ids <= stocked_ids)
        purchases = board.get("purchases") or []
        self.assertTrue(
            any(
                (p.get("action") == "restock" and "egg" in str(p.get("name") or "").lower())
                or p.get("action") in ("restock", "add")
                for p in purchases
            ),
            msg=f"expected restock/add purchases, got {purchases}",
        )
        # Eggs OOS should surface as restock when suggestions work
        self.assertTrue(
            any("egg" in str(p.get("name") or "").lower() for p in purchases)
            or any(p.get("action") == "restock" for p in purchases)
        )

    def test_today_remaining_macros_track_logged_intake(self):
        rec = RecoveryStatus(label="Ready", score=75.0, reasons=[])
        targets = {"calories": 2000, "protein_g": 200, "carbs_g": 180, "fat_g": 50}
        low = build_today_board(
            as_of="2026-07-11",
            recovery=rec,
            workout_plan={"is_rest_day": False, "session_type": "pull", "exercises": []},
            meal_plan={},
            consumed={"calories": 500, "protein_g": 50, "carbs_g": 40, "fat_g": 15},
            targets=targets,
            adherence={},
        )
        high = build_today_board(
            as_of="2026-07-11",
            recovery=rec,
            workout_plan={"is_rest_day": False, "session_type": "pull", "exercises": []},
            meal_plan={},
            consumed={"calories": 1500, "protein_g": 150, "carbs_g": 120, "fat_g": 40},
            targets=targets,
            adherence={},
        )
        self.assertGreater(
            low["nutrition"]["remaining"]["calories"],
            high["nutrition"]["remaining"]["calories"],
        )
        self.assertGreater(
            low["nutrition"]["remaining"]["protein_g"],
            high["nutrition"]["remaining"]["protein_g"],
        )
        protein_acts = [
            a for a in low.get("actions") or [] if a.get("id") == "protein-remaining"
        ]
        self.assertEqual(len(protein_acts), 1)
        self.assertIn("Cover remaining protein", protein_acts[0]["text"])
        self.assertIn("~150 g", protein_acts[0]["text"])
        # Target rows also reflect progress
        low_p = next(t for t in low["targets"] if t["id"] == "protein_g")
        high_p = next(t for t in high["targets"] if t["id"] == "protein_g")
        self.assertGreater(high_p["consumed"], low_p["consumed"])
        self.assertLess(high_p["remaining"], low_p["remaining"])

    def test_today_empty_stock_purchase_recommendation(self):
        rec = RecoveryStatus(label="Ready", score=80.0, reasons=[])
        plan = generate_meal_plan(
            {"ingredients": []},
            {"calories": 2100, "protein_g": 200, "carbs_g": 180, "fat_g": 55},
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        )
        board = build_today_board(
            as_of="2026-07-11",
            recovery=rec,
            workout_plan={"is_rest_day": False, "session_type": "legs", "exercises": []},
            meal_plan=plan,
            consumed={"calories": 0, "protein_g": 0},
            targets={"calories": 2100, "protein_g": 200},
            adherence={},
            inventory_suggestions={"suggestions": []},
        )
        self.assertTrue(board["meal"].get("empty"))
        self.assertTrue(board.get("purchases"))
        self.assertTrue(any("stock" in (p.get("reason") or "").lower() or p.get("name") for p in board["purchases"]))

    def test_today_pantry_dark_is_unavailable_not_restock_copy(self):
        rec = RecoveryStatus(label="Ready", score=80.0, reasons=[])
        plan = generate_meal_plan(
            {"ingredients": []},
            {"calories": 2100, "protein_g": 200, "carbs_g": 180, "fat_g": 55},
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        )
        board = build_today_board(
            as_of="2026-07-11",
            recovery=rec,
            workout_plan={"is_rest_day": False, "session_type": "legs", "exercises": []},
            meal_plan=plan,
            consumed={"calories": 0, "protein_g": 0},
            targets={"calories": 2100, "protein_g": 200},
            adherence={},
            inventory_suggestions={"suggestions": []},
            inventory_dark=True,
        )
        self.assertTrue(board["meal"].get("empty"))
        self.assertEqual(board["meal"].get("message"), "Pantry unavailable")
        self.assertEqual(board["meal"].get("empty_reason"), "pantry_unavailable")
        self.assertFalse(board.get("purchases"))

    def test_today_oos_is_no_in_stock_items(self):
        rec = RecoveryStatus(label="Ready", score=80.0, reasons=[])
        plan = generate_meal_plan(
            {
                "ingredients": [
                    {
                        "id": "chicken",
                        "name": "Chicken",
                        "calories": 280,
                        "protein_g": 52,
                        "carbs_g": 0,
                        "fat_g": 6,
                        "in_stock": False,
                    }
                ]
            },
            {"calories": 2100, "protein_g": 200, "carbs_g": 180, "fat_g": 55},
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        )
        board = build_today_board(
            as_of="2026-07-11",
            recovery=rec,
            workout_plan={"is_rest_day": False, "session_type": "legs", "exercises": []},
            meal_plan=plan,
            consumed={"calories": 0, "protein_g": 0},
            targets={"calories": 2100, "protein_g": 200},
            adherence={},
            inventory_suggestions={"suggestions": []},
        )
        self.assertTrue(board["meal"].get("empty"))
        self.assertEqual(board["meal"].get("message"), "No in-stock items")
        self.assertEqual(board["meal"].get("empty_reason"), "no_in_stock")

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
        self.assertIn("Nutrition Coach", fc["markdown"])


if __name__ == "__main__":
    unittest.main()
