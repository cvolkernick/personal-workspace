"""Snapshot FitDash macro calorie-share math from the real app.js functions.

Evaluates ``macroCalPct``, ``macroTargetBandClass``, ``targetPct``,
``rollingAverage``, ``macroRollingShare``, and the Trends chart split /
7-day roll / 30-day health-goal average / period pool by extracting those
functions from ``static/app.js``. No reimplementation, no Turso, no network.

Targets passed in are 170 g protein / 195 g carbs / 60 g fat (34% / 39% / 27%
of macro kcal at 4 / 4 / 9). This does not read or write ``targets.json``.

Regenerate the checked-in snapshot:

    UPDATE_SNAPSHOTS=1 python3 -m unittest tests.test_macro_share_snapshots
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve().parent / "macro_share_snapshots.js"
EXPECTED = Path(__file__).resolve().parent / "fixtures" / "macro_share_expected.json"
APP_JS = ROOT / "static" / "app.js"

IN_RANGE = "chip-in-range"
OUT = "chip-out-of-range"


def _run_snapshot() -> str:
    proc = subprocess.run(
        ["node", str(SCRIPT)],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    raw = proc.stdout
    if not raw.endswith("\n"):
        raw += "\n"
    return raw


class TestMacroShareSnapshots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = _run_snapshot()
        cls.actual = json.loads(cls.raw)
        if os.environ.get("UPDATE_SNAPSHOTS") == "1":
            EXPECTED.parent.mkdir(parents=True, exist_ok=True)
            EXPECTED.write_text(cls.raw, encoding="utf-8")

    def _day(self, date: str) -> dict:
        series = self.actual["cases"]["thirty_day_series_with_gaps"]
        for row in series["days"]:
            if row["date"] == date:
                return row
        self.fail(f"missing {date}")

    def test_snapshot_matches_checked_in_output(self):
        self.assertTrue(EXPECTED.is_file(), "missing snapshot; run with UPDATE_SNAPSHOTS=1")
        expected = EXPECTED.read_text(encoding="utf-8")
        self.assertEqual(self.raw, expected)

    def test_runner_reads_app_js_only(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("static/app.js", text)
        self.assertNotIn("turso", text.lower())
        self.assertNotIn("fetch(", text)
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)
        app = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function macroCalPct(", app)
        self.assertIn("function macroGram(", app)
        self.assertIn("function macroRollingShare(", app)
        self.assertIn("±5 percentage points", app)
        self.assertIn("const rollWin = 7;", app)
        self.assertIn("const roll30Win = 30;", app)
        self.assertIn("const roll30Min = 21;", app)
        self.assertIn("const macroSpanDays = 90;", app)
        self.assertIn("const pct = macroCalPct(n.protein_g, n.carbs_g, n.fat_g);", app)
        self.assertIn("30-day average", app)
        self.assertIn("not enough data", app)
        self.assertIn("health goal", app)
        self.assertNotIn("Math.round((pK / tot) * 1000) / 10", app)

    def test_targets_are_34_39_27_from_four_four_nine(self):
        # 170*4 + 195*4 + 60*9 = 2000 kcal → 34% / 39% / 27%.
        targets = self.actual["targets"]
        self.assertEqual(targets["protein_g"], 170)
        self.assertEqual(targets["carbs_g"], 195)
        self.assertEqual(targets["fat_g"], 60)
        self.assertEqual(targets["tile_pct"], {"p": 34, "c": 39, "f": 27})
        self.assertEqual(targets["chart_pct"], {"p": 34, "c": 39, "f": 27})
        self.assertEqual(self.actual["meta"]["kcal_per_gram"], {"protein": 4, "carbs": 4, "fat": 9})
        self.assertEqual(self.actual["meta"]["chart_roll_window_days"], 7)
        self.assertEqual(self.actual["meta"]["chart_roll_30_days"], 30)
        self.assertEqual(self.actual["meta"]["chart_roll_30_min_logged"], 21)
        self.assertEqual(self.actual["meta"]["chart_span_days"], 90)

    def test_on_target_day_is_inside_the_band_and_on_gram_targets(self):
        day = self.actual["cases"]["on_target_day"]
        self.assertEqual(day["chart_pct"], {"p": 34, "c": 39, "f": 27})
        self.assertEqual(day["tile_pct"], day["chart_pct"])
        self.assertEqual(
            day["band_on_chart_pct"],
            {"p": IN_RANGE, "c": IN_RANGE, "f": IN_RANGE},
        )
        self.assertEqual(day["gram_hit_pct"], {"p": 100, "c": 100, "f": 100})
        self.assertEqual(day["roll_window_days"], 7)

    def test_recent_reality_protein_low_fat_at_high_edge(self):
        # 121.5 g / 184.5 g / 64 g → 27% / 41% / 32%.
        # Fat 32% is the inclusive top of 27±5. Protein 27% is outside 34±5.
        day = self.actual["cases"]["recent_reality_protein_low_fat_high"]
        self.assertEqual(day["chart_pct"], {"p": 27, "c": 41, "f": 32})
        self.assertEqual(day["tile_pct"], day["chart_pct"])
        self.assertEqual(day["band_on_chart_pct"]["p"], OUT)
        self.assertEqual(day["band_on_chart_pct"]["c"], IN_RANGE)
        self.assertEqual(day["band_on_chart_pct"]["f"], IN_RANGE)

    def test_band_edges_are_inclusive_and_the_next_tenth_is_outside(self):
        direct = self.actual["cases"]["band_edges_direct"]
        self.assertEqual(direct["protein_target_34"]["29"], IN_RANGE)
        self.assertEqual(direct["protein_target_34"]["39"], IN_RANGE)
        self.assertEqual(direct["protein_target_34"]["28.9"], OUT)
        self.assertEqual(direct["protein_target_34"]["39.1"], OUT)
        self.assertEqual(direct["fat_target_27"]["22"], IN_RANGE)
        self.assertEqual(direct["fat_target_27"]["32"], IN_RANGE)
        self.assertEqual(direct["fat_target_27"]["21.9"], OUT)
        self.assertEqual(direct["fat_target_27"]["32.1"], OUT)
        self.assertEqual(direct["carbs_target_39"]["34"], IN_RANGE)
        self.assertEqual(direct["carbs_target_39"]["44"], IN_RANGE)
        self.assertEqual(direct["carbs_target_39"]["33.9"], OUT)
        self.assertEqual(direct["carbs_target_39"]["44.1"], OUT)
        self.assertEqual(direct["null_and_non_positive_target"]["null_value"], "")
        self.assertEqual(direct["null_and_non_positive_target"]["zero_target"], "")

        grams = self.actual["cases"]["band_edges_from_grams"]
        expect = {
            "protein_29": ("p", 29, IN_RANGE),
            "protein_28_9": ("p", 28.9, OUT),
            "protein_39": ("p", 39, IN_RANGE),
            "protein_39_1": ("p", 39.1, OUT),
            "fat_22": ("f", 22, IN_RANGE),
            "fat_21_9": ("f", 21.9, OUT),
            "fat_32": ("f", 32, IN_RANGE),
            "fat_32_1": ("f", 32.1, OUT),
            "carbs_34": ("c", 34, IN_RANGE),
            "carbs_33_9": ("c", 33.9, OUT),
            "carbs_44": ("c", 44, IN_RANGE),
            "carbs_44_1": ("c", 44.1, OUT),
        }
        for name, (key, pct, band) in expect.items():
            row = grams[name]
            self.assertEqual(row["chart_pct"][key], pct, name)
            self.assertEqual(row["tile_pct"][key], pct, name)
            self.assertEqual(row["band_on_chart_pct"][key], band, name)

    def test_zero_food_day_has_no_calorie_share(self):
        day = self.actual["cases"]["zero_food_day"]
        self.assertEqual(day["chart_pct"], {"p": None, "c": None, "f": None})
        self.assertEqual(day["tile_pct"], {"p": None, "c": None, "f": None})
        self.assertEqual(day["band_on_chart_pct"], {"p": "", "c": "", "f": ""})
        self.assertIsNone(day["roll_pct"])
        blank = self.actual["cases"]["missing_fields"]["all_null"]
        self.assertEqual(blank["chart_pct"], {"p": None, "c": None, "f": None})
        self.assertEqual(blank["band_on_chart_pct"], {"p": "", "c": "", "f": ""})

    def test_gaps_and_zero_days_do_not_count_as_zero_percent(self):
        series = self.actual["cases"]["thirty_day_series_with_gaps"]
        self.assertEqual(series["roll_window_days"], 7)
        for date in ("2026-09-05", "2026-09-06", "2026-09-12", "2026-09-08"):
            self.assertIsNone(self._day(date)["pct"]["p"], date)
        # Sep 7's trail contains two gap days. Mean stays 34, not 34 * 5/7.
        self.assertEqual(self._day("2026-09-07")["roll"], {"p": 34, "c": 39, "f": 27})
        # Sep 8 is an explicit 0 g day. It is skipped, not averaged in as 0%.
        self.assertEqual(self._day("2026-09-08")["roll"], {"p": 34, "c": 39, "f": 27})
        counted = [row for row in series["days"] if row["pct"]["p"] is not None]
        self.assertEqual(series["period"]["n_days"], len(counted))
        self.assertNotIn("2026-09-08", [row["date"] for row in counted])

    def test_thirty_day_series_period_pool_and_seven_day_roll(self):
        series = self.actual["cases"]["thirty_day_series_with_gaps"]
        period = series["period"]
        # Sep 10 abstains, so the pool is 25 complete days, not 26.
        # 23 on-target + half + Sep 30: 4116.5 / 4767 / 1474 g, 48800 kcal.
        self.assertEqual(period["n_days"], 25)
        self.assertEqual(period["sum_g"], {"p": 4116.5, "c": 4767, "f": 1474})
        self.assertEqual(period["tot_kcal"], 48800)
        self.assertEqual(period["pct"], {"p": 33.7, "c": 39.1, "f": 27.2})
        self.assertEqual(period["band"], {"p": IN_RANGE, "c": IN_RANGE, "f": IN_RANGE})
        self.assertEqual(period["displayed_avg_g"], {"p": 165, "c": 191, "f": 59})
        latest = series["latest"]
        self.assertEqual(latest["date"], "2026-09-30")
        self.assertEqual(latest["pct"], {"p": 27, "c": 41, "f": 32})
        self.assertEqual(latest["band"]["p"], OUT)
        # Trailing 7 chart points are six on-target days plus Sep 30.
        # (34*6 + 27) / 7 = 33, (39*6 + 41) / 7 → 39.3, (27*6 + 32) / 7 → 27.7.
        self.assertEqual(series["rolling_7d"]["pct"], {"p": 33, "c": 39.3, "f": 27.7})
        self.assertEqual(
            series["rolling_7d"]["band"],
            {"p": IN_RANGE, "c": IN_RANGE, "f": IN_RANGE},
        )
        # 24 days at 34/39/27 plus Sep 30 at 27/41/32. Sep 10 is not in the mean.
        # (34*24 + 27) / 25 = 33.72 → 33.7; carbs 39.08 → 39.1; fat 27.2.
        judge = series["rolling_30d"]
        self.assertEqual(judge["window_days"], 30)
        self.assertEqual(judge["min_logged_days"], 21)
        self.assertEqual(judge["logged_days"], 25)
        self.assertTrue(judge["enough"])
        self.assertEqual(judge["pct"], {"p": 33.7, "c": 39.1, "f": 27.2})
        self.assertEqual(
            judge["band"],
            {"p": IN_RANGE, "c": IN_RANGE, "f": IN_RANGE},
        )
        self.assertEqual(self._day("2026-09-30")["roll30"], judge["pct"])

    def test_grams_and_calorie_share_are_different_judges(self):
        half = self.actual["cases"]["grams_vs_pct_disagreement"]["half_portions_same_pct"]
        self.assertEqual(half["chart_pct"], {"p": 34, "c": 39, "f": 27})
        self.assertEqual(
            half["band_on_chart_pct"],
            {"p": IN_RANGE, "c": IN_RANGE, "f": IN_RANGE},
        )
        self.assertEqual(half["gram_hit_pct"], {"p": 50, "c": 50, "f": 50})

        skewed = self.actual["cases"]["grams_vs_pct_disagreement"][
            "protein_grams_on_target_share_low"
        ]
        self.assertEqual(skewed["chart_pct"], {"p": 26.8, "c": 30.7, "f": 42.5})
        self.assertEqual(skewed["gram_hit_pct"], {"p": 100, "c": 100, "f": 200})
        self.assertEqual(skewed["band_on_chart_pct"]["p"], OUT)
        self.assertEqual(skewed["band_on_chart_pct"]["c"], OUT)
        self.assertEqual(skewed["band_on_chart_pct"]["f"], OUT)

    def test_independent_rounding_can_miss_100(self):
        # 1 g / 1 g / 1 g → 23.5 + 23.5 + 52.9 = 99.9. Each macro is rounded
        # on its own; the chart does not force the three shares to sum to 100.
        pct = self.actual["cases"]["rounding_residue"]["tile_pct"]
        self.assertEqual(pct, {"p": 23.5, "c": 23.5, "f": 52.9})
        self.assertEqual(round(pct["p"] + pct["c"] + pct["f"], 1), 99.9)

    def test_today_tile_and_chart_use_the_same_percent(self):
        """Today tile and Trends bars share macroCalPct's rounding order.

        100.5 g protein / 5 g carbs / 42 g fat is 50.3 / 2.5 / 47.3 on both.
        The old chart formula rounded protein to 50.2 on this input.
        """
        day = self.actual["cases"]["formula_disagreement"]
        self.assertEqual(day["tile_pct"], {"p": 50.3, "c": 2.5, "f": 47.3})
        self.assertEqual(day["chart_pct"], day["tile_pct"])
        self.assertEqual(day["band_on_tile_pct"], day["band_on_chart_pct"])

    def test_missing_macro_is_not_treated_as_zero_grams(self):
        """A day with any missing or non-numeric macro abstains.

        It stays out of the 7-day mean, the 30-day average, and the period
        pool. Sep 10 (protein 170 g, carbs and fat null) used to be judged
        100/0/0 and pulled the fat roll to 20.3%.
        """
        missing = self.actual["cases"]["missing_fields"]
        for name in ("protein_only", "fat_missing", "non_numeric_protein"):
            row = missing[name]
            self.assertEqual(row["chart_pct"], {"p": None, "c": None, "f": None}, name)
            self.assertEqual(row["tile_pct"], row["chart_pct"], name)
            self.assertIsNone(row["roll_pct"], name)
        sep10 = self._day("2026-09-10")
        self.assertEqual(sep10["pct"], {"p": None, "c": None, "f": None})
        self.assertEqual(sep10["roll"], {"p": 34, "c": 39, "f": 27})
        self.assertEqual(sep10["roll30"], {"p": None, "c": None, "f": None})
        series = self.actual["cases"]["thirty_day_series_with_gaps"]
        self.assertNotEqual(series["period"]["sum_g"]["p"], 4286.5)

    def test_thirty_day_judge_needs_21_logged_days_and_skips_nulls(self):
        judge = self.actual["cases"]["rolling_30d_judge"]
        short = judge["logged_20"]
        self.assertEqual(short["window_days"], 30)
        self.assertEqual(short["min_logged_days"], 21)
        self.assertEqual(short["logged_days"], 20)
        self.assertEqual(short["civil_days"], 20)
        self.assertFalse(short["enough"])
        self.assertIsNone(short["pct"])
        self.assertEqual(short["band"], {"p": "", "c": "", "f": ""})

        full = judge["logged_21"]
        self.assertEqual(full["logged_days"], 21)
        self.assertTrue(full["enough"])
        self.assertEqual(full["pct"], {"p": 34, "c": 39, "f": 27})
        self.assertEqual(
            full["band"],
            {"p": IN_RANGE, "c": IN_RANGE, "f": IN_RANGE},
        )

        # 9 nulls inside a 30-day window must not dilute 34% (zero-fill would be 23.8).
        skipped = judge["twenty_one_logged_among_nulls"]
        self.assertEqual(skipped["civil_days"], 30)
        self.assertEqual(skipped["logged_days"], 21)
        self.assertTrue(skipped["enough"])
        self.assertEqual(skipped["pct"], {"p": 34, "c": 39, "f": 27})
        self.assertEqual(skipped["band"]["p"], IN_RANGE)

        thin = judge["twenty_logged_among_nulls"]
        self.assertEqual(thin["civil_days"], 30)
        self.assertEqual(thin["logged_days"], 20)
        self.assertFalse(thin["enough"])
        self.assertIsNone(thin["pct"])

    def test_thirty_day_judge_band_edges(self):
        edges = self.actual["cases"]["rolling_30d_judge"]["band_edges"]
        expect = {
            "protein_29": ("p", 29, IN_RANGE),
            "protein_28_9": ("p", 28.9, OUT),
            "protein_39": ("p", 39, IN_RANGE),
            "protein_39_1": ("p", 39.1, OUT),
            "fat_22": ("f", 22, IN_RANGE),
            "fat_21_9": ("f", 21.9, OUT),
            "fat_32": ("f", 32, IN_RANGE),
            "fat_32_1": ("f", 32.1, OUT),
            "carbs_34": ("c", 34, IN_RANGE),
            "carbs_33_9": ("c", 33.9, OUT),
            "carbs_44": ("c", 44, IN_RANGE),
            "carbs_44_1": ("c", 44.1, OUT),
        }
        for name, (key, pct, band) in expect.items():
            row = edges[name]
            self.assertTrue(row["enough"], name)
            self.assertEqual(row["logged_days"], 21, name)
            self.assertEqual(row["pct"][key], pct, name)
            self.assertEqual(row["band"][key], band, name)


if __name__ == "__main__":
    unittest.main()
