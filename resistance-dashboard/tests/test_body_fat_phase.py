"""Body-fat streak proposals. Read-only: nothing writes nutrition targets."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import body_fat_phase as bf
from rt_dashboard.coach_steps import clear_rules_cache
from rt_dashboard.cut_coach import coach_card_for_payload

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
PHASE_SRC = (ROOT / "body_fat_phase.py").read_text(encoding="utf-8")

# 11/6–11/9 snapshot. Twins share a whole second and differ only in the fraction.
NOV_FIXTURE = [
    {"date": "2026-11-06", "percentage": 10.4, "recorded_at": "2026-11-06T12:00:00.100Z"},
    {"date": "2026-11-06", "percentage": 10.4, "recorded_at": "2026-11-06T12:00:00.900Z"},
    {"date": "2026-11-07", "percentage": 10.6, "recorded_at": "2026-11-07T08:00:00.100Z"},
    {"date": "2026-11-07", "percentage": 10.6, "recorded_at": "2026-11-07T08:00:00.400Z"},
    {"date": "2026-11-07", "percentage": 10.6, "recorded_at": "2026-11-07T15:00:00Z"},
    {"date": "2026-11-07", "percentage": 10.2, "recorded_at": "2026-11-07T18:00:00.100Z"},
    {"date": "2026-11-07", "percentage": 10.2, "recorded_at": "2026-11-07T18:00:00.200Z"},
    {"date": "2026-11-07", "percentage": 10.2, "recorded_at": "2026-11-07T18:00:00.300Z"},
    {"date": "2026-11-08", "percentage": 9.9, "recorded_at": "2026-11-08T12:00:00Z"},
    {"date": "2026-11-09", "percentage": 10.3, "recorded_at": "2026-11-09T12:00:00Z"},
]


def _days(start: str, n: int) -> list[str]:
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _series(start: str, n: int, value: float) -> list[dict]:
    return [
        {"date": day, "percentage": value, "recorded_at": f"{day}T12:00:00Z"}
        for day in _days(start, n)
    ]


def _end(start: str, n: int) -> str:
    return _days(start, n)[-1]


class WindowMath(unittest.TestCase):
    def test_same_day_readings_both_count(self):
        rows = [
            {"date": "2026-11-08", "percentage": 8, "recorded_at": "2026-11-08T08:00:00Z"},
            {"date": "2026-11-08", "percentage": 12, "recorded_at": "2026-11-08T18:00:00Z"},
        ]
        stats = bf.window_stats(rows, "2026-11-08")
        self.assertEqual(stats["n_readings"], 2)
        self.assertAlmostEqual(stats["avg"], 10.0)

    def test_twins_count_once(self):
        rows = [
            {"date": "2026-11-08", "percentage": 10.4, "recorded_at": "2026-11-08T12:00:00.100Z"},
            {"date": "2026-11-08", "percentage": 10.4, "recorded_at": "2026-11-08T12:00:00.900Z"},
            {"date": "2026-11-08", "percentage": 12, "recorded_at": "2026-11-08T18:00:00Z"},
        ]
        stats = bf.window_stats(rows, "2026-11-08")
        self.assertEqual(stats["n_readings"], 2)
        self.assertAlmostEqual(stats["avg"], 11.2)

    def test_empty_window_is_not_zero(self):
        stats = bf.window_stats([], "2026-11-08")
        self.assertIsNone(stats["avg"])
        self.assertNotEqual(stats["avg"], 0)
        self.assertTrue(stats["thin"])

    def test_missing_days_are_not_zero_filled(self):
        rows = [
            {"date": "2026-11-02", "percentage": 10, "recorded_at": "2026-11-02T12:00:00Z"},
            {"date": "2026-11-08", "percentage": 20, "recorded_at": "2026-11-08T12:00:00Z"},
        ]
        stats = bf.window_stats(rows, "2026-11-08")
        self.assertAlmostEqual(stats["avg"], 15.0)
        self.assertNotAlmostEqual(stats["avg"], (10 + 20) / 7.0)
        self.assertEqual(stats["n_readings"], 2)
        self.assertEqual(stats["n_days"], 2)
        self.assertTrue(stats["thin"])

    def test_logged_zero_counts_and_out_of_range_does_not(self):
        rows = [
            {"date": "2026-11-08", "percentage": 0, "recorded_at": "2026-11-08T08:00:00Z"},
            {"date": "2026-11-08", "percentage": 20, "recorded_at": "2026-11-08T18:00:00Z"},
            {"date": "2026-11-08", "percentage": -1, "recorded_at": "2026-11-08T09:00:00Z"},
            {"date": "2026-11-08", "percentage": 140, "recorded_at": "2026-11-08T10:00:00Z"},
        ]
        stats = bf.window_stats(rows, "2026-11-08")
        self.assertEqual(stats["n_readings"], 2)
        self.assertAlmostEqual(stats["avg"], 10.0)

    def test_dst_window_is_seven_civil_days(self):
        stats = bf.window_stats(
            [{"date": "2026-10-27", "percentage": 14, "recorded_at": "2026-10-27T12:00:00Z"}],
            "2026-11-02",
        )
        self.assertEqual(stats["window_start"], "2026-10-27")
        self.assertEqual(stats["window_end"], "2026-11-02")
        self.assertEqual(stats["n_readings"], 1)


class StreakGate(unittest.TestCase):
    def test_fourteen_qualifying_days_propose_bulk_and_thirteen_hold(self):
        start = "2026-10-29"
        proposed = bf.evaluate(_series(start, 17, 10.0), as_of=_end(start, 17), phase="cut", tdee=2318)
        self.assertEqual(proposed["streak_days"], 14)
        self.assertEqual(proposed["streak_needed"], 14)
        self.assertEqual(proposed["status"], "propose_bulk")
        self.assertEqual(proposed["phase"], "cut")
        self.assertAlmostEqual(proposed["avg_7d"], 10.0)
        held = bf.evaluate(_series(start, 16, 10.0), as_of=_end(start, 16), phase="cut", tdee=2318)
        self.assertEqual(held["streak_days"], 13)
        self.assertEqual(held["status"], "hold")
        self.assertIsNone(held["proposed_targets"])

    def test_avg_10_1_resets_streak(self):
        rows = _series("2026-10-01", 51, 10.0)
        rows[43]["percentage"] = 10.7  # 2026-11-13; windows that include it average 10.1
        out = bf.evaluate(rows, as_of="2026-11-20", phase="cut", tdee=2318)
        self.assertAlmostEqual(bf.window_stats(rows, "2026-11-19")["avg"], 10.1)
        self.assertAlmostEqual(out["avg_7d"], 10.0)
        self.assertEqual(out["streak_days"], 1)
        self.assertEqual(out["status"], "hold")
        self.assertIsNone(out["proposed_targets"])
        flat = bf.evaluate(_series("2026-10-29", 17, 10.1), as_of=_end("2026-10-29", 17), phase="cut")
        self.assertEqual(flat["streak_days"], 0)
        self.assertEqual(flat["status"], "hold")

    def test_thin_day_resets_streak(self):
        rows = [
            row
            for row in _series("2026-10-01", 51, 10.0)
            if not ("2026-11-10" <= row["date"] <= "2026-11-14")
        ]
        out = bf.evaluate(rows, as_of="2026-11-20", phase="cut", tdee=2318)
        self.assertTrue(bf.window_stats(rows, "2026-11-17")["thin"])
        self.assertFalse(out["n_readings_7d"] < 4 and out["n_days_7d"] < 3)
        self.assertEqual(out["streak_days"], 3)
        self.assertEqual(out["status"], "hold")
        self.assertIsNone(out["proposed_targets"])

    def test_today_thin_is_data_thin_and_streak_zero(self):
        rows = [
            {"date": "2026-11-09", "percentage": 10.0, "recorded_at": "2026-11-09T08:00:00Z"},
            {"date": "2026-11-09", "percentage": 10.2, "recorded_at": "2026-11-09T18:00:00Z"},
        ]
        out = bf.evaluate(rows, as_of="2026-11-09", phase="cut")
        self.assertEqual(out["status"], "data_thin")
        self.assertEqual(out["streak_days"], 0)
        self.assertEqual(out["n_readings_7d"], 2)
        self.assertEqual(out["n_days_7d"], 1)
        self.assertIsNone(out["proposed_targets"])

    def test_bulk_15_for_14_days_proposes_cut_from_live_targets(self):
        live = {"calories": 1875, "protein_g": 160, "carbs_g": 180, "fat_g": 55, "phase": "slow_bulk"}
        start = "2026-10-29"
        out = bf.evaluate(
            _series(start, 17, 15.0),
            as_of=_end(start, 17),
            phase=live["phase"],
            applied=live,
        )
        self.assertEqual(out["phase"], "bulk")
        self.assertEqual(out["streak_days"], 14)
        self.assertEqual(out["status"], "propose_cut")
        self.assertEqual(out["threshold_label"], "Cut at >=15.0%")
        proposed = out["proposed_targets"]
        self.assertEqual(proposed["calories"], 1875)
        self.assertEqual(proposed["protein_g"], 160)
        self.assertEqual(proposed["carbs_g"], 180)
        self.assertEqual(proposed["fat_g"], 55)
        self.assertFalse(proposed["applies_automatically"])
        self.assertNotEqual(proposed["calories"], 2000)
        echoed = bf.evaluate(
            _series(start, 17, 15.0),
            as_of=_end(start, 17),
            phase="bulk",
            applied={"calories": 2000, "protein_g": 170, "carbs_g": 195, "fat_g": 60},
        )
        self.assertEqual(echoed["proposed_targets"]["calories"], 2000)
        self.assertEqual(echoed["proposed_targets"]["carbs_g"], 195)

    def test_avg_14_9_resets_bulk_streak(self):
        rows = _series("2026-10-01", 51, 15.0)
        rows[43]["percentage"] = 14.3  # windows that include 2026-11-13 average 14.9
        out = bf.evaluate(rows, as_of="2026-11-20", phase="bulk")
        self.assertAlmostEqual(bf.window_stats(rows, "2026-11-19")["avg"], 14.9)
        self.assertAlmostEqual(out["avg_7d"], 15.0)
        self.assertEqual(out["streak_days"], 1)
        self.assertEqual(out["status"], "hold")
        flat = bf.evaluate(_series("2026-10-29", 17, 14.9), as_of=_end("2026-10-29", 17), phase="slow_bulk")
        self.assertEqual(flat["streak_days"], 0)
        self.assertEqual(flat["status"], "hold")
        self.assertEqual(flat["phase"], "bulk")

    def test_bulk_proposal_uses_live_tdee_then_plus_200(self):
        start = "2026-10-29"
        out = bf.evaluate(_series(start, 17, 10.0), as_of=_end(start, 17), phase=None, tdee=2318)
        self.assertEqual(out["phase"], "cut")
        self.assertEqual(out["status"], "propose_bulk")
        self.assertEqual(out["threshold_label"], "Bulk at <=10.0%")
        proposed = out["proposed_targets"]
        self.assertEqual(proposed["tdee_kcal"], 2318)
        self.assertEqual(proposed["flip_day"]["calories"], 2318)
        self.assertEqual(proposed["flip_day"]["protein_g"], 170)
        self.assertEqual(proposed["flip_day"]["fat_g"], 65)
        self.assertEqual(proposed["flip_day"]["carbs_g"], 263)
        self.assertEqual(proposed["after_14d"]["calories"], 2500)
        self.assertEqual(proposed["after_14d"]["protein_g"], 170)
        self.assertEqual(proposed["after_14d"]["fat_g"], 65)
        self.assertEqual(proposed["after_14d"]["carbs_g"], 309)
        self.assertEqual(proposed["gain_target_pct_wk"], 0.25)
        self.assertEqual(proposed["gain_band_pct_wk"], [0.1, 0.35])
        self.assertFalse(proposed["applies_automatically"])

    def test_nov_fixture_holds_in_cut_with_streak_zero(self):
        raw = [row["percentage"] for row in NOV_FIXTURE]
        self.assertAlmostEqual(sum(raw) / len(raw), 10.34)
        for day in ("2026-11-09", "2026-11-10"):
            out = bf.evaluate(NOV_FIXTURE, as_of=day, phase="cut")
            self.assertEqual(out["phase"], "cut")
            self.assertEqual(out["n_readings_7d"], 6)
            self.assertEqual(out["n_days_7d"], 4)
            self.assertAlmostEqual(out["avg_7d"], (10.4 + 10.6 + 10.6 + 10.2 + 9.9 + 10.3) / 6)
            # 6 deduped readings on 4 days clears the thin guard. The mean is above
            # 10.0, so today does not join a streak.
            self.assertFalse(bf.window_stats(NOV_FIXTURE, day)["thin"])
            self.assertEqual(out["status"], "hold")
            self.assertEqual(out["streak_days"], 0)
            self.assertIsNone(out["proposed_targets"])
            self.assertNotIn("avg_14d", out)
            self.assertNotIn("consecutive_days_past_threshold", out)


class NoWrite(unittest.TestCase):
    def test_module_does_not_name_target_writers(self):
        for name in ("switch_phase", "applied_targets_write", "persist_targets", "body_fat_by_date"):
            self.assertNotIn(name, PHASE_SRC)

    def test_evaluate_does_not_mutate_or_persist_targets(self):
        import rt_dashboard.nutrition_store as ns

        applied = {"calories": 1875, "protein_g": 160, "carbs_g": 180, "fat_g": 55, "phase": "cut"}
        snapshot = dict(applied)
        start = "2026-10-29"
        with mock.patch.object(ns, "persist_targets", side_effect=AssertionError("persist")):
            with mock.patch.object(ns, "applied_targets_write", side_effect=AssertionError("applied")):
                out = bf.evaluate(
                    _series(start, 17, 10.0),
                    as_of=_end(start, 17),
                    phase=applied["phase"],
                    tdee=2318,
                    applied=applied,
                )
        self.assertEqual(applied, snapshot)
        self.assertEqual(out["status"], "propose_bulk")
        self.assertIsNot(out["proposed_targets"], applied)


class CoachCardPayload(unittest.TestCase):
    def setUp(self):
        clear_rules_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"FITDASH_COACH_STATE_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
        clear_rules_cache()

    def test_coach_card_exposes_bf_phase_and_does_not_write_targets(self):
        import rt_dashboard.nutrition_store as ns

        payload = {
            "meta": {"local_today": "2026-11-09"},
            "health": {"body_fat": NOV_FIXTURE, "weight": [], "nutrition": [], "steps": []},
            "nutrition_store": {
                "targets": {
                    "phase": "cut",
                    "calories": 2000,
                    "protein_g": 170,
                    "carbs_g": 195,
                    "fat_g": 60,
                }
            },
        }
        with mock.patch.object(ns, "persist_targets", side_effect=AssertionError("persist")):
            with mock.patch.object(ns, "applied_targets_write", side_effect=AssertionError("applied")):
                card = coach_card_for_payload(payload, as_of="2026-11-09")
        self.assertIn("bf_phase", card)
        self.assertEqual(card["bf_phase"]["status"], "hold")
        self.assertEqual(card["bf_phase"]["streak_days"], 0)
        self.assertEqual(card["bf_phase"]["phase"], "cut")
        self.assertIsNone(card["bf_phase"]["proposed_targets"])


class CoachCardUi(unittest.TestCase):
    def test_phase_card_shows_avg_threshold_streak_and_chips(self):
        self.assertIn('id="coach-bf-avg"', HTML)
        self.assertIn('id="coach-bf-threshold"', HTML)
        self.assertIn('id="coach-bf-streak"', HTML)
        self.assertIn('id="coach-bf-thin"', HTML)
        self.assertIn('id="coach-bf-proposal"', HTML)
        proposal = HTML[HTML.index('id="coach-bf-proposal"'): HTML.index('id="coach-bf-proposal"') + 80]
        self.assertIn("<p ", HTML[HTML.rfind("<p ", 0, HTML.index('id="coach-bf-proposal"')): HTML.index('id="coach-bf-proposal"') + 40])
        self.assertNotIn("<button", proposal)
        self.assertIn("7-day BF avg", APP)
        self.assertIn("BF data thin", APP)
        self.assertIn("Proposed: switch to BULK", APP)
        self.assertIn("Proposed: switch to CUT", APP)
        self.assertIn("≤10%", APP)
        self.assertIn("≥15%", APP)
        self.assertIn("7-day avg", APP)
        self.assertNotIn("paused: bulk", APP)
        self.assertNotIn("paused: bulk", HTML)


if __name__ == "__main__":
    unittest.main()
