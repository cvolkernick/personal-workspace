"""Goal 3b coach rules, staged calendar, hydration, and fiber floor.

Calendar output is compared with the file present and with the path missing.
The staged panel and the advisory pre-draw reminder are display-only, so the
byte check is the decision payload (targets, rate, recommendation, state).
"""

from __future__ import annotations

import copy
import inspect
import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from rt_dashboard.coach_steps import clear_rules_cache, fiber_floor_g, load_rules
from rt_dashboard.cut_coach import (
    BANDS,
    build_card,
    decision_view,
    decide,
    fast_yellow,
    refresh_bands,
    today_targets,
)
from rt_dashboard.guardrails import build_guardrails
from rt_dashboard.hydration_bars import build_hydration_bars_payload, rules_target_ml
from rt_dashboard.phase_barometer import scale_thresholds
from rt_dashboard.nutrition_targets import micros_for_calories
from rt_dashboard.phase_calendar import (
    effective,
    pre_draw_reminder,
    resolve_phases,
)

ROOT = Path(__file__).resolve().parents[2]
CALENDAR = ROOT / "fitness" / "coach" / "phase_calendar_goal3b.json"
RULES = ROOT / "fitness" / "coach" / "coach_rules.json"
SEED = ROOT / "fitness" / "nutrition" / "targets.json"


def _rules(**updates):
    clear_rules_cache()
    rules = copy.deepcopy(load_rules())
    block = dict(rules.get("phase_calendar") or {})
    block.update(updates)
    rules["phase_calendar"] = block
    return rules


def _on():
    return _rules(enabled=True, approved_by="Chris", approved_at="2026-10-28")


def _state(**kwargs):
    base = {
        "kcal_target": 2000,
        "step_target": 7000,
        "phase": "cut",
        "weight_lbs": 170,
        "last_step_up_date": None,
        "step_events": [],
    }
    base.update(kwargs)
    return base


def _weeks(*rows):
    return [dict(row) for row in rows]


def _bytes(card):
    return json.dumps(decision_view(card), sort_keys=True, separators=(",", ":"))


class Goal3bCoach(unittest.TestCase):
    def tearDown(self):
        clear_rules_cache()
        refresh_bands(load_rules())

    def test_calendar_file_stays_off(self):
        raw = json.loads(RULES.read_text(encoding="utf-8"))
        steps = raw["phase_calendar"]["steps"]
        kcal = raw["phase_calendar"]["kcal"]
        self.assertTrue(steps["enabled"])
        self.assertEqual(steps["approved_by"], "Chris 2026-10-27 21:58 ET via Grok")
        self.assertFalse(kcal["enabled"])
        self.assertIsNone(kcal["approved_by"])
        cal = json.loads(CALENDAR.read_text(encoding="utf-8"))
        schedule = cal["tracks"]["steps"]["schedule"]
        self.assertEqual(schedule[0], {"from": "2026-10-28", "step_target": 7000})
        self.assertEqual(schedule[1], {"from": "2026-11-16", "step_target": 8000})
        self.assertEqual(schedule[2], {"from": "2026-12-07", "step_target": 9000})
        self.assertFalse(cal["tracks"]["kcal"]["enabled"])
        self.assertEqual(cal["tracks"]["kcal"]["phases"], [])
        blob = json.dumps(cal)
        self.assertNotIn("2300", blob)
        self.assertNotIn("maintenance", blob)
        self.assertEqual(cal["draw"]["date"], "2027-01-28")

    def test_cut_band_from_rules_json(self):
        refresh_bands(load_rules())
        self.assertEqual(BANDS["cut"], (0.25, 0.5))
        self.assertEqual(BANDS["cut_target"], (0.25, 0.5))
        bare = copy.deepcopy(load_rules())
        bare.pop("cut_band_pct", None)
        bare.pop("cut_target_pct", None)
        built = refresh_bands(bare)
        self.assertEqual(built["cut"], (0.25, 0.5))
        self.assertEqual(built["cut_target"], (0.25, 0.5))

    def test_guardrail_scale_reads_the_same_cut_band(self):
        live = scale_thresholds("cut")
        self.assertAlmostEqual(live["target_high_pct"], -0.25)
        self.assertAlmostEqual(live["target_low_pct"], -0.5)
        self.assertAlmostEqual(live["yellow_loss_under_pct"], 0.25)
        self.assertAlmostEqual(live["yellow_loss_over_pct"], 0.5)
        bare = copy.deepcopy(load_rules())
        bare.pop("cut_band_pct", None)
        fallback = scale_thresholds("cut", bare)
        self.assertAlmostEqual(fallback["target_high_pct"], -0.25)
        self.assertAlmostEqual(fallback["target_low_pct"], -0.5)
        payload = {
            "sessions": [],
            "health": {
                "weight": [
                    {"date": "2026-10-14", "weight_lbs": 180},
                    {"date": "2026-10-21", "weight_lbs": 179.5},
                    {"date": "2026-10-28", "weight_lbs": 179.1},
                ]
            },
            "phase_barometer": {"phase": "cut"},
            "workout_store": {"goals": {}},
            "meta": {"local_today": "2026-10-28"},
        }
        tile = next(t for t in build_guardrails(payload, as_of="2026-10-28")["tiles"] if t["id"] == "scale")
        self.assertAlmostEqual(tile["band"]["high"], -0.25)
        self.assertAlmostEqual(tile["band"]["low"], -0.5)
        self.assertIn("0.25–0.5%", tile["rule"])
        self.assertNotIn("1.5%", tile["rule"])
        self.assertNotIn("1.0%", tile["rule"])
        with mock.patch("rt_dashboard.coach_steps.cut_band", return_value=(0.3, 0.4)):
            shifted = scale_thresholds("cut", {"cut_band_pct": [0.3, 0.4]})
            self.assertAlmostEqual(shifted["target_high_pct"], -0.3)
            self.assertAlmostEqual(shifted["target_low_pct"], -0.4)
            moved = next(
                t for t in build_guardrails(payload, as_of="2026-10-28")["tiles"] if t["id"] == "scale"
            )
        self.assertAlmostEqual(moved["band"]["high"], -0.3)
        self.assertAlmostEqual(moved["band"]["low"], -0.4)
        self.assertIn("0.3–0.4%", moved["rule"])

    def test_cut_rate_cap_half_pct(self):
        self.assertNotIn("0.75", inspect.getsource(fast_yellow))
        fast = _weeks(
            {"rate_pct": 0.6, "counted": True},
            {"rate_pct": 0.6, "counted": True},
        )
        card = decide(as_of="2026-10-28", state=_state(), rules=_rules(), weeks=fast)
        self.assertEqual(card["recommendation"]["action"], "none")
        self.assertIn("losing faster than 0.5%/wk, flagged to coach", card["notes"])
        self.assertEqual(card["targets"]["kcal_target"], 2000)
        self.assertEqual(card["recommendation"]["proposed_kcal"], 2000)
        inside = _weeks(
            {"rate_pct": 0.4, "counted": True},
            {"rate_pct": 0.4, "counted": True},
        )
        quiet = decide(as_of="2026-10-28", state=_state(), rules=_rules(), weeks=inside)
        self.assertEqual(quiet["recommendation"]["action"], "none")
        self.assertNotIn("losing faster than 0.5%/wk, flagged to coach", quiet["notes"])
        yellow = _weeks({"rate_pct": 0.6, "strength_yellow": True, "counted": True})
        flagged = decide(as_of="2026-10-28", state=_state(), rules=_rules(), weeks=yellow)
        self.assertEqual(flagged["recommendation"]["action"], "none")
        self.assertIn("losing faster than 0.5%/wk, flagged to coach", flagged["notes"])
        self.assertEqual(flagged["targets"]["kcal_target"], 2000)
        self.assertTrue(fast_yellow(0.6, 0.5, strength_yellow=True))
        self.assertFalse(fast_yellow(0.4, 0.5, strength_yellow=True))

    def test_cut_too_slow_live_unchanged(self):
        rules = _rules()
        over = _weeks(
            {"rate_pct": 0.1, "kcal_avg": 2200, "steps_avg": 7000, "counted": True},
            {"rate_pct": 0.1, "kcal_avg": 2200, "steps_avg": 7000, "counted": True},
        )
        hit = decide(as_of="2026-10-28", state=_state(), rules=rules, weeks=over)
        self.assertEqual(hit["recommendation"]["action"], "hit_target")
        on_target = _weeks(
            {"rate_pct": 0.1, "kcal_avg": 2200, "steps_avg": 8000, "counted": True},
            {"rate_pct": 0.1, "kcal_avg": 2200, "steps_avg": 8000, "counted": True},
        )
        cut = decide(
            as_of="2026-10-28",
            state=_state(kcal_target=2200),
            rules=rules,
            weeks=on_target,
        )
        self.assertEqual(cut["recommendation"]["action"], "kcal_minus_150")
        at_floor = _weeks(
            {"rate_pct": 0.1, "kcal_avg": 2000, "steps_avg": 8000, "counted": True},
            {"rate_pct": 0.1, "kcal_avg": 2000, "steps_avg": 8000, "counted": True},
        )
        stepped = decide(as_of="2026-10-28", state=_state(), rules=rules, weeks=at_floor)
        self.assertEqual(stepped["recommendation"]["action"], "steps_plus_1500")
        self.assertEqual(stepped["state"]["kcal_target"], 2000)
        self.assertEqual(stepped["state"]["step_target"], 7000)

    def test_calendar_off_is_noop(self):
        """Steps track on: only the step target moves on/after 11/16 and 12/7."""
        present = _rules()
        held = copy.deepcopy(present)
        held["phase_calendar"]["steps"] = {"enabled": False, "approved_by": None}
        state = _state()
        snapshot = copy.deepcopy(state)
        expect = {
            "2026-10-28": 7000,
            "2026-11-09": 7000,
            "2026-11-15": 7000,
            "2026-11-16": 8000,
            "2026-12-06": 8000,
            "2026-12-07": 9000,
            "2026-12-21": 9000,
        }
        for day, steps in expect.items():
            on = build_card(as_of=day, state=state, rules=present)
            off = build_card(as_of=day, state=state, rules=held)
            self.assertEqual(on["targets"]["kcal_target"], 2000, day)
            self.assertEqual(off["targets"]["kcal_target"], 2000, day)
            self.assertEqual(on["targets"]["step_target"], steps, day)
            self.assertEqual(off["targets"]["step_target"], 7000, day)
            self.assertEqual(on["kcal_indicator"]["low"], 1850, day)
            self.assertEqual(on["kcal_indicator"]["high"], 2150, day)
            self.assertEqual(on["kcal_indicator"]["source"], "cut")
            self.assertNotIn("maintenance", on["kcal_indicator"]["text"].lower())
            self.assertNotIn("maintenance", on["phase_pill"].lower())
            self.assertEqual(on["phase"], "cut")
            if steps == 7000:
                self.assertEqual(on["targets"]["step_target"], off["targets"]["step_target"], day)
                self.assertEqual(on["state"]["step_events"], [])
            else:
                self.assertNotEqual(on["targets"]["step_target"], off["targets"]["step_target"], day)
        self.assertEqual(state, snapshot)
        label = build_card(as_of="2026-10-28", state=state, rules=present)["staged"]["label"]
        self.assertIn("Steps approved and applied", label)
        self.assertIn("Calories held", label)

    def test_calendar_requires_approval(self):
        rules = _rules()
        rules["phase_calendar"]["steps"] = {"enabled": True, "approved_by": None}
        self.assertIsNone(effective("2026-11-16", rules=rules))
        state = _state()
        card = decide(as_of="2026-11-16", state=state, rules=rules)
        self.assertFalse(card["kcal_applied"])
        self.assertEqual(card["targets"]["step_target"], 7000)
        self.assertEqual(card["targets"]["kcal_target"], 2000)
        self.assertEqual(state["step_target"], 7000)

    def test_phase_calendar_targets(self):
        from rt_dashboard.phase_calendar import steps_resolution

        rules = _rules()
        expect = {
            "2026-11-08": 7000,
            "2026-11-09": 7000,
            "2026-11-16": 8000,
            "2026-11-26": 8000,
            "2026-12-07": 9000,
            "2026-12-21": 9000,
            "2027-01-04": 9000,
            "2027-01-28": 9000,
        }
        for day, steps in expect.items():
            self.assertIsNone(effective(day, rules=rules), day)
            got = steps_resolution(day, rules=rules)
            self.assertTrue(got["applied"], day)
            self.assertEqual(got["step_target"], steps, day)
            card = decide(as_of=day, state=_state(), rules=rules)
            self.assertEqual(card["targets"]["kcal_target"], 2000, day)
            self.assertEqual(card["targets"]["step_target"], steps, day)
            self.assertEqual(card["phase"], "cut", day)
        self.assertFalse(steps_resolution("2027-02-13", rules=rules)["applied"])

    def test_calendar_overrides_auto_steps(self):
        rules = _on()
        slow = _weeks(
            {"rate_pct": 0.1, "kcal_avg": 2000, "steps_avg": 4000, "counted": True},
            {"rate_pct": 0.1, "kcal_avg": 2000, "steps_avg": 4000, "counted": True},
        )
        held = decide(
            as_of="2026-11-20",
            state=_state(weight_lbs=170),
            rules=rules,
            weeks=slow,
        )
        self.assertNotEqual(held["recommendation"]["action"], "steps_plus_1500")
        self.assertEqual(held["targets"]["step_target"], 8000)
        drop = decide(as_of="2026-11-09", state=_state(), rules=rules)
        self.assertEqual(drop["targets"]["kcal_target"], 2000)
        self.assertEqual(drop["targets"]["step_target"], 7000)
        bump = decide(
            as_of="2026-11-16",
            state=_state(),
            rules=rules,
            weeks=_weeks({"rate_pct": 0.2, "steps_avg": 5810, "kcal_avg": 2000, "counted": True}),
        )
        self.assertEqual(bump["targets"]["step_target"], 8000)
        self.assertNotEqual(bump["recommendation"]["action"], "steps_plus_1500")
        self.assertIn("Hit the step target", bump["labels"])
        kinds = [event.get("kind") for event in bump["step_events"]]
        self.assertIn("calendar", kinds)

    def test_calendar_deload_restores_to_calendar_target(self):
        rules = _on()
        down = decide(as_of="2026-12-10", state=_state(), rules=rules, deload=True)
        self.assertEqual(down["targets"]["step_target"], 7500)
        back = decide(as_of="2026-12-11", state=_state(), rules=rules, deload=False)
        self.assertEqual(back["targets"]["step_target"], 9000)

    def test_calendar_end_resumes_auto(self):
        card = decide(as_of="2027-02-13", state=_state(), rules=_on())
        self.assertFalse(card["calendar_applied"])
        self.assertTrue(card["auto_steps"])
        self.assertEqual(card["targets"]["step_target"], 9000)
        self.assertEqual(card["state"]["last_step_up_date"], "2026-12-07")
        self.assertEqual(card["next_step_up_earliest"], "2026-12-21")

    def test_kcal_track_held_off(self):
        rules = _rules()
        self.assertFalse(rules["phase_calendar"]["kcal"]["enabled"])
        for day in ("2026-10-28", "2026-11-09", "2026-11-26", "2026-12-21", "2027-01-28"):
            self.assertIsNone(effective(day, rules=rules), day)
            card = decide(as_of=day, state=_state(), rules=rules)
            self.assertEqual(card["targets"]["kcal_target"], 2000, day)
            self.assertFalse(card["kcal_applied"], day)
            self.assertEqual(card["kcal_indicator"]["source"], "cut", day)
            self.assertIn("· cut", card["kcal_indicator"]["text"])
            self.assertNotIn("maintenance", card["kcal_indicator"]["text"].lower())

    def test_sixteen_week_cut_does_not_switch_phase(self):
        card = decide(
            as_of="2026-12-28",
            state=_state(phase="cut", anchor="2026-09-07"),
            rules=_rules(),
        )
        self.assertEqual(card["phase"], "cut")
        self.assertEqual(card["state"]["phase"], "cut")
        self.assertEqual(card["targets"]["kcal_target"], 2000)
        self.assertNotEqual(card["recommendation"]["action"], "switch_phase")
        self.assertNotIn("maintenance", card["phase_pill"].lower())

    def test_kcal_indicator_follows_phase(self):
        off = decide(as_of="2026-10-28", state=_state(), rules=_rules())
        ind = off["kcal_indicator"]
        self.assertEqual(ind["low"], 1850)
        self.assertEqual(ind["high"], 2150)
        self.assertEqual(ind["source"], "cut")
        self.assertEqual(ind["phase_label"], "cut")
        self.assertIn("· cut", ind["text"])
        later = decide(as_of="2026-11-26", state=_state(), rules=_rules())
        self.assertEqual(later["kcal_indicator"]["weekly_mean"], 2000)
        self.assertEqual(later["kcal_indicator"]["low"], 1850)
        self.assertEqual(later["kcal_indicator"]["high"], 2150)
        self.assertEqual(later["kcal_indicator"]["source"], "cut")
        self.assertNotIn("maintenance", later["kcal_indicator"]["text"].lower())

    def test_hydration_target_override_3000(self):
        self.assertEqual(rules_target_ml(), 3000.0)
        payload = build_hydration_bars_payload(
            weight=[{"date": "2026-10-28", "weight_lbs": 180}],
            as_of="2026-10-28",
            target_ml_override=rules_target_ml(),
            lifting_day=True,
            hot_day=True,
        )
        self.assertEqual(payload["target_ml"], 3000.0)
        self.assertEqual(payload["target_source"], "override")
        hot = build_hydration_bars_payload(
            as_of="2026-10-28",
            target_ml_override=3000,
            lifting_day=False,
            hot_day=True,
        )
        self.assertEqual(hot["target_ml"], 3000.0)

    def test_fiber_target_35(self):
        self.assertEqual(fiber_floor_g(), 35)
        self.assertEqual(micros_for_calories(2000)["fiber_g"], 35)
        self.assertEqual(micros_for_calories(2100)["fiber_g"], 35)
        seed = json.loads(SEED.read_text(encoding="utf-8"))
        self.assertEqual(seed["fiber_g"], 35)
        self.assertEqual(json.loads(RULES.read_text(encoding="utf-8"))["nutrition_floors"]["fiber_g_min"], 35)

    def test_draw_tokens_follow_booking_date(self):
        placeholder = resolve_phases(rules=_on(), use_store=False)
        self.assertEqual(placeholder["draw"]["date"], "2027-01-28")
        self.assertEqual(placeholder["draw"]["source"], "plan_default")
        self.assertEqual(placeholder["flags"]["draw"], "2027-01-28")
        self.assertEqual(placeholder["flags"]["no_program_change_from"], "2027-01-14")
        self.assertNotIn("p3_end", placeholder["flags"])
        self.assertNotIn("no_hard_training_from", placeholder["flags"])
        reminder = pre_draw_reminder("2027-01-28")
        self.assertEqual(reminder["from"], "2027-01-26")
        self.assertEqual(reminder["from_note"], "AM")
        self.assertFalse(reminder["enforced"])
        self.assertFalse(reminder["chip"])
        self.assertEqual(date(2027, 1, 28).strftime("%A"), "Thursday")
        self.assertEqual(date(2027, 1, 26).strftime("%A"), "Tuesday")
        src = inspect.getsource(pre_draw_reminder)
        for word in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "weekday"):
            self.assertNotIn(word, src)

        booked = resolve_phases(rules=_on(), booking_date="2027-02-03", use_store=False)
        self.assertEqual(booked["draw"]["date"], "2027-02-03")
        self.assertEqual(booked["draw"]["source"], "labs_booking")
        self.assertEqual(booked["flags"]["draw"], "2027-02-03")
        self.assertEqual(booked["flags"]["no_program_change_from"], "2027-01-20")
        moved = pre_draw_reminder(booked["draw"]["date"])
        self.assertEqual(moved["from"], "2027-02-01")

        via_labs = resolve_phases(
            rules=_on(),
            labs={"booking": {"date": "2027-03-02"}},
            use_store=False,
        )
        self.assertEqual(via_labs["draw"]["date"], "2027-03-02")
        self.assertEqual(pre_draw_reminder(via_labs["draw"]["date"])["from"], "2027-02-28")

        via_config = _on()
        via_config["phase_calendar"]["labs_booking_date"] = "2027-02-10"
        configured = resolve_phases(rules=via_config, use_store=False)
        self.assertEqual(configured["draw"]["date"], "2027-02-10")
        self.assertEqual(pre_draw_reminder(configured["draw"]["date"])["from"], "2027-02-08")

        for bad in (None, "", "not-a-date", {"booking": {}}):
            fallen = resolve_phases(rules=_on(), labs=bad, use_store=False)
            self.assertEqual(fallen["draw"]["date"], "2027-01-28", bad)

        advisory_day = decide(as_of="2027-01-26", state=_state(), rules=_on())
        self.assertNotIn("no_hard_training", json.dumps(decision_view(advisory_day)))
        self.assertEqual(advisory_day["recommendation"]["action"], "none")
        self.assertEqual(advisory_day["targets"]["kcal_target"], 2000)
        self.assertEqual(advisory_day["targets"]["step_target"], 9000)
        self.assertEqual(advisory_day["phase"], "cut")
        self.assertFalse(advisory_day["pre_draw_reminder"]["enforced"])
        self.assertFalse(advisory_day["pre_draw_reminder"]["chip"])
        self.assertEqual(advisory_day["pre_draw_reminder"]["from"], "2027-01-26")


if __name__ == "__main__":
    unittest.main()
