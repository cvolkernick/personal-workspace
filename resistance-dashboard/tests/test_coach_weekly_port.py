"""Weekly coach card port (Goal 3b item b): counted-week gate, noise, protein gap,
TDEE, steps-first one-lever advisory. Kcal never suggested while the kcal track is off."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from rt_dashboard import coach_weekly as cw
from rt_dashboard.coach_steps import clear_rules_cache, load_rules
from rt_dashboard.cut_coach import attach_coach_card, coach_card_for_payload

ROOT = Path(__file__).resolve().parents[1]


def _days(start: str, n: int):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _payload(weeks, *, start="2026-10-05", per_day=7, kcal=2000, protein=180, steps=7500, today="2026-11-02"):
    """``weeks``: avg weight per Mon-Sun week, oldest first."""
    weight, nutrition, step_rows = [], [], []
    for i, lbs in enumerate(weeks):
        days = _days((datetime.strptime(start, "%Y-%m-%d") + timedelta(days=7 * i)).strftime("%Y-%m-%d"), 7)
        for d in days[:per_day]:
            if lbs is not None:
                weight.append({"date": d, "weight_lbs": lbs})
            nutrition.append({"date": d, "calories": kcal, "protein_g": protein})
            step_rows.append({"date": d, "steps": steps})
    return {
        "meta": {"local_today": today},
        "health": {"weight": weight, "nutrition": nutrition, "steps": step_rows},
        "nutrition_store": {"targets": {"calories": 2000}},
    }


class _Env(unittest.TestCase):
    def setUp(self):
        clear_rules_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"FITDASH_COACH_STATE_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
        clear_rules_cache()


class WeekRows(_Env):
    def test_counted_needs_5_of_7_weigh_ins_and_food(self):
        rows = cw.build_weekly_rows(_payload([200, 199], per_day=4), as_of="2026-10-19", weeks_back=1)
        self.assertEqual([r["counted"] for r in rows], [False, False])
        rows = cw.build_weekly_rows(_payload([200, 199], per_day=5), as_of="2026-10-19", weeks_back=1)
        self.assertEqual([r["counted"] for r in rows], [True, True])
        self.assertEqual(rows[-1]["weigh_ins"], 5)

    def test_rate_sign_and_loss_pct(self):
        rows = cw.build_weekly_rows(_payload([200, 199]), as_of="2026-10-19", weeks_back=1)
        self.assertAlmostEqual(rows[-1]["rate_pct"], -0.5)
        self.assertAlmostEqual(rows[-1]["loss_pct"], 0.5)

    def test_noise_weight_up_without_kcal_up(self):
        wk = {"rate_pct": 1.2, "kcal_delta": 100}
        self.assertIn("weight_up_gt_1pct_kcal_up_lt_300", cw.noise_reasons(wk))
        self.assertEqual(cw.noise_reasons({"rate_pct": 1.2, "kcal_delta": 400}), [])

    def test_tags_manual_only_never_inferred(self):
        self.assertEqual(cw.noise_reasons({"rate_pct": -0.3}), [])
        self.assertEqual(cw.noise_reasons({"rate_pct": -0.3}, tags=["travel", "party"]), ["travel"])
        store = {"week_tags": {"2026-10-12": ["illness"]}}
        rows = cw.build_weekly_rows(_payload([200, 199]), as_of="2026-10-19", store=store, weeks_back=1)
        self.assertEqual(rows[-1]["noise"], ["illness"])
        self.assertFalse(cw.decision_weeks(rows)[-1]["counted"])

    def test_protein_low_days(self):
        rows = cw.build_weekly_rows(_payload([200, 199], protein=160), as_of="2026-10-19", weeks_back=1)
        self.assertEqual(rows[-1]["protein_low_days"], 7)
        rows = cw.build_weekly_rows(_payload([200, 199], protein=170), as_of="2026-10-19", weeks_back=1)
        self.assertEqual(rows[-1]["protein_low_days"], 0)

    def test_tdee_needs_two_counted_weeks(self):
        rows = cw.build_weekly_rows(_payload([200, 199]), as_of="2026-10-19", weeks_back=1)
        self.assertIsNone(cw.estimate_tdee(rows))
        rows = cw.build_weekly_rows(_payload([200, 199, 198]), as_of="2026-10-26", weeks_back=2)
        t = cw.estimate_tdee(rows)
        self.assertEqual(t["tdee"], 2500)  # 2000 + 1 lb/wk * 500
        self.assertEqual((t["low"], t["high"]), (2350, 2650))
        self.assertEqual(t["weeks_used"], 2)


class Advisory(_Env):
    def test_building_below_two_counted_weeks(self):
        a = cw.steps_first_advisory(decision_rate=None, band=(0.25, 0.5), counted=1, step_target=7000, as_of="2026-11-02")
        self.assertEqual(a["action"], "building")
        self.assertIn("Weekly data building", a["detail"])

    def test_too_slow_steps_only_next_calendar_step_capped(self):
        rules = load_rules()
        a = cw.steps_first_advisory(decision_rate=0.1, band=(0.25, 0.5), counted=2, step_target=7000, as_of="2026-11-02", rules=rules)
        self.assertEqual(a["action"], "steps_pull_earlier")
        self.assertEqual(a["lever"], "steps")
        self.assertEqual(a["next_step"], {"from": "2026-11-16", "step_target": 8000})
        self.assertEqual(a["proposed_steps"], 8000)
        self.assertNotIn("kcal", a["detail"].lower())
        self.assertFalse(a["applies_automatically"])
        b = cw.steps_first_advisory(decision_rate=0.1, band=(0.25, 0.5), counted=2, step_target=8000, as_of="2026-11-20", rules=rules)
        self.assertEqual(b["next_step"]["step_target"], 9000)
        self.assertEqual(b["proposed_steps"], 9000)

    def test_too_fast_flags_grok_no_change(self):
        a = cw.steps_first_advisory(decision_rate=0.8, band=(0.25, 0.5), counted=2, step_target=7000, as_of="2026-11-02")
        self.assertEqual(a["action"], "flag_to_grok")
        self.assertEqual(a["proposed_steps"], 7000)
        self.assertIn("flag to Grok", a["detail"])

    def test_inside_band_change_nothing(self):
        a = cw.steps_first_advisory(decision_rate=0.3, band=(0.25, 0.5), counted=2, step_target=7000, as_of="2026-11-02")
        self.assertEqual(a["action"], "change_nothing")


class Card(_Env):
    def test_card_building_before_two_counted_weeks(self):
        card = coach_card_for_payload(_payload([200], start="2026-10-26"), as_of="2026-11-02")
        w = card["weekly"]
        self.assertEqual(w["status"], "building")
        self.assertTrue(w["text"].startswith("Weekly data building"))
        self.assertEqual(card["recommendation"]["action"], "building")
        self.assertEqual(card["recommendation"]["proposed_kcal"], 2000)

    def test_too_slow_card_never_suggests_kcal_cut_while_off(self):
        # 200 -> 199.9 -> 199.8: 0.05%/wk, too slow over 2 counted weeks.
        card = coach_card_for_payload(_payload([200, 199.9, 199.8], start="2026-10-12"), as_of="2026-11-02")
        rec = card["recommendation"]
        self.assertEqual(card["weekly"]["status"], "ready")
        self.assertEqual(rec["action"], "steps_pull_earlier")
        self.assertEqual(rec["proposed_kcal"], 2000)
        self.assertEqual(rec["proposed_steps"], 8000)
        self.assertNotIn("kcal", rec["detail"].lower())
        self.assertIsNone(card["macros"])
        self.assertEqual(card["targets"]["kcal_target"], 2000)
        self.assertNotIn("1950", json.dumps(card))

    def test_too_fast_card(self):
        card = coach_card_for_payload(_payload([200, 198.5, 197], start="2026-10-12"), as_of="2026-11-02")
        self.assertEqual(card["recommendation"]["action"], "flag_to_grok")
        self.assertEqual(card["recommendation"]["proposed_steps"], card["targets"]["step_target"])

    def test_noise_week_does_not_count(self):
        store = {"week_tags": {"2026-10-26": ["travel"]}}
        cw.save_store(dict(cw.STORE_DEFAULT, **store))
        card = coach_card_for_payload(_payload([200, 199.9, 199.8], start="2026-10-12"), as_of="2026-11-02")
        self.assertEqual(card["weekly"]["noise"], ["travel"])
        self.assertEqual(card["weekly"]["status"], "building")

    def test_protein_chip_and_tdee_on_card(self):
        card = coach_card_for_payload(_payload([200, 199.4, 198.8], start="2026-10-12", protein=150), as_of="2026-11-02")
        w = card["weekly"]
        self.assertTrue(w["protein_chip"]["show"])
        self.assertEqual(w["protein_chip"]["label"], "Protein gap")
        self.assertIsNotNone(w["tdee"])

    def test_attach_keeps_tdee_history_once_per_week(self):
        p = _payload([200, 199.4, 198.8], start="2026-10-12")
        attach_coach_card(p, as_of="2026-11-02")
        attach_coach_card(_payload([200, 199.4, 198.8], start="2026-10-12"), as_of="2026-11-02")
        hist = cw.load_store()["tdee_history"]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["week_start"], "2026-10-26")

    def test_store_round_trip_keeps_defaults(self):
        s = cw.load_store()
        self.assertEqual(s["tdee_history"], [])
        self.assertIsNone(s["last_adjustment"])
        s["last_adjustment"] = {"week_start": "2026-10-26", "lever": "steps"}
        cw.save_store(s)
        self.assertEqual(cw.load_store()["last_adjustment"]["lever"], "steps")


class Ui(unittest.TestCase):
    def test_node_weekly_lines(self):
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("node missing")
        proc = subprocess.run([node, str(ROOT / "tests" / "coach_weekly_card.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_weekly_slot_present(self):
        html = (ROOT / "static" / "index.html").read_text()
        self.assertIn('id="coach-weekly"', html)
        js = (ROOT / "static" / "app.js").read_text()
        self.assertIn("function renderCoachWeekly", js)


if __name__ == "__main__":
    unittest.main()
