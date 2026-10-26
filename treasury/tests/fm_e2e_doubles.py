"""Dry-run doubles for the fund-manager order path.

No network, no grok, no Robinhood. The submit wrapper records whether the
object handed to the broker is a GatedOrder from ``treasury.fund_manager_order_gate``
(the class ``submit`` checks), then calls the real adapter.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List


# One gate process resolves a new client per leg. Share placed orders so the
# 24h duplicate check still sees earlier legs of this run.
_PLACED: List[Dict[str, Any]] = []


def _log(event: Dict[str, Any]) -> None:
    raw = (os.environ.get("FM_E2E_LOG") or "").strip()
    if not raw:
        return
    path = Path(raw)
    rows: List[Any] = []
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8") or "[]")
            if isinstance(loaded, list):
                rows = loaded
        except (OSError, ValueError):
            rows = []
    rows.append(event)
    path.write_text(json.dumps(rows), encoding="utf-8")


class FakeClient:
    """Broker client that only records calls. Never reviews or places a live order."""

    def __init__(self) -> None:
        self.account = "674601752"
        self.orders: List[Dict[str, Any]] = []
        self.n = 0

    def get_account(self, account_number: str) -> Dict[str, Any]:
        _log({"event": "get_account", "account_number": account_number})
        return {
            "account_number": self.account,
            "buying_power": "7.62",
            "cash": "7.62",
            "settled_cash": "7.62",
            "unleveraged_buying_power": "7.62",
        }

    def get_recent_orders(self, account_number: str, since: Any) -> List[Dict[str, Any]]:
        _log({"event": "get_recent_orders", "account_number": account_number})
        return list(_PLACED)

    def get_tradability(self, symbol: str) -> Dict[str, Any]:
        _log({"event": "get_tradability", "symbol": symbol})
        return {"tradable": True, "fractional_tradable": True}

    def get_quotes(self, symbols: List[str]) -> Dict[str, float]:
        _log({"event": "get_quotes", "symbols": list(symbols)})
        return {}

    def review_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        _log({"event": "review_order", "order": dict(order)})
        return {"ok": True, "reason": "", "warnings": []}

    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        self.n += 1
        res = {
            "order_id": f"dry-{self.n}",
            "state": "accepted",
            "symbol": order.get("symbol"),
            "side": "buy",
            "dollar_amount": order.get("dollar_amount"),
        }
        _log({"event": "place_order", "order": dict(order), "result": res})
        self.orders.append(res)
        _PLACED.append(res)
        return res


def factory() -> FakeClient:
    return FakeClient()


def plan(intents: Any, *args: Any, **kwargs: Any):
    from treasury import fund_manager_broker as broker

    return broker.plan_intents(intents, *args, **kwargs)


def submit(gated: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    from treasury import fund_manager_broker as broker
    from treasury import fund_manager_order_gate as gate

    intent = getattr(gated, "intent", None)
    intent = intent if isinstance(intent, dict) else {}
    _log(
        {
            "event": "submit",
            "is_gated": isinstance(gated, gate.GatedOrder),
            "cls": f"{type(gated).__module__}.{type(gated).__name__}",
            "symbol": intent.get("symbol"),
            "side": intent.get("side"),
            "notional_usd": intent.get("notional_usd"),
        }
    )
    return broker.submit(gated, *args, **kwargs)


submit.plan = plan  # type: ignore[attr-defined]
