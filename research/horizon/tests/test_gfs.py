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
    BOOK_PREFIXES,
    DEFAULT_FIXTURE,
    build_gfs_packet,
    ensure_gfs_packet,
    validate_graph,
)

NAKA_IDS = {
    "usd_liquidity",
    "real_rates",
    "dollar",
    "btc_liquidity",
    "onchain_credit",
    "equity_risk",
    "hashprice_power",
    "strc_credit",
    "policy_rate",
    "fed_plumbing",
    "usdc_integrity",
    "onchain_usd_float",
}
VETOED_IDS = {
    "credit_spreads",
    "treasury_function",
    "boj_carry",
    "china_credit",
    "energy",
    "fed_balance_sheet",
    "tga_rrp",
    "fx_swap_lines",
}


class TestGfsPacket(unittest.TestCase):
    def test_fixture_validates(self):
        raw = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(validate_graph(raw), [])

    def test_build_packet_shape(self):
        pkt = build_gfs_packet()
        self.assertTrue(pkt["ok"])
        self.assertEqual(pkt["pressure_count"], 8)
        self.assertEqual(pkt["valve_count"], 4)
        self.assertEqual(pkt["source_kind"], "fixture")
        self.assertGreaterEqual(pkt["pressure_index"], 2.0)
        self.assertLessEqual(pkt["pressure_index"], 5.0)
        ids = {n["id"] for n in pkt["nodes"]}
        self.assertEqual(ids, NAKA_IDS)
        self.assertIn("usd_liquidity", ids)
        self.assertIn("fed_plumbing", ids)
        self.assertNotIn("fed_balance_sheet", ids)
        self.assertTrue(ids.isdisjoint(VETOED_IDS))
        self.assertEqual(set(pkt["book_channels"]), set(BOOK_PREFIXES))
        self.assertIn("Listed STRC", pkt["book_channels"]["strc_jr"])
        aliases = {n.get("horizon_alias") for n in pkt["nodes"]}
        self.assertIn("capital-btc-liquidity", aliases)
        self.assertIn("macro-rates-regime", aliases)
        rels = {e["relation"] for e in pkt["edges"]}
        self.assertTrue({"tightens", "vents", "exposes"} <= rels)
        self.assertTrue(rels <= {"tightens", "vents", "funds", "exposes"})
        self.assertTrue(any("btc_morpho_ltv" in p["path"] for p in pkt["transmissions"]))
        for n in pkt["nodes"]:
            self.assertIn(n["kind"], ("pressure", "valve"))
            self.assertGreaterEqual(n["level"], 1)
            self.assertLessEqual(n["level"], 5)
            self.assertLessEqual(n["confidence"], 0.50)

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
            ids = {n["id"] for n in payload["gfs"]["nodes"]}
            self.assertIn("usd_liquidity", ids)
            self.assertIn("fed_plumbing", ids)
            self.assertNotIn("fed_balance_sheet", ids)


if __name__ == "__main__":
    unittest.main()
