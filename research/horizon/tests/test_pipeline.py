#!/usr/bin/env python3
"""End-to-end tests of the shipped pipeline on offline fixtures."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon import REQUIRED_DOMAINS  # noqa: E402
from research.horizon.pipeline import run_pipeline  # noqa: E402
from research.horizon.sources.fixture import FixtureSource  # noqa: E402


class TestPipeline(unittest.TestCase):
    def test_offline_pipeline_writes_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            result = run_pipeline(
                workspace=ROOT,
                data_dir=data_dir,
                offline=True,
            )
            self.assertTrue(result["ok"])
            self.assertIn("fixture", result["source_modes"])
            ws_path = Path(result["paths"]["world_state_latest"])
            brief_path = Path(result["paths"]["brief_latest_json"])
            self.assertTrue(ws_path.is_file())
            self.assertTrue(brief_path.is_file())

            state = json.loads(ws_path.read_text(encoding="utf-8"))
            for d in REQUIRED_DOMAINS:
                self.assertIn(d, state["domains"])
            # Fixture covers all 10 domains with at least one node each
            for d in REQUIRED_DOMAINS:
                self.assertGreaterEqual(
                    len(state["domains"][d]["nodes"]),
                    1,
                    f"domain {d} should have nodes from fixtures",
                )

            brief = json.loads(brief_path.read_text(encoding="utf-8"))
            for key in (
                "executive_brief",
                "current_world_state",
                "implications_for_my_strategy",
                "watchlist",
            ):
                self.assertIn(key, brief)
                section = brief[key]
                self.assertTrue(section, f"{key} should be non-empty")

            impl = brief["implications_for_my_strategy"]
            # Real strategy paths referenced
            paths = impl.get("strategy_paths") or {}
            self.assertIn("bets", paths)
            self.assertTrue(str(paths["bets"]).endswith("strategy/bets.md"))
            self.assertTrue(
                impl.get("sections") or impl.get("thematic_bets") or impl.get("intent_accomplishing")
            )

            # History version written
            hist = list((data_dir / "history").glob("world_state_*.json"))
            self.assertGreaterEqual(len(hist), 1)

            # L0 implication packet (#49)
            packet_path = Path(result["paths"]["packet_latest"])
            self.assertTrue(packet_path.is_file())
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            self.assertEqual(packet["level"], "L0")
            self.assertEqual(packet["direction"], "down")
            self.assertGreaterEqual(len(packet.get("implications_for_l4") or []), 1)

    def test_link_only_recomputes_without_requiring_new_events(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            r1 = run_pipeline(workspace=ROOT, data_dir=data_dir, offline=True)
            self.assertTrue(r1["ok"])
            r2 = run_pipeline(
                workspace=ROOT, data_dir=data_dir, offline=True, link_only=True
            )
            self.assertTrue(r2["ok"])
            self.assertTrue(r2["link_only"])
            self.assertGreater(r2["linkage_count"], 0)

    def test_fixture_source_loads_shipped_file(self):
        events = FixtureSource().fetch()
        domains = {e["domain"] for e in events}
        for d in REQUIRED_DOMAINS:
            self.assertIn(d, domains)


if __name__ == "__main__":
    unittest.main()
