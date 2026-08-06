#!/usr/bin/env python3
"""Tests that synthesis produces required sections with fact/interpretation/confidence."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon import REQUIRED_DOMAINS  # noqa: E402
from research.horizon.sources.fixture import FixtureSource  # noqa: E402
from research.horizon.strategy_link import link_world_to_strategy, load_strategy  # noqa: E402
from research.horizon.synthesis import (  # noqa: E402
    build_watchlist,
    render_markdown,
    synthesize,
)
from research.horizon.world_state import apply_events, empty_world_state  # noqa: E402


class TestSynthesis(unittest.TestCase):
    def _state_and_links(self):
        events = FixtureSource().fetch()
        self.assertGreaterEqual(len(events), 10)
        state = apply_events(
            empty_world_state("syn1"),
            events,
            version_id="syn1",
            source_modes=["fixture"],
        )
        strategy = load_strategy(ROOT)
        linkages = link_world_to_strategy(state, strategy)
        return state, strategy, linkages

    def test_synthesize_required_sections(self):
        state, strategy, linkages = self._state_and_links()
        brief = synthesize(state, strategy, linkages)

        self.assertIn("regime", brief)
        self.assertIn("primary", brief["regime"])
        self.assertIn("executive_brief", brief)
        self.assertIn("current_world_state", brief)
        self.assertIn("implications_for_my_strategy", brief)
        self.assertIn("watchlist", brief)
        self.assertIn("regime", brief)
        self.assertIn("primary", brief["regime"])
        self.assertIn("probabilities", brief["regime"])

        eb = brief["executive_brief"]
        self.assertGreater(len(eb["items"]), 0)
        item = eb["items"][0]
        self.assertIn("fact", item)
        self.assertIn("interpretation", item)
        self.assertIn("confidence", item)
        self.assertIn("priority_rationale", item)
        # Strategy linkage should surface on ranked items when fixtures match bets
        self.assertIn("strategy_priorities", item)
        self.assertIn("strategy_links", item)
        any_linked = any(
            (it.get("strategy_priorities") or it.get("strategy_links"))
            for it in eb["items"]
        )
        self.assertTrue(
            any_linked,
            "expected at least one executive-brief item linked to strategy",
        )

        ws = brief["current_world_state"]
        for d in REQUIRED_DOMAINS:
            self.assertIn(d, ws["domains"], f"missing domain {d}")
            self.assertIn("summary", ws["domains"][d])

        impl = brief["implications_for_my_strategy"]
        self.assertIn("sections", impl)
        self.assertTrue(
            impl.get("intent_accomplishing") or impl.get("thematic_bets"),
            "strategy implications should reference loaded priorities",
        )
        # At least one section linking world to strategy when fixtures + real strategy
        self.assertGreater(impl.get("linkage_count", 0), 0)

        wl = brief["watchlist"]
        self.assertGreater(len(wl["items"]), 0)
        self.assertEqual(wl["items"][0]["rank"], 1)
        self.assertIn("why_watch", wl["items"][0])
        self.assertIn("priority_score", wl["items"][0])
        self.assertIn("confidence", wl["items"][0])

    def test_watchlist_ranking_structure(self):
        state, _, linkages = self._state_and_links()
        wl = build_watchlist(state, top_n=5, linkages=linkages)
        # rank_score is the strategy-aware sort key (monotonic non-increasing)
        rank_scores = [float(i.get("rank_score") or i["priority_score"]) for i in wl["items"]]
        self.assertEqual(rank_scores, sorted(rank_scores, reverse=True))
        ranks = [i["rank"] for i in wl["items"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
        self.assertIn("strategy_priorities", wl["items"][0])

    def test_markdown_contains_four_sections(self):
        state, strategy, linkages = self._state_and_links()
        brief = synthesize(state, strategy, linkages)
        md = render_markdown(brief)
        self.assertIn("## 0. Regime Assessment", md)
        self.assertIn("## 1. Executive Brief", md)
        self.assertIn("## 2. Current World State", md)
        self.assertIn("## 3. Implications for My Strategy", md)
        self.assertIn("## 4. Watchlist / Radar", md)
        self.assertIn("**Fact:**", md)
        self.assertIn("**Confidence:**", md)


if __name__ == "__main__":
    unittest.main()
