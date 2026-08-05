#!/usr/bin/env python3
"""Unit tests for world-state update, query, and domain coverage (shipped code)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon import REQUIRED_DOMAINS  # noqa: E402
from research.horizon.world_state import (  # noqa: E402
    apply_events,
    domain_coverage,
    empty_world_state,
    ensure_domains,
    query_nodes,
)


class TestWorldState(unittest.TestCase):
    def test_empty_has_all_required_domains(self):
        state = empty_world_state("testv1")
        for d in REQUIRED_DOMAINS:
            self.assertIn(d, state["domains"])
            self.assertEqual(state["domains"][d]["nodes"], [])

    def test_apply_events_upsert_and_query(self):
        state = empty_world_state("t1")
        events = [
            {
                "id": "n1",
                "domain": "energy",
                "title": "Nuclear demand rising",
                "facts": ["Hyperscalers seek firm power."],
                "interpretation": "Supports uranium thesis.",
                "confidence": 0.8,
                "impact": "high",
                "tags": ["nuclear", "uranium"],
                "related_domains": ["technology_ai"],
            },
            {
                "id": "n2",
                "domain": "macroeconomics",
                "title": "Policy rates elevated",
                "facts": ["Rates above 2010s averages."],
                "interpretation": "Liquidity headwind.",
                "confidence": 0.7,
                "impact": "medium",
                "tags": ["rates", "fed"],
            },
        ]
        out = apply_events(state, events, version_id="t2", source_modes=["fixture"])
        self.assertEqual(out["version_id"], "t2")
        cov = domain_coverage(out)
        self.assertEqual(cov["energy"], 1)
        self.assertEqual(cov["macroeconomics"], 1)
        self.assertEqual(cov["geopolitics"], 0)

        energy = query_nodes(out, domain="energy")
        self.assertEqual(len(energy), 1)
        self.assertEqual(energy[0]["id"], "n1")
        self.assertIn("facts", energy[0])
        self.assertTrue(energy[0]["priority_score"] > 0)

        tagged = query_nodes(out, tag="nuclear")
        self.assertEqual(len(tagged), 1)

        # Upsert same id updates title
        out2 = apply_events(
            out,
            [
                {
                    "id": "n1",
                    "domain": "energy",
                    "title": "Nuclear demand still rising",
                    "facts": ["Updated fact."],
                    "confidence": 0.9,
                    "impact": "critical",
                    "tags": ["nuclear"],
                }
            ],
            version_id="t3",
        )
        energy2 = query_nodes(out2, domain="energy")
        self.assertEqual(len(energy2), 1)
        self.assertEqual(energy2[0]["title"], "Nuclear demand still rising")
        self.assertEqual(energy2[0]["impact"], "critical")

    def test_ensure_domains_fills_missing(self):
        partial = {"domains": {"energy": {"nodes": []}}, "edges": []}
        ensure_domains(partial)
        for d in REQUIRED_DOMAINS:
            self.assertIn(d, partial["domains"])

    def test_query_min_confidence_and_limit(self):
        state = apply_events(
            empty_world_state(),
            [
                {
                    "id": "a",
                    "domain": "geopolitics",
                    "title": "A",
                    "facts": ["f"],
                    "confidence": 0.9,
                    "impact": "high",
                },
                {
                    "id": "b",
                    "domain": "geopolitics",
                    "title": "B",
                    "facts": ["f"],
                    "confidence": 0.2,
                    "impact": "low",
                },
            ],
        )
        high = query_nodes(state, min_confidence=0.5)
        self.assertEqual(len(high), 1)
        self.assertEqual(high[0]["id"], "a")
        limited = query_nodes(state, limit=1)
        self.assertEqual(len(limited), 1)


if __name__ == "__main__":
    unittest.main()
