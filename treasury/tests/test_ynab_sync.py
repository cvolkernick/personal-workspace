"""Tests for YNAB One Card normalization (no live token required)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.ynab_sync import (  # noqa: E402
    milli_to_units,
    normalize_one_card,
    pick_one_card_account,
)
from treasury.policy import evaluate_treasury  # noqa: E402


class TestMilli(unittest.TestCase):
    def test_milli(self):
        self.assertAlmostEqual(milli_to_units(-418550), -418.55)


class TestPickAccount(unittest.TestCase):
    def test_prefers_coinbase_one_card(self):
        accts = [
            {"name": "Checking", "type": "checking", "deleted": False, "closed": False},
            {
                "name": "Coinbase One Card – 5361",
                "type": "creditCard",
                "deleted": False,
                "closed": False,
            },
        ]
        a = pick_one_card_account(accts)
        self.assertEqual(a["name"], "Coinbase One Card – 5361")


class TestNormalize(unittest.TestCase):
    def test_balance_owed_and_spend(self):
        account = {
            "id": "a1",
            "name": "Coinbase One Card – 5361",
            "type": "creditCard",
            "balance": -418550,
            "balance_currency": -418.55,
            "cleared_balance": -418550,
            "uncleared_balance": 0,
            "direct_import_linked": True,
            "direct_import_in_error": False,
        }
        txs = [
            {
                "id": "t1",
                "date": "2026-07-17",
                "payee_name": "Starting Balance",
                "amount": -465870,
                "deleted": False,
            },
            {
                "id": "t2",
                "date": "2026-07-17",
                "payee_name": "Payment",
                "amount": 47320,
                "deleted": False,
            },
            {
                "id": "t3",
                "date": "2026-07-10",
                "payee_name": "Coffee Shop",
                "amount": -12500,
                "deleted": False,
            },
        ]
        snap = normalize_one_card(
            account, txs, budget_id="b1", budget_name="Chris's Plan"
        )
        self.assertEqual(snap["source"], "ynab")
        self.assertAlmostEqual(snap["balance_owed"], 418.55)
        self.assertAlmostEqual(snap["card_balance"], 418.55)
        self.assertAlmostEqual(snap["spend_30d"], 12.5)
        self.assertEqual(snap["account_name"], "Coinbase One Card – 5361")


class TestPolicyUsesOneCard(unittest.TestCase):
    def test_card_from_one_card_snapshot(self):
        snap = {
            "coinbase": {"liquid_usdc": 100, "liquid_btc": 0, "source": "live"},
            "coinbase_manual": {},
            "one_card": {
                "source": "ynab",
                "as_of": "2026-07-17T00:00:00+00:00",
                "card_balance": 418.55,
                "balance_owed": 418.55,
                "spend_30d": 12.5,
                "account_name": "Coinbase One Card – 5361",
            },
            "robinhood": {
                "buying_power": 2000,
                "cash": 1000,
                "equity_value": 10000,
                "source": "live",
            },
        }
        ev = evaluate_treasury(snap)
        self.assertAlmostEqual(ev["inputs"]["card_balance"], 418.55)
        self.assertEqual(ev["inputs"]["card_source"], "ynab")
        self.assertNotIn("card_balance", ev["data_quality"]["missing_manual_fields"])
        kinds = [a["kind"] for a in ev["actions"]]
        self.assertIn("card_paydown", kinds)


if __name__ == "__main__":
    unittest.main()
