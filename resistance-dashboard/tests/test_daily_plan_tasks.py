"""Unit tests for daily plan task planning (no live Google Tasks calls)."""

from __future__ import annotations

import unittest

from rt_dashboard.daily_plan_tasks import (
    cache_key,
    plan_from_today_board,
    plan_preview,
)


class TestDailyPlanTasks(unittest.TestCase):
    def test_cache_key(self):
        self.assertEqual(cache_key("training", "session"), "training|session")

    def test_plan_preview_fast(self):
        prev = plan_preview(
            {
                "date": "2026-08-08",
                "actions": [{"kind": "training", "text": "Train", "id": "t"}],
                "workout": {"is_rest_day": True, "exercises": []},
                "meal": {"meals": [], "items": []},
                "purchases": [],
            }
        )
        self.assertEqual(prev["source"], "plan_preview")
        self.assertTrue(prev["groups"])

    def test_plan_groups_from_board(self):
        board = {
            "date": "2026-08-08",
            "actions": [
                {
                    "kind": "training",
                    "text": "Complete today's PUSH session",
                    "id": "train-session",
                },
                {
                    "kind": "nutrition",
                    "text": "Cover remaining protein",
                    "id": "protein-gap",
                },
                {
                    "kind": "shopping",
                    "text": "Restock chicken",
                    "id": "shop-chicken",
                },
                {
                    "kind": "sleep",
                    "text": "Protect bedtime",
                    "id": "sleep-bed",
                },
            ],
            "workout": {
                "is_rest_day": False,
                "exercises": [
                    {"name": "DB Press", "sets": 3, "reps": 10, "weight_lbs": 50}
                ],
            },
            "meal": {
                "meals": [
                    {
                        "label": "Next meal",
                        "items": [
                            {"name": "Chicken", "serving_label": "210g"},
                            {"name": "Rice", "serving_label": "195g"},
                        ],
                    },
                    {
                        "label": "Later meal",
                        "items": [{"name": "Yogurt", "serving_label": "200g"}],
                    },
                ],
                "items": [],  # flat list unused when meals present
            },
            "purchases": [{"name": "Greek yogurt", "action": "restock", "reason": "OOS"}],
        }
        groups = plan_from_today_board(board, day="2026-08-08")
        by = {g.group: g for g in groups}
        self.assertIn("training", by)
        self.assertIn("nutrition", by)
        self.assertIn("shopping", by)
        self.assertIn("sleep", by)
        # session + exercise
        self.assertGreaterEqual(len(by["training"].items), 2)
        # protein action + 3 foods across meal buckets
        self.assertGreaterEqual(len(by["nutrition"].items), 4)
        titles = " ".join(i.title for i in by["training"].items)
        self.assertIn("PUSH", titles)
        self.assertIn("DB Press", titles)
        meal_titles = " ".join(i.title for i in by["nutrition"].items)
        self.assertIn("Next meal", meal_titles)
        self.assertIn("Later meal", meal_titles)
        self.assertTrue(any(i.meal_label == "Next meal" for i in by["nutrition"].items))

    def test_rest_day_skips_exercises(self):
        board = {
            "date": "2026-08-08",
            "actions": [{"kind": "training", "text": "Rest day", "id": "rest"}],
            "workout": {
                "is_rest_day": True,
                "exercises": [{"name": "Squat", "sets": 3, "reps": 5}],
            },
            "meal": {"items": []},
            "purchases": [],
        }
        groups = plan_from_today_board(board)
        train = next(g for g in groups if g.group == "training")
        self.assertEqual(len(train.items), 1)
        self.assertIn("Rest", train.items[0].title)


if __name__ == "__main__":
    unittest.main()
