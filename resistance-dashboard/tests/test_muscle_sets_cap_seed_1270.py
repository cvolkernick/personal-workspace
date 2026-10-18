"""Cap seed and catalog credits from Frankenfit's live check on #1193 (#1270)."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from rt_dashboard import muscle_sets as ms
from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.workout_store import load_workspace_catalog, load_workspace_goals

CATALOG, _CSRC = load_workspace_catalog()
GOALS, _GSRC = load_workspace_goals()
ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
DAY = "2026-10-18"  # Sunday of the week that starts Mon 10/12
CUR = datetime(2026, 10, 12)


def _mon(weeks_back):
    return (CUR - timedelta(weeks=weeks_back)).strftime("%Y-%m-%d")


def _ex(name, sets, reps, weight):
    return {"name": name, "sets": [{"weight_lbs": weight, "sets": sets, "reps": reps}]}


def _week(monday, press, pull, lateral):
    """One logged session: press (2.0 credits a set), pull (1.5), lateral (1.0)."""
    lifts = []
    if press:
        lifts.append(_ex("DB Flat Press", press, 8, 50))
    if pull:
        lifts.append(_ex("Pulldowns", pull, 8, 100))
    if lateral:
        lifts.append(_ex("Lateral Raises", lateral, 12, 15))
    return {"date": monday, "session_type": "push", "exercises": lifts} if lifts else None


# Completed weeks, oldest first: a ramp from a near-empty first week, then the
# four weeks of Frankenfit's numbers (10, 11, 12, 11 proper sets = 44, and
# 53.5 credited, a 13.38 four-week credited average).
RAMP = [(8, (0, 1, 1)), (7, (0, 1, 2)), (6, (1, 2, 3)), (5, (2, 3, 4))]  # 2, 3, 6, 9 sets
LAST_FOUR = [(4, (1, 2, 7)), (3, (1, 2, 8)), (2, (1, 3, 8)), (1, (1, 4, 6))]  # 10, 11, 12, 11 sets
THIS_WEEK = {
    "date": "2026-10-13",
    "session_type": "push",
    "exercises": [_ex("DB Flat Press", 2, 8, 50), _ex("Pulldowns", 4, 8, 100),
                  _ex("Lateral Raises", 7, 12, 15)],
}  # 13 proper sets


def _sessions(weeks, current=True):
    out = [s for s in (_week(_mon(i), *mix) for i, mix in weeks) if s]
    return out + ([copy.deepcopy(THIS_WEEK)] if current else [])


def _payload(sessions, day=DAY, **extra):
    base = {
        "coach": {"today": {"date": day}},
        "meta": {"local_today": day},
        "sessions": sessions,
        "workout_store": {"goals": copy.deepcopy(GOALS), "catalog": CATALOG, "plan": {}},
    }
    base.update(extra)
    return base


def _budget(sessions, tonnage="red", day=DAY, **extra):
    with mock.patch.object(ms, "_tonnage_status", return_value=tonnage):
        return ms.build_muscle_set_report(_payload(sessions, day, **extra), as_of=day)["budget"]


class CapSeed(unittest.TestCase):
    def test_frankenfit_numbers_give_a_cap_near_the_average_not_2(self):
        for tonnage in ("red", "green"):
            with self.subTest(tonnage=tonnage):
                b = _budget(_sessions(RAMP + LAST_FOUR), tonnage)
                self.assertEqual(b["cap_raw"], 11)
                self.assertEqual(b["cap"], 11)
                self.assertAlmostEqual(b["credited_avg_4w"], 13.38, places=2)
                self.assertEqual(b["week_proper_sets"], 13)
                self.assertAlmostEqual(b["credit_cap"], 13.4, places=1)
                self.assertEqual(b["tonnage_status"], tonnage)
                self.assertEqual(b["cap_source"], "rolling")

    def test_the_old_walk_pinned_this_history_at_the_first_week(self):
        # Document the trap: round(prev * 1.1) == prev for any cap of 4 or less.
        for prev in (1, 2, 3, 4):
            self.assertEqual(ms._round_half_up(prev * 1.1), prev)
            self.assertEqual(ms._round_half_up(prev * 0.9), prev)

    def test_cap_does_not_depend_on_the_first_logged_week(self):
        with_tiny_start = _budget(_sessions(RAMP + LAST_FOUR), "green")
        without = _budget(_sessions(LAST_FOUR), "green")
        self.assertEqual(with_tiny_start["cap"], without["cap"])

    def test_small_cap_can_move(self):
        counts = {_mon(i): v for i, v in ((9, 3), (8, 3), (7, 3), (6, 3), (5, 3), (4, 12), (3, 12), (2, 12))}
        caps = [ms.resolve_weekly_cap(counts, as_of=_mon(i))["cap"] for i in (4, 3, 2, 1, 0)]
        self.assertEqual(caps[0], 3)
        self.assertGreater(caps[-1], caps[0])
        self.assertEqual(sorted(caps), caps)  # rises week over week, never sticks

    def test_short_history_uses_the_plain_average(self):
        counts = {_mon(2): 2, _mon(1): 12}
        info = ms.resolve_weekly_cap(counts, as_of=_mon(0))
        self.assertEqual(info["cap"], 7)
        self.assertEqual(info["cap_raw"], 7)
        # A freeze has nothing to hold at yet, so it does not pin a seed.
        held = ms.resolve_weekly_cap(counts, as_of=_mon(0), blocked_mondays=[_mon(0)])
        self.assertEqual(held["cap"], 7)

    def test_step_limit_applies_once_a_four_week_base_exists(self):
        counts = {_mon(i): v for i, v in ((6, 40), (5, 40), (4, 40), (3, 40), (2, 40), (1, 100))}
        info = ms.resolve_weekly_cap(counts, as_of=_mon(0))
        self.assertEqual(info["cap_raw"], 55)
        self.assertEqual(info["cap"], 44)  # base 40, +10%
        down = {_mon(i): v for i, v in ((6, 100), (5, 100), (4, 100), (3, 100), (2, 100), (1, 0))}
        self.assertEqual(ms.resolve_weekly_cap(down, as_of=_mon(0))["cap"], 90)

    def test_cap_stays_within_ten_percent_of_its_base_every_week(self):
        series = [5, 6, 6, 7, 7, 30, 31, 2, 2, 2, 40, 40]
        counts = {_mon(len(series) - i): v for i, v in enumerate(series)}
        for back in range(len(series) - 5, -1, -1):
            monday = _mon(back)
            info = ms.resolve_weekly_cap(counts, as_of=monday)
            base = ms._step_base(counts, datetime.strptime(monday, "%Y-%m-%d").date())
            if base is None:
                continue
            with self.subTest(monday=monday):
                self.assertGreaterEqual(info["cap"], round(base * 0.9) - 1)
                self.assertLessEqual(info["cap"], round(base * 1.1) + 1)

    def test_freeze_holds_at_the_recent_average_not_a_seed(self):
        history = _sessions(RAMP + LAST_FOUR)
        green = _budget(history, "green")["cap"]
        red = _budget(history, "red")["cap"]
        self.assertLessEqual(red, green)
        self.assertGreaterEqual(red, 10)  # the average last Monday was 10.5
        counts = {_mon(i): v for i, v in ((6, 40), (5, 40), (4, 40), (3, 40), (2, 40), (1, 100))}
        held = ms.resolve_weekly_cap(counts, as_of=_mon(0), blocked_mondays=[_mon(0)])
        self.assertEqual(held["cap"], 40)
        falling = {_mon(i): v for i, v in ((6, 60), (5, 60), (4, 60), (3, 60), (2, 60), (1, 20))}
        self.assertLess(
            ms.resolve_weekly_cap(falling, as_of=_mon(0), blocked_mondays=[_mon(0)])["cap"], 60
        )

    def test_deload_called_still_holds_the_cap(self):
        payload = _payload(_sessions(RAMP + LAST_FOUR))
        payload["workout_store"]["goals"]["deload"] = True
        with mock.patch.object(ms, "_tonnage_status", return_value="green"):
            report = ms.build_muscle_set_report(payload, as_of=DAY)["budget"]
        self.assertTrue(report["deload"])
        self.assertLessEqual(report["cap"], 11)

    def test_override_still_wins(self):
        payload = _payload(_sessions(RAMP + LAST_FOUR))
        payload["workout_store"]["goals"]["muscle_set_cap_override"] = 48
        with mock.patch.object(ms, "_tonnage_status", return_value="red"):
            b = ms.build_muscle_set_report(payload, as_of=DAY)["budget"]
        self.assertEqual((b["cap"], b["cap_source"], b["cap_override"]), (48, "override", 48))

    def test_feed_carries_the_seeded_cap_and_no_error(self):
        with mock.patch.object(ms, "_tonnage_status", return_value="red"):
            week = export_agent_today(_payload(_sessions(RAMP + LAST_FOUR)))["week"]
        budget = week["muscle_set_budget"]
        self.assertNotIn("error", budget)
        self.assertEqual(budget["cap"], 11)
        self.assertEqual(budget["week_proper_sets"], 13)


class SteadyAndLongGapRegressions(unittest.TestCase):
    def _balanced(self, monday):
        return [
            {"date": monday, "session_type": "push", "exercises": [
                _ex("DB Flat Press", 4, 8, 50), _ex("DB Shoulder Press", 3, 8, 30),
                _ex("Lateral Raises", 4, 12, 15), _ex("Tricep Pushdowns", 3, 12, 40)]},
            {"date": (datetime.strptime(monday, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"),
             "session_type": "pull", "exercises": [
                _ex("Pulldowns", 4, 8, 100), _ex("Seated Cable Row", 4, 8, 90),
                _ex("DB Curls", 3, 12, 25), _ex("Reverse Pec Deck", 3, 12, 40)]},
            {"date": (datetime.strptime(monday, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d"),
             "session_type": "legs", "exercises": [
                _ex("Leg Press", 4, 8, 200), _ex("RDL", 3, 8, 135),
                _ex("Calf Extensions", 4, 12, 80), _ex("Ab Wheel Rollout", 3, 10, 0)]},
            {"date": (datetime.strptime(monday, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d"),
             "session_type": "push", "exercises": [
                _ex("DB Incline Press", 4, 8, 45), _ex("Cable Lateral Raise", 4, 12, 10),
                _ex("Tricep Pushdowns (Rope)", 3, 12, 40)]},
            {"date": (datetime.strptime(monday, "%Y-%m-%d") + timedelta(days=4)).strftime("%Y-%m-%d"),
             "session_type": "pull", "exercises": [
                _ex("Machine Row", 4, 8, 100), _ex("Hammer Curls", 3, 12, 25),
                _ex("Smith Shrugs", 3, 12, 100)]},
        ]

    def _history(self, weeks):
        out = []
        for i in range(weeks, 0, -1):
            out += self._balanced(_mon(i))
        return out

    def test_steady_history_cap_is_the_average_and_no_false_warning(self):
        b = _budget(self._history(4), "green", day="2026-10-16")
        self.assertEqual(b["cap"], 63)
        self.assertEqual(b["credited_avg_4w"], 92.5)
        self.assertFalse(b["overrun"])
        self.assertIsNone(b["warning"])

    def test_steady_history_same_with_a_frozen_week(self):
        self.assertEqual(_budget(self._history(4), "red", day="2026-10-16")["cap"], 63)

    def test_forty_week_history_matches_the_unbounded_walk(self):
        payload = _payload(self._history(40), day="2026-10-16")
        with mock.patch.object(ms, "_tonnage_status", return_value="green"):
            bounded = ms.build_muscle_set_report(payload, as_of="2026-10-16")
            with mock.patch.object(ms, "HISTORY_WEEKS", 10_000):
                unbounded = ms.build_muscle_set_report(payload, as_of="2026-10-16")
        self.assertEqual(bounded, unbounded)
        self.assertEqual(bounded["budget"]["cap"], 63)

    def test_one_old_session_does_not_drag_the_cap(self):
        old = [{"date": "2025-01-06", "session_type": "push",
                "exercises": [_ex("DB Flat Press", 40, 8, 50)]}]
        self.assertEqual(
            _budget(old + self._history(4), "green", day="2026-10-16")["cap"], 63
        )

    def test_a_two_week_gap_inside_the_window_counts_as_zero_weeks(self):
        history = self._history(8)
        history = [s for s in history if not (_mon(3) <= s["date"] < _mon(1))]
        b = _budget(history, "green", day="2026-10-16")
        # Window is 63, 0, 0, 63: the average is 31.5 and last Monday's was
        # 31.5 too, so the step limit holds the cap there, not at a seed.
        self.assertEqual(b["cap_raw"], 32)
        self.assertEqual(b["cap"], 32)


class WarningCoherence(unittest.TestCase):
    def test_targets_far_above_recent_volume_say_so_instead_of_blaming_the_list(self):
        b = _budget(_sessions(RAMP + LAST_FOUR), "red")
        self.assertTrue(b["overrun"])
        self.assertFalse(b["drop_closes_gap"])
        self.assertIn("weekly cap is 11 proper sets", b["warning"])
        self.assertNotIn("cap is 2 ", b["warning"])
        self.assertIn("would not close the gap", b["warning"])
        self.assertTrue(b["drop_suggestion"])  # field kept for consumers
        self.assertIn(ms.GROUP_LABELS[b["drop_suggestion"]], b["warning"])

    def test_a_closeable_overrun_still_says_drop(self):
        text = ms.budget_warning(45, "side_delts", credit_cap=60.0, drop_closes_gap=True)
        self.assertIn("Drop side delts from the 8–10 list.", text)
        self.assertNotIn("would not close", text)
        self.assertEqual(ms.demand_after_priority_drops(), 56)
        self.assertIn("Drop chest", ms.budget_warning(30, "chest"))
        self.assertIn("Drop chest", ms.budget_warning(63, "chest", credit_cap=40.0))

    def test_no_warning_when_the_credit_cap_covers_demand(self):
        self.assertIsNone(ms.budget_warning(63, "chest", credit_cap=91.0, drop_closes_gap=True))
        self.assertIsNone(ms.budget_warning(None, "chest"))

    def test_seed_artifact_cannot_fire_a_warning(self):
        # Old behavior: cap 2 -> credit_cap 2.5 -> "weekly cap is 2 proper sets".
        b = _budget(_sessions(RAMP + LAST_FOUR), "green")
        self.assertGreater(b["cap"], 4)
        self.assertGreater(b["credit_cap"], 10)


class CatalogCredits(unittest.TestCase):
    ROOT_CATALOG = REPO / "fitness" / "exercises" / "catalog.json"
    BUNDLED = ROOT / "fitness" / "exercises" / "catalog.json"

    @staticmethod
    def _row(rows, rid):
        return next(r for r in rows if r["id"] == rid)

    def _rows(self):
        return json.loads(self.BUNDLED.read_text(encoding="utf-8"))["exercises"]

    def test_copies_are_identical_and_all_46_rows_validate(self):
        self.assertEqual(
            self.ROOT_CATALOG.read_text(encoding="utf-8"), self.BUNDLED.read_text(encoding="utf-8")
        )
        rows = self._rows()
        self.assertEqual(len(rows), 46)
        self.assertEqual(len({r["id"] for r in rows}), 46)
        for row in rows:
            primary = row.get("volume_primary")
            secondary = row.get("volume_secondary")
            self.assertIsInstance(primary, list, row["id"])
            self.assertIsInstance(secondary, list, row["id"])
            for group in primary + secondary:
                self.assertIn(group, ms.VOLUME_GROUPS, f"{row['id']} {group}")
            self.assertFalse(set(primary) & set(secondary), row["id"])
            self.assertEqual(len(primary), len(set(primary)), row["id"])
            self.assertEqual(len(secondary), len(set(secondary)), row["id"])
            self.assertNotIn("muscle_credit", row)  # master's field names only

    def test_db_shoulder_press_credits(self):
        row = self._row(self._rows(), "db-shoulder-press")
        self.assertEqual(row["volume_primary"], ["front_delts"])
        self.assertEqual(row["volume_secondary"], ["side_delts", "triceps"])
        p, s = ms.volume_groups(row)
        self.assertEqual(
            ms.credit_proper_sets(p, s, 1),
            {"front_delts": 1.0, "side_delts": 0.5, "triceps": 0.5},
        )

    def test_smith_btn_press_credits(self):
        row = self._row(self._rows(), "smith-btn-press")
        self.assertEqual(row["volume_primary"], ["side_delts"])
        self.assertEqual(row["volume_secondary"], ["front_delts", "triceps", "traps"])
        p, s = ms.volume_groups(row)
        self.assertEqual(
            ms.credit_proper_sets(p, s, 1),
            {"side_delts": 1.0, "front_delts": 0.5, "triceps": 0.5, "traps": 0.5},
        )

    def test_logged_sets_credit_through_the_report(self):
        sessions = [{
            "date": "2026-10-13", "session_type": "push",
            "exercises": [_ex("DB Shoulder Press", 3, 8, 30),
                          _ex("Behind-the-Neck Smith Press", 2, 8, 60)],
        }]
        with mock.patch.object(ms, "_tonnage_status", return_value="green"):
            sets = ms.build_muscle_set_report(_payload(sessions, "2026-10-16"), as_of="2026-10-16")["muscle_sets"]
        self.assertEqual(sets["front_delts"]["proper_sets"], 3 * 1.0 + 2 * 0.5)
        self.assertEqual(sets["side_delts"]["proper_sets"], 3 * 0.5 + 2 * 1.0)
        self.assertEqual(sets["triceps"]["proper_sets"], 3 * 0.5 + 2 * 0.5)
        self.assertEqual(sets["traps"]["proper_sets"], 2 * 0.5)

    def test_only_those_two_rows_differ_from_the_1264_tags(self):
        pinned = {
            "db-shoulder-press": (["front_delts"], ["side_delts", "triceps"]),
            "smith-btn-press": (["side_delts"], ["front_delts", "triceps", "traps"]),
            "db-flat-press": (["chest"], ["triceps", "front_delts"]),
            "lateral-raises": (["side_delts"], []),
            "band-adduction": ([], []),
            "machine-adduction": ([], []),
            "wrist-roller": ([], []),
        }
        rows = self._rows()
        for rid, (p, s) in pinned.items():
            row = self._row(rows, rid)
            self.assertEqual((row["volume_primary"], row["volume_secondary"]), (p, s), rid)


if __name__ == "__main__":
    unittest.main()
