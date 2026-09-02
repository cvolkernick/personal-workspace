"""Tests for YNAB One Card normalization (no live token required)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.ynab_sync import (  # noqa: E402
    _roll_up_x_money,
    _snapshot_needs_live_refresh,
    account_last4,
    milli_to_units,
    normalize_one_card,
    normalize_rh_checking,
    normalize_x_money,
    pick_one_card_account,
    pick_rh_checking_account,
    pick_x_money_account,
    pick_x_money_spaces,
)
from treasury.policy import evaluate_treasury  # noqa: E402


class TestMilli(unittest.TestCase):
    def test_milli(self):
        self.assertAlmostEqual(milli_to_units(-418550), -418.55)


class TestSnapshotNeedsRefresh(unittest.TestCase):
    def test_missing_and_error_need_refresh(self):
        self.assertTrue(_snapshot_needs_live_refresh(None))
        self.assertTrue(_snapshot_needs_live_refresh({"source": "empty"}))
        self.assertTrue(
            _snapshot_needs_live_refresh(
                {"source": "ynab", "as_of": "2026-08-10T00:00:00+00:00", "live_error": "boom"}
            )
        )

    def test_fresh_file_skips_refresh(self):
        from datetime import datetime, timezone

        fresh = datetime.now(timezone.utc).isoformat()
        self.assertFalse(
            _snapshot_needs_live_refresh(
                {"source": "ynab", "as_of": fresh}, max_age_hours=6.0
            )
        )

    def test_aged_file_needs_refresh(self):
        self.assertTrue(
            _snapshot_needs_live_refresh(
                {"source": "ynab", "as_of": "2026-08-08T06:00:00+00:00"},
                max_age_hours=6.0,
            )
        )


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

    def test_prefers_rh_checking(self):
        accts = [
            {"name": "Coinbase One Card – 5361", "type": "creditCard", "deleted": False, "closed": False},
            {"name": "RH Checking – 3646", "type": "checking", "deleted": False, "closed": False, "id": "rh"},
            {"name": "Other Checking", "type": "checking", "deleted": False, "closed": False, "id": "xm"},
        ]
        a = pick_rh_checking_account(accts)
        self.assertEqual(a["name"], "RH Checking – 3646")

    def test_prefers_x_money_or_leftover_checking(self):
        accts = [
            {"name": "RH Checking – 3646", "type": "checking", "deleted": False, "closed": False, "id": "rh"},
            {"name": "Checking – 2201", "type": "checking", "deleted": False, "closed": False, "id": "xm"},
        ]
        a = pick_x_money_account(accts, exclude_ids={"rh"})
        self.assertEqual(a["name"], "Checking – 2201")
        named = [
            {"name": "X Money", "type": "cash", "deleted": False, "closed": False, "id": "x1"},
            {"name": "Checking – 2201", "type": "checking", "deleted": False, "closed": False, "id": "c1"},
        ]
        self.assertEqual(pick_x_money_account(named)["name"], "X Money")

    def test_last4_pin_beats_everyday_checking(self):
        accts = [
            {"name": "RH Checking – 3646", "type": "checking", "deleted": False, "closed": False, "id": "rh"},
            {"name": "Main – 2201", "type": "checking", "deleted": False, "closed": False, "id": "main"},
            {"name": "Auto Fleet – 0895", "type": "checking", "deleted": False, "closed": False, "id": "af"},
            {"name": "Collateral – 3326", "type": "checking", "deleted": False, "closed": False, "id": "col"},
            {"name": "Utilities – 4867", "type": "checking", "deleted": False, "closed": False, "id": "util"},
            {
                "name": "EveryDay Checking – 8680",
                "type": "checking",
                "deleted": False,
                "closed": False,
                "id": "navy",
            },
        ]
        a = pick_x_money_account(
            accts,
            prefer_name="Checking – 2201",
            exclude_ids={"rh"},
            prefer_last4="2201",
            exclude_last4=["8680"],
        )
        self.assertEqual(a["name"], "Main – 2201")
        a2 = pick_x_money_account(
            accts,
            prefer_name="Main – 2201",
            exclude_ids={"rh"},
            prefer_last4="2201",
            exclude_last4=["8680"],
        )
        self.assertEqual(a2["name"], "Main – 2201")

    def test_exclude_last4_never_picks_navy_federal(self):
        accts = [
            {
                "name": "EveryDay Checking – 8680",
                "type": "checking",
                "deleted": False,
                "closed": False,
                "id": "navy",
            },
            {"name": "Main – 2201", "type": "checking", "deleted": False, "closed": False, "id": "main"},
        ]
        a = pick_x_money_account(
            accts, prefer_last4="2201", exclude_last4=["8680"]
        )
        self.assertEqual(a["name"], "Main – 2201")
        # Last-4 pin miss must not fall through to 8680
        self.assertIsNone(
            pick_x_money_account(
                accts,
                prefer_last4="9999",
                exclude_last4=["8680"],
            )
        )

    def test_multiple_leftover_checkings_do_not_guess(self):
        accts = [
            {"name": "Main – 2201", "type": "checking", "deleted": False, "closed": False, "id": "main"},
            {
                "name": "EveryDay Checking – 8680",
                "type": "checking",
                "deleted": False,
                "closed": False,
                "id": "navy",
            },
        ]
        self.assertIsNone(pick_x_money_account(accts))

    def test_account_last4(self):
        self.assertEqual(account_last4("Main – 2201"), "2201")
        self.assertEqual(account_last4("Checking – 2201"), "2201")
        self.assertEqual(account_last4("EveryDay Checking – 8680"), "8680")
        self.assertIsNone(account_last4("X Money"))

    def test_pick_x_money_spaces(self):
        accts = [
            {"name": "RH Checking – 3646", "type": "checking", "deleted": False, "closed": False, "id": "rh"},
            {"name": "Main – 2201", "type": "checking", "deleted": False, "closed": False, "id": "main"},
            {"name": "Auto Fleet – 0895", "type": "checking", "deleted": False, "closed": False, "id": "af"},
            {"name": "Collateral – 3326", "type": "checking", "deleted": False, "closed": False, "id": "col"},
            {"name": "Utilities – 4867", "type": "checking", "deleted": False, "closed": False, "id": "util"},
            {
                "name": "EveryDay Checking – 8680",
                "type": "checking",
                "deleted": False,
                "closed": False,
                "id": "navy",
            },
        ]
        spaces = pick_x_money_spaces(
            accts,
            ["Auto Fleet", "Collateral", "Utilities"],
            exclude_ids={"rh", "main"},
            exclude_last4=["8680"],
        )
        self.assertEqual(
            [s["name"] for s in spaces],
            ["Auto Fleet – 0895", "Collateral – 3326", "Utilities – 4867"],
        )


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


class TestNormalizeChecking(unittest.TestCase):
    def test_rh_checking_cash(self):
        account = {
            "id": "c1",
            "name": "RH Checking – 3646",
            "type": "checking",
            "balance": 2430,
            "balance_currency": 2.43,
            "cleared_balance": 2430,
            "uncleared_balance": 0,
            "direct_import_linked": True,
        }
        txs = [
            {
                "id": "t1",
                "date": "2026-07-10",
                "payee_name": "GM Financial",
                "amount": -50000,
                "deleted": False,
            },
            {
                "id": "t2",
                "date": "2026-07-12",
                "payee_name": "Transfer In",
                "amount": 100000,
                "deleted": False,
            },
        ]
        snap = normalize_rh_checking(
            account, txs, budget_id="b1", budget_name="Chris's Plan"
        )
        self.assertAlmostEqual(snap["cash"], 2.43)
        self.assertAlmostEqual(snap["spend_30d"], 50.0)
        self.assertAlmostEqual(snap["inflow_30d"], 100.0)

    def test_x_money_cash(self):
        account = {
            "id": "x1",
            "name": "Checking – 2201",
            "type": "checking",
            "balance": 22760,
            "balance_currency": 22.76,
            "cleared_balance": 22760,
            "uncleared_balance": 0,
            "direct_import_linked": True,
        }
        snap = normalize_x_money(account, [], budget_id="b1", budget_name="Chris's Plan")
        self.assertAlmostEqual(snap["cash"], 22.76)
        self.assertAlmostEqual(snap["available"], 22.76)
        self.assertEqual(snap["account_name"], "Checking – 2201")

    def test_x_money_signed_overdraft(self):
        account = {
            "id": "x1",
            "name": "Main – 2201",
            "type": "checking",
            "balance": -116500,
            "balance_currency": -116.50,
            "cleared_balance": -116500,
            "uncleared_balance": 0,
            "direct_import_linked": True,
        }
        snap = normalize_x_money(account, [], budget_id="b1", budget_name="Chris's Plan")
        self.assertAlmostEqual(snap["cash"], -116.50)
        self.assertAlmostEqual(snap["available"], 0.0)

    def test_x_money_space_rollup(self):
        main = normalize_x_money(
            {
                "id": "main",
                "name": "Main – 2201",
                "type": "checking",
                "balance": -116500,
                "balance_currency": -116.50,
            },
            [],
            budget_id="b1",
            budget_name="Chris's Plan",
        )
        fleet = normalize_x_money(
            {
                "id": "af",
                "name": "Auto Fleet – 0895",
                "type": "checking",
                "balance": 997460,
                "balance_currency": 997.46,
            },
            [],
            budget_id="b1",
            budget_name="Chris's Plan",
        )
        rolled = _roll_up_x_money(main, [fleet])
        self.assertEqual(rolled["account_name"], "Main – 2201")
        self.assertAlmostEqual(rolled["main_cash"], -116.50)
        self.assertAlmostEqual(rolled["cash"], 880.96)
        self.assertAlmostEqual(rolled["available"], 997.46)
        self.assertEqual(rolled["spaces"][0]["account_name"], "Auto Fleet – 0895")
        self.assertEqual(rolled["spaces"][0]["last4"], "0895")


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
            "rh_checking": {
                "source": "ynab",
                "cash": 2.43,
                "account_name": "RH Checking – 3646",
            },
            "x_money": {
                "source": "ynab",
                "cash": 22.76,
                "account_name": "Checking – 2201",
                "apy_est": 0.06,
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
        self.assertAlmostEqual(ev["inputs"]["rh_checking_cash"], 2.43)
        self.assertAlmostEqual(ev["inputs"]["x_money_cash"], 22.76)
        self.assertAlmostEqual(ev["inputs"]["x_money_apy_est"], 0.06)
        self.assertAlmostEqual(ev["inputs"]["bank_cash"], 25.19)
        self.assertAlmostEqual(ev["inputs"]["bill_pay_cash"], 2.43)
        self.assertNotIn("card_balance", ev["data_quality"]["missing_manual_fields"])
        kinds = [a["kind"] for a in ev["actions"]]
        self.assertIn("cash_stack", kinds)


if __name__ == "__main__":
    unittest.main()
