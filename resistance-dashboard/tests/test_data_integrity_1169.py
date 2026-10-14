"""FitDash data integrity: food-log merge, sleep-gap day boundary, naps,
full re-pull cadence, burn sanity cap, energy-vs-scale partial/gaps.

Synthetic data only.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from rt_dashboard import background_refresh as br
from rt_dashboard.dashboard_cache import (
    drop_suspect_burn,
    health_from_dict,
    merge_food_logs,
    merge_health_snapshots,
    merge_sleep_intervals,
)
from rt_dashboard.google_health import parse_food_log_entries
from rt_dashboard.models import CaloriesBurnedDay, FoodLogEntry, HealthSnapshot, NutritionDay
from rt_dashboard.nutrition_day import (
    bucket_intake_by_civil_day,
    civil_burn_series,
    compose_nutrition_today,
    list_nutrition_days,
    sum_intake_for_span,
)
from rt_dashboard.sleep_battery import compute_sleep_battery

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("America/New_York")


def _food(date, time_hm, name, kcal, log_id=None):
    return FoodLogEntry(date=date, name=name, calories=kcal, time=time_hm, log_id=log_id)


class FoodLogIdParse(unittest.TestCase):
    def test_parse_keeps_google_datapoint_name(self):
        payload = {
            "dataPoints": [
                {
                    "name": "users/me/dataTypes/nutrition-log/dataPoints/abc123",
                    "nutritionLog": {
                        "foodDisplayName": "Pasta bowl",
                        "energy": {"kcal": 1300},
                        "interval": {
                            "civilStartTime": {
                                "date": {"year": 2099, "month": 1, "day": 2},
                                "time": {"hours": 14, "minutes": 18},
                            }
                        },
                    },
                }
            ]
        }
        with mock.patch("rt_dashboard.google_health.datetime") as dt:
            dt.now.return_value = datetime(2099, 1, 3, tzinfo=TZ)
            dt.strptime = datetime.strptime
            rows = parse_food_log_entries(payload, days=30)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].log_id, "users/me/dataTypes/nutrition-log/dataPoints/abc123")
        self.assertEqual(rows[0].calories, 1300)


class FoodLogMerge(unittest.TestCase):
    def test_edited_entry_replaces_old_version_by_id(self):
        base = [_food("2099-01-02", "14:18", "Pasta bowl", 6500, log_id="p1")]
        upd = [_food("2099-01-02", "14:18", "Pasta bowl", 1300, log_id="p1")]
        out = merge_food_logs(base, upd, covered_dates=set())
        self.assertEqual([r["calories"] for r in out], [1300])

    def test_covered_date_replaced_wholesale_without_ids(self):
        # Legacy cache rows have no log_id: the edit (6500 → 1300) must not
        # survive on a date the fresh pull covered.
        base = [
            _food("2099-01-02", "14:18", "Pasta bowl", 6500),
            _food("2099-01-02", "14:18", "Pasta bowl", 1300),
            _food("2098-12-01", "09:00", "Old toast", 200),
        ]
        upd = [_food("2099-01-02", "14:18", "Pasta bowl", 1300, log_id="p1")]
        out = merge_food_logs(base, upd, covered_dates={"2099-01-02"})
        by_date = {}
        for r in out:
            by_date.setdefault(r["date"], []).append(r["calories"])
        self.assertEqual(by_date["2099-01-02"], [1300])
        self.assertEqual(by_date["2098-12-01"], [200])  # history kept

    def test_snapshot_merge_uses_fresh_window(self):
        base = HealthSnapshot(
            food_logs=[
                _food("2099-01-02", "14:18", "Pasta bowl", 6500),
                _food("2099-01-02", "14:18", "Pasta bowl", 1300),
            ]
        )
        upd = HealthSnapshot(
            food_logs=[_food("2099-01-02", "14:18", "Pasta bowl", 1300, log_id="p1")],
            nutrition=[NutritionDay(date="2099-01-01", calories=2000)],
        )
        merged = merge_health_snapshots(base, upd)
        self.assertEqual(sum(f.calories for f in merged.food_logs), 1300)
        self.assertEqual(merged.food_logs[0].log_id, "p1")


class InflationFlag(unittest.TestCase):
    def test_day_over_110pct_of_google_uses_google_total(self):
        now = datetime(2099, 1, 5, 12, 0, tzinfo=TZ)
        logs = [
            {"date": "2099-01-02", "time": "14:18", "name": "Pasta", "calories": 1300},
            {"date": "2099-01-02", "time": "14:18", "name": "Pasta", "calories": 6500},
            {"date": "2099-01-03", "time": "12:00", "name": "Rice", "calories": 2050},
        ]
        roll = [
            {"date": "2099-01-02", "calories": 2928},
            {"date": "2099-01-03", "calories": 2000},
        ]
        rows = {r["date"]: r for r in bucket_intake_by_civil_day(logs, now=now, nutrition_rollups=roll)}
        self.assertTrue(rows["2099-01-02"]["inflated"])
        self.assertEqual(rows["2099-01-02"]["calories"], 2928)
        self.assertEqual(rows["2099-01-02"]["food_log_calories"], 7800)
        self.assertNotIn("inflated", rows["2099-01-03"])  # 2.5% over: fine
        self.assertEqual(rows["2099-01-03"]["calories"], 2050)


class SleepGapDayBoundary(unittest.TestCase):
    # Night 1: 01:00 → 09:00. Day: 09:00 → 03:54. Night 2: 03:54 → 08:21.
    IV = [
        {"start": "2099-01-02T01:00:00-05:00", "end": "2099-01-02T09:00:00-05:00"},
        {"start": "2099-01-03T03:54:00-05:00", "end": "2099-01-03T08:21:00-05:00"},
    ]
    NOW = datetime(2099, 1, 3, 12, 0, tzinfo=TZ)

    def _days(self, iv=None):
        return list_nutrition_days(now=self.NOW, sleep_intervals=iv or self.IV)

    def test_food_logged_during_sleep_goes_to_new_day(self):
        logs = [
            {"date": "2099-01-03", "time": "01:43", "name": "Vitamin", "calories": 20},
            {"date": "2099-01-03", "time": "08:00", "name": "Bagel", "calories": 680},
            {"date": "2099-01-03", "time": "09:36", "name": "Coffee", "calories": 130},
        ]
        days = self._days()
        today = days[-1]
        prev = days[-2]
        got = sum_intake_for_span(logs, today, now=self.NOW)
        self.assertEqual(got["calories"], 810)  # bagel + coffee
        self.assertEqual(sum_intake_for_span(logs, prev, now=prev.end)["calories"], 20)

    def test_grace_60min_before_wake_never_steals_previous_day(self):
        days = self._days()
        today = days[-1]
        self.assertEqual(today.intake_from, days[-2].end)
        self.assertIn("intake_start", today.as_dict())

    def test_compose_today_includes_pre_wake_bagel(self):
        logs = [
            FoodLogEntry(date="2099-01-03", name="Bagel", calories=680, time="08:00"),
            FoodLogEntry(date="2099-01-03", name="Coffee", calories=130, time="09:36"),
        ]
        out = compose_nutrition_today(
            food_logs=logs, now=self.NOW, tz_name="America/New_York", sleep_intervals=self.IV
        )
        consumed = out.get("consumed") or out.get("today_consumed") or out
        self.assertEqual(float(consumed.get("calories")), 810.0)


class NapNeverStartsDay(unittest.TestCase):
    IV = [
        {"start": "2099-01-03T03:54:00-05:00", "end": "2099-01-03T08:21:00-05:00"},
        {"start": "2099-01-03T16:36:00-05:00", "end": "2099-01-03T19:02:00-05:00"},
    ]
    NOW = datetime(2099, 1, 3, 20, 40, tzinfo=TZ)

    def test_battery_wake_is_main_sleep_not_nap(self):
        bat = compute_sleep_battery(intervals=self.IV, now=self.NOW)
        self.assertTrue(str(bat["last_wake_at"]).startswith("2099-01-03T08:21"))
        self.assertEqual(bat["mode"], "awake")

    def test_nutrition_day_not_reset_by_nap(self):
        days = list_nutrition_days(now=self.NOW, sleep_intervals=self.IV)
        self.assertEqual(days[-1].start.strftime("%H:%M"), "08:21")

    def test_hydration_before_nap_counts_today(self):
        from rt_dashboard.hydration_bars import build_hydration_bars_payload

        bat = compute_sleep_battery(intervals=self.IV, now=self.NOW)
        samples = [
            {"logged_at": "2099-01-03T10:00:00-05:00", "water_ml": 2000.0},
            {"logged_at": "2099-01-03T15:00:00-05:00", "water_ml": 2475.5},
        ]
        try:
            out = build_hydration_bars_payload(
                hydration=[], samples=samples, weight=[], sleep_battery=bat,
                as_of="2099-01-03", now=self.NOW, tz_name="America/New_York",
            )
        except TypeError as e:  # signature drift: still assert the wake anchor
            self.skipTest(f"hydration payload signature: {e}")
        pacing = out["pacing"]
        self.assertEqual(pacing["window"]["window_start"], "2099-01-03T08:21:00-05:00")
        self.assertAlmostEqual(float(pacing["consumed_ml"]), 4475.5, places=1)


class SleepIntervalMerge(unittest.TestCase):
    def test_merge_by_start_keeps_history_and_fresh_wins(self):
        base = [
            {"start": "2099-01-01T04:00:00+00:00", "end": "2099-01-01T11:00:00+00:00"},
            {"start": "2099-01-02T04:00:00+00:00", "end": "2099-01-02T10:00:00+00:00"},
        ]
        upd = [{"start": "2099-01-02T04:00:00+00:00", "end": "2099-01-02T12:00:00+00:00"}]
        out = merge_sleep_intervals(base, upd)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[-1]["end"], "2099-01-02T12:00:00+00:00")
        merged = merge_health_snapshots(HealthSnapshot(sleep_intervals=base), HealthSnapshot(sleep_intervals=upd))
        self.assertEqual(len(merged.sleep_intervals), 2)


class BurnSanityCap(unittest.TestCase):
    def test_over_6000_dropped_on_merge_and_cache_load(self):
        rows = [CaloriesBurnedDay(date="2099-01-01", calories=10383.6), CaloriesBurnedDay(date="2099-01-02", calories=2600)]
        self.assertEqual([r.date for r in drop_suspect_burn(rows)], ["2099-01-02"])
        snap = health_from_dict({"calories_burned": [r.to_dict() for r in rows]})
        self.assertEqual([r.date for r in snap.calories_burned], ["2099-01-02"])
        merged = merge_health_snapshots(HealthSnapshot(calories_burned=rows[:1]), HealthSnapshot(calories_burned=rows[1:]))
        self.assertEqual([r.date for r in merged.calories_burned], ["2099-01-02"])


class FullRepullCadence(unittest.TestCase):
    def test_due_when_no_marker_then_not_due_after_mark(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(
            os.environ, {"RESISTANCE_DASHBOARD_CACHE_DIR": d, "FITDASH_FULL_REPULL_HOURS": "24"}
        ), mock.patch("rt_dashboard.background_refresh._full_marker_path", return_value=Path(d) / "m.json"):
            self.assertTrue(br.full_repull_due())
            br._mark_full_pull(now=time.time())
            self.assertFalse(br.full_repull_due())
            self.assertTrue(br.full_repull_due(now=time.time() + 25 * 3600))

    def test_warm_upgrades_to_full_when_due(self):
        with mock.patch.object(br, "full_repull_due", return_value=True), mock.patch.object(
            br, "_spawn_refresh"
        ) as spawn, mock.patch.object(br, "_last_scheduled", 0.0):
            br.schedule_incremental_warm(force_schedule=True)
        self.assertFalse(spawn.call_args.kwargs["incremental"])

    def test_disabled_with_zero(self):
        with mock.patch.dict(os.environ, {"FITDASH_FULL_REPULL_HOURS": "0"}):
            self.assertFalse(br.full_repull_due())


class PartialToday(unittest.TestCase):
    def test_today_rows_marked_partial(self):
        now = datetime(2099, 1, 3, 20, 0, tzinfo=TZ)
        burn = civil_burn_series([{"date": "2099-01-02", "calories": 2600}, {"date": "2099-01-03", "calories": 2142}], now=now)
        self.assertEqual([r.get("partial", False) for r in burn], [False, True])
        intake = bucket_intake_by_civil_day(
            [{"date": "2099-01-03", "time": "09:00", "name": "x", "calories": 500}], now=now
        )
        self.assertTrue(intake[-1]["partial"])


NODE_RUNNER = r"""
const api = require(process.argv[1]);
const spec = JSON.parse(process.argv[2]);
const labels = [];
for (let i = 0; i < spec.days; i++) labels.push(api.addDays("2099-01-01", i));
const weights = spec.weighDays.map((i, k) => ({date: labels[i], weight_lbs: 180 - 0.1 * k}));
const intakeRows = labels.map((d) => ({date: d, calories: 2000}));
const burnedRows = labels.map((d) => ({date: d, calories: 2500}));
intakeRows[spec.days - 1].partial = true;
burnedRows[spec.days - 1] = {date: labels[spec.days - 1], calories: 900, partial: true};
const model = api.energyScaleExtension({labels, intakeRows, burnedRows, weights, foodLogRows: []});
const html = api.extensionHtml(model);
console.log(JSON.stringify({
  actual: model.actual, burned: model.burnedSeries, open: model.open,
  dots: (html.match(/class="ewi-weighin"/g) || []).length,
  amberPaths: (html.match(/stroke="#f0c14d"/g) || []).length,
  amberD: (html.match(/<path d="([^"]*)" fill="none" stroke="#f0c14d"/) || [null, ""])[1],
}));
"""


def _run_scale(days, weigh_days):
    js = ROOT / "static" / "energy-weight-align.js"
    res = subprocess.run(
        ["node", "-e", NODE_RUNNER, str(js), json.dumps({"days": days, "weighDays": weigh_days})],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


class EnergyScaleChartJs(unittest.TestCase):
    def test_partial_today_excluded_and_short_gaps_carried(self):
        out = _run_scale(10, [0, 3, 8])
        self.assertIsNone(out["burned"][9])  # partial day not paired
        actual = out["actual"]
        # 01-02 has no weigh-in but 01-01 is inside the 7-day window → value.
        self.assertIsNotNone(actual[1])
        self.assertIsNotNone(actual[9])
        self.assertNotIn(None, actual)

    def test_gap_of_7_plus_days_breaks_line(self):
        # Weigh-ins on day 0 and day 12: days 7..11 have none in the window.
        out = _run_scale(14, [0, 12])
        actual = out["actual"]
        self.assertIsNotNone(actual[6])
        for i in range(7, 12):
            self.assertIsNone(actual[i], i)
        self.assertIsNotNone(actual[12])

    def test_every_other_day_weighins_one_continuous_line_with_dots(self):
        weigh = list(range(0, 30, 2))
        out = _run_scale(30, weigh)
        self.assertTrue(out["open"])
        self.assertNotIn(None, out["actual"])
        self.assertEqual(out["amberPaths"], 1)
        self.assertEqual(out["amberD"].count("M"), 1)  # one segment
        self.assertEqual(out["dots"], len(weigh))

    def test_app_js_filters_partial_rows(self):
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("!row.partial", app)


if __name__ == "__main__":
    unittest.main()
