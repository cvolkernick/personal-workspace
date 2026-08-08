"""Unit tests for daily plan task planning (no live Google Tasks calls)."""

from __future__ import annotations

import unittest

from rt_dashboard.daily_plan_tasks import (
    make_notes,
    parse_notes,
    plan_from_today_board,
)


class TestDailyPlanTasks(unittest.TestCase):
    def test_notes_roundtrip(self):
        n = make_notes(day="2026-08-08", group="training", slug="session")
        self.assertIn("fitdash:v1", n)
        meta = parse_notes(n)
        self.assertEqual(meta["day"], "2026-08-08")
        self.assertEqual(meta["group"], "training")
        self.assertEqual(meta["slug"], "session")

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
                "items": [
                    {"name": "Chicken", "serving_label": "210g"},
                    {"name": "Rice", "serving_label": "195g"},
                ]
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
        # protein action + 2 foods
        self.assertGreaterEqual(len(by["nutrition"].items), 3)
        titles = " ".join(i.title for i in by["training"].items)
        self.assertIn("PUSH", titles)
        self.assertIn("DB Press", titles)

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
