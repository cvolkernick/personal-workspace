"""P1 tests for unitary day_plan composer + collector packet fields.

Fixtures only — no live child servers / HTTP.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "orchestra"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors import (  # noqa: E402
    collect_finance,
    collect_fitness,
    collect_holistic,
    collect_workflow,
)
from day_plan import (  # noqa: E402
    compose_day_plan,
    finance_freshness_tier,
)
from payload import build_orchestra_payload  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: object) -> None:
    _write(path, json.dumps(data, indent=2))


NOW = datetime(2026, 8, 10, 18, 0, 0, tzinfo=timezone.utc)


def _wall_as_of(*, hours_ago: float) -> str:
    """as_of relative to wall clock — collect_finance ages against now(), not fixture NOW."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _isolate_fcc_worktrees(worktree_root: Path | None = None):
    """Prevent collect_finance from reading the live ~/…/treasury worktree."""
    empty = tempfile.TemporaryDirectory()
    empty_path = Path(empty.name) / "no-worktrees"
    empty_path.mkdir(parents=True, exist_ok=True)
    env = {
        "PERSONAL_WORKSPACE_WORKTREES": str(empty_path),
        "FCC_WORKTREE_ROOT": str(worktree_root) if worktree_root else "",
    }
    return mock.patch.dict(os.environ, env, clear=False), empty


def _finance_domain(
    *,
    as_of: str,
    stress_overall: str = "green",
    actions: list | None = None,
    dca: dict | None = None,
    red_mode: bool | None = None,
) -> dict:
    age_tier = finance_freshness_tier(as_of, now=NOW)
    known = age_tier["freshness"] in ("fresh", "soft_stale")
    rm = red_mode
    if rm is None and known:
        rm = stress_overall == "red"
    fcg = "unknown"
    if known:
        fcg = "block_new_risk" if rm else "allow"
    return {
        "id": "finance",
        "available": True,
        "url": "http://127.0.0.1:8000/financial-command/",
        "signals": {
            "as_of": as_of,
            "stress_overall": stress_overall if known else "unknown",
            "stress": stress_overall,
            "dca": dca or {},
            "red_mode": rm if known else None,
            "red_mode_reasons": ["stress_overall_red"] if rm else [],
            "free_cash_gate": fcg,
            "day_actions": actions
            or [
                {"kind": "ltv_check", "title": "Confirm Morpho LTV", "detail": "check"},
                {
                    "kind": "dca",
                    "title": "Deploy free cash DCA buy",
                    "detail": "risk",
                },
                {"kind": "fill_manual", "title": "Fill missing Coinbase fields"},
            ],
            "freshness": age_tier["freshness"],
            "age_hours": age_tier["age_hours"],
        },
    }


def _fit_domain(**day_fields: object) -> dict:
    day = {
        "as_of": NOW.isoformat(),
        "session_due": True,
        "session_type": "push",
        "train_recommendation": "train",
        "recovery_score": 70,
        "recovery_label": "Ready",
        "protein_gap_band": "ok",
        "protein_remaining_g": 10,
        "protein_target_g": 180,
        "protein_as_of": NOW.isoformat(),
        **day_fields,
    }
    return {
        "id": "fitness",
        "available": True,
        "url": "http://127.0.0.1:8787/",
        "signals": {"day": day, "as_of": day.get("as_of")},
    }


def _wf_domain(**board_fields: object) -> dict:
    board = {
        "as_of": NOW.isoformat(),
        "fresh_for_hours": 4,
        "fetch_ok": True,
        "stale": False,
        "ready_count": 2,
        "ready_top": [{"number": 92, "title": "day plan", "size": "M"}],
        "in_progress": [],
        "pending_review_count": 0,
        "blocked": [],
        "wip_overload": False,
        "free_agent_count": 2,
        "pipeline_pressure": "ok",
        "summary": "Ready 2 · IP 0",
        **board_fields,
    }
    return {
        "id": "workflow",
        "available": True,
        "url": "http://127.0.0.1:8765/",
        "signals": {"board": board},
    }


def _hol_domain() -> dict:
    return {
        "id": "holistic",
        "available": True,
        "url": "http://127.0.0.1:8770/",
        "signals": {
            "targets": ["Sleep (rolling 7-day avg ≥ 8h)", "Walk Duchess", "Deep work"],
            "target_objects": [
                {"id": "sleep", "title": "Sleep", "reserve_minutes": 480},
                {"id": "duchess-walk", "title": "Walk Duchess", "reserve_minutes": 45},
            ],
            "plan_blocks": [
                {
                    "id": "sleep",
                    "title": "Sleep",
                    "minutes": 480,
                    "role": "reserve",
                    "kind": "rolling_avg",
                },
                {
                    "id": "duchess-walk",
                    "title": "Walk Duchess",
                    "minutes": 45,
                    "role": "fixed",
                    "kind": "daily_duration",
                },
                {
                    "id": "deep-work",
                    "title": "Deep work",
                    "minutes": 120,
                    "role": "work",
                    "kind": "capacity",
                },
            ],
            "free_minutes": 90,
            "sleep_reserve_minutes": 480,
        },
    }


class FinanceTierTests(unittest.TestCase):
    def test_dual_tier(self) -> None:
        fresh_as = (NOW - timedelta(hours=2)).isoformat()
        soft_as = (NOW - timedelta(hours=12)).isoformat()
        hard_as = (NOW - timedelta(hours=72)).isoformat()
        self.assertEqual(finance_freshness_tier(fresh_as, now=NOW)["freshness"], "fresh")
        soft = finance_freshness_tier(soft_as, now=NOW)
        self.assertEqual(soft["freshness"], "soft_stale")
        self.assertTrue(soft["stale"])
        hard = finance_freshness_tier(hard_as, now=NOW)
        self.assertEqual(hard["freshness"], "unknown")
        self.assertTrue(hard["unknown"])
        self.assertEqual(finance_freshness_tier(None, now=NOW)["freshness"], "unknown")


class DayPlanComposerTests(unittest.TestCase):
    def test_shape_and_next3_cap(self) -> None:
        plan = compose_day_plan(
            [
                _hol_domain(),
                _wf_domain(),
                _fit_domain(),
                _finance_domain(as_of=(NOW - timedelta(hours=1)).isoformat()),
            ],
            now=NOW,
        )
        self.assertEqual(plan["schema_version"], 1)
        self.assertIn("generated_at", plan)
        self.assertIn("summary", plan)
        self.assertIsInstance(plan["next3"], list)
        self.assertLessEqual(len(plan["next3"]), 3)
        self.assertIsInstance(plan["blocks"], list)
        self.assertIsInstance(plan["gates"], list)
        for key in ("holistic", "workflow", "fitness", "finance"):
            self.assertIn(key, plan["sources"])
            src = plan["sources"][key]
            self.assertEqual(src["domain"], key)
            self.assertIn("as_of", src)
            self.assertIn("stale", src)
            self.assertIn("deep_link", src)
        # Holistic spine
        kinds = {b["kind"] for b in plan["blocks"]}
        self.assertTrue({"sleep", "fixed"} & kinds or len(plan["blocks"]) >= 2)
        free = plan["sources"]["holistic"].get("free_minutes")
        self.assertEqual(free, 90)

    def test_finance_hard_unknown(self) -> None:
        old = (NOW - timedelta(hours=96)).isoformat()
        plan = compose_day_plan(
            [
                _hol_domain(),
                _wf_domain(),
                _fit_domain(session_due=False),
                _finance_domain(
                    as_of=old,
                    stress_overall="green",
                    actions=[
                        {"kind": "dca", "title": "Buy more BTC with free cash"},
                        {"kind": "ltv_check", "title": "LTV check"},
                    ],
                ),
            ],
            now=NOW,
        )
        fin = plan["sources"]["finance"]
        self.assertEqual(fin["freshness"], "unknown")
        self.assertTrue(fin["stale"])
        self.assertIsNone(fin["red_mode"])
        self.assertEqual(fin["free_cash_gate"], "unknown")
        gate_ids = {g["id"] for g in plan["gates"]}
        self.assertIn("capital_freshness", gate_ids)
        # No free-dollar risk in next3; refresh allowed
        for item in plan["next3"]:
            title = (item.get("title") or "").lower()
            self.assertNotIn("buy more btc", title)
            self.assertNotIn("free cash", title)
        self.assertTrue(
            any(
                "refresh" in (i.get("title") or "").lower()
                or i.get("kind") == "refresh"
                for i in plan["next3"] + plan["sources"]["finance"]["suggested_actions"]
            )
        )

    def test_finance_red_mode_excludes_free_dollar(self) -> None:
        as_of = (NOW - timedelta(hours=1)).isoformat()
        plan = compose_day_plan(
            [
                _hol_domain(),
                _wf_domain(),
                _fit_domain(session_due=False, protein_gap_band="ok"),
                _finance_domain(
                    as_of=as_of,
                    stress_overall="red",
                    red_mode=True,
                    actions=[
                        {"kind": "dca", "title": "Deploy free cash DCA buy"},
                        {"kind": "ltv_check", "title": "Confirm Morpho LTV"},
                        {"kind": "card_float", "title": "Top up card float"},
                    ],
                ),
            ],
            now=NOW,
        )
        fin = plan["sources"]["finance"]
        self.assertTrue(fin["red_mode"])
        self.assertEqual(fin["free_cash_gate"], "block_new_risk")
        sev = {
            g["id"]: g["severity"]
            for g in plan["gates"]
            if g["domain"] == "finance"
        }
        self.assertEqual(sev.get("capital_red_mode"), "block")
        titles = " ".join(i["title"].lower() for i in plan["next3"])
        self.assertNotIn("deploy free cash", titles)
        # whitelist still eligible
        all_titles = " ".join(
            i["title"].lower()
            for i in plan["next3"] + plan["sources"]["finance"]["suggested_actions"]
        )
        self.assertTrue("ltv" in all_titles or "card float" in all_titles)

    def test_finance_soft_stale_warn(self) -> None:
        as_of = (NOW - timedelta(hours=12)).isoformat()
        plan = compose_day_plan(
            [
                _hol_domain(),
                _wf_domain(),
                _fit_domain(session_due=False),
                _finance_domain(as_of=as_of, stress_overall="yellow"),
            ],
            now=NOW,
        )
        fin = plan["sources"]["finance"]
        self.assertEqual(fin["freshness"], "soft_stale")
        self.assertTrue(fin["stale"])
        capital_gates = [g for g in plan["gates"] if g["domain"] == "finance"]
        self.assertTrue(capital_gates)
        self.assertTrue(
            any(g["severity"] in ("warn", "block") and g.get("stale") for g in capital_gates)
        )

    def test_fitness_rest_blocks_train(self) -> None:
        plan = compose_day_plan(
            [
                _hol_domain(),
                _wf_domain(ready_count=0, free_agent_count=1, pipeline_pressure="dry"),
                _fit_domain(
                    train_recommendation="rest",
                    recovery_score=30,
                    session_due=True,
                    protein_gap_band="ok",
                ),
                _finance_domain(as_of=(NOW - timedelta(hours=1)).isoformat()),
            ],
            now=NOW,
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

    def test_fitness_protein_gap_candidate(self) -> None:
        plan = compose_day_plan(
            [
                _hol_domain(),
                _wf_domain(ready_count=0, free_agent_count=0, in_progress=[]),
                _fit_domain(
                    session_due=False,
                    train_recommendation="rest",
                    recovery_score=35,
                    protein_gap_band="gap",
                    protein_remaining_g=90,
                    protein_target_g=180,
                ),
                _finance_domain(
                    as_of=(NOW - timedelta(hours=1)).isoformat(),
                    actions=[{"kind": "ltv_check", "title": "LTV"}],
                ),
            ],
            now=NOW,
        )
        titles = [i["title"].lower() for i in plan["next3"]]
        self.assertTrue(
            any("protein" in t for t in titles)
            or any(
                "protein" in (a.get("title") or "").lower()
                for a in plan["sources"]["fitness"]["suggested_actions"]
            )
        )
        self.assertTrue(any(g["id"] == "protein_gap" for g in plan["gates"]))

    def test_workflow_stale_unknown_not_pretty_zeros(self) -> None:
        plan = compose_day_plan(
            [
                _hol_domain(),
                _wf_domain(fetch_ok=False, ready_count=None, stale=True, as_of=None),
                _fit_domain(session_due=False),
                _finance_domain(as_of=(NOW - timedelta(hours=1)).isoformat()),
            ],
            now=NOW,
        )
        wf = plan["sources"]["workflow"]
        self.assertTrue(wf["stale"])
        self.assertEqual(wf["confidence"], 0.0)
        # Must not invent Ready 0 as healthy
        self.assertTrue(
            any(
                g["domain"] == "workflow" and g["severity"] in ("unknown", "warn")
                for g in plan["gates"]
            )
        )
        self.assertIsNone(wf.get("ready_count"))

    def test_workflow_wip_overload_gate(self) -> None:
        plan = compose_day_plan(
            [
                _hol_domain(),
                _wf_domain(
                    wip_overload=True,
                    in_progress=[
                        {
                            "number": 1,
                            "title": "A",
                            "primary_owner": "Forge",
                        },
                        {
                            "number": 2,
                            "title": "B",
                            "primary_owner": "Forge",
                        },
                    ],
                    free_agent_count=0,
                    ready_count=3,
                ),
                _fit_domain(session_due=False),
                _finance_domain(as_of=(NOW - timedelta(hours=1)).isoformat()),
            ],
            now=NOW,
        )
        self.assertTrue(
            any(
                g["id"] == "wip_overload" and g["severity"] == "block"
                for g in plan["gates"]
            )
        )
        # no pull Ready while overloaded
        for item in plan["next3"]:
            self.assertNotEqual(item.get("kind"), "ready")


class CollectorDayFieldsTests(unittest.TestCase):
    def test_collect_finance_reads_stress_overall(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            # collect_finance ages against wall clock — keep as_of within fresh tier (≤6h)
            as_of = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            _write_json(
                ws / "treasury" / "snapshots" / "treasury_latest.json",
                {
                    "snapshot": {
                        "as_of": as_of,
                        "coinbase": {"btc_usd_price": 1, "liquid_usdc": 10},
                        "robinhood": {"total_value": 1, "buying_power": 5},
                    },
                    "evaluation": {
                        "stress": {
                            "overall": "red",
                            "coinbase_ltv": "unknown",
                            "coinbase_liquid": "yellow",
                            "coinbase_card": "green",
                            "robinhood": "green",
                            "data_quality": "yellow",
                        },
                        "dca": {
                            "allow_dca": False,
                            "throttle": "pause",
                            "reason": "buying power below floor",
                            "margin_use": None,
                        },
                        "buckets": {"working_usdc": 10, "shortfall": 0},
                        "actions": [
                            {
                                "kind": "fill_manual",
                                "title": "Fill missing fields",
                                "priority": 0,
                            },
                            {
                                "kind": "ltv_check",
                                "title": "Confirm LTV",
                                "priority": 1,
                            },
                        ],
                    },
                },
            )
            patcher, empty = _isolate_fcc_worktrees()
            with patcher:
                try:
                    fin = collect_finance(ws)
                finally:
                    empty.cleanup()
            sig = fin["signals"]
            self.assertEqual(sig.get("stress_overall"), "red")
            self.assertTrue(sig.get("red_mode"))
            self.assertEqual(sig.get("free_cash_gate"), "block_new_risk")
            self.assertEqual(sig.get("freshness"), "fresh")
            kinds = {a.get("kind") for a in sig.get("day_actions") or []}
            self.assertIn("fill_manual", kinds)

    def test_collect_finance_prefers_worktree_over_stale_monorepo(self) -> None:
        """Worktree FCC path wins even when monorepo snapshot exists (dual-SoT)."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            ws = base / "monorepo"
            wt = base / "treasury-wt"
            monorepo_as = _wall_as_of(hours_ago=300)
            worktree_as = _wall_as_of(hours_ago=2)
            # Stale monorepo: looks "green" if naively trusted
            _write_json(
                ws / "treasury" / "snapshots" / "treasury_latest.json",
                {
                    "snapshot": {
                        "as_of": monorepo_as,
                        "coinbase": {"btc_usd_price": 1},
                        "robinhood": {},
                    },
                    "evaluation": {
                        "stress": {"overall": "green"},
                        "dca": {},
                        "buckets": {},
                        "actions": [],
                    },
                },
            )
            _write_json(
                wt / "financial-command" / "treasury_latest.json",
                {
                    "snapshot": {
                        "as_of": worktree_as,
                        "coinbase": {"btc_usd_price": 99},
                        "robinhood": {"buying_power": 10},
                    },
                    "evaluation": {
                        "stress": {
                            "overall": "red",
                            "coinbase_liquid": "red",
                            "coinbase_card": "red",
                        },
                        "dca": {
                            "allow_dca": False,
                            "throttle": "ok",
                            "reason": "",
                        },
                        "buckets": {"working_usdc": 50, "shortfall": 100},
                        "actions": [
                            {
                                "kind": "card_float",
                                "title": "Fill card float",
                                "priority": 1,
                            }
                        ],
                    },
                },
            )
            with mock.patch.dict(
                os.environ,
                {
                    "FCC_WORKTREE_ROOT": str(wt),
                    # Isolate from real ~/personal-workspace-worktrees
                    "PERSONAL_WORKSPACE_WORKTREES": str(base / "no-such-worktrees"),
                },
                clear=False,
            ):
                fin = collect_finance(ws)
            sig = fin["signals"]
            self.assertEqual(sig.get("btc_usd_price"), 99)
            self.assertEqual(sig.get("stress_overall"), "red")
            self.assertTrue(sig.get("red_mode"))
            self.assertEqual(sig.get("free_cash_gate"), "block_new_risk")
            self.assertEqual(sig.get("freshness"), "fresh")
            # Source should point at worktree (absolute or containing financial-command)
            src = str(sig.get("source") or "")
            self.assertIn("financial-command", src)
            self.assertNotIn("snapshots/treasury_latest", src.replace("\\", "/"))

    def test_collect_finance_hard_stale_monorepo_never_green(self) -> None:
        """Monorepo-only hard-stale snapshot must not paint stress green."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            ws = base / "monorepo"
            hard_as = _wall_as_of(hours_ago=72)
            _write_json(
                ws / "financial-command" / "treasury_latest.json",
                {
                    "snapshot": {
                        "as_of": hard_as,
                        "coinbase": {"btc_usd_price": 1},
                        "robinhood": {},
                    },
                    "evaluation": {
                        "stress": {"overall": "green"},
                        "dca": {},
                        "buckets": {},
                        "actions": [{"kind": "ltv_check", "title": "Check LTV"}],
                    },
                },
            )
            patcher, empty = _isolate_fcc_worktrees()
            with patcher:
                try:
                    fin = collect_finance(ws)
                finally:
                    empty.cleanup()
            sig = fin["signals"]
            self.assertEqual(sig.get("freshness"), "unknown")
            self.assertEqual(sig.get("stress_overall"), "unknown")
            self.assertIsNone(sig.get("red_mode"))
            self.assertEqual(sig.get("free_cash_gate"), "unknown")
            self.assertTrue(sig.get("fcc_stale"))
            # Must not surface raw green from hard-stale SoT
            self.assertNotEqual(sig.get("stress_overall"), "green")
            self.assertIn("unknown", (fin.get("summary") or "").lower())

    def test_collect_holistic_full_blocks_and_free_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _write_json(
                ws / "holistic" / "data" / "tasks.json",
                {
                    "targets": [
                        {"id": "sleep", "title": "Sleep", "reserve_minutes": 480},
                        {"id": "duchess-walk", "title": "Walk Duchess", "minutes": 45},
                    ],
                    "items": [],
                    "plan": {
                        "sleep_reserve_minutes": 480,
                        "unallocated_active_minutes": 75,
                        "blocks": [
                            {
                                "id": "sleep",
                                "title": "Sleep",
                                "minutes": 480,
                                "role": "reserve",
                            },
                            {
                                "id": "duchess-walk",
                                "title": "Walk Duchess",
                                "minutes": 45,
                                "role": "fixed",
                            },
                        ],
                    },
                },
            )
            hol = collect_holistic(ws)
            sig = hol["signals"]
            self.assertEqual(sig.get("free_minutes"), 75)
            self.assertIsInstance(sig.get("plan_blocks"), list)
            self.assertIsInstance(sig["plan_blocks"][0], dict)
            self.assertEqual(sig["plan_blocks"][0].get("id"), "sleep")

    def test_collect_workflow_and_fitness_packet_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _write_json(ws / "ops" / "backlog" / "items.json", {"items": []})
            _write_json(
                ws / "ops" / "board" / "day_constraints.json",
                {
                    "as_of": NOW.isoformat(),
                    "fetch_ok": True,
                    "ready_count": 1,
                    "ready_top": [{"number": 92, "title": "day plan"}],
                    "in_progress": [],
                    "pending_review_count": 0,
                    "blocked": [],
                    "wip_overload": False,
                    "free_agent_count": 1,
                    "pipeline_pressure": "ok",
                    "summary": "Ready 1 · IP 0",
                },
            )
            _write_json(
                ws / "fitness" / "data" / "day_constraints.json",
                {
                    "as_of": NOW.isoformat(),
                    "session_due": True,
                    "session_type": "pull",
                    "train_recommendation": "easy",
                    "recovery_score": 50,
                    "recovery_label": "Caution",
                    "protein_gap_band": "watch",
                    "protein_remaining_g": 40,
                    "protein_target_g": 180,
                },
            )
            wf = collect_workflow(ws)
            self.assertEqual(wf["signals"]["board"].get("ready_count"), 1)
            self.assertEqual(wf["signals"]["board"].get("sot"), "buzz-board-project-1")
            self.assertTrue(wf["signals"]["backlog"].get("not_board_status"))
            self.assertEqual(wf["signals"]["backlog"].get("role"), "session_hint")
            # Summary leads with Board packet, not "N active backlog" Ready fiction
            self.assertIn("Ready 1", wf["summary"])
            self.assertNotIn("active backlog", wf["summary"])
            self.assertEqual(wf["status"], "ok")
            self.assertTrue(
                any("day_constraints.json" in s for s in (wf.get("sources") or []))
            )
            fit = collect_fitness(ws)
            self.assertEqual(fit["signals"]["day"].get("train_recommendation"), "easy")

    def test_collect_workflow_backlog_alone_not_board_ok(self) -> None:
        """ops/backlog alone must not paint Board Ready / ok work status."""
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _write_json(
                ws / "ops" / "backlog" / "items.json",
                {
                    "items": [
                        {
                            "id": "b1",
                            "title": "not a board card",
                            "status": "open",
                            "priority": "high",
                        }
                    ]
                },
            )
            wf = collect_workflow(ws)
            self.assertEqual(wf["signals"]["board"], {})
            self.assertTrue(wf["signals"]["backlog"].get("not_board_status"))
            self.assertIn("Board unknown", wf["summary"])
            self.assertNotIn("active backlog", wf["summary"])
            self.assertIn("not Board Status", wf["summary"])
            self.assertIn(wf["status"], ("partial", "missing"))


class PayloadDayPlanTests(unittest.TestCase):
    def test_payload_includes_day_plan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            # Minimal multi-domain fixture
            _write(
                ws / "strategy" / "today.md",
                "# Today\n- [ ] **Ship day plan**\n",
            )
            _write(ws / "strategy" / "bets.md", "# Bets\n- **AI**\n")
            _write_json(ws / "ops" / "backlog" / "items.json", {"items": []})
            _write_json(
                ws / "ops" / "board" / "day_constraints.json",
                {
                    "as_of": NOW.isoformat(),
                    "fetch_ok": True,
                    "ready_count": 1,
                    "ready_top": [{"number": 92, "title": "unitary daily planner"}],
                    "in_progress": [],
                    "pending_review_count": 0,
                    "blocked": [],
                    "wip_overload": False,
                    "free_agent_count": 1,
                    "pipeline_pressure": "ok",
                    "summary": "Ready 1",
                },
            )
            _write_json(
                ws / "treasury" / "snapshots" / "treasury_latest.json",
                {
                    "snapshot": {
                        "as_of": _wall_as_of(hours_ago=72),
                        "coinbase": {"btc_usd_price": 1},
                        "robinhood": {},
                    },
                    "evaluation": {
                        "stress": {"overall": "green"},
                        "actions": [
                            {"kind": "ltv_check", "title": "LTV"},
                        ],
                    },
                },
            )
            # (continued below — worktree isolation wraps collect/payload)
            _write_json(
                ws / "fitness" / "data" / "day_constraints.json",
                {
                    "as_of": NOW.isoformat(),
                    "session_due": False,
                    "train_recommendation": "rest",
                    "recovery_score": 25,
                    "protein_gap_band": "gap",
                    "protein_remaining_g": 100,
                    "protein_target_g": 180,
                    "protein_as_of": NOW.isoformat(),
                },
            )
            _write_json(
                ws / "holistic" / "data" / "tasks.json",
                {
                    "targets": [
                        {"id": "sleep", "title": "Sleep"},
                        {"id": "duchess-walk", "title": "Walk Duchess"},
                    ],
                    "items": [],
                    "plan": {
                        "unallocated_active_minutes": 60,
                        "sleep_reserve_minutes": 480,
                        "blocks": [
                            {
                                "id": "sleep",
                                "title": "Sleep",
                                "minutes": 480,
                                "role": "reserve",
                            },
                            {
                                "id": "duchess-walk",
                                "title": "Walk Duchess",
                                "minutes": 45,
                                "role": "fixed",
                            },
                        ],
                    },
                },
            )
            patcher, empty = _isolate_fcc_worktrees()
            with patcher:
                try:
                    payload = build_orchestra_payload(ws, probe_ports=False)
                finally:
                    empty.cleanup()
            self.assertTrue(payload["ok"])
            self.assertIn("day_plan", payload)
            dp = payload["day_plan"]
            self.assertLessEqual(len(dp["next3"]), 3)
            self.assertIn("gates", dp)
            self.assertIn("blocks", dp)
            self.assertIn("sources", dp)
            # hard-unknown finance from 72h as_of (monorepo only; live worktree isolated)
            self.assertEqual(dp["sources"]["finance"]["freshness"], "unknown")
            self.assertIn("day_plan_next3", payload["counts"])


if __name__ == "__main__":
    unittest.main()
