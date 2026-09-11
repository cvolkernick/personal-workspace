"""Cash Streams: live YNAB Sankey, no CDN, loud errors."""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.cash_streams import (  # noqa: E402
    ALLOWED_DAYS,
    DEFAULT_DAYS,
    TOP_N_INCOME,
    build_cash_streams,
    clamp_days,
    load_cash_streams,
)

FCC = ROOT / "financial-command"
PAGE = FCC / "cash-streams.html"
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


def _ids(payload: dict, layer: str) -> list[str]:
    return [n["name"] for n in payload["nodes"] if n["layer"] == layer]


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

        payload = load_cash_streams(
            days=90,
            today=TODAY,
            stale=True,
            fetch=fake_fetch,
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["ynab"]["stale"])
        self.assertEqual(payload["totals"]["inflow"], 10.0)
        self.assertEqual(payload["ynab"]["as_of"], "2026-09-11T00:00:00+00:00")


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
        self.assertIn('id="nav-capital-flows"', html)

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
