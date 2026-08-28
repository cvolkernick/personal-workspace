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
    SNAPSHOT_TX_LIMIT,
    _newest_snapshot_txs,
    _report_from_writes,
    _safe_write,
    _snapshot_needs_live_refresh,
    _summarize_txs,
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

    def test_closed_pin_does_not_steal_other_checking(self):
        accts = [
            {
                "id": "other",
                "name": "Other Checking",
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
        self.assertIsNone(
            pick_x_money_account(
                accts, prefer_id=XM_PIN, prefer_name="Checking – 2201"
            )
        )


class TestConfigPin(unittest.TestCase):
    def test_x_money_id_and_budget_pinned(self):
        from treasury.adapters import load_config

        ynab = load_config().get("ynab") or {}
        self.assertEqual(ynab.get("x_money_account_id"), XM_PIN)
        self.assertEqual(ynab.get("budget_name"), "Chris's Plan")
        self.assertEqual(fold_dashes(ynab.get("x_money_account_name")), fold_dashes("Checking – 2201"))


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


def _oldest_first_july_august(
    *,
    july_n: int = 50,
    august_n: int = 12,
    july_milli: int = -10000,
    august_milli: int = -5000,
) -> list:
    """YNAB since_date order: oldest first. Recreates the 2026-08-28 prism slice."""
    txs = []
    for i in range(july_n):
        day = 21 + (i % 8)  # 2026-07-21 .. 2026-07-28
        txs.append(
            {
                "id": f"jul-{i:03d}",
                "date": f"2026-07-{day:02d}",
                "payee_name": f"July {i}",
                "amount": july_milli,
                "deleted": False,
            }
        )
    for i in range(august_n):
        day = 1 + (i % 28)  # 2026-08-01 .. 2026-08-28
        txs.append(
            {
                "id": f"aug-{i:03d}",
                "date": f"2026-08-{day:02d}",
                "payee_name": f"August {i}",
                "amount": august_milli,
                "deleted": False,
            }
        )
    return txs


def _checking_acct(**overrides):
    acct = {
        "id": XM_PIN,
        "name": "Checking – 2201",
        "type": "checking",
        "balance": 229720,
        "balance_currency": 229.72,
        "cleared_balance": 229720,
        "uncleared_balance": 0,
        "direct_import_linked": True,
        "direct_import_in_error": False,
    }
    acct.update(overrides)
    return acct


def _card_acct(**overrides):
    acct = {
        "id": "card-1",
        "name": "Coinbase One Card – 5361",
        "type": "creditCard",
        "balance": -418550,
        "balance_currency": -418.55,
        "cleared_balance": -418550,
        "uncleared_balance": 0,
        "direct_import_linked": True,
        "direct_import_in_error": False,
    }
    acct.update(overrides)
    return acct


class TestSnapshotKeepsNewestTxs(unittest.TestCase):
    """Oldest-first API order must not produce a July-only transactions[] head."""

    def test_summarize_preserves_api_order(self):
        txs = _oldest_first_july_august(july_n=3, august_n=2)
        txs_out, _, _ = _summarize_txs(txs, account_type="checking")
        self.assertEqual([t["date"] for t in txs_out], [t["date"] for t in txs])
        self.assertTrue(txs_out[0]["date"].startswith("2026-07"))
        self.assertTrue(txs_out[-1]["date"].startswith("2026-08"))

    def test_newest_helper_drops_oldest_head(self):
        txs_out, _, _ = _summarize_txs(
            _oldest_first_july_august(july_n=50, august_n=12),
            account_type="checking",
        )
        head = txs_out[:SNAPSHOT_TX_LIMIT]
        self.assertTrue(all(t["date"].startswith("2026-07") for t in head))
        newest = _newest_snapshot_txs(txs_out)
        dates = [t["date"] for t in newest]
        self.assertTrue(any(d.startswith("2026-08") for d in dates))
        self.assertFalse(all(d.startswith("2026-07") for d in dates))
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual(len(newest), SNAPSHOT_TX_LIMIT)

    def _assert_newest_snapshot(self, snap, *, full_count):
        listed = snap["transactions"]
        dates = [t["date"] for t in listed]
        self.assertEqual(snap["transaction_count"], full_count)
        self.assertLessEqual(len(listed), SNAPSHOT_TX_LIMIT)
        self.assertTrue(any(d.startswith("2026-08") for d in dates), dates[:3])
        self.assertFalse(all(d.startswith("2026-07") for d in dates))
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertTrue(dates[0].startswith("2026-08"))

    def test_x_money_includes_august_not_july_head(self):
        txs = _oldest_first_july_august(july_n=50, august_n=12)
        snap = normalize_x_money(
            _checking_acct(), txs, budget_id="b1", budget_name="Chris's Plan"
        )
        self._assert_newest_snapshot(snap, full_count=62)

    def test_rh_checking_includes_august_not_july_head(self):
        txs = _oldest_first_july_august(july_n=50, august_n=12)
        snap = normalize_rh_checking(
            _checking_acct(id="rh", name="RH Checking – 3646", balance_currency=2.43),
            txs,
            budget_id="b1",
            budget_name="Chris's Plan",
        )
        self._assert_newest_snapshot(snap, full_count=62)

    def test_one_card_includes_august_not_july_head(self):
        txs = _oldest_first_july_august(july_n=50, august_n=12)
        snap = normalize_one_card(
            _card_acct(), txs, budget_id="b1", budget_name="Chris's Plan"
        )
        self._assert_newest_snapshot(snap, full_count=62)

    def test_spend_30d_uses_full_set_not_displayed_slice(self):
        # More than 50 txs inside the 30d window so a display slice cannot match full spend.
        outside = date.today() - timedelta(days=40)
        inside = date.today() - timedelta(days=5)
        txs = []
        for i in range(50):
            txs.append(
                {
                    "id": f"old-{i:03d}",
                    "date": outside.isoformat(),
                    "payee_name": f"Old {i}",
                    "amount": -10000,
                    "deleted": False,
                }
            )
        for i in range(60):
            txs.append(
                {
                    "id": f"new-{i:03d}",
                    "date": inside.isoformat(),
                    "payee_name": f"New {i}",
                    "amount": -5000,
                    "deleted": False,
                }
            )
        snap = normalize_x_money(
            _checking_acct(), txs, budget_id="b1", budget_name="Chris's Plan"
        )
        self.assertEqual(snap["transaction_count"], 110)
        self.assertEqual(len(snap["transactions"]), SNAPSHOT_TX_LIMIT)
        # Full 60 in-window outflows at $5, not the displayed 50 ($250) or the old head ($0).
        self.assertAlmostEqual(snap["spend_30d"], 300.0)
        self.assertAlmostEqual(snap["inflow_30d"], 0.0)
        self.assertTrue(all(t["date"] == inside.isoformat() for t in snap["transactions"]))


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
