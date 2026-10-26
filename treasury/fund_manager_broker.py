#!/usr/bin/env python3
"""Buy-only Robinhood broker adapter + deterministic run planner (#1093).

Selected by the order gate via ``FM_BROKER_ADAPTER``::

    FM_BROKER_ADAPTER=treasury.fund_manager_broker:submit

Two deterministic stages sit between the LLM's buy intents and an order:

**Run planner** (``plan_intents``; the gate calls it through ``submit.plan``
before gating). Policy (Nakatoshi 2026-10-24, Grok-approved): Agentic is ~60%
stocks (TSLA/SPCX 50/50) and 40% BTC + digital credit; hold, never sell; new
cash fills the BTC + digital-credit sleeve first until it reaches 40%.

* window: ET weekday, not a market holiday, ``earliest_place_et`` (10:00) <=
  now < ``latest_place_et`` (15:50, or 10 min before an early close). Else
  ``not_submitted_before_window`` / ``not_submitted_outside_market_hours`` (no queuing).
* one buy run per ET day (shared by the daily run and bp-poll; external
  same-day fills count): else ``skipped_already_bought_today``.
* run total = min(LLM-proposed allowlisted buys, settled cash,
  ``max_run_notional_usd`` (160), daily cap left). Under ``min_investable_usd`` (5):
  ``skipped_below_min_investable``.
* sleeve gate: BTC sleeve under 40% of invested value => the whole run goes to
  the BTC sleeve and TSLA/SPCX legs are dropped (``not_submitted_sleeve_gate``).
  At/over 40%, BTC gets what keeps it at 40% after the run and stocks get the
  rest, split to move TSLA/SPCX toward 50/50.
* BTC-sleeve weights: STRC 25 / SATA 25 / STRK 15 of the BTC amount, each within
  max($1, 5% of the run total) of the LLM's figure, else repaired to target
  (``weights_repaired``). The floating 35% always goes to ``fold_into`` (IBIT)
  unless ``execution.broker_adapter.honor_ai_floating`` is literally true.
  AI-proposed floating names (MSTR, miners) are ignored. Legs > $75 spill into
  IBIT; legs < $1 fold into IBIT.

**submit** (per gate-approved order): buy-only hard reject, kill switches,
planned-leg check (``not_submitted_unplanned`` for anything the planner did not
emit), window, day guard, local review (allowlist, caps), idempotency ledger,
broker review (account, live buying power / settled cash, same-day external
fills -> day guard, open/recent duplicates, tradability, ``review_equity_order``),
then place. The ledger row is ``placing`` before the place call, so a crash or
timeout is ``place_error_outcome_unknown`` and never retried.

Sells are never placed here; any sell is a manual action Chris takes himself.
No secrets are logged. The Robinhood client is injectable (``client=`` or
``FM_BROKER_CLIENT``); ``GrokMcpClient`` reuses prism's grok + robinhood-trading
MCP OAuth (no new credentials).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, time as dtime, timedelta, timezone
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
CENT = Decimal("0.01")
ZERO = Decimal("0")

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
S_UNPLANNED = "not_submitted_unplanned"
S_BEFORE_WINDOW = "not_submitted_before_window"
S_OUTSIDE_HOURS = "not_submitted_outside_market_hours"
S_DAY_DONE = "skipped_already_bought_today"
S_SLEEVE_GATE = "not_submitted_sleeve_gate"
S_BELOW_MIN = "skipped_below_min_investable"
S_BOOK_UNKNOWN = "not_submitted_book_unknown"
S_CALENDAR = "not_submitted_calendar_unknown"
# plan statuses
P_PLANNED = "planned"
P_REPAIRED = "weights_repaired"
P_NO_BUYS = "no_buy_intents"
TERMINAL_STATUSES = frozenset(
    {
        S_PLACED, S_NOT_GATED, S_NON_BUY, S_DISABLED, S_LIVE_FALSE, S_CONFIG, S_NO_CLIENT,
        S_REVIEW_FAILED, S_BROKER_REJECTED, S_DUP_KEY, S_DUP_BROKER, S_LEDGER, S_PLACE_UNKNOWN,
        S_UNPLANNED, S_BEFORE_WINDOW, S_OUTSIDE_HOURS, S_DAY_DONE, S_SLEEVE_GATE, S_BELOW_MIN,
        S_BOOK_UNKNOWN, S_CALENDAR,
    }
)
PLAN_SKIP_STATUSES = frozenset({S_CONFIG, S_CALENDAR, S_BEFORE_WINDOW, S_OUTSIDE_HOURS, S_DAY_DONE, S_BELOW_MIN, S_BOOK_UNKNOWN, S_LEDGER})
# Ledger states that block a re-submit of the same idempotency key.
_BLOCKING_LEDGER_STATES = frozenset({"placing", S_PLACED, S_PLACE_UNKNOWN})
# Broker order states that do NOT count as a live/duplicate order.
_DEAD_BROKER_STATES = frozenset({"cancelled", "canceled", "rejected", "failed", "voided", "expired"})
_OPEN_BROKER_STATES = frozenset(
    {"queued", "unconfirmed", "confirmed", "partially_filled", "pending", "new", "open", "accepted"}
)

try:  # ET run date for the idempotency key and the trading window
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = timezone(timedelta(hours=-4))


class BrokerError(Exception):
    """Client-side failure. The message must never contain secrets."""


class RobinhoodClient(Protocol):
    """What the adapter needs from a broker client (all agentic-account scoped)."""

    def get_account(self, account_number: str) -> Dict[str, Any]:
        """{"account_number": str, "buying_power": float, "cash": float?, "settled_cash": float?}"""

    def get_recent_orders(self, account_number: str, since: datetime) -> List[Dict[str, Any]]:
        """[{"order_id","symbol","side","state","dollar_amount"|"notional_usd","created_at"}]"""

    def get_tradability(self, symbol: str) -> Dict[str, Any]:
        """{"tradable": bool, "fractional_tradable": bool}"""

    def get_quotes(self, symbols: List[str]) -> Dict[str, float]:
        """{symbol: last trade price} -- read-only quote call."""

    def review_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """{"ok": bool, "reason": str, "warnings": [...]} -- no order is created."""

    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """{"order_id", "state", "symbol", "side", "dollar_amount", "quantity"?, "average_price"?}"""


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


def _hhmm(val: Any, default: str) -> Optional[dtime]:
    try:
        h, m = str(val or default).strip().split(":")
        return dtime(int(h), int(m))
    except (ValueError, TypeError):
        return None


def _upper_list(val: Any) -> List[str]:
    return [str(s).strip().upper() for s in (val or []) if isinstance(s, str) and s.strip()]


def load_adapter_config(policy_path: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """(config, error). Reads ``live``, ``account``, ``guardrails``, ``targets``,
    ``sleeves`` and ``execution.broker_adapter`` from fund_manager.json."""
    p = Path(policy_path or gate.test_path("FM_POLICY_PATH", gate.POLICY_PATH))
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
    allow = _upper_list(ba.get("symbol_allowlist"))
    if not allow:
        return None, "execution.broker_adapter.symbol_allowlist missing/empty"
    acct = str(((data.get("account") or {}).get("account_number")) or "").strip()
    if not acct:
        return None, "account.account_number missing"
    max_order = _d(ba.get("max_order_notional_usd"))
    max_daily = _d(ba.get("max_daily_notional_usd"))
    max_run = _d(ba.get("max_run_notional_usd"))
    min_order = _d(ba.get("min_order_notional_usd"))
    min_inv = _d(ba.get("min_investable_usd"))
    for name, v in (("max_order_notional_usd", max_order), ("max_daily_notional_usd", max_daily),
                    ("max_run_notional_usd", max_run), ("min_investable_usd", min_inv)):
        if v is None or v <= 0:
            return None, f"execution.broker_adapter.{name} must be a positive number"
    order_type = str(ba.get("order_type") or "market").strip().lower()
    if order_type != "market":
        return None, f"order_type {order_type!r} unsupported (market dollar orders only)"
    if ba.get("dollar_based", True) is not True:
        return None, "only dollar_based orders are supported"
    if ba.get("extended_hours", False) is not False:
        return None, "extended_hours must be false (regular hours only, no after-hours queuing)"
    earliest = _hhmm(ba.get("earliest_place_et"), "10:00")
    latest = _hhmm(ba.get("latest_place_et"), "15:50")
    if earliest is None or latest is None or earliest >= latest:
        return None, "earliest_place_et / latest_place_et malformed"
    sleeves = data.get("sleeves") or {}
    btc = sleeves.get("btc_digital_credit") or {}
    nmw = btc.get("new_money_weights") or {}
    fixed: Dict[str, Decimal] = {}
    for sym in ("STRC", "SATA", "STRK"):
        w = _d(nmw.get(sym))
        if w is None or w < 0:
            return None, f"sleeves.btc_digital_credit.new_money_weights.{sym} missing"
        fixed[sym] = w
    floating_w = _d(nmw.get("floating"))
    floating = _upper_list(nmw.get("floating_universe"))
    fold_into = str(ba.get("fold_into") or nmw.get("floating_default") or "IBIT").strip().upper()
    if floating_w is None or abs(sum(fixed.values()) + floating_w - 1) > Decimal("0.0001"):
        return None, "new_money_weights must sum to 1"
    if fold_into not in floating:
        return None, f"fold_into {fold_into} must be in floating_universe"
    stocks = _upper_list((sleeves.get("stocks_growth") or {}).get("symbols"))
    if len(stocks) != 2:
        return None, "sleeves.stocks_growth.symbols must be exactly two names (50/50)"
    missing = [s for s in list(fixed) + floating + stocks if s not in allow]
    if missing:
        return None, f"sleeve symbols not on symbol_allowlist: {missing}"
    btc_target = _d((data.get("targets") or {}).get("btc_digital_credit_pct"))
    if btc_target is None or not (0 < btc_target < 1):
        return None, "targets.btc_digital_credit_pct malformed"
    blocked = ((data.get("guardrails") or {}).get("blocked_symbols")) or []
    cal, cal_err = _load_calendar(ba.get("market_calendar"))
    cfg = {
        "live": data.get("live") is True,
        "enabled": ba.get("enabled") is True,
        "account_number": acct,
        "allowlist": tuple(sorted(set(allow))),
        "blocked": tuple(str(s).strip().upper() for s in blocked if isinstance(s, str)),
        "min_order": min_order if (min_order is not None and min_order > 0) else Decimal("1"),
        "max_order": max_order,
        "max_daily": max_daily,
        "max_run": max_run,
        "min_investable": min_inv,
        "order_type": "market",
        "time_in_force": str(ba.get("time_in_force") or "gfd").strip().lower(),
        "market_hours": "regular_hours",
        "earliest": earliest,
        "latest": latest,
        "calendar": cal,
        "calendar_error": cal_err,
        "dedupe_window_hours": float(gate._num(ba.get("dedupe_window_hours")) or 24.0),
        "snapshot_max_age_hours": float(gate._num(ba.get("snapshot_max_age_hours")) or 6.0),
        "tol_usd": _d(ba.get("weight_tolerance_usd")) or Decimal("1"),
        "tol_pct": _d(ba.get("weight_tolerance_pct")) or Decimal("0.05"),
        "fold_below": _d(ba.get("fold_below_usd")) or Decimal("1"),
        "fold_into": fold_into,
        # Only an explicit true lets AI name the floating leg. Missing/false
        # keeps that leg on fold_into (IBIT).
        "honor_ai_floating": ba.get("honor_ai_floating") is True,
        "fixed_weights": fixed,
        "floating_weight": floating_w,
        "floating": tuple(floating),
        "stocks": tuple(stocks),
        "btc_target": btc_target,
        "one_run_per_day": ba.get("one_buy_run_per_et_day", True) is not False,
        "low_preference": ba.get("low_preference") if isinstance(ba.get("low_preference"), dict) else {},
        "client": str(ba.get("client") or "").strip(),
    }
    return cfg, ""


def _load_calendar(raw: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """``execution.broker_adapter.market_calendar`` -> {years, holidays, early_close}.
    Anything malformed => (None, reason): orders fail closed (calendar unknown)."""
    if not isinstance(raw, dict):
        return None, "market_calendar missing"
    years = raw.get("years")
    if not isinstance(years, list) or not years or not all(isinstance(y, int) and not isinstance(y, bool) for y in years):
        return None, "market_calendar.years missing/malformed"
    hol, early = raw.get("holidays"), raw.get("early_close")
    if not isinstance(hol, list) or not isinstance(early, dict):
        return None, "market_calendar.holidays/early_close malformed"
    try:
        hdays = frozenset(datetime.strptime(str(d), "%Y-%m-%d").date().isoformat() for d in hol)
        edays = {datetime.strptime(str(k), "%Y-%m-%d").date().isoformat(): _hhmm(v, "13:00") for k, v in early.items()}
    except ValueError:
        return None, "market_calendar has a malformed date"
    if any(t is None for t in edays.values()):
        return None, "market_calendar.early_close has a malformed time"
    yrs = frozenset(years)
    if any(int(d[:4]) not in yrs for d in list(hdays) + list(edays)):
        return None, "market_calendar date outside market_calendar.years"
    return {"years": yrs, "holidays": hdays, "early_close": edays, "source": str(raw.get("source") or "")}, ""


# --------------------------------------------------------------------------
# Ledger (idempotency, plans, one-buy-run-per-ET-day guard)
# --------------------------------------------------------------------------


class BrokerLedger:
    def __init__(self, path: Path, data: Dict[str, Any]):
        self.path = path
        self.data = data

    @staticmethod
    def _load(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {"version": 2, "orders": {}, "runs": {}, "days": {}}
        data = json.loads(path.read_text(encoding="utf-8"))  # ValueError => caller fails closed
        if not isinstance(data, dict) or not isinstance(data.get("orders"), dict):
            raise ValueError("broker ledger malformed")
        for k in ("runs", "days"):
            if not isinstance(data.setdefault(k, {}), dict):
                raise ValueError("broker ledger malformed")
        return data

    @classmethod
    @contextlib.contextmanager
    def transaction(cls, path: Optional[Path] = None):
        import fcntl

        p = Path(path or gate.test_path("FM_BROKER_LEDGER_PATH", LEDGER_PATH))
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
        tot = ZERO
        for row in self.data["orders"].values():
            if row.get("run_date") == run_date and row.get("status") in _BLOCKING_LEDGER_STATES:
                tot += _d(row.get("dollar_amount")) or ZERO
        return tot

    def run_order_ids(self, run_id: str) -> set:
        return {
            str(r.get("order_id")) for r in self.data["orders"].values()
            if r.get("run_id") == run_id and r.get("order_id")
        }

    def day_claim(self, run_date: str) -> Optional[Dict[str, Any]]:
        return self.data["days"].get(run_date)

    def claim_day(self, run_date: str, by: str, reason: str, now: datetime) -> None:
        if run_date not in self.data["days"]:
            self.data["days"][run_date] = {"claimed_by": by, "reason": reason, "at": now.isoformat()}
            self.save()

    def prune(self, now: datetime) -> None:
        cutoff = (now.astimezone(_ET) - timedelta(days=LEDGER_KEEP_DAYS)).date().isoformat()
        self.data["orders"] = {k: v for k, v in self.data["orders"].items() if str(v.get("run_date") or "9") >= cutoff}
        self.data["runs"] = {k: v for k, v in self.data["runs"].items() if str(v.get("run_date") or "9") >= cutoff}
        self.data["days"] = {k: v for k, v in self.data["days"].items() if k >= cutoff}


def idempotency_key(run_date: str, account: str, symbol: str, side: str, cents: int) -> str:
    return f"{run_date}|{account[-4:]}|{symbol}|{side}|{cents}c"


def ref_id_for(key: str) -> str:
    """Deterministic client order ref (broker-side idempotency when supported)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "fund-manager-broker:" + key))


# --------------------------------------------------------------------------
# Trading window, settled cash, book
# --------------------------------------------------------------------------


def et_date(now: datetime) -> str:
    return now.astimezone(_ET).date().isoformat()


def market_window(now: datetime, cfg: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """(None, "") inside the placing window, else (status, reason). Never queues."""
    et = now.astimezone(_ET)
    day = et.date().isoformat()
    cal = cfg.get("calendar")
    if not cal:
        return S_CALENDAR, f"market calendar unavailable ({cfg.get('calendar_error') or 'not loaded'}); placing nothing"
    if et.year not in cal["years"]:
        return S_CALENDAR, f"{day} is outside the configured market calendar years {sorted(cal['years'])}; placing nothing"
    if et.weekday() >= 5:
        return S_OUTSIDE_HOURS, f"weekend ({day} ET); no queuing"
    if day in cal["holidays"]:
        return S_OUTSIDE_HOURS, f"market holiday {day}; no queuing"
    latest = cfg["latest"]
    if day in cal["early_close"]:
        close = cal["early_close"][day] or dtime(13, 0)
        latest = min(latest, (datetime.combine(et.date(), close) - timedelta(minutes=10)).time())
    t = et.time()
    if t < cfg["earliest"]:
        return S_BEFORE_WINDOW, f"{t.strftime('%H:%M')} ET is before {cfg['earliest'].strftime('%H:%M')} ET; not queued"
    if t >= latest:
        return S_OUTSIDE_HOURS, f"{t.strftime('%H:%M')} ET is after {latest.strftime('%H:%M')} ET; no after-hours queuing"
    return None, ""


def settled_cash(account: Dict[str, Any]) -> Optional[Decimal]:
    """Settled, spendable cash. Explicit settled fields win; else the most
    conservative of cash / buying power / unleveraged buying power."""
    for k in ("settled_cash", "cash_available_for_withdrawal", "withdrawable_cash"):
        v = _d(account.get(k))
        if v is not None:
            return max(ZERO, v)
    vals = [_d(account.get(k)) for k in ("cash", "buying_power", "unleveraged_buying_power")]
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:  # need at least cash + buying power
        return None
    return max(ZERO, min(vals))


def _snapshot_agentic(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ag = snapshot.get("agentic") if isinstance(snapshot.get("agentic"), dict) else None
    if ag is None and snapshot.get("agentic_allowed") is True:
        ag = snapshot
    return ag


def load_snapshot(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(
            Path(path or gate.test_path("FM_RH_SNAPSHOT_PATH", gate.RH_SNAPSHOT_PATH)).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _needs_quotes(snapshot: Dict[str, Any]) -> List[str]:
    """Held symbols whose snapshot row has no market value / last price."""
    ag = _snapshot_agentic(snapshot) or {}
    out = []
    for pos in ag.get("positions") or []:
        if not isinstance(pos, dict) or (_d(pos.get("quantity")) or ZERO) <= 0:
            continue
        if _d(pos.get("market_value")) is None and not any(
            (_d(pos.get(k)) or ZERO) > 0 for k in ("last_price", "price", "mark_price")
        ):
            out.append(str(pos.get("symbol") or "").strip().upper())
    return [s for s in out if s]


def live_quotes(snapshot: Dict[str, Any], cfg: Dict[str, Any], client: Optional["RobinhoodClient"]) -> Tuple[Dict[str, Decimal], str]:
    """Read-only RH quotes for held symbols lacking a market value.
    Returns (quotes, valuation label). Any failure => cost-basis fallback label."""
    need = _needs_quotes(snapshot)
    if not need:
        return {}, "snapshot_market_value"
    if client is None or not hasattr(client, "get_quotes"):
        return {}, "cost_basis_fallback: no broker client for quotes"
    try:
        raw = client.get_quotes(sorted(set(need))) or {}
    except Exception as exc:
        msg = str(exc)[:80] if isinstance(exc, BrokerError) else type(exc).__name__
        return {}, f"cost_basis_fallback: quote call failed ({msg})"
    q = {str(k).strip().upper(): _d(v) for k, v in (raw.items() if isinstance(raw, dict) else [])}
    q = {k: v for k, v in q.items() if v is not None and v > 0}
    missing = [s for s in need if s not in q]
    if not q:
        return {}, "cost_basis_fallback: quotes returned nothing usable"
    if missing:
        return q, f"live_quotes_partial: cost_basis_fallback for {','.join(sorted(set(missing)))}"
    return q, "live_quotes"


def book_values(snapshot: Dict[str, Any], cfg: Dict[str, Any], quotes: Optional[Dict[str, Decimal]] = None) -> Dict[str, Decimal]:
    """Invested market value by sleeve + per stock symbol: snapshot market value,
    else snapshot last price, else live quote, else cost basis."""
    quotes = quotes or {}
    ag = _snapshot_agentic(snapshot) or {}
    btc_set = set(cfg["fixed_weights"]) | set(cfg["floating"])
    out = {"btc": ZERO, "stocks": ZERO, "other": ZERO}
    for s in cfg["stocks"]:
        out["sym:" + s] = ZERO
    for pos in ag.get("positions") or []:
        if not isinstance(pos, dict):
            continue
        sym = str(pos.get("symbol") or "").strip().upper()
        qty = _d(pos.get("quantity")) or ZERO
        mv = _d(pos.get("market_value"))
        if mv is None:
            px = None
            for k in ("last_price", "price", "mark_price"):
                px = _d(pos.get(k))
                if px is not None and px > 0:
                    break
                px = None
            if px is None:
                px = quotes.get(sym) or _d(pos.get("average_buy_price"))
            mv = qty * (px or ZERO)
        if mv <= 0:
            continue
        if sym in btc_set:
            out["btc"] += mv
        elif sym in cfg["stocks"]:
            out["stocks"] += mv
            out["sym:" + sym] += mv
        else:
            out["other"] += mv
    return out


def _snapshot_age_hours(snapshot: Dict[str, Any], now: datetime) -> Optional[float]:
    ag = _snapshot_agentic(snapshot) or {}
    ts = gate._parse_ts(ag.get("as_of") or snapshot.get("as_of"))
    if ts is None:
        return None
    return (now - ts).total_seconds() / 3600.0


# --------------------------------------------------------------------------
# Run planner
# --------------------------------------------------------------------------


def _clamp(v: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, v))


def _cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_DOWN)


def plan_intents(
    intents: Any,
    *,
    snapshot: Optional[Dict[str, Any]] = None,
    policy_path: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
    now: Optional[datetime] = None,
    persist: bool = True,
    client: Optional["RobinhoodClient"] = None,
    ai_note: str = "",
) -> Tuple[Any, Dict[str, Any]]:
    """Build the deterministic run plan. Run size, sleeve split and fixed legs
    are ours. The floating 35% goes to ``fold_into`` (IBIT) unless
    ``honor_ai_floating`` is true. This function returns intents; it does not
    submit. The order gate mints a GatedOrder for each and passes that to submit.

    Returns (intents for the gate, plan info). Non-buy intents (and a malformed
    payload) are passed through so the gate still blocks/logs/alerts them. An
    AI step that errored / proposed nothing still gets a full run (floating ->
    IBIT). Never raises: any planner error drops every buy (fail closed)."""
    now = now or gate.test_now()
    if not isinstance(intents, list):
        others: List[Any] = [] if intents is None else [intents]
        buys: List[Dict[str, Any]] = []
        ai_note = ai_note or "AI intents payload is not a list"
    else:
        buys = [it for it in intents if isinstance(it, dict) and gate.classify_side(it)[0] == "buy"]
        others = [it for it in intents if not any(it is b for b in buys)]
    try:
        orders, info = _plan(buys, snapshot, policy_path, ledger_path, now, persist, client, ai_note)
    except Exception as exc:
        orders, info = [], {"status": "plan_error", "reason": f"planner error {type(exc).__name__}; no buys"}
    _log(f"plan {info.get('status')}: {info.get('reason') or info.get('summary') or ''}")
    return others + orders, info


def _plan(buys, snapshot, policy_path, ledger_path, now, persist, client=None, ai_note=""):
    run_date = et_date(now)
    info: Dict[str, Any] = {"run_date": run_date, "dropped": [], "notes": []}

    def done(status: str, reason: str):
        info.update(status=status, reason=reason)
        return [], info

    cfg, err = load_adapter_config(policy_path)
    if cfg is None:
        return done(S_CONFIG, err)
    st, why = market_window(now, cfg)
    if st:
        return done(st, why)
    snapshot = snapshot if snapshot is not None else load_snapshot()
    ag = _snapshot_agentic(snapshot or {})
    if not ag:
        return done(S_BOOK_UNKNOWN, "no agentic block in robinhood snapshot")
    age = _snapshot_age_hours(snapshot, now)
    if age is None or age > cfg["snapshot_max_age_hours"]:
        return done(S_BOOK_UNKNOWN, f"robinhood snapshot stale/undated (age {age if age is None else round(age, 1)}h)")
    settled = settled_cash(ag)
    if settled is None:
        return done(S_BOOK_UNKNOWN, "settled cash unknown")

    with BrokerLedger.transaction(ledger_path) as led:
        claim = led.day_claim(run_date) if cfg["one_run_per_day"] else None
        if claim:
            return done(S_DAY_DONE, f"buy run already done today ({claim.get('claimed_by')}: {claim.get('reason')})")
        daily_left = max(ZERO, cfg["max_daily"] - led.placed_total(run_date))

        proposed: Dict[str, Decimal] = {}
        for it in buys:
            sym = gate.intent_symbol(it)
            amt = _d(gate.intent_notional(it))
            if sym not in cfg["allowlist"] or sym in cfg["blocked"]:
                info["dropped"].append({"symbol": sym or "?", "notional_usd": float(amt or 0), "status": S_REVIEW_FAILED,
                                        "reason": "not on broker_adapter.symbol_allowlist"})
                continue
            if amt is None or amt <= 0:
                info["dropped"].append({"symbol": sym, "notional_usd": 0.0, "status": S_REVIEW_FAILED,
                                        "reason": "no positive notional"})
                continue
            proposed[sym] = proposed.get(sym, ZERO) + amt
        prop_total = sum(proposed.values(), ZERO)
        # Run size is deterministic: ALL investable cash, never the AI's total.
        investable = _cents(min(settled, cfg["max_run"], daily_left))
        run_total = investable
        info.update(settled_cash=float(settled), investable_usd=float(investable),
                    ai_proposed_usd=float(prop_total), run_total_usd=float(run_total))
        if investable < cfg["min_investable"]:
            return done(S_BELOW_MIN, f"investable ${investable} below ${cfg['min_investable']} minimum")
        if prop_total and abs(prop_total - run_total) > CENT:
            info["notes"].append(f"AI-proposed total ${prop_total:.2f} ignored; run invests all ${run_total:.2f}")

        if client is None and _needs_quotes(snapshot):
            client = resolve_client(cfg)
        quotes, valuation = live_quotes(snapshot, cfg, client)
        info["valuation"] = valuation
        if valuation.startswith("cost_basis") or "cost_basis_fallback" in valuation:
            info["notes"].append(f"40% test valuation: {valuation}")
        bv = book_values(snapshot, cfg, quotes)
        invested = bv["btc"] + bv["stocks"] + bv["other"]
        share = (bv["btc"] / invested) if invested > 0 else ZERO
        info["btc_share_before"] = float(round(share, 4))
        tgt = cfg["btc_target"]
        stocks = cfg["stocks"]
        prop_stock = sum((proposed.get(s, ZERO) for s in stocks), ZERO)
        repaired: List[str] = []
        if share < tgt:
            btc_amt, stock_amt = run_total, ZERO
            for s in stocks:
                if proposed.get(s):
                    info["dropped"].append({"symbol": s, "notional_usd": float(proposed[s]), "status": S_SLEEVE_GATE,
                                            "reason": f"BTC + digital credit sleeve {share:.1%} < {tgt:.0%}: no {'/'.join(stocks)} buys"})
            if prop_stock > 0:
                repaired.append("sleeve_gate: stock legs redirected to the BTC sleeve")
        else:
            btc_amt = _clamp(tgt * (invested + run_total) - bv["btc"], ZERO, run_total)
            stock_amt = run_total - btc_amt

        tol = max(cfg["tol_usd"], cfg["tol_pct"] * run_total)
        legs: Dict[str, Decimal] = {}
        # stocks: move TSLA/SPCX toward 50/50
        if stock_amt > 0:
            a, b = stocks
            amt_a = _clamp((bv["sym:" + a] + bv["sym:" + b] + stock_amt) / 2 - bv["sym:" + a], ZERO, stock_amt)
            legs[a], legs[b] = amt_a, stock_amt - amt_a
            for s in stocks:
                if abs(proposed.get(s, ZERO) - legs[s]) > tol:
                    repaired.append(f"{s} ${proposed.get(s, ZERO):.2f} -> ${legs[s]:.2f} (50/50 stock rule)")
        # BTC sleeve fixed legs
        # fixed legs are always exactly STRC 25 / SATA 25 / STRK 15 of the BTC amount;
        # AI-proposed fixed amounts are ignored (a large mismatch is logged as a repair).
        fixed = {s: btc_amt * w for s, w in cfg["fixed_weights"].items()}
        if proposed:
            for s, t in fixed.items():
                if abs(proposed.get(s, ZERO) - t) > tol:
                    repaired.append(f"{s} AI ${proposed.get(s, ZERO):.2f} -> ${t:.2f} (fixed weight)")
        floating_amt = max(ZERO, btc_amt - sum(fixed.values(), ZERO))
        ai_float = {s: proposed[s] for s in cfg["floating"] if proposed.get(s)}
        ai_float_total = sum(ai_float.values(), ZERO)
        legs.update(fixed)
        # honor_ai_floating is opt-in. The shipped policy leaves it false so the
        # AI review can still name MSTR/miners and the planner will not follow.
        honor_ai = bool(cfg.get("honor_ai_floating"))
        redirect = honor_ai and ai_float_total > 0
        ignored = sorted(s for s in ai_float if s != cfg["fold_into"])
        if redirect:
            info["floating_source"] = "ai"
        elif not honor_ai and ignored:
            names = ", ".join(ignored)
            info["floating_source"] = f"{cfg['fold_into']}_only: AI floating names ignored ({names})"
            repaired.append(
                f"floating -> {cfg['fold_into']} only (AI named {names}; honor_ai_floating is false)"
            )
            info["notes"].append(
                f"AI floating names ignored ({names}); floating leg is {cfg['fold_into']} only"
            )
        elif not honor_ai and not ai_float:
            why_fb = ai_note or ("AI proposed no valid floating names" if buys else "AI proposed nothing")
            info["floating_source"] = f"{cfg['fold_into']}_fallback: {why_fb}"
        elif not honor_ai:
            info["floating_source"] = f"{cfg['fold_into']}_only"
        else:
            why_fb = ai_note or ("AI proposed no valid floating names" if buys else "AI proposed nothing")
            info["floating_source"] = f"{cfg['fold_into']}_fallback: {why_fb}"
        if floating_amt > 0:
            if redirect:
                for s, v in ai_float.items():
                    legs[s] = legs.get(s, ZERO) + floating_amt * v / ai_float_total
            else:
                legs[cfg["fold_into"]] = legs.get(cfg["fold_into"], ZERO) + floating_amt
                info["notes"].append(f"floating ${floating_amt:.2f} -> {cfg['fold_into']} ({info['floating_source']})")
        lowpref = _low_pref_active(cfg, now)
        for s in lowpref:
            if legs.get(s):
                info["notes"].append(f"{s} has low preference until {cfg['low_preference'][s].get('until')} (AI chose ${legs[s]:.2f})")

        legs = _finalize_legs(legs, run_total, cfg, info)
        if not legs:
            return done(S_BELOW_MIN, "no leg left after folding/caps")
        run_id = f"{run_date}T{now.astimezone(_ET).strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
        status = P_REPAIRED if repaired else P_PLANNED
        info.update(
            status=status, run_id=run_id, repairs=repaired,
            legs=[{"symbol": s, "notional_usd": float(v), "sleeve": "stocks_growth" if s in stocks else "btc_digital_credit"}
                  for s, v in legs.items()],
        )
        info["summary"] = (f"{status} run {run_id}: " + ", ".join(f"{s} ${v:.2f}" for s, v in legs.items())
                           + f" | valuation {info.get('valuation')} | floating {info.get('floating_source')}")
        if persist:
            led.data["runs"][run_id] = {
                "run_date": run_date, "status": status, "created_at": now.isoformat(),
                "legs": {s: f"{v:.2f}" for s, v in legs.items()}, "repairs": repaired,
            }
            led.prune(now)
            led.save()
        orders = [
            {"symbol": s, "side": "buy", "notional_usd": float(v), "purpose": "deploy", "run_id": run_id,
             "sleeve": "stocks_growth" if s in stocks else "btc_digital_credit"}
            for s, v in legs.items()
        ]
        return orders, info


def _low_pref_active(cfg: Dict[str, Any], now: datetime) -> List[str]:
    out = []
    today = et_date(now)
    for sym, spec in (cfg.get("low_preference") or {}).items():
        until = str((spec or {}).get("until") or "") if isinstance(spec, dict) else ""
        if until and today <= until:
            out.append(sym.upper())
    return out


def _finalize_legs(legs: Dict[str, Decimal], run_total: Decimal, cfg: Dict[str, Any], info: Dict[str, Any]) -> Dict[str, Decimal]:
    """Cents (round down), per-order cap spill, fold < $1 into IBIT; sum <= run_total."""
    into = cfg["fold_into"]
    legs = {s: _cents(v) for s, v in legs.items() if v > 0}
    # per-order cap: spill excess into the fold target, then trim the fold target
    for s in list(legs):
        if s != into and legs[s] > cfg["max_order"]:
            spill = legs[s] - cfg["max_order"]
            legs[s] = cfg["max_order"]
            legs[into] = legs.get(into, ZERO) + spill
            info["notes"].append(f"{s} capped at ${cfg['max_order']}; ${spill:.2f} -> {into}")
    if legs.get(into, ZERO) > cfg["max_order"]:
        trim = legs[into] - cfg["max_order"]
        legs[into] = cfg["max_order"]
        info["notes"].append(f"{into} capped at ${cfg['max_order']}; ${trim:.2f} left uninvested")
    # rounding remainder -> fold target
    rem = run_total - sum(legs.values(), ZERO)
    if ZERO < rem < Decimal("1") and legs.get(into, ZERO) + rem <= cfg["max_order"]:
        legs[into] = legs.get(into, ZERO) + rem
    # fold legs under $1 into the fold target
    for s in list(legs):
        if s != into and legs[s] < cfg["fold_below"]:
            info["notes"].append(f"{s} ${legs[s]:.2f} < ${cfg['fold_below']} folded into {into}")
            legs[into] = legs.get(into, ZERO) + legs.pop(s)
    if into in legs and legs[into] < cfg["fold_below"]:
        small = legs.pop(into)
        if legs:
            big = max(legs, key=lambda k: legs[k])
            legs[big] += small
            info["notes"].append(f"{into} ${small:.2f} < ${cfg['fold_below']} merged into {big}")
    return {s: v for s, v in legs.items() if v > 0}


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


def _order_amount(o: Dict[str, Any]) -> Optional[Decimal]:
    for k in ("dollar_amount", "notional_usd", "amount_usd", "notional"):
        v = _d(o.get(k))
        if v is not None:
            return v
    return None


def _live_buy(o: Any) -> bool:
    return (
        isinstance(o, dict)
        and gate._norm_token(o.get("side") or "") == "buy"
        and gate._norm_token(o.get("state") or o.get("status") or "") not in _DEAD_BROKER_STATES
    )


def _external_same_day(orders: List[Dict[str, Any]], run_date: str, own_ids: set) -> Optional[str]:
    """A live (non-cancelled) buy created today ET that this run did not place,
    e.g. Nakatoshi's manual buy. Undated orders count (fail closed)."""
    for o in orders:
        if not _live_buy(o):
            continue
        oid = str(o.get("order_id") or o.get("id") or "")
        if oid and oid in own_ids:
            continue
        ts = gate._parse_ts(o.get("created_at") or o.get("updated_at"))
        if ts is None or et_date(ts) == run_date:
            sym = str(o.get("symbol") or "?").upper()
            return f"same-day buy {sym} (order {oid or '?'}) placed outside this run"
    return None


def _broker_duplicate(orders: List[Dict[str, Any]], symbol: str, amount: Decimal) -> Optional[str]:
    for o in orders:
        if not _live_buy(o) or str(o.get("symbol") or "").strip().upper() != symbol:
            continue
        state = gate._norm_token(o.get("state") or o.get("status") or "")
        oid = str(o.get("order_id") or o.get("id") or "?")
        if state in _OPEN_BROKER_STATES:
            return f"open broker buy order for {symbol} exists (order {oid}, state {state})"
        amt = _order_amount(o)
        if amt is not None and abs(amt - amount) <= CENT:
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
    """Review-then-place one gate-approved, planner-emitted BUY. Never raises."""
    try:
        out = _submit(gated, client, policy_path, ledger_path, now)
    except Exception as exc:  # never turn an adapter bug into an order
        out = _result(S_REVIEW_FAILED, f"adapter error {type(exc).__name__}; nothing placed")
    sym = gate.intent_symbol(gated.intent) if isinstance(gated, gate.GatedOrder) else "?"
    extra = " ".join(f"{k}={out[k]}" for k in ("idempotency_key", "order_id") if out.get(k))
    _log(f"{out['status']}: buy {sym} {extra} -- {out.get('broker_reason', '')}".replace("  ", " "))
    return out


def _submit(gated, client, policy_path, ledger_path, now) -> Dict[str, Any]:
    now = now or gate.test_now()
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
    st, why = market_window(now, cfg)
    if st:
        return _result(st, why)

    it = gated.intent
    symbol = gate.intent_symbol(it)
    raw_amt = _d(gate.intent_notional(it))
    amount = _cents(raw_amt) if raw_amt is not None else None
    run_date = et_date(now)
    acct = cfg["account_number"]
    cents = int((amount or ZERO) * 100)
    key = idempotency_key(run_date, acct, symbol, "buy", cents)
    base = {"idempotency_key": key}

    problem: Optional[str] = None
    if symbol not in cfg["allowlist"]:
        problem = f"{symbol or '?'} not on broker_adapter.symbol_allowlist"
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
            return _review_and_place(led, it, cfg, client, symbol, amount, run_date, key, now)
    except (OSError, ValueError) as exc:
        return _result(S_LEDGER, f"broker ledger unavailable ({type(exc).__name__}); failing closed", **base)


def _review_and_place(led, it, cfg, client, symbol, amount, run_date, key, now) -> Dict[str, Any]:
    base = {"idempotency_key": key}
    acct = cfg["account_number"]
    run_id = str(it.get("run_id") or "")
    plan = led.data["runs"].get(run_id) if run_id else None
    if not plan or plan.get("run_date") != run_date or (plan.get("legs") or {}).get(symbol) != f"{amount:.2f}":
        return _result(S_UNPLANNED, "order is not a leg of today's recorded run plan (planner is mandatory)", **base)
    claim = led.day_claim(run_date) if cfg["one_run_per_day"] else None
    if claim and claim.get("claimed_by") != run_id:
        return _result(S_DAY_DONE, f"buy run already done today ({claim.get('claimed_by')}: {claim.get('reason')})", **base)
    prev = led.get(key)
    if prev and prev.get("status") in _BLOCKING_LEDGER_STATES:
        return _result(
            S_DUP_KEY,
            f"idempotency key already {prev.get('status')} at {prev.get('updated_at')}"
            + (f" (order {prev.get('order_id')})" if prev.get("order_id") else ""),
            **base,
        )

    def rec(status, reason, **kw):
        return _record(led, key, run_id, run_date, symbol, amount, status, reason, now, **kw)

    if led.placed_total(run_date) + amount > cfg["max_daily"]:
        return rec(S_REVIEW_FAILED, f"daily cap: ${led.placed_total(run_date)} placed today + ${amount} > max_daily_notional_usd ${cfg['max_daily']}")

    client = client or resolve_client(cfg)
    if client is None:
        return rec(S_NO_CLIENT, f"no broker client ({CLIENT_ENV} unset and execution.broker_adapter.client empty)")

    order = {
        "account_number": acct,
        "symbol": symbol,
        "side": "buy",
        "type": cfg["order_type"],
        "dollar_amount": f"{amount:.2f}",
        "time_in_force": cfg["time_in_force"],
        "market_hours": "regular_hours",
        "extended_hours": False,
        "ref_id": ref_id_for(key),
    }

    try:
        acc = client.get_account(acct) or {}
        if str(acc.get("account_number") or "").strip() != acct:
            return rec(S_REVIEW_FAILED, "broker account mismatch (agentic only)")
        bp = _d(acc.get("buying_power"))
        if bp is None:
            return rec(S_REVIEW_FAILED, "buying power unknown")
        if amount > bp:
            return rec(S_REVIEW_FAILED, f"${amount} exceeds live buying power ${bp}")
        sc = settled_cash(acc)
        if sc is not None and amount > sc:
            return rec(S_REVIEW_FAILED, f"${amount} exceeds settled cash ${sc}")
        since = now - timedelta(hours=cfg["dedupe_window_hours"])
        orders = client.get_recent_orders(acct, since)
        if not isinstance(orders, list):
            return rec(S_REVIEW_FAILED, "broker orders unavailable")
        ext = _external_same_day(orders, run_date, led.run_order_ids(run_id))
        if ext and cfg["one_run_per_day"] and not (claim and claim.get("claimed_by") == run_id):
            led.claim_day(run_date, "external", ext, now)
            return rec(S_DAY_DONE, f"{ext}; one buy run per ET day")
        dup = _broker_duplicate(orders, symbol, amount)
        if dup:
            return rec(S_DUP_BROKER, dup)
        tr = client.get_tradability(symbol) or {}
        if tr.get("tradable") is not True:
            return rec(S_REVIEW_FAILED, f"{symbol} not tradable")
        if tr.get("fractional_tradable") is not True:  # every order here is a dollar (fractional) order
            return rec(S_REVIEW_FAILED, f"{symbol} not fractional/dollar tradable")
        rv = client.review_order(dict(order)) or {}
    except BrokerError as exc:
        return rec(S_REVIEW_FAILED, f"broker review error: {exc}"[:200])
    except Exception as exc:
        return rec(S_REVIEW_FAILED, f"broker review error {type(exc).__name__}")
    if rv.get("ok") is not True:
        return rec(S_REVIEW_FAILED, f"broker review rejected: {str(rv.get('reason') or 'no reason')[:160]} (not retried)")
    review = {"buying_power": str(bp), "warnings": [str(w)[:120] for w in (rv.get("warnings") or [])][:5]}
    rec("placing", "reviewed; placing", review=review)
    if cfg["one_run_per_day"]:
        led.claim_day(run_date, run_id, "adapter buy run", now)

    try:
        res = client.place_order(dict(order)) or {}
    except Exception as exc:
        msg = str(exc)[:160] if isinstance(exc, BrokerError) else type(exc).__name__
        return rec(S_PLACE_UNKNOWN, f"place call failed ({msg}); outcome unknown -- key stays blocked, check broker", review=review)
    state = gate._norm_token(res.get("state") or res.get("status") or "")
    oid = str(res.get("order_id") or res.get("id") or "") or None
    fill = {
        "filled_quantity": gate._num(res.get("quantity") or res.get("filled_quantity")),
        "fill_price": gate._num(res.get("average_price") or res.get("fill_price")),
    }
    mism = []
    if res.get("symbol") is not None and str(res.get("symbol")).strip().upper() != symbol:
        mism.append("symbol")
    if res.get("side") is not None and gate._norm_token(res.get("side")) != "buy":
        mism.append("side")
    ramt = _order_amount(res)
    if ramt is not None and abs(ramt - amount) > CENT:
        mism.append("amount")
    if mism:
        return rec(S_PLACE_UNKNOWN, f"broker response mismatch on {','.join(mism)} -- CHECK BROKER NOW", order_id=oid, review=review)
    if state in _DEAD_BROKER_STATES:
        return rec(S_BROKER_REJECTED, f"broker returned state {state} (not retried)", order_id=oid, review=review)
    if not oid:
        return rec(S_PLACE_UNKNOWN, "broker returned no order id; outcome unknown", review=review)
    return rec(S_PLACED, f"placed (broker state {state or '?'})", order_id=oid, review=review,
               broker_state=state or None, **fill)


def _record(led, key, run_id, run_date, symbol, amount, status, reason, now, *, order_id=None, review=None,
            broker_state=None, filled_quantity=None, fill_price=None) -> Dict[str, Any]:
    row = {
        "run_id": run_id,
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
    for k, v in (("review", review), ("order_id", order_id), ("broker_state", broker_state),
                 ("filled_quantity", filled_quantity), ("fill_price", fill_price)):
        if v is not None:
            row[k] = v
    led.prune(now)
    led.put(key, row)
    return _result(status, reason, idempotency_key=key, order_id=order_id, broker_state=broker_state,
                   filled_quantity=filled_quantity, fill_price=fill_price)


# --------------------------------------------------------------------------
# should-buy (bp-poll / daily trigger; no network)
# --------------------------------------------------------------------------


def should_buy(
    *, snapshot: Optional[Dict[str, Any]] = None, policy_path: Optional[Path] = None,
    ledger_path: Optional[Path] = None, now: Optional[datetime] = None,
) -> Tuple[bool, str]:
    """True only when a buy run may start: live + enabled, inside the window,
    no buy run yet this ET day, fresh snapshot, settled cash >= min_investable."""
    now = now or gate.test_now()
    cfg, err = load_adapter_config(policy_path)
    if cfg is None:
        return False, f"{S_CONFIG}: {err}"
    if not cfg["live"] or not cfg["enabled"]:
        return False, f"{S_LIVE_FALSE}: live/enabled not true"
    st, why = market_window(now, cfg)
    if st:
        return False, f"{st}: {why}"
    try:
        with BrokerLedger.transaction(ledger_path) as led:
            claim = led.day_claim(et_date(now)) if cfg["one_run_per_day"] else None
    except (OSError, ValueError) as exc:
        return False, f"{S_LEDGER}: {type(exc).__name__}"
    if claim:
        return False, f"{S_DAY_DONE}: {claim.get('claimed_by')} ({claim.get('reason')})"
    snapshot = snapshot if snapshot is not None else load_snapshot()
    ag = _snapshot_agentic(snapshot or {})
    age = _snapshot_age_hours(snapshot or {}, now) if ag else None
    if not ag or age is None or age > cfg["snapshot_max_age_hours"]:
        return False, f"{S_BOOK_UNKNOWN}: snapshot missing/stale"
    sc = settled_cash(ag)
    if sc is None or sc < cfg["min_investable"]:
        return False, f"{S_BELOW_MIN}: settled cash ${sc} < ${cfg['min_investable']}"
    return True, f"settled cash ${sc} >= ${cfg['min_investable']}; buy run allowed"


# --------------------------------------------------------------------------
# GrokMcpClient: the repo's existing Robinhood auth (grok + robinhood-trading MCP)
# --------------------------------------------------------------------------

RH = gate.RH_SERVER
_READ_ONLY_TOOLS = ("get_portfolio", "get_accounts", "get_equity_orders", "get_equity_tradability", "get_equity_quotes")
# grok headless envelope keys (grok >= 1.0.13 prints one pretty-printed JSON object).
_GROK_ENVELOPE_KEYS = frozenset({"text", "stopReason", "sessionId", "requestId", "usage", "num_turns", "modelUsage"})


def _last_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Last complete top-level JSON object in ``text`` (tolerates leading/trailing noise)."""
    dec = json.JSONDecoder()
    found: Optional[Dict[str, Any]] = None
    pos = 0
    while True:
        i = text.find("{", pos)
        if i < 0:
            return found
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            pos = i + 1
            continue
        if isinstance(obj, dict):
            found = obj
        pos = end


def parse_grok_output(stdout: Optional[str], *, require_tool_round: bool = False) -> Dict[str, Any]:
    """Structured result of one ``grok -p ... --json-schema`` run.

    1. Parse the WHOLE stdout as JSON (grok 1.0.13 prints a multi-line envelope);
       if that fails, fall back to the last complete JSON object (leading noise).
    2. Envelope => ``structuredOutput`` (grok 1.0.13), else ``structured_output``
       (older grok). An envelope without either is an error, never a guess.
    3. A bare object (no envelope keys) is the schema result itself.
    4. ``require_tool_round``: an envelope reporting ``num_turns`` < 2 means the
       model answered without any tool round trip (seen on prism 2026-10-25: an
       all-null get_portfolio answer in 1 turn). That is never trusted: an
       invented ``orders: []`` would bypass the same-day order guard.
    Raises BrokerError with a short, secret-free reason otherwise."""
    text = (stdout or "").strip()
    if not text:
        raise BrokerError("grok returned empty output")
    try:
        out: Any = json.loads(text)
    except ValueError:
        out = _last_json_object(text)
        if out is None:
            raise BrokerError("grok returned non-JSON (no complete JSON object in output)")
    if not isinstance(out, dict):
        raise BrokerError(f"grok returned JSON {type(out).__name__}, expected an object")
    for key in ("structuredOutput", "structured_output"):
        if key in out:
            so = out[key]
            if not isinstance(so, dict):
                raise BrokerError(f"grok {key} is {type(so).__name__}, expected an object")
            turns = gate._num(out.get("num_turns"))
            if require_tool_round and turns is not None and turns < 2:
                raise BrokerError(f"grok answered in {int(turns)} turn(s) without a tool call; result not trusted")
            return so
    if _GROK_ENVELOPE_KEYS & set(out):
        stop = str(out.get("stopReason") or "?")[:40]
        raise BrokerError(f"grok envelope has no structuredOutput/structured_output (stopReason={stop})")
    return out


def portfolio_account(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a get_portfolio result for ``RobinhoodClient.get_account``.
    ``settled_cash`` = min(explicit settled, cash, unleveraged_buying_power) over
    the values present (None if none): conservative, never leveraged BP."""
    acct = str(raw.get("account_number") or "").strip()
    bp = gate._num(raw.get("buying_power"))
    cash = gate._num(raw.get("cash"))
    ubp = gate._num(raw.get("unleveraged_buying_power"))
    explicit = gate._num(raw.get("settled_cash"))
    vals = [v for v in (explicit, cash, ubp) if v is not None]
    return {
        "account_number": acct,
        "buying_power": bp,
        "cash": cash,
        "unleveraged_buying_power": ubp,
        "settled_cash": max(0.0, min(vals)) if vals else None,
        "settled_cash_source": ("explicit" if explicit is not None else "min(cash, unleveraged_buying_power)") if vals else "unknown",
    }


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
    session logs + research notes: get_portfolio, get_accounts, get_equity_orders, get_equity_tradability, get_equity_quotes,
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
    def _extra_deny(tools: Tuple[str, ...] = ()) -> List[str]:
        args: List[str] = []
        extra = ["cancel_*", "review_option_order", "preview_crypto_order"]
        if "review_equity_order" not in tools:  # read calls: no order review either
            extra.append("review_equity_order")
        for t in extra:
            args += ["--deny", f"MCPTool({RH}__{t})"]
        return args

    def _call(self, instruction: str, schema: Dict[str, Any], tools: Tuple[str, ...], *, allow_place: bool = False) -> Dict[str, Any]:
        prompt = (
            "You are a deterministic broker shim. Call ONLY these robinhood-trading tools: "
            + ", ".join(tools)
            + ". You MAY first use the MCP tool-discovery helper (search_tool) to load those tools' schemas; "
            "that lookup is allowed and expected. Make no other tool calls, do not read or write files, do not "
            "place, modify or cancel any other order. You must actually call the tool(s) and copy values from "
            "their responses; never answer from memory. Return only JSON matching the schema.\n\n" + instruction
        )
        carve = ("place_equity_order",) if allow_place else ()
        cmd = [self.grok_bin, "-p", prompt, "--cwd", self.cwd, "--yolo", "--disable-web-search",
               "--json-schema", json.dumps(schema), *grok_deny_args(allow_tools=carve)]
        for t in tools:
            cmd += ["--allow", f"MCPTool({RH}__{t})"]
        cmd += self._extra_deny(tools)
        try:
            r = self.runner(cmd, capture_output=True, text=True, timeout=self.timeout_s, check=False)
        except subprocess.TimeoutExpired as exc:
            raise BrokerError("grok timeout") from exc
        except OSError as exc:
            raise BrokerError(f"grok unavailable ({type(exc).__name__})") from exc
        if r.returncode != 0:
            raise BrokerError(f"grok exit {r.returncode}")
        return parse_grok_output(r.stdout, require_tool_round=True)

    def get_account(self, account_number: str) -> Dict[str, Any]:
        """Buying power / cash from ``get_portfolio`` (``get_accounts`` carries no
        reliable buying power; its tool description routes that to get_portfolio).
        ``get_accounts`` is allowed only to discover/confirm the account.

        Fields (prism grok 1.0.13, agentic 674601752): ``buying_power``, ``cash``,
        ``unleveraged_buying_power``; no explicit settled field. ``settled_cash``
        is conservative: the min of every value present among an explicit
        settled/withdrawable figure, ``cash`` and ``unleveraged_buying_power``
        (never derived from leveraged buying power); null when none is present.
        The adapter still rejects any amount above live ``buying_power``."""
        raw = self._call(
            f"Call get_portfolio for account {account_number} (call get_accounts first only if you need it to "
            f"find or confirm that account). Return account_number exactly as the broker reports it (\"\" if "
            "account not found) and, as plain numbers copied from get_portfolio, buying_power, cash and "
            "unleveraged_buying_power. Set settled_cash ONLY if the tool returns an explicit settled or "
            "withdrawable cash field (e.g. cash_available_for_withdrawal / withdrawable_cash); otherwise null. "
            "Never estimate or derive a number; use null for anything absent.",
            {"type": "object", "required": ["account_number", "buying_power", "cash", "unleveraged_buying_power", "settled_cash"],
             "properties": {"account_number": {"type": "string"}, "buying_power": {"type": ["number", "null"]},
                            "cash": {"type": ["number", "null"]},
                            "unleveraged_buying_power": {"type": ["number", "null"]},
                            "settled_cash": {"type": ["number", "null"]}}},
            ("get_portfolio", "get_accounts"),
        )
        return portfolio_account(raw)

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

    def get_quotes(self, symbols: List[str]) -> Dict[str, float]:
        out = self._call(
            "Call get_equity_quotes for these symbols: " + ", ".join(symbols)
            + ". Return each symbol's last trade price as a number (read-only; place nothing).",
            {"type": "object", "required": ["quotes"], "properties": {"quotes": {"type": "array", "items": {
                "type": "object", "properties": {"symbol": {"type": "string"}, "price": {"type": ["number", "null"]}}}}}},
            ("get_equity_quotes",),
        )
        return {str(q.get("symbol") or "").upper(): q.get("price") for q in (out.get("quotes") or []) if isinstance(q, dict)}

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
            + json.dumps(order) + ". Return the created order's order_id, state, symbol, side, dollar_amount, and quantity/average_price if filled.",
            {"type": "object", "required": ["order_id", "state"], "properties": {
                "order_id": {"type": "string"}, "state": {"type": "string"}, "symbol": {"type": "string"},
                "side": {"type": "string"}, "dollar_amount": {"type": ["number", "null"]},
                "quantity": {"type": ["number", "null"]}, "average_price": {"type": ["number", "null"]}}},
            ("place_equity_order",), allow_place=True,
        )


# The gate runs the planner before gating when the adapter exposes it.
submit.plan = plan_intents  # type: ignore[attr-defined]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="fund-manager broker adapter (no network)")
    ap.add_argument("--should-buy", action="store_true",
                    help="exit 0 if a buy run may start now (settled cash >= min, once per ET day), else 1")
    args = ap.parse_args(argv)
    if args.should_buy:
        ok, why = should_buy()
        print(json.dumps({"should_buy": ok, "reason": why}))
        return 0 if ok else 1
    cfg, err = load_adapter_config()
    print(json.dumps({"adapter": ADAPTER_SPEC, "config_ok": cfg is not None, "error": err,
                      "live": (cfg or {}).get("live"), "enabled": (cfg or {}).get("enabled"),
                      "allowlist": list((cfg or {}).get("allowlist") or ())}, indent=2))
    return 0 if cfg is not None else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
