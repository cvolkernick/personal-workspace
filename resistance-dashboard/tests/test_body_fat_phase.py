"""Body-fat median phase flip. Auto-apply goes through the real target writers."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import body_fat_phase as bf
from rt_dashboard.coach_steps import clear_rules_cache
from rt_dashboard.cut_coach import coach_card_for_payload

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
PHASE_SRC = (ROOT / "body_fat_phase.py").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")

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

CUT_LIVE = {"phase": "cut", "calories": 1875, "protein_g": 160, "carbs_g": 180, "fat_g": 55}


def _days(start: str, n: int) -> list[str]:
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _series(start: str, n: int, value: float) -> list[dict]:
    return [
        {"date": day, "percentage": value, "recorded_at": f"{day}T12:00:00Z"}
        for day in _days(start, n)
    ]


def _readings(day: str, n: int, value: float) -> list[dict]:
    return [
        {"date": day, "percentage": value, "recorded_at": f"{day}T12:00:{i:02d}Z"}
        for i in range(n)
    ]


class WindowMath(unittest.TestCase):
    def test_same_day_readings_both_count(self):
        rows = [
            {"date": "2026-11-08", "percentage": 8, "recorded_at": "2026-11-08T08:00:00Z"},
            {"date": "2026-11-08", "percentage": 12, "recorded_at": "2026-11-08T18:00:00Z"},
        ]
        stats = bf.window_stats(rows, "2026-11-08")
        self.assertEqual(stats["n_readings"], 2)
        self.assertAlmostEqual(stats["median"], 10.0)

    def test_twins_count_once(self):
        rows = [
            {"date": "2026-11-08", "percentage": 10.4, "recorded_at": "2026-11-08T12:00:00.100Z"},
            {"date": "2026-11-08", "percentage": 10.4, "recorded_at": "2026-11-08T12:00:00.900Z"},
            {"date": "2026-11-08", "percentage": 12, "recorded_at": "2026-11-08T18:00:00Z"},
        ]
        stats = bf.window_stats(rows, "2026-11-08")
        self.assertEqual(stats["n_readings"], 2)
        self.assertAlmostEqual(stats["median"], 11.2)

    def test_empty_window_is_not_zero(self):
        stats = bf.window_stats([], "2026-11-08")
        self.assertIsNone(stats["median"])
        self.assertNotEqual(stats["median"], 0)
        self.assertTrue(stats["thin"])

    def test_missing_days_are_not_zero_filled(self):
        rows = [
            {"date": "2026-11-02", "percentage": 10, "recorded_at": "2026-11-02T12:00:00Z"},
            {"date": "2026-11-08", "percentage": 20, "recorded_at": "2026-11-08T12:00:00Z"},
        ]
        stats = bf.window_stats(rows, "2026-11-08")
        self.assertAlmostEqual(stats["median"], 15.0)
        self.assertEqual(stats["n_readings"], 2)
        self.assertEqual(stats["n_days"], 2)
        self.assertTrue(stats["thin"])

    def test_four_readings_on_one_day_are_countable(self):
        rows = _readings("2026-11-08", 4, 10.0)
        stats = bf.window_stats(rows, "2026-11-08")
        self.assertEqual(stats["n_readings"], 4)
        self.assertEqual(stats["n_days"], 1)
        self.assertFalse(stats["thin"])
        self.assertTrue(bf.window_stats(_readings("2026-11-08", 3, 10.0), "2026-11-08")["thin"])

    def test_logged_zero_counts_and_out_of_range_does_not(self):
        rows = [
            {"date": "2026-11-08", "percentage": 0, "recorded_at": "2026-11-08T08:00:00Z"},
            {"date": "2026-11-08", "percentage": 20, "recorded_at": "2026-11-08T18:00:00Z"},
            {"date": "2026-11-08", "percentage": -1, "recorded_at": "2026-11-08T09:00:00Z"},
            {"date": "2026-11-08", "percentage": 140, "recorded_at": "2026-11-08T10:00:00Z"},
        ]
        stats = bf.window_stats(rows, "2026-11-08")
        self.assertEqual(stats["n_readings"], 2)
        self.assertAlmostEqual(stats["median"], 10.0)

    def test_dst_window_is_seven_civil_days(self):
        stats = bf.window_stats(
            [{"date": "2026-10-27", "percentage": 14, "recorded_at": "2026-10-27T12:00:00Z"}],
            "2026-11-02",
        )
        self.assertEqual(stats["window_start"], "2026-10-27")
        self.assertEqual(stats["window_end"], "2026-11-02")
        self.assertEqual(stats["n_readings"], 1)


class CountedRule(unittest.TestCase):
    def test_fourteen_counted_days_flip_and_thirteen_hold(self):
        rows = _series("2026-10-01", 40, 9.5)
        end = rows[-1]["date"]
        flipped = bf.evaluate(rows, as_of=end, phase="cut")
        self.assertEqual(flipped["counted"], 14)
        self.assertEqual(flipped["hits"], 14)
        self.assertEqual(flipped["hits_needed"], 12)
        self.assertTrue(flipped["should_flip"])
        self.assertEqual(flipped["status"], "flip")
        self.assertAlmostEqual(flipped["median_7d"], 9.5)
        self.assertAlmostEqual(flipped["median_14d"], 9.5)
        self.assertEqual(flipped["phase"], "cut")
        short = _series("2026-10-29", 16, 10.0)
        held = bf.evaluate(short, as_of=short[-1]["date"], phase="cut")
        self.assertEqual(held["counted"], 13)
        self.assertEqual(held["hits"], 13)
        self.assertFalse(held["should_flip"])
        self.assertEqual(held["status"], "hold")
        self.assertIsNone(held["median_14d"])

    def test_day_13_outlier_does_not_reset(self):
        """One counted day above 10% is a miss. The other 13 still count, and 13 >= 12 flips."""
        today = "2026-11-20"
        hist_end = bf.add_days(today, -7)
        rows = _series(bf.add_days(hist_end, -40), 41, 9.0) + _readings(today, 4, 12.0)
        self.assertEqual(rows[-5]["date"], hist_end)
        out = bf.evaluate(rows, as_of=today, phase="cut")
        self.assertEqual(out["counted"], 14)
        self.assertEqual(out["hits"], 13)
        self.assertNotEqual(out["hits"], 0)
        misses = [row for row in out["counted_days"] if not row["past"]]
        self.assertEqual([row["date"] for row in misses], [today])
        self.assertAlmostEqual(misses[0]["median"], 12.0)
        self.assertAlmostEqual(out["median_14d"], 9.0)
        self.assertTrue(out["should_flip"])
        self.assertEqual(out["status"], "flip")

    def test_eleven_hits_hold_without_resetting(self):
        end = "2026-11-20"
        low_end = bf.add_days(end, -10)
        rows = _series(bf.add_days(low_end, -30), 31, 9.0)
        for day in (bf.add_days(end, -2), bf.add_days(end, -1), end):
            rows.extend(_readings(day, 4, 12.0))
        out = bf.evaluate(rows, as_of=end, phase="cut")
        self.assertEqual(out["counted"], 14)
        self.assertEqual(out["hits"], 11)
        self.assertGreater(out["hits"], 0)
        self.assertFalse(out["should_flip"])
        self.assertEqual(out["status"], "hold")

    def test_thin_day_pauses_and_the_window_stretches(self):
        rows = [row for row in _series("2026-09-01", 70, 9.0) if row["date"] not in set(_days("2026-10-10", 8))]
        out = bf.evaluate(rows, as_of="2026-10-30", phase="cut")
        dates = [row["date"] for row in out["counted_days"]]
        self.assertTrue(bf.window_stats(rows, "2026-10-17")["thin"])
        self.assertNotIn("2026-10-17", dates)
        self.assertEqual(out["counted"], 14)
        self.assertEqual(out["hits"], 14)
        self.assertLess(dates[0], "2026-10-17")
        self.assertGreater(dates[-1], "2026-10-17")
        self.assertTrue(out["should_flip"])
        after_pause = [day for day in dates if day > "2026-10-20"]
        self.assertLess(len(after_pause), 14)

    def test_bulk_bound_is_fifteen(self):
        rows = _series("2026-10-01", 40, 15.0)
        out = bf.evaluate(rows, as_of=rows[-1]["date"], phase="slow_bulk")
        self.assertEqual(out["phase"], "bulk")
        self.assertEqual(out["threshold_label"], "Cut at >=15.0%")
        self.assertEqual(out["hits"], 14)
        self.assertTrue(out["should_flip"])
        flat = bf.evaluate(_series("2026-10-01", 40, 14.9), as_of=_days("2026-10-01", 40)[-1], phase="bulk")
        self.assertEqual(flat["hits"], 0)
        self.assertFalse(flat["should_flip"])
        self.assertEqual(flat["phase"], "bulk")

    def test_anchor_drops_days_on_and_before_the_flip(self):
        rows = _series("2026-10-01", 40, 9.5)
        end = rows[-1]["date"]
        out = bf.evaluate(rows, as_of=end, phase="cut", anchor=end)
        self.assertEqual(out["counted"], 0)
        self.assertFalse(out["should_flip"])
        nxt = bf.add_days(end, 1)
        later = bf.evaluate(rows + _series(nxt, 1, 9.5), as_of=nxt, phase="bulk", anchor=end)
        self.assertTrue(all(row["date"] > end for row in later["counted_days"]))
        self.assertLess(later["counted"], 14)
        self.assertFalse(later["should_flip"])


class _WriterCase(unittest.TestCase):
    def setUp(self):
        clear_rules_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ,
            {
                "FITDASH_COACH_STATE_DIR": self.tmp.name,
                "LOCAL_WORKSPACE_DIR": self.tmp.name,
                "TURSO_DATABASE_URL": "",
                "TURSO_AUTH_TOKEN": "",
            },
        )
        self.env.start()
        import rt_dashboard.nutrition_store as ns

        self.ns = ns
        self.write_calls = []
        self.persist_calls = []
        real_write = ns.applied_targets_write
        real_persist = ns.persist_targets

        def spy_write(*args, **kwargs):
            self.write_calls.append(kwargs.get("message"))
            return real_write(*args, **kwargs)

        def spy_persist(*args, **kwargs):
            self.persist_calls.append(kwargs.get("message"))
            return real_persist(*args, **kwargs)

        self.patches = [
            mock.patch.object(ns, "applied_targets_write", spy_write),
            mock.patch.object(ns, "persist_targets", spy_persist),
            mock.patch("rt_dashboard.turso_http.connect", side_effect=AssertionError("turso")),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.env.stop()
        self.tmp.cleanup()
        clear_rules_cache()

    def _targets(self) -> dict:
        path = Path(self.tmp.name) / "fitness" / "nutrition" / "targets.json"
        self.assertTrue(path.is_file(), "applied_targets_write did not land a targets file")
        return json.loads(path.read_text(encoding="utf-8"))


class AutoApply(_WriterCase):
    def test_module_calls_the_real_target_writers(self):
        self.assertIn("applied_targets_write", PHASE_SRC)
        self.assertIn("persist_targets", PHASE_SRC)
        self.assertIn("update_targets", PHASE_SRC)
        self.assertIn("record_manual_flip", SERVER)
        self.assertIn('base["phase"] = raw_phase', SERVER)

    def test_cut_to_bulk_writes_maintenance_then_plus_200_on_day_14(self):
        rows = _series("2026-10-01", 40, 9.5)
        as_of = rows[-1]["date"]
        first = bf.run_phase(
            rows, as_of=as_of, phase="cut", tdee=2318, applied=dict(CUT_LIVE), user_id="u", apply=True
        )
        self.assertEqual(first["status"], "flipped")
        self.assertEqual(first["phase_label"], "BULK")
        self.assertEqual(first["banner"]["text"], f"Phase switched to BULK on {as_of}")
        written = self._targets()
        self.assertEqual(written["phase"], "slow_bulk")
        self.assertEqual(written["calories"], 2318)
        self.assertEqual(written["protein_g"], 170)
        self.assertEqual(written["fat_g"], 65)
        self.assertEqual(written["carbs_g"], 263)
        self.assertEqual(len(self.write_calls), 1)
        self.assertEqual(len(self.persist_calls), 1)
        self.assertIn("cut to bulk", self.write_calls[0])

        again = bf.run_phase(
            rows, as_of=as_of, phase="cut", tdee=2318, applied=dict(CUT_LIVE), user_id="u", apply=True
        )
        self.assertFalse(again["applied"])
        self.assertEqual(len(self.write_calls), 1)
        self.assertEqual(again["anchor"], as_of)

        nxt = bf.add_days(as_of, 1)
        nxt_rows = rows + _series(nxt, 1, 9.5)
        following = bf.run_phase(
            nxt_rows,
            as_of=nxt,
            phase="slow_bulk",
            tdee=2318,
            applied={"phase": "slow_bulk", "calories": 2318, "protein_g": 170, "carbs_g": 263, "fat_g": 65},
            user_id="u",
            apply=True,
        )
        self.assertEqual(following["phase"], "bulk")
        self.assertFalse(following["should_flip"])
        self.assertFalse(following["applied"])
        self.assertEqual(len(self.write_calls), 1)
        self.assertTrue(all(row["date"] > as_of for row in following["counted_days"]))

        day14 = bf.add_days(as_of, 14)
        ramped = bf.run_phase(
            rows,
            as_of=day14,
            phase="slow_bulk",
            tdee=3000,
            applied={"phase": "slow_bulk", "calories": 2318, "protein_g": 170, "carbs_g": 263, "fat_g": 65},
            user_id="u",
            apply=True,
        )
        self.assertEqual(ramped["status"], "ramp")
        self.assertTrue(ramped["applied"])
        stepped = self._targets()
        self.assertEqual(stepped["calories"], 2500)
        self.assertEqual(stepped["protein_g"], 170)
        self.assertEqual(stepped["fat_g"], 65)
        self.assertEqual(stepped["carbs_g"], 309)
        self.assertEqual(stepped["phase"], "slow_bulk")
        self.assertEqual(len(self.write_calls), 2)
        self.assertIn("TDEE+200", self.write_calls[1])

        repeat = bf.run_phase(
            rows,
            as_of=day14,
            phase="slow_bulk",
            tdee=3000,
            applied=stepped,
            user_id="u",
            apply=True,
        )
        self.assertFalse(repeat["applied"])
        self.assertEqual(len(self.write_calls), 2)
        self.assertEqual(self._targets()["calories"], 2500)

    def test_bulk_to_cut_restores_stored_cut_targets(self):
        rows = _series("2026-10-01", 40, 9.5)
        as_of = rows[-1]["date"]
        bf.run_phase(rows, as_of=as_of, phase="cut", tdee=2318, applied=dict(CUT_LIVE), user_id="u", apply=True)
        high = _series(bf.add_days(as_of, 1), 30, 16.0)
        end = high[-1]["date"]
        back = bf.run_phase(
            rows + high,
            as_of=end,
            phase="slow_bulk",
            tdee=2318,
            applied={"phase": "slow_bulk", "calories": 2318, "protein_g": 170, "carbs_g": 263, "fat_g": 65},
            user_id="u",
            apply=True,
        )
        self.assertEqual(back["status"], "flipped")
        self.assertEqual(back["phase"], "cut")
        self.assertEqual(back["banner"]["text"], f"Phase switched to CUT on {end}")
        restored = self._targets()
        self.assertEqual(restored["phase"], "cut")
        self.assertEqual(restored["calories"], 1875)
        self.assertEqual(restored["protein_g"], 160)
        self.assertEqual(restored["carbs_g"], 180)
        self.assertEqual(restored["fat_g"], 55)

    def test_two_requests_same_day_write_once(self):
        rows = _series("2026-10-01", 40, 9.5)
        as_of = rows[-1]["date"]
        barrier = threading.Barrier(2)
        errors = []

        def worker():
            try:
                barrier.wait(timeout=5)
                bf.run_phase(
                    rows, as_of=as_of, phase="cut", tdee=2318, applied=dict(CUT_LIVE), user_id="race", apply=True
                )
            except Exception as exc:  # noqa: BLE001 - fail the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        phase_events = [
            row for row in bf._file_load() if row.get("kind") == "phase" and row.get("user_id") == "race"
        ]
        self.assertEqual(len(phase_events), 1)
        self.assertEqual(phase_events[0]["trigger"], "auto")
        self.assertEqual(len(self.write_calls), 1)
        self.assertEqual(self._targets()["phase"], "slow_bulk")
        self.assertEqual(self._targets()["calories"], 2318)

    def test_manual_override_logs_and_resets_the_count(self):
        rows = _series("2026-10-01", 40, 9.5)
        as_of = rows[-1]["date"]
        event = bf.record_manual_flip(
            previous=dict(CUT_LIVE),
            updated={"phase": "slow_bulk", "calories": 2200, "protein_g": 170, "carbs_g": 230, "fat_g": 60},
            user_id="u",
            as_of=as_of,
        )
        self.assertEqual(event["trigger"], "manual")
        self.assertEqual(event["old_phase"], "cut")
        self.assertEqual(event["date"], as_of)
        self.assertEqual(self.write_calls, [])
        self.assertEqual(self.persist_calls, [])
        view = bf.run_phase(
            rows, as_of=as_of, phase="cut", tdee=2318, applied=dict(CUT_LIVE), user_id="u", apply=True
        )
        self.assertEqual(view["anchor"], as_of)
        self.assertEqual(view["counted"], 0)
        self.assertFalse(view["applied"])
        self.assertEqual(self.write_calls, [])
        logged = bf.load_events("u")
        self.assertEqual([row["trigger"] for row in logged], ["manual"])

    def test_disable_flag_blocks_the_write(self):
        rows = _series("2026-10-01", 40, 9.5)
        as_of = rows[-1]["date"]
        with mock.patch.object(bf, "auto_flip_enabled", return_value=False):
            view = bf.run_phase(
                rows, as_of=as_of, phase="cut", tdee=2318, applied=dict(CUT_LIVE), user_id="u", apply=True
            )
        self.assertEqual(view["status"], "disabled")
        self.assertTrue(view["should_flip"])
        self.assertFalse(view["applied"])
        self.assertEqual(self.write_calls, [])
        self.assertFalse((Path(self.tmp.name) / "fitness" / "nutrition" / "targets.json").exists())

    def test_missing_tdee_does_not_invent_calories(self):
        rows = _series("2026-10-01", 40, 9.5)
        view = bf.run_phase(
            rows, as_of=rows[-1]["date"], phase="cut", tdee=None, applied=dict(CUT_LIVE), user_id="u", apply=True
        )
        self.assertEqual(view["status"], "hold")
        self.assertIn("TDEE", view["note"])
        self.assertEqual(self.write_calls, [])

    def test_banner_lasts_seven_days_and_can_be_dismissed(self):
        rows = _series("2026-10-01", 40, 9.5)
        as_of = rows[-1]["date"]
        bf.run_phase(rows, as_of=as_of, phase="cut", tdee=2318, applied=dict(CUT_LIVE), user_id="u", apply=True)
        shown = bf.run_phase(
            rows, as_of=bf.add_days(as_of, 7), phase="slow_bulk", tdee=2318, applied={"phase": "slow_bulk"}, user_id="u", apply=False
        )
        self.assertEqual(shown["banner"]["text"], f"Phase switched to BULK on {as_of}")
        self.assertEqual(shown["last_flip"]["trigger"], "auto")
        hidden = bf.run_phase(
            rows, as_of=bf.add_days(as_of, 8), phase="slow_bulk", tdee=2318, applied={"phase": "slow_bulk"}, user_id="u", apply=False
        )
        self.assertIsNone(hidden["banner"])
        self.assertEqual(bf.dismiss_bf_banner(user_id="u", as_of=as_of)["dismissed"], True)
        dismissed = bf.run_phase(
            rows, as_of=as_of, phase="slow_bulk", tdee=2318, applied={"phase": "slow_bulk"}, user_id="u", apply=False
        )
        self.assertIsNone(dismissed["banner"])
        self.assertEqual(dismissed["last_flip"]["date"], as_of)

    def test_dry_run_on_november_fixture_holds_and_writes_nothing(self):
        deduped = bf.dedupe_twins(bf.measurements(NOV_FIXTURE))
        self.assertEqual(len(deduped), 6)
        self.assertEqual(len({row["date"] for row in deduped}), 4)
        values = sorted(row["percentage"] for row in deduped)
        self.assertAlmostEqual(bf._median(values), 10.35)
        for day in ("2026-11-09", "2026-11-10"):
            view = bf.run_phase(
                NOV_FIXTURE,
                as_of=day,
                phase="cut",
                tdee=2318,
                applied={"phase": "cut", "calories": 2000, "protein_g": 170, "carbs_g": 195, "fat_g": 60},
                user_id="dry",
                apply=True,
            )
            self.assertEqual(view["phase"], "cut")
            self.assertEqual(view["status"], "hold")
            self.assertFalse(view["thin"])
            self.assertEqual(view["n_readings_7d"], 6)
            self.assertEqual(view["n_days_7d"], 4)
            self.assertAlmostEqual(view["median_7d"], 10.35)
            self.assertEqual(view["hits"], 0)
            self.assertFalse(view["should_flip"])
            self.assertFalse(view["applied"])
            self.assertIsNone(view["new_targets"])
        self.assertEqual(self.write_calls, [])
        self.assertEqual(self.persist_calls, [])
        self.assertFalse((Path(self.tmp.name) / "fitness" / "nutrition" / "targets.json").exists())
        self.assertFalse(bf._log_path().is_file())


class RulesFlag(unittest.TestCase):
    def test_auto_flip_defaults_on_and_reads_the_json_flag(self):
        for path in (
            ROOT / "fitness" / "coach" / "coach_rules.json",
            REPO / "fitness" / "coach" / "coach_rules.json",
        ):
            self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["bf_auto_flip"])
        self.assertTrue(bf.auto_flip_enabled())
        with mock.patch("rt_dashboard.coach_steps.read_json", return_value={"bf_auto_flip": False}):
            self.assertFalse(bf.auto_flip_enabled())
        with mock.patch("rt_dashboard.coach_steps.read_json", return_value={}):
            self.assertTrue(bf.auto_flip_enabled())
        with mock.patch("rt_dashboard.coach_steps.read_json", return_value=None):
            self.assertTrue(bf.auto_flip_enabled())


class CoachCardPayload(_WriterCase):
    def test_hold_fixture_is_on_the_card_and_writes_nothing(self):
        payload = {
            "meta": {"local_today": "2026-11-10", "user_id": "card"},
            "health": {"body_fat": NOV_FIXTURE, "weight": [], "nutrition": [], "steps": []},
            "nutrition_store": {
                "targets": {"phase": "cut", "calories": 2000, "protein_g": 170, "carbs_g": 195, "fat_g": 60}
            },
        }
        card = coach_card_for_payload(payload, as_of="2026-11-10")
        phase = card["bf_phase"]
        self.assertEqual(phase["status"], "hold")
        self.assertEqual(phase["phase"], "cut")
        self.assertEqual(phase["phase_label"], "CUT")
        self.assertAlmostEqual(phase["median_7d"], 10.35)
        self.assertFalse(phase["thin"])
        self.assertEqual(phase["hits"], 0)
        self.assertEqual(phase["hits_needed"], 12)
        self.assertIsNone(phase["median_14d"])
        self.assertIn("<=10.0%", phase["threshold_label"])
        self.assertIsNone(phase["last_flip"])
        self.assertEqual(self.write_calls, [])
        self.assertEqual(payload["nutrition_store"]["targets"]["calories"], 2000)
        self.assertEqual(payload["nutrition_store"]["targets"]["phase"], "cut")

    def test_flip_updates_the_payload_targets_from_the_live_tdee(self):
        payload = {
            "meta": {"local_today": "2026-11-09", "user_id": "card"},
            "health": {"body_fat": _series("2026-10-01", 40, 9.5), "weight": [], "nutrition": [], "steps": []},
            "nutrition_store": {"targets": dict(CUT_LIVE)},
        }
        as_of = payload["health"]["body_fat"][-1]["date"]
        self.assertEqual(as_of, "2026-11-09")
        # No weigh-ins: the coach card has no live TDEE, so it must not invent calories.
        held = coach_card_for_payload(payload, as_of=as_of)
        self.assertEqual(held["bf_phase"]["status"], "hold")
        self.assertIn("TDEE", held["bf_phase"]["note"])
        self.assertEqual(self.write_calls, [])
        self.assertEqual(payload["nutrition_store"]["targets"]["calories"], 1875)
        # The card passes weekly tdee into the same helper. 2318 is that live value.
        self.assertIn('tdee_block.get("tdee")', (ROOT / "rt_dashboard" / "cut_coach.py").read_text(encoding="utf-8"))
        view = bf.bf_phase_for_payload(payload, as_of=as_of, tdee=2318, user_id="card")
        self.assertEqual(view["status"], "flipped")
        self.assertEqual(payload["nutrition_store"]["targets"]["phase"], "slow_bulk")
        self.assertEqual(payload["nutrition_store"]["targets"]["calories"], 2318)
        self.assertEqual(payload["nutrition_store"]["targets"]["carbs_g"], 263)


class CoachCardUi(unittest.TestCase):
    def test_phase_card_shows_median_count_threshold_and_banners(self):
        self.assertIn('id="coach-bf-avg"', HTML)
        self.assertIn('id="coach-bf-threshold"', HTML)
        self.assertIn('id="coach-bf-streak"', HTML)
        self.assertIn('id="coach-bf-median14"', HTML)
        self.assertIn('id="coach-bf-thin"', HTML)
        self.assertIn('id="coach-bf-last"', HTML)
        self.assertIn('id="bf-phase-banner"', HTML)
        self.assertIn('id="bf-phase-banner-coach"', HTML)
        today = HTML[HTML.index('id="today-hub"'): HTML.index('id="today-hub"') + 2500]
        self.assertIn('id="bf-phase-banner"', today)
        self.assertNotIn('id="coach-bf-avg"', today)
        proposal = HTML[HTML.index('id="coach-bf-proposal"'): HTML.index('id="coach-bf-proposal"') + 80]
        self.assertNotIn("<button", proposal)
        self.assertIn("7-day BF median", APP)
        self.assertIn("14-day median", APP)
        self.assertIn("of last 14 counted days", APP)
        self.assertIn("BF data thin", APP)
        self.assertIn("≤10%", APP)
        self.assertIn("≥15%", APP)
        self.assertIn("dismiss_bf_banner", APP)
        self.assertIn("Last flip:", APP)
        self.assertNotIn("Proposed: switch to BULK", APP)
        self.assertNotIn("Proposed: switch to CUT", APP)
        self.assertNotIn("paused: bulk", APP)
        self.assertNotIn("paused: bulk", HTML)


if __name__ == "__main__":
    unittest.main()
