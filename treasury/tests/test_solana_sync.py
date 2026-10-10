"""Solana whitelist adapter — parse, offline fallback (JR-strcUSX retired)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.policy import evaluate_treasury  # noqa: E402
from treasury.solana_sync import (  # noqa: E402
    USDC_MINT,
    WSOL_MINT,
    fetch_solana,
    normalize_solana_book,
    overlay_solana_snapshot,
    parse_token_accounts,
    write_solana_snapshot,
)

# Retired mint — still appears on-chain as dust; must be ignored when not whitelisted.
_RETIRED_STRCUSX_MINT = "BQ6LPc68knpko292UsMLbQYfaHhWD7S84sA98632hrzX"


def _rpc_accounts(*rows):
    value = []
    for mint, ui, dec in rows:
        value.append(
            {
                "pubkey": "ata-" + mint[:6],
                "account": {
                    "data": {
                        "parsed": {
                            "info": {
                                "mint": mint,
                                "tokenAmount": {
                                    "uiAmount": ui,
                                    "decimals": dec,
                                },
                            }
                        }
                    }
                },
            }
        )
    return {"result": {"context": {"slot": 1}, "value": value}}


class TestParseAndNormalize(unittest.TestCase):
    def test_whitelist_only_and_usd(self):
        rows = parse_token_accounts(
            _rpc_accounts(
                (USDC_MINT, 12.5, 6),
                (_RETIRED_STRCUSX_MINT, 3.317128, 6),
                ("FLJYGHpCCcfYUdzhcfHSeSd2peb5SMajNWaCsRnhpump", 69.0, 6),
            )
        )
        self.assertEqual(len(rows), 3)
        book = normalize_solana_book(
            wallet="CzuRxF4H7qtcCbP37zcLu9DTgcHySmi8NcTsrS6W7bDm",
            sol_lamports=58_890_792,
            token_rows=rows,
            prices={
                WSOL_MINT: 76.0,
                USDC_MINT: 1.0,
                _RETIRED_STRCUSX_MINT: 1.0255,
            },
            whitelist=[
                {"symbol": "SOL", "mint": WSOL_MINT, "role": "gas"},
                {"symbol": "USDC", "mint": USDC_MINT, "role": "onchain_stable"},
            ],
        )
        self.assertAlmostEqual(book["sol"], 0.058890792)
        self.assertAlmostEqual(book["usdc"], 12.5)
        self.assertNotIn("jr_strcusx", book)
        self.assertFalse(book["counts_toward_hy"])
        self.assertFalse(book["counts_toward_working_usdc"])
        self.assertEqual(book["ignored_token_accounts"], 2)
        symbols = {h["symbol"] for h in book["holdings"]}
        self.assertEqual(symbols, {"SOL", "USDC"})
        self.assertNotIn("JR-strcUSX", symbols)


class TestFetchFallback(unittest.TestCase):
    def test_offline_reads_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sol.json"
            write_solana_snapshot(
                {
                    "source": "live",
                    "sol": 0.05,
                    "usdc": 3.3,
                    "book_usd": 7.2,
                    "counts_toward_hy": True,  # poisoned — fetch must force false
                },
                path=p,
            )
            r = fetch_solana(prefer_live=False, snapshot_path=p)
            self.assertAlmostEqual(r["usdc"], 3.3)
            self.assertFalse(r["counts_toward_hy"])
            self.assertFalse(r["counts_toward_ltv_defense"])

    def test_live_error_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sol.json"
            write_solana_snapshot(
                {"source": "live", "usdc": 1.0, "book_usd": 1.0, "sol": 0},
                path=p,
            )
            with mock.patch(
                "treasury.solana_sync.fetch_solana_live",
                return_value=(None, "rpc down"),
            ):
                r = fetch_solana(prefer_live=True, snapshot_path=p)
            self.assertEqual(r["live_error"], "rpc down")
            self.assertAlmostEqual(r["usdc"], 1.0)
            self.assertFalse(r["counts_toward_hy"])

    def test_overlay_fills_empty_block_does_not_clobber(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sol.json"
            write_solana_snapshot(
                {
                    "source": "live",
                    "as_of": "2026-08-29T18:12:13+00:00",
                    "book_usd": 9.58,
                    "usdc": 3.317128,
                },
                path=p,
            )
            empty = {"snapshot": {"solana": {}, "coinbase": {"as_of": "cb"}}}
            overlay_solana_snapshot(empty, snapshot_path=p)
            self.assertEqual(
                empty["snapshot"]["solana"]["as_of"], "2026-08-29T18:12:13+00:00"
            )
            self.assertTrue(empty["snapshot"]["meta"]["solana_sidecar"])
            keep = {
                "snapshot": {
                    "solana": {"as_of": "keep-me", "book_usd": 1.0},
                }
            }
            overlay_solana_snapshot(keep, snapshot_path=p)
            self.assertEqual(keep["snapshot"]["solana"]["as_of"], "keep-me")


class TestPolicyIsolation(unittest.TestCase):
    def test_solana_does_not_fill_hy_or_working_usdc(self):
        snap = {
            "coinbase": {"liquid_usdc": 10.0, "source": "live"},
            "coinbase_manual": {"vault_usdc": 50.0, "ltv": 0.2},
            "robinhood": {"buying_power": 100, "cash": 0, "equity_value": 100, "total_value": 100},
            "one_card": {"source": "ynab", "card_balance": 0, "balance_owed": 0},
            "solana": {
                "source": "live",
                "wallet": "CzuRxF4H7qtcCbP37zcLu9DTgcHySmi8NcTsrS6W7bDm",
                "sol": 0.05,
                "usdc": 12.0,
                "book_usd": 118.0,
                "counts_toward_hy": False,
            },
        }
        ev = evaluate_treasury(snap)
        self.assertAlmostEqual(ev["inputs"]["working_usdc"], 60.0)
        self.assertAlmostEqual(ev["inputs"]["liquid_usdc"], 10.0)
        self.assertFalse(ev["inputs"]["solana_counts_toward_hy"])
        sleeve = ev["sleeves"]["solana"]
        self.assertFalse(sleeve["counts_toward_hy"])
        self.assertFalse(sleeve["counts_toward_ltv_defense"])
        self.assertAlmostEqual(sleeve["usdc"], 12.0)
        self.assertIn("NOT HY", ev["agent_brief"])


if __name__ == "__main__":
    unittest.main()
