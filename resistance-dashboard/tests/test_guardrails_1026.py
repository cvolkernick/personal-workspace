"""#1026 Trends guardrails: phase bands, deload, aliases, and the agent phase field."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rt_dashboard.agent_today import export_agent_today  # noqa: E402
from rt_dashboard.guardrails import build_guardrails  # noqa: E402
from rt_dashboard.phase_barometer import GUARDRAIL_THRESHOLDS  # noqa: E402
from rt_dashboard.trends_export import export_trends_window  # noqa: E402

AS_OF = "2026-10-03"
WEEK = {
    0: "2026-09-30",
    1: "2026-09-23",
    2: "2026-09-16",
    3: "2026-09-09",
}
GOALS = {
    "main_lifts": [
        "RDL",
        "Leg Press",
        "DB Incline Press",
        "DB Flat Press",
        "Seated Cable Row",
        "Pulldowns",
    ],
    "lift_aliases": {
        "Romanian Deadlift": "RDL",
        "RDLs": "RDL",
        "Lat Pulldown": "Pulldowns",
    },
}


def _sess(day, volume, exercises=None, notes="", session_type="push"):
    return {
        "date": day,
        "session_type": session_type,
        "volume": volume,
        "notes": notes,
        "exercises": exercises or [],
    }


def _lift(name, weight, reps):
    return {"name": name, "sets": [{"weight_lbs": weight, "sets": 1, "reps": reps}]}


def _payload(sessions, weights=None, phase="cut", plan=None, goals=None):
    store = {"goals": GOALS if goals is None else goals}
    if plan:
        store["plan"] = plan
    return {
        "sessions": sessions,
        "health": {
            "weight": [
                {"date": day, "weight_lbs": lbs} for day, lbs in (weights or [])
            ]
        },
        "phase_barometer": {"phase": phase},
        "workout_store": store,
        "meta": {"local_today": AS_OF},
    }


def _tile(body, tile_id):
    for tile in body["tiles"]:
        if tile["id"] == tile_id:
            return tile
    raise AssertionError(tile_id)


def _line(tile, role):
    for line in tile["lines"]:
        if line["role"] == role:
            return line
    raise AssertionError(role)


def _weeks(volumes):
    return [_sess(WEEK[idx], volumes[idx]) for idx in sorted(volumes)]


class GuardrailThresholds(unittest.TestCase):
    def test_thresholds_live_next_to_the_barometer(self):
        self.assertEqual(
            GUARDRAIL_THRESHOLDS["tonnage"]["phases"]["maintain"],
            GUARDRAIL_THRESHOLDS["tonnage"]["phases"]["cut"],
        )
        self.assertEqual(
            GUARDRAIL_THRESHOLDS["tonnage"]["phases"]["recomp"]["yellow_low_pct"],
            10.0,
        )
        self.assertEqual(
            GUARDRAIL_THRESHOLDS["tonnage"]["phases"]["slow_bulk"]["yellow_low_pct"],
            15.0,
        )
        self.assertEqual(GUARDRAIL_THRESHOLDS["scale"]["phases"]["maintain"]["yellow_abs_pct"], 0.5)
        self.assertEqual(GUARDRAIL_THRESHOLDS["lifts"]["yellow_weeks_down"], 2)
        self.assertEqual(GUARDRAIL_THRESHOLDS["lifts"]["red_weeks_down"], 3)


class TonnageBands(unittest.TestCase):
    def test_empty_history_does_not_invent_a_value(self):
        tile = _tile(build_guardrails(_payload([]), as_of=AS_OF), "tonnage")
        self.assertEqual(tile["status"], "insufficient")
        self.assertIsNone(tile["value"])
        self.assertIsNone(tile["delta_pct"])
        self.assertEqual(tile["reason"], "Not enough data.")
        self.assertEqual(build_guardrails(_payload([]), as_of=AS_OF)["flag"]["sentence"], "Not enough data.")

    def test_cut_yellow_between_10_and_20(self):
        # Weeks 1–3 at 10_000 and week 0 at 8_462 is about 12% under the 4-week week.
        body = build_guardrails(_payload(_weeks({0: 8462, 1: 10000, 2: 10000, 3: 10000})), as_of=AS_OF)
        tile = _tile(body, "tonnage")
        self.assertEqual(tile["status"], "yellow")
        self.assertEqual(tile["value"], 8462)
        self.assertLess(tile["delta_pct"], 0)

    def test_bulk_is_looser_than_cut_at_12_percent(self):
        sessions = _weeks({0: 8462, 1: 10000, 2: 10000, 3: 10000})
        bulk = _tile(build_guardrails(_payload(sessions, phase="slow_bulk"), as_of=AS_OF), "tonnage")
        self.assertEqual(bulk["status"], "green")
        maintain = _tile(build_guardrails(_payload(sessions, phase="maintain"), as_of=AS_OF), "tonnage")
        recomp = _tile(build_guardrails(_payload(sessions, phase="recomp"), as_of=AS_OF), "tonnage")
        self.assertEqual(maintain["status"], "yellow")
        self.assertEqual(recomp["status"], "yellow")

    def test_one_deep_week_is_not_red_yet(self):
        body = build_guardrails(_payload(_weeks({0: 5000, 1: 10000, 2: 10000, 3: 10000})), as_of=AS_OF)
        tile = _tile(body, "tonnage")
        self.assertEqual(tile["status"], "yellow")
        self.assertEqual(tile["weeks_below_red"], 1)
        self.assertIn("Red needs 2 weeks", tile["reason"])

    def test_two_weeks_under_the_red_line(self):
        sessions = _weeks({0: 5000, 1: 5000, 2: 10000, 3: 10000})
        cut = build_guardrails(_payload(sessions, phase="cut"), as_of=AS_OF)
        bulk = build_guardrails(_payload(sessions, phase="bulk"), as_of=AS_OF)
        self.assertEqual(_tile(cut, "tonnage")["status"], "red")
        self.assertEqual(_tile(bulk, "tonnage")["status"], "red")
        self.assertGreaterEqual(_tile(cut, "tonnage")["weeks_below_red"], 2)
        self.assertEqual(bulk["phase"], "slow_bulk")

    def test_bulk_yellow_band_is_15_to_25(self):
        tile = _tile(
            build_guardrails(
                _payload(_weeks({0: 7267, 1: 10000, 2: 10000, 3: 10000}), phase="slow_bulk"),
                as_of=AS_OF,
            ),
            "tonnage",
        )
        self.assertEqual(tile["status"], "yellow")
        self.assertEqual(tile["weeks_below_red"], 0)

    def test_logged_deload_suppresses_red(self):
        sessions = _weeks({0: 5000, 1: 5000, 2: 10000, 3: 10000})
        sessions[0]["notes"] = "Restore deload"
        tile = _tile(build_guardrails(_payload(sessions), as_of=AS_OF), "tonnage")
        self.assertEqual(tile["status"], "yellow")
        self.assertTrue(tile["deload_suppressed"])

    def test_deload_flag_override_and_continuity_suppress_red(self):
        sessions = _weeks({0: 5000, 1: 5000, 2: 10000, 3: 10000})
        flagged = _payload(sessions, goals={**GOALS, "deload": True})
        self.assertEqual(_tile(build_guardrails(flagged, as_of=AS_OF), "tonnage")["status"], "yellow")
        override = _payload(sessions, plan={"exercises": [{"progression_reason": "deload_override"}]})
        self.assertTrue(_tile(build_guardrails(override, as_of=AS_OF), "tonnage")["deload_suppressed"])
        cont = _payload(sessions, plan={"training_continuity": {"phase": "deload"}})
        self.assertEqual(_tile(build_guardrails(cont, as_of=AS_OF), "tonnage")["status"], "yellow")

    def test_old_deload_and_return_phase_do_not_suppress_red(self):
        sessions = _weeks({0: 5000, 1: 5000, 2: 10000, 3: 10000})
        sessions.append(_sess("2026-08-01", 1000, notes="deload week"))
        tile = _tile(build_guardrails(_payload(sessions), as_of=AS_OF), "tonnage")
        self.assertEqual(tile["status"], "red")
        returned = _payload(sessions, plan={"training_continuity": {"phase": "return"}})
        self.assertEqual(_tile(build_guardrails(returned, as_of=AS_OF), "tonnage")["status"], "red")


class LiftBands(unittest.TestCase):
    def test_alias_merges_romanian_deadlift_onto_rdl(self):
        sessions = [
            _sess(WEEK[1], 0, [_lift("Romanian Deadlift", 45, 8)]),
            _sess(WEEK[0], 0, [_lift("RDLs", 45, 8)]),
        ]
        tile = _tile(build_guardrails(_payload(sessions), as_of=AS_OF), "lifts")
        line = _line(tile, "hinge")
        self.assertEqual(line["name"], "RDL")
        self.assertEqual(line["label"], "45×8")
        self.assertEqual(line["status"], "green")
        self.assertEqual(_line(tile, "squat")["status"], "insufficient")

    def test_more_reps_at_the_same_load_is_not_down(self):
        sessions = [
            _sess(WEEK[1], 0, [_lift("RDL", 45, 5)]),
            _sess(WEEK[0], 0, [_lift("RDL", 45, 8)]),
        ]
        line = _line(_tile(build_guardrails(_payload(sessions), as_of=AS_OF), "lifts"), "hinge")
        self.assertEqual(line["move"], "flat")
        self.assertEqual(line["weeks_down"], 0)
        self.assertEqual(line["status"], "green")

    def test_two_weeks_down_is_yellow_and_three_is_red(self):
        yellow = [
            _sess(WEEK[2], 0, [_lift("Leg Press", 200, 8)]),
            _sess(WEEK[1], 0, [_lift("Leg Press", 200, 6)]),
            _sess(WEEK[0], 0, [_lift("Leg Press", 200, 4)]),
        ]
        line = _line(_tile(build_guardrails(_payload(yellow), as_of=AS_OF), "lifts"), "squat")
        self.assertEqual(line["status"], "yellow")
        self.assertEqual(line["weeks_down"], 2)
        red = [
            _sess(WEEK[3], 0, [_lift("DB Incline Press", 50, 8)]),
            _sess(WEEK[2], 0, [_lift("DB Incline Press", 45, 8)]),
            _sess(WEEK[1], 0, [_lift("DB Incline Press", 40, 8)]),
            _sess(WEEK[0], 0, [_lift("DB Incline Press", 35, 8)]),
        ]
        pushed = _line(_tile(build_guardrails(_payload(red), as_of=AS_OF), "lifts"), "push")
        self.assertEqual(pushed["name"], "DB Incline Press")
        self.assertEqual(pushed["status"], "red")
        self.assertEqual(pushed["weeks_down"], 3)
        self.assertEqual(pushed["label"], "35×8")

    def test_roles_follow_the_pinned_list_not_log_frequency(self):
        sessions = [
            _sess(WEEK[1], 0, [_lift("Bicep Curl", 20, 12), _lift("Lat Pulldown", 80, 8)]),
            _sess(WEEK[0], 0, [_lift("Bicep Curl", 25, 12), _lift("Pulldowns", 80, 8)]),
        ]
        tile = _tile(build_guardrails(_payload(sessions), as_of=AS_OF), "lifts")
        self.assertEqual([line["role"] for line in tile["lines"]], ["hinge", "squat", "push", "pull"])
        self.assertEqual(_line(tile, "pull")["name"], "Seated Cable Row")
        self.assertEqual(_line(tile, "push")["name"], "DB Incline Press")
        self.assertTrue(all(line["name"] != "Bicep Curl" for line in tile["lines"]))
        self.assertTrue(all(line["name"] != "Pulldowns" for line in tile["lines"]))


class ScaleBands(unittest.TestCase):
    def test_missing_scale_does_not_invent_a_percent(self):
        tile = _tile(build_guardrails(_payload([]), as_of=AS_OF), "scale")
        self.assertEqual(tile["status"], "insufficient")
        self.assertIsNone(tile["weekly_pct"])
        self.assertIsNone(tile["value"])

    def test_cut_yellow_when_loss_is_under_a_quarter_percent(self):
        weights = [(WEEK[3], 200), (WEEK[2], 200), (WEEK[1], 199.8), (WEEK[0], 199.6)]
        tile = _tile(build_guardrails(_payload([], weights=weights, phase="cut"), as_of=AS_OF), "scale")
        self.assertEqual(tile["status"], "yellow")
        self.assertLess(tile["weekly_pct"], 0)

    def test_cut_red_needs_a_stall_and_a_sliding_lift(self):
        weights = [(WEEK[3], 200), (WEEK[2], 200), (WEEK[1], 200), (WEEK[0], 200)]
        sliding = [
            _sess(WEEK[3], 1000, [_lift("RDL", 100, 5)]),
            _sess(WEEK[2], 1000, [_lift("RDL", 90, 5)]),
            _sess(WEEK[1], 1000, [_lift("RDL", 80, 5)]),
            _sess(WEEK[0], 1000, [_lift("RDL", 70, 5)]),
        ]
        red = _tile(build_guardrails(_payload(sliding, weights=weights), as_of=AS_OF), "scale")
        self.assertEqual(red["status"], "red")
        holding = [
            _sess(day, 1000, [_lift("RDL", 100, 5)]) for day in WEEK.values()
        ]
        held = _tile(build_guardrails(_payload(holding, weights=weights), as_of=AS_OF), "scale")
        self.assertEqual(held["status"], "yellow")

    def test_bulk_yellow_at_two_weeks_and_red_at_three(self):
        fast = [(WEEK[2], 180), (WEEK[1], 181.5), (WEEK[0], 183.02)]
        yellow = _tile(
            build_guardrails(_payload([], weights=fast, phase="slow_bulk"), as_of=AS_OF),
            "scale",
        )
        self.assertEqual(yellow["status"], "yellow")
        flat = [(WEEK[3], 180), (WEEK[2], 180), (WEEK[1], 180), (WEEK[0], 180)]
        red = _tile(
            build_guardrails(_payload([], weights=flat, phase="slow_bulk"), as_of=AS_OF),
            "scale",
        )
        self.assertEqual(red["status"], "red")

    def test_maintain_yellow_past_half_percent_for_two_weeks(self):
        weights = [(WEEK[2], 180), (WEEK[1], 181.08), (WEEK[0], 182.17)]
        tile = _tile(
            build_guardrails(_payload([], weights=weights, phase="maintain"), as_of=AS_OF),
            "scale",
        )
        self.assertEqual(tile["status"], "yellow")
        quiet = [(WEEK[1], 180), (WEEK[0], 180.18)]
        held = _tile(
            build_guardrails(_payload([], weights=quiet, phase="recomp"), as_of=AS_OF),
            "scale",
        )
        self.assertEqual(held["status"], "green")
        self.assertAlmostEqual(held["band"]["low"], -0.25)
        self.assertAlmostEqual(held["band"]["high"], 0.25)


class TreadingWater(unittest.TestCase):
    def test_green_when_tonnage_is_up(self):
        body = build_guardrails(
            _payload(_weeks({0: 4000, 1: 1000, 2: 1000, 3: 1000})),
            as_of=AS_OF,
        )
        self.assertEqual(body["flag"]["status"], "green")
        self.assertIn("Tonnage is up", body["flag"]["sentence"])
        self.assertEqual(_tile(body, "flag")["status"], "green")

    def test_red_when_all_three_are_flat_for_three_weeks(self):
        sessions = [
            _sess(day, 8000, [_lift("RDL", 100, 5)]) for day in (WEEK[0], WEEK[1], WEEK[2], WEEK[3])
        ]
        weights = [(day, 180) for day in (WEEK[0], WEEK[1], WEEK[2], WEEK[3])]
        body = build_guardrails(_payload(sessions, weights=weights), as_of=AS_OF)
        self.assertGreaterEqual(body["flag"]["weeks_flat"], 3)
        self.assertEqual(body["flag"]["status"], "red")
        self.assertIn("All three metrics flat for", body["flag"]["sentence"])
        chips = {chip["id"]: chip["status"] for chip in body["flag"]["chips"]}
        self.assertEqual(set(chips), {"tonnage", "lifts", "scale"})

    def test_yellow_when_flat_for_fewer_than_three_weeks(self):
        sessions = []
        for idx, day in WEEK.items():
            exercises = [_lift("RDL", 100, 5)] if idx < 3 else []
            sessions.append(_sess(day, 8000, exercises))
        weights = [(day, 180) for day in WEEK.values()]
        body = build_guardrails(_payload(sessions, weights=weights), as_of=AS_OF)
        self.assertEqual(body["flag"]["weeks_flat"], 2)
        self.assertEqual(body["flag"]["status"], "yellow")
        self.assertIn("2 weeks", body["flag"]["sentence"])


class AgentPhaseFeed(unittest.TestCase):
    def test_cookie_less_today_returns_canonical_phase(self):
        body = export_agent_today(
            {
                "coach": {"today": {"date": AS_OF}},
                "nutrition_store": {"targets": {"phase": "bulk"}},
                "sessions": [],
                "health": {"weight": []},
                "meta": {"local_today": AS_OF},
            }
        )
        self.assertEqual(body["phase_barometer"]["phase"], "slow_bulk")
        self.assertEqual(body["phase_barometer"]["phase_label"], "Bulk")
        self.assertIn("guardrails", body)
        self.assertEqual(body["guardrails"]["phase"], "slow_bulk")

    def test_switch_phase_write_is_what_the_feed_reads(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('base["phase"] = raw_phase', server)
        payload = {
            "coach": {
                "today": {"date": AS_OF},
                "nutrition_targets": {"phase": "cut"},
            },
            "nutrition_store": {"targets": {"phase": "cut"}},
            "meta": {"local_today": AS_OF},
            "sessions": [],
            "health": {"weight": []},
        }
        first = export_agent_today(payload)
        self.assertEqual(first["phase_barometer"]["phase"], "cut")
        self.assertEqual(first["phase_barometer"]["phase_label"], "Cut")
        payload["nutrition_store"]["targets"]["phase"] = "maintain"
        payload["coach"]["nutrition_targets"]["phase"] = "maintain"
        payload["phase_barometer"] = {"phase": "maintain", "phase_label": "Maintenance"}
        second = export_agent_today(payload)
        self.assertEqual(second["phase_barometer"]["phase"], "maintain")
        self.assertEqual(second["phase_barometer"]["phase_label"], "Maintenance")
        self.assertEqual(second["guardrails"]["phase"], "maintain")

    def test_export_carries_guardrail_statuses(self):
        body = export_trends_window(
            {"weight": [{"date": WEEK[0], "weight_lbs": 180}, {"date": WEEK[1], "weight_lbs": 181}]},
            days=14,
            end=AS_OF,
            tz_name="America/New_York",
            sessions=_weeks({0: 8462, 1: 10000, 2: 10000, 3: 10000}),
            goals=GOALS,
            phase="cut",
        )
        self.assertTrue(body["ok"])
        self.assertEqual(body["guardrails"]["phase"], "cut")
        statuses = {tile["id"]: tile["status"] for tile in body["guardrails"]["tiles"]}
        self.assertEqual(statuses["tonnage"], "yellow")
        self.assertIn("flag", body["guardrails"])


class GuardrailMarkup(unittest.TestCase):
    def test_first_card_on_trends_not_today(self):
        html = (ROOT / "static/index.html").read_text(encoding="utf-8")
        css = (ROOT / "static/styles.css").read_text(encoding="utf-8")
        js = (ROOT / "static/app.js").read_text(encoding="utf-8")
        sw = (ROOT / "static/sw.js").read_text(encoding="utf-8")
        guard = html.find('id="guardrail-monitor"')
        weekly = html.find('id="weekly-review-card"')
        self.assertGreater(guard, 0)
        self.assertLess(guard, weekly)
        card = html[guard:weekly]
        self.assertIn('data-m-panel="trends"', card)
        self.assertNotIn('data-m-panel="today"', card)
        self.assertIn("Guardrails", card)
        self.assertIn("4-week baselines", card)
        self.assertNotIn("First on Trends", html[weekly:weekly + 500])
        self.assertIn("function renderGuardrails", js)
        self.assertIn("Not enough data.", js)
        self.assertIn("@media (max-width: 560px)", css)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr))", css)
        self.assertIn('const CACHE = "fitdash-shell-v128"', sw)
        self.assertIn("/app.js?v=guardrails-1026-1", html)
        self.assertIn("/app.js?v=guardrails-1026-1", sw)
        self.assertIn("/styles.css?v=guardrails-1026-1", html)
        self.assertIn("/styles.css?v=guardrails-1026-1", sw)
        self.assertIn("/history-sets.js?v=history-sets-1", sw)
