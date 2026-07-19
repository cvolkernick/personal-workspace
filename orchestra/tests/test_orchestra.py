"""Unit tests for orchestra aggregation, synergy detection, and priority synthesis.

Drives the real shipped functions (collectors, synergies, priorities, payload)
against temporary workspace fixtures — no mocking of units under test.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "orchestra"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from attention import (  # noqa: E402
    compute_freshness,
    hours_since,
    parse_timestamp,
    synthesize_attention,
)
from collectors import (  # noqa: E402
    collect_all_domains,
    collect_finance,
    collect_fitness,
    collect_holistic,
    collect_iot,
    collect_strategy,
    collect_workflow,
)
from payload import build_orchestra_payload  # noqa: E402
from priorities import synthesize_priorities  # noqa: E402
from recommendations import synthesize_recommendations  # noqa: E402
from synergies import detect_synergies  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_fixture_workspace(base: Path) -> Path:
    """Create a minimal multi-domain workspace under base."""
    _write(
        base / "strategy" / "bets.md",
        """# High-Conviction Bets
- **Energy**
- **Bitcoin**
- **AI**
- **Autonomy**
- **Robotics**
Fitness / Health & Vitality is an enabler for deep work on the bets.
Investment / Wealth Building supports Bitcoin accumulation.
""",
    )
    _write(
        base / "strategy" / "today.md",
        """# Today
## Top Priorities Right Now
- [ ] **Next action from AI/Autonomy leverage initiative** (command center automation)
- [ ] **Fitness / Health enabler action** hit the full PPL session + nutrition
- [ ] **Investment / thematic bet maintenance** review DCA and treasury liquidity
- [x] already done item should be ignored
""",
    )
    _write(
        base / "initiatives" / "build-automation.md",
        """---
title: "Build small automation for leverage"
status: active
linked_bets: ["AI/Autonomy/Robotics"]
priority_impact: high
next_action: "Prototype a script that synthesizes today.md from initiatives"
energy: medium
---

## Description
Advances the AI tooling bet.
""",
    )
    _write(
        base / "initiatives" / "treasury-review.md",
        """---
title: "Weekly treasury review"
status: todo
linked_bets: ["Bitcoin"]
priority_impact: medium
next_action: "Fill Morpho LTV fields and confirm buying power floor"
---
""",
    )
    _write(
        base / "ops" / "backlog" / "items.json",
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "id": "bl-1",
                        "title": "Ship orchestra command center automation",
                        "priority": "high",
                        "status": "in_progress",
                        "area": "orchestra",
                        "notes": "Unify strategy finance fitness dashboards",
                        "tags": ["initiative"],
                    },
                    {
                        "id": "bl-2",
                        "title": "Old done item",
                        "priority": "low",
                        "status": "done",
                        "notes": "",
                    },
                ],
            },
            indent=2,
        ),
    )
    _write(
        base / "ops" / "session-index" / "latest.json",
        json.dumps({"sessions": [{"id": "s1", "title": "Orchestra work"}]}),
    )
    _write(
        base / "treasury" / "snapshots" / "treasury_latest.json",
        json.dumps(
            {
                "snapshot": {
                    "as_of": "2026-07-17T00:00:00+00:00",
                    "coinbase": {
                        "btc_usd_price": 60000,
                        "liquid_btc_usd": 10.5,
                        "liquid_usdc": 100.0,
                    },
                    "robinhood": {
                        "total_value": 200.0,
                        "buying_power": 50.0,
                    },
                },
                "evaluation": {
                    "stress": {"level": "ok"},
                    "actions": [
                        {
                            "priority": 0,
                            "title": "Fill missing Coinbase app fields",
                            "actor": "human",
                        },
                        {
                            "priority": 1,
                            "title": "Confirm Morpho loan LTV",
                            "actor": "human",
                        },
                    ],
                    "next_steps": ["Review RH buying power floor"],
                },
            }
        ),
    )
    _write(
        base / "fitness" / "data" / "health-metrics.json",
        json.dumps(
            {
                "weight": [
                    {"date": "2026-07-10", "weight_lbs": 180.0},
                    {"date": "2026-07-15", "weight_lbs": 179.2},
                ]
            }
        ),
    )
    _write(base / "fitness" / "workouts" / "push.md", "# Push day\n")
    _write(base / "fitness" / "workouts" / "pull.md", "# Pull day\n")
    _write(
        base / "holistic" / "data" / "tasks.json",
        json.dumps(
            {
                "version": 2,
                "items": [],
                "targets": [
                    {"id": "sleep", "title": "Sleep (rolling 7-day avg ≥ 8h)"},
                    {"id": "workout", "title": "Workout"},
                    {"id": "deep-work", "title": "Deep work on AI systems"},
                ],
                "plan": {
                    "blocks": [
                        {"id": "sleep", "title": "Sleep"},
                        {"id": "workout", "title": "Workout"},
                    ]
                },
            }
        ),
    )
    _write(
        base / "iot" / "wiz-lights" / "bulbs.json",
        json.dumps(
            {
                "entryway1": {"ip": "192.168.1.10", "mac": "aabbccddee01"},
                "entryway2": {"ip": "192.168.1.11", "mac": "aabbccddee02"},
                "livingroom1": {"ip": "192.168.1.20", "mac": "aabbccddee03"},
            }
        ),
    )
    _write(
        base / "iot" / "groups.json",
        json.dumps(
            {
                "entryway": {
                    "label": "Entryway",
                    "members": ["entryway1", "entryway2"],
                },
                "livingroom": {
                    "label": "Living room",
                    "members": ["livingroom1"],
                },
            }
        ),
    )
    _write(
        base / "iot" / "schedule.json",
        json.dumps(
            {
                "location": {
                    "latitude": 26.6,
                    "longitude": -81.6,
                    "timezone": "America/New_York",
                    "label": "Test home",
                },
                "routines": [
                    {
                        "id": "sunset_all_on",
                        "enabled": True,
                        "name": "Sunset — all lights on",
                        "trigger": "sunset",
                        "target": "all",
                        "color": "magenta",
                    },
                    {
                        "id": "sunrise_all_off",
                        "enabled": True,
                        "name": "Sunrise — all lights off",
                        "trigger": "sunrise",
                        "target": "all",
                        "color": "off",
                    },
                ],
            }
        ),
    )
    return base


class CollectorsAggregationTests(unittest.TestCase):
    def test_multi_domain_status_aggregation_from_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _build_fixture_workspace(Path(td))
            domains = collect_all_domains(ws, probe_ports=False)
            ids = {d["id"] for d in domains}
            self.assertEqual(
                ids,
                {"strategy", "workflow", "finance", "fitness", "holistic", "iot"},
            )
            by = {d["id"]: d for d in domains}

            self.assertTrue(by["strategy"]["available"])
            self.assertGreaterEqual(by["strategy"]["signals"]["today_count"], 3)
            self.assertIn("Bitcoin", by["strategy"]["signals"]["thematic_bets"])
            self.assertGreaterEqual(len(by["strategy"]["signals"]["initiatives"]), 2)

            self.assertTrue(by["workflow"]["available"])
            self.assertGreaterEqual(by["workflow"]["signals"]["backlog"]["count"], 1)
            self.assertEqual(by["workflow"]["port"], 8765)
            self.assertIn("8765", by["workflow"]["url"])

            self.assertTrue(by["finance"]["available"])
            self.assertEqual(by["finance"]["signals"]["btc_usd_price"], 60000)
            self.assertGreaterEqual(len(by["finance"]["signals"]["action_titles"]), 2)
            self.assertEqual(by["finance"]["port"], 8000)

            self.assertTrue(by["fitness"]["available"])
            self.assertIsNotNone(by["fitness"]["signals"]["latest_weight"])
            self.assertEqual(by["fitness"]["port"], 8787)

            self.assertTrue(by["holistic"]["available"])
            self.assertGreaterEqual(by["holistic"]["signals"]["target_count"], 3)
            self.assertEqual(by["holistic"]["port"], 8770)

            self.assertTrue(by["iot"]["available"])
            self.assertEqual(by["iot"]["signals"]["device_count"], 3)
            self.assertGreaterEqual(by["iot"]["signals"]["routine_count"], 2)
            self.assertEqual(by["iot"]["port"], 8780)
            self.assertIn("8780", by["iot"]["url"])

            # Individual collectors agree with aggregate
            self.assertEqual(collect_strategy(ws)["id"], "strategy")
            self.assertEqual(collect_workflow(ws)["id"], "workflow")
            self.assertEqual(collect_finance(ws)["id"], "finance")
            self.assertEqual(collect_fitness(ws)["id"], "fitness")
            self.assertEqual(collect_holistic(ws)["id"], "holistic")
            self.assertEqual(collect_iot(ws)["id"], "iot")


class SynergyTests(unittest.TestCase):
    def test_connection_overlap_between_two_domains(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _build_fixture_workspace(Path(td))
            domains = collect_all_domains(ws, probe_ports=False)
            strategy = next(d for d in domains if d["id"] == "strategy")
            initiatives = strategy["signals"]["initiatives"]
            today = strategy["signals"]["today_open"]
            synergies = detect_synergies(
                domains, initiatives=initiatives, today_items=today
            )
            self.assertGreaterEqual(len(synergies), 1)
            # At least one item spans two distinct domains
            multi = [s for s in synergies if len(set(s.get("domains") or [])) >= 2]
            self.assertGreaterEqual(len(multi), 1, msg=synergies)
            for s in multi:
                self.assertTrue(s.get("title"))
                self.assertTrue(s.get("detail"))
                self.assertIn(s.get("kind"), ("overlap", "connection", "relationship", "synergy"))

            # Strategy ↔ finance or strategy ↔ workflow should appear given fixture
            pairs = {frozenset(s["domains"]) for s in multi}
            self.assertTrue(
                any(
                    {"strategy", "workflow"}.issubset(p)
                    or {"strategy", "finance"}.issubset(p)
                    or {"fitness", "strategy"}.issubset(p)
                    or {"finance", "strategy"}.issubset(p)
                    or {"iot", "holistic"}.issubset(p)
                    or {"iot", "strategy"}.issubset(p)
                    for p in pairs
                ),
                msg=f"expected cross-domain pair, got {pairs}",
            )
            # IoT fixture should produce at least one iot-linked synergy
            iot_linked = [s for s in multi if "iot" in (s.get("domains") or [])]
            self.assertGreaterEqual(len(iot_linked), 1, msg=synergies)


class PrioritySynthesisTests(unittest.TestCase):
    def test_priorities_from_strategy_initiative_backlog_inputs(self) -> None:
        today = [
            "Ship automation for command center",
            "Fitness enabler PPL session",
        ]
        initiatives = [
            {
                "id": "init-1",
                "title": "Build automation",
                "status": "active",
                "priority_impact": "high",
                "linked_bets": ["AI/Autonomy/Robotics"],
                "next_action": "Write the orchestra synthesizer",
            }
        ]
        backlog = [
            {
                "id": "b1",
                "title": "Groom backlog",
                "priority": "medium",
                "status": "todo",
                "notes": "triage",
                "area": "projects-dashboard",
            }
        ]
        finance_actions = ["Fill Morpho LTV", "Check RH BP floor"]
        pris = synthesize_priorities(
            today_items=today,
            initiatives=initiatives,
            backlog_active=backlog,
            finance_actions=finance_actions,
            fitness_summary="weight 179 lbs; workouts: push, pull",
            holistic_targets=["Sleep", "Workout"],
            synergies=[
                {
                    "title": "Fitness enables deep work",
                    "strength": "high",
                    "domains": ["fitness", "strategy"],
                    "detail": "Energy enabler",
                }
            ],
            limit=15,
        )
        self.assertGreaterEqual(len(pris), 4)
        sources = {p["source"] for p in pris}
        # Must reflect multiple input families
        self.assertTrue(any("today" in (p.get("kind") or "") for p in pris))
        self.assertTrue(any(p.get("kind") == "initiative" for p in pris))
        self.assertTrue(any(p.get("kind") == "finance" for p in pris))
        self.assertTrue(any(p.get("kind") == "backlog" for p in pris))
        for p in pris:
            self.assertTrue(p.get("title"))
            self.assertIn("rank", p)
            self.assertTrue(p.get("domains"))

        # Full payload integration on fixture
        with tempfile.TemporaryDirectory() as td:
            ws = _build_fixture_workspace(Path(td))
            payload = build_orchestra_payload(ws, probe_ports=False)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["service"], "orchestra")
            self.assertGreaterEqual(len(payload["domains"]), 6)
            domain_ids = set(payload["domain_ids"])
            self.assertTrue(
                {"strategy", "workflow", "finance", "fitness", "holistic", "iot"}
                <= domain_ids
            )
            self.assertGreaterEqual(len(payload["synergies"]), 1)
            self.assertTrue(payload["synergies"][0].get("title"))
            self.assertGreaterEqual(len(payload["priorities"]), 1)
            self.assertIn("recommendations", payload)
            self.assertGreaterEqual(len(payload["recommended_actions"]), 1)
            self.assertGreaterEqual(len(payload["action_plan"]), 1)
            # action_plan is primary alias for recommended actions
            self.assertEqual(
                [x.get("id") for x in payload["action_plan"]],
                [x.get("id") for x in payload["recommended_actions"]],
            )
            # Launch pointers for known subordinate ports
            ports = {
                ln.get("port")
                for ln in payload["links"]
                if ln.get("port") is not None
            }
            self.assertIn(8000, ports)
            self.assertIn(8765, ports)
            self.assertIn(8770, ports)
            self.assertIn(8780, ports)
            self.assertIn(8787, ports)
            # meta documents orchestra port
            self.assertEqual(payload["meta"]["subordinate_ports"]["orchestra"], 8790)
            self.assertEqual(
                payload["meta"]["subordinate_ports"]["financial-command"], 8000
            )
            self.assertEqual(payload["meta"]["subordinate_ports"]["iot"], 8780)
            self.assertEqual(payload["meta"].get("primary_output"), "recommendations")
            # unused var silence
            _ = sources


class FreshnessAndAttentionTests(unittest.TestCase):
    """Drive shipped attention/freshness functions and full payload paths."""

    def test_parse_timestamp_and_hours_since(self) -> None:
        now = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
        dt = parse_timestamp("2026-07-17T12:00:00+00:00")
        self.assertIsNotNone(dt)
        age = hours_since("2026-07-17T12:00:00+00:00", now=now)
        self.assertIsNotNone(age)
        self.assertAlmostEqual(age, 24.0, places=2)
        self.assertIsNone(parse_timestamp("not-a-date"))
        self.assertIsNone(hours_since(None, now=now))
        # Z suffix
        age_z = hours_since("2026-07-18T06:00:00Z", now=now)
        self.assertAlmostEqual(age_z, 6.0, places=2)

    def test_compute_freshness_flags_stale_finance_snapshot(self) -> None:
        now = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
        domains = [
            {
                "id": "finance",
                "available": True,
                "status": "ok",
                "signals": {
                    "as_of": (now - timedelta(hours=72)).isoformat(),
                    "source": "treasury/snapshots/treasury_latest.json",
                },
            },
            {
                "id": "workflow",
                "available": True,
                "status": "ok",
                "signals": {
                    "backlog": {
                        "ok": True,
                        "updated_at": (now - timedelta(hours=2)).isoformat(),
                        "source": "ops/backlog/items.json",
                    }
                },
            },
        ]
        fresh = compute_freshness(domains, now=now, stale_hours=48.0)
        self.assertTrue(fresh["has_stale"])
        self.assertGreaterEqual(fresh["stale_count"], 1)
        self.assertIn("finance_snapshot", fresh["stale_ids"])
        by = {s["id"]: s for s in fresh["sources"]}
        self.assertTrue(by["finance_snapshot"]["stale"])
        self.assertFalse(by["backlog"]["stale"])
        self.assertAlmostEqual(by["finance_snapshot"]["age_hours"], 72.0, places=1)

    def test_synthesize_attention_missing_domain_and_stale(self) -> None:
        now = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
        domains = [
            {
                "id": "finance",
                "label": "Finance / Treasury",
                "available": False,
                "status": "missing",
                "summary": "No treasury snapshot found",
                "signals": {},
            },
            {
                "id": "strategy",
                "label": "Strategy",
                "available": True,
                "status": "ok",
                "signals": {"today_count": 0, "today_open": []},
            },
            {
                "id": "workflow",
                "label": "Workflow",
                "available": True,
                "status": "ok",
                "signals": {
                    "backlog": {
                        "ok": True,
                        "updated_at": (now - timedelta(hours=100)).isoformat(),
                        "source": "ops/backlog/items.json",
                    }
                },
            },
        ]
        freshness = compute_freshness(domains, now=now, stale_hours=48.0)
        bridge = {
            "candidates": [
                {"backlog_id": "b1", "title": "Ship feature A"},
                {"backlog_id": "b2", "title": "Ship feature B"},
                {"backlog_id": "b3", "title": "Ship feature C"},
            ]
        }
        priorities = [
            {
                "title": "Do the important thing",
                "domains": ["strategy"],
                "priority": "high",
                "rationale": "from today",
                "source": "strategy/today.md",
            }
        ]
        atts = synthesize_attention(
            domains,
            priorities=priorities,
            bridge=bridge,
            freshness=freshness,
            synergies=[
                {
                    "title": "Fitness enables deep work",
                    "strength": "high",
                    "domains": ["fitness", "strategy"],
                    "detail": "Energy enabler",
                }
            ],
        )
        self.assertGreaterEqual(len(atts), 3)
        kinds = {a["kind"] for a in atts}
        self.assertIn("domain_missing", kinds)
        self.assertIn("stale_source", kinds)
        self.assertIn("bridge_backlog", kinds)
        self.assertIn("empty_today", kinds)
        for a in atts:
            self.assertTrue(a.get("title"))
            self.assertIn("rank", a)
            self.assertIn(a.get("severity"), ("critical", "high", "medium", "low", "info"))
            self.assertTrue(a.get("domains"))

    def test_payload_includes_attention_and_freshness_on_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _build_fixture_workspace(Path(td))
            # Make finance snapshot old so stale flag fires under 48h threshold
            snap_path = ws / "treasury" / "snapshots" / "treasury_latest.json"
            data = json.loads(snap_path.read_text(encoding="utf-8"))
            old = (datetime.now(timezone.utc) - timedelta(hours=96)).isoformat()
            data["snapshot"]["as_of"] = old
            snap_path.write_text(json.dumps(data), encoding="utf-8")

            # Also stamp backlog updated_at as fresh
            bl_path = ws / "ops" / "backlog" / "items.json"
            bl = json.loads(bl_path.read_text(encoding="utf-8"))
            bl["updated_at"] = datetime.now(timezone.utc).isoformat()
            bl_path.write_text(json.dumps(bl), encoding="utf-8")

            payload = build_orchestra_payload(ws, probe_ports=False, stale_hours=48.0)
            self.assertTrue(payload["ok"])
            self.assertIn("attention", payload)
            self.assertIn("freshness", payload)
            self.assertIsInstance(payload["attention"], list)
            self.assertGreaterEqual(len(payload["attention"]), 1)
            self.assertIn("sources", payload["freshness"])
            self.assertTrue(payload["freshness"].get("has_stale"))
            self.assertIn("attention", payload["counts"])
            self.assertIn("stale_sources", payload["counts"])
            self.assertGreaterEqual(payload["counts"]["stale_sources"], 1)
            self.assertIn("synergies_high", payload["counts"])

            finance = next(d for d in payload["domains"] if d["id"] == "finance")
            self.assertTrue(finance.get("stale"))
            self.assertIsNotNone(finance.get("age_hours"))
            self.assertGreater(finance["age_hours"], 48.0)

            kinds = {a["kind"] for a in payload["attention"]}
            self.assertIn("stale_source", kinds)

            # IoT keyword tagging on today items (priorities enhancement)
            pris = synthesize_priorities(
                today_items=["Check home IoT lights and wiz bulbs schedule"],
                limit=5,
            )
            self.assertTrue(pris)
            self.assertIn("iot", pris[0].get("domains") or [])

            # Stale finance should surface in automated recommendations
            rec = payload["recommendations"]
            self.assertTrue(rec.get("summary"))
            self.assertGreaterEqual(len(rec.get("items") or []), 1)
            rec_kinds = {r.get("kind") for r in rec["items"]}
            self.assertTrue(
                "hygiene" in rec_kinds or any(
                    "stale" in (r.get("action") or "").lower()
                    or "stale" in (r.get("title") or "").lower()
                    for r in rec["items"]
                ),
                msg=rec["items"],
            )


class RecommendationsTests(unittest.TestCase):
    """Drive shipped synthesize_recommendations — high focus + medium fallback."""

    def test_high_synergies_produce_synergy_actions(self) -> None:
        rec = synthesize_recommendations(
            domains=[
                {"id": "strategy", "available": True, "status": "ok"},
                {"id": "finance", "available": True, "status": "ok"},
            ],
            priorities=[
                {
                    "id": "pri-1",
                    "title": "Ship automation",
                    "priority": "high",
                    "kind": "today",
                    "domains": ["strategy", "workflow"],
                    "rationale": "from today",
                    "source": "strategy/today.md",
                    "rank": 1,
                }
            ],
            attention=[],
            synergies=[
                {
                    "id": "syn-1",
                    "title": "Treasury actions support wealth / Bitcoin leg",
                    "strength": "high",
                    "kind": "connection",
                    "domains": ["finance", "strategy"],
                    "detail": "Open treasury actions protect liquidity.",
                }
            ],
            bridge={"candidates": []},
            freshness={"stale_count": 0},
            limit=8,
        )
        self.assertEqual(rec["mode"], "high_focus")
        self.assertEqual(rec["high_synergy_count"], 1)
        self.assertGreaterEqual(len(rec["items"]), 2)
        self.assertTrue(rec["summary"])
        kinds = {i["kind"] for i in rec["items"]}
        self.assertIn("synergy", kinds)
        self.assertIn("focus", kinds)
        for item in rec["items"]:
            self.assertTrue(item.get("action"))
            self.assertTrue(item.get("why"))
            self.assertTrue(item.get("automated"))
            self.assertIn("rank", item)
        # Focus is top N
        self.assertEqual(len(rec["focus"]), min(3, len(rec["items"])))

    def test_no_high_synergies_falls_back_to_medium(self) -> None:
        rec = synthesize_recommendations(
            domains=[{"id": "strategy", "available": True, "status": "ok"}],
            priorities=[
                {
                    "title": "Groom backlog",
                    "priority": "medium",
                    "kind": "backlog",
                    "domains": ["workflow"],
                    "rationale": "triage",
                    "source": "ops/backlog",
                }
            ],
            attention=[],
            synergies=[
                {
                    "id": "syn-m",
                    "title": "Shared theme: Time/Focus",
                    "strength": "medium",
                    "kind": "overlap",
                    "domains": ["strategy", "holistic"],
                    "detail": "Theme appears in two domains.",
                }
            ],
            bridge={"candidates": []},
            freshness={},
        )
        self.assertEqual(rec["mode"], "fallback_medium")
        self.assertEqual(rec["high_synergy_count"], 0)
        self.assertTrue(rec.get("deferred_note"))
        kinds = {i["kind"] for i in rec["items"]}
        self.assertIn("fallback", kinds)
        self.assertTrue(any(i.get("related", {}).get("fallback") for i in rec["items"] if i.get("kind") == "fallback"))

    def test_hygiene_first_when_stale_attention(self) -> None:
        rec = synthesize_recommendations(
            domains=[{"id": "finance", "available": True, "status": "ok"}],
            priorities=[],
            attention=[
                {
                    "id": "att-1",
                    "title": "Stale data: Treasury snapshot",
                    "severity": "high",
                    "kind": "stale_source",
                    "domains": ["finance"],
                    "detail": "96h old",
                }
            ],
            synergies=[],
            bridge={},
            freshness={"stale_count": 1},
        )
        self.assertGreaterEqual(len(rec["items"]), 1)
        self.assertEqual(rec["items"][0]["kind"], "hygiene")
        self.assertEqual(rec["mode"], "hygiene_first")
        self.assertIn("Refresh", rec["items"][0]["action"])

    def test_payload_recommendations_on_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _build_fixture_workspace(Path(td))
            payload = build_orchestra_payload(ws, probe_ports=False)
            rec = payload["recommendations"]
            self.assertIn(rec["mode"], ("high_focus", "fallback_medium", "hygiene_first", "thin_data"))
            self.assertGreaterEqual(len(payload["recommended_actions"]), 1)
            self.assertEqual(
                payload["counts"]["recommendations"],
                len(payload["recommended_actions"]),
            )
            # Fixture has fitness + today + finance → expect high synergies path typically
            self.assertIsInstance(rec["high_synergy_count"], int)
            for item in payload["recommended_actions"]:
                self.assertTrue(item.get("action"))
                self.assertTrue(item.get("title"))


if __name__ == "__main__":
    unittest.main()
