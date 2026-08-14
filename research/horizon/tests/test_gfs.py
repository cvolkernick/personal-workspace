#!/usr/bin/env python3
"""GFS pressure/valve packet tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon.gfs import (  # noqa: E402
    DEFAULT_FIXTURE,
    build_gfs_packet,
    ensure_gfs_packet,
    validate_graph,
)


class TestGfsPacket(unittest.TestCase):
    def test_fixture_validates(self):
        raw = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(validate_graph(raw), [])

    def test_build_packet_shape(self):
        pkt = build_gfs_packet()
        self.assertTrue(pkt["ok"])
        self.assertGreaterEqual(pkt["pressure_count"], 1)
        self.assertGreaterEqual(pkt["valve_count"], 1)
        self.assertEqual(pkt["source_kind"], "fixture")
        self.assertGreaterEqual(pkt["pressure_index"], 2.0)
        self.assertLessEqual(pkt["pressure_index"], 5.0)
        ids = {n["id"] for n in pkt["nodes"]}
        self.assertIn("usd_liquidity", ids)
        self.assertIn("fed_balance_sheet", ids)
        rels = {e["relation"] for e in pkt["edges"]}
        self.assertTrue({"tightens", "vents", "exposes"} <= rels)
        self.assertTrue(any("btc_morpho_ltv" in p["path"] for p in pkt["transmissions"]))
        for n in pkt["nodes"]:
            self.assertIn(n["kind"], ("pressure", "valve"))
            self.assertGreaterEqual(n["level"], 1)
            self.assertLessEqual(n["level"], 5)

    def test_unknown_edge_is_error(self):
        raw = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
        raw["edges"] = list(raw["edges"]) + [
            {"from_id": "usd_liquidity", "to_id": "not_a_node", "relation": "tightens"}
        ]
        errs = validate_graph(raw)
        self.assertTrue(any("not_a_node" in e for e in errs))
        pkt = build_gfs_packet(raw)
        self.assertFalse(pkt["ok"])

    def test_ensure_writes(self):
        with tempfile.TemporaryDirectory() as td:
            dest_dir = Path(td)
            pkt = ensure_gfs_packet(data_dir=dest_dir, rebuild=True)
            self.assertTrue((dest_dir / "gfs_latest.json").is_file())
            self.assertGreaterEqual(pkt["node_count"], 10)

    def test_dashboard_includes_gfs(self):
        from research.horizon.pipeline import run_pipeline
        from research.horizon.server import build_dashboard_payload

        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            run_pipeline(workspace=ROOT, data_dir=data_dir, offline=True)
            payload = build_dashboard_payload(ROOT, data_dir)
            self.assertTrue(payload.get("has_gfs"))
            self.assertIn("usd_liquidity", {n["id"] for n in payload["gfs"]["nodes"]})


if __name__ == "__main__":
    unittest.main()
