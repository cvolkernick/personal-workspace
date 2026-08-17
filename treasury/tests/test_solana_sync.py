"""Solana whitelist adapter — parse, isolate JR from HY, offline fallback."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.solana_sync import (  # noqa: E402
    JR_STRCUSX_MINT,
    USDC_MINT,
    WSOL_MINT,
    fetch_solana,
    normalize_solana_book,
    parse_token_accounts,
    write_solana_snapshot,
)


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
                (USDC_MINT, 0.0, 6),
                (JR_STRCUSX_MINT, 3.317128, 6),
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
                JR_STRCUSX_MINT: 1.0255,
            },
            whitelist=[
                {"symbol": "SOL", "mint": WSOL_MINT, "role": "gas"},
                {"symbol": "USDC", "mint": USDC_MINT, "role": "onchain_stable"},
                {"symbol": "JR-strcUSX", "mint": JR_STRCUSX_MINT, "role": "dc_credit_parlay"},
            ],
        )
        self.assertAlmostEqual(book["sol"], 0.058890792)
        self.assertAlmostEqual(book["usdc"], 0.0)
        self.assertAlmostEqual(book["jr_strcusx"], 3.317128)
        self.assertAlmostEqual(book["jr_strcusx_usd"], 3.317128 * 1.0255, places=4)
        self.assertFalse(book["counts_toward_hy"])
        self.assertFalse(book["counts_toward_working_usdc"])
        self.assertEqual(book["ignored_token_accounts"], 1)
        symbols = {h["symbol"] for h in book["holdings"]}
        self.assertEqual(symbols, {"SOL", "USDC", "JR-strcUSX"})
        self.assertNotIn("STORE", symbols)


class TestFetchFallback(unittest.TestCase):
    def test_offline_reads_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sol.json"
            write_solana_snapshot(
                {
                    "source": "live",
                    "sol": 0.05,
                    "usdc": 0,
                    "jr_strcusx": 3.3,
                    "book_usd": 7.2,
                    "counts_toward_hy": True,  # poisoned — fetch must force false
                },
                path=p,
            )
            r = fetch_solana(prefer_live=False, snapshot_path=p)
            self.assertAlmostEqual(r["jr_strcusx"], 3.3)
            self.assertFalse(r["counts_toward_hy"])
            self.assertFalse(r["counts_toward_ltv_defense"])

    def test_live_error_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sol.json"
            write_solana_snapshot(
                {"source": "live", "jr_strcusx": 1.0, "book_usd": 1.0, "sol": 0},
                path=p,
            )
            with mock.patch(
                "treasury.solana_sync.fetch_solana_live",
                return_value=(None, "rpc down"),
            ):
                r = fetch_solana(prefer_live=True, snapshot_path=p)
            self.assertEqual(r["live_error"], "rpc down")
            self.assertAlmostEqual(r["jr_strcusx"], 1.0)
            self.assertFalse(r["counts_toward_hy"])


if __name__ == "__main__":
    unittest.main()
