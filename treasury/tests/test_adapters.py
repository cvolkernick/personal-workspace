"""Tests for adapters: parse coinbase payload + RH normalize + offline snapshot path."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import (  # noqa: E402
    _parse_coinbase_balance_payload,
    build_snapshot,
    fetch_coinbase_liquid,
    fetch_robinhood,
    normalize_robinhood_payload,
    save_json,
    write_robinhood_snapshot,
)


class TestParseCoinbase(unittest.TestCase):
    def test_sums_usdc_usd_btc(self):
        payload = {
            "accounts": [
                {"currency": "USDC", "available_balance": {"value": "100.5"}},
                {"currency": "USD", "available_balance": {"value": "50"}},
                {"currency": "BTC", "available_balance": {"value": "0.25"}},
                {"currency": "ETH", "available_balance": {"value": "9"}},
            ]
        }
        t = _parse_coinbase_balance_payload(payload)
        self.assertAlmostEqual(t["USDC"], 100.5)
        self.assertAlmostEqual(t["USD"], 50.0)
        self.assertAlmostEqual(t["BTC"], 0.25)


class TestRobinhoodNormalize(unittest.TestCase):
    def test_mcp_envelope(self):
        raw = {
            "data": {
                "total_value": "144.23",
                "equity_value": "144.14",
                "cash": "0.08",
                "buying_power": {
                    "buying_power": "0.0800",
                    "unleveraged_buying_power": "0.0800",
                },
            }
        }
        n = normalize_robinhood_payload(raw, source="live")
        self.assertAlmostEqual(n["buying_power"], 0.08)
        self.assertAlmostEqual(n["total_value"], 144.23)
        self.assertEqual(n["source"], "live")


class TestSnapshotFallback(unittest.TestCase):
    def test_coinbase_file_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cb.json"
            save_json(
                p,
                {
                    "source": "snapshot",
                    "liquid_usdc": 1234.0,
                    "liquid_btc": 0.5,
                },
            )
            r = fetch_coinbase_liquid(prefer_live=False, snapshot_path=p)
            self.assertEqual(r["liquid_usdc"], 1234.0)
            self.assertEqual(r["liquid_btc"], 0.5)

    def test_robinhood_file_and_build(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rh.json"
            write_robinhood_snapshot(
                {
                    "source": "live",
                    "total_value": 10000,
                    "equity_value": 9000,
                    "cash": 1000,
                    "buying_power": 2500,
                    "unleveraged_buying_power": 1000,
                },
                path=p,
            )
            r = fetch_robinhood(snapshot_path=p)
            self.assertAlmostEqual(r["buying_power"], 2500)
            self.assertTrue(p.is_file())


if __name__ == "__main__":
    unittest.main()
