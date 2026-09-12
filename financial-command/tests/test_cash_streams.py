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
from datetime import date, datetime, timezone
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
    TOP_N_INCOME,
    build_cash_streams,
    clamp_days,
    display_payee,
    load_cash_streams,
    load_payee_display_names,
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


class TestCashStreamsPage(unittest.TestCase):
    def test_page_contract(self) -> None:
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn("<h1>Cash Streams</h1>", html)
        self.assertIn("/api/cash-streams", html)
        self.assertIn("vendor/d3.min.js", html)
        self.assertIn("vendor/d3-sankey.min.js", html)
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

    def test_index_links_cash_streams(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn("cash-streams.html", html)
        self.assertIn('id="nav-cash-streams"', html)

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


if __name__ == "__main__":
    unittest.main()
