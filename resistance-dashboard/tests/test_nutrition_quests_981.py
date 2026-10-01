"""Today nutrition quests are log and shop only (#981).

Remaining protein is allocated into meal items. Ahead calorie pace shifts
remaining meal clocks inside the waking window. Neither becomes a quest.
"""

from __future__ import annotations

import re
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.coach import build_today_board  # noqa: E402
from rt_dashboard.daily_plan_tasks import plan_preview  # noqa: E402
from rt_dashboard.models import RecoveryStatus  # noqa: E402
from rt_dashboard.nutrition_planner import generate_meal_plan  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY = "2026-08-22"
TARGETS = {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55}
ADVISORY_RE = re.compile(
    r"cover remaining protein|calorie pace|slow intake|don't skip planned",
    re.I,
)
MEAL_HEAD_RE = re.compile(
    r"^(Next meal|Later meal|Evening|Optional snack|Earlier meal|Eat)\b",
    re.I,
)
STOCKED = {
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
    ]
}


def _nutrition_items(preview: dict) -> list:
    for group in preview.get("groups") or []:
        if group.get("group") == "nutrition":
            return list(group.get("items") or [])
    return []


def _shopping_items(preview: dict) -> list:
    for group in preview.get("groups") or []:
        if group.get("group") == "shopping":
            return list(group.get("items") or [])
    return []


class TestNutritionQuests981(unittest.TestCase):
    def _board(
        self,
        consumed: dict,
        *,
        now: datetime,
        window_start: datetime,
        window_end: datetime,
        pacing_status: str | None = None,
        suggestions: dict | None = None,
    ) -> dict:
        plan = generate_meal_plan(
            STOCKED,
            TARGETS,
            consumed,
            now=now,
            tz_name="America/New_York",
            window_start=window_start,
            window_end=window_end,
        )
        bars = None
        if pacing_status:
            bars = {"pacing": {"status": pacing_status, "summary": pacing_status}}
        board = build_today_board(
            as_of=DAY,
            recovery=RecoveryStatus(label="Ready", score=80.0, reasons=[]),
            workout_plan={
                "is_rest_day": False,
                "session_type": "push",
                "exercises": [],
            },
            meal_plan=plan,
            consumed=consumed,
            targets=TARGETS,
            adherence={},
            calorie_bars=bars,
            inventory_suggestions=suggestions,
        )
        board["date"] = DAY
        board["_plan"] = plan
        return board

    def _assert_quests_actionable(self, preview: dict) -> None:
        titles = [str(it.get("title") or "") for it in _nutrition_items(preview)]
        for title in titles:
            self.assertIsNone(
                ADVISORY_RE.search(title),
                msg=f"advisory nutrition quest: {title}",
            )
            head = title.split(": ", 1)[0]
            self.assertRegex(head, MEAL_HEAD_RE)
            self.assertIn(": ", title)
            self.assertNotIn("~0 g", title)
            self.assertNotRegex(title, r"\(\s*0\s*g\s*\)")

    def test_unallocated_protein_lands_in_meals_not_a_quest(self):
        now = datetime(2026, 8, 22, 11, 0, tzinfo=ET)
        start = now.replace(hour=8, minute=0)
        end = now.replace(hour=22, minute=0)
        consumed = {"calories": 400, "protein_g": 40, "carbs_g": 40, "fat_g": 10}
        board = self._board(
            consumed,
            now=now,
            window_start=start,
            window_end=end,
            suggestions={
                "suggestions": [
                    {
                        "action": "restock",
                        "name": "Eggs",
                        "reason": "needed for meals",
                    }
                ]
            },
        )
        plan = board.pop("_plan")
        meals = plan.get("meals") or []
        self.assertGreaterEqual(len(meals), 2)
        meal_protein = sum(
            float((m.get("totals") or {}).get("protein_g") or 0) for m in meals
        )
        self.assertGreaterEqual(meal_protein, 150.0)
        self.assertLessEqual(
            float((plan.get("remaining_after_plan") or {}).get("protein_g") or 0),
            20.0,
        )
        for meal in meals:
            self.assertGreater(float((meal.get("totals") or {}).get("protein_g") or 0), 0)
            for item in meal.get("items") or []:
                self.assertGreater(float(item.get("protein_g") or 0), 0)
        action_ids = {a.get("id") for a in board.get("actions") or []}
        self.assertNotIn("protein-remaining", action_ids)
        self.assertNotIn("calorie-pace", action_ids)
        preview = plan_preview(board, day=DAY)
        self._assert_quests_actionable(preview)
        self.assertGreaterEqual(len(_nutrition_items(preview)), 2)
        shop = _shopping_items(preview)
        self.assertTrue(any("Eggs" in str(it.get("title") or "") for it in shop))
        self.assertTrue(
            all(str(it.get("title") or "").startswith(("Restock", "Get", "Add")) for it in shop)
        )

    def test_ahead_pace_shifts_meals_and_drops_the_quest(self):
        now = datetime(2026, 8, 22, 12, 0, tzinfo=ET)
        start = now.replace(hour=8, minute=0)
        end = now.replace(hour=22, minute=0)
        ahead = self._board(
            {"calories": 1500, "protein_g": 80, "carbs_g": 100, "fat_g": 40},
            now=now,
            window_start=start,
            window_end=end,
            pacing_status="ahead",
        )
        behind = self._board(
            {"calories": 200, "protein_g": 20, "carbs_g": 30, "fat_g": 8},
            now=now,
            window_start=start,
            window_end=end,
            pacing_status="behind",
        )
        ahead_plan = ahead.pop("_plan")
        behind_plan = behind.pop("_plan")
        ahead_meals = ahead_plan.get("meals") or []
        behind_meals = behind_plan.get("meals") or []
        self.assertTrue(ahead_meals)
        self.assertTrue(behind_meals)
        ahead_first = datetime.fromisoformat(ahead_meals[0]["eat_at"])
        behind_first = datetime.fromisoformat(behind_meals[0]["eat_at"])
        self.assertGreater(ahead_first, behind_first)
        self.assertGreater(ahead_first, now)
        self.assertLessEqual(ahead_first, end)
        self.assertLessEqual(behind_first, end)
        for meal in ahead_meals + behind_meals:
            eat = datetime.fromisoformat(meal["eat_at"])
            self.assertGreaterEqual(eat, start)
            self.assertLessEqual(eat, end)
        ahead_protein = sum(
            float((m.get("totals") or {}).get("protein_g") or 0) for m in ahead_meals
        )
        self.assertGreater(ahead_protein, 40.0)
        for board in (ahead, behind):
            ids = {a.get("id") for a in board.get("actions") or []}
            self.assertNotIn("calorie-pace", ids)
            self.assertNotIn("protein-remaining", ids)
            texts = " ".join(str(a.get("text") or "") for a in board.get("actions") or [])
            self.assertIsNone(ADVISORY_RE.search(texts))
            preview = plan_preview(board, day=DAY)
            self._assert_quests_actionable(preview)

    def test_near_zero_protein_does_not_mint_a_zero_gram_quest(self):
        now = datetime(2026, 8, 22, 11, 0, tzinfo=ET)
        start = now.replace(hour=8, minute=0)
        end = now.replace(hour=22, minute=0)
        board = self._board(
            {"calories": 2000, "protein_g": 208, "carbs_g": 170, "fat_g": 50},
            now=now,
            window_start=start,
            window_end=end,
        )
        plan = board.pop("_plan")
        self.assertLessEqual(
            float((plan.get("remaining_before_plan") or {}).get("protein_g") or 0),
            5.0,
        )
        for item in plan.get("items") or []:
            self.assertGreater(float(item.get("protein_g") or 0), 0)
        texts = " ".join(str(a.get("text") or "") for a in board.get("actions") or [])
        self.assertIsNone(ADVISORY_RE.search(texts))
        preview = plan_preview(board, day=DAY)
        self._assert_quests_actionable(preview)
        self.assertNotIn("~0", " ".join(
            str(it.get("title") or "") for it in _nutrition_items(preview)
        ))
