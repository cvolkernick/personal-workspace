"""Runway: live YNAB 90d-forward forecast, no CDN, loud errors."""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.runway import (  # noqa: E402
    ALLOWED_DAYS,
    DEFAULT_THRESHOLD,
    LOOKBACK_DAYS,
    POLICY_MIN_BUFFER_KEY,
    build_runway,
    clamp_threshold,
    load_runway,
    parse_forecast_buffer_target,
    resolve_min_buffer,
    starting_buffer,
)

FCC = ROOT / "financial-command"
PAGE = FCC / "runway.html"
INDEX = FCC / "index.html"
TODAY = date(2026, 9, 11)

GROUPS = [
    {
        "id": "g-house",
        "name": "Housing",
        "categories": [{"id": "c-rent", "name": "Rent"}],
    },
    {
        "id": "g-food",
        "name": "Food",
        "categories": [{"id": "c-groc", "name": "Groceries"}],
    },
]


def _tx(
    *,
    amount: int,
    payee: str = "",
    date_s: str = "2026-08-01",
    category_id: str | None = None,
    category_name: str | None = None,
    transfer_account_id: str | None = None,
    account_id: str = "onb",
    deleted: bool = False,
    subtransactions=None,
) -> dict:
    row = {
        "amount": amount,
        "payee_name": payee,
        "date": date_s,
        "category_id": category_id,
        "category_name": category_name,
        "transfer_account_id": transfer_account_id,
        "account_id": account_id,
        "deleted": deleted,
    }
    if subtransactions is not None:
        row["subtransactions"] = subtransactions
    return row


def _acct(
    *,
    aid: str,
    name: str,
    typ: str,
    balance: int,
    on_budget: bool = True,
    closed: bool = False,
    deleted: bool = False,
) -> dict:
    return {
        "id": aid,
        "name": name,
        "type": typ,
        "balance": balance,
        "on_budget": on_budget,
        "closed": closed,
        "deleted": deleted,
    }


def _stx(
    *,
    amount: int,
    payee: str,
    date_next: str,
    frequency: str = "monthly",
    category_id: str | None = None,
    category_name: str | None = None,
    transfer_account_id: str | None = None,
    account_id: str = "onb",
    deleted: bool = False,
) -> dict:
    return {
        "amount": amount,
        "payee_name": payee,
        "date_next": date_next,
        "frequency": frequency,
        "category_id": category_id,
        "category_name": category_name,
        "transfer_account_id": transfer_account_id,
        "account_id": account_id,
        "deleted": deleted,
    }


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server_runway", FCC / "server.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _fresh_sheet(
    essential=None,
    fleet=None,
    as_of: str | None = None,
) -> dict:
    return {
        "source": "google_sheets",
        "as_of": as_of or datetime.now(timezone.utc).isoformat(),
        "sheet_name": "Personal Expense Sheet",
        "tabs": {
            "Essential": {
                "role": "upcoming_expense_estimates",
                "items": list(essential or []),
            },
            "Fleet": {
                "role": "fleet_ops",
                "items": list(fleet or []),
            },
        },
    }


def _sheet_item(
    *,
    item: str,
    date_s: str,
    monthly: float,
    annually: float | None = None,
    quarterly: float | None = None,
    frm: str = "X Money",
    tab: str = "Essential",
) -> dict:
    annual = annually if annually is not None else monthly
    return {
        "item": item,
        "date": date_s,
        "from": frm,
        "monthly": monthly,
        "annually": annual,
        "quarterly": quarterly if quarterly is not None else annual,
        "tab": tab,
    }


class TestRunwayBuilder(unittest.TestCase):
    def test_clamp_threshold(self) -> None:
        self.assertEqual(clamp_threshold(500), 500.0)
        self.assertEqual(clamp_threshold("250"), 250.0)
        self.assertEqual(clamp_threshold("nope"), DEFAULT_THRESHOLD)
        self.assertEqual(clamp_threshold(-10), 0.0)
        self.assertEqual(ALLOWED_DAYS, (30, 60, 90, 180))

    def test_resolve_min_buffer_from_policy(self) -> None:
        resolved = resolve_min_buffer(policy={POLICY_MIN_BUFFER_KEY: 350})
        self.assertEqual(resolved["usd"], 350.0)
        self.assertEqual(resolved["source"], "treasury_policy")
        self.assertIn("treasury policy", resolved["source_label"])
        self.assertFalse(resolved["sheet_config_drift"])

    def test_resolve_min_buffer_fallback_is_labeled(self) -> None:
        missing = resolve_min_buffer(policy={})
        self.assertEqual(missing["usd"], DEFAULT_THRESHOLD)
        self.assertEqual(missing["source"], "fallback_default")
        self.assertIn("hardcoded fallback", missing["source_label"])
        self.assertIn("missing", missing["source_label"])
        bad = resolve_min_buffer(policy={POLICY_MIN_BUFFER_KEY: "nope"})
        self.assertEqual(bad["source"], "fallback_default")
        self.assertIn("unparseable", bad["source_label"])

    def test_resolve_min_buffer_explicit_overrides_policy(self) -> None:
        resolved = resolve_min_buffer(
            explicit=500,
            policy={POLICY_MIN_BUFFER_KEY: 200},
        )
        self.assertEqual(resolved["usd"], 500.0)
        self.assertEqual(resolved["source"], "explicit")
        self.assertIn("?threshold=", resolved["source_label"])
        self.assertIn("$200", resolved["source_label"])

    def test_sheet_config_drift_is_visible(self) -> None:
        aligned = resolve_min_buffer(
            policy={POLICY_MIN_BUFFER_KEY: 200},
            sheet_forecast_buffer=200,
        )
        self.assertFalse(aligned["sheet_config_drift"])
        drifted = resolve_min_buffer(
            policy={POLICY_MIN_BUFFER_KEY: 200},
            sheet_forecast_buffer=500,
        )
        self.assertTrue(drifted["sheet_config_drift"])
        self.assertEqual(drifted["sheet_forecast_buffer_usd"], 500.0)
        self.assertEqual(drifted["usd"], 200.0)

    def test_parse_forecast_buffer_target_row(self) -> None:
        csv_text = (
            "Metric,Sep 2026,Oct 2026\n"
            "Essential,2475.76,3564\n"
            "Buffer target,200,\n"
            "Room for discretionary,-1092,-6236\n"
        )
        self.assertEqual(parse_forecast_buffer_target(csv_text), 200.0)
        self.assertIsNone(parse_forecast_buffer_target("Metric,Sep\nIncome,1\n"))

    def test_starting_buffer_excludes_credit_and_closed(self) -> None:
        total, liquid = starting_buffer(
            [
                _acct(aid="c", name="RH Checking", typ="checking", balance=800_000),
                _acct(aid="s", name="Savings", typ="savings", balance=200_000),
                _acct(aid="cash", name="Wallet", typ="cash", balance=50_000),
                _acct(aid="cc", name="One Card", typ="creditCard", balance=-400_000),
                _acct(aid="old", name="Old", typ="checking", balance=99_000, closed=True),
                _acct(aid="off", name="Off", typ="checking", balance=99_000, on_budget=False),
            ]
        )
        self.assertEqual(total, 1050.0)
        names = {a["name"] for a in liquid}
        self.assertEqual(names, {"RH Checking", "Savings", "Wallet"})

    def test_buffer_math_reconciles_and_transfers_excluded(self) -> None:
        # 90d lookback: $900 inflow, $90 grocery outflow, $80 transfer ignored.
        txs = [
            _tx(amount=900_000, payee="Lyft", date_s="2026-07-01"),
            _tx(amount=-90_000, payee="Kroger", date_s="2026-07-02", category_id="c-groc"),
            _tx(amount=80_000, payee="Move", date_s="2026-07-03", transfer_account_id="other"),
            _tx(amount=50_000, payee="Starting balance", date_s="2026-07-04"),
        ]
        accounts = [_acct(aid="onb", name="Checking", typ="checking", balance=1_000_000)]
        payload = build_runway(
            days=90,
            threshold=500,
            today=TODAY,
            transactions=txs,
            category_groups=GROUPS,
            accounts=accounts,
            on_budget_ids={"onb"},
            sheet_unreachable=True,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["today"], "2026-09-11")
        self.assertEqual(payload["days"], 90)
        self.assertEqual(payload["threshold"], 500.0)
        self.assertEqual(payload["starting_buffer"], 1000.0)
        self.assertEqual(len(payload["daily"]), 90)
        self.assertEqual(payload["daily"][0]["date"], "2026-09-11")
        self.assertEqual(payload["daily"][-1]["date"], "2026-12-09")
        income_rate = round(900.0 / LOOKBACK_DAYS, 2)
        variable_rate = round(90.0 / LOOKBACK_DAYS, 2)
        self.assertEqual(payload["assumptions"]["income_rate_daily"], income_rate)
        self.assertEqual(payload["assumptions"]["variable_rate_daily"], variable_rate)
        self.assertEqual(payload["assumptions"]["bill_source"], "none")
        prev = 1000.0
        for row in payload["daily"]:
            expected = round(prev + row["income"] - row["bills"] - row["variable"], 2)
            self.assertEqual(row["buffer"], expected)
            self.assertEqual(row["net"], round(row["income"] - row["bills"] - row["variable"], 2))
            self.assertEqual(row["income"], income_rate)
            self.assertEqual(row["variable"], variable_rate)
            self.assertEqual(row["bills"], 0.0)
            prev = row["buffer"]

    def test_scheduled_bills_labeled_and_spike(self) -> None:
        txs = [_tx(amount=900_000, payee="Lyft", date_s="2026-07-01")]
        scheduled = [
            _stx(
                amount=-1_200_000,
                payee="Landlord",
                date_next="2026-10-01",
                frequency="monthly",
                category_id="c-rent",
            )
        ]
        accounts = [_acct(aid="onb", name="Checking", typ="checking", balance=2_000_000)]
        payload = build_runway(
            days=90,
            today=TODAY,
            transactions=txs,
            scheduled=scheduled,
            category_groups=GROUPS,
            accounts=accounts,
            on_budget_ids={"onb"},
            sheet_unreachable=True,
        )
        self.assertEqual(payload["assumptions"]["bill_source"], "scheduled")
        self.assertIn("scheduled", payload["assumptions"]["bill_source_label"].lower())
        dates = {ev["date"] for ev in payload["bill_events"]}
        self.assertIn("2026-10-01", dates)
        self.assertIn("2026-11-01", dates)
        oct1 = next(r for r in payload["daily"] if r["date"] == "2026-10-01")
        self.assertEqual(oct1["bills"], 1200.0)
        self.assertTrue(any(ev["payee"] == "Landlord" and ev["amount"] == 1200.0 for ev in payload["bill_events"]))

    def test_detected_bills_when_scheduled_empty(self) -> None:
        txs = [
            _tx(amount=3_000_000, payee="Lyft", date_s="2026-06-20"),
            _tx(amount=-1_000_000, payee="Landlord", date_s="2026-06-15", category_id="c-rent"),
            _tx(amount=-1_000_000, payee="Landlord", date_s="2026-07-15", category_id="c-rent"),
            _tx(amount=-1_000_000, payee="Landlord", date_s="2026-08-15", category_id="c-rent"),
            _tx(amount=-20_000, payee="Kroger", date_s="2026-08-01", category_id="c-groc"),
            _tx(amount=-80_000, payee="Kroger", date_s="2026-08-20", category_id="c-groc"),
        ]
        accounts = [_acct(aid="onb", name="Checking", typ="checking", balance=5_000_000)]
        payload = build_runway(
            days=90,
            today=TODAY,
            transactions=txs,
            scheduled=[],
            category_groups=GROUPS,
            accounts=accounts,
            on_budget_ids={"onb"},
            sheet_unreachable=True,
        )
        self.assertEqual(payload["assumptions"]["bill_source"], "detected")
        self.assertIn("detected", payload["assumptions"]["bill_source_label"].lower())
        rent_events = [ev for ev in payload["bill_events"] if ev["payee"] == "Landlord"]
        self.assertGreaterEqual(len(rent_events), 1)
        # Groceries are irregular → stay in variable, not bills.
        self.assertTrue(all(ev["payee"] != "Kroger" for ev in payload["bill_events"]))
        # Rent category excluded from variable baseline.
        self.assertLess(payload["assumptions"]["variable_rate_daily"], 15.0)

    def test_scheduled_inflow_added_on_exact_date(self) -> None:
        txs = [_tx(amount=900_000, payee="Lyft", date_s="2026-07-01")]
        scheduled = [
            _stx(
                amount=500_000,
                payee="Bonus",
                date_next="2026-09-20",
                frequency="never",
            )
        ]
        payload = build_runway(
            days=30,
            today=TODAY,
            transactions=txs,
            scheduled=scheduled,
            category_groups=GROUPS,
            accounts=[_acct(aid="onb", name="Checking", typ="checking", balance=100_000)],
            on_budget_ids={"onb"},
            sheet_unreachable=True,
        )
        rate = payload["assumptions"]["income_rate_daily"]
        day = next(r for r in payload["daily"] if r["date"] == "2026-09-20")
        self.assertEqual(day["income"], round(rate + 500.0, 2))
        other = next(r for r in payload["daily"] if r["date"] == "2026-09-21")
        self.assertEqual(other["income"], rate)

    def test_error_is_loud(self) -> None:
        payload = build_runway(days=90, today=TODAY, error="no YNAB token")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "no YNAB token")
        self.assertEqual(payload["daily"], [])
        self.assertEqual(payload["bill_events"], [])
        self.assertIn("stale", payload["ynab"])
        self.assertIn("soft_preserved", payload["ynab"])

    def test_load_uses_injected_fetch_not_live(self) -> None:
        def fake_fetch(since: str) -> dict:
            self.assertEqual(since, "2026-06-13")
            return {
                "ok": True,
                "transactions": [_tx(amount=10_000, payee="Lyft")],
                "scheduled": [],
                "category_groups": GROUPS,
                "accounts": [_acct(aid="onb", name="Checking", typ="checking", balance=100_000)],
                "on_budget_ids": ["onb"],
                "as_of": "2026-09-11T00:00:00+00:00",
            }

        payload = load_runway(
            days=90,
            today=TODAY,
            stale=True,
            fetch=fake_fetch,
            expenses_fetch=lambda: _fresh_sheet(),
            policy={POLICY_MIN_BUFFER_KEY: 200},
            forecast_fetch=lambda: 200,
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["ynab"]["stale"])
        self.assertEqual(payload["starting_buffer"], 100.0)
        self.assertEqual(payload["ynab"]["as_of"], "2026-09-11T00:00:00+00:00")
        self.assertEqual(len(payload["daily"]), 90)
        self.assertEqual(payload["assumptions"]["bill_source"], "sheet")
        self.assertEqual(payload["threshold"], 200.0)
        self.assertEqual(payload["assumptions"]["min_buffer_source"], "treasury_policy")

    def test_load_runway_reads_config_policy_without_code_change(self) -> None:
        import tempfile

        def fake_fetch(since: str) -> dict:
            return {
                "ok": True,
                "transactions": [_tx(amount=10_000, payee="Lyft")],
                "scheduled": [],
                "category_groups": GROUPS,
                "accounts": [_acct(aid="onb", name="Checking", typ="checking", balance=100_000)],
                "on_budget_ids": ["onb"],
                "as_of": "2026-09-11T00:00:00+00:00",
            }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "treasury").mkdir()
            (root / "treasury" / "config.json").write_text(
                json.dumps({"policy": {POLICY_MIN_BUFFER_KEY: 425}}),
                encoding="utf-8",
            )
            payload = load_runway(
                days=90,
                today=TODAY,
                stale=True,
                root=root,
                fetch=fake_fetch,
                expenses_fetch=lambda: _fresh_sheet(),
                forecast_fetch=lambda: 200,
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["threshold"], 425.0)
        self.assertEqual(payload["assumptions"]["min_buffer_source"], "treasury_policy")
        self.assertTrue(payload["assumptions"]["sheet_config_drift"])
        self.assertEqual(payload["assumptions"]["sheet_forecast_buffer_usd"], 200.0)

    def test_expenses_forecast_tab_drives_drift_not_default(self) -> None:
        expenses = _fresh_sheet(
            essential=[_sheet_item(item="Rent", date_s="10/1/2026", monthly=2100.0, annually=2100.0)],
        )
        expenses["tabs"]["Forecast"] = {"buffer_target_usd": 500}
        payload = build_runway(
            days=90,
            today=TODAY,
            transactions=[_tx(amount=900_000, payee="Lyft", date_s="2026-07-01")],
            expenses=expenses,
            accounts=[_acct(aid="onb", name="Checking", typ="checking", balance=1_000_000)],
            on_budget_ids={"onb"},
            policy={POLICY_MIN_BUFFER_KEY: 200},
        )
        self.assertEqual(payload["threshold"], 200.0)
        self.assertEqual(payload["assumptions"]["min_buffer_source"], "treasury_policy")
        self.assertEqual(payload["assumptions"]["sheet_forecast_buffer_usd"], 500.0)
        self.assertTrue(payload["assumptions"]["sheet_config_drift"])

    def test_sheet_bills_once_and_monthly(self) -> None:
        expenses = _fresh_sheet(
            essential=[
                _sheet_item(
                    item="September Rent",
                    date_s="9/1/2026",
                    monthly=2100.0,
                    annually=2100.0,
                    quarterly=2100.0,
                    frm="Coinbase",
                ),
                _sheet_item(
                    item="October Rent",
                    date_s="10/1/2026",
                    monthly=2100.0,
                    annually=2100.0,
                    quarterly=2100.0,
                    frm="Coinbase",
                ),
                _sheet_item(
                    item="Planet Fitness",
                    date_s="9/17/2026",
                    monthly=27.0,
                    annually=324.0,
                    quarterly=81.0,
                ),
            ]
        )
        payload = build_runway(
            days=90,
            today=TODAY,
            transactions=[_tx(amount=900_000, payee="Lyft", date_s="2026-07-01")],
            expenses=expenses,
            accounts=[_acct(aid="onb", name="Checking", typ="checking", balance=1_000_000)],
            on_budget_ids={"onb"},
            policy={POLICY_MIN_BUFFER_KEY: 200},
            sheet_forecast_buffer=200,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["threshold"], 200.0)
        self.assertEqual(DEFAULT_THRESHOLD, 200.0)
        self.assertEqual(payload["assumptions"]["bill_source"], "sheet")
        self.assertEqual(payload["assumptions"]["min_buffer_source"], "treasury_policy")
        self.assertIn("treasury policy", payload["assumptions"]["min_buffer_source_label"])
        self.assertFalse(payload["assumptions"]["sheet_config_drift"])
        payees = {ev["payee"] for ev in payload["bill_events"]}
        self.assertIn("October Rent", payees)
        self.assertNotIn("September Rent", payees)  # dated 9/1, before today
        oct1 = next(r for r in payload["daily"] if r["date"] == "2026-10-01")
        self.assertGreaterEqual(oct1["bills"], 2100.0)
        pf = [ev for ev in payload["bill_events"] if ev["payee"] == "Planet Fitness"]
        self.assertGreaterEqual(len(pf), 3)  # monthly across 90d

    def test_stale_sheet_is_loud_not_detection(self) -> None:
        expenses = _fresh_sheet(
            essential=[_sheet_item(item="Rent", date_s="10/1/2026", monthly=2100.0, annually=2100.0)],
            as_of="2026-08-01T00:00:00+00:00",
        )
        txs = [
            _tx(amount=3_000_000, payee="Lyft", date_s="2026-06-20"),
            _tx(amount=-1_000_000, payee="Landlord", date_s="2026-06-15", category_id="c-rent"),
            _tx(amount=-1_000_000, payee="Landlord", date_s="2026-07-15", category_id="c-rent"),
            _tx(amount=-1_000_000, payee="Landlord", date_s="2026-08-15", category_id="c-rent"),
        ]
        payload = build_runway(
            days=90,
            today=TODAY,
            transactions=txs,
            expenses=expenses,
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        self.assertFalse(payload["ok"])
        self.assertIn("stale", payload["error"].lower())
        self.assertEqual(payload["daily"], [])
        self.assertTrue(payload["sheet"]["stale"])
        self.assertNotEqual(payload["assumptions"].get("bill_source"), "detected")

    def test_missing_sheet_is_loud(self) -> None:
        payload = build_runway(days=90, today=TODAY)
        self.assertFalse(payload["ok"])
        self.assertIn("missing", payload["error"].lower())
        self.assertEqual(payload["daily"], [])

    def test_sheet_beats_scheduled(self) -> None:
        expenses = _fresh_sheet(
            essential=[
                _sheet_item(
                    item="October Rent",
                    date_s="10/1/2026",
                    monthly=2100.0,
                    annually=2100.0,
                    quarterly=2100.0,
                )
            ]
        )
        scheduled = [
            _stx(
                amount=-1_200_000,
                payee="Landlord",
                date_next="2026-10-01",
                frequency="monthly",
                category_id="c-rent",
            )
        ]
        payload = build_runway(
            days=90,
            today=TODAY,
            scheduled=scheduled,
            expenses=expenses,
            category_groups=GROUPS,
            accounts=[_acct(aid="onb", name="Checking", typ="checking", balance=1_000_000)],
            on_budget_ids={"onb"},
        )
        self.assertEqual(payload["assumptions"]["bill_source"], "sheet")
        self.assertTrue(any(ev["payee"] == "October Rent" for ev in payload["bill_events"]))
        self.assertFalse(any(ev["payee"] == "Landlord" for ev in payload["bill_events"]))

    def test_progressive_one_off_excluded_from_income_rate(self) -> None:
        txs = [
            _tx(amount=900_000, payee="Lyft", date_s="2026-07-01"),
            _tx(amount=1_000_000, payee="Progressive", date_s="2026-08-15"),
        ]
        payload = build_runway(
            days=90,
            today=TODAY,
            transactions=txs,
            expenses=_fresh_sheet(),
            accounts=[_acct(aid="onb", name="Checking", typ="checking", balance=100_000)],
            on_budget_ids={"onb"},
        )
        self.assertEqual(payload["assumptions"]["income_rate_daily"], round(900.0 / LOOKBACK_DAYS, 2))
        self.assertEqual(payload["assumptions"]["income_one_offs_excluded"], 1000.0)
        self.assertEqual(payload["assumptions"]["income_one_offs"][0]["payee"], "Progressive")

    def test_empty_from_fleet_not_a_bill(self) -> None:
        expenses = _fresh_sheet(
            essential=[],
            fleet=[
                {
                    "item": "Rivian",
                    "date": "10/1/2026",
                    "from": None,
                    "monthly": 1350.0,
                    "annually": 1350.0,
                    "tab": "Fleet",
                },
                {
                    "item": "Santander",
                    "date": "10/21/2026",
                    "from": "X Money",
                    "monthly": 1082.52,
                    "annually": 1082.52,
                    "tab": "Fleet",
                },
            ],
        )
        payload = build_runway(
            days=90,
            today=TODAY,
            expenses=expenses,
            accounts=[_acct(aid="onb", name="Checking", typ="checking", balance=100_000)],
            on_budget_ids={"onb"},
        )
        payees = {ev["payee"] for ev in payload["bill_events"]}
        self.assertIn("Santander", payees)
        self.assertNotIn("Rivian", payees)

    def test_load_stale_sheet_does_not_hit_ynab_fallback(self) -> None:
        def boom(since: str) -> dict:
            raise AssertionError("YNAB must not be the silent fallback for a stale sheet")

        payload = load_runway(
            days=90,
            today=TODAY,
            fetch=boom,
            expenses_fetch=lambda: _fresh_sheet(as_of="2026-08-01T00:00:00+00:00"),
            policy={POLICY_MIN_BUFFER_KEY: 200},
            forecast_fetch=lambda: 200,
        )
        self.assertFalse(payload["ok"])
        self.assertIn("stale", payload["error"].lower())


class TestRunwayPage(unittest.TestCase):
    def test_page_contract(self) -> None:
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn("<h1>Runway</h1>", html)
        self.assertIn("/api/runway", html)
        self.assertIn("vendor/d3.min.js", html)
        self.assertIn('id="nav-fcc"', html)
        self.assertIn('id="nav-fleet"', html)
        self.assertIn('id="nav-cash-streams"', html)
        self.assertIn("nav-fleet.js", html)
        self.assertIn("nav-horizon.js", html)
        self.assertNotIn("cdn.jsdelivr", html.lower())
        self.assertNotIn("unpkg.com", html.lower())
        self.assertNotIn("cdnjs", html.lower())
        self.assertNotIn("https://d3js.org", html)
        self.assertIn("estimated", html.lower())
        self.assertIn("load-error", html)
        self.assertIn("stale-banner", html)
        self.assertIn("assumptions", html.lower())
        self.assertIn("typical", html.lower())
        self.assertIn("bill_source", html)
        self.assertIn("data-days=\"30\"", html)
        self.assertIn("data-days=\"180\"", html)
        self.assertIn("threshold", html.lower())
        self.assertIn('value="200"', html)
        self.assertIn("income_one_offs", html)
        self.assertIn("thresholdOverridden", html)
        self.assertIn("min_buffer_source_label", html)
        self.assertIn("sheet_config_drift", html)
        self.assertIn("if (thresholdOverridden && currentThreshold != null)", html)

    def test_index_links_runway(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn("runway.html", html)
        self.assertIn('id="nav-runway"', html)

    def test_vendor_d3_exists(self) -> None:
        d3 = (FCC / "vendor" / "d3.min.js").read_text(encoding="utf-8")
        self.assertIn("d3js.org", d3.splitlines()[0])


class TestRunwayApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_fcc_server()
        cls.port = _free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), cls.mod.FCCHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def _get(self, path: str) -> tuple[int, bytes]:
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_api_and_pretty_url(self) -> None:
        fixture = build_runway(
            days=90,
            today=TODAY,
            transactions=[_tx(amount=10_000, payee="Lyft")],
            accounts=[_acct(aid="onb", name="Checking", typ="checking", balance=100_000)],
            on_budget_ids={"onb"},
            expenses=_fresh_sheet(),
            ynab_stale=False,
            ynab_as_of="2026-09-11T00:00:00+00:00",
        )
        with mock.patch.object(self.mod, "load_runway", return_value=fixture) as mocked:
            code, body = self._get("/api/runway?days=90&threshold=200")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["days"], 90)
        self.assertEqual(data["threshold"], 200.0)
        self.assertEqual(mocked.call_args.kwargs.get("threshold"), "200")
        self.assertIn("daily", data)
        self.assertIn("bill_events", data)
        self.assertIn("assumptions", data)
        self.assertIn("starting_buffer", data)
        self.assertIn("ynab", data)
        self.assertIn("stale", data["ynab"])
        self.assertIn("soft_preserved", data["ynab"])

        page_code, page_body = self._get("/financial-command/runway")
        self.assertEqual(page_code, 200)
        self.assertIn(b"<h1>Runway</h1>", page_body)
        root_code, root_body = self._get("/runway.html")
        self.assertEqual(root_code, 200)
        self.assertIn(b"<h1>Runway</h1>", root_body)

    def test_api_omitted_threshold_does_not_force_hardcoded(self) -> None:
        fixture = build_runway(
            days=90,
            today=TODAY,
            transactions=[_tx(amount=10_000, payee="Lyft")],
            accounts=[_acct(aid="onb", name="Checking", typ="checking", balance=100_000)],
            on_budget_ids={"onb"},
            expenses=_fresh_sheet(),
            policy={POLICY_MIN_BUFFER_KEY: 350},
        )
        self.assertEqual(fixture["threshold"], 350.0)
        self.assertEqual(fixture["assumptions"]["min_buffer_source"], "treasury_policy")
        with mock.patch.object(self.mod, "load_runway", return_value=fixture) as mocked:
            code, body = self._get("/api/runway?days=90")
        self.assertEqual(code, 200)
        self.assertIsNone(mocked.call_args.kwargs.get("threshold"))
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["threshold"], 350.0)

    def test_api_error_is_json_not_empty(self) -> None:
        err = build_runway(days=90, today=TODAY, error="no YNAB token")
        with mock.patch.object(self.mod, "load_runway", return_value=err):
            code, body = self._get("/api/runway")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertFalse(data.get("ok"))
        self.assertTrue(data.get("error"))
        self.assertEqual(data["daily"], [])

    def test_health_lists_runway(self) -> None:
        code, body = self._get("/api/health")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("runway", data.get("features") or [])


if __name__ == "__main__":
    unittest.main()
