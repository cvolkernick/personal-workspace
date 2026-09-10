"""Phase Barometer — Keep vs Pivot (#576)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from rt_dashboard.phase_barometer import (
    attach_phase_barometer,
    build_phase_barometer,
    collect_kpis,
    dismiss_banner,
    evaluate_decision,
    iso_week_key,
    load_store,
    save_store,
    undismiss_banner,
    upsert_week,
)


ROOT = Path(__file__).resolve().parents[1]
COACH_PY = (ROOT / "rt_dashboard" / "coach.py").read_text(encoding="utf-8")
SERVER_PY = (ROOT / "server.py").read_text(encoding="utf-8")
DASH_PY = (ROOT / "api" / "dashboard.py").read_text(encoding="utf-8")
UTIL_PY = (ROOT / "api" / "workout" / "_util.py").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
ASK_PY = (ROOT / "rt_dashboard" / "grok_ask.py").read_text(encoding="utf-8")


def _kpis(
    as_of: str,
    *,
    phase: str = "cut",
    weekly: float = 1.65,
    protein: float = 42.9,
    score: float = 86.0,
    sleep: float = 41.4,
    vol: float = 18100.0,
    deficit: float = 400.0,
    labs_low: bool = True,
    fat_gain: bool = False,
    over_12=None,
    recommended=None,
) -> dict:
    return {
        "as_of": as_of,
        "week": iso_week_key(as_of),
        "phase": phase,
        "weight_delta_7d_lbs": 1.2,
        "latest_weight_lbs": 173.0,
        "weekly_14d_lb": weekly,
        "protein_pct": protein,
        "calories_pct": 50.0,
        "recovery_score": score,
        "sleep_battery_pct": sleep,
        "avg_sleep_hours_7d": 6.2,
        "labs_cluster_id": "energy_availability" if labs_low else None,
        "labs_low": labs_low,
        "fat_gain_labs": fat_gain,
        "markers": {"free_t3": 2.6, "total_testosterone": 420, "free_testosterone": 80, "estrogen": 12},
        "training_volume_7d": vol,
        "muscles_over_12": list(over_12 or []),
        "muscle_status_counts": {"under": 4, "high": 0, "over": 0},
        "volume_manageable": not (over_12 or []) and vol < 22000,
        "deficit_kcal": deficit,
        "recommended": recommended or {"calories": 2100, "protein_g": 175, "carbs_g": 215, "fat_g": 60},
        "applied": {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
        "focus_muscles": ["chest", "lats"],
    }


def _good_cut(as_of: str, **extra) -> dict:
    kwargs = dict(
        phase="cut",
        weekly=0.1,
        protein=75.0,
        score=60.0,
        sleep=40.0,
        vol=18000.0,
        deficit=400.0,
        labs_low=True,
    )
    kwargs.update(extra)
    return _kpis(as_of, **kwargs)


class CurrentSnapshotBlocks(unittest.TestCase):
    def test_issue_snapshot_blocks_pivot(self):
        d = evaluate_decision(_kpis("2026-09-09"), history=[])
        self.assertEqual(d["reading"], "keep")
        self.assertEqual(d["phase"], "cut")
        self.assertIn("Keep Cutting", d["status"])
        self.assertEqual(d["tone"], "green")
        self.assertTrue(d["protein_blocks_pivot"])
        self.assertFalse(d["consecutive_weeks_met"])
        self.assertIn("42.9", d["explanation"])
        self.assertIn("1.65", d["explanation"])
        self.assertIn("energy-availability", d["explanation"])
        self.assertFalse(d["banner"])

    def test_never_pivot_when_protein_under_70(self):
        hist = [{"week": iso_week_key("2026-08-26"), "kpis": _good_cut("2026-08-26")}]
        now = _good_cut("2026-09-02", protein=60.0)
        d = evaluate_decision(now, history=hist)
        self.assertEqual(d["reading"], "keep")
        self.assertTrue(d["protein_blocks_pivot"])
        self.assertIn("60.0%", d["explanation"])
        self.assertIn("70%", d["explanation"])

    def test_two_weeks_all_gates_pivots_to_bulk(self):
        hist = [{"week": iso_week_key("2026-08-26"), "kpis": _good_cut("2026-08-26")}]
        d = evaluate_decision(_good_cut("2026-09-02"), history=hist)
        self.assertEqual(d["reading"], "pivot")
        self.assertEqual(d["next_phase"], "slow_bulk")
        self.assertIn("Consider Bulk", d["status"])
        self.assertIn(d["tone"], ("amber", "red"))
        self.assertTrue(d["banner"])
        self.assertTrue(d["consecutive_weeks_met"])

    def test_missing_prior_week_does_not_pivot(self):
        d = evaluate_decision(_good_cut("2026-09-02"), history=[])
        self.assertEqual(d["reading"], "keep")
        self.assertFalse(d["consecutive_weeks_met"])

    def test_iso_week_cliff_is_not_two_weeks(self):
        """Sunday 2026-09-06 = W36, Monday 2026-09-07 = W37 — 1 day, not 2 weeks."""
        hist = [
            {
                "week": iso_week_key("2026-09-06"),
                "as_of": "2026-09-06",
                "kpis": _good_cut("2026-09-06"),
            }
        ]
        d = evaluate_decision(_good_cut("2026-09-07"), history=hist)
        self.assertEqual(iso_week_key("2026-09-06"), "2026-W36")
        self.assertEqual(iso_week_key("2026-09-07"), "2026-W37")
        self.assertEqual(d["reading"], "keep")
        self.assertFalse(d["consecutive_weeks_met"])

    def test_prior_as_of_exactly_7_days_still_counts(self):
        hist = [
            {
                "week": iso_week_key("2026-08-31"),
                "as_of": "2026-08-31",
                "kpis": _good_cut("2026-08-31"),
            }
        ]
        d = evaluate_decision(_good_cut("2026-09-07"), history=hist)
        self.assertEqual(d["reading"], "pivot")
        self.assertTrue(d["consecutive_weeks_met"])


class BulkAndMaintain(unittest.TestCase):
    def test_bulk_to_cut(self):
        k = _kpis(
            "2026-09-02",
            phase="slow_bulk",
            weekly=0.7,
            protein=80.0,
            score=50.0,
            sleep=35.0,
            fat_gain=True,
            labs_low=False,
        )
        hist = [{"week": iso_week_key("2026-08-26"), "kpis": dict(k, as_of="2026-08-26", week=iso_week_key("2026-08-26"))}]
        d = evaluate_decision(k, history=hist)
        self.assertEqual(d["reading"], "pivot")
        self.assertEqual(d["next_phase"], "cut")
        self.assertIn("Consider Cut", d["status"])

    def test_maintain_stays_without_3w_drift(self):
        k = _kpis("2026-09-02", phase="maintain", weekly=0.1, protein=80.0, score=80.0, sleep=70.0, labs_low=False)
        d = evaluate_decision(k, history=[])
        self.assertEqual(d["reading"], "keep")
        self.assertIn("Keep Current Phase", d["status"])

    def test_maintain_pivots_after_3w_drift(self):
        def row(day, weekly):
            k = _kpis(day, phase="maintain", weekly=weekly, protein=80.0, score=80.0, sleep=70.0, labs_low=False)
            return {"week": k["week"], "kpis": k}

        hist = [row("2026-08-19", 0.5), row("2026-08-26", 0.5)]
        d = evaluate_decision(
            _kpis("2026-09-02", phase="maintain", weekly=0.5, protein=80.0, score=80.0, sleep=70.0, labs_low=False),
            history=hist,
        )
        self.assertEqual(d["reading"], "pivot")


class CollectFromJson(unittest.TestCase):
    def test_reads_named_fields_only(self):
        kpis = collect_kpis(
            as_of="2026-09-09",
            recovery={
                "score": 86,
                "inputs": {
                    "weight_delta_7d_lbs": 1.2,
                    "latest_weight_lbs": 173.4,
                    "avg_sleep_hours_7d": 6.1,
                    "training_volume_7d": 18100,
                },
            },
            sleep_battery={"pct_charged": 41.1},
            coach={
                "adherence_7d": {"protein": {"pct": 42.9}, "calories": {"pct": 40.0}},
                "nutrition_targets": {
                    "phase": "cut",
                    "tdee_kcal": 2500,
                    "applied": {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
                    "recommended": {"calories": 2100, "protein_g": 175, "carbs_g": 215, "fat_g": 60},
                    "reasons": ["14d scale weekly +1.65 lb/week", "phase=cut; deficit 400 kcal"],
                },
            },
            nutrition_store={
                "labs": {
                    "cluster": {"id": "energy_availability"},
                    "markers": {
                        "free_t3": {"id": "free_t3", "value": 2.6},
                        "total_testosterone": {"id": "total_testosterone", "value": 420},
                    },
                }
            },
            workout_store={
                "goals": {"focus_muscles": ["chest"]},
                "plan": {
                    "volume": {
                        "muscles": [
                            {"muscle": "chest", "projected": 8, "status": "ok"},
                            {"muscle": "lats", "projected": 3, "status": "under"},
                        ]
                    }
                },
            },
        )
        self.assertEqual(kpis["protein_pct"], 42.9)
        self.assertEqual(kpis["weekly_14d_lb"], 1.65)
        self.assertEqual(kpis["deficit_kcal"], 400)
        self.assertTrue(kpis["labs_low"])
        self.assertTrue(kpis["volume_manageable"])
        self.assertEqual(kpis["sleep_battery_pct"], 41.1)
        rec = kpis["recommended"]
        self.assertEqual(rec["protein_g"], 175)
        self.assertEqual(rec["carbs_g"], 215)
        self.assertEqual(rec["fat_g"], 60)

    def test_deficit_uses_tdee_minus_applied_not_reason_string(self):
        """Applied maintenance (or <300 gap) must not inherit recommended 'deficit N kcal'."""
        nt = {
            "phase": "cut",
            "tdee_kcal": 2500,
            "applied": {"calories": 2500, "protein_g": 210, "carbs_g": 250, "fat_g": 70},
            "recommended": {"calories": 2100, "protein_g": 175, "carbs_g": 215, "fat_g": 60},
            "reasons": ["14d scale weekly +0.10 lb/week", "phase=cut; deficit 400 kcal"],
        }
        kpis = collect_kpis(
            as_of="2026-09-09",
            recovery={"score": 60, "inputs": {"training_volume_7d": 18000}},
            sleep_battery={"pct_charged": 40.0},
            coach={"adherence_7d": {"protein": {"pct": 75.0}}, "nutrition_targets": nt},
            nutrition_store={"labs": {"cluster": {"id": "energy_availability"}, "markers": {}}},
            workout_store={"plan": {"volume": {"muscles": []}}},
        )
        self.assertEqual(kpis["deficit_kcal"], 0)
        hist = [{"week": iso_week_key("2026-08-26"), "kpis": _good_cut("2026-08-26")}]
        now = _good_cut("2026-09-02", deficit=kpis["deficit_kcal"])
        d = evaluate_decision(now, history=hist)
        self.assertEqual(d["reading"], "keep")
        self.assertFalse(d["gates"]["deficit"])
        self.assertFalse(d["consecutive_weeks_met"])
        thin = dict(nt)
        thin["applied"] = {"calories": 2300}
        thin_kpis = collect_kpis(
            as_of="2026-09-09",
            recovery={"score": 60, "inputs": {"training_volume_7d": 18000}},
            sleep_battery={"pct_charged": 40.0},
            coach={"adherence_7d": {"protein": {"pct": 75.0}}, "nutrition_targets": thin},
            nutrition_store={"labs": {"cluster": {"id": "energy_availability"}, "markers": {}}},
            workout_store={"plan": {"volume": {"muscles": []}}},
        )
        self.assertEqual(thin_kpis["deficit_kcal"], 200)
        d_thin = evaluate_decision(_good_cut("2026-09-02", deficit=200), history=hist)
        self.assertEqual(d_thin["reading"], "keep")
        self.assertFalse(d_thin["gates"]["deficit"])

    def test_deficit_falls_back_to_recommended_then_reasons(self):
        rec_only = collect_kpis(
            as_of="2026-09-09",
            recovery={"score": 60, "inputs": {"training_volume_7d": 18000}},
            sleep_battery={"pct_charged": 40.0},
            coach={
                "adherence_7d": {"protein": {"pct": 75.0}},
                "nutrition_targets": {
                    "phase": "cut",
                    "tdee_kcal": 2500,
                    "applied": {"protein_g": 210},
                    "recommended": {"calories": 2100},
                    "reasons": ["phase=cut; deficit 999 kcal"],
                },
            },
            nutrition_store={"labs": {}},
            workout_store={"plan": {"volume": {"muscles": []}}},
        )
        self.assertEqual(rec_only["deficit_kcal"], 400)
        reasons_only = collect_kpis(
            as_of="2026-09-09",
            recovery={"score": 60, "inputs": {"training_volume_7d": 18000}},
            sleep_battery={"pct_charged": 40.0},
            coach={
                "adherence_7d": {"protein": {"pct": 75.0}},
                "nutrition_targets": {
                    "phase": "cut",
                    "reasons": ["phase=cut; deficit 350 kcal"],
                },
            },
            nutrition_store={"labs": {}},
            workout_store={"plan": {"volume": {"muscles": []}}},
        )
        self.assertEqual(reasons_only["deficit_kcal"], 350)


class PersistWeeks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["FITDASH_PHASE_BAROMETER_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("FITDASH_PHASE_BAROMETER_DIR", None)
        self.tmp.cleanup()

    def test_disk_not_ram(self):
        k1 = _good_cut("2026-08-26")
        store = upsert_week({}, k1)
        save_store(store, user_id="u1")
        loaded = load_store("u1")
        self.assertEqual(len(loaded["weekly_snapshots"]), 1)
        payload = {
            "meta": {"local_today": "2026-09-02", "user_id": "u1"},
            "recovery": {"score": 60, "inputs": {"training_volume_7d": 18000, "weight_delta_7d_lbs": 0.1, "latest_weight_lbs": 173}},
            "sleep_battery": {"pct_charged": 40.0},
            "coach": {
                "adherence_7d": {"protein": {"pct": 75.0}},
                "nutrition_targets": {
                    "phase": "cut",
                    "tdee_kcal": 2500,
                    "applied": {"calories": 2100},
                    "recommended": {"protein_g": 175, "carbs_g": 215, "fat_g": 60, "calories": 2100},
                    "reasons": ["14d scale weekly +0.10 lb/week", "deficit 400 kcal"],
                },
            },
            "nutrition_store": {"labs": {"cluster": {"id": "energy_availability"}, "markers": {}}},
            "workout_store": {"plan": {"volume": {"muscles": []}}, "goals": {}},
        }
        obj = build_phase_barometer(payload, user_id="u1", persist=True)
        self.assertEqual(obj["reading"], "pivot")
        self.assertTrue(obj["persisted"])
        self.assertGreaterEqual(len(obj["weeks"]), 2)

    def test_dismiss_hides_banner_only(self):
        hist = [{"week": iso_week_key("2026-08-26"), "kpis": _good_cut("2026-08-26")}]
        d = evaluate_decision(_good_cut("2026-09-02"), history=hist)
        self.assertTrue(d["banner"])
        saved = dismiss_banner(as_of="2026-09-02", user_id="u2")
        self.assertEqual(saved["dismiss_banner_until"], "2026-09-09")
        d2 = evaluate_decision(
            _good_cut("2026-09-02"),
            history=hist,
            dismiss_until=saved["dismiss_banner_until"],
        )
        self.assertEqual(d2["reading"], "pivot")
        self.assertFalse(d2["banner"])
        self.assertIn("Consider Bulk", d2["status"])
        self.assertEqual(d2["dismissed_until"], "2026-09-09")

    def test_changed_verdict_clears_dismiss(self):
        payload = {
            "meta": {"local_today": "2026-09-02", "user_id": "u-clear"},
            "recovery": {
                "score": 60,
                "inputs": {
                    "training_volume_7d": 18000,
                    "weight_delta_7d_lbs": 0.1,
                    "latest_weight_lbs": 173,
                },
            },
            "sleep_battery": {"pct_charged": 40.0},
            "coach": {
                "adherence_7d": {"protein": {"pct": 75.0}},
                "nutrition_targets": {
                    "phase": "cut",
                    "tdee_kcal": 2500,
                    "applied": {"calories": 2100},
                    "recommended": {
                        "protein_g": 175,
                        "carbs_g": 215,
                        "fat_g": 60,
                        "calories": 2100,
                    },
                    "reasons": ["14d scale weekly +0.10 lb/week", "deficit 400 kcal"],
                },
            },
            "nutrition_store": {
                "labs": {"cluster": {"id": "energy_availability"}, "markers": {}},
                "targets": {"phase": "cut"},
            },
            "workout_store": {"plan": {"volume": {"muscles": []}}, "goals": {}},
        }
        save_store(upsert_week({}, _good_cut("2026-08-26")), user_id="u-clear")
        first = build_phase_barometer(payload, user_id="u-clear", persist=True)
        self.assertEqual(first["reading"], "pivot")
        dismiss_banner(as_of="2026-09-02", user_id="u-clear")
        still = build_phase_barometer(payload, user_id="u-clear", persist=True)
        self.assertTrue(still["dismissed_until"])
        self.assertFalse(still["banner"])
        payload["coach"]["adherence_7d"]["protein"]["pct"] = 42.9
        payload["coach"]["nutrition_targets"]["reasons"] = [
            "14d scale weekly +1.65 lb/week"
        ]
        changed = build_phase_barometer(payload, user_id="u-clear", persist=True)
        self.assertIsNone(changed["dismissed_until"])
        self.assertEqual(changed["reading"], "keep")
        self.assertFalse(changed["banner"])

    def test_undismiss_restores_banner(self):
        hist = [{"week": iso_week_key("2026-08-26"), "kpis": _good_cut("2026-08-26")}]
        dismiss_banner(as_of="2026-09-02", user_id="u-undo")
        undismiss_banner(as_of="2026-09-02", user_id="u-undo")
        store = load_store("u-undo")
        self.assertIsNone(store.get("dismiss_banner_until"))
        d = evaluate_decision(
            _good_cut("2026-09-02"),
            history=hist,
            dismiss_until=store.get("dismiss_banner_until"),
        )
        self.assertTrue(d["banner"])

    def test_attach_writes_three_surfaces(self):
        payload = {
            "meta": {"local_today": "2026-09-09"},
            "recovery": {"score": 86, "inputs": {"training_volume_7d": 18100, "weight_delta_7d_lbs": 1.2, "latest_weight_lbs": 173}},
            "sleep_battery": {"pct_charged": 41.1},
            "coach": {
                "today": {},
                "weekly_review": {"bullets": ["Training: 3 sessions"]},
                "adherence_7d": {"protein": {"pct": 42.9}},
                "nutrition_targets": {
                    "phase": "cut",
                    "recommended": {"protein_g": 175, "carbs_g": 215, "fat_g": 60},
                    "reasons": ["14d scale weekly +1.65 lb/week"],
                },
            },
            "nutrition_store": {"labs": {}, "targets": {"phase": "cut"}},
            "workout_store": {"plan": {"volume": {"muscles": []}}},
        }
        attach_phase_barometer(payload, user_id="u3")
        self.assertIn("phase_barometer", payload)
        self.assertIn("phase_barometer", payload["coach"]["today"])
        self.assertIn("phase_barometer", payload["coach"]["weekly_review"])
        self.assertIn("phase_barometer", payload["nutrition_store"])
        rec = payload["phase_barometer"]["recommended"]
        self.assertEqual(rec["protein_g"], 175)
        self.assertNotIn("_path", payload["phase_barometer"])
        self.assertNotIn("_path", payload["coach"]["today"]["phase_barometer"])


class Wiring(unittest.TestCase):
    def test_attach_called_from_pi_and_vercel(self):
        self.assertIn("attach_phase_barometer", SERVER_PY)
        self.assertIn("attach_phase_barometer", DASH_PY)
        self.assertIn("phase_barometer", ASK_PY)

    def test_widget_mounts(self):
        self.assertIn('id="phase-barometer-targets"', HTML)
        self.assertIn('id="phase-barometer-banner"', HTML)
        self.assertNotIn('id="phase-barometer-today"', HTML)
        self.assertNotIn('id="phase-barometer-weekly"', HTML)
        self.assertNotIn('id="phase-barometer-nutrition"', HTML)
        self.assertEqual(HTML.count('id="phase-barometer-'), 2)
        self.assertIn("renderPhaseBarometer", APP_JS)
        self.assertIn("phaseBaroAlertHtml", APP_JS)
        self.assertIn("phaseBaroDismissedHtml", APP_JS)
        self.assertIn("Review on More", APP_JS)
        self.assertIn("Barometer dismissed until", APP_JS)
        self.assertIn("undismiss", APP_JS)
        self.assertIn("open_home", APP_JS)
        self.assertIn("Apply coach recommended targets", APP_JS)
        self.assertIn("Switch phase to Bulk", APP_JS)
        self.assertIn("Dismiss for 7d", APP_JS)
        self.assertIn("/api/phase-barometer", APP_JS)
        self.assertIn(".phase-baro-alert", CSS)
        self.assertIn("reading === \"pivot\"", APP_JS)
        self.assertIn("phase-barometer-targets", APP_JS)

    def test_api_routes(self):
        self.assertIn("/api/phase-barometer", SERVER_PY)
        self.assertIn("/api/phase-barometer", VERCEL)
        self.assertIn("phase_barometer", UTIL_PY)
        self.assertIn("undismiss", UTIL_PY)
        self.assertIn("undismiss", SERVER_PY)

    def test_apply_uses_recommended_object(self):
        d = evaluate_decision(_kpis("2026-09-09"))
        rec = d["recommended"]
        self.assertEqual(rec["protein_g"], 175)
        self.assertEqual(rec["carbs_g"], 215)
        self.assertEqual(rec["fat_g"], 60)
        self.assertIn("apply_coach: true", APP_JS)


if __name__ == "__main__":
    unittest.main()
