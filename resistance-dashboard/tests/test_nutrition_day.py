"""Wake-to-sleep nutrition day (#828) — one clock for every surface."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.calorie_bars import build_calorie_bars_payload
from rt_dashboard.models import CaloriesBurnedDay, FoodLogEntry
from rt_dashboard.nutrition_day import (
    WAKE_BACKSTOP_HOURS,
    bucket_burn_by_nutrition_day,
    bucket_intake_by_nutrition_day,
    compose_nutrition_today,
    list_nutrition_days,
    resolve_nutrition_day,
)
from rt_dashboard.nutrition_planner import generate_meal_plan, remaining_macros

ET = ZoneInfo("America/New_York")

FULL_TARGETS = {
    "calories": 2100,
    "protein_g": 210,
    "carbs_g": 180,
    "fat_g": 55,
}
STOCKED = {
    "ingredients": [
        {
            "id": "chicken",
            "name": "Chicken",
            "calories": 280,
            "protein_g": 52,
            "carbs_g": 0,
            "fat_g": 6,
            "in_stock": True,
            "serving_g": 170,
            "serving_label": "6 oz",
        },
        {
            "id": "rice",
            "name": "Rice",
            "calories": 200,
            "protein_g": 4,
            "carbs_g": 45,
            "fat_g": 0,
            "in_stock": True,
            "serving_g": 150,
            "serving_label": "1 cup",
        },
    ]
}


def _log(date, time, calories, name="Meal", **kw):
    return FoodLogEntry(
        date=date,
        name=name,
        calories=calories,
        protein_g=kw.get("protein_g", 20),
        carbs_g=kw.get("carbs_g", 20),
        fat_g=kw.get("fat_g", 5),
        time=time,
    )


def _intervals(*pairs):
    out = []
    for st, en in pairs:
        out.append(
            {
                "start": st.isoformat(timespec="seconds"),
                "end": en.isoformat(timespec="seconds"),
                "source": "google_health",
            }
        )
    return out


class TestNutritionDayBoundary(unittest.TestCase):
    def test_food_at_0130_before_sleep_stays_on_morning_day(self):
        """AC: 1:30 AM food before sleep counts toward the day that began that morning."""
        wake = datetime(2026, 9, 17, 9, 0, tzinfo=ET)
        onset = datetime(2026, 9, 18, 2, 0, tzinfo=ET)
        next_wake = datetime(2026, 9, 18, 8, 0, tzinfo=ET)
        now = datetime(2026, 9, 18, 1, 45, tzinfo=ET)
        iv = _intervals(
            (datetime(2026, 9, 17, 0, 0, tzinfo=ET), wake),
            (onset, next_wake),
        )
        span = resolve_nutrition_day(
            now=now, tz_name="America/New_York", sleep_intervals=iv
        )
        self.assertEqual(span.day_id, "2026-09-17")
        self.assertEqual(span.start, wake)
        logs = [
            _log("2026-09-17", "12:00", 1500, "Lunch"),
            _log("2026-09-18", "01:30", 800, "Late snack"),
        ]
        composed = compose_nutrition_today(
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            food_logs=logs,
        )
        self.assertEqual(composed["today_consumed"]["calories"], 2300.0)
        self.assertEqual(composed["today_consumed"]["food_log_count"], 2)
        names = [r["name"] for r in composed["food_logs_today"]]
        self.assertIn("Late snack", names)

    def test_food_after_sleep_onset_is_new_day(self):
        wake = datetime(2026, 9, 17, 9, 0, tzinfo=ET)
        onset = datetime(2026, 9, 18, 2, 0, tzinfo=ET)
        next_wake = datetime(2026, 9, 18, 8, 0, tzinfo=ET)
        now = datetime(2026, 9, 18, 10, 0, tzinfo=ET)
        iv = _intervals(
            (datetime(2026, 9, 17, 0, 0, tzinfo=ET), wake),
            (onset, next_wake),
        )
        span = resolve_nutrition_day(
            now=now, tz_name="America/New_York", sleep_intervals=iv
        )
        self.assertEqual(span.day_id, "2026-09-18")
        logs = [
            _log("2026-09-18", "01:30", 800, "Late snack"),
            _log("2026-09-18", "09:00", 400, "Breakfast"),
        ]
        composed = compose_nutrition_today(
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            food_logs=logs,
        )
        self.assertEqual(composed["today_consumed"]["calories"], 400.0)
        names = [r["name"] for r in composed["food_logs_today"]]
        self.assertEqual(names, ["Breakfast"])

    def test_nap_does_not_flip_the_day(self):
        wake = datetime(2026, 9, 17, 8, 0, tzinfo=ET)
        nap_st = datetime(2026, 9, 17, 14, 0, tzinfo=ET)
        nap_en = datetime(2026, 9, 17, 14, 40, tzinfo=ET)
        now = datetime(2026, 9, 17, 16, 0, tzinfo=ET)
        iv = _intervals(
            (datetime(2026, 9, 16, 23, 0, tzinfo=ET), wake),
            (nap_st, nap_en),
        )
        span = resolve_nutrition_day(
            now=now, tz_name="America/New_York", sleep_intervals=iv
        )
        self.assertEqual(span.day_id, "2026-09-17")
        self.assertEqual(span.start, wake)

    def test_backstop_rolls_at_wake_plus_20h(self):
        """AC: awake with no sleep recorded rolls at wake+20h — documented + tested."""
        self.assertEqual(WAKE_BACKSTOP_HOURS, 20.0)
        wake = datetime(2026, 9, 17, 9, 0, tzinfo=ET)
        now = datetime(2026, 9, 18, 10, 0, tzinfo=ET)  # 25h later
        bat = {"last_wake_at": wake.isoformat(), "empty_at": (wake + timedelta(hours=15)).isoformat()}
        span = resolve_nutrition_day(
            now=now, tz_name="America/New_York", sleep_battery=bat
        )
        self.assertEqual(span.backstop, "wake_plus_20h")
        self.assertGreaterEqual(span.start, wake + timedelta(hours=20))
        self.assertIn("wake+20h", (span.as_dict().get("note") or ""))

    def test_missing_sleep_uses_civil_midnight(self):
        now = datetime(2026, 9, 18, 10, 0, tzinfo=ET)
        span = resolve_nutrition_day(now=now, tz_name="America/New_York")
        self.assertEqual(span.source, "missing_sleep_civil")
        self.assertEqual(span.backstop, "missing_sleep_civil")
        self.assertEqual(span.day_id, "2026-09-18")
        self.assertEqual(span.start.hour, 0)

    def test_food_outside_eating_window_still_counts(self):
        """AC: eating outside the eating window still counts toward the waking day."""
        wake = datetime(2026, 9, 17, 9, 0, tzinfo=ET)
        empty = datetime(2026, 9, 17, 22, 0, tzinfo=ET)
        now = datetime(2026, 9, 17, 23, 30, tzinfo=ET)
        bat = {
            "last_wake_at": wake.isoformat(),
            "empty_at": empty.isoformat(),
            "awake_budget_hours": 13.0,
        }
        logs = [
            _log("2026-09-17", "12:00", 1000, "Lunch"),
            _log("2026-09-17", "23:00", 400, "After empty"),
        ]
        composed = compose_nutrition_today(
            now=now,
            tz_name="America/New_York",
            sleep_battery=bat,
            food_logs=logs,
        )
        self.assertEqual(composed["today_consumed"]["calories"], 1400.0)

    def test_planner_remaining_matches_today_so_far(self):
        """AC: planner remaining == today-so-far remaining (70-vs-800 class)."""
        wake = datetime(2026, 9, 18, 8, 0, tzinfo=ET)
        now = datetime(2026, 9, 18, 22, 41, tzinfo=ET)
        empty = wake + timedelta(hours=15)
        bat = {
            "last_wake_at": wake.isoformat(),
            "empty_at": empty.isoformat(),
            "awake_budget_hours": 15.0,
        }
        logs = [
            _log("2026-09-18", "09:00", 500, "Breakfast", protein_g=40),
            _log("2026-09-18", "13:00", 700, "Lunch", protein_g=60),
            _log("2026-09-18", "19:00", 830, "Dinner", protein_g=70),
        ]
        composed = compose_nutrition_today(
            now=now,
            tz_name="America/New_York",
            sleep_battery=bat,
            food_logs=logs,
        )
        consumed = composed["today_consumed"]
        plan = generate_meal_plan(
            STOCKED,
            FULL_TARGETS,
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
            now=now,
            tz_name="America/New_York",
            sleep_battery=bat,
            food_logs=logs,
        )
        rem_today = remaining_macros(FULL_TARGETS, consumed)
        rem_plan = plan["remaining_before_plan"]
        self.assertAlmostEqual(rem_today["calories"], rem_plan["calories"], places=0)
        self.assertAlmostEqual(consumed["calories"], 2030.0, places=0)
        # The 70-vs-800 gap: remaining is ~70, not ~800.
        self.assertLess(rem_plan["calories"], 150)
        self.assertGreater(rem_plan["calories"], 0)

    def test_trends_late_night_no_phantom_surplus(self):
        """AC: late-night food does not alternate surplus/deficit vs burn."""
        wake = datetime(2026, 9, 17, 9, 0, tzinfo=ET)
        onset = datetime(2026, 9, 18, 2, 0, tzinfo=ET)
        next_wake = datetime(2026, 9, 18, 8, 0, tzinfo=ET)
        now = datetime(2026, 9, 18, 10, 0, tzinfo=ET)
        iv = _intervals(
            (datetime(2026, 9, 17, 0, 0, tzinfo=ET), wake),
            (onset, next_wake),
        )
        logs = [
            _log("2026-09-17", "12:00", 1500, "Day food"),
            _log("2026-09-18", "01:30", 800, "Late snack"),
        ]
        burned = [
            CaloriesBurnedDay(date="2026-09-17", calories=2400),
            CaloriesBurnedDay(date="2026-09-18", calories=2400),
        ]
        days = list_nutrition_days(
            now=now, tz_name="America/New_York", sleep_intervals=iv
        )
        intake = {r["date"]: r["calories"] for r in bucket_intake_by_nutrition_day(logs, days)}
        burn = {r["date"]: r["calories"] for r in bucket_burn_by_nutrition_day(burned, days)}
        self.assertAlmostEqual(intake.get("2026-09-17") or 0, 2300.0, places=0)
        # Late-night 800 is NOT a Sep 18 surplus sitting on a full burn day.
        self.assertNotEqual(intake.get("2026-09-18"), 800.0)
        d17 = (intake.get("2026-09-17") or 0) - (burn.get("2026-09-17") or 0)
        d18 = (intake.get("2026-09-18") or 0) - (burn.get("2026-09-18") or 0)
        # Civil clocks produced ~D17 deficit 900 / D18 surplus from 800 vs 2400.
        # Waking days must not recreate that sign flip from the 800 kcal move.
        self.assertGreater(d17, -400)
        self.assertLess(d18, 0)

    def test_calorie_bars_in_out_uses_waking_day(self):
        wake = datetime(2026, 9, 17, 9, 0, tzinfo=ET)
        now = datetime(2026, 9, 18, 0, 14, tzinfo=ET)
        empty = datetime(2026, 9, 18, 1, 0, tzinfo=ET)
        logs = [
            _log("2026-09-17", "14:00", 800, "Lunch"),
            _log("2026-09-17", "21:00", 600, "Dinner"),
            _log("2026-09-17", "08:00", 200, "Before wake"),
        ]
        payload = build_calorie_bars_payload(
            today_consumed={"calories": 0},
            targets={"calories": 2000},
            sleep_battery={
                "last_wake_at": wake.isoformat(),
                "empty_at": empty.isoformat(),
                "awake_budget_hours": 16,
            },
            food_logs=logs,
            now=now,
            tz_name="America/New_York",
        )
        self.assertEqual(payload["pacing"]["intake_source"], "waking_day_logs")
        self.assertEqual(payload["pacing"]["consumed"], 1400.0)
        self.assertEqual(payload["delta"]["intake"], 1400.0)
        self.assertEqual(payload["nutrition_day"]["clock"], "waking_day")


if __name__ == "__main__":
    unittest.main()
