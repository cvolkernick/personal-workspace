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
from rt_dashboard.models import CaloriesBurnedDay, FoodLogEntry, SleepSample
from rt_dashboard.nutrition_day import (
    SOURCE_BACKSTOP_20H,
    SOURCE_SLEEP,
    WAKE_BACKSTOP_HOURS,
    NutritionDaySpan,
    _overlap_hours,
    bucket_burn_by_nutrition_day,
    bucket_intake_by_nutrition_day,
    burn_domains,
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


_UNSET = object()


def _span(
    start,
    end,
    next_wake,
    *,
    open_=False,
    source=SOURCE_SLEEP,
    backstop=None,
    sleep_onset=_UNSET,
):
    if sleep_onset is _UNSET:
        sleep_onset = end
    return NutritionDaySpan(
        day_id=start.strftime("%Y-%m-%d"),
        start=start,
        end=end,
        sleep_onset=sleep_onset,
        next_wake=next_wake,
        open=open_,
        source=source,
        backstop=backstop,
    )


def _domain_hours(domains, c0, c1):
    return sum(_overlap_hours(c0, c1, a, b) for a, b in domains)


def _assert_disjoint(testcase, domains):
    for i, left in enumerate(domains):
        for right in domains[i + 1 :]:
            testcase.assertEqual(_overlap_hours(*left, *right), 0.0)


class TestBurnRebucketConservation(unittest.TestCase):
    """#878: sleep-gap and in-sleep spans must not share a burn domain."""

    def test_gap_synthetics_do_not_double_count(self):
        """Missing overnight: synthetic backstops nest inside next_wake.

        Civil Jun 2 and Jun 3 are fully inside the partition. Their burn
        must come back out equal to the civil totals, not k+1 times.
        """
        tz = ET
        real = _span(
            datetime(2026, 6, 1, 7, tzinfo=tz),
            datetime(2026, 6, 2, 3, tzinfo=tz),
            datetime(2026, 6, 4, 7, tzinfo=tz),
            backstop=SOURCE_BACKSTOP_20H,
        )
        syn1 = _span(
            datetime(2026, 6, 2, 3, tzinfo=tz),
            datetime(2026, 6, 2, 23, tzinfo=tz),
            datetime(2026, 6, 4, 7, tzinfo=tz),
            source=SOURCE_BACKSTOP_20H,
            backstop=SOURCE_BACKSTOP_20H,
        )
        syn2 = _span(
            datetime(2026, 6, 2, 23, tzinfo=tz),
            datetime(2026, 6, 3, 19, tzinfo=tz),
            datetime(2026, 6, 4, 7, tzinfo=tz),
            source=SOURCE_BACKSTOP_20H,
            backstop=SOURCE_BACKSTOP_20H,
        )
        syn3 = _span(
            datetime(2026, 6, 3, 19, tzinfo=tz),
            datetime(2026, 6, 4, 1, tzinfo=tz),
            datetime(2026, 6, 4, 7, tzinfo=tz),
            source=SOURCE_BACKSTOP_20H,
            backstop=SOURCE_BACKSTOP_20H,
        )
        awake = _span(
            datetime(2026, 6, 4, 7, tzinfo=tz),
            datetime(2026, 6, 4, 12, tzinfo=tz),
            None,
            open_=True,
        )
        days = [real, syn1, syn2, syn3, awake]
        nominal = [(s.start, s.burn_end) for s in days]
        domains = burn_domains(days)
        _assert_disjoint(self, domains)

        jun3_0 = datetime(2026, 6, 3, tzinfo=tz)
        jun3_1 = jun3_0 + timedelta(hours=24)
        nominal_hours = _domain_hours(nominal, jun3_0, jun3_1)
        fixed_hours = _domain_hours(domains, jun3_0, jun3_1)
        self.assertGreater(nominal_hours, 24.0)
        self.assertAlmostEqual(fixed_hours, 24.0, places=5)

        burned = [
            {"date": "2026-06-02", "calories": 2400},
            {"date": "2026-06-03", "calories": 2400},
        ]
        rows = bucket_burn_by_nutrition_day(burned, days)
        self.assertAlmostEqual(sum(r["calories"] for r in rows), 4800.0, places=1)
        # syn1 and syn2 share 2026-06-02. One row, not the summed total twice.
        self.assertEqual(len([r for r in rows if r["date"] == "2026-06-02"]), 1)

    def test_in_sleep_bmr_lands_once_on_the_closed_day(self):
        """While asleep, tonight's BMR stays on the day that just closed."""
        tz = ET
        onset = datetime(2026, 6, 12, 1, tzinfo=tz)
        wake = datetime(2026, 6, 12, 8, tzinfo=tz)
        closed = _span(
            datetime(2026, 6, 11, 7, tzinfo=tz),
            onset,
            wake,
        )
        asleep = _span(
            onset,
            datetime(2026, 6, 12, 3, tzinfo=tz),
            wake,
            open_=True,
            sleep_onset=None,
        )
        days = [closed, asleep]
        domains = burn_domains(days)
        _assert_disjoint(self, domains)
        self.assertEqual(domains[1][0], domains[1][1])
        self.assertLessEqual(domains[0][0], onset)
        self.assertGreaterEqual(domains[0][1], wake)

        owners = [
            h
            for h in (_overlap_hours(onset, wake, a, b) for a, b in domains)
            if h > 0
        ]
        self.assertEqual(len(owners), 1)
        self.assertAlmostEqual(owners[0], 7.0, places=5)

        burned = [
            {"date": "2026-06-11", "calories": 2400},
            {"date": "2026-06-12", "calories": 2400},
        ]
        rows = {
            r["date"]: r["calories"] for r in bucket_burn_by_nutrition_day(burned, days)
        }
        # Jun 11 07:00–24:00 (17h) + Jun 12 00:00–08:00 (8h), once.
        self.assertAlmostEqual(rows["2026-06-11"], 2500.0, places=1)
        self.assertNotIn("2026-06-12", rows)

        now = datetime(2026, 6, 12, 3, tzinfo=tz)
        iv = _intervals(
            (datetime(2026, 6, 10, 23, tzinfo=tz), datetime(2026, 6, 11, 7, tzinfo=tz)),
            (onset, wake),
        )
        composed = compose_nutrition_today(
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            calories_burned=[
                CaloriesBurnedDay(date="2026-06-11", calories=2400),
                CaloriesBurnedDay(date="2026-06-12", calories=2400),
            ],
            food_logs=[_log("2026-06-11", "12:00", 500, "Lunch")],
        )
        chart = {
            r["date"]: r["calories"] for r in composed["trends_calories_burned"]
        }
        self.assertAlmostEqual(chart["2026-06-11"], 2500.0, places=1)
        self.assertNotIn("2026-06-12", chart)
        intake_ids = {r["date"] for r in composed["trends_nutrition"]}
        self.assertIn("2026-06-11", intake_ids)
        self.assertIn("2026-06-11", chart)
        # New day is open at onset; its burn row does not carry the sleep.
        self.assertEqual(composed["nutrition_day"]["day_id"], "2026-06-12")
        self.assertIsNone(composed["calories_burned_today"])

    def test_shared_day_id_in_sleep_is_not_emitted_twice(self):
        """Onset before midnight shares a day id with the closed span."""
        tz = ET
        onset = datetime(2026, 6, 11, 23, tzinfo=tz)
        wake = datetime(2026, 6, 12, 7, tzinfo=tz)
        closed = _span(datetime(2026, 6, 11, 7, tzinfo=tz), onset, wake)
        asleep = _span(
            onset,
            datetime(2026, 6, 11, 23, 30, tzinfo=tz),
            wake,
            open_=True,
            sleep_onset=None,
        )
        self.assertEqual(closed.day_id, asleep.day_id)
        domains = burn_domains([closed, asleep])
        _assert_disjoint(self, domains)
        burned = [
            {"date": "2026-06-11", "calories": 2400},
            {"date": "2026-06-12", "calories": 2400},
        ]
        rows = bucket_burn_by_nutrition_day(burned, [closed, asleep])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-06-11")
        # 17h of Jun 11 + 7h of Jun 12, once. A second emit would be 4800.
        self.assertAlmostEqual(rows[0]["calories"], 2400.0, places=1)

    def test_lone_in_sleep_span_keeps_its_hours(self):
        """No previous day: do not drop the sleep by zeroing the only span."""
        tz = ET
        onset = datetime(2026, 6, 12, 1, tzinfo=tz)
        wake = datetime(2026, 6, 12, 8, tzinfo=tz)
        asleep = _span(
            onset,
            datetime(2026, 6, 12, 3, tzinfo=tz),
            wake,
            open_=True,
            sleep_onset=None,
        )
        domains = burn_domains([asleep])
        self.assertAlmostEqual(_overlap_hours(onset, wake, *domains[0]), 7.0, places=5)

    def test_conservation_90d_window_including_sleep_gaps(self):
        """Sum of bucketed burn equals the civil totals on fully covered days."""
        tz = ET
        base = datetime(2026, 6, 1, tzinfo=tz)
        skip = {30, 31, 32, 60, 61}
        pairs = []
        for i in range(92):
            if i in skip:
                continue
            onset = base + timedelta(days=i, hours=23)
            wake = base + timedelta(days=i + 1, hours=7)
            pairs.append((onset, wake))
        now = base + timedelta(days=92, hours=12)
        days = list_nutrition_days(
            now=now,
            tz_name="America/New_York",
            sleep_intervals=_intervals(*pairs),
        )
        self.assertTrue(any(s.backstop == SOURCE_BACKSTOP_20H for s in days))
        domains = burn_domains(days)
        nominal = [(s.start, s.burn_end) for s in days]
        _assert_disjoint(self, domains)

        full_dates = []
        inflated = []
        cursor = base
        last = base + timedelta(days=93)
        while cursor < last:
            c1 = cursor + timedelta(hours=24)
            got = _domain_hours(domains, cursor, c1)
            nom = _domain_hours(nominal, cursor, c1)
            if nom > 24.0 + 1e-6:
                inflated.append(cursor.date().isoformat())
            if abs(got - 24.0) < 1e-6:
                full_dates.append(cursor.date().isoformat())
            cursor = c1
        self.assertGreaterEqual(len(full_dates), 80)
        # A day the nominal windows over-claim is covered exactly once.
        self.assertTrue(set(inflated) & set(full_dates))

        burned = [{"date": day, "calories": 2400} for day in full_dates]
        rows = bucket_burn_by_nutrition_day(burned, days)
        self.assertAlmostEqual(
            sum(r["calories"] for r in rows),
            2400.0 * len(full_dates),
            places=1,
        )
        intake = bucket_intake_by_nutrition_day([], days)
        span_ids = {s.day_id for s in days}
        self.assertTrue({r["date"] for r in rows} <= span_ids)
        self.assertTrue({r["date"] for r in intake} <= span_ids)


def _burn_row(date, calories=2400):
    return CaloriesBurnedDay(date=date, calories=calories)


class TestTodaySleepBasal881(unittest.TestCase):
    """#881: Today out includes sleep already inside the day. It does not invent one.

    Rule (#878, unchanged): sleep BMR stays on the nutrition day that closed
    at that night's onset. An open waking Today is the open window only.
    Last night is on yesterday. Tonight is not a window until logged.
    """

    def test_logged_sleep_inside_today_raises_out_by_the_sleep_hours(self):
        """Pre-midnight night shares today's id, so its basal is on the card."""
        tz = ET
        iv = _intervals(
            (datetime(2026, 6, 19, 23, tzinfo=tz), datetime(2026, 6, 20, 7, tzinfo=tz)),
            (datetime(2026, 6, 20, 23, tzinfo=tz), datetime(2026, 6, 21, 7, tzinfo=tz)),
        )
        now = datetime(2026, 6, 20, 23, 30, tzinfo=tz)
        burned = [
            _burn_row("2026-06-19"),
            _burn_row("2026-06-20"),
            _burn_row("2026-06-21"),
        ]
        composed = compose_nutrition_today(
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            calories_burned=burned,
        )
        self.assertEqual(composed["nutrition_day"]["day_id"], "2026-06-20")
        # [Jun 20 07:00, Jun 21 07:00) = 24h at 100 kcal/h.
        # Wake-only [07:00, onset 23:00) = 16h = 1600. The 8h night is the gap.
        self.assertAlmostEqual(composed["calories_burned_today"], 2400.0, places=1)
        self.assertAlmostEqual(
            composed["calories_burned_today"] - 1600.0, 800.0, places=1
        )
        payload = build_calorie_bars_payload(
            targets={"calories": 2100},
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            calories_burned=burned,
            calories_burned_today=composed["calories_burned_today"],
            nutrition_day=composed["nutrition_day"],
        )
        self.assertEqual(payload["delta"]["status"], "ok")
        self.assertAlmostEqual(payload["delta"]["burned"], 2400.0, places=1)

    def test_open_waking_today_keeps_last_night_on_yesterday(self):
        """Do not add a second sleep term onto the open window."""
        tz = ET
        iv = _intervals(
            (datetime(2026, 6, 19, 23, tzinfo=tz), datetime(2026, 6, 20, 7, tzinfo=tz)),
            (datetime(2026, 6, 20, 23, tzinfo=tz), datetime(2026, 6, 21, 7, tzinfo=tz)),
        )
        now = datetime(2026, 6, 21, 15, tzinfo=tz)
        burned = [_burn_row(f"2026-06-{d:02d}") for d in range(19, 22)]
        # A 7am approximation for this morning must not pull wake earlier.
        daily = [SleepSample(date="2026-06-21", sleep_hours=8.0, source="google_health")]
        composed = compose_nutrition_today(
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            daily_sleep=daily,
            calories_burned=burned,
        )
        self.assertEqual(composed["nutrition_day"]["start"][:19], "2026-06-21T07:00:00")
        self.assertIsNone(composed["nutrition_day"]["next_wake"])
        # 07:00–15:00 = 8h. Not 8h + last night, and not a synthetic tonight.
        self.assertAlmostEqual(composed["calories_burned_today"], 800.0, places=1)
        chart = {
            r["date"]: r["calories"] for r in composed["trends_calories_burned"]
        }
        self.assertAlmostEqual(chart["2026-06-20"], 2400.0, places=1)
        payload = build_calorie_bars_payload(
            targets={"calories": 2100},
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            daily_sleep=daily,
            calories_burned=burned,
            calories_burned_today=composed["calories_burned_today"],
            nutrition_day=composed["nutrition_day"],
        )
        self.assertAlmostEqual(payload["delta"]["burned"], 800.0, places=1)

    def test_after_midnight_sleep_is_not_synthesized_onto_the_new_day(self):
        tz = ET
        iv = _intervals(
            (datetime(2026, 6, 20, 1, tzinfo=tz), datetime(2026, 6, 20, 8, tzinfo=tz)),
            (datetime(2026, 6, 21, 1, tzinfo=tz), datetime(2026, 6, 21, 8, tzinfo=tz)),
        )
        now = datetime(2026, 6, 21, 3, tzinfo=tz)
        burned = [_burn_row(f"2026-06-{d:02d}") for d in range(20, 22)]
        composed = compose_nutrition_today(
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            calories_burned=burned,
        )
        self.assertEqual(composed["nutrition_day"]["day_id"], "2026-06-21")
        self.assertEqual(composed["nutrition_day"]["start"][:19], "2026-06-21T01:00:00")
        self.assertIsNone(composed["calories_burned_today"])
        chart = {
            r["date"]: r["calories"] for r in composed["trends_calories_burned"]
        }
        # Closed day [Jun 20 08:00, Jun 21 08:00) holds the in-progress night.
        self.assertAlmostEqual(chart["2026-06-20"], 2400.0, places=1)
        self.assertNotIn("2026-06-21", chart)
        payload = build_calorie_bars_payload(
            targets={"calories": 2100},
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            calories_burned=burned,
            calories_burned_today=composed["calories_burned_today"],
            nutrition_day=composed["nutrition_day"],
        )
        self.assertEqual(payload["delta"]["status"], "no_burned")
        self.assertIsNone(payload["delta"]["burned"])

    def test_no_sleep_does_not_invent_basal(self):
        tz = ET
        now = datetime(2026, 6, 21, 15, tzinfo=tz)
        composed = compose_nutrition_today(
            now=now,
            tz_name="America/New_York",
            calories_burned=[_burn_row("2026-06-21")],
        )
        self.assertEqual(composed["nutrition_day"]["source"], "missing_sleep_civil")
        # Civil midnight → 15:00 only. No +8h sleep term.
        self.assertAlmostEqual(composed["calories_burned_today"], 1500.0, places=1)

    def test_measured_intervals_ignore_overlapping_daily_7am(self):
        """A 7am approximation must not move the measured onset or the basal."""
        tz = ET
        onset = datetime(2026, 9, 18, 4, 23, tzinfo=tz)
        wake = datetime(2026, 9, 18, 12, 32, tzinfo=tz)
        prev_wake = datetime(2026, 9, 17, 11, 22, tzinfo=tz)
        iv = _intervals(
            (datetime(2026, 9, 17, 1, 43, tzinfo=tz), prev_wake),
            (onset, wake),
        )
        now = datetime(2026, 9, 18, 8, 0, tzinfo=tz)
        daily = [
            SleepSample(date="2026-09-17", sleep_hours=9.65, source="google_health"),
            SleepSample(date="2026-09-18", sleep_hours=8.15, source="google_health"),
        ]
        burned = [
            _burn_row("2026-09-16"),
            _burn_row("2026-09-17"),
            _burn_row("2026-09-18"),
        ]
        composed = compose_nutrition_today(
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            daily_sleep=daily,
            calories_burned=burned,
        )
        # Invented onset would be 22:51 the night before (8.15h before 07:00).
        self.assertEqual(composed["nutrition_day"]["start"][:19], "2026-09-18T04:23:00")
        self.assertEqual(composed["nutrition_day"]["day_id"], "2026-09-18")
        self.assertIsNone(composed["calories_burned_today"])
        chart = {
            r["date"]: r["calories"] for r in composed["trends_calories_burned"]
        }
        # Previous domain runs through the measured wake and includes the night.
        self.assertIn("2026-09-17", chart)
        self.assertGreater(chart["2026-09-17"], 2400.0)
        self.assertNotIn("2026-09-18", chart)
        payload = build_calorie_bars_payload(
            targets={"calories": 2100},
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            daily_sleep=daily,
            calories_burned=burned,
            calories_burned_today=composed["calories_burned_today"],
            nutrition_day=composed["nutrition_day"],
        )
        self.assertEqual(payload["delta"]["status"], "no_burned")

    def test_lagging_daily_fill_keeps_that_night_on_the_closed_day(self):
        """Intervals stopped yesterday. Today's completed daily night fills once."""
        tz = ET
        old_wake = datetime(2026, 7, 28, 12, 0, tzinfo=tz)
        iv = _intervals((old_wake - timedelta(hours=8), old_wake))
        daily = [
            SleepSample(date="2026-07-28", sleep_hours=8.0, source="google_health"),
            SleepSample(date="2026-07-29", sleep_hours=8.0, source="google_health"),
        ]
        now = datetime(2026, 7, 29, 12, 0, tzinfo=tz)
        burned = [_burn_row("2026-07-28"), _burn_row("2026-07-29")]
        composed = compose_nutrition_today(
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            daily_sleep=daily,
            calories_burned=burned,
        )
        self.assertEqual(composed["nutrition_day"]["start"][:19], "2026-07-29T07:00:00")
        # Open window 07:00–12:00. The filled night is not added again.
        self.assertAlmostEqual(composed["calories_burned_today"], 500.0, places=1)
        chart = {
            r["date"]: r["calories"] for r in composed["trends_calories_burned"]
        }
        # [Jul 28 12:00, Jul 29 07:00) = 19h, including the 8h filled night.
        self.assertAlmostEqual(chart["2026-07-28"], 1900.0, places=1)
        payload = build_calorie_bars_payload(
            targets={"calories": 2100},
            now=now,
            tz_name="America/New_York",
            sleep_intervals=iv,
            daily_sleep=daily,
            calories_burned=burned,
            calories_burned_today=9999,
            nutrition_day=composed["nutrition_day"],
        )
        # Card follows the partition, not the stale caller override.
        self.assertAlmostEqual(payload["delta"]["burned"], 500.0, places=1)


if __name__ == "__main__":
    unittest.main()
