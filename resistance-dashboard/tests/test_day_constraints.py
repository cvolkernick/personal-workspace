"""P3-F Fit day constraints exporter tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rt_dashboard.day_constraints import (  # noqa: E402
    build_day_constraints_packet,
    export_day_constraints_from_dashboard,
    protein_gap_band,
    write_day_constraints,
)
from rt_dashboard.models import (  # noqa: E402
    RecoveryStatus,
    Session,
    SleepSample,
)


class ProteinBandTests(unittest.TestCase):
    def test_ok_when_consumed_hits_85pct(self) -> None:
        self.assertEqual(
            protein_gap_band(
                protein_target_g=180,
                protein_remaining_g=20,
                protein_consumed_g=160,
                has_today_protein_log=True,
            ),
            "ok",
        )

    def test_ok_when_remaining_small(self) -> None:
        self.assertEqual(
            protein_gap_band(
                protein_target_g=200,
                protein_remaining_g=20,  # 0.10 × target
                protein_consumed_g=180,
                has_today_protein_log=True,
            ),
            "ok",
        )

    def test_watch_mid_gap(self) -> None:
        self.assertEqual(
            protein_gap_band(
                protein_target_g=200,
                protein_remaining_g=60,  # 0.30 × target
                protein_consumed_g=140,
                has_today_protein_log=True,
            ),
            "watch",
        )

    def test_gap_large_remaining(self) -> None:
        self.assertEqual(
            protein_gap_band(
                protein_target_g=180,
                protein_remaining_g=90,  # 0.50 × target
                protein_consumed_g=90,
                has_today_protein_log=True,
            ),
            "gap",
        )

    def test_unknown_without_target(self) -> None:
        self.assertEqual(
            protein_gap_band(
                protein_target_g=None,
                protein_remaining_g=50,
                has_today_protein_log=True,
            ),
            "unknown",
        )

    def test_unknown_other_civil_day(self) -> None:
        self.assertEqual(
            protein_gap_band(
                protein_target_g=180,
                protein_remaining_g=20,
                protein_consumed_g=160,
                has_today_protein_log=True,
                same_civil_day=False,
            ),
            "unknown",
        )

    def test_gap_from_weak_7d_when_no_today(self) -> None:
        self.assertEqual(
            protein_gap_band(
                protein_target_g=180,
                protein_remaining_g=None,
                has_today_protein_log=False,
                protein_adherence_7d_pct=40.0,
            ),
            "gap",
        )


class PacketBuilderTests(unittest.TestCase):
    def _board(
        self,
        *,
        rec: str = "train",
        score: float = 70,
        label: str = "Moderate",
        session_type: str = "push",
        is_rest: bool = False,
        protein_rem: float = 10,
        protein_tgt: float = 180,
        protein_cons: float = 170,
    ) -> dict:
        return {
            "date": "2026-08-10",
            "recommendation": rec,
            "recovery": {"label": label, "score": score},
            "workout": {
                "session_type": session_type,
                "is_rest_day": is_rest,
            },
            "nutrition": {
                "consumed": {"protein_g": protein_cons, "calories": 1800},
                "targets": {"protein_g": protein_tgt, "calories": 2200},
                "remaining": {"protein_g": protein_rem, "calories": 400},
            },
        }

    def test_rest_low_score_packet(self) -> None:
        pkt = build_day_constraints_packet(
            today_board=self._board(rec="rest", score=30, label="Needs Rest", is_rest=True, session_type="rest"),
            recovery=RecoveryStatus(label="Needs Rest", score=30.0, reasons=["low sleep"]),
            workout_plan={"is_rest_day": True, "session_type": "rest", "exercises": []},
            sessions=[],
            sleep=[SleepSample(date="2026-08-10", sleep_hours=5.0)],
            civil_day="2026-08-10",
            as_of="2026-08-10T18:00:00+00:00",
            coach_ok=True,
        )
        self.assertEqual(pkt["train_recommendation"], "rest")
        self.assertEqual(pkt["recovery_score"], 30.0)
        self.assertTrue(pkt["session_due"])  # recovery-forced rest still due
        self.assertFalse(pkt["stale"])
        self.assertIn(pkt["protein_gap_band"], ("ok", "watch", "gap", "unknown"))
        for key in (
            "session_due",
            "session_type",
            "train_recommendation",
            "recovery_label",
            "recovery_score",
            "protein_gap_band",
            "protein_remaining_g",
            "protein_target_g",
            "summary",
            "confidence",
            "deep_link",
            "as_of",
        ):
            self.assertIn(key, pkt)

    def test_never_invent_ready_when_coach_down(self) -> None:
        pkt = build_day_constraints_packet(
            today_board=self._board(rec="train", score=80, label="Ready"),
            recovery={"label": "Ready", "score": 80},
            workout_plan={"is_rest_day": False, "session_type": "pull"},
            coach_ok=False,
            fitness_down=True,
            civil_day="2026-08-10",
            as_of="2026-08-10T18:00:00Z",
        )
        self.assertTrue(pkt["stale"])
        self.assertNotEqual(pkt.get("recovery_label"), "Ready")
        self.assertLessEqual(float(pkt.get("confidence") or 0), 0.3)

    def test_protein_gap_band_gap(self) -> None:
        pkt = build_day_constraints_packet(
            today_board=self._board(
                rec="rest",
                score=35,
                label="Caution",
                is_rest=True,
                session_type="rest",
                protein_rem=100,
                protein_tgt=180,
                protein_cons=80,
            ),
            recovery=RecoveryStatus(label="Caution", score=35.0, reasons=[]),
            workout_plan={"is_rest_day": True, "session_type": "rest"},
            civil_day="2026-08-10",
            as_of="2026-08-10T18:00:00+00:00",
        )
        self.assertEqual(pkt["protein_gap_band"], "gap")
        self.assertEqual(pkt["protein_remaining_g"], 100.0)
        self.assertEqual(pkt["train_recommendation"], "rest")

    def test_session_logged_clears_due(self) -> None:
        sessions = [
            Session(date="2026-08-10", session_type="push", exercises=[]),
        ]
        pkt = build_day_constraints_packet(
            today_board=self._board(rec="train", score=75, label="Ready"),
            recovery=RecoveryStatus(label="Ready", score=75.0, reasons=[]),
            workout_plan={"is_rest_day": False, "session_type": "push"},
            sessions=sessions,
            civil_day="2026-08-10",
            as_of="2026-08-10T18:00:00+00:00",
        )
        self.assertFalse(pkt["session_due"])

    def test_sleep_battery_omitted_when_no_data(self) -> None:
        pkt = build_day_constraints_packet(
            today_board=self._board(),
            sleep_battery={"mode": "no_data", "pct_charged": 0},
            civil_day="2026-08-10",
            as_of="2026-08-10T18:00:00+00:00",
        )
        self.assertNotIn("sleep_battery", pkt)

    def test_sleep_battery_included_when_live(self) -> None:
        pkt = build_day_constraints_packet(
            today_board=self._board(),
            sleep_battery={
                "mode": "awake",
                "pct_charged": 62.0,
                "empty_at": "2026-08-10T23:00:00-05:00",
                "level": "ok",
                "summary": "62%",
            },
            civil_day="2026-08-10",
            as_of="2026-08-10T18:00:00+00:00",
        )
        self.assertIn("sleep_battery", pkt)
        self.assertEqual(pkt["sleep_battery"]["pct_charged"], 62.0)


class WriteAndOrchestraTests(unittest.TestCase):
    def test_write_atomic_json(self) -> None:
        pkt = build_day_constraints_packet(
            today_board={
                "recommendation": "rest",
                "recovery": {"label": "Needs Rest", "score": 25},
                "workout": {"session_type": "rest", "is_rest_day": True},
                "nutrition": {
                    "consumed": {"protein_g": 80},
                    "targets": {"protein_g": 180},
                    "remaining": {"protein_g": 100},
                },
            },
            recovery=RecoveryStatus(label="Needs Rest", score=25.0, reasons=[]),
            workout_plan={"is_rest_day": True, "session_type": "rest"},
            civil_day="2026-08-10",
            as_of="2026-08-10T18:00:00+00:00",
        )
        with tempfile.TemporaryDirectory() as td:
            path = write_day_constraints(td, pkt)
            self.assertTrue(path.is_file())
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["train_recommendation"], "rest")
            self.assertEqual(loaded["recovery_score"], 25.0)
            self.assertEqual(loaded["protein_gap_band"], "gap")

    def test_export_from_dashboard_shape(self) -> None:
        dashboard = {
            "coach": {
                "today": {
                    "recommendation": "easy",
                    "recovery": {"label": "Caution", "score": 48},
                    "workout": {"session_type": "pull", "is_rest_day": False},
                    "nutrition": {
                        "consumed": {"protein_g": 100},
                        "targets": {"protein_g": 180},
                        "remaining": {"protein_g": 80},
                    },
                },
                "adherence_7d": {"protein": {"pct": 70}},
            },
            "recovery": {"label": "Caution", "score": 48, "sparse": False},
            "workout_store": {
                "plan": {"is_rest_day": False, "session_type": "pull", "exercises": []}
            },
            "sleep_battery": {
                "mode": "awake",
                "pct_charged": 55.0,
                "empty_at": "2026-08-10T22:00:00Z",
            },
            "meta": {
                "local_today": "2026-08-10",
                "generated_at": "2026-08-10T18:00:00Z",
                "health_credentials": True,
            },
        }
        with tempfile.TemporaryDirectory() as td:
            pkt = export_day_constraints_from_dashboard(
                dashboard, workspace=td, sessions=[], sleep=[], write=True
            )
            self.assertEqual(pkt["train_recommendation"], "easy")
            path = Path(td) / "fitness" / "data" / "day_constraints.json"
            self.assertTrue(path.is_file())
            disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(disk["session_type"], "pull")

    def test_orchestra_composer_rest_blocks_train(self) -> None:
        """AC5: rest / score<40 packet blocks train in next3 when composer present."""
        monorepo = ROOT.parent
        orch = monorepo / "orchestra"
        if not (orch / "day_plan.py").is_file():
            self.skipTest("orchestra/day_plan.py not in workspace")
        if str(orch) not in sys.path:
            sys.path.insert(0, str(orch))
        from day_plan import compose_day_plan  # type: ignore  # noqa: E402

        pkt = build_day_constraints_packet(
            today_board={
                "recommendation": "rest",
                "recovery": {"label": "Needs Rest", "score": 28},
                "workout": {"session_type": "rest", "is_rest_day": True},
                "nutrition": {
                    "consumed": {"protein_g": 70},
                    "targets": {"protein_g": 180},
                    "remaining": {"protein_g": 110},
                },
            },
            recovery=RecoveryStatus(label="Needs Rest", score=28.0, reasons=[]),
            workout_plan={"is_rest_day": True, "session_type": "rest"},
            civil_day="2026-08-10",
            as_of="2026-08-10T18:00:00+00:00",
        )
        self.assertEqual(pkt["train_recommendation"], "rest")
        self.assertLess(pkt["recovery_score"], 40)
        self.assertEqual(pkt["protein_gap_band"], "gap")

        fit_domain = {
            "id": "fitness",
            "available": True,
            "url": pkt["deep_link"],
            "summary": pkt["summary"],
            "signals": {
                "day": {
                    k: pkt.get(k)
                    for k in (
                        "as_of",
                        "session_due",
                        "session_type",
                        "train_recommendation",
                        "recovery_score",
                        "recovery_label",
                        "protein_gap_band",
                        "protein_remaining_g",
                        "protein_target_g",
                        "protein_as_of",
                        "summary",
                        "confidence",
                        "deep_link",
                    )
                },
                "as_of": pkt["as_of"],
            },
        }
        plan = compose_day_plan(
            [
                {
                    "id": "holistic",
                    "available": True,
                    "url": "http://127.0.0.1:8770/",
                    "signals": {
                        "plan_blocks": [
                            {
                                "id": "sleep",
                                "title": "Sleep",
                                "minutes": 480,
                                "role": "reserve",
                            }
                        ],
                        "free_minutes": 90,
                    },
                },
                {
                    "id": "workflow",
                    "available": True,
                    "url": "http://127.0.0.1:8765/",
                    "signals": {
                        "board": {
                            "as_of": pkt["as_of"],
                            "fetch_ok": True,
                            "ready_count": 0,
                            "ready_top": [],
                            "in_progress": [],
                            "pending_review_count": 0,
                            "blocked": [],
                            "wip_overload": False,
                            "free_agent_count": 0,
                            "pipeline_pressure": "dry",
                            "stale": False,
                        }
                    },
                },
                fit_domain,
                {
                    "id": "finance",
                    "available": True,
                    "url": "http://127.0.0.1:8000/financial-command/",
                    "signals": {
                        "as_of": pkt["as_of"],
                        "stress_overall": "green",
                        "red_mode": False,
                        "free_cash_gate": "allow",
                        "day_actions": [],
                        "freshness": "fresh",
                    },
                },
            ],
            now=None,
        )
        # Force as_of freshness: compose with now matching packet
        from datetime import datetime, timezone

        plan = compose_day_plan(
            [
                {
                    "id": "holistic",
                    "available": True,
                    "url": "http://127.0.0.1:8770/",
                    "signals": {
                        "plan_blocks": [
                            {
                                "id": "sleep",
                                "title": "Sleep",
                                "minutes": 480,
                                "role": "reserve",
                            }
                        ],
                        "free_minutes": 90,
                    },
                },
                {
                    "id": "workflow",
                    "available": True,
                    "url": "http://127.0.0.1:8765/",
                    "signals": {
                        "board": {
                            "as_of": "2026-08-10T18:00:00+00:00",
                            "fetch_ok": True,
                            "ready_count": 0,
                            "ready_top": [],
                            "in_progress": [],
                            "pending_review_count": 0,
                            "blocked": [],
                            "wip_overload": False,
                            "free_agent_count": 0,
                            "pipeline_pressure": "dry",
                            "stale": False,
                        }
                    },
                },
                fit_domain,
                {
                    "id": "finance",
                    "available": True,
                    "url": "http://127.0.0.1:8000/financial-command/",
                    "signals": {
                        "as_of": "2026-08-10T18:00:00+00:00",
                        "stress_overall": "green",
                        "red_mode": False,
                        "free_cash_gate": "allow",
                        "day_actions": [{"kind": "ltv_check", "title": "LTV"}],
                        "freshness": "fresh",
                    },
                },
            ],
            now=datetime(2026, 8, 10, 18, 30, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(plan["sources"]["fitness"].get("rest_blocks_train"))
        self.assertTrue(
            any(g["id"] == "body_rest" and g["severity"] == "block" for g in plan["gates"])
        )
        for item in plan["next3"]:
            self.assertFalse(
                item.get("kind") == "train"
                or "train session" in (item.get("title") or "").lower()
            )
        # Protein gap surfaces as gate or suggested action
        self.assertTrue(
            any(g["id"] == "protein_gap" for g in plan["gates"])
            or any(
                "protein" in (a.get("title") or "").lower()
                for a in plan["sources"]["fitness"].get("suggested_actions") or []
            )
            or any("protein" in (i.get("title") or "").lower() for i in plan["next3"])
        )


if __name__ == "__main__":
    unittest.main()
