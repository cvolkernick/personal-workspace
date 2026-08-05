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

        self.assertIn("executive_brief", brief)
        self.assertIn("current_world_state", brief)
        self.assertIn("implications_for_my_strategy", brief)
        self.assertIn("watchlist", brief)

        eb = brief["executive_brief"]
        self.assertGreater(len(eb["items"]), 0)
        item = eb["items"][0]
        self.assertIn("fact", item)
        self.assertIn("interpretation", item)
        self.assertIn("confidence", item)
        self.assertIn("priority_rationale", item)

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
        state, _, _ = self._state_and_links()
        wl = build_watchlist(state, top_n=5)
        scores = [float(i["priority_score"]) for i in wl["items"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        ranks = [i["rank"] for i in wl["items"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    def test_markdown_contains_four_sections(self):
        state, strategy, linkages = self._state_and_links()
        brief = synthesize(state, strategy, linkages)
        md = render_markdown(brief)
        self.assertIn("## 1. Executive Brief", md)
        self.assertIn("## 2. Current World State", md)
        self.assertIn("## 3. Implications for My Strategy", md)
        self.assertIn("## 4. Watchlist / Radar", md)
        self.assertIn("**Fact:**", md)
        self.assertIn("**Confidence:**", md)


if __name__ == "__main__":
    unittest.main()
