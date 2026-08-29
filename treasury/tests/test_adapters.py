"""Tests for adapters: parse coinbase payload + RH normalize + offline snapshot path."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
    should_force_offline_consumer,
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
            disk = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(disk["source"], "snapshot")
            self.assertIn("coinbase CLI not found", disk.get("live_error") or "")

    def test_no_cli_skips_live_subprocess(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cb.json"
            save_json(
                p,
                {
                    "source": "live",
                    "as_of": "2026-08-28T19:59:03+00:00",
                    "liquid_usdc": 104.0,
                    "liquid_btc": 0.0,
                },
            )
            with mock.patch(
                "treasury.adapters._resolve_coinbase_bin", return_value=None
            ):
                with mock.patch(
                    "treasury.adapters.fetch_coinbase_liquid_live"
                ) as live:
                    r = fetch_coinbase_liquid(prefer_live=True, snapshot_path=p)
                    live.assert_not_called()
            self.assertEqual(r["source"], "snapshot")
            self.assertEqual(r["live_error"], "coinbase CLI not found")
            self.assertAlmostEqual(r["liquid_usdc"], 104.0)

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


class TestOfflineConsumerPolicy(unittest.TestCase):
    def test_explicit_or_env(self):
        self.assertTrue(should_force_offline_consumer(explicit=True, has_ynab_token=True, has_coinbase_cli=True))
        self.assertTrue(should_force_offline_consumer(env_consumer=True, has_ynab_token=True, has_coinbase_cli=True))

    def test_no_creds_fail_closed(self):
        self.assertTrue(
            should_force_offline_consumer(has_ynab_token=False, has_coinbase_cli=False)
        )
        self.assertTrue(should_force_offline_consumer())

    def test_ynab_without_coinbase_is_split_live_not_consumer(self):
        self.assertFalse(
            should_force_offline_consumer(has_ynab_token=True, has_coinbase_cli=False)
        )

    def test_coinbase_without_ynab_is_producer(self):
        self.assertFalse(
            should_force_offline_consumer(has_ynab_token=False, has_coinbase_cli=True)
        )


class TestSolanaIndependentOfCoinbase(unittest.TestCase):
    def test_explicit_live_solana_when_cb_skipped(self):
        called = {}

        def fake_solana(*, prefer_live=True, config=None, snapshot_path=None):
            called["prefer_live"] = prefer_live
            return {"source": "live", "as_of": "now", "book_usd": 9.58}

        with mock.patch("treasury.solana_sync.fetch_solana", side_effect=fake_solana):
            with mock.patch(
                "treasury.adapters.fetch_coinbase_liquid",
                return_value={"source": "snapshot", "as_of": "old"},
            ):
                with mock.patch("treasury.adapters.fetch_robinhood", return_value={}):
                    with mock.patch("treasury.ynab_sync.fetch_one_card", return_value={}):
                        with mock.patch(
                            "treasury.ynab_sync.fetch_rh_checking", return_value={}
                        ):
                            with mock.patch(
                                "treasury.expenses_sync.fetch_expenses",
                                return_value={},
                            ):
                                with mock.patch(
                                    "treasury.morpho_hy_sync.fetch_morpho_hy",
                                    return_value={"source": "empty"},
                                ):
                                    with mock.patch(
                                        "treasury.usdg_hy_sync.fetch_usdg_hy",
                                        return_value={"source": "empty"},
                                    ):
                                        with mock.patch(
                                            "treasury.morpho_borrow_sync.fetch_morpho_borrow",
                                            return_value={"source": "empty"},
                                        ):
                                            with mock.patch(
                                                "treasury.solstice_jr_sync.fetch_solstice_jr",
                                                return_value={"source": "empty"},
                                            ):
                                                snap = build_snapshot(
                                                    prefer_live_coinbase=False,
                                                    prefer_live_ynab=True,
                                                    prefer_live_solana=True,
                                                    config={
                                                        "policy": {},
                                                        "coinbase_manual": {},
                                                    },
                                                )
        self.assertTrue(called.get("prefer_live"))
        self.assertAlmostEqual(snap["solana"]["book_usd"], 9.58)


if __name__ == "__main__":
    unittest.main()
