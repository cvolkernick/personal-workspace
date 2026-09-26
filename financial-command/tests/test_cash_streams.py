"""Cash Streams: live YNAB Sankey, no CDN, loud errors."""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.cash_streams import (  # noqa: E402
    ALLOWED_DAYS,
    DEFAULT_DAYS,
    MINING_NODE_NAME,
    ROLLING_DISPLAY_DAYS,
    ROLLING_SEED_DAYS,
    TOP_N_INCOME,
    OTHER_INCOME_BAND,
    build_cash_streams,
    build_rolling_cash_series,
    clamp_days,
    income_band_other,
    coinbase_usd_tracking_ids,
    display_payee,
    fetch_ynab_window,
    load_cash_streams,
    load_payee_display_names,
    load_rolling_cash_series,
    mining_from_snapshots,
)

FCC = ROOT / "financial-command"
PAGE = FCC / "cash-streams.html"
INDEX = FCC / "index.html"
TODAY = date(2026, 9, 11)
AS_OF = "2026-09-11T12:00:00+00:00"
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

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
    memo: str | None = None,
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
    if memo is not None:
        row["memo"] = memo
    if subtransactions is not None:
        row["subtransactions"] = subtransactions
    return row


def _ids(payload: dict, layer: str) -> list[str]:
    return [n["name"] for n in payload["nodes"] if n["layer"] == layer]


def _write_snaps(root: Path, *, braiins=None, coinbase=None) -> None:
    snap = root / "treasury" / "snapshots"
    snap.mkdir(parents=True, exist_ok=True)
    if braiins is not None:
        (snap / "braiins_latest.json").write_text(
            json.dumps(braiins), encoding="utf-8"
        )
    if coinbase is not None:
        (snap / "coinbase_latest.json").write_text(
            json.dumps(coinbase), encoding="utf-8"
        )


def _ok_mining(*, usd: float = 1000.0, count: int = 1, btc: float = 0.01) -> dict:
    return {
        "status": "ok",
        "error": None,
        "usd": usd,
        "payout_btc": btc,
        "payout_count": count,
        "price_usd": 100000.0,
        "as_of": AS_OF,
        "stale": False,
    }


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server", FCC / "server.py")
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


class TestCashStreamsBuilder(unittest.TestCase):
    def test_clamp_days(self) -> None:
        self.assertEqual(clamp_days(90), 90)
        self.assertEqual(clamp_days("30"), 30)
        self.assertEqual(clamp_days(7), DEFAULT_DAYS)
        self.assertEqual(clamp_days(120), DEFAULT_DAYS)
        self.assertEqual(clamp_days("nope"), DEFAULT_DAYS)
        self.assertEqual(ALLOWED_DAYS, (30, 60, 90, 180))

    def test_transfers_excluded_uncategorized_kept_totals_reconcile(self) -> None:
        txs = [
            _tx(amount=200_000, payee="Lyft"),
            _tx(amount=150_000, payee="Turo"),
            _tx(amount=80_000, payee="Move", transfer_account_id="other"),
            _tx(amount=-100_000, payee="Landlord", category_id="c-rent"),
            _tx(amount=-30_000, payee="Kroger", category_id="c-groc"),
            _tx(amount=-20_000, payee="Mystery"),
            _tx(amount=50_000, payee="Starting balance"),
            _tx(amount=-10_000, payee="Old", date_s="2025-01-01", category_id="c-rent"),
        ]
        payload = build_cash_streams(
            days=90,
            today=TODAY,
            transactions=txs,
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["window"]["days"], 90)
        self.assertEqual(payload["window"]["end"], "2026-09-11")
        self.assertEqual(payload["window"]["start"], "2026-06-13")
        self.assertEqual(payload["totals"]["inflow"], 350.0)
        self.assertEqual(payload["totals"]["outflow"], 150.0)
        self.assertEqual(payload["totals"]["retained"], 200.0)
        self.assertEqual(set(_ids(payload, "inflow")), {"Lyft", "Turo"})
        self.assertIn("Uncategorized", _ids(payload, "group"))
        self.assertIn("Uncategorized", _ids(payload, "category"))
        self.assertIn("Housing", _ids(payload, "group"))
        self.assertIn("Food", _ids(payload, "group"))
        self.assertIn("Retained", _ids(payload, "retained"))
        sources = sum(
            n["amount"] for n in payload["nodes"] if n["layer"] == "inflow" and n["id"] != "deficit"
        )
        groups = sum(n["amount"] for n in payload["nodes"] if n["layer"] == "group")
        retained = next(n["amount"] for n in payload["nodes"] if n["id"] == "retained")
        revenue = next(n["amount"] for n in payload["nodes"] if n["id"] == "revenue")
        self.assertEqual(sources, revenue)
        self.assertEqual(revenue, groups + retained)

    def test_fcc_reconcile_bookkeeping_excluded(self) -> None:
        groups = GROUPS + [
            {
                "id": "g-recon",
                "name": "Internal",
                "categories": [{"id": "c-recon", "name": "FCC reconcile"}],
            }
        ]
        txs = [
            _tx(amount=300_000, payee="Lyft"),
            _tx(amount=-40_000, payee="Kroger", category_id="c-groc"),
            _tx(
                amount=-791_180,
                payee="FCC reconcile (working USDC)",
                memo="spot=106.06+vault=100",
            ),
            _tx(amount=125_000, payee="Adjustment", memo="FCC reconcile leftover"),
            _tx(amount=-15_000, payee="Bookstore", category_id="c-recon"),
            _tx(
                amount=-20_000,
                payee="FCC reconcile (working USDC)",
                subtransactions=[
                    {"amount": -20_000, "category_id": "c-groc", "payee_name": ""},
                ],
            ),
        ]
        payload = build_cash_streams(
            days=90,
            today=TODAY,
            transactions=txs,
            category_groups=groups,
            on_budget_ids={"onb"},
        )
        self.assertEqual(payload["totals"]["inflow"], 300.0)
        self.assertEqual(payload["totals"]["outflow"], 40.0)
        names = _ids(payload, "inflow") + _ids(payload, "category")
        self.assertNotIn("FCC reconcile (working USDC)", names)
        self.assertNotIn("Adjustment", names)
        self.assertNotIn("FCC reconcile", names)

    def test_trailing_30d_average_is_totals_over_30(self) -> None:
        txs = [
            _tx(amount=300_000, payee="Lyft", date_s="2026-09-01"),
            _tx(amount=-150_000, payee="Kroger", category_id="c-groc", date_s="2026-09-02"),
            _tx(amount=900_000, payee="Starting Balance", date_s="2026-09-03"),
            _tx(
                amount=-500_000,
                payee="FCC reconcile (working USDC)",
                date_s="2026-09-04",
            ),
        ]
        payload = build_cash_streams(
            days=30,
            today=TODAY,
            transactions=txs,
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        self.assertEqual(payload["window"]["days"], 30)
        self.assertEqual(payload["totals"]["inflow"], 300.0)
        self.assertEqual(payload["totals"]["outflow"], 150.0)
        self.assertEqual(payload["totals"]["inflow"] / payload["window"]["days"], 10.0)
        self.assertEqual(payload["totals"]["outflow"] / payload["window"]["days"], 5.0)

    def test_deficit_link_back(self) -> None:
        txs = [
            _tx(amount=100_000, payee="Lyft"),
            _tx(amount=-150_000, payee="Landlord", category_id="c-rent"),
        ]
        payload = build_cash_streams(
            days=90, today=TODAY, transactions=txs, category_groups=GROUPS
        )
        self.assertEqual(payload["totals"]["inflow"], 100.0)
        self.assertEqual(payload["totals"]["outflow"], 150.0)
        self.assertEqual(payload["totals"]["retained"], -50.0)
        self.assertIn("Deficit", _ids(payload, "inflow"))
        self.assertNotIn("Retained", [n["name"] for n in payload["nodes"]])
        self.assertTrue(any(lk["source"] == "deficit" and lk["target"] == "revenue" for lk in payload["links"]))

    def test_other_income_roll_up(self) -> None:
        txs = [
            _tx(amount=(TOP_N_INCOME + 1 - i) * 1000, payee=f"P{i}")
            for i in range(TOP_N_INCOME + 2)
        ]
        payload = build_cash_streams(days=90, today=TODAY, transactions=txs)
        names = _ids(payload, "inflow")
        self.assertIn("Other income", names)
        self.assertEqual(len([n for n in names if n != "Other income"]), TOP_N_INCOME)

    def test_splits_go_to_categories(self) -> None:
        txs = [
            _tx(amount=100_000, payee="Lyft"),
            _tx(
                amount=-100_000,
                payee="Costco",
                subtransactions=[
                    {"amount": -40_000, "category_id": "c-groc", "payee_name": "Costco"},
                    {"amount": -60_000, "category_id": "c-rent", "payee_name": "Costco"},
                ],
            ),
        ]
        payload = build_cash_streams(
            days=90, today=TODAY, transactions=txs, category_groups=GROUPS
        )
        cats = {n["name"]: n["amount"] for n in payload["nodes"] if n["layer"] == "category"}
        self.assertEqual(cats.get("Groceries"), 40.0)
        self.assertEqual(cats.get("Rent"), 60.0)

    def test_error_is_loud(self) -> None:
        payload = build_cash_streams(days=90, today=TODAY, error="no YNAB token")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "no YNAB token")
        self.assertEqual(payload["nodes"], [])
        self.assertIn("stale", payload["ynab"])
        self.assertIn("soft_preserved", payload["ynab"])

    def test_load_uses_injected_fetch_not_live(self) -> None:
        def fake_fetch(since: str) -> dict:
            self.assertEqual(since, "2026-06-13")
            return {
                "ok": True,
                "transactions": [_tx(amount=10_000, payee="Lyft")],
                "category_groups": GROUPS,
                "on_budget_ids": ["onb"],
                "as_of": "2026-09-11T00:00:00+00:00",
            }

        with tempfile.TemporaryDirectory() as tmp:
            payload = load_cash_streams(
                days=90,
                today=TODAY,
                stale=True,
                fetch=fake_fetch,
                root=Path(tmp),
            )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["ynab"]["stale"])
        self.assertEqual(payload["totals"]["inflow"], 10.0)
        self.assertEqual(payload["ynab"]["as_of"], "2026-09-11T00:00:00+00:00")
        self.assertEqual(payload["mining"]["status"], "unknown")
        self.assertIn("Braiins payout feed missing", payload["mining"]["error"])

    def test_live_pull_ignores_stale_balance_snapshots(self) -> None:
        def fake_fetch(since: str) -> dict:
            return {
                "ok": True,
                "transactions": [_tx(amount=10_000, payee="Lyft")],
                "category_groups": GROUPS,
                "on_budget_ids": ["onb"],
                "as_of": "2026-09-12T08:00:00+00:00",
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snap = root / "treasury" / "snapshots"
            snap.mkdir(parents=True, exist_ok=True)
            old = {
                "source": "ynab",
                "as_of": "2026-09-08T19:47:13+00:00",
            }
            for name in (
                "x_money_latest.json",
                "one_card_latest.json",
                "rh_checking_latest.json",
            ):
                (snap / name).write_text(json.dumps(old), encoding="utf-8")
            payload = load_cash_streams(
                days=90,
                today=TODAY,
                fetch=fake_fetch,
                root=root,
            )
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["ynab"]["stale"])
        self.assertFalse(payload["ynab"]["soft_preserved"])
        self.assertEqual(payload["ynab"]["as_of"], "2026-09-12T08:00:00+00:00")

    def test_live_fail_is_stale_without_snapshot_soft_preserve(self) -> None:
        def fake_fetch(since: str) -> dict:
            return {"ok": False, "error": "YNAB HTTP 401"}

        with tempfile.TemporaryDirectory() as tmp:
            payload = load_cash_streams(
                days=90,
                today=TODAY,
                fetch=fake_fetch,
                root=Path(tmp),
            )
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["ynab"]["stale"])
        self.assertFalse(payload["ynab"]["soft_preserved"])
        self.assertIn("YNAB HTTP 401", payload["error"])

    def test_coinbase_inflow_excluded_subscription_outflow_kept(self) -> None:
        txs = [
            _tx(amount=10_000, payee="Lyft"),
            _tx(amount=100_000, payee="Coinbase"),
            _tx(amount=50_000, payee="COINBASE INC."),
            _tx(amount=-29_990, payee="Coinbase", category_id="c-groc"),
        ]
        payload = build_cash_streams(
            days=90,
            today=TODAY,
            transactions=txs,
            category_groups=GROUPS,
            mining=_ok_mining(usd=1000.0),
        )
        self.assertEqual(payload["totals"]["inflow"], 1010.0)
        self.assertEqual(payload["totals"]["outflow"], 29.99)
        self.assertEqual(set(_ids(payload, "inflow")), {MINING_NODE_NAME, "Lyft"})
        self.assertNotIn("Coinbase", _ids(payload, "inflow"))
        self.assertNotIn("COINBASE INC.", _ids(payload, "inflow"))
        self.assertIn("Groceries", _ids(payload, "category"))

    def test_seed_maps_grubhub_bank_descriptor(self) -> None:
        names = load_payee_display_names(ROOT)
        self.assertEqual(names.get("HW*GrubHub Holdings Inc."), "GrubHub")
        self.assertEqual(
            display_payee("HW*GrubHub Holdings Inc.", names), "GrubHub"
        )
        self.assertEqual(display_payee("Lyft", names), "Lyft")

    def test_payee_map_then_group_merges_raw_and_clean(self) -> None:
        mapping = {"HW*GrubHub Holdings Inc.": "GrubHub"}
        txs = [
            _tx(amount=40_000, payee="HW*GrubHub Holdings Inc."),
            _tx(amount=10_000, payee="GrubHub"),
            _tx(amount=20_000, payee="Lyft"),
        ]
        payload = build_cash_streams(
            days=90,
            today=TODAY,
            transactions=txs,
            payee_display_names=mapping,
        )
        self.assertEqual(set(_ids(payload, "inflow")), {"GrubHub", "Lyft"})
        self.assertNotIn("HW*GrubHub Holdings Inc.", _ids(payload, "inflow"))
        amounts = {n["name"]: n["amount"] for n in payload["nodes"] if n["layer"] == "inflow"}
        self.assertEqual(amounts["GrubHub"], 50.0)
        self.assertEqual(payload["totals"]["inflow"], 70.0)

    def test_unmapped_payee_stays_raw(self) -> None:
        payload = build_cash_streams(
            days=90,
            today=TODAY,
            transactions=[_tx(amount=10_000, payee="HW*GrubHub Holdings Inc.")],
            payee_display_names={},
        )
        self.assertEqual(_ids(payload, "inflow"), ["HW*GrubHub Holdings Inc."])

    def test_missing_or_bad_payee_map_file_falls_back(self) -> None:
        def fake_fetch(since: str) -> dict:
            return {
                "ok": True,
                "transactions": [
                    _tx(amount=10_000, payee="HW*GrubHub Holdings Inc.")
                ],
                "category_groups": GROUPS,
                "on_budget_ids": ["onb"],
                "as_of": AS_OF,
            }

        with tempfile.TemporaryDirectory() as tmp:
            missing = load_cash_streams(
                days=90, today=TODAY, fetch=fake_fetch, root=Path(tmp)
            )
            bad_root = Path(tmp) / "bad"
            (bad_root / "treasury").mkdir(parents=True)
            (bad_root / "treasury" / "payee_display_names.json").write_text(
                "{not json", encoding="utf-8"
            )
            broken = load_cash_streams(
                days=90, today=TODAY, fetch=fake_fetch, root=bad_root
            )
        self.assertTrue(missing["ok"])
        self.assertTrue(broken["ok"])
        self.assertEqual(_ids(missing, "inflow"), ["HW*GrubHub Holdings Inc."])
        self.assertEqual(_ids(broken, "inflow"), ["HW*GrubHub Holdings Inc."])

    def test_mining_node_pinned_outside_top_n(self) -> None:
        txs = [
            _tx(amount=(TOP_N_INCOME + 1 - i) * 1000, payee=f"P{i}")
            for i in range(TOP_N_INCOME + 2)
        ]
        payload = build_cash_streams(
            days=90,
            today=TODAY,
            transactions=txs,
            mining=_ok_mining(usd=12.34),
        )
        names = _ids(payload, "inflow")
        self.assertEqual(names[0], MINING_NODE_NAME)
        self.assertIn("Other income", names)
        self.assertAlmostEqual(
            next(n["amount"] for n in payload["nodes"] if n["id"] == "in-mining"),
            12.34,
        )
        ynab_in = [
            n["name"]
            for n in payload["nodes"]
            if n["layer"] == "inflow" and n["id"] not in {"in-mining", "deficit"}
        ]
        self.assertEqual(len([n for n in ynab_in if n != "Other income"]), TOP_N_INCOME)

    def test_mining_unknown_does_not_add_zero_node(self) -> None:
        txs = [_tx(amount=10_000, payee="Lyft")]
        payload = build_cash_streams(
            days=90,
            today=TODAY,
            transactions=txs,
            mining={
                "status": "unknown",
                "error": "Braiins payout feed stale",
                "usd": None,
                "payout_count": 0,
                "stale": True,
            },
        )
        self.assertEqual(payload["totals"]["inflow"], 10.0)
        self.assertNotIn(MINING_NODE_NAME, _ids(payload, "inflow"))
        self.assertEqual(payload["mining"]["status"], "unknown")
        self.assertIsNone(payload["mining"]["usd"])
        self.assertIn("stale", payload["mining"]["error"])

    def test_payout_then_withdrawal_counts_income_once(self) -> None:
        txs = [
            _tx(amount=10_000, payee="Lyft"),
            _tx(amount=773_115, payee="Coinbase"),
        ]
        payload = build_cash_streams(
            days=90,
            today=TODAY,
            transactions=txs,
            mining=_ok_mining(usd=773.12, btc=0.01),
        )
        self.assertEqual(payload["totals"]["inflow"], 783.12)
        self.assertEqual(set(_ids(payload, "inflow")), {MINING_NODE_NAME, "Lyft"})

    def test_mining_from_snapshots_sums_window_at_stamped_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_snaps(
                root,
                braiins={
                    "ok": True,
                    "as_of": AS_OF,
                    "payouts": [
                        {
                            "status": "confirmed",
                            "amount_btc": 0.01,
                            "at": "2026-09-01T00:00:00+00:00",
                            "tx_id": "in-window",
                            "usd_price_at_payout": 100000.0,
                        },
                        {
                            "status": "confirmed",
                            "amount_btc": 0.02,
                            "at": "2025-01-01T00:00:00+00:00",
                            "tx_id": "out-of-window",
                            "usd_price_at_payout": 50000.0,
                        },
                        {
                            "status": "queued",
                            "amount_btc": 0.01,
                            "at": "2026-09-02T00:00:00+00:00",
                            "tx_id": "not-confirmed",
                            "usd_price_at_payout": 100000.0,
                        },
                    ],
                },
                coinbase={
                    "as_of": AS_OF,
                    "btc_usd_price": 99999.0,
                    "source": "live",
                },
            )
            start, end, _days = (date(2026, 6, 13), date(2026, 9, 11), 90)
            got = mining_from_snapshots(
                start=start, end=end, root=root, now=NOW
            )
        self.assertEqual(got["status"], "ok")
        self.assertEqual(got["usd"], 1000.0)
        self.assertEqual(got["payout_count"], 1)
        self.assertEqual(got["payouts"][0]["tx_id"], "in-window")

    def test_mining_unknown_when_payouts_key_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_snaps(
                root,
                braiins={
                    "ok": True,
                    "as_of": AS_OF,
                    "last_payout_btc": 0.005,
                },
                coinbase={"as_of": AS_OF, "btc_usd_price": 100000.0},
            )
            got = mining_from_snapshots(
                start=date(2026, 6, 13),
                end=date(2026, 9, 11),
                root=root,
                now=NOW,
            )
        self.assertEqual(got["status"], "unknown")
        self.assertIsNone(got["usd"])
        self.assertIn("payout history missing", got["error"])
        self.assertIn("braiins-refresh.timer", got["error"])
        self.assertNotIn("python3 treasury/", got["error"])
        self.assertNotIn("systemctl", got["error"])
        self.assertNotIn("launchctl", got["error"])
        self.assertNotIn("run braiins_sync", got["error"])

    def test_mining_unknown_when_price_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_snaps(
                root,
                braiins={
                    "ok": True,
                    "as_of": AS_OF,
                    "payouts": [],
                },
                coinbase={
                    "as_of": "2026-09-10T00:00:00+00:00",
                    "btc_usd_price": 100000.0,
                },
            )
            got = mining_from_snapshots(
                start=date(2026, 6, 13),
                end=date(2026, 9, 11),
                root=root,
                now=NOW,
            )
        self.assertEqual(got["status"], "unknown")
        self.assertIn("Coinbase price feed stale", got["error"])
        self.assertIn("coinbase-price-refresh.timer", got["error"])
        self.assertNotIn("systemctl", got["error"])
        self.assertNotIn("python3 treasury/", got["error"])

    def test_load_adds_mining_from_snapshots(self) -> None:
        def fake_fetch(since: str) -> dict:
            return {
                "ok": True,
                "transactions": [_tx(amount=10_000, payee="Lyft")],
                "category_groups": GROUPS,
                "on_budget_ids": ["onb"],
                "as_of": AS_OF,
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_snaps(
                root,
                braiins={
                    "ok": True,
                    "as_of": AS_OF,
                    "payouts": [
                        {
                            "status": "confirmed",
                            "amount_btc": 0.005,
                            "at": "2026-09-02T02:40:25+00:00",
                            "tx_id": "e2a9",
                            "usd_price_at_payout": 80000.0,
                        }
                    ],
                },
                coinbase={"as_of": AS_OF, "btc_usd_price": 80000.0},
            )
            payload = load_cash_streams(
                days=90,
                today=TODAY,
                stale=False,
                fetch=fake_fetch,
                root=root,
                now=NOW,
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mining"]["status"], "ok")
        self.assertEqual(payload["totals"]["inflow"], 410.0)
        self.assertIn(MINING_NODE_NAME, _ids(payload, "inflow"))


class TestCoinbaseUsdTrackingOutflows(unittest.TestCase):
    """#932: Coinbase USD tracking sends count. Main → Coinbase transfers do not."""

    def _txs(self) -> list:
        return [
            _tx(amount=100_000, payee="Lyft", account_id="onb", date_s="2026-09-01"),
            _tx(
                amount=-30_000,
                payee="Kroger",
                account_id="onb",
                category_id="c-groc",
                date_s="2026-09-11",
            ),
            _tx(amount=-25_000, payee="Nicole", account_id="cb-usd", date_s="2026-09-01"),
            _tx(amount=-900_000, payee="Thaís", account_id="cb-usd", date_s="2026-09-10"),
            _tx(
                amount=-200_000,
                payee="Transfer : Coinbase USD (tracking)",
                account_id="onb",
                transfer_account_id="cb-usd",
                date_s="2026-09-11",
            ),
            _tx(
                amount=200_000,
                payee="Transfer : Main – 2201",
                account_id="cb-usd",
                transfer_account_id="onb",
                date_s="2026-09-11",
            ),
            _tx(
                amount=-100_000,
                payee="Transfer : Coinbase One Card – 5361",
                account_id="cb-usd",
                transfer_account_id="card",
                date_s="2026-08-30",
            ),
            _tx(amount=80_000, payee="Reward", account_id="cb-usd", date_s="2026-09-05"),
            _tx(amount=1_000, payee="Starting Balance", account_id="cb-usd", date_s="2026-09-09"),
            _tx(
                amount=-50_000,
                payee="FCC reconcile (working USDC)",
                account_id="cb-usd",
                date_s="2026-09-09",
            ),
            _tx(amount=-40_000, payee="Other track", account_id="other-track", date_s="2026-09-11"),
        ]

    def test_sankey_includes_sends_and_drops_transfers(self) -> None:
        kwargs = dict(
            days=30,
            today=TODAY,
            transactions=self._txs(),
            category_groups=GROUPS,
            on_budget_ids={"onb"},
            tracking_outflow_ids={"cb-usd"},
        )
        payload = build_cash_streams(**kwargs)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["totals"]["inflow"], 100.0)
        self.assertEqual(payload["totals"]["outflow"], 955.0)
        self.assertEqual(payload["window"]["start"], "2026-08-12")
        uncategorized = next(n for n in payload["nodes"] if n["name"] == "Uncategorized")
        self.assertEqual(uncategorized["amount"], 925.0)
        self.assertIn("Groceries", _ids(payload, "category"))
        names = {n["name"] for n in payload["nodes"]}
        self.assertNotIn("Transfer : Coinbase USD (tracking)", names)
        self.assertNotIn("Transfer : Main – 2201", names)
        self.assertNotIn("Reward", names)

        dropped = build_cash_streams(
            days=30,
            today=TODAY,
            transactions=self._txs(),
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        self.assertEqual(dropped["totals"]["outflow"], 30.0)
        self.assertEqual(dropped["totals"]["inflow"], 100.0)

    def test_rolling_chart_uses_the_same_sends(self) -> None:
        payload = build_rolling_cash_series(
            today=TODAY,
            transactions=self._txs(),
            category_groups=GROUPS,
            on_budget_ids={"onb"},
            tracking_outflow_ids={"cb-usd"},
        )
        sep10 = next(p for p in payload["points"] if p["date"] == "2026-09-10")
        last = payload["points"][-1]
        self.assertEqual(sep10["outflow"], 30.83)
        self.assertEqual(last["outflow"], 31.83)
        self.assertEqual(last["inflow"], 3.33)
        without = build_rolling_cash_series(
            today=TODAY,
            transactions=self._txs(),
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        self.assertEqual(without["points"][-1]["outflow"], 1.0)

    def test_loader_threads_tracking_ids(self) -> None:
        def fake_fetch(since: str) -> dict:
            return {
                "ok": True,
                "transactions": self._txs(),
                "category_groups": GROUPS,
                "on_budget_ids": ["onb"],
                "tracking_outflow_ids": ["cb-usd"],
                "as_of": AS_OF,
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_snaps(
                root,
                braiins={"ok": True, "as_of": AS_OF, "payouts": []},
                coinbase={"as_of": AS_OF, "btc_usd_price": 100000.0},
            )
            sankey = load_cash_streams(
                days=30,
                today=TODAY,
                stale=False,
                fetch=fake_fetch,
                root=root,
                now=NOW,
            )
        self.assertEqual(sankey["totals"]["outflow"], 955.0)
        series = load_rolling_cash_series(today=TODAY, fetch=fake_fetch, stale=False)
        self.assertEqual(series["points"][-1]["outflow"], 31.83)

    def test_account_selector_is_tracking_coinbase_usd_only(self) -> None:
        accounts = [
            {
                "id": "901c6c2e-1111",
                "name": "Coinbase USD (tracking)",
                "on_budget": False,
                "closed": False,
                "deleted": False,
            },
            {
                "id": "renamed-prefix",
                "name": "Renamed wallet",
                "on_budget": False,
            },
            {
                "id": "901c6c2e-closed",
                "name": "Coinbase USD (tracking)",
                "on_budget": False,
                "closed": True,
            },
            {
                "id": "name-only",
                "name": "Coinbase USD",
                "on_budget": False,
            },
            {
                "id": "card",
                "name": "Coinbase One Card – 5361",
                "on_budget": True,
            },
            {
                "id": "901c6c2e-onb",
                "name": "Coinbase USD (tracking)",
                "on_budget": True,
            },
            {
                "id": "gone",
                "name": "Coinbase USD (tracking)",
                "on_budget": False,
                "deleted": True,
            },
            {
                "id": "usdc",
                "name": "Coinbase USDC",
                "on_budget": False,
            },
        ]
        # The renamed row does not carry the id prefix, so it stays out.
        self.assertEqual(
            coinbase_usd_tracking_ids(accounts),
            ["901c6c2e-1111", "901c6c2e-closed", "name-only"],
        )
        # Prefix match still wins when the display name no longer says Coinbase USD.
        prefixed = dict(accounts[1])
        prefixed["id"] = "901c6c2e-renamed"
        self.assertEqual(coinbase_usd_tracking_ids([prefixed]), ["901c6c2e-renamed"])

    def test_fetch_marks_tracking_ids_separate_from_on_budget(self) -> None:
        accounts = [
            {
                "id": "901c6c2e-1111",
                "name": "Coinbase USD (tracking)",
                "on_budget": False,
                "deleted": False,
                "closed": False,
            },
            {
                "id": "onb",
                "name": "Main – 2201",
                "on_budget": True,
                "deleted": False,
                "closed": False,
            },
            {
                "id": "card",
                "name": "Coinbase One Card – 5361",
                "on_budget": True,
                "deleted": False,
                "closed": False,
            },
        ]

        def fake_get(path, token, params=None):
            if path == "/budgets":
                return {"data": {"budgets": [{"id": "b1", "name": "Chris's Plan"}]}}
            if path.endswith("/accounts"):
                return {"data": {"accounts": accounts}}
            if path.endswith("/categories"):
                return {"data": {"category_groups": []}}
            if path.endswith("/transactions"):
                return {"data": {"transactions": []}}
            raise AssertionError(path)

        with mock.patch(
            "treasury.ynab_sync.load_ynab_token", return_value=("tok", "file")
        ), mock.patch(
            "treasury.ynab_sync.pick_budget",
            return_value={"id": "b1", "name": "Chris's Plan"},
        ), mock.patch("treasury.ynab_sync.ynab_get", side_effect=fake_get):
            got = fetch_ynab_window("2026-08-12")
        self.assertTrue(got["ok"])
        self.assertEqual(got["on_budget_ids"], ["onb", "card"])
        self.assertEqual(got["tracking_outflow_ids"], ["901c6c2e-1111"])


class TestRollingCashSeries(unittest.TestCase):
    """90 displayed days, trailing 30-day mean, chart-only reconcile payees.

    TODAY is 2026-09-11.
    Seed start = today - 120d = 2026-05-14 (inclusive).
    First displayed day = today - 89d = 2026-06-14.
    That day's window is the 30 days ending 2026-06-14, so 2026-05-16 through
    2026-06-14. 2026-05-15 is inside the seed and outside every displayed window.
    """

    def _assert_band_stack(self, point: dict) -> None:
        """Named bands plus other meet the inflow line, and other never goes negative."""
        lyft = point["lyft"]
        grubhub = point["grubhub"]
        turo = point["turo"]
        other = point["other"]
        named = round(lyft + grubhub + turo, 2)
        self.assertGreaterEqual(other, 0.0)
        self.assertEqual(other, income_band_other(point["inflow"], lyft, grubhub, turo))
        if named <= point["inflow"]:
            self.assertEqual(round(named + other, 2), point["inflow"])
        else:
            self.assertEqual(other, 0.0)

    def test_calendar_anchors(self) -> None:
        self.assertEqual(ROLLING_DISPLAY_DAYS, 90)
        self.assertEqual(ROLLING_SEED_DAYS, 120)
        self.assertEqual((TODAY - timedelta(days=89)).isoformat(), "2026-06-14")
        self.assertEqual((TODAY - timedelta(days=120)).isoformat(), "2026-05-14")
        self.assertEqual((date(2026, 6, 14) - timedelta(days=29)).isoformat(), "2026-05-16")

    def test_empty_window_is_ninety_zero_days(self) -> None:
        payload = build_rolling_cash_series(
            today=TODAY,
            transactions=[],
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["window"]["start"], "2026-06-14")
        self.assertEqual(payload["window"]["end"], "2026-09-11")
        self.assertEqual(payload["window"]["days"], 90)
        self.assertEqual(payload["seed"]["start"], "2026-05-14")
        self.assertEqual(payload["seed"]["days"], 120)
        self.assertEqual(payload["rolling_days"], 30)
        self.assertEqual(payload["unit"], "usd_per_day")
        self.assertFalse(payload["includes_mining"])
        self.assertEqual(len(payload["points"]), 90)
        dates = [date.fromisoformat(p["date"]) for p in payload["points"]]
        self.assertEqual(dates[0].isoformat(), "2026-06-14")
        self.assertEqual(dates[-1].isoformat(), "2026-09-11")
        self.assertEqual(dates, [dates[0] + timedelta(days=i) for i in range(90)])
        self.assertTrue(all(p["inflow"] == 0.0 and p["outflow"] == 0.0 for p in payload["points"]))
        self.assertTrue(all(p["lyft"] == 0.0 and p["grubhub"] == 0.0 and p["turo"] == 0.0 for p in payload["points"]))
        self.assertTrue(all(p["other"] == 0.0 for p in payload["points"]))
        for point in payload["points"]:
            self._assert_band_stack(point)
        self.assertEqual(payload["source_warnings"], [])
        self.assertEqual([s["id"] for s in payload["sources"]], ["lyft", "grubhub", "turo"])
        self.assertEqual(payload["other_band"], {"id": "other", "label": "Other", "color": "#6b7c8d"})

    def test_hand_bucket_trailing_mean(self) -> None:
        # $3000 on the first included seed day → only 2026-06-14 moves, by 3000/30.
        # $30 on 2026-09-10 and $300 on 2026-09-11:
        #   09-10 mean = 30/30 = 1.00
        #   09-11 mean = (30+300)/30 = 11.00
        # $90 outflow on 2026-09-11 → 90/30 = 3.00
        txs = [
            _tx(amount=3_000_000, payee="Lyft", date_s="2026-05-16"),
            _tx(amount=9_000_000, payee="Lyft", date_s="2026-05-15"),
            _tx(amount=30_000, payee="Turo", date_s="2026-09-10"),
            _tx(amount=300_000, payee="Lyft", date_s="2026-09-11"),
            _tx(amount=-90_000, payee="Kroger", category_id="c-groc", date_s="2026-09-11"),
        ]
        payload = build_rolling_cash_series(
            today=TODAY,
            transactions=txs,
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        first = payload["points"][0]
        sep10 = next(p for p in payload["points"] if p["date"] == "2026-09-10")
        last = payload["points"][-1]
        self.assertEqual(first["date"], "2026-06-14")
        self.assertEqual(first["inflow"], 100.0)
        self.assertEqual(first["outflow"], 0.0)
        self.assertEqual(sep10["inflow"], 1.0)
        self.assertEqual(sep10["outflow"], 0.0)
        self.assertEqual(last["inflow"], 11.0)
        self.assertEqual(last["outflow"], 3.0)
        self.assertEqual(first["lyft"], 100.0)
        self.assertEqual(first["grubhub"], 0.0)
        self.assertEqual(first["turo"], 0.0)
        self.assertEqual(sep10["turo"], 1.0)
        self.assertEqual(sep10["lyft"], 0.0)
        self.assertEqual(last["lyft"], 10.0)
        self.assertEqual(last["turo"], 1.0)
        self.assertEqual(last["grubhub"], 0.0)
        self.assertEqual(last["other"], 0.0)
        self.assertEqual(first["other"], 0.0)
        self.assertEqual(sep10["other"], 0.0)
        for point in payload["points"]:
            self._assert_band_stack(point)
        self.assertEqual(payload["source_warnings"], ["grubhub"])
        # The 2026-05-15 inflow is outside every displayed 30-day window.
        without = build_rolling_cash_series(
            today=TODAY,
            transactions=[tx for tx in txs if tx["date"] != "2026-05-15"],
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        self.assertEqual(without["points"], payload["points"])

    def test_other_band_floors_at_zero(self) -> None:
        self.assertEqual(income_band_other(16.5, 10.0, 2.0, 1.0), 3.5)
        self.assertEqual(income_band_other(10.0, 6.0, 3.0, 2.0), 0.0)
        self.assertEqual(income_band_other(0.0, 0.0, 0.0, 0.0), 0.0)
        self.assertEqual(income_band_other(1.0, 0.34, 0.33, 0.34), 0.0)
        self.assertEqual(income_band_other(1.0, 0.33, 0.33, 0.33), 0.01)
        self.assertEqual(OTHER_INCOME_BAND["id"], "other")
        self.assertEqual(OTHER_INCOME_BAND["color"], "#6b7c8d")

    def test_source_lines_use_payee_or_category_after_shared_exclusions(self) -> None:
        txs = [
            _tx(amount=300_000, payee="Lyft Inc", date_s="2026-09-11"),
            _tx(amount=60_000, payee="HW*GrubHub Holdings Inc.", date_s="2026-09-11"),
            _tx(amount=30_000, payee="Stripe", category_name="Turo", date_s="2026-09-10"),
            _tx(amount=90_000, payee="Employer", date_s="2026-09-11"),
            _tx(amount=-30_000, payee="Lyft", date_s="2026-09-11"),
            _tx(amount=300_000, payee="Lyft", transfer_account_id="other", date_s="2026-09-11"),
            _tx(amount=300_000, payee="Starting Balance", date_s="2026-09-11"),
            _tx(amount=40_000, payee="Coinbase Lyft", date_s="2026-09-11"),
            _tx(amount=20_000, payee="Lyft reconcile", date_s="2026-09-11"),
            _tx(amount=15_000, payee="Grubby", date_s="2026-09-11"),
        ]
        payload = build_rolling_cash_series(
            today=TODAY,
            transactions=txs,
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        sep10 = next(p for p in payload["points"] if p["date"] == "2026-09-10")
        last = payload["points"][-1]
        # 300 + 60 + 90 + 15 = 465 countable inflow on 09-11; 30 Turo on 09-10.
        self.assertEqual(last["inflow"], 16.5)
        self.assertEqual(last["lyft"], 10.0)
        self.assertEqual(last["grubhub"], 2.0)
        self.assertEqual(last["turo"], 1.0)
        self.assertEqual(sep10["turo"], 1.0)
        self.assertEqual(sep10["inflow"], 1.0)
        self.assertEqual(sep10["other"], 0.0)
        # Employer $90 + Grubby $15 = $105 on 09-11 → 3.50/day in other.
        self.assertEqual(last["other"], 3.5)
        self.assertEqual(payload["source_warnings"], [])
        self.assertNotIn("other", payload["source_warnings"])
        self.assertEqual(last["lyft"] + last["grubhub"] + last["turo"] + last["other"], 16.5)
        for point in payload["points"]:
            self._assert_band_stack(point)

    def test_chart_reconcile_payee_does_not_widen_shared_filter(self) -> None:
        txs = [
            _tx(amount=-30_000, payee="Bank reconcile", date_s="2026-09-11", category_id="c-groc"),
            _tx(amount=-15_000, payee="RECONCILE adjustment", date_s="2026-09-11", category_id="c-rent"),
            _tx(
                amount=-20_000,
                payee="Bank reconcile",
                date_s="2026-09-11",
                subtransactions=[
                    {"amount": -20_000, "category_id": "c-groc", "payee_name": "Kroger"},
                ],
            ),
        ]
        series = build_rolling_cash_series(
            today=TODAY,
            transactions=txs,
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        sankey = build_cash_streams(
            days=90,
            today=TODAY,
            transactions=txs,
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        self.assertEqual(series["points"][-1]["outflow"], 0.0)
        self.assertEqual(series["points"][-1]["inflow"], 0.0)
        self.assertEqual(sankey["totals"]["outflow"], 65.0)
        self.assertIn("Groceries", _ids(sankey, "category"))
        self.assertIn("Rent", _ids(sankey, "category"))

    def test_memo_reconcile_without_fcc_stays_in_chart(self) -> None:
        txs = [
            _tx(
                amount=-30_000,
                payee="Kroger",
                memo="please reconcile the receipt",
                category_id="c-groc",
                date_s="2026-09-11",
            )
        ]
        series = build_rolling_cash_series(
            today=TODAY,
            transactions=txs,
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        sankey = build_cash_streams(
            days=90,
            today=TODAY,
            transactions=txs,
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        self.assertEqual(series["points"][-1]["outflow"], 1.0)
        self.assertEqual(sankey["totals"]["outflow"], 30.0)

    def test_shared_exclusions_still_apply_to_chart(self) -> None:
        txs = [
            _tx(amount=300_000, payee="Lyft", date_s="2026-09-11"),
            _tx(amount=900_000, payee="Starting Balance", date_s="2026-09-11"),
            _tx(amount=50_000, payee="Move", transfer_account_id="other", date_s="2026-09-11"),
            _tx(amount=40_000, payee="Coinbase withdrawal", date_s="2026-09-11"),
            _tx(
                amount=-500_000,
                payee="FCC reconcile (working USDC)",
                date_s="2026-09-11",
            ),
            _tx(amount=-90_000, payee="Kroger", category_id="c-groc", date_s="2026-09-11"),
        ]
        series = build_rolling_cash_series(
            today=TODAY,
            transactions=txs,
            category_groups=GROUPS,
            on_budget_ids={"onb"},
        )
        last = series["points"][-1]
        self.assertEqual(last["inflow"], 10.0)
        self.assertEqual(last["outflow"], 3.0)

    def test_load_fetches_seed_not_sankey_window(self) -> None:
        seen: dict[str, str] = {}

        def fake_fetch(since: str) -> dict:
            seen["since"] = since
            return {
                "ok": True,
                "transactions": [_tx(amount=30_000, payee="Lyft", date_s="2026-09-11")],
                "category_groups": GROUPS,
                "on_budget_ids": ["onb"],
                "as_of": AS_OF,
            }

        payload = load_rolling_cash_series(today=TODAY, fetch=fake_fetch, stale=False)
        self.assertEqual(seen["since"], "2026-05-14")
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["ynab"]["stale"])
        self.assertEqual(payload["points"][-1]["inflow"], 1.0)

        def fail_fetch(since: str) -> dict:
            return {"ok": False, "error": "no YNAB token"}

        err = load_rolling_cash_series(today=TODAY, fetch=fail_fetch)
        self.assertFalse(err["ok"])
        self.assertEqual(err["error"], "no YNAB token")
        self.assertEqual(err["points"], [])
        self.assertTrue(err["ynab"]["stale"])

    def test_sankey_days_120_still_clamps(self) -> None:
        seen: dict[str, str] = {}

        def fake_fetch(since: str) -> dict:
            seen["since"] = since
            return {
                "ok": True,
                "transactions": [],
                "category_groups": GROUPS,
                "on_budget_ids": ["onb"],
                "as_of": AS_OF,
            }

        with tempfile.TemporaryDirectory() as tmp:
            payload = load_cash_streams(
                days=120,
                today=TODAY,
                fetch=fake_fetch,
                root=Path(tmp),
                now=NOW,
            )
        self.assertEqual(payload["window"]["days"], 90)
        self.assertEqual(payload["window"]["start"], "2026-06-13")
        self.assertEqual(seen["since"], "2026-06-13")


class TestCashStreamsPage(unittest.TestCase):
    def test_page_contract(self) -> None:
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn("<h1>Cash Streams</h1>", html)
        self.assertIn("/api/cash-streams", html)
        self.assertIn('src="/financial-command/vendor/d3.min.js"', html)
        self.assertIn('src="/financial-command/vendor/d3-sankey.min.js"', html)
        self.assertNotIn('src="vendor/d3.min.js"', html)
        self.assertNotIn('src="vendor/d3-sankey.min.js"', html)
        self.assertIn('id="nav-fcc"', html)
        self.assertIn('id="nav-fleet"', html)
        self.assertIn("nav-fleet.js", html)
        self.assertIn("nav-horizon.js", html)
        self.assertNotIn("cdn.jsdelivr", html.lower())
        self.assertNotIn("unpkg.com", html.lower())
        self.assertNotIn("cdnjs", html.lower())
        self.assertNotIn("https://d3js.org", html)
        self.assertIn("estimated", html.lower())
        self.assertIn("load-error", html)
        self.assertIn("stale-banner", html)
        self.assertIn("mining-banner", html)
        self.assertIn("Bitcoin mining", html)
        self.assertIn('id="nav-capital-flows"', html)
        self.assertIn("Does not sync", html)
        self.assertIn("Re-fetch live YNAB transactions", html)
        self.assertNotIn("YNAB cash feeds older than 6h", html)
        self.assertIn('id="rolling"', html)
        self.assertIn("/api/cash-streams-rolling", html)
        self.assertIn(".line-in", html)
        self.assertIn(".line-out", html)
        self.assertIn('"line-in"', html)
        self.assertIn('"line-out"', html)
        self.assertNotIn(".line-lyft", html)
        self.assertNotIn(".line-grubhub", html)
        self.assertNotIn(".line-turo", html)
        self.assertNotIn('"line-lyft"', html)
        self.assertNotIn('"line-grubhub"', html)
        self.assertNotIn('"line-turo"', html)
        self.assertIn(".band-lyft", html)
        self.assertIn(".band-grubhub", html)
        self.assertIn(".band-turo", html)
        self.assertIn(".band-other", html)
        self.assertIn("fill-opacity: 0.6", html)
        self.assertIn("#ff69b4", html)
        self.assertIn("#ff8c1a", html)
        self.assertIn("#b7c0c8", html)
        self.assertIn(OTHER_INCOME_BAND["color"], html)
        self.assertIn('id="rolling-legend"', html)
        self.assertIn('id="rolling-source-warn"', html)
        self.assertIn("> Lyft</span>", html)
        self.assertIn("> Grubhub</span>", html)
        self.assertIn("> Turo</span>", html)
        self.assertIn("> Other</span>", html)
        self.assertIn('key: "lyft"', html)
        band_lyft = html.index('key: "lyft"')
        band_grubhub = html.index('key: "grubhub"')
        band_turo = html.index('key: "turo"')
        band_other = html.index('key: "other"')
        self.assertLess(band_lyft, band_grubhub)
        self.assertLess(band_grubhub, band_turo)
        self.assertLess(band_turo, band_other)
        self.assertIn("of inflow", html)
        self.assertIn("floored at 0", html)
        self.assertIn("bandOther", html)
        self.assertIn("pctOfInflow", html)
        self.assertIn(".curve(d3.curveLinear)", html)
        self.assertLess(
            html.index("curve: d3.curveLinear"),
            html.index("curve: d3.curveMonotoneX"),
        )
        self.assertIn("drop that band to zero", html)
        self.assertIn("external inflows only", html)
        self.assertIn("@media (max-width: 720px)", html)
        self.assertIn('$/day', html)
        self.assertIn("Payees containing \"reconcile\"", html)
        self.assertIn("No CDN", html)
        self.assertLess(html.index("id=\"sankey\""), html.index("id=\"rolling\""))

    def test_index_links_cash_streams(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn("cash-streams.html", html)
        self.assertIn('id="nav-cash-streams"', html)
        self.assertNotIn("run braiins_sync", html)
        self.assertNotIn("python3 treasury/braiins_sync.py", html)
        self.assertNotIn("launchctl", html)
        self.assertNotIn("systemctl", html)
        self.assertIn("Braiins data missing.", html)

    def test_vendor_files_exist(self) -> None:
        d3 = (FCC / "vendor" / "d3.min.js").read_text(encoding="utf-8")
        sankey = (FCC / "vendor" / "d3-sankey.min.js").read_text(encoding="utf-8")
        self.assertIn("d3js.org", d3.splitlines()[0])
        self.assertIn("d3-sankey", sankey.splitlines()[0])
        self.assertIn("n.sankey", sankey)


class TestCashStreamsApi(unittest.TestCase):
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
        fixture = build_cash_streams(
            days=90,
            today=TODAY,
            transactions=[_tx(amount=10_000, payee="Lyft")],
            ynab_stale=False,
            ynab_as_of="2026-09-11T00:00:00+00:00",
        )
        with mock.patch.object(self.mod, "load_cash_streams", return_value=fixture):
            code, body = self._get("/api/cash-streams?days=90")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["window"]["days"], 90)
        self.assertIn("nodes", data)
        self.assertIn("links", data)
        self.assertIn("totals", data)
        self.assertIn("ynab", data)
        self.assertIn("stale", data["ynab"])
        self.assertIn("soft_preserved", data["ynab"])
        self.assertIn("mining", data)
        self.assertIn("status", data["mining"])

        page_code, page_body = self._get("/financial-command/cash-streams")
        self.assertEqual(page_code, 200)
        self.assertIn(b"<h1>Cash Streams</h1>", page_body)
        root_code, root_body = self._get("/cash-streams.html")
        self.assertEqual(root_code, 200)
        self.assertIn(b"<h1>Cash Streams</h1>", root_body)

    def test_api_error_is_json_not_empty(self) -> None:
        err = build_cash_streams(days=90, today=TODAY, error="no YNAB token")
        with mock.patch.object(self.mod, "load_cash_streams", return_value=err):
            code, body = self._get("/api/cash-streams")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertFalse(data.get("ok"))
        self.assertTrue(data.get("error"))

    def test_rolling_api(self) -> None:
        fixture = build_rolling_cash_series(today=TODAY, transactions=[])
        with mock.patch.object(self.mod, "load_rolling_cash_series", return_value=fixture):
            code, body = self._get("/api/cash-streams-rolling")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(len(data["points"]), 90)
        self.assertEqual(data["unit"], "usd_per_day")
        self.assertFalse(data["includes_mining"])
        self.assertEqual(data["points"][0]["other"], 0.0)
        self.assertEqual(data["other_band"]["id"], "other")
        self.assertEqual([row["id"] for row in data["sources"]], ["lyft", "grubhub", "turo"])
        self.assertNotIn("nodes", data)
        self.assertNotIn("totals", data)


if __name__ == "__main__":
    unittest.main()
