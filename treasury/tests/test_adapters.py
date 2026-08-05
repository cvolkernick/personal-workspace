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
    _merge_manual_with_one_card,
    _parse_coinbase_balance_payload,
    build_robinhood_dual_snapshot,
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

    def test_dual_account_agentic(self):
        snap = build_robinhood_dual_snapshot(
            primary_portfolio={
                "data": {
                    "total_value": "100",
                    "equity_value": "90",
                    "cash": "10",
                    "buying_power": {"buying_power": "10"},
                }
            },
            agentic_portfolio={
                "data": {
                    "total_value": "50",
                    "equity_value": "0",
                    "cash": "50",
                    "buying_power": {"buying_power": "50"},
                }
            },
            primary_account="5QW39737",
            agentic_account="674601752",
            primary_positions=[{"symbol": "TSLA", "quantity": "1"}],
            agentic_positions=[],
            source="live",
        )
        self.assertAlmostEqual(snap["buying_power"], 10.0)
        self.assertEqual(snap["account_number_last4"], "9737")
        self.assertFalse(snap["agentic_allowed"])
        self.assertAlmostEqual(snap["agentic"]["buying_power"], 50.0)
        self.assertTrue(snap["agentic"]["agentic_allowed"])
        self.assertEqual(snap["agentic"]["account_number_last4"], "1752")
        self.assertEqual(snap["positions"][0]["symbol"], "TSLA")
        self.assertTrue(snap["mcp"]["connected"])


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

    def test_coinbase_live_fail_demotes_source(self):
        """When live CLI fails, do not keep source=live on a stale file fallback."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cb.json"
            save_json(
                p,
                {
                    "source": "live",
                    "as_of": "2026-07-29T18:13:11+00:00",
                    "liquid_usdc": 1.0,
                    "liquid_btc": 0.0,
                },
            )
            import treasury.adapters as ad

            real = ad.fetch_coinbase_liquid_live

            def boom(*, timeout: float = 30.0):
                return None, "coinbase CLI not found (PATH missing homebrew?)"

            ad.fetch_coinbase_liquid_live = boom  # type: ignore[assignment]
            try:
                r = fetch_coinbase_liquid(prefer_live=True, snapshot_path=p)
            finally:
                ad.fetch_coinbase_liquid_live = real  # type: ignore[assignment]
            self.assertEqual(r["liquid_usdc"], 1.0)
            self.assertEqual(r["source"], "snapshot")
            self.assertIn("coinbase CLI not found", r.get("live_error") or "")

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


class TestMergeManualWithOneCard(unittest.TestCase):
    def test_ynab_overrides_stale_manual_card_balance(self):
        merged = _merge_manual_with_one_card(
            {"card_balance": 499.23, "vault_usdc": 127},
            {"source": "ynab", "card_balance": 440.18, "balance_owed": 440.18},
        )
        self.assertEqual(merged["card_balance"], 440.18)
        self.assertEqual(merged["card_balance_source"], "ynab")
        self.assertEqual(merged["vault_usdc"], 127)

    def test_empty_ynab_keeps_manual(self):
        merged = _merge_manual_with_one_card(
            {"card_balance": 100.0},
            {"source": "empty", "live_error": "no YNAB token"},
        )
        self.assertEqual(merged["card_balance"], 100.0)
        self.assertIsNone(merged.get("card_balance_source"))


if __name__ == "__main__":
    unittest.main()
