#!/usr/bin/env python3
"""Tests for multi-axis regime assessment layer."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon.regime import (  # noqa: E402
    REGIME_AXES,
    assess_regime,
    attach_regime,
    regime_brief_block,
)
from research.horizon.sources.fixture import FixtureSource  # noqa: E402
from research.horizon.sources.rss import DEFAULT_FEEDS  # noqa: E402
from research.horizon.world_state import apply_events, empty_world_state  # noqa: E402


class TestRegime(unittest.TestCase):
    def _fixture_state(self):
        events = FixtureSource().fetch()
        return apply_events(
            empty_world_state("reg1"),
            events,
            version_id="reg1",
            source_modes=["fixture"],
        )

    def test_assess_regime_multi_axis_schema(self):
        state = self._fixture_state()
        r = assess_regime(state)
        self.assertEqual(r["schema_version"], 1)
        self.assertIn("primary", r)
        self.assertIn("axes", r)
        self.assertIn("scenarios", r)
        self.assertIn("active_forces", r)
        self.assertIn("inflection_watch", r)
        self.assertIn("confidence_overall", r)
        self.assertIn("data_vintage", r)

        axis_ids = {a["id"] for a in r["axes"]}
        for ax in REGIME_AXES:
            self.assertIn(ax, axis_ids)

        for ax in r["axes"]:
            probs = [float(s["probability"]) for s in ax["states"]]
            self.assertAlmostEqual(sum(probs), 1.0, places=2)
            self.assertEqual(ax["dominant"], max(ax["states"], key=lambda s: s["probability"])["id"])

        s_probs = [float(s["probability"]) for s in r["scenarios"]]
        self.assertAlmostEqual(sum(s_probs), 1.0, places=2)
        self.assertGreater(r["confidence_overall"], 0.0)
        self.assertLessEqual(r["confidence_overall"], 0.75)
        self.assertGreaterEqual(r["data_vintage"]["node_count"], 10)
        self.assertTrue(r["data_vintage"].get("fixture_scaffold_dominant"))

        primary = r["primary"]
        self.assertTrue(primary.get("label"))
        self.assertIn("probability", primary)

    def test_attach_and_brief_block(self):
        state = self._fixture_state()
        out = attach_regime(state)
        self.assertIn("regime", out)
        block = regime_brief_block(out["regime"])
        self.assertEqual(block["title"], "Regime Assessment")
        self.assertEqual(len(block["axes"]), len(REGIME_AXES))
        self.assertTrue(block["primary"])

    def test_fixture_primary_axes_structural(self):
        """Rates + geo + AI fixtures should pin structural axes."""
        state = self._fixture_state()
        r = assess_regime(state)
        by_id = {a["id"]: a for a in r["axes"]}
        self.assertEqual(by_id["monetary"]["dominant"], "higher_for_longer")
        self.assertEqual(by_id["geopolitics"]["dominant"], "elevated_competition")
        self.assertIn(
            by_id["energy_tech"]["dominant"],
            {"power_constrained_ai", "commodity_shock", "transition_smooth"},
        )

    def test_source_density_feeds_expanded(self):
        names = {f["name"] for f in DEFAULT_FEEDS}
        self.assertIn("Federal Reserve Press", names)
        self.assertIn("EIA Today in Energy", names)
        self.assertGreaterEqual(len(DEFAULT_FEEDS), 8)
        self.assertTrue(
            {"ECB Press", "BLS News Releases", "IMF News"} & names,
            f"expected expanded official feeds, got {names}",
        )


if __name__ == "__main__":
    unittest.main()
