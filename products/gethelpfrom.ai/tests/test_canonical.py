#!/usr/bin/env python3
"""Canonical JSON integrity + drift rules (#621)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


class TestCanonical(unittest.TestCase):
    def test_versions_present(self) -> None:
        for name in ("questions.json", "scenarios.json", "scoring.json"):
            data = _load(name)
            self.assertIn("version", data, name)

    def test_every_scenario_maps_to_known_solution(self) -> None:
        sols = set(_load("scoring.json")["solutions"])
        missing = [
            s["id"]
            for s in _load("scenarios.json")["scenarios"]
            if s["solution"] not in sols
        ]
        self.assertEqual(missing, [])

    def test_four_scenarios_per_sub(self) -> None:
        from collections import Counter

        c = Counter(
            (s["bucket"], s["sub"]) for s in _load("scenarios.json")["scenarios"]
        )
        bad = {k: n for k, n in c.items() if n != 4}
        self.assertEqual(bad, {})
        self.assertEqual(len(c), 48)

    def test_questions_subs_match_scenario_groups(self) -> None:
        qsubs = {
            (bid, s["id"])
            for bid, subs in _load("questions.json")["stage2"]["subs"].items()
            for s in subs
        }
        ssubs = {
            (s["bucket"], s["sub"]) for s in _load("scenarios.json")["scenarios"]
        }
        self.assertEqual(qsubs, ssubs)

    def test_digest_schema_required_keys(self) -> None:
        schema = _load("digest.schema.json")
        for key in (
            "contact",
            "ranked",
            "why",
            "demo_prep",
            "scoring_version",
        ):
            self.assertIn(key, schema["required"])

    def test_scenario_text_is_concrete(self) -> None:
        banned = ("optimization", "inefficiency", "leverage", "synergy")
        hits = [
            s["id"]
            for s in _load("scenarios.json")["scenarios"]
            if any(b in s["text"].lower() for b in banned)
        ]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
