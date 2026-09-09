"""Read-only Plaid MCP: last4 pin, no write tools, mocked HTTP. No live secrets."""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.client import HTTPConnection
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.plaid_mcp.http_server import bind_is_public, serve
from treasury.plaid_mcp.plaid_http import PlaidClient, PlaidError
from treasury.plaid_mcp.protocol import (
    ALLOWED_TOOL_NAMES,
    PlaidBankSession,
    handle_rpc,
    shape_balances,
    shape_transactions,
)
from treasury.plaid_mcp.spaces import (
    SPACE_BY_LAST4,
    dollars_to_cents,
    filter_x_money_accounts,
    is_in_scope,
    is_write_tool,
    last4_of_account,
)


def _acct(mask: str, name: str, current: float, available: float, account_id: str) -> Dict[str, Any]:
    return {
        "account_id": account_id,
        "mask": mask,
        "name": name,
        "official_name": name,
        "type": "depository",
        "subtype": "checking",
        "balances": {
            "current": current,
            "available": available,
            "iso_currency_code": "USD",
        },
    }


FOUR = [
    _acct("2201", "Main", 12.34, 10.00, "a-main"),
    _acct("0895", "Auto Fleet", 100.00, 100.00, "a-fleet"),
    _acct("3326", "Collateral", 50.55, 50.55, "a-col"),
    _acct("4867", "Utilities", 1.00, 1.00, "a-util"),
]
NAVY = _acct("8680", "EveryDay Checking", 620.00, 620.00, "a-navy")


def fixture_payload(extra: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    extras = [NAVY] if extra is None else list(extra)
    return {"accounts": list(FOUR) + extras, "item": {"item_id": "item-x-money"}}


class TestSpaces(unittest.TestCase):
    def test_last4_from_mask_and_name(self):
        self.assertEqual(last4_of_account({"mask": "2201"}), "2201")
        self.assertEqual(last4_of_account({"name": "Main – 2201"}), "2201")
        self.assertEqual(last4_of_account({"official_name": "Checking 0895"}), "0895")

    def test_filter_keeps_four_drops_navy(self):
        ordered, missing = filter_x_money_accounts(FOUR + [NAVY])
        self.assertEqual([last4_of_account(a) for a in ordered], ["2201", "0895", "3326", "4867"])
        self.assertEqual(missing, [])
        self.assertFalse(is_in_scope(NAVY))
        self.assertTrue(is_in_scope(FOUR[0]))

    def test_missing_last4_reported(self):
        ordered, missing = filter_x_money_accounts(FOUR[:2])
        self.assertEqual([last4_of_account(a) for a in ordered], ["2201", "0895"])
        self.assertEqual(missing, ["3326", "4867"])

    def test_cents_bankers_rounding(self):
        self.assertEqual(dollars_to_cents("12.34"), 1234)
        self.assertEqual(dollars_to_cents(50.55), 5055)
        self.assertEqual(dollars_to_cents("1.225"), 122)  # HALF_EVEN
        self.assertEqual(dollars_to_cents("1.235"), 124)
        self.assertIsNone(dollars_to_cents(None))

    def test_write_tool_hints(self):
        self.assertTrue(is_write_tool("transfer_funds"))
        self.assertTrue(is_write_tool("create_link_token"))
        self.assertTrue(is_write_tool("payment_initiate"))
        self.assertFalse(is_write_tool("get_balances"))
        self.assertFalse(is_write_tool("list_transactions"))


class TestShape(unittest.TestCase):
    def test_balances_four_spaces_as_of(self):
        out = shape_balances(fixture_payload(), as_of="2026-09-09T05:00:00Z")
        self.assertEqual(out["as_of"], "2026-09-09T05:00:00Z")
        self.assertTrue(out["read_only"])
        self.assertEqual(out["item_id"], "item-x-money")
        last4s = [a["last4"] for a in out["accounts"]]
        self.assertEqual(last4s, ["2201", "0895", "3326", "4867"])
        self.assertEqual([a["space"] for a in out["accounts"]], ["Main", "Auto Fleet", "Collateral", "Utilities"])
        self.assertNotIn("8680", last4s)
        self.assertEqual(out["total_current_cents"], 1234 + 10000 + 5055 + 100)
        self.assertEqual(out["total_current"], 163.89)
        self.assertEqual(out["spaces"], dict(SPACE_BY_LAST4))
        self.assertEqual(out["rounding"], "ROUND_HALF_EVEN")

    def test_transactions_filter_to_in_scope(self):
        payload = {
            "transactions": [
                {
                    "transaction_id": "t1",
                    "account_id": "a-main",
                    "amount": 12.00,
                    "date": "2026-09-08",
                    "name": "Coffee",
                    "pending": False,
                    "iso_currency_code": "USD",
                },
                {
                    "transaction_id": "t-navy",
                    "account_id": "a-navy",
                    "amount": 50.00,
                    "date": "2026-09-08",
                    "name": "Should drop",
                    "pending": False,
                },
            ]
        }
        out = shape_transactions(
            payload,
            fixture_payload(),
            start_date="2026-08-26",
            end_date="2026-09-09",
            as_of="2026-09-09T05:00:00Z",
        )
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["transactions"][0]["last4"], "2201")
        self.assertEqual(out["transactions"][0]["space"], "Main")
        self.assertEqual(out["transactions"][0]["amount_cents"], 1200)
        self.assertEqual(out["transactions"][0]["amount_sign"], "plaid_positive_outflow")


class FakePlaid(PlaidClient):
    def __init__(self, payload: Dict[str, Any]):
        super().__init__(
            client_id="id",
            secret="sec",
            access_token="access-sandbox",
            env="sandbox",
            post_fn=self._fake,
        )
        self.payload = payload
        self.paths: List[str] = []

    def _fake(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.paths.append(path)
        if path == "/transactions/get":
            return {
                "transactions": [
                    {
                        "transaction_id": "t1",
                        "account_id": "a-main",
                        "amount": 5.25,
                        "date": "2026-09-08",
                        "name": "Test",
                        "pending": False,
                        "iso_currency_code": "USD",
                    }
                ]
            }
        return self.payload


class TestClientDeny(unittest.TestCase):
    def test_refuses_write_and_link_paths(self):
        c = PlaidClient(client_id="i", secret="s", access_token="t", env="sandbox", post_fn=lambda p, b: {})
        with self.assertRaises(PlaidError):
            c.post("/transfer/authorize")
        with self.assertRaises(PlaidError):
            c.post("/payment_initiation/payment/create")
        with self.assertRaises(PlaidError):
            c.post("/link/token/create")
        with self.assertRaises(PlaidError):
            c.post("/item/public_token/exchange")
        with self.assertRaises(PlaidError):
            c.post("/accounts/balance/get/../transfer")


class TestRpc(unittest.TestCase):
    def setUp(self):
        self.session = PlaidBankSession(FakePlaid(fixture_payload()))

    def test_initialize_and_tools_list(self):
        init = handle_rpc(self.session, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "plaid-bank")
        listed = handle_rpc(self.session, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in listed["result"]["tools"]]
        self.assertEqual(set(names), set(ALLOWED_TOOL_NAMES))
        joined = " ".join(names)
        self.assertNotIn("transfer", joined)
        self.assertNotIn("payment", joined)
        self.assertNotIn("link", joined)

    def test_call_get_balances(self):
        reply = handle_rpc(
            self.session,
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_balances", "arguments": {}}},
        )
        self.assertFalse(reply["result"]["isError"])
        body = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(len(body["accounts"]), 4)
        self.assertTrue(body["as_of"])
        self.assertEqual(body["accounts"][0]["last4"], "2201")

    def test_call_list_transactions(self):
        reply = handle_rpc(
            self.session,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "list_transactions", "arguments": {"days": 7}},
            },
        )
        self.assertFalse(reply["result"]["isError"])
        body = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(body["count"], 1)

    def test_write_tool_rejected(self):
        reply = handle_rpc(
            self.session,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "transfer_create", "arguments": {}},
            },
        )
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("not exposed", reply["result"]["content"][0]["text"])


class TestHTTP(unittest.TestCase):
    def setUp(self):
        self.session = PlaidBankSession(FakePlaid(fixture_payload()))
        self.httpd = serve("127.0.0.1", 0, self.session, "test-token")
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _req(self, method: str, path: str, body: bytes = b"", token: str | None = "test-token") -> Any:
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, raw

    def test_healthz_no_auth(self):
        status, raw = self._req("GET", "/healthz", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(raw.strip(), b"ok")

    def test_api_requires_token(self):
        status, _ = self._req("GET", "/api/balances", token=None)
        self.assertEqual(status, 401)

    def test_api_balances(self):
        status, raw = self._req("GET", "/api/balances")
        self.assertEqual(status, 200)
        body = json.loads(raw)
        self.assertEqual(len(body["accounts"]), 4)
        self.assertEqual(body["accounts"][1]["space"], "Auto Fleet")

    def test_mcp_tools_list(self):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        status, raw = self._req("POST", "/mcp", body=payload)
        self.assertEqual(status, 200)
        body = json.loads(raw)
        names = [t["name"] for t in body["result"]["tools"]]
        self.assertEqual(set(names), {"get_balances", "list_accounts", "list_transactions"})

    def test_mcp_wrong_token(self):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        status, _ = self._req("POST", "/mcp", body=payload, token="nope")
        self.assertEqual(status, 401)

    def test_public_bind_without_token_refused(self):
        self.assertTrue(bind_is_public("0.0.0.0"))
        with self.assertRaises(SystemExit):
            serve("0.0.0.0", 19999, self.session, "")


if __name__ == "__main__":
    unittest.main()
