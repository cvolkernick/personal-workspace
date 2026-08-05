#!/usr/bin/env python3
"""Tests for strategy loading from real workspace paths and linkage recompute."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon.strategy_link import (  # noqa: E402
    link_world_to_strategy,
    load_strategy,
    score_affinity,
)
from research.horizon.world_state import apply_events, empty_world_state  # noqa: E402


class TestStrategyLink(unittest.TestCase):
    def test_load_strategy_from_real_workspace(self):
        """Drive load_strategy against the real personal-workspace paths."""
        strategy = load_strategy(ROOT)
        paths = strategy["paths"]
        # Must point at real strategy/ files, not hard-coded only in tests
        self.assertTrue(paths["bets"].endswith("strategy/bets.md"))
        self.assertTrue(paths["intent"].endswith("strategy/intent.json"))
        self.assertTrue(paths["today"].endswith("strategy/today.md"))
        self.assertTrue(paths["positions"].endswith("investment/positions.md"))
        # Core Orchestrator sources present on finance worktrees; intent may
        # live on orchestra-merged trees — loader must still wire the path.
        self.assertTrue(strategy["paths_exist"]["bets"])
        self.assertTrue(strategy["paths_exist"]["today"])
        self.assertTrue(len(strategy["priorities"]) >= 1)
        # Thematic bets or intent should surface Energy/Bitcoin/AI themes
        blob = json.dumps(strategy).lower()
        self.assertTrue(
            "bitcoin" in blob or "energy" in blob or "ai" in blob,
            "expected thematic content from strategy sources",
        )
        # If intent.json exists, it must be parsed into accomplishing / priorities
        if strategy["paths_exist"]["intent"]:
            self.assertTrue(
                (strategy.get("intent") or {}).get("accomplishing")
                or any(p.get("id") == "intent_north_star" for p in strategy["priorities"])
            )

    def test_linkage_changes_when_strategy_keywords_change(self):
        state = apply_events(
            empty_world_state(),
            [
                {
                    "id": "btc-1",
                    "domain": "capital_flows",
                    "title": "Bitcoin ETF flows surge",
                    "facts": ["Spot BTC ETF net inflows rose."],
                    "interpretation": "Supports accumulation.",
                    "confidence": 0.8,
                    "impact": "high",
                    "tags": ["bitcoin", "btc", "etf"],
                },
                {
                    "id": "agri-1",
                    "domain": "climate_resources",
                    "title": "Soybean harvest outlook",
                    "facts": ["Harvest estimates revised."],
                    "confidence": 0.6,
                    "impact": "low",
                    "tags": ["agriculture", "soy"],
                },
            ],
        )

        strat_btc = {
            "priorities": [
                {
                    "id": "bitcoin",
                    "label": "Bitcoin",
                    "keywords": ["bitcoin", "btc", "etf"],
                }
            ],
            "paths": {},
            "paths_exist": {},
            "thematic_bets": ["Bitcoin"],
            "intent": {},
            "positions_symbols": [],
        }
        links_btc = link_world_to_strategy(state, strat_btc, min_affinity=0.05)
        btc_ids = {l["node_id"] for l in links_btc}
        self.assertIn("btc-1", btc_ids)

        strat_agri = {
            "priorities": [
                {
                    "id": "agri",
                    "label": "Agriculture",
                    "keywords": ["soy", "agriculture", "harvest"],
                }
            ],
            "paths": {},
            "paths_exist": {},
            "thematic_bets": [],
            "intent": {},
            "positions_symbols": [],
        }
        links_agri = link_world_to_strategy(state, strat_agri, min_affinity=0.05)
        agri_ids = {l["node_id"] for l in links_agri}
        self.assertIn("agri-1", agri_ids)
        # Different strategy focus should change which node ranks first
        self.assertNotEqual(links_btc[0]["node_id"], links_agri[0]["node_id"])

    def test_score_affinity_uses_shipped_function(self):
        node = {
            "title": "Nuclear power for AI data centers",
            "tags": ["nuclear", "ai"],
            "facts": ["Uranium demand rising."],
            "interpretation": "Energy bottleneck.",
            "confidence": 0.9,
            "impact": "high",
            "domain": "energy",
        }
        pr = {"keywords": ["nuclear", "uranium", "ai"]}
        score = score_affinity(node, pr)
        self.assertGreater(score, 0.2)
        zero = score_affinity(node, {"keywords": ["bananas-only-xyz"]})
        self.assertEqual(zero, 0.0)

    def test_load_strategy_from_temp_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "strategy").mkdir()
            (root / "investment").mkdir()
            (root / "strategy" / "bets.md").write_text(
                "# Bets\n- **Energy**\n- **Bitcoin**\n- **AI**\n",
                encoding="utf-8",
            )
            (root / "strategy" / "intent.json").write_text(
                json.dumps({"accomplishing": "Accumulate Bitcoin and Energy exposure"}),
                encoding="utf-8",
            )
            (root / "strategy" / "today.md").write_text("# Today\n- review DCA\n", encoding="utf-8")
            (root / "investment" / "positions.md").write_text(
                "| Symbol | Notes |\n|--------|-------|\n| BTC | Bitcoin |\n| CCJ | Uranium |\n",
                encoding="utf-8",
            )
            s = load_strategy(root)
            self.assertTrue(s["paths_exist"]["bets"])
            self.assertIn("BTC", s["positions_symbols"])
            self.assertTrue(
                any("bitcoin" in (p.get("label") or "").lower() or p.get("id") == "bitcoin"
                    for p in s["priorities"])
            )


if __name__ == "__main__":
    unittest.main()
