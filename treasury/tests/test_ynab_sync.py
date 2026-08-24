"""Tests for YNAB One Card / RH Checking / X Money sync (no live token required)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.ynab_sync import (  # noqa: E402
    _report_from_writes,
    _safe_write,
    _snapshot_needs_live_refresh,
    closed_pin_reason,
    fetch_rh_checking,
    fetch_x_money,
    fold_dashes,
    milli_to_units,
    normalize_one_card,
    normalize_rh_checking,
    normalize_x_money,
    pick_one_card_account,
    pick_rh_checking_account,
    pick_x_money_account,
    write_x_money_snapshot,
    ynab_feed_soft_preserved,
    ynab_feeds_clean,
)
from treasury.policy import evaluate_treasury  # noqa: E402

XM_PIN = "cc4aa802-28b5-47bd-b8e4-fae703c98a93"
RECENT = (date.today() - timedelta(days=5)).isoformat()
RECENT2 = (date.today() - timedelta(days=3)).isoformat()


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
            _snapshot_needs_live_refresh({"source": "ynab", "as_of": fresh}, max_age_hours=6.0)
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
            {"name": "RH Checking – 3646", "type": "checking", "deleted": False, "closed": False},
            {"name": "Other Checking", "type": "checking", "deleted": False, "closed": False},
        ]
        a = pick_rh_checking_account(accts)
        self.assertEqual(a["name"], "RH Checking – 3646")

    def test_prefers_x_money_or_leftover_checking(self):
        accts = [
            {
                "name": "RH Checking – 3646",
                "type": "checking",
                "deleted": False,
                "closed": False,
                "id": "rh",
            },
            {
                "name": "Checking – 2201",
                "type": "checking",
                "deleted": False,
                "closed": False,
                "id": "xm",
            },
        ]
        a = pick_x_money_account(accts, exclude_ids={"rh"})
        self.assertEqual(a["name"], "Checking – 2201")
        named = [
            {"name": "X Money", "type": "cash", "deleted": False, "closed": False, "id": "x1"},
            {
                "name": "Checking – 2201",
                "type": "checking",
                "deleted": False,
                "closed": False,
                "id": "c1",
            },
        ]
        self.assertEqual(pick_x_money_account(named)["name"], "X Money")

    def test_id_pin_wins_over_name(self):
        accts = [
            {
                "id": XM_PIN,
                "name": "X Money renamed",
                "type": "checking",
                "deleted": False,
                "closed": False,
            },
            {
                "id": "other",
                "name": "Checking – 2201",
                "type": "checking",
                "deleted": False,
                "closed": False,
            },
        ]
        a = pick_x_money_account(
            accts, prefer_name="Checking – 2201", prefer_id=XM_PIN
        )
        self.assertEqual(a["id"], XM_PIN)
        self.assertEqual(a["name"], "X Money renamed")

    def test_dash_fold_matches_en_dash_and_hyphen(self):
        self.assertEqual(fold_dashes("Checking – 2201"), fold_dashes("Checking - 2201"))
        accts = [
            {
                "id": "xm",
                "name": "Checking - 2201",
                "type": "checking",
                "deleted": False,
                "closed": False,
            }
        ]
        a = pick_x_money_account(accts, prefer_name="Checking – 2201")
        self.assertIsNotNone(a)
        self.assertEqual(a["id"], "xm")

    def test_closed_prefer_name_returns_none(self):
        accts = [
            {
                "id": "rh",
                "name": "RH Checking – 3646",
                "type": "checking",
                "deleted": False,
                "closed": False,
            },
            {
                "id": XM_PIN,
                "name": "Checking – 2201",
                "type": "checking",
                "deleted": False,
                "closed": True,
            },
        ]
        self.assertIsNone(pick_x_money_account(accts, prefer_name="Checking – 2201"))
        self.assertIsNone(pick_x_money_account(accts, prefer_id=XM_PIN))
        reason = closed_pin_reason(accts, prefer_name="Checking – 2201")
        self.assertIn("closed", reason or "")


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
                "date": RECENT,
                "payee_name": "Starting Balance",
                "amount": -465870,
                "deleted": False,
            },
            {
                "id": "t2",
                "date": RECENT,
                "payee_name": "Payment",
                "amount": 47320,
                "deleted": False,
            },
            {
                "id": "t3",
                "date": RECENT,
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
                "date": RECENT,
                "payee_name": "GM Financial",
                "amount": -50000,
                "deleted": False,
            },
            {
                "id": "t2",
                "date": RECENT2,
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
            "id": XM_PIN,
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
        self.assertEqual(snap["account_name"], "Checking – 2201")
        self.assertEqual(snap["account_id"], XM_PIN)


class TestSafeWritePreserve(unittest.TestCase):
    def test_closed_prefer_name_preserves_and_surfaces_error(self):
        accts = [
            {
                "id": "rh",
                "name": "RH Checking – 3646",
                "type": "checking",
                "deleted": False,
                "closed": False,
            },
            {
                "id": XM_PIN,
                "name": "Checking – 2201",
                "type": "checking",
                "deleted": False,
                "closed": True,
            },
        ]
        self.assertIsNone(pick_x_money_account(accts, prefer_name="Checking – 2201"))
        live_error = closed_pin_reason(accts, prefer_name="Checking – 2201")
        self.assertTrue(live_error)

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x_money_latest.json"
            prior = {
                "source": "ynab",
                "as_of": "2026-08-20T12:00:00+00:00",
                "cash": 178.14,
                "token_source": "/Users/chris/.config/ynab/token",
                "account_name": "Checking – 2201",
            }
            path.write_text(json.dumps(prior), encoding="utf-8")
            err_payload = {
                "source": "ynab",
                "as_of": "2026-08-24T05:00:00+00:00",
                "live_error": live_error,
                "token_source": "/home/prism-agent/.config/ynab/token",
            }
            wr = _safe_write(write_x_money_snapshot, err_payload, path)
            self.assertTrue(wr["preserved"])
            self.assertEqual(wr["skip_reason"], live_error)
            on_disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["as_of"], "2026-08-20T12:00:00+00:00")
            self.assertNotIn("live_error", on_disk)
            self.assertAlmostEqual(on_disk["cash"], 178.14)

            writes = {
                "one_card": {
                    "path": Path(td) / "missing_one.json",
                    "preserved": False,
                    "skip_reason": None,
                },
                "rh_checking": {
                    "path": Path(td) / "missing_rh.json",
                    "preserved": False,
                    "skip_reason": None,
                },
                "x_money": wr,
            }
            bundle = {
                "one_card": {
                    "as_of": "2026-08-24T05:00:00+00:00",
                    "token_source": "/home/prism-agent/.config/ynab/token",
                },
                "rh_checking": {
                    "as_of": "2026-08-24T05:00:00+00:00",
                    "token_source": "/home/prism-agent/.config/ynab/token",
                },
                "x_money": err_payload,
            }
            report = _report_from_writes(bundle, writes)
            self.assertTrue(ynab_feed_soft_preserved(report, "x_money"))
            self.assertFalse(ynab_feeds_clean(report))
            xm = report["x_money"]
            self.assertEqual(xm["as_of"], "2026-08-20T12:00:00+00:00")
            self.assertEqual(xm["token_source"], "/Users/chris/.config/ynab/token")
            self.assertEqual(xm["live_error"], live_error)
            self.assertEqual(xm["preserved"], live_error)

    def test_good_payload_overwrites(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x_money_latest.json"
            path.write_text(
                json.dumps({"source": "ynab", "as_of": "2026-08-20T00:00:00+00:00", "cash": 1}),
                encoding="utf-8",
            )
            fresh = {
                "source": "ynab",
                "as_of": "2026-08-24T05:00:00+00:00",
                "cash": 99.0,
                "token_source": "/home/prism-agent/.config/ynab/token",
            }
            wr = _safe_write(write_x_money_snapshot, fresh, path)
            self.assertFalse(wr["preserved"])
            on_disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertAlmostEqual(on_disk["cash"], 99.0)


class TestFetchAttachesLiveError(unittest.TestCase):
    def test_fetch_x_money_does_not_return_silent_stale(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x_money_latest.json"
            prior = {
                "source": "ynab",
                "as_of": "2026-08-20T12:00:00+00:00",
                "cash": 50.0,
                "token_source": "/Users/chris/.config/ynab/token",
            }
            path.write_text(json.dumps(prior), encoding="utf-8")
            bundle = {
                "one_card": {
                    "source": "ynab",
                    "as_of": "2026-08-24T05:00:00+00:00",
                    "token_source": "prism",
                },
                "rh_checking": {
                    "source": "ynab",
                    "as_of": "2026-08-24T05:00:00+00:00",
                    "token_source": "prism",
                },
                "x_money": {
                    "source": "ynab",
                    "as_of": "2026-08-24T05:00:00+00:00",
                    "live_error": "no X Money / non-RH checking account found in YNAB",
                    "token_source": "prism",
                },
            }
            with patch("treasury.ynab_sync.sync_ynab", return_value=bundle):
                out = fetch_x_money(prefer_live=True, snapshot_path=path, max_age_hours=0.0)
            self.assertEqual(
                out["live_error"],
                "no X Money / non-RH checking account found in YNAB",
            )
            self.assertAlmostEqual(out["cash"], 50.0)
            self.assertTrue(out.get("preserved"))
            on_disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("live_error", on_disk)
            self.assertAlmostEqual(on_disk["cash"], 50.0)

    def test_fetch_rh_checking_attaches_live_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rh_checking_latest.json"
            prior = {
                "source": "ynab",
                "as_of": "2026-08-20T12:00:00+00:00",
                "cash": 2.43,
                "token_source": "mac",
            }
            path.write_text(json.dumps(prior), encoding="utf-8")
            bundle = {
                "one_card": {"source": "ynab", "as_of": "now", "token_source": "prism"},
                "rh_checking": {
                    "source": "ynab",
                    "as_of": "now",
                    "live_error": "no RH Checking / Robinhood checking account found in YNAB",
                    "token_source": "prism",
                },
                "x_money": {"source": "ynab", "as_of": "now", "token_source": "prism"},
            }
            with patch("treasury.ynab_sync.sync_ynab", return_value=bundle):
                out = fetch_rh_checking(prefer_live=True, snapshot_path=path, max_age_hours=0.0)
            self.assertIn("live_error", out)
            self.assertAlmostEqual(out["cash"], 2.43)


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
        self.assertAlmostEqual(ev["inputs"]["bill_pay_cash"], 2.43)
        self.assertNotIn("card_balance", ev["data_quality"]["missing_manual_fields"])
        kinds = [a["kind"] for a in ev["actions"]]
        self.assertIn("card_paydown", kinds)


if __name__ == "__main__":
    unittest.main()
