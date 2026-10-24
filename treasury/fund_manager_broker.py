#!/usr/bin/env python3
"""Buy-only Robinhood broker adapter for the fund-manager order gate (#1093).

Selected by the order gate via ``FM_BROKER_ADAPTER``::

    FM_BROKER_ADAPTER=treasury.fund_manager_broker:submit

``fund_manager_order_gate.execute_intents`` is the ONLY caller: it hands
``submit`` a ``GatedOrder`` the gate minted. This module adds defense in depth
on top of the gate and never widens it:

1. BUY-ONLY, hard. Anything that is not a plain buy (sell, short, option,
   crypto, conflicting side fields, a non-``GatedOrder``) is rejected here
   regardless of what the gate said.
2. Kill switches: ``FM_BROKER_DISABLE=1``; policy ``live`` must be literally
   ``true``; ``execution.broker_adapter.enabled`` must be ``true``.
3. Local review: symbol on ``execution.broker_adapter.symbol_allowlist`` (and not
   in ``guardrails.blocked_symbols``), notional > 0, >= min, <= per-order max,
   today's placed total + this <= daily max, dollar amount rounded DOWN to cents.
4. Duplicate check (two layers): an idempotency key per intended order
   (ET run date + account + symbol + side + cents) persisted in
   ``treasury/snapshots/fund_manager_broker_ledger.json`` under an flock, and the
   broker's own open/recent orders for the agentic account.
5. Broker review: live buying power and account number, tradability (fractional
   for dollar orders), then the broker's ``review_equity_order`` preview.
6. Place: the ledger row is persisted as ``placing`` BEFORE the place call, so a
   crash or timeout can never be retried into a double buy
   (``place_error_outcome_unknown`` keeps blocking that key).

Every outcome is a terminal status string (see ``TERMINAL_STATUSES``) with a
``broker_reason``. Nothing secret is logged: no tokens, no client output beyond
order id/state.

The Robinhood client is injectable (``client=`` or ``FM_BROKER_CLIENT``). The
repo's only Robinhood auth is the Grok CLI + ``robinhood-trading`` MCP OAuth on
the producer host (prism); ``GrokMcpClient`` drives that same mechanism with a
per-call tool allow/deny list. No new credentials are introduced.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury import fund_manager_order_gate as gate  # noqa: E402
from treasury.fund_manager_order_gate import grok_deny_args  # noqa: E402

ADAPTER_SPEC = "treasury.fund_manager_broker:submit"
CLIENT_ENV = "FM_BROKER_CLIENT"  # "grok_mcp" | "pkg.module:factory"; unset => no client
DISABLE_ENV = "FM_BROKER_DISABLE"  # "1" => place nothing
LEDGER_PATH = ROOT / "treasury" / "snapshots" / "fund_manager_broker_ledger.json"
LEDGER_KEEP_DAYS = 45

# ---- terminal statuses ----------------------------------------------------
S_PLACED = "placed"  # in gate.EXECUTED_STATUSES
S_NOT_GATED = "rejected_not_gated"
S_NON_BUY = "rejected_non_buy_side"
S_DISABLED = "not_submitted_adapter_disabled"
S_LIVE_FALSE = "not_submitted_live_false"
S_CONFIG = "not_submitted_adapter_config"
S_NO_CLIENT = "not_submitted_no_broker_client"
S_REVIEW_FAILED = "not_submitted_review_failed"
S_BROKER_REJECTED = "not_submitted_broker_rejected"
S_DUP_KEY = "skipped_duplicate_idempotency"
S_DUP_BROKER = "skipped_duplicate_broker_order"
S_LEDGER = "not_submitted_ledger_unavailable"
S_PLACE_UNKNOWN = "place_error_outcome_unknown"
TERMINAL_STATUSES = frozenset(
    {
        S_PLACED, S_NOT_GATED, S_NON_BUY, S_DISABLED, S_LIVE_FALSE, S_CONFIG, S_NO_CLIENT,
        S_REVIEW_FAILED, S_BROKER_REJECTED, S_DUP_KEY, S_DUP_BROKER, S_LEDGER, S_PLACE_UNKNOWN,
    }
)
# Ledger states that block a re-submit of the same idempotency key.
_BLOCKING_LEDGER_STATES = frozenset({"placing", S_PLACED, S_PLACE_UNKNOWN})
# Broker order states that do NOT count as a live/duplicate order.
_DEAD_BROKER_STATES = frozenset({"cancelled", "canceled", "rejected", "failed", "voided", "expired"})
_OPEN_BROKER_STATES = frozenset(
    {"queued", "unconfirmed", "confirmed", "partially_filled", "pending", "new", "open", "accepted"}
)

try:  # ET run date for the idempotency key
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = timezone(timedelta(hours=-4))


class BrokerError(Exception):
    """Client-side failure. The message must never contain secrets."""


class RobinhoodClient(Protocol):
    """What the adapter needs from a broker client (all agentic-account scoped)."""

    def get_account(self, account_number: str) -> Dict[str, Any]:
        """{"account_number": str, "buying_power": float, "cash": float?}"""

    def get_recent_orders(self, account_number: str, since: datetime) -> List[Dict[str, Any]]:
        """[{"order_id","symbol","side","state","dollar_amount"|"notional_usd","created_at"}]"""

    def get_tradability(self, symbol: str) -> Dict[str, Any]:
        """{"tradable": bool, "fractional_tradable": bool}"""

    def review_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """{"ok": bool, "reason": str, "warnings": [...]} -- no order is created."""

    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """{"order_id": str, "state": str, "symbol", "side", "dollar_amount"}"""


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def _d(val: Any) -> Optional[Decimal]:
    n = gate._num(val)
    if n is None:
        return None
    try:
        return Decimal(str(n))
    except InvalidOperation:
        return None


def load_adapter_config(policy_path: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """(config, error). Reads ``live``, ``account``, ``guardrails`` and
    ``execution.broker_adapter`` from fund_manager.json. Any problem => error."""
    p = Path(policy_path or gate.POLICY_PATH)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"policy unreadable ({type(exc).__name__})"
    if not isinstance(data, dict):
        return None, "policy is not an object"
    ex = data.get("execution") if isinstance(data.get("execution"), dict) else {}
    ba = ex.get("broker_adapter")
    if not isinstance(ba, dict):
        return None, "execution.broker_adapter missing"
    allow = ba.get("symbol_allowlist")
    if not isinstance(allow, list) or not allow or not all(isinstance(s, str) and s.strip() for s in allow):
        return None, "execution.broker_adapter.symbol_allowlist missing/empty"
    acct = str(((data.get("account") or {}).get("account_number")) or "").strip()
    if not acct:
        return None, "account.account_number missing"
    max_order = _d(ba.get("max_order_notional_usd"))
    max_daily = _d(ba.get("max_daily_notional_usd"))
    min_order = _d(ba.get("min_order_notional_usd"))
    if max_order is None or max_order <= 0 or max_daily is None or max_daily <= 0:
        return None, "max_order_notional_usd / max_daily_notional_usd must be positive numbers"
    order_type = str(ba.get("order_type") or "market").strip().lower()
    if order_type != "market":
        return None, f"order_type {order_type!r} unsupported (market dollar orders only)"
    if ba.get("dollar_based", True) is not True:
        return None, "only dollar_based orders are supported"
    blocked = ((data.get("guardrails") or {}).get("blocked_symbols")) or []
    cfg = {
        "live": data.get("live") is True,
        "enabled": ba.get("enabled") is True,
        "account_number": acct,
        "allowlist": tuple(sorted({s.strip().upper() for s in allow})),
        "blocked": tuple(str(s).strip().upper() for s in blocked if isinstance(s, str)),
        "min_order": min_order if (min_order is not None and min_order > 0) else Decimal("1"),
        "max_order": max_order,
        "max_daily": max_daily,
        "order_type": "market",
        "time_in_force": str(ba.get("time_in_force") or "gfd").strip().lower(),
        "market_hours": str(ba.get("market_hours") or "regular_hours").strip().lower(),
        "dedupe_window_hours": float(gate._num(ba.get("dedupe_window_hours")) or 24.0),
        "client": str(ba.get("client") or "").strip(),
    }
    return cfg, ""


# --------------------------------------------------------------------------
# Ledger (idempotency + review/placement record)
# --------------------------------------------------------------------------


class BrokerLedger:
    def __init__(self, path: Path, data: Dict[str, Any]):
        self.path = path
        self.data = data

    @staticmethod
    def _load(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {"version": 1, "orders": {}}
        data = json.loads(path.read_text(encoding="utf-8"))  # ValueError => caller fails closed
        if not isinstance(data, dict) or not isinstance(data.get("orders"), dict):
            raise ValueError("broker ledger malformed")
        return data

    @classmethod
    @contextlib.contextmanager
    def transaction(cls, path: Optional[Path] = None):
        import fcntl

        p = Path(path or LEDGER_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        lock = open(str(p) + ".lock", "a+")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield cls(p, cls._load(p))
        finally:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            finally:
                lock.close()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".broker-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.data["orders"].get(key)

    def put(self, key: str, row: Dict[str, Any]) -> None:
        self.data["orders"][key] = row
        self.save()

    def placed_total(self, run_date: str) -> Decimal:
        tot = Decimal("0")
        for row in self.data["orders"].values():
            if row.get("run_date") == run_date and row.get("status") in _BLOCKING_LEDGER_STATES:
                tot += _d(row.get("dollar_amount")) or Decimal("0")
        return tot

    def prune(self, now: datetime) -> None:
        cutoff = (now.astimezone(_ET) - timedelta(days=LEDGER_KEEP_DAYS)).date().isoformat()
        self.data["orders"] = {k: v for k, v in self.data["orders"].items() if str(v.get("run_date") or "9") >= cutoff}


def idempotency_key(run_date: str, account: str, symbol: str, side: str, cents: int) -> str:
    return f"{run_date}|{account[-4:]}|{symbol}|{side}|{cents}c"


def ref_id_for(key: str) -> str:
    """Deterministic client order ref (broker-side idempotency when supported)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "fund-manager-broker:" + key))


# --------------------------------------------------------------------------
# Client resolution
# --------------------------------------------------------------------------


def resolve_client(cfg: Dict[str, Any]) -> Optional[RobinhoodClient]:
    spec = (os.environ.get(CLIENT_ENV) or cfg.get("client") or "").strip()
    if not spec:
        return None
    if spec == "grok_mcp":
        return GrokMcpClient()
    if ":" not in spec:
        return None
    mod, _, fn = spec.partition(":")
    try:
        return getattr(importlib.import_module(mod), fn)()
    except Exception:
        return None


# --------------------------------------------------------------------------
# submit (the FM_BROKER_ADAPTER callable)
# --------------------------------------------------------------------------


def _log(msg: str) -> None:
    print(f"[fund-manager broker] {msg}", file=sys.stderr)


def _result(status: str, reason: str, **extra: Any) -> Dict[str, Any]:
    out = {"status": status, "broker_reason": reason}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def _is_plain_buy(gated: Any) -> Tuple[bool, str]:
    if not isinstance(gated, gate.GatedOrder):
        return False, "not a GatedOrder minted by the order gate"
    v = gated.verdict
    if not v.allowed:
        return False, "gate verdict is not an approval"
    it = gated.intent
    side, detail = gate.classify_side(it)
    if side != "buy" or v.side != "buy":
        return False, f"side {side!r} is not a buy {detail}".strip()
    raw = [gate._norm_token(it[k]) for k in gate._SIDE_KEYS if it.get(k) not in (None, "")]
    if raw != ["buy"] * len(raw) or not raw:
        return False, f"side fields must be exactly 'buy' (got {sorted(set(raw))})"
    if it.get("short") is True or gate._is_option(it) or gate._is_crypto(it):
        return False, "short/option/crypto orders are never placed by this adapter"
    if v.override_used:
        return False, "override-approved orders are sells; adapter is buy-only"
    return True, ""


def _cents(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _order_amount(o: Dict[str, Any]) -> Optional[Decimal]:
    for k in ("dollar_amount", "notional_usd", "amount_usd", "notional"):
        v = _d(o.get(k))
        if v is not None:
            return v
    return None


def _broker_duplicate(orders: List[Dict[str, Any]], symbol: str, amount: Decimal) -> Optional[str]:
    for o in orders:
        if not isinstance(o, dict):
            continue
        if str(o.get("symbol") or "").strip().upper() != symbol:
            continue
        if gate._norm_token(o.get("side") or "") != "buy":
            continue
        state = gate._norm_token(o.get("state") or o.get("status") or "")
        if state in _DEAD_BROKER_STATES:
            continue
        oid = str(o.get("order_id") or o.get("id") or "?")
        if state in _OPEN_BROKER_STATES:
            return f"open broker buy order for {symbol} exists (order {oid}, state {state})"
        amt = _order_amount(o)
        if amt is not None and abs(amt - amount) <= Decimal("0.01"):
            return f"recent broker buy {symbol} ${amt} matches (order {oid}, state {state or '?'})"
    return None


def submit(
    gated: Any,
    *,
    client: Optional[RobinhoodClient] = None,
    policy_path: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Review-then-place one gate-approved BUY. Never raises; returns a status dict."""
    try:
        out = _submit(gated, client, policy_path, ledger_path, now)
    except Exception as exc:  # never turn an adapter bug into an order
        out = _result(S_REVIEW_FAILED, f"adapter error {type(exc).__name__}; nothing placed")
    sym = gate.intent_symbol(gated.intent) if isinstance(gated, gate.GatedOrder) else "?"
    extra = " ".join(f"{k}={out[k]}" for k in ("idempotency_key", "order_id") if out.get(k))
    _log(f"{out['status']}: buy {sym} {extra} -- {out.get('broker_reason', '')}".replace("  ", " "))
    return out


def _submit(gated, client, policy_path, ledger_path, now) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    ok, why = _is_plain_buy(gated)
    if not ok:
        return _result(S_NOT_GATED if "GatedOrder" in why or "verdict" in why else S_NON_BUY, why)
    if (os.environ.get(DISABLE_ENV) or "").strip() == "1":
        return _result(S_DISABLED, f"{DISABLE_ENV}=1")
    cfg, err = load_adapter_config(policy_path)
    if cfg is None:
        return _result(S_CONFIG, err)
    if not cfg["live"]:
        return _result(S_LIVE_FALSE, "policy live is not true")
    if not cfg["enabled"]:
        return _result(S_CONFIG, "execution.broker_adapter.enabled is not true")

    it = gated.intent
    symbol = gate.intent_symbol(it)
    raw_amt = _d(gate.intent_notional(it))
    amount = _cents(raw_amt) if raw_amt is not None else None
    run_date = now.astimezone(_ET).date().isoformat()
    acct = cfg["account_number"]
    cents = int((amount or Decimal("0")) * 100)
    key = idempotency_key(run_date, acct, symbol, "buy", cents)
    base = {"idempotency_key": key}

    # ---- local review (no client needed) ----
    problem: Optional[str] = None
    if symbol not in cfg["allowlist"]:
        problem = f"{symbol or '?'} not on broker_adapter.symbol_allowlist {list(cfg['allowlist'])}"
    elif symbol in cfg["blocked"]:
        problem = f"{symbol} is in guardrails.blocked_symbols"
    elif amount is None or amount <= 0:
        problem = "notional must be > 0"
    elif amount < cfg["min_order"]:
        problem = f"${amount} below min_order_notional_usd ${cfg['min_order']}"
    elif amount > cfg["max_order"]:
        problem = f"${amount} above max_order_notional_usd ${cfg['max_order']}"
    if problem:
        return _result(S_REVIEW_FAILED, problem, **base)

    try:
        with BrokerLedger.transaction(ledger_path) as led:
            return _review_and_place(led, gated, cfg, client, symbol, amount, run_date, key, now)
    except (OSError, ValueError) as exc:
        return _result(S_LEDGER, f"broker ledger unavailable ({type(exc).__name__}); failing closed", **base)


def _review_and_place(led, gated, cfg, client, symbol, amount, run_date, key, now) -> Dict[str, Any]:
    base = {"idempotency_key": key}
    acct = cfg["account_number"]
    prev = led.get(key)
    if prev and prev.get("status") in _BLOCKING_LEDGER_STATES:
        return _result(
            S_DUP_KEY,
            f"idempotency key already {prev.get('status')} at {prev.get('updated_at')}"
            + (f" (order {prev.get('order_id')})" if prev.get("order_id") else ""),
            **base,
        )
    if led.placed_total(run_date) + amount > cfg["max_daily"]:
        reason = f"daily cap: ${led.placed_total(run_date)} placed today + ${amount} > max_daily_notional_usd ${cfg['max_daily']}"
        return _record(led, key, run_date, symbol, amount, S_REVIEW_FAILED, reason, now)

    client = client or resolve_client(cfg)
    if client is None:
        return _record(
            led, key, run_date, symbol, amount, S_NO_CLIENT,
            f"no broker client ({CLIENT_ENV} unset and execution.broker_adapter.client empty)", now,
        )

    order = {
        "account_number": acct,
        "symbol": symbol,
        "side": "buy",
        "type": cfg["order_type"],
        "dollar_amount": f"{amount:.2f}",
        "time_in_force": cfg["time_in_force"],
        "market_hours": cfg["market_hours"],
        "ref_id": ref_id_for(key),
    }

    # ---- broker review ----
    try:
        acc = client.get_account(acct) or {}
        if str(acc.get("account_number") or "").strip() != acct:
            return _record(led, key, run_date, symbol, amount, S_REVIEW_FAILED, "broker account mismatch (agentic only)", now)
        bp = _d(acc.get("buying_power"))
        if bp is None:
            return _record(led, key, run_date, symbol, amount, S_REVIEW_FAILED, "buying power unknown", now)
        if amount > bp:
            return _record(
                led, key, run_date, symbol, amount, S_REVIEW_FAILED,
                f"${amount} exceeds live buying power ${bp}", now,
            )
        since = now - timedelta(hours=cfg["dedupe_window_hours"])
        orders = client.get_recent_orders(acct, since)
        if not isinstance(orders, list):
            return _record(led, key, run_date, symbol, amount, S_REVIEW_FAILED, "broker orders unavailable", now)
        dup = _broker_duplicate(orders, symbol, amount)
        if dup:
            return _record(led, key, run_date, symbol, amount, S_DUP_BROKER, dup, now)
        tr = client.get_tradability(symbol) or {}
        if tr.get("tradable") is not True:
            return _record(led, key, run_date, symbol, amount, S_REVIEW_FAILED, f"{symbol} not tradable", now)
        if tr.get("fractional_tradable") is not True:  # every order here is a dollar (fractional) order
            return _record(
                led, key, run_date, symbol, amount, S_REVIEW_FAILED,
                f"{symbol} not fractional/dollar tradable", now,
            )
        rv = client.review_order(dict(order)) or {}
    except BrokerError as exc:
        return _record(led, key, run_date, symbol, amount, S_REVIEW_FAILED, f"broker review error: {exc}"[:200], now)
    except Exception as exc:
        return _record(led, key, run_date, symbol, amount, S_REVIEW_FAILED, f"broker review error {type(exc).__name__}", now)
    if rv.get("ok") is not True:
        return _record(
            led, key, run_date, symbol, amount, S_REVIEW_FAILED,
            f"broker review rejected: {str(rv.get('reason') or 'no reason')[:160]}", now,
        )
    review = {"buying_power": str(bp), "warnings": [str(w)[:120] for w in (rv.get("warnings") or [])][:5]}
    _record(led, key, run_date, symbol, amount, "placing", "reviewed; placing", now, review=review)

    # ---- place ----
    try:
        res = client.place_order(dict(order)) or {}
    except Exception as exc:
        msg = str(exc)[:160] if isinstance(exc, BrokerError) else type(exc).__name__
        return _record(
            led, key, run_date, symbol, amount, S_PLACE_UNKNOWN,
            f"place call failed ({msg}); outcome unknown -- key stays blocked, check broker", now, review=review,
        )
    state = gate._norm_token(res.get("state") or res.get("status") or "")
    oid = str(res.get("order_id") or res.get("id") or "") or None
    mism = []
    if res.get("symbol") is not None and str(res.get("symbol")).strip().upper() != symbol:
        mism.append("symbol")
    if res.get("side") is not None and gate._norm_token(res.get("side")) != "buy":
        mism.append("side")
    ramt = _order_amount(res)
    if ramt is not None and abs(ramt - amount) > Decimal("0.01"):
        mism.append("amount")
    if mism:
        return _record(
            led, key, run_date, symbol, amount, S_PLACE_UNKNOWN,
            f"broker response mismatch on {','.join(mism)} -- CHECK BROKER NOW", now, order_id=oid, review=review,
        )
    if state in _DEAD_BROKER_STATES:
        return _record(
            led, key, run_date, symbol, amount, S_BROKER_REJECTED,
            f"broker returned state {state}", now, order_id=oid, review=review,
        )
    if not oid:
        return _record(
            led, key, run_date, symbol, amount, S_PLACE_UNKNOWN,
            "broker returned no order id; outcome unknown", now, review=review,
        )
    return _record(
        led, key, run_date, symbol, amount, S_PLACED, f"placed (broker state {state or '?'})", now,
        order_id=oid, review=review, broker_state=state or None,
    )


def _record(led, key, run_date, symbol, amount, status, reason, now, *, order_id=None, review=None,
            broker_state=None) -> Dict[str, Any]:
    row = {
        "run_date": run_date,
        "symbol": symbol,
        "side": "buy",
        "dollar_amount": f"{amount:.2f}",
        "status": status,
        "reason": reason,
        "updated_at": now.isoformat(),
    }
    prev = led.get(key) or {}
    row["created_at"] = prev.get("created_at") or now.isoformat()
    # one attempt per review pass; the placing -> final transition is the same attempt
    row["attempts"] = int(prev.get("attempts") or 0) + (0 if prev.get("status") == "placing" else 1)
    if review is not None:
        row["review"] = review
    if order_id:
        row["order_id"] = order_id
    if broker_state:
        row["broker_state"] = broker_state
    led.prune(now)
    led.put(key, row)
    return _result(status, reason, idempotency_key=key, order_id=order_id)


# --------------------------------------------------------------------------
# GrokMcpClient: the repo's existing Robinhood auth (grok + robinhood-trading MCP)
# --------------------------------------------------------------------------

RH = gate.RH_SERVER
_READ_ONLY_TOOLS = ("get_accounts", "get_equity_orders", "get_equity_tradability")


class GrokMcpClient:
    """Drives the producer host's ``grok`` CLI + ``robinhood-trading`` MCP OAuth.

    Each call is a separate headless grok run constrained by ``--json-schema``
    and a per-call tool allow/deny list:

    * reads/review: every order-mutating glob from the order gate is denied
      (``gate.grok_deny_args()``) and only the named read tools are allowed;
    * place: only ``place_equity_order`` is allowed; every other mutating tool
      (options, crypto, replace/modify/submit/create, *sell*, *short*, transfers,
      exercise) stays denied. The adapter re-checks the returned symbol/side/amount.

    UNVERIFIED against live tool schemas (tool names come from prism grok
    session logs: get_accounts, get_equity_orders, get_equity_tradability,
    review_equity_order, place_equity_order). Smoke-test the read/review path on
    prism before enabling ``live``.
    """

    def __init__(self, grok_bin: Optional[str] = None, cwd: Optional[str] = None,
                 timeout_s: float = 300.0, runner: Callable[..., Any] = subprocess.run):
        self.grok_bin = grok_bin or os.environ.get("FM_BROKER_GROK_BIN") or "grok"
        self.cwd = cwd or str(ROOT)
        self.timeout_s = float(os.environ.get("FM_BROKER_GROK_TIMEOUT_S") or timeout_s)
        self.runner = runner

    @staticmethod
    def _extra_deny() -> List[str]:
        args: List[str] = []
        for t in ("cancel_*", "review_option_order", "preview_crypto_order"):
            args += ["--deny", f"MCPTool({RH}__{t})"]
        return args

    def _call(self, instruction: str, schema: Dict[str, Any], tools: Tuple[str, ...], *, allow_place: bool = False) -> Dict[str, Any]:
        prompt = (
            "You are a deterministic broker shim. Call ONLY these robinhood-trading tools: "
            + ", ".join(tools)
            + ". Make no other tool calls, do not read or write files, do not place, modify or cancel any other order. "
            "Return only JSON matching the schema.\n\n" + instruction
        )
        carve = ("place_equity_order",) if allow_place else ()
        cmd = [self.grok_bin, "-p", prompt, "--cwd", self.cwd, "--yolo", "--disable-web-search",
               "--json-schema", json.dumps(schema), *grok_deny_args(allow_tools=carve)]
        for t in tools:
            cmd += ["--allow", f"MCPTool({RH}__{t})"]
        cmd += self._extra_deny()
        try:
            r = self.runner(cmd, capture_output=True, text=True, timeout=self.timeout_s, check=False)
        except subprocess.TimeoutExpired as exc:
            raise BrokerError("grok timeout") from exc
        except OSError as exc:
            raise BrokerError(f"grok unavailable ({type(exc).__name__})") from exc
        if r.returncode != 0:
            raise BrokerError(f"grok exit {r.returncode}")
        try:
            out = json.loads((r.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise BrokerError("grok returned non-JSON") from exc
        if isinstance(out, dict) and isinstance(out.get("structured_output"), dict):
            out = out["structured_output"]
        if not isinstance(out, dict):
            raise BrokerError("grok returned non-object")
        return out

    def get_account(self, account_number: str) -> Dict[str, Any]:
        return self._call(
            f"Call get_accounts. Return the account whose account number is {account_number} with its "
            "buying_power and cash as numbers. If not found return account_number \"\".",
            {"type": "object", "required": ["account_number", "buying_power"],
             "properties": {"account_number": {"type": "string"}, "buying_power": {"type": ["number", "null"]},
                            "cash": {"type": ["number", "null"]}}},
            ("get_accounts",),
        )

    def get_recent_orders(self, account_number: str, since: datetime) -> List[Dict[str, Any]]:
        out = self._call(
            f"Call get_equity_orders for account {account_number}. Return every equity order created at or after "
            f"{since.isoformat()} (and every order not yet in a final state) with symbol, side, state, "
            "dollar_amount (null if share-based), order_id, created_at.",
            {"type": "object", "required": ["orders"], "properties": {"orders": {"type": "array", "items": {
                "type": "object", "properties": {"symbol": {"type": "string"}, "side": {"type": "string"},
                "state": {"type": "string"}, "dollar_amount": {"type": ["number", "null"]},
                "order_id": {"type": "string"}, "created_at": {"type": "string"}}}}}},
            ("get_equity_orders",),
        )
        return out.get("orders") if isinstance(out.get("orders"), list) else None  # type: ignore[return-value]

    def get_tradability(self, symbol: str) -> Dict[str, Any]:
        return self._call(
            f"Call get_equity_tradability for {symbol}. Return tradable and fractional_tradable booleans.",
            {"type": "object", "required": ["tradable", "fractional_tradable"],
             "properties": {"tradable": {"type": "boolean"}, "fractional_tradable": {"type": "boolean"}}},
            ("get_equity_tradability",),
        )

    def review_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        return self._call(
            "Call review_equity_order (a preview; it must not create an order) with exactly: "
            + json.dumps(order) + ". Return ok=true only if the review shows the order can be placed as given.",
            {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"},
             "reason": {"type": "string"}, "warnings": {"type": "array", "items": {"type": "string"}}}},
            ("review_equity_order",),
        )

    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        if order.get("side") != "buy":  # belt and braces: never send a non-buy
            raise BrokerError("non-buy order refused by client")
        return self._call(
            "Call place_equity_order EXACTLY ONCE with exactly these arguments (a BUY; never sell): "
            + json.dumps(order) + ". Return the created order's order_id, state, symbol, side and dollar_amount.",
            {"type": "object", "required": ["order_id", "state"], "properties": {
                "order_id": {"type": "string"}, "state": {"type": "string"}, "symbol": {"type": "string"},
                "side": {"type": "string"}, "dollar_amount": {"type": ["number", "null"]}}},
            ("place_equity_order",), allow_place=True,
        )


if __name__ == "__main__":  # pragma: no cover
    cfg, err = load_adapter_config()
    print(json.dumps({"adapter": ADAPTER_SPEC, "config_ok": cfg is not None, "error": err,
                      "live": (cfg or {}).get("live"), "enabled": (cfg or {}).get("enabled"),
                      "allowlist": list((cfg or {}).get("allowlist") or ())}, indent=2))
