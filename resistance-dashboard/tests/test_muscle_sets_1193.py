"""Weekly proper-set tracker and priority budget (#1193).

One class per acceptance criterion. The feed tests run ``export_agent_today``
over dict sessions (the shape ``load_dashboard_data`` hands over) and over
``Session`` objects, then the HTTP route end to end.
"""

from __future__ import annotations

import copy
import json
import sys
import threading
import unittest
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.request import urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server as fitdash_server  # noqa: E402
from rt_dashboard import agent_today as agent_today_mod  # noqa: E402
from rt_dashboard import muscle_sets as ms  # noqa: E402
from rt_dashboard.agent_today import export_agent_today  # noqa: E402
from rt_dashboard.models import ExerciseEntry, Session, SetEntry  # noqa: E402
from rt_dashboard.workout_planner import (  # noqa: E402
    generate_workout_plan,
    normalize_exercise,
    normalize_goals,
)
from rt_dashboard.workout_store import (  # noqa: E402
    load_workspace_catalog,
    load_workspace_goals,
)

ET = ZoneInfo("America/New_York")
CATALOG, _SRC = load_workspace_catalog()
GOALS, _GSRC = load_workspace_goals()
SUN = "2026-10-18"  # Sunday: last day of the week that starts Mon 10/12
MON = "2026-10-12"
NEXT_MON = "2026-10-19"
THU = "2026-10-15"


def _sets(*triples, **extra):
    return [dict({"weight_lbs": w, "sets": n, "reps": r}, **extra) for (w, n, r) in triples]


def _ex(name, *triples):
    return {"name": name, "sets": _sets(*triples)}


def _session(day, letter, *exercises):
    return {"date": day, "session_type": letter, "exercises": list(exercises)}


def _payload(day, sessions, **extra):
    base = {
        "coach": {"today": {"date": day}},
        "meta": {"local_today": day},
        "sessions": sessions,
        "workout_store": {"goals": copy.deepcopy(GOALS), "catalog": CATALOG, "plan": {}},
    }
    base.update(extra)
    return base


def _credits_for(ex_dict, planned=None, day="2026-10-13"):
    credits, n = ms.session_credits(
        _session(day, "push", ex_dict), CATALOG, planned_loads=planned
    )
    return credits, n


class CatalogTags(unittest.TestCase):
    """Missing muscle tags filled in on every catalog row, both copies."""

    def test_every_row_has_valid_credit_tags_and_copies_match(self):
        root = ROOT.parent / "fitness" / "exercises" / "catalog.json"
        bundled = ROOT / "fitness" / "exercises" / "catalog.json"
        self.assertEqual(root.read_text(encoding="utf-8"), bundled.read_text(encoding="utf-8"))
        rows = json.loads(bundled.read_text(encoding="utf-8"))["exercises"]
        self.assertEqual(len(rows), 46)
        for row in rows:
            credit = row.get("muscle_credit")
            self.assertIsInstance(credit, dict, row["id"])
            for key in ("primary", "secondary"):
                for group in credit[key]:
                    self.assertIn(group, ms.GROUPS, f"{row['id']} {group}")
            self.assertFalse(set(credit["primary"]) & set(credit["secondary"]), row["id"])

    def test_fourteen_groups_named_in_the_issue(self):
        self.assertEqual(
            set(ms.GROUPS),
            {
                "chest", "front_delts", "side_delts", "rear_delts", "lats_upper_back",
                "traps", "biceps", "triceps", "quads", "hamstrings", "glutes",
                "calves", "abs", "lower_back",
            },
        )
        self.assertEqual(len(ms.GROUPS), 14)

    def test_resaving_a_row_keeps_its_credit_tags(self):
        row = next(r for r in CATALOG["exercises"] if r["id"] == "pulldowns")
        self.assertEqual(normalize_exercise(row)["muscle_credit"], row["muscle_credit"])
        self.assertNotIn("muscle_credit", normalize_exercise({"name": "Custom Lift"}))


class ProperSetFiltering(unittest.TestCase):
    """AC: warm-up and below-band sets are excluded."""

    def test_warmup_ramp_sets_below_85_percent_of_working_load_are_dropped(self):
        credits, n = _credits_for(
            _ex("DB Flat Press", (30, 1, 8), (40, 1, 8), (50, 3, 7))
        )
        self.assertEqual(n, 3)
        self.assertEqual(credits["chest"], 3.0)

    def test_set_at_85_percent_of_working_load_counts(self):
        _credits, n = _credits_for(_ex("DB Flat Press", (42.5, 1, 8), (50, 2, 7)))
        self.assertEqual(n, 3)
        _credits, n = _credits_for(_ex("DB Flat Press", (42, 1, 8), (50, 2, 7)))
        self.assertEqual(n, 2)

    def test_planned_load_is_the_reference_for_the_plan_day(self):
        # Whole session logged light against a planned 100: nothing counts.
        planned = {"db-flat-press": 100.0}
        _c, n = _credits_for(_ex("DB Flat Press", (60, 3, 8)), planned=planned)
        self.assertEqual(n, 0)
        _c, n = _credits_for(_ex("DB Flat Press", (85, 3, 8)), planned=planned)
        self.assertEqual(n, 3)
        # Without a plan the session's own top set is the reference.
        _c, n = _credits_for(_ex("DB Flat Press", (60, 3, 8)))
        self.assertEqual(n, 3)

    def test_planned_load_applies_only_to_the_plan_day(self):
        sessions = [
            _session("2026-10-13", "push", _ex("DB Flat Press", (60, 3, 8))),
            _session("2026-10-15", "push", _ex("DB Flat Press", (60, 3, 8))),
        ]
        weeks = ms.weekly_credits(
            sessions, CATALOG, planned_loads={"db-flat-press": 100.0}, planned_day="2026-10-15"
        )
        self.assertEqual(weeks[MON]["chest"], 3.0)  # Tuesday counted, Thursday did not

    def test_explicit_warmup_flag_never_counts(self):
        ex = {
            "name": "DB Flat Press",
            "sets": _sets((50, 2, 8), warmup=True) + _sets((50, 3, 8)),
        }
        _c, n = _credits_for(ex)
        self.assertEqual(n, 3)
        ex["sets"] = _sets((50, 2, 8), set_type="warm_up") + _sets((50, 1, 8))
        _c, n = _credits_for(ex)
        self.assertEqual(n, 1)

    def test_compound_rep_band_5_to_9_with_one_rep_of_slack(self):
        for reps, counts in ((3, False), (4, True), (5, True), (9, True), (10, True), (11, False)):
            with self.subTest(reps=reps):
                _c, n = _credits_for(_ex("DB Flat Press", (50, 3, reps)))
                self.assertEqual(n, 3 if counts else 0)

    def test_isolation_rep_band_8_to_15_with_one_rep_of_slack(self):
        for reps, counts in ((6, False), (7, True), (8, True), (15, True), (16, True), (17, False)):
            with self.subTest(reps=reps):
                _c, n = _credits_for(_ex("Lateral Raises", (15, 2, reps)))
                self.assertEqual(n, 2 if counts else 0)

    def test_status_rows_and_unknown_lifts_never_count(self):
        row = next(r for r in CATALOG["exercises"] if r["id"] == "leg-press")
        status = _ex("Already trained today (LEGS). Next session: PUSH.", (100, 3, 8))
        self.assertEqual(ms.proper_sets_for_exercise(status, row), 0)
        credits, n = ms.session_credits(
            _session("2026-10-13", "legs", status, _ex("Mystery Machine", (100, 3, 8))),
            CATALOG,
        )
        self.assertEqual((credits, n), ({}, 0))

    def test_assisted_lifts_count_by_reps(self):
        # No weigh-in: effective load is absent, so reps decide.
        _c, n = _credits_for(_ex("Assisted Pullups", (75, 2, 6)))
        self.assertEqual(n, 2)
        _c, n = _credits_for(_ex("Assisted Pullups", (75, 2, 2)))
        self.assertEqual(n, 0)

    def test_assisted_load_gate_uses_effective_load(self):
        ex = {
            "name": "Assisted Pullups",
            "sets": [
                {"weight_lbs": 110, "sets": 1, "reps": 6, "effective_load_lbs": 63.0,
                 "effective_load_source": "nearest_weighin"},
                {"weight_lbs": 75, "sets": 2, "reps": 6, "effective_load_lbs": 98.0,
                 "effective_load_source": "nearest_weighin"},
            ],
        }
        _c, n = _credits_for(ex)
        self.assertEqual(n, 2)  # 63 < 0.85 * 98, so the heavy-assist set is a ramp


class CreditMath(unittest.TestCase):
    """AC: primary 1.0, secondary 0.5 per proper set."""

    def test_primary_one_secondary_half(self):
        credits, n = _credits_for(_ex("DB Flat Press", (50, 3, 8)))
        self.assertEqual(n, 3)
        self.assertEqual(credits, {"chest": 3.0, "triceps": 1.5, "front_delts": 1.5})

    def test_two_lifts_add_per_group(self):
        credits, _n = ms.session_credits(
            _session(
                "2026-10-14", "pull",
                _ex("Pulldowns", (100, 3, 8)),
                _ex("Seated Cable Row", (90, 2, 8)),
            ),
            CATALOG,
        )
        self.assertEqual(credits["lats_upper_back"], 5.0)
        self.assertEqual(credits["biceps"], 2.5)
        self.assertEqual(credits["rear_delts"], 1.0)

    def test_primary_beats_secondary_when_both_list_a_group(self):
        row = {"muscle_credit": {"primary": ["chest"], "secondary": ["chest", "triceps"]}}
        self.assertEqual(ms.catalog_credit(row), {"chest": 1.0, "triceps": 0.5})

    def test_two_primaries_each_get_full_credit(self):
        credits, _n = _credits_for(_ex("RDL", (135, 3, 8)))
        self.assertEqual(credits["hamstrings"], 3.0)
        self.assertEqual(credits["glutes"], 3.0)
        self.assertEqual(credits["lower_back"], 1.5)


class WeekBoundaries(unittest.TestCase):
    """AC: Monday to Sunday ET."""

    def test_sunday_closes_the_week_and_monday_opens_the_next(self):
        self.assertEqual(ms.week_bounds(SUN), (MON, SUN))
        self.assertEqual(ms.week_bounds(MON), (MON, SUN))
        self.assertEqual(ms.week_bounds(NEXT_MON), (NEXT_MON, "2026-10-25"))
        self.assertEqual(ms.week_bounds("2026-10-11"), ("2026-10-05", "2026-10-11"))

    def test_sessions_land_in_the_right_week(self):
        sessions = [
            _session("2026-10-11", "legs", _ex("Leg Press", (200, 3, 8))),  # prior Sunday
            _session(MON, "push", _ex("DB Flat Press", (50, 3, 8))),
            _session(SUN, "pull", _ex("Pulldowns", (100, 3, 8))),
            _session(NEXT_MON, "push", _ex("DB Flat Press", (50, 3, 8))),
        ]
        sunday = ms.build_muscle_sets(_payload(SUN, sessions), SUN)
        self.assertEqual(sunday["muscle_sets_meta"]["week_start"], MON)
        self.assertEqual(sunday["muscle_sets_meta"]["week_end"], SUN)
        self.assertEqual(sunday["muscle_sets"]["chest"]["proper_sets"], 3.0)
        self.assertEqual(sunday["muscle_sets"]["lats_upper_back"]["proper_sets"], 3.0)
        self.assertEqual(sunday["muscle_sets"]["quads"]["proper_sets"], 0.0)  # Sunday 10/11 is last week
        monday = ms.build_muscle_sets(_payload(NEXT_MON, sessions), NEXT_MON)
        self.assertEqual(monday["muscle_sets_meta"]["week_start"], NEXT_MON)
        self.assertEqual(monday["muscle_sets"]["chest"]["proper_sets"], 3.0)
        self.assertEqual(monday["muscle_sets"]["lats_upper_back"]["proper_sets"], 0.0)

    def test_feed_week_is_sunday_through_monday_reset(self):
        sessions = [_session(SUN, "pull", _ex("Pulldowns", (100, 3, 8)))]
        sun = export_agent_today(_payload(SUN, sessions))["week"]
        mon = export_agent_today(_payload(NEXT_MON, sessions))["week"]
        self.assertEqual(sun["muscle_sets"]["lats_upper_back"]["proper_sets"], 3.0)
        self.assertEqual(mon["muscle_sets"]["lats_upper_back"]["proper_sets"], 0.0)
        self.assertEqual(mon["muscle_sets"]["lats_upper_back"]["trend_4w"]["weeks"][-1]["proper_sets"], 3.0)


class Tiering(unittest.TestCase):
    """AC: 0-3 red, 4-7 green, 8+ blue."""

    def test_tier_boundaries(self):
        for value, tier in (
            (0, "red"), (3, "red"), (3.5, "red"), (3.99, "red"),
            (4, "green"), (5.5, "green"), (7, "green"), (7.5, "green"),
            (8, "blue"), (10, "blue"), (14, "blue"),
        ):
            with self.subTest(value=value):
                self.assertEqual(ms.tier_for(value), tier)

    def test_tier_shows_up_in_the_feed(self):
        sessions = [
            _session("2026-10-13", "push", _ex("DB Flat Press", (50, 4, 8))),
            _session("2026-10-14", "push", _ex("Smith Flat Bench", (100, 4, 8))),
        ]
        out = ms.build_muscle_sets(_payload(SUN, sessions), SUN)["muscle_sets"]
        self.assertEqual(out["chest"]["proper_sets"], 8.0)
        self.assertEqual(out["chest"]["tier"], "blue")
        self.assertEqual(out["triceps"]["proper_sets"], 4.0)
        self.assertEqual(out["triceps"]["tier"], "green")
        self.assertEqual(out["quads"]["tier"], "red")


class CapAndBudget(unittest.TestCase):
    """AC: cap rule, targets, and the cap-overrun warning."""

    @staticmethod
    def _weeks(totals):
        # {monday: {"chest": total}} from (monday, total) pairs.
        return {monday: {"chest": float(total)} for monday, total in totals}

    FLAT = [
        ("2026-08-31", 40), ("2026-09-07", 40), ("2026-09-14", 40),
        ("2026-09-21", 40), ("2026-09-28", 40),
    ]

    def test_coach_defaults(self):
        cfg = ms.resolve_config({})
        self.assertEqual(cfg["priority_groups"], ["side_delts", "lats_upper_back", "chest"])
        self.assertEqual(cfg["middle_groups"], ["hamstrings", "rear_delts", "biceps", "triceps"])
        self.assertEqual(cfg["priority_target"], (8, 10))
        self.assertEqual(cfg["middle_target"], (4, 7))
        self.assertEqual(cfg["maintenance_floor"], 4)
        self.assertEqual(
            set(cfg["deprioritized_groups"]),
            {"front_delts", "traps", "quads", "glutes", "calves", "abs", "lower_back"},
        )
        self.assertIsNone(cfg["cap_override"])
        self.assertAlmostEqual(cfg["max_weekly_change"], 0.10)
        targets = ms.group_targets(cfg)
        self.assertEqual(targets["side_delts"], {"role": "priority", "low": 8, "high": 10})
        self.assertEqual(targets["hamstrings"], {"role": "middle", "low": 4, "high": 7})
        self.assertEqual(targets["quads"], {"role": "deprioritized", "low": 4, "high": 4})

    def test_goals_json_matches_the_coach_defaults_and_survives_normalize(self):
        stored = GOALS["set_budget"]
        self.assertEqual(stored["cap_override"], None)
        for key in ("priority_groups", "middle_groups", "priority_target", "middle_target", "maintenance_floor"):
            self.assertEqual(stored[key], ms.DEFAULT_SET_BUDGET[key], key)
        self.assertEqual(normalize_goals(GOALS)["set_budget"], stored)

    def test_cap_is_rounded_4_week_average(self):
        weeks = self._weeks(self.FLAT)
        weeks["2026-09-28"] = {"chest": 41.0}
        weeks["2026-09-21"] = {"chest": 40.0}
        weeks["2026-09-14"] = {"chest": 40.0}
        weeks["2026-09-07"] = {"chest": 40.0}
        cfg = ms.resolve_config({})
        info = ms.weekly_cap(weeks, "2026-10-05", cfg, first_week="2026-08-31", hold_up=False)
        self.assertEqual(info["cap"], 40)  # avg 40.25 rounds to 40
        self.assertEqual(info["source"], "rolling_4w_avg")

    def test_cap_moves_at_most_ten_percent_a_week(self):
        weeks = self._weeks(self.FLAT + [("2026-10-05", 100)])
        cfg = ms.resolve_config({})
        info = ms.weekly_cap(weeks, "2026-10-12", cfg, first_week="2026-08-31", hold_up=False)
        # Raw 4-week average is 55; last week's cap was 40; 40 * 1.10 = 44.
        self.assertEqual(info["cap"], 44)
        down = self._weeks(
            [("2026-08-31", 100), ("2026-09-07", 100), ("2026-09-14", 100),
             ("2026-09-21", 100), ("2026-09-28", 100), ("2026-10-05", 0)]
        )
        info = ms.weekly_cap(down, "2026-10-12", cfg, first_week="2026-08-31", hold_up=False)
        self.assertEqual(info["cap"], 90)  # raw 75; floor is 100 * 0.90

    def test_cap_walk_never_jumps_more_than_ten_percent_between_mondays(self):
        weeks = self._weeks(self.FLAT + [("2026-10-05", 100), ("2026-10-12", 5)])
        cfg = ms.resolve_config({})
        caps = [
            ms.weekly_cap(weeks, m, cfg, first_week="2026-08-31", hold_up=False)["cap"]
            for m in ("2026-10-05", "2026-10-12", "2026-10-19")
        ]
        for a, b in zip(caps, caps[1:]):
            self.assertLessEqual(abs(b - a), a * 0.10 + 1e-9, caps)

    def test_cap_never_goes_up_while_held(self):
        weeks = self._weeks(self.FLAT + [("2026-10-05", 100)])
        cfg = ms.resolve_config({})
        held = ms.weekly_cap(weeks, "2026-10-12", cfg, first_week="2026-08-31", hold_up=True)
        self.assertEqual(held["cap"], 40)
        self.assertTrue(held["held"])
        # A hold does not force it down.
        falling = self._weeks(
            [(m, 60) for m, _ in self.FLAT] + [("2026-10-05", 20)]
        )
        info = ms.weekly_cap(falling, "2026-10-12", cfg, first_week="2026-08-31", hold_up=True)
        self.assertLess(info["cap"], 60)
        self.assertFalse(info["held"])

    def _history_payload(self, day, **extra):
        sessions = []
        # Four completed weeks of 12 proper chest sets each (3 sessions x 4 sets):
        # chest 12 + triceps 6 + front delts 6 = 24 credited proper sets a week.
        for monday in ("2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05"):
            for offset in (0, 2, 4):
                d = datetime.strptime(monday, "%Y-%m-%d") + timedelta(days=offset)
                sessions.append(
                    _session(d.strftime("%Y-%m-%d"), "push", _ex("DB Flat Press", (50, 4, 8)))
                )
        return _payload(day, sessions, **extra)

    def test_hold_while_tonnage_is_red(self):
        for hold_status, held in (("green", False), ("red", True)):
            with self.subTest(status=hold_status):
                payload = self._history_payload(
                    SUN, guardrails={"tiles": [{"id": "tonnage", "status": hold_status}]}
                )
                # Make this week's raw cap want to rise: a big prior week.
                payload["sessions"].append(
                    _session("2026-10-06", "push", _ex("Smith Flat Bench", (100, 40, 8)))
                )
                out = ms.build_muscle_sets(payload, SUN)["muscle_budget"]
                self.assertEqual(out["cap_held"], held)
                if held:
                    self.assertEqual(out["cap_hold_reason"], "tonnage_red")

    def test_hold_when_a_deload_is_called(self):
        payload = self._history_payload(SUN)
        payload["sessions"].append(
            _session("2026-10-06", "push", _ex("Smith Flat Bench", (100, 40, 8)))
        )
        payload["workout_store"]["goals"]["deload"] = True
        out = ms.build_muscle_sets(payload, SUN)["muscle_budget"]
        self.assertTrue(out["cap_held"])
        self.assertEqual(out["cap_hold_reason"], "deload")

    def test_manual_override_is_empty_by_default_and_wins_when_set(self):
        payload = self._history_payload(SUN)
        out = ms.build_muscle_sets(payload, SUN)["muscle_budget"]
        self.assertIsNone(out["cap_override"])
        self.assertEqual(out["cap_source"], "rolling_4w_avg")
        payload["workout_store"]["goals"]["set_budget"] = {"cap_override": 90}
        out = ms.build_muscle_sets(payload, SUN)["muscle_budget"]
        self.assertEqual(out["cap"], 90)
        self.assertEqual(out["cap_source"], "manual_override")

    def test_no_history_means_no_cap_and_no_invented_volume(self):
        out = ms.build_muscle_sets(_payload(SUN, []), SUN)["muscle_budget"]
        self.assertIsNone(out["cap"])
        self.assertEqual(out["cap_source"], "none")
        self.assertIsNone(out["warning"])

    def test_overrun_warns_and_names_the_group_to_drop(self):
        cfg = ms.resolve_config({})
        targets = ms.group_targets(cfg)
        avg = {"side_delts": 2.0, "lats_upper_back": 6.0, "chest": 7.0}
        plan = ms.budget_plan(cfg, targets, 60, avg)
        self.assertEqual(plan["planned_total"], 68.0)
        self.assertEqual(plan["overrun"], 8.0)
        warning = plan["warning"]
        self.assertEqual(warning["code"], "targets_exceed_cap")
        self.assertEqual(warning["suggest_drop"], "side_delts")  # furthest short of 8
        self.assertEqual(warning["drop_order"][:2], ["side_delts", "lats_upper_back"])
        self.assertTrue(warning["fits_after_drops"])
        self.assertIn("Side delts", warning["message"])
        self.assertIn("cap is 60", warning["message"])

    def test_no_warning_when_targets_fit_and_an_unfixable_overrun_still_warns(self):
        cfg = ms.resolve_config({})
        targets = ms.group_targets(cfg)
        self.assertIsNone(ms.budget_plan(cfg, targets, 68, {})["warning"])
        self.assertIsNone(ms.budget_plan(cfg, targets, 90, {})["warning"])
        tight = ms.budget_plan(cfg, targets, 30, {})
        self.assertIsNotNone(tight["warning"])  # never silently overrun
        self.assertFalse(tight["warning"]["fits_after_drops"])

    def test_overrun_warning_reaches_the_feed(self):
        payload = self._history_payload(SUN)  # cap 24 vs 68 needed
        week = export_agent_today(payload)["week"]
        budget = week["muscle_budget"]
        self.assertEqual(budget["cap"], 24)
        self.assertEqual(budget["planned_total"], 68.0)
        self.assertEqual(budget["warning"]["code"], "targets_exceed_cap")
        self.assertIsNotNone(budget["warning"]["suggest_drop"])

    def test_priority_groups_may_stretch_past_10_only_with_green_tonnage_and_room(self):
        payload = self._history_payload(
            SUN, guardrails={"tiles": [{"id": "tonnage", "status": "green"}]}
        )
        payload["workout_store"]["goals"]["set_budget"] = {"cap_override": 200}
        out = ms.build_muscle_sets(payload, SUN)
        self.assertTrue(out["muscle_budget"]["stretch_ok"])
        self.assertTrue(out["muscle_sets"]["chest"]["target"]["stretch"])
        self.assertNotIn("stretch", out["muscle_sets"]["biceps"]["target"])
        payload["guardrails"] = {"tiles": [{"id": "tonnage", "status": "yellow"}]}
        out = ms.build_muscle_sets(payload, SUN)
        self.assertFalse(out["muscle_budget"]["stretch_ok"])
        payload["guardrails"] = {"tiles": [{"id": "tonnage", "status": "green"}]}
        payload["workout_store"]["goals"]["set_budget"] = {"cap_override": 40}
        self.assertFalse(ms.build_muscle_sets(payload, SUN)["muscle_budget"]["stretch_ok"])


class FeedShape(unittest.TestCase):
    """AC: the live feed shows ``muscle_sets`` and no existing field changes."""

    def _sessions(self):
        return [
            _session("2026-10-13", "push", _ex("DB Flat Press", (50, 3, 8)), _ex("Lateral Raises", (15, 3, 12))),
            _session("2026-10-15", "pull", _ex("Pulldowns", (100, 3, 8))),
        ]

    def test_week_muscle_sets_shape(self):
        week = export_agent_today(_payload("2026-10-16", self._sessions()))["week"]
        self.assertEqual(list(week["muscle_sets"]), list(ms.GROUPS))
        chest = week["muscle_sets"]["chest"]
        self.assertEqual(chest["proper_sets"], 3.0)
        self.assertEqual(chest["tier"], "red")
        self.assertEqual(chest["target"], {"role": "priority", "low": 8, "high": 10})
        self.assertEqual(set(chest["trend_4w"]), {"weeks", "avg", "direction"})
        self.assertEqual(week["muscle_sets"]["side_delts"]["proper_sets"], 3.0)
        self.assertEqual(week["muscle_sets_meta"]["week_start"], MON)
        self.assertEqual(week["muscle_sets_meta"]["proper_set_total"], 13.5)

    def test_dict_and_object_sessions_give_the_same_answer(self):
        dicts = self._sessions()
        objects = [
            Session(
                date=s["date"],
                session_type=s["session_type"],
                exercises=[
                    ExerciseEntry(
                        name=e["name"],
                        sets=[SetEntry(weight_lbs=t["weight_lbs"], sets=t["sets"], reps=t["reps"]) for t in e["sets"]],
                    )
                    for e in s["exercises"]
                ],
            )
            for s in dicts
        ]
        as_dicts = [o.to_dict() for o in objects]
        a = export_agent_today(_payload("2026-10-16", dicts))["week"]["muscle_sets"]
        b = export_agent_today(_payload("2026-10-16", objects))["week"]["muscle_sets"]
        c = export_agent_today(_payload("2026-10-16", as_dicts))["week"]["muscle_sets"]
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_no_existing_feed_field_changes(self):
        payload = _payload("2026-10-16", self._sessions())
        with mock.patch.object(agent_today_mod, "_attach_muscle_sets", lambda *a, **k: None):
            before = export_agent_today(copy.deepcopy(payload))
        after = export_agent_today(copy.deepcopy(payload))
        added = {"muscle_sets", "muscle_sets_meta", "muscle_budget", "muscle_coach"}
        self.assertEqual(set(after["week"]) - set(before["week"]), added)
        for key in added:
            after["week"].pop(key)
        self.assertEqual(before, after)

    def test_empty_week_keeps_honest_empties(self):
        out = export_agent_today({})
        self.assertEqual(out["week"]["muscle_sets"], {})
        self.assertIsNone(out["week"]["muscle_budget"])

    def test_a_builder_failure_does_not_break_the_feed(self):
        payload = _payload("2026-10-16", self._sessions())
        with mock.patch.object(ms, "build_muscle_sets", side_effect=RuntimeError("boom")):
            out = export_agent_today(payload)
        self.assertTrue(out["ok"])
        self.assertIn("boom", out["week"]["muscle_sets_meta"]["error"])

    def test_dashboard_payload_key(self):
        payload = _payload("2026-10-16", self._sessions())
        ms.attach_muscle_sets(payload, as_of="2026-10-16")
        self.assertEqual(payload["muscle_sets_week"]["muscle_sets"]["chest"]["proper_sets"], 3.0)


class FeedRoute(unittest.TestCase):
    """HTTP ``GET /api/agent/today`` with dict sessions returns ``week.muscle_sets``."""

    DAY = "2026-10-16"

    def setUp(self):
        sessions = [
            _session("2026-10-13", "push", _ex("DB Flat Press", (50, 3, 8))),
        ]
        payload = _payload(self.DAY, sessions, recovery={"score": 80, "sparse": True})
        now = datetime(2026, 10, 16, 9, 0, tzinfo=ET)
        self._patches = [
            mock.patch.object(fitdash_server, "load_dashboard_data", return_value=payload),
            mock.patch.object(fitdash_server, "_auth_required", return_value=False),
            mock.patch("rt_dashboard.timeutil.local_now", return_value=now),
            mock.patch("rt_dashboard.timeutil.local_today_iso", return_value=self.DAY),
            mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False),
            mock.patch(
                "rt_dashboard.gym_calendar.sync_gym_from_workout",
                return_value={"ok": True, "skipped": True},
            ),
            mock.patch(
                "rt_dashboard.google_health.GoogleHealthClient.credentials_present",
                return_value=False,
            ),
        ]
        for p in self._patches:
            p.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), fitdash_server.DashboardHandler)
        self.port = int(self.httpd.server_address[1])
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for p in reversed(self._patches):
            p.stop()

    def test_route_returns_muscle_sets(self):
        with urlopen(f"http://127.0.0.1:{self.port}/api/agent/today", timeout=20) as resp:
            status = int(resp.status)
            body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"), body)
        sets = body["week"]["muscle_sets"]
        self.assertEqual(sets["chest"]["proper_sets"], 3.0)
        self.assertEqual(sets["chest"]["tier"], "red")
        self.assertEqual(body["week"]["muscle_sets_meta"]["week_start"], MON)
        # Existing fields are still there.
        for key in ("nutrition", "logged_sessions", "sleep", "start", "end"):
            self.assertIn(key, body["week"])


class ThursdayCoach(unittest.TestCase):
    """AC: Thursday flags priority groups on pace under 8 and groups trending under 4."""

    def _payload(self, day, chest_sets=3):
        sessions = [
            _session("2026-10-12", "push", _ex("DB Flat Press", (50, chest_sets, 8))),
        ]
        return _payload(day, sessions)

    def test_flags_only_on_thursday(self):
        for day, active in (("2026-10-14", False), (THU, True), ("2026-10-16", False), (SUN, False)):
            with self.subTest(day=day):
                coach = ms.build_muscle_sets(self._payload(day), day)["muscle_coach"]
                self.assertEqual(coach["active"], active)
                if not active:
                    self.assertEqual(coach["flags"], [])
        self.assertEqual(
            ms.build_muscle_sets(self._payload(THU), THU)["muscle_coach"]["weekday"], "Thursday"
        )

    def test_priority_group_on_pace_below_8_is_flagged_with_options(self):
        coach = ms.build_muscle_sets(self._payload(THU, chest_sets=3), THU)["muscle_coach"]
        chest = [f for f in coach["flags"] if f["kind"] == "priority_below_pace" and f["group"] == "chest"]
        self.assertEqual(len(chest), 1)
        self.assertEqual(chest[0]["pace"], 5.2)  # 3 sets through Thursday, 3 * 7 / 4
        self.assertIn("DB Flat Press", chest[0]["suggested_lifts"])
        self.assertTrue(coach["suggestions_only"])

    def test_priority_group_on_pace_for_8_is_not_flagged(self):
        coach = ms.build_muscle_sets(self._payload(THU, chest_sets=5), THU)["muscle_coach"]
        self.assertFalse(
            [f for f in coach["flags"] if f["kind"] == "priority_below_pace" and f["group"] == "chest"]
        )

    def test_group_trending_under_4_is_flagged(self):
        sessions = []
        for monday in ("2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05"):
            sessions.append(
                _session(
                    monday, "push",
                    _ex("Lateral Raises", (15, 2, 12)),
                    _ex("DB Flat Press", (50, 4, 8)),
                )
            )
        coach = ms.build_muscle_sets(_payload(THU, sessions), THU)["muscle_coach"]
        side = [f for f in coach["flags"] if f["kind"] == "trending_under_4" and f["group"] == "side_delts"]
        self.assertEqual(len(side), 1)
        self.assertEqual(side[0]["avg_4w"], 2.0)
        # A group at 4 or more is not flagged as trending under.
        self.assertFalse([f for f in coach["flags"] if f["kind"] == "trending_under_4" and f["group"] == "chest"])

    def test_suggestions_only_the_plan_is_untouched(self):
        payload = _payload(THU, self._payload(THU)["sessions"])
        with mock.patch.object(agent_today_mod, "_attach_muscle_sets", lambda *a, **k: None):
            before = export_agent_today(copy.deepcopy(payload))
        after = export_agent_today(copy.deepcopy(payload))
        self.assertTrue(after["week"]["muscle_coach"]["active"])
        self.assertEqual(after["today"], before["today"])
        self.assertEqual(after["main_lifts"], before["main_lifts"])


class PlannerTiebreak(unittest.TestCase):
    """The planner uses the targets as a tiebreak only; main lifts do not move."""

    def test_selection_need_prefers_the_short_priority_group(self):
        budget = ms.planner_budget([], CATALOG, GOALS, SUN)
        by_id = {r["id"]: r for r in CATALOG["exercises"]}
        lateral = ms.selection_need(by_id["lateral-raises"], budget)
        curls = ms.selection_need(by_id["db-curls"], budget)
        shrugs = ms.selection_need(by_id["smith-shrugs"], budget)
        self.assertGreater(lateral, curls)  # side delts priority 8 vs biceps middle 4
        self.assertGreater(curls, shrugs)  # biceps middle vs traps floor
        self.assertEqual(ms.selection_need(by_id["lateral-raises"], None), 0.0)

    def test_selection_need_falls_as_the_group_fills(self):
        by_id = {r["id"]: r for r in CATALOG["exercises"]}
        empty = ms.planner_budget([], CATALOG, GOALS, SUN)
        full = copy.deepcopy(empty)
        full["done"]["side_delts"] = 9.0
        self.assertGreater(
            ms.selection_need(by_id["lateral-raises"], empty),
            ms.selection_need(by_id["lateral-raises"], full),
        )
        self.assertEqual(ms.selection_need(by_id["lateral-raises"], full), 0.0)

    def test_priority_group_gets_trimmed_sets_back_never_past_the_prior_count(self):
        row = next(r for r in CATALOG["exercises"] if r["id"] == "lateral-raises")
        budget = ms.planner_budget([], CATALOG, GOALS, SUN)
        sets, note = ms.adjust_accessory_sets(1, row, budget, {}, room=6, prior_sets=3)
        self.assertEqual(sets, 3)
        self.assertIn("Side delts priority", note)
        sets, note = ms.adjust_accessory_sets(1, row, budget, {}, room=2, prior_sets=3)
        self.assertEqual(sets, 2)  # session room
        # A lift planned at its prior count is left alone: no increase past it.
        self.assertEqual(ms.adjust_accessory_sets(3, row, budget, {}, room=6, prior_sets=3), (3, None))
        self.assertEqual(ms.adjust_accessory_sets(2, row, budget, {}, room=6, prior_sets=2), (2, None))

    def test_floor_group_already_met_gives_up_a_set(self):
        row = next(r for r in CATALOG["exercises"] if r["id"] == "smith-shrugs")
        budget = ms.planner_budget([], CATALOG, GOALS, SUN)
        budget["done"]["traps"] = 4.0
        sets, note = ms.adjust_accessory_sets(2, row, budget, {}, room=6, prior_sets=2)
        self.assertEqual(sets, 1)
        self.assertIn("holds its floor", note)
        self.assertEqual(ms.adjust_accessory_sets(1, row, budget, {}, room=6, prior_sets=2), (1, None))
        budget["done"]["traps"] = 3.0
        self.assertEqual(ms.adjust_accessory_sets(2, row, budget, {}, room=6, prior_sets=2), (2, None))

    def _history(self):
        return [
            Session(
                date="2026-10-13",
                session_type="legs",
                exercises=[
                    ExerciseEntry(name="Leg Press", sets=[SetEntry(200, 2, 8)]),
                    ExerciseEntry(name="RDL", sets=[SetEntry(135, 2, 8)]),
                    ExerciseEntry(name="Calf Extensions", sets=[SetEntry(80, 4, 12)]),
                ],
            ),
            Session(
                date="2026-10-14",
                session_type="push",
                exercises=[
                    ExerciseEntry(name="DB Flat Press", sets=[SetEntry(50, 2, 8)]),
                ],
            ),
            Session(
                date="2026-10-15",
                session_type="pull",
                exercises=[
                    ExerciseEntry(name="Pulldowns", sets=[SetEntry(100, 2, 8)]),
                ],
            ),
        ]

    def _plan(self, **kw):
        return generate_workout_plan(
            CATALOG,
            GOALS,
            self._history(),
            recovery_score=80,
            session_type="legs",
            as_of="2026-10-16",
            **kw,
        )

    def test_main_lifts_are_identical_with_and_without_the_budget(self):
        main = {n.lower() for n in GOALS["main_lifts"]}
        off = self._plan(set_budget=None)
        auto = self._plan()
        explicit = self._plan(set_budget=ms.planner_budget(self._history(), CATALOG, GOALS, "2026-10-16"))

        def mains(plan):
            return {
                r["name"]: (r["prescription"]["weight_lbs"], r["prescription"]["sets"], r["prescription"]["reps"])
                for r in plan["exercises"]
                if r["name"].lower() in main
            }

        self.assertTrue(mains(off))
        self.assertEqual(mains(off), mains(auto))
        self.assertEqual(mains(off), mains(explicit))

    def _push_history(self):
        return [
            Session(
                date="2026-10-12",
                session_type="push",
                exercises=[
                    ExerciseEntry(name="DB Shoulder Press", sets=[SetEntry(30, 4, 10)]),
                    ExerciseEntry(name="Lateral Raises", sets=[SetEntry(15, 3, 12)]),
                    ExerciseEntry(name="Smith Shrugs", sets=[SetEntry(100, 4, 12)]),
                    ExerciseEntry(name="DB Flat Press", sets=[SetEntry(50, 2, 8)]),
                ],
            )
        ]

    def test_short_priority_group_gets_trimmed_sets_back_on_an_accessory(self):
        kw = dict(recovery_score=80, session_type="push", as_of="2026-10-14")
        off = generate_workout_plan(CATALOG, GOALS, self._push_history(), set_budget=None, **kw)
        on = generate_workout_plan(CATALOG, GOALS, self._push_history(), **kw)
        main = {n.lower() for n in GOALS["main_lifts"]}
        self.assertEqual(
            [r["name"] for r in off["exercises"]], [r["name"] for r in on["exercises"]]
        )
        noted = [r for r in on["exercises"] if r.get("set_budget_note")]
        self.assertTrue(noted)
        by_name_off = {r["name"]: r["prescription"]["sets"] for r in off["exercises"]}
        for row in on["exercises"]:
            self.assertNotIn(row["name"].lower(), main)
            sets_off = by_name_off[row["name"]]
            if row.get("set_budget_note"):
                self.assertGreater(row["prescription"]["sets"], sets_off)
                self.assertLessEqual(row["prescription"]["sets"], 2)  # never past the default
                self.assertIn("Chest priority", row["set_budget_note"])
                self.assertIn("Set budget", row["rationale"])
            else:
                self.assertEqual(row["prescription"]["sets"], sets_off)

    def test_budget_failure_falls_back_to_the_plain_plan(self):
        off = self._plan(set_budget=None)
        with mock.patch.object(ms, "planner_budget", side_effect=RuntimeError("boom")):
            auto = self._plan()
        self.assertEqual(
            [r["name"] for r in off["exercises"]], [r["name"] for r in auto["exercises"]]
        )


class UiWiring(unittest.TestCase):
    """The tile is in the page, the cache and the render pass."""

    def test_card_script_and_cache_entries(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="muscle-sets-card"', html)
        self.assertIn('id="muscle-sets-body"', html)
        self.assertIn("/muscle-sets.js?v=muscle-sets-1193-1", html)
        self.assertLess(html.find("/muscle-sets.js"), html.find("/app.js?v="))
        self.assertIn("/muscle-sets.js?v=muscle-sets-1193-1", sw)
        self.assertIn('const CACHE = "fitdash-shell-v139"', sw)
        self.assertIn("renderMuscleSets(data);", app)
        self.assertIn("muscle_sets_week", app)


if __name__ == "__main__":
    unittest.main()
