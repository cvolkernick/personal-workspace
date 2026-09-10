"""Coach-owned nutrition target recommendations (formula v1).

Recommend is pure. Dashboard load must not write targets.json.
Missing burned days are not averaged as 0.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from rt_dashboard.models import (
    CaloriesBurnedDay,
    HealthSnapshot,
    NutritionDay,
    RecoveryStatus,
    WeightSample,
)
from rt_dashboard.nutrition_planner import normalize_targets
from rt_dashboard.nutrition_targets import (
    infer_phase,
    merge_recommended_into_applied,
    micros_for_calories,
    recommend_nutrition_targets,
    round_g,
    round_kcal,
)

ROOT = Path(__file__).resolve().parents[1]
COACH_PY = (ROOT / "rt_dashboard" / "coach.py").read_text(encoding="utf-8")
SERVER_PY = (ROOT / "server.py").read_text(encoding="utf-8")
NT_PY = (ROOT / "rt_dashboard" / "nutrition_targets.py").read_text(encoding="utf-8")
ACTIONS_PY = (ROOT / "rt_dashboard" / "coach_actions.py").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
PLANNER = (ROOT / "rt_dashboard" / "nutrition_planner.py").read_text(encoding="utf-8")


def _snap(*, burned, weights, nutrition=None, as_prefix="2026-08-"):
    return HealthSnapshot(
        calories_burned=[
            CaloriesBurnedDay(date=d, calories=c) for d, c in burned
        ],
        weight=[WeightSample(date=d, weight_lbs=w) for d, w in weights],
        nutrition=[
            NutritionDay(date=d, calories=c) for d, c in (nutrition or [])
        ],
    )


class FormulaHelpers(unittest.TestCase):
    def test_round_kcal_50(self):
        self.assertEqual(round_kcal(2474), 2450)
        self.assertEqual(round_kcal(2475), 2500)

    def test_round_g_5(self):
        self.assertEqual(round_g(172), 170)
        self.assertEqual(round_g(173), 175)

    def test_infer_phase_notes_and_goal(self):
        self.assertEqual(infer_phase({"notes": "Default cutting targets"}, 173), "cut")
        self.assertEqual(infer_phase({"phase": "slow_bulk"}, 173), "slow_bulk")
        self.assertEqual(
            infer_phase({"weight_goal_lbs": 150}, 173), "cut"
        )
        self.assertEqual(
            infer_phase({"weight_goal_lbs": 180}, 173), "slow_bulk"
        )
        self.assertEqual(
            infer_phase({"weight_goal_lbs": 174}, 173), "maintain"
        )


class RecommendCut(unittest.TestCase):
    def test_cut_uses_present_burn_mean_not_missing_zero(self):
        # 14d window ending 2026-08-27. Five 2500 days + no other days.
        burned = [(f"2026-08-{d:02d}", 2500) for d in (23, 24, 25, 26, 27)]
        weights = [("2026-08-20", 173.0), ("2026-08-27", 172.8)]
        rec = recommend_nutrition_targets(
            health=_snap(burned=burned, weights=weights),
            targets={
                "calories": 2100,
                "protein_g": 210,
                "carbs_g": 180,
                "fat_g": 55,
                "weight_goal_lbs": 150,
                "notes": "Default cutting targets",
            },
            as_of="2026-08-27",
        )
        self.assertEqual(rec["tdee_kcal"], 2500)
        self.assertEqual(rec["tdee_days"], 5)
        self.assertEqual(rec["phase"], "cut")
        self.assertFalse(rec["abstain"])
        # gap 22.8 lb → deficit clamp(22.8*15, 250, 500) = 342 → 2500-342 → round 50
        self.assertLess(rec["recommended"]["calories"], 2500)
        self.assertGreaterEqual(rec["recommended"]["calories"], 1800)
        self.assertEqual(rec["recommended"]["calories"] % 50, 0)
        self.assertEqual(rec["recommended"]["protein_g"] % 5, 0)
        # Missing burned days must not pull the mean down toward 0
        self.assertGreater(rec["tdee_kcal"], 2000)
        self.assertIn("wearable is an estimate", " ".join(rec["reasons"]))

    def test_no_weigh_in_calorie_stays_applied(self):
        """Grok AC: 14d burned all 2500, weight=[], notes=cutting, applied=2100.

        abstain=True, TDEE hat still 2500, recommended calories stay 2100.
        Do not invent gap_lb=10.
        """
        burned = [(f"2026-08-{d:02d}", 2500) for d in range(14, 28)]
        rec = recommend_nutrition_targets(
            health=_snap(burned=burned, weights=[]),
            targets={
                "calories": 2100,
                "protein_g": 210,
                "carbs_g": 180,
                "fat_g": 55,
                "notes": "cutting",
            },
            as_of="2026-08-27",
        )
        self.assertTrue(rec["abstain"])
        self.assertEqual(rec["tdee_kcal"], 2500)
        self.assertEqual(rec["tdee_days"], 14)
        self.assertIsNone(rec["current_weight_lbs"])
        self.assertEqual(rec["recommended"]["calories"], 2100)
        self.assertEqual(rec["delta"]["calories"], 0)
        self.assertEqual(rec["recommended"]["protein_g"], 210)
        self.assertTrue(
            any("weigh-in" in r.lower() and "abstain" in r.lower() for r in rec["reasons"])
        )

    def test_missing_burn_not_zero_abstain(self):
        rec = recommend_nutrition_targets(
            health=_snap(burned=[], weights=[("2026-08-27", 173)]),
            targets={"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            as_of="2026-08-27",
        )
        self.assertTrue(rec["abstain"])
        self.assertIsNone(rec["tdee_kcal"])
        self.assertEqual(rec["recommended"]["calories"], 2100)

    def test_recovery_floor_does_not_deepen(self):
        burned = [(f"2026-08-{d:02d}", 2500) for d in range(14, 28)]
        rec = recommend_nutrition_targets(
            health=_snap(
                burned=burned,
                weights=[("2026-08-20", 173.0), ("2026-08-27", 172.8)],
            ),
            targets={
                "calories": 2400,
                "protein_g": 210,
                "carbs_g": 180,
                "fat_g": 55,
                "weight_goal_lbs": 150,
                "phase": "cut",
            },
            recovery=RecoveryStatus(label="Caution", score=35.0, reasons=["low"]),
            as_of="2026-08-27",
        )
        self.assertGreaterEqual(rec["recommended"]["calories"], 2400)
        self.assertTrue(any("recovery" in r.lower() for r in rec["reasons"]))

    def test_protein_compliance_does_not_deepen(self):
        burned = [(f"2026-08-{d:02d}", 2500) for d in range(14, 28)]
        rec = recommend_nutrition_targets(
            health=_snap(
                burned=burned,
                weights=[("2026-08-20", 173.0), ("2026-08-27", 172.8)],
            ),
            targets={
                "calories": 2400,
                "protein_g": 210,
                "carbs_g": 180,
                "fat_g": 55,
                "weight_goal_lbs": 150,
                "phase": "cut",
            },
            recovery=RecoveryStatus(label="Ready", score=80.0, reasons=[]),
            adherence_7d={"protein": {"pct": 40.0}},
            as_of="2026-08-27",
        )
        self.assertGreaterEqual(rec["recommended"]["calories"], 2400)
        self.assertTrue(any("protein hit rate" in r.lower() for r in rec["reasons"]))

    def test_maintain_equals_tdee(self):
        burned = [(f"2026-08-{d:02d}", 2400) for d in range(14, 28)]
        rec = recommend_nutrition_targets(
            health=_snap(
                burned=burned,
                weights=[("2026-08-20", 175.0), ("2026-08-27", 175.2)],
            ),
            targets={
                "calories": 2100,
                "protein_g": 180,
                "carbs_g": 200,
                "fat_g": 60,
                "weight_goal_lbs": 175,
                "phase": "maintain",
            },
            as_of="2026-08-27",
        )
        self.assertEqual(rec["phase"], "maintain")
        self.assertEqual(rec["recommended"]["calories"], 2400)

    def test_slow_bulk_plus_200(self):
        burned = [(f"2026-08-{d:02d}", 2400) for d in range(14, 28)]
        rec = recommend_nutrition_targets(
            health=_snap(
                burned=burned,
                weights=[("2026-08-20", 160.0), ("2026-08-27", 160.5)],
            ),
            targets={
                "calories": 2100,
                "protein_g": 180,
                "carbs_g": 200,
                "fat_g": 60,
                "weight_goal_lbs": 175,
                "phase": "slow_bulk",
            },
            as_of="2026-08-27",
        )
        self.assertEqual(rec["recommended"]["calories"], 2600)

    def test_does_not_invent_tdee_from_intake(self):
        rec = recommend_nutrition_targets(
            health=_snap(
                burned=[],
                weights=[("2026-08-27", 173)],
                nutrition=[(f"2026-08-{d:02d}", 1800) for d in range(14, 28)],
            ),
            targets={"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            as_of="2026-08-27",
        )
        self.assertTrue(rec["abstain"])
        self.assertIsNone(rec["tdee_kcal"])
        self.assertEqual(rec["recommended"]["calories"], 2100)


class MicrosFormula(unittest.TestCase):
    def test_fiber_sugar_sodium_from_calories(self):
        m2100 = micros_for_calories(2100)
        self.assertEqual(m2100, {"fiber_g": 30, "sugar_g": 50, "sodium_mg": 2300})
        m1800 = micros_for_calories(1800)
        self.assertEqual(m1800["fiber_g"], 25)
        self.assertEqual(m1800["sugar_g"], 45)
        self.assertEqual(m1800["sodium_mg"], 2300)
        m2500 = micros_for_calories(2500)
        self.assertEqual(m2500["fiber_g"], 35)
        self.assertEqual(m2500["sugar_g"], round_g(2500 * 0.10 / 4.0))
        self.assertEqual(m2500["sodium_mg"], 2300)
        self.assertEqual(micros_for_calories(1000)["fiber_g"], 20)

    def test_recommend_includes_micros_with_reasons(self):
        rec = recommend_nutrition_targets(
            health=_snap(burned=[], weights=[]),
            targets={"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            as_of="2026-08-27",
        )
        self.assertTrue(rec["abstain"])
        self.assertEqual(rec["recommended"]["calories"], 2100)
        self.assertEqual(rec["recommended"]["fiber_g"], 30)
        self.assertEqual(rec["recommended"]["sugar_g"], 50)
        self.assertEqual(rec["recommended"]["sodium_mg"], 2300)
        self.assertNotIn("fiber_g", rec["applied"])
        self.assertEqual(rec["delta"]["fiber_g"], 30)
        blob = " ".join(rec["reasons"]).lower()
        self.assertIn("fiber 30g", blob)
        self.assertIn("total sugars", blob)
        self.assertIn("not added sugars", blob)
        self.assertIn("salt (sodium) 2300mg", blob)

    def test_does_not_invent_applied_micros(self):
        rec = recommend_nutrition_targets(
            health=_snap(burned=[], weights=[]),
            targets={"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            as_of="2026-08-27",
        )
        self.assertNotIn("fiber_g", rec["applied"])
        self.assertNotIn("sugar_g", rec["applied"])
        self.assertNotIn("sodium_mg", rec["applied"])

    def test_normalize_targets_persists_micros_and_does_not_invent(self):
        bare = normalize_targets({"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55})
        self.assertNotIn("fiber_g", bare)
        self.assertNotIn("sugar_g", bare)
        self.assertNotIn("sodium_mg", bare)
        filled = normalize_targets(
            {
                "calories": 2100,
                "protein_g": 210,
                "carbs_g": 180,
                "fat_g": 55,
                "fiber_g": 30,
                "sugar_g": 40,
                "sodium_mg": 2000,
            }
        )
        self.assertEqual(filled["fiber_g"], 30)
        self.assertEqual(filled["sugar_g"], 40)
        self.assertEqual(filled["sodium_mg"], 2000)


class ApplyMerge(unittest.TestCase):
    def test_preserves_weight_goal(self):
        rec = {
            "as_of": "2026-08-27",
            "phase": "cut",
            "recommended": {
                "calories": 2000,
                "protein_g": 175,
                "carbs_g": 180,
                "fat_g": 60,
            },
        }
        merged = merge_recommended_into_applied(
            {
                "calories": 2100,
                "protein_g": 210,
                "carbs_g": 180,
                "fat_g": 55,
                "weight_goal_lbs": 150,
            },
            rec,
        )
        self.assertEqual(merged["calories"], 2000)
        self.assertEqual(merged["weight_goal_lbs"], 150)
        self.assertEqual(merged["phase"], "cut")

    def test_merge_carries_micros(self):
        rec = {
            "as_of": "2026-08-27",
            "phase": "cut",
            "recommended": {
                "calories": 2000,
                "protein_g": 175,
                "carbs_g": 180,
                "fat_g": 60,
                "fiber_g": 30,
                "sugar_g": 50,
                "sodium_mg": 2300,
            },
        }
        merged = merge_recommended_into_applied(
            {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            rec,
        )
        self.assertEqual(merged["fiber_g"], 30)
        self.assertEqual(merged["sugar_g"], 50)
        self.assertEqual(merged["sodium_mg"], 2300)


class WiringLock(unittest.TestCase):
    def test_payload_includes_nutrition_targets(self):
        self.assertIn("nutrition_targets", COACH_PY)
        self.assertIn("recommend_nutrition_targets", COACH_PY)
        self.assertIn("nutrition_targets", SERVER_PY)

    def test_apply_coach_targets_action(self):
        self.assertIn("apply_coach_targets", ACTIONS_PY)
        self.assertIn("apply coach targets", ACTIONS_PY)
        self.assertIn("apply_coach_targets", SERVER_PY)

    def test_ui_apply_button(self):
        self.assertIn("Apply coach targets", HTML)
        self.assertIn("coach-targets-rec", HTML)
        self.assertIn("applyCoachTargets", APP_JS)
        self.assertIn("tgt-phase", HTML)
        self.assertIn("tgt-fiber", HTML)
        self.assertIn("tgt-sugar", HTML)
        self.assertIn("tgt-sodium", HTML)
        self.assertIn("Salt (sodium)", HTML)
        self.assertIn("btn.disabled = !recd", APP_JS)
        self.assertNotIn("btn.disabled = !!rec.abstain", APP_JS)
        self.assertIn('progressRow("Salt (sodium)"', APP_JS)

    def test_phase_normalized(self):
        self.assertIn('"phase"', PLANNER)
        self.assertIn("cut", PLANNER)
        self.assertIn("slow_bulk", PLANNER)

    def test_load_does_not_write_targets(self):
        self.assertNotIn("write_nutrition_file", NT_PY)
        self.assertNotIn("update_targets(", NT_PY)
        # recommend path in coach payload is compute-only
        self.assertIn("recommend_nutrition_targets", COACH_PY)

    def test_does_not_touch_azm_or_cals_window(self):
        self.assertIn("const CAL_IN_OUT_SPAN_DAYS = 75;", APP_JS)
        self.assertIn("/trends-azm.js?v=azm-90d-3", HTML)
        self.assertNotIn(".sb-shell", NT_PY)


if __name__ == "__main__":
    unittest.main()
