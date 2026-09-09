"""MCP JSON-RPC + JSON REST for read-only X Money Plaid."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from treasury.plaid_mcp import SERVER_NAME, __version__
from treasury.plaid_mcp.plaid_http import PlaidClient, PlaidError
from treasury.plaid_mcp.spaces import (
    SPACE_BY_LAST4,
    dollars_to_cents,
    filter_x_money_accounts,
    is_write_tool,
    last4_of_account,
    space_name_for,
)

PROTOCOL_VERSION = "2024-11-05"

ALLOWED_TOOLS = (
    {
        "name": "get_balances",
        "description": (
            "Live balances for Chris's four X Money checkings (last4 2201 Main, "
            "0895 Auto Fleet, 3326 Collateral, 4867 Utilities). Read-only. "
            "Includes as_of UTC and integer cents. Excludes EveryDay-8680."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_accounts",
        "description": "List the four in-scope X Money accounts (no balances refresh).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_transactions",
        "description": (
            "Recent transactions for the four X Money accounts. "
            "Optional days (1-90, default 14)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 90, "default": 14},
            },
            "additionalProperties": False,
        },
    },
)

ALLOWED_TOOL_NAMES = frozenset(t["name"] for t in ALLOWED_TOOLS)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_day(d: date) -> str:
    return d.isoformat()


def _account_row(account: Dict[str, Any], *, as_of: str, live_balance: bool) -> Dict[str, Any]:
    n4 = last4_of_account(account)
    bals = account.get("balances") or {}
    current = bals.get("current")
    available = bals.get("available")
    current_cents = dollars_to_cents(current)
    available_cents = dollars_to_cents(available)
    row: Dict[str, Any] = {
        "space": space_name_for(n4, account),
        "last4": n4,
        "account_id": account.get("account_id"),
        "name": account.get("name"),
        "official_name": account.get("official_name"),
        "type": account.get("type"),
        "subtype": account.get("subtype"),
        "iso_currency_code": bals.get("iso_currency_code") or "USD",
        "as_of": as_of,
    }
    if live_balance:
        row.update(
            {
                "current": cents_to_dollars_or_raw(current, current_cents),
                "current_cents": current_cents,
                "available": cents_to_dollars_or_raw(available, available_cents),
                "available_cents": available_cents,
            }
        )
    return row


def cents_to_dollars_or_raw(raw: Any, cents: Optional[int]) -> Optional[float]:
    if cents is None:
        return None
    return round(cents / 100.0, 2)


def shape_balances(payload: Dict[str, Any], *, as_of: Optional[str] = None) -> Dict[str, Any]:
    as_of = as_of or utc_now()
    accounts = payload.get("accounts") or []
    in_scope, missing = filter_x_money_accounts(accounts)
    rows = [_account_row(a, as_of=as_of, live_balance=True) for a in in_scope]
    total_cents = 0
    total_avail_cents = 0
    have_current = False
    have_avail = False
    for r in rows:
        if r.get("current_cents") is not None:
            total_cents += int(r["current_cents"])
            have_current = True
        if r.get("available_cents") is not None:
            total_avail_cents += int(r["available_cents"])
            have_avail = True
    return {
        "venue": "x_money",
        "item_id": ((payload.get("item") or {}).get("item_id")),
        "as_of": as_of,
        "rounding": "ROUND_HALF_EVEN",
        "accounts": rows,
        "missing_last4": missing,
        "excluded_last4": sorted(list({"8680"})),
        "total_current_cents": total_cents if have_current else None,
        "total_current": round(total_cents / 100.0, 2) if have_current else None,
        "total_available_cents": total_avail_cents if have_avail else None,
        "total_available": round(total_avail_cents / 100.0, 2) if have_avail else None,
        "spaces": dict(SPACE_BY_LAST4),
        "source": "plaid",
        "read_only": True,
    }


def shape_accounts(payload: Dict[str, Any], *, as_of: Optional[str] = None) -> Dict[str, Any]:
    as_of = as_of or utc_now()
    accounts = payload.get("accounts") or []
    in_scope, missing = filter_x_money_accounts(accounts)
    rows = [_account_row(a, as_of=as_of, live_balance=False) for a in in_scope]
    return {
        "venue": "x_money",
        "item_id": ((payload.get("item") or {}).get("item_id")),
        "as_of": as_of,
        "accounts": rows,
        "missing_last4": missing,
        "excluded_last4": ["8680"],
        "read_only": True,
    }


def shape_transactions(
    payload: Dict[str, Any],
    accounts_payload: Dict[str, Any],
    *,
    start_date: str,
    end_date: str,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    as_of = as_of or utc_now()
    in_scope, missing = filter_x_money_accounts(accounts_payload.get("accounts") or payload.get("accounts") or [])
    id_to_meta = {}
    for a in in_scope:
        n4 = last4_of_account(a)
        id_to_meta[a.get("account_id")] = {
            "last4": n4,
            "space": space_name_for(n4, a),
        }
    allowed_ids = set(id_to_meta)
    txs_out: List[Dict[str, Any]] = []
    for tx in payload.get("transactions") or []:
        aid = tx.get("account_id")
        if aid not in allowed_ids:
            continue
        amount = tx.get("amount")
        amount_cents = dollars_to_cents(amount)
        meta = id_to_meta.get(aid) or {}
        txs_out.append(
            {
                "transaction_id": tx.get("transaction_id"),
                "account_id": aid,
                "space": meta.get("space"),
                "last4": meta.get("last4"),
                "date": tx.get("date"),
                "name": tx.get("name") or tx.get("merchant_name"),
                "merchant_name": tx.get("merchant_name"),
                "pending": bool(tx.get("pending")),
                "amount": cents_to_dollars_or_raw(amount, amount_cents),
                "amount_cents": amount_cents,
                "amount_sign": "plaid_positive_outflow",
                "iso_currency_code": tx.get("iso_currency_code") or "USD",
                "category": tx.get("category"),
            }
        )
    return {
        "venue": "x_money",
        "as_of": as_of,
        "start_date": start_date,
        "end_date": end_date,
        "rounding": "ROUND_HALF_EVEN",
        "transactions": txs_out,
        "count": len(txs_out),
        "missing_last4": missing,
        "read_only": True,
        "source": "plaid",
    }


class PlaidBankSession:
    def __init__(self, client: Optional[PlaidClient] = None):
        self.client = client

    def _client(self) -> PlaidClient:
        if self.client is None:
            self.client = PlaidClient.from_env()
        return self.client

    def get_balances(self) -> Dict[str, Any]:
        payload = self._client().balances_get()
        return shape_balances(payload)

    def list_accounts(self) -> Dict[str, Any]:
        payload = self._client().accounts_get()
        return shape_accounts(payload)

    def list_transactions(self, days: int = 14) -> Dict[str, Any]:
        days = max(1, min(int(days or 14), 90))
        end = date.today()
        start = end - timedelta(days=days)
        start_s, end_s = _iso_day(start), _iso_day(end)
        client = self._client()
        accounts_payload = client.accounts_get()
        payload = client.transactions_get(start_date=start_s, end_date=end_s, count=250)
        return shape_transactions(payload, accounts_payload, start_date=start_s, end_date=end_s)

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        arguments = arguments or {}
        if is_write_tool(name) or name not in ALLOWED_TOOL_NAMES:
            raise PlaidError(f"tool {name!r} is not exposed (read-only plaid-bank)")
        if name == "get_balances":
            return self.get_balances()
        if name == "list_accounts":
            return self.list_accounts()
        if name == "list_transactions":
            return self.list_transactions(days=int(arguments.get("days") or 14))
        raise PlaidError(f"tool {name!r} is not exposed (read-only plaid-bank)")


def _text_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    import json

    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=False)}],
        "structuredContent": payload,
        "isError": False,
    }


def _error_result(message: str) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def handle_rpc(session: PlaidBankSession, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC object. Notifications return None."""
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": (
                    "Read-only X Money Plaid. Tools: get_balances, list_accounts, "
                    "list_transactions. No transfers/payments. Last4 2201/0895/3326/4867."
                ),
            },
        }
    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": list(ALLOWED_TOOLS)}}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        try:
            payload = session.call_tool(name, arguments if isinstance(arguments, dict) else {})
            return {"jsonrpc": "2.0", "id": req_id, "result": _text_result(payload)}
        except PlaidError as e:
            return {"jsonrpc": "2.0", "id": req_id, "result": _error_result(str(e))}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": req_id, "result": _error_result(f"internal error: {e}")}
    if req_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }
