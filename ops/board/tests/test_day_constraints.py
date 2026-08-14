"""P3-W Board day constraints exporter tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BOARD_PKG = Path(__file__).resolve().parents[1]
# monorepo root on path for ops.board imports via file path
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BOARD_PKG.parent) not in sys.path:
    sys.path.insert(0, str(BOARD_PKG.parent))

from board.day_constraints import (  # noqa: E402
    FRESH_FOR_HOURS,
    build_day_constraints_packet,
    build_fetch_failed_packet,
    compute_wip_overload,
    day_constraints_path,
    pipeline_pressure,
    resolve_primary_owner,
    write_day_constraints,
)

NOW = datetime(2026, 8, 10, 20, 0, 0, tzinfo=timezone.utc)


def _item(
    number: int,
    title: str,
    status: str,
    *,
    owner: str | None = None,
    assignees: list | None = None,
    body: str = "",
    state: str = "OPEN",
    labels: list | None = None,
) -> dict:
    return {
        "number": number,
        "title": title,
        "status": status,
        "kind": "Issue",
        "state": state,
        "owner": owner,
        "assignees": assignees or [],
        "body": body,
        "labels": labels or [],
    }


class ResolveOwnerTests(unittest.TestCase):
    def test_overlay_owner(self) -> None:
        owner = resolve_primary_owner(
            _item(1, "x", "In Progress"),
            overlay={"owner": "Forge"},
            roster=["Forge", "Grok"],
        )
        self.assertEqual(owner, "Forge")

    def test_body_owner_line(self) -> None:
        owner = resolve_primary_owner(
            _item(
                2,
                "y",
                "In Progress",
                body="## Summary\n**Owner:** Frankenfit (+ Forge if orchestra-only)\n",
            ),
            roster=["Forge", "Frankenfit", "Grok"],
        )
        self.assertEqual(owner, "Frankenfit")


class PipelineAndWipTests(unittest.TestCase):
    def test_pipeline_dry(self) -> None:
        self.assertEqual(
            pipeline_pressure(
                ready_count=0,
                free_agent_count=2,
                pending_review_count=0,
                in_progress_count=0,
            ),
            "dry",
        )

    def test_pipeline_stuck(self) -> None:
        self.assertEqual(
            pipeline_pressure(
                ready_count=3,
                free_agent_count=0,
                pending_review_count=0,
                in_progress_count=2,
            ),
            "stuck",
        )

    def test_wip_overload(self) -> None:
        self.assertTrue(
            compute_wip_overload(
                [
                    {"primary_owner": "Forge"},
                    {"primary_owner": "Forge"},
                ]
            )
        )
        self.assertFalse(
            compute_wip_overload(
                [
                    {"primary_owner": "Forge"},
                    {"primary_owner": "Grok"},
                ]
            )
        )


class PacketShapeTests(unittest.TestCase):
    def test_fields_and_ready_top_cap(self) -> None:
        items = [
            _item(10, "A", "Ready"),
            _item(11, "B", "Ready"),
            _item(12, "C", "Ready"),
            _item(13, "D", "Ready"),
            _item(20, "Work", "In Progress", owner="Forge"),
            _item(30, "PR", "Pending Review", owner="Grok"),
        ]
        pkt = build_day_constraints_packet(
            items,
            agents=[
                {"name": "Forge", "seat": "implement"},
                {"name": "Grok", "seat": "gate"},
                {"name": "Meridian", "seat": "implement"},
            ],
            overlays={20: {"size": 3, "owner": "Forge"}},
            now=NOW,
        )
        for key in (
            "ready_count",
            "process_ready_count",
            "ready_top",
            "in_progress",
            "pending_review_count",
            "blocked",
            "wip_overload",
            "free_agent_count",
            "pipeline_pressure",
            "as_of",
            "fresh_for_hours",
            "stale",
            "fetch_ok",
            "summary",
            "confidence",
            "deep_link",
        ):
            self.assertIn(key, pkt)
        self.assertEqual(pkt["fresh_for_hours"], FRESH_FOR_HOURS)
        self.assertEqual(pkt["ready_count"], 4)
        self.assertEqual(pkt["process_ready_count"], 0)
        self.assertLessEqual(len(pkt["ready_top"]), 3)
        self.assertEqual(pkt["pending_review_count"], 1)
        self.assertEqual(len(pkt["in_progress"]), 1)
        self.assertEqual(pkt["in_progress"][0]["primary_owner"], "Forge")
        self.assertFalse(pkt["wip_overload"])
        self.assertFalse(pkt["stale"])
        self.assertTrue(pkt["fetch_ok"])
        # Forge busy → free = Grok + Meridian (WIP seats)
        self.assertEqual(pkt["free_agent_count"], 2)
        self.assertEqual(pkt["pipeline_pressure"], "ok")

    def test_wip_overload_and_missing_owner_blocked(self) -> None:
        items = [
            _item(1, "A", "In Progress", owner="Forge"),
            _item(2, "B", "In Progress", owner="Forge"),
            _item(3, "C", "In Progress"),  # no owner
            _item(4, "Ready card", "Ready"),
        ]
        pkt = build_day_constraints_packet(
            items,
            agents=[
                {"name": "Forge", "seat": "implement"},
                {"name": "Grok", "seat": "gate"},
            ],
            now=NOW,
        )
        self.assertTrue(pkt["wip_overload"])
        self.assertTrue(
            any(
                b.get("number") == 3 and "missing" in (b.get("reason") or "").lower()
                for b in pkt["blocked"]
            )
        )
        # PR does not busy — only Forge on IP
        self.assertEqual(pkt["free_agent_count"], 1)  # Grok free

    def test_pending_review_does_not_busy(self) -> None:
        items = [
            _item(1, "PR only", "Pending Review", owner="Grok"),
            _item(2, "Ready", "Ready"),
        ]
        pkt = build_day_constraints_packet(
            items,
            agents=[
                {"name": "Forge", "seat": "implement"},
                {"name": "Grok", "seat": "gate"},
            ],
            now=NOW,
        )
        self.assertEqual(pkt["pending_review_count"], 1)
        self.assertEqual(len(pkt["in_progress"]), 0)
        # Both free
        self.assertEqual(pkt["free_agent_count"], 2)

    def test_fetch_fail_never_invents_zeros(self) -> None:
        pkt = build_fetch_failed_packet(now=NOW, detail="token missing")
        self.assertTrue(pkt["stale"])
        self.assertFalse(pkt["fetch_ok"])
        self.assertEqual(pkt["confidence"], 0.0)
        self.assertNotIn("ready_count", pkt)
        self.assertNotIn("process_ready_count", pkt)
        self.assertNotIn("free_agent_count", pkt)
        self.assertIsNone(pkt.get("wip_overload"))

    def test_build_with_fetch_ok_false_delegates(self) -> None:
        pkt = build_day_constraints_packet(
            [_item(1, "x", "Ready")],
            fetch_ok=False,
            now=NOW,
        )
        self.assertFalse(pkt["fetch_ok"])
        self.assertNotIn("ready_count", pkt)
        self.assertNotIn("process_ready_count", pkt)

    def test_mixed_process_eng_and_human_only_ready(self) -> None:
        """Ready+process → process_ready_count; human-only excluded; eng-only in ready_*."""
        items = [
            _item(108, "Extractor ingest", "Ready", labels=["process"]),
            _item(112, "Portfolio unpark", "Ready", labels=["process"]),
            _item(144, "Tech debt loop", "Ready", labels=["process"]),
            _item(145, "Extractor feedback", "Ready", labels=["process"]),
            _item(147, "Packet honesty", "Ready"),
            _item(99, "Chris decision", "Ready", labels=["human-only"]),
            _item(20, "Work", "In Progress", owner="Forge"),
        ]
        pkt = build_day_constraints_packet(
            items,
            agents=[
                {"name": "Forge", "seat": "implement"},
                {"name": "Grok", "seat": "gate"},
                {"name": "Meridian", "seat": "implement"},
            ],
            now=NOW,
        )
        self.assertEqual(pkt["ready_count"], 1)
        self.assertEqual(pkt["process_ready_count"], 4)
        self.assertEqual([c["number"] for c in pkt["ready_top"]], [147])
        self.assertEqual(pkt["pipeline_pressure"], "ok")
        self.assertIn("Ready 1 · process 4 ·", pkt["summary"])
        self.assertNotIn(99, [c.get("number") for c in pkt["ready_top"]])
        self.assertNotIn(108, [c.get("number") for c in pkt["ready_top"]])

    def test_process_only_ready_is_pipeline_dry(self) -> None:
        items = [
            _item(108, "Extractor ingest", "Ready", labels=["process"]),
            _item(112, "Portfolio unpark", "Ready", labels=[{"name": "process"}]),
        ]
        pkt = build_day_constraints_packet(
            items,
            agents=[
                {"name": "Forge", "seat": "implement"},
                {"name": "Meridian", "seat": "implement"},
            ],
            now=NOW,
        )
        self.assertEqual(pkt["ready_count"], 0)
        self.assertEqual(pkt["process_ready_count"], 2)
        self.assertEqual(pkt["ready_top"], [])
        self.assertEqual(pkt["pipeline_pressure"], "dry")
        self.assertIn("Ready 0 · process 2 ·", pkt["summary"])


class WriteAndCollectorTests(unittest.TestCase):
    def test_atomic_write_and_orchestra_collector(self) -> None:
        items = [
            _item(96, "P3-W", "In Progress", owner="Forge"),
            _item(97, "P3-$", "Ready"),
            _item(95, "P3-F", "Pending Review", owner="Frankenfit"),
        ]
        pkt = build_day_constraints_packet(items, now=NOW)
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            path = write_day_constraints(ws, pkt)
            self.assertEqual(path, day_constraints_path(ws))
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["ready_count"], 1)
            self.assertEqual(loaded["process_ready_count"], 0)
            self.assertEqual(loaded["in_progress"][0]["number"], 96)

            # Collector consumption (no invented zeros when file present)
            orch = ROOT / "orchestra"
            if str(orch) not in sys.path:
                sys.path.insert(0, str(orch))
            from collectors import collect_workflow  # noqa: E402

            snap = collect_workflow(ws)
            board = (snap.get("signals") or {}).get("board") or {}
            self.assertEqual(board.get("ready_count"), 1)
            self.assertEqual(board.get("process_ready_count"), 0)
            self.assertEqual(board.get("fetch_ok"), True)
            self.assertFalse(board.get("stale"))

    def test_composer_stale_and_wip_from_file_shape(self) -> None:
        """AC5: stale fail packet + wip_overload shape feed day_plan gates."""
        orch = ROOT / "orchestra"
        if str(orch) not in sys.path:
            sys.path.insert(0, str(orch))
        from day_plan import compose_day_plan  # noqa: E402

        fail = build_fetch_failed_packet(now=NOW)
        plan = compose_day_plan(
            [
                {
                    "id": "workflow",
                    "available": True,
                    "url": "http://127.0.0.1:8765/",
                    "signals": {"board": fail},
                },
                {
                    "id": "holistic",
                    "available": False,
                    "signals": {},
                },
                {
                    "id": "fitness",
                    "available": False,
                    "signals": {},
                },
                {
                    "id": "finance",
                    "available": False,
                    "signals": {},
                },
            ],
            now=NOW,
        )
        self.assertTrue(
            any(
                g.get("domain") == "workflow"
                and g.get("severity") in ("unknown", "warn")
                for g in plan["gates"]
            )
        )

        overload = build_day_constraints_packet(
            [
                _item(1, "A", "In Progress", owner="Forge"),
                _item(2, "B", "In Progress", owner="Forge"),
            ],
            agents=[{"name": "Forge", "seat": "implement"}],
            now=NOW,
        )
        self.assertTrue(overload["wip_overload"])
        plan2 = compose_day_plan(
            [
                {
                    "id": "workflow",
                    "available": True,
                    "url": "http://127.0.0.1:8765/",
                    "signals": {"board": overload},
                },
                {"id": "holistic", "available": False, "signals": {}},
                {"id": "fitness", "available": False, "signals": {}},
                {"id": "finance", "available": False, "signals": {}},
            ],
            now=NOW,
        )
        self.assertTrue(
            any(
                g.get("id") == "wip_overload" and g.get("severity") == "block"
                for g in plan2["gates"]
            )
        )


if __name__ == "__main__":
    unittest.main()
