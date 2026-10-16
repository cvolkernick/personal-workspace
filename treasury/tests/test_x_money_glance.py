"""Glance X Money row: Plaid current by account_id (#996)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.x_money_glance import (
    attach_plaid_x_money,
    is_stale,
    pins_from_config,
    read_prior_plaid_x_money,
    shape_plaid_x_money,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
PINS = [
    {"slot": "main", "label": "X Money Main", "account_id": "id-main"},
    {"slot": "auto_fleet", "label": "X Money Auto Fleet", "account_id": "id-fleet"},
    {"slot": "collateral", "label": "X Money Collateral", "account_id": "id-collat"},
    {"slot": "utilities", "label": "X Money Utilities", "account_id": "id-util"},
]


def _acct(account_id: str, name: str, current: float, available: float) -> dict:
    return {
        "account_id": account_id,
        "name": name,
        "official_name": "Checking",
        "mask": "0000",
        "balances": {"current": current, "available": available, "iso_currency_code": "USD"},
    }


def _payload(accounts, *, item_error=None, as_of=None):
    body = {"accounts": accounts, "item": {"item_id": "item"}}
    if item_error:
        body["item"]["error"] = item_error
    if as_of:
        body["as_of"] = as_of
    return body


class TestXMoneyGlance(unittest.TestCase):
    def test_pins_default_and_override(self):
        defaults = pins_from_config({})
        self.assertEqual(
            [row["label"] for row in defaults],
            [pin["label"] for pin in PINS],
        )
        self.assertTrue(all(row["account_id"] for row in defaults))
        overridden = pins_from_config({"plaid_x_money": {"accounts": PINS}})
        self.assertEqual([row["account_id"] for row in overridden], [p["account_id"] for p in PINS])

    def test_current_not_available_and_rename_keeps_slot(self):
        row = shape_plaid_x_money(
            _payload(
                [
                    _acct("id-util", "Power", 10, 99),
                    _acct("id-main", "Renamed Operating", 71.54, 0),
                    _acct("id-fleet", "Cars", 12.5, 100),
                    _acct("id-collat", "Hold", 3, 3),
                ]
            ),
            PINS,
            now=NOW,
        )
        self.assertFalse(is_stale(row, now=NOW))
        by_label = {a["label"]: a["current"] for a in row["accounts"]}
        self.assertEqual(
            by_label,
            {
                "X Money Main": 71.54,
                "X Money Auto Fleet": 12.5,
                "X Money Collateral": 3.0,
                "X Money Utilities": 10.0,
            },
        )
        self.assertIsNone(row["warning"])
        self.assertIsNone(row["follow_up"])

    def test_display_name_does_not_match(self):
        row = shape_plaid_x_money(
            _payload([_acct("other-id", "Main", 999, 999)]),
            PINS,
            now=NOW,
        )
        self.assertIsNone(row["accounts"][0]["current"])
        self.assertIn("missing", row["warning"])
        self.assertIn("unexpected", row["warning"])
        self.assertEqual(len(row["accounts"]), 4)

    def test_missing_account_warns_instead_of_subset(self):
        row = shape_plaid_x_money(
            _payload(
                [
                    _acct("id-main", "Main", 1, 0),
                    _acct("id-fleet", "Auto Fleet", 2, 0),
                    _acct("id-util", "Utilities", 4, 0),
                ]
            ),
            PINS,
            now=NOW,
        )
        self.assertEqual(len(row["accounts"]), 4)
        self.assertEqual(row["accounts"][2]["current"], None)
        self.assertIn("X Money Collateral", row["warning"])
        self.assertNotIn("silent", row["warning"] or "")

    def test_default_is_follow_up_not_a_chip(self):
        accounts = [
            _acct("id-main", "Main", 1, 0),
            _acct("id-fleet", "Auto Fleet", 2, 0),
            _acct("id-collat", "Collateral", 3, 0),
            _acct("id-util", "Utilities", 4, 0),
            _acct("id-default", "Default", 516.22, 516.22),
        ]
        row = shape_plaid_x_money(_payload(accounts), PINS, now=NOW)
        self.assertEqual([a["label"] for a in row["accounts"]], [p["label"] for p in PINS])
        self.assertIsNone(row["warning"])
        self.assertIn("Default", row["follow_up"])
        self.assertNotIn("516", row["follow_up"])
        self.assertTrue(all(a["account_id"] != "id-default" for a in row["accounts"]))

    def test_item_error_hides_last_known_balances(self):
        row = shape_plaid_x_money(
            _payload(
                [_acct("id-main", "Main", 71.54, 71.54)],
                item_error={"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "login"},
            ),
            PINS,
            now=NOW,
        )
        self.assertTrue(row["stale"])
        self.assertTrue(is_stale(row, now=NOW))
        self.assertTrue(all(a["current"] is None for a in row["accounts"]))
        self.assertIn("ITEM_LOGIN_REQUIRED", row["stale_reason"])

    def test_aged_reading_is_stale(self):
        old = (NOW - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = shape_plaid_x_money(
            _payload([_acct(p["account_id"], p["label"], 5, 5) for p in PINS], as_of=old),
            PINS,
            now=NOW,
        )
        self.assertTrue(row["stale"])
        self.assertTrue(all(a["current"] is None for a in row["accounts"]))
        fresh = shape_plaid_x_money(
            _payload([_acct(p["account_id"], "x", 5, 1) for p in PINS]),
            PINS,
            now=NOW,
        )
        self.assertFalse(is_stale(fresh, now=NOW + timedelta(hours=5)))
        self.assertTrue(is_stale(fresh, now=NOW + timedelta(hours=7)))

    def test_live_failure_does_not_keep_prior_balances(self):
        prior = shape_plaid_x_money(
            _payload([_acct(p["account_id"], "x", 40, 0) for p in PINS]),
            PINS,
            now=NOW,
        )

        def boom():
            raise RuntimeError("Plaid network error: timed out")

        failed = attach_plaid_x_money(
            config={"plaid_x_money": {"accounts": PINS}},
            prefer_live=True,
            prior=prior,
            now=NOW,
            fetch=boom,
        )
        self.assertTrue(failed["stale"])
        self.assertTrue(all(a["current"] is None for a in failed["accounts"]))
        self.assertNotIn("40", failed["stale_reason"])

    def test_offline_keeps_prior_block(self):
        prior = {"source": "plaid", "ok": True, "stale": False, "accounts": [{"label": "X Money Main"}]}
        kept = attach_plaid_x_money(
            config={"plaid_x_money": {"accounts": PINS}},
            prefer_live=False,
            prior=prior,
            now=NOW,
            fetch=lambda: (_ for _ in ()).throw(AssertionError("offline must not fetch")),
        )
        self.assertIs(kept, prior)


class TestPriorPlaidFallback(unittest.TestCase):
    def test_ynab_x_money_file_is_not_a_plaid_block(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x_money_latest.json"
            path.write_text(
                json.dumps({"source": "ynab", "balance": 19.04}) + "\n",
                encoding="utf-8",
            )
            self.assertIsNone(read_prior_plaid_x_money(path))

    def test_torn_latest_falls_through_to_plaid_block(self):
        with tempfile.TemporaryDirectory() as td:
            torn = Path(td) / "treasury_latest.json"
            torn.write_text('{"snapshot":', encoding="utf-8")
            alt = Path(td) / "x_money_latest.json"
            block = {"source": "plaid", "ok": True, "stale": False, "accounts": []}
            alt.write_text(json.dumps(block) + "\n", encoding="utf-8")
            self.assertEqual(read_prior_plaid_x_money(torn, alt), block)

    def test_evaluation_doc_still_wins(self):
        with tempfile.TemporaryDirectory() as td:
            latest = Path(td) / "treasury_latest.json"
            block = {"source": "plaid", "ok": True, "accounts": [{"label": "Main"}]}
            latest.write_text(
                json.dumps({"snapshot": {"plaid_x_money": block}}) + "\n",
                encoding="utf-8",
            )
            alt = Path(td) / "x_money_latest.json"
            alt.write_text(
                json.dumps({"source": "plaid", "ok": False, "accounts": []}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(read_prior_plaid_x_money(latest, alt)["accounts"][0]["label"], "Main")


if __name__ == "__main__":
    unittest.main()
