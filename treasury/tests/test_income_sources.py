"""Shared Lyft / Grubhub / Turo matcher (#936) and Braiins mining income (#948)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.income_sources import (  # noqa: E402
    BRAIINS_PAYOUT_ADDRESS_ENV,
    INCOME_SOURCE_LINES,
    bitcoin_history_start,
    bitcoin_mining_income,
    bitcoin_trailing_mean,
    classify_income_source,
    income_source_public,
)

ADDR = "bc1qexamplepayoutaddrzzzz"
TODAY = date(2026, 9, 11)


class TestClassifyIncomeSource(unittest.TestCase):
    def test_colors_are_the_chart_contract(self) -> None:
        public = {row["id"]: row for row in income_source_public()}
        self.assertEqual(public["lyft"]["color"], "#ff69b4")
        self.assertEqual(public["grubhub"]["color"], "#ff8c1a")
        self.assertEqual(public["turo"]["color"], "#b7c0c8")
        self.assertEqual(public["lyft"]["label"], "Lyft")
        self.assertEqual(public["grubhub"]["label"], "Grubhub")
        self.assertEqual(public["turo"]["label"], "Turo")
        self.assertEqual(
            [row["id"] for row in INCOME_SOURCE_LINES],
            ["lyft", "grubhub", "turo"],
        )
        for row in income_source_public():
            self.assertNotIn("needles", row)

    def test_payee_and_category_are_case_insensitive_tokens(self) -> None:
        self.assertEqual(classify_income_source("Lyft Inc"), "lyft")
        self.assertEqual(classify_income_source("LYFT"), "lyft")
        self.assertEqual(classify_income_source("Payout", "Lyft"), "lyft")
        self.assertEqual(classify_income_source("HW*GrubHub Holdings Inc."), "grubhub")
        self.assertEqual(classify_income_source("Daily pay", "Grub"), "grubhub")
        self.assertEqual(classify_income_source("grubhub"), "grubhub")
        self.assertEqual(classify_income_source("TURO payout"), "turo")
        self.assertEqual(classify_income_source("Stripe", "Turo"), "turo")

    def test_short_grub_needle_does_not_eat_unrelated_words(self) -> None:
        self.assertIsNone(classify_income_source("Grubby"))
        self.assertIsNone(classify_income_source("Groceries"))
        self.assertIsNone(classify_income_source("Tutorial"))
        self.assertIsNone(classify_income_source(""))
        self.assertIsNone(classify_income_source(None, None))

    def test_payee_wins_when_category_disagrees(self) -> None:
        self.assertEqual(classify_income_source("Lyft", "Turo"), "lyft")
        self.assertEqual(classify_income_source("Turo", "Grubhub"), "turo")
        self.assertEqual(classify_income_source("Daily pay", "Grubhub"), "grubhub")


def _ts(day: str) -> int:
    return int(datetime.fromisoformat(day + "T12:00:00+00:00").timestamp())


def _tx(txid: str, *, day: str | None, confirmed: bool, spends: bool, sats: int) -> dict:
    vin_addr = ADDR if spends else "bc1qpoolpaysminerzzzz"
    status: dict = {"confirmed": confirmed}
    if day is not None:
        status["block_time"] = _ts(day)
    return {
        "txid": txid,
        "status": status,
        "vin": [{"prevout": {"scriptpubkey_address": vin_addr, "value": sats + 1000}}],
        "vout": [
            {"scriptpubkey_address": ADDR, "value": sats},
            {"scriptpubkey_address": "bc1qsomewhereelsezzzz", "value": 1000},
        ],
    }


class TestBitcoinMiningIncome(unittest.TestCase):
    def test_history_start_covers_the_first_90_day_window(self) -> None:
        self.assertEqual(bitcoin_history_start(TODAY), date(2026, 3, 17))

    def test_fixture_counts_deposit_and_drops_sweep_and_unconfirmed(self) -> None:
        # Two outputs to the address on one confirmed payout: 100000 + 50000 sats.
        deposit = _tx("dep", day="2026-09-01", confirmed=True, spends=False, sats=100_000)
        deposit["vout"].append({"scriptpubkey_address": ADDR, "value": 50_000})
        sweep = _tx("sweep", day="2026-09-02", confirmed=True, spends=True, sats=9_000)
        pending = _tx("pending", day=None, confirmed=False, spends=False, sats=100_000)
        pending["status"] = {"confirmed": False}
        out = bitcoin_mining_income(
            today=TODAY,
            address=ADDR,
            transactions=[deposit, sweep, pending],
            prices={"2026-09-01": 100_000.0, "2026-09-02": 100_000.0},
        )
        self.assertTrue(out["ok"])
        self.assertIsNone(out["error"])
        self.assertTrue(out["address_set"])
        self.assertFalse(out["from_cache"])
        self.assertEqual([row["txid"] for row in out["deposits"]], ["dep"])
        self.assertEqual(out["deposits"][0]["sats"], 150_000)
        self.assertEqual(out["deposits"][0]["usd"], 150.0)
        self.assertEqual(out["usd_by_day"], {"2026-09-01": 150.0})
        blob = json.dumps(out)
        self.assertNotIn(ADDR, blob)

    def test_90_day_mean_includes_boundary_day_and_drops_the_day_before(self) -> None:
        series = {"2026-06-14": 9000.0, "2026-03-16": 9000.0, "2026-03-17": 1800.0}
        self.assertEqual(bitcoin_trailing_mean(date(2026, 9, 11), series), 100.0)
        self.assertEqual(bitcoin_trailing_mean(date(2026, 6, 14), series), 120.0)
        self.assertEqual(bitcoin_trailing_mean(date(2026, 6, 15), series), 100.0)
        self.assertEqual(
            bitcoin_trailing_mean(date(2026, 6, 14), {"2026-03-16": 9000.0}),
            0.0,
        )

    def test_empty_response_retries_once_then_paginates(self) -> None:
        recent = _tx("recent", day="2026-09-01", confirmed=True, spends=False, sats=100_000)
        older = _tx("older", day="2026-03-17", confirmed=True, spends=False, sats=50_000)
        calls: list[str] = []

        def fetch_json(url: str):
            calls.append(url)
            if url.endswith("/txs"):
                if calls.count(url) == 1:
                    return []
                return [recent]
            if url.endswith("/txs/chain/recent"):
                return [older]
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "braiins_address_income.json"
            out = bitcoin_mining_income(
                today=TODAY,
                address=ADDR,
                prices={"2026-09-01": 100_000.0, "2026-03-17": 80_000.0},
                fetch_json=fetch_json,
                cache_path=path,
            )
            cached_text = path.read_text(encoding="utf-8")
        self.assertEqual([row["txid"] for row in out["deposits"]], ["recent", "older"])
        self.assertEqual(out["usd_by_day"]["2026-09-01"], 100.0)
        self.assertEqual(out["usd_by_day"]["2026-03-17"], 40.0)
        self.assertFalse(out["from_cache"])
        self.assertGreaterEqual(sum(1 for url in calls if url.endswith("/txs")), 2)
        self.assertTrue(any(url.endswith("/txs/chain/recent") for url in calls))
        self.assertNotIn(ADDR, json.dumps(out))
        self.assertNotIn(ADDR, cached_text)

    def test_api_error_uses_cache_and_does_not_wipe_it(self) -> None:
        cached = {
            "address_sha256": hashlib.sha256(ADDR.encode()).hexdigest(),
            "deposits": [
                {
                    "txid": "cached",
                    "day": "2026-08-01",
                    "sats": 100_000,
                    "btc": 0.001,
                    "close": 50000.0,
                    "usd": 50.0,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "braiins_address_income.json"
            path.write_text(json.dumps(cached), encoding="utf-8")

            def fetch_json(url: str):
                raise OSError("down")

            out = bitcoin_mining_income(
                today=TODAY,
                address=ADDR,
                fetch_json=fetch_json,
                cache_path=path,
            )
            self.assertTrue(out["ok"])
            self.assertIsNone(out["error"])
            self.assertTrue(out["from_cache"])
            self.assertEqual(out["usd_by_day"], {"2026-08-01": 50.0})
            self.assertNotIn(ADDR, path.read_text(encoding="utf-8"))
            self.assertNotIn(ADDR, json.dumps(out))

            def empty_twice(url: str):
                return []

            again = bitcoin_mining_income(
                today=TODAY,
                address=ADDR,
                fetch_json=empty_twice,
                cache_path=path,
            )
            self.assertTrue(again["from_cache"])
            self.assertEqual(again["usd_by_day"], {"2026-08-01": 50.0})
            self.assertIn("cached", path.read_text(encoding="utf-8"))

    def test_unset_env_yields_zero_without_error_or_fetch(self) -> None:
        def fetch_json(url: str):
            raise AssertionError(url)

        with mock.patch.dict(os.environ, {}, clear=True):
            out = bitcoin_mining_income(today=TODAY, fetch_json=fetch_json)
        self.assertTrue(out["ok"])
        self.assertIsNone(out["error"])
        self.assertFalse(out["address_set"])
        self.assertFalse(out["from_cache"])
        self.assertEqual(out["deposits"], [])
        self.assertEqual(out["usd_by_day"], {})

    def test_cache_filename_is_gitignored(self) -> None:
        text = (ROOT / "treasury" / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("braiins_address_income.json", text)
        self.assertNotIn(BRAIINS_PAYOUT_ADDRESS_ENV, text)


if __name__ == "__main__":
    unittest.main()
