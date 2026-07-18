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
from synergies import detect_synergies  # noqa: E402


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
            self.assertGreaterEqual(len(payload["action_plan"]), 1)
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
            # unused var silence
            _ = sources


if __name__ == "__main__":
    unittest.main()
