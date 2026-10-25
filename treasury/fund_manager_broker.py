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
  (``weights_repaired``). The LLM only chooses how the floating 35% splits among
  the floating names (default: all IBIT). Legs > $75 spill into IBIT; legs < $1
  fold into IBIT.

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
# plan statuses
P_PLANNED = "planned"
P_REPAIRED = "weights_repaired"
P_NO_BUYS = "no_buy_intents"
TERMINAL_STATUSES = frozenset(
    {
        S_PLACED, S_NOT_GATED, S_NON_BUY, S_DISABLED, S_LIVE_FALSE, S_CONFIG, S_NO_CLIENT,
        S_REVIEW_FAILED, S_BROKER_REJECTED, S_DUP_KEY, S_DUP_BROKER, S_LEDGER, S_PLACE_UNKNOWN,
        S_UNPLANNED, S_BEFORE_WINDOW, S_OUTSIDE_HOURS, S_DAY_DONE, S_SLEEVE_GATE, S_BELOW_MIN,
        S_BOOK_UNKNOWN,
    }
)
PLAN_SKIP_STATUSES = frozenset({S_CONFIG, S_BEFORE_WINDOW, S_OUTSIDE_HOURS, S_DAY_DONE, S_BELOW_MIN, S_BOOK_UNKNOWN, S_LEDGER})
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
        "holidays": frozenset(str(x) for x in (ba.get("market_holidays") or [])),
        "early_close": {str(k): v for k, v in (ba.get("early_close_days") or {}).items()},
        "dedupe_window_hours": float(gate._num(ba.get("dedupe_window_hours")) or 24.0),
        "snapshot_max_age_hours": float(gate._num(ba.get("snapshot_max_age_hours")) or 6.0),
        "tol_usd": _d(ba.get("weight_tolerance_usd")) or Decimal("1"),
        "tol_pct": _d(ba.get("weight_tolerance_pct")) or Decimal("0.05"),
        "fold_below": _d(ba.get("fold_below_usd")) or Decimal("1"),
        "fold_into": fold_into,
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
    if et.weekday() >= 5:
        return S_OUTSIDE_HOURS, f"weekend ({day} ET); no queuing"
    if day in cfg["holidays"]:
        return S_OUTSIDE_HOURS, f"market holiday {day}; no queuing"
    latest = cfg["latest"]
    if day in cfg["early_close"]:
        close = _hhmm(cfg["early_close"][day], "13:00") or dtime(13, 0)
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
        data = json.loads(Path(path or gate.RH_SNAPSHOT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def book_values(snapshot: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Decimal]:
    """Invested market value by sleeve + per stock symbol (last price if the
    snapshot has one, else cost basis -- same rule as fund_manager.analyze)."""
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
            for k in ("last_price", "price", "mark_price", "average_buy_price"):
                px = _d(pos.get(k))
                if px is not None and px > 0:
                    break
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
) -> Tuple[Any, Dict[str, Any]]:
    """Rebuild the LLM's buy intents into the deterministic run plan.

    Returns (intents for the gate, plan info). Non-buy intents are passed
    through untouched so the gate still blocks/logs/alerts them. Never raises:
    any planner error drops every buy (fail closed)."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(intents, list):
        return intents, {"status": "not_planned", "reason": "intents payload is not a list"}
    buys = [it for it in intents if isinstance(it, dict) and gate.classify_side(it)[0] == "buy"]
    others = [it for it in intents if not any(it is b for b in buys)]
    try:
        orders, info = _plan(buys, snapshot, policy_path, ledger_path, now, persist)
    except Exception as exc:
        orders, info = [], {"status": "plan_error", "reason": f"planner error {type(exc).__name__}; no buys"}
    _log(f"plan {info.get('status')}: {info.get('reason') or info.get('summary') or ''}")
    return others + orders, info


def _plan(buys, snapshot, policy_path, ledger_path, now, persist):
    run_date = et_date(now)
    info: Dict[str, Any] = {"run_date": run_date, "dropped": [], "notes": []}

    def done(status: str, reason: str):
        info.update(status=status, reason=reason)
        return [], info

    cfg, err = load_adapter_config(policy_path)
    if cfg is None:
        return done(S_CONFIG, err)
    if not buys:
        return done(P_NO_BUYS, "no buy intents")
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
        investable = _cents(min(settled, cfg["max_run"], daily_left))
        run_total = _cents(min(prop_total, investable))
        info.update(settled_cash=float(settled), investable_usd=float(investable),
                    proposed_usd=float(prop_total), run_total_usd=float(run_total))
        if investable < cfg["min_investable"] or run_total < cfg["min_investable"]:
            return done(S_BELOW_MIN, f"investable ${investable} / run ${run_total} below ${cfg['min_investable']} minimum")

        bv = book_values(snapshot, cfg)
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
        targets = {s: btc_amt * w for s, w in cfg["fixed_weights"].items()}
        prop_btc = sum((v for s, v in proposed.items() if s not in stocks), ZERO)
        within = abs(prop_btc - btc_amt) <= tol and all(abs(proposed.get(s, ZERO) - t) <= tol for s, t in targets.items())
        if within:
            fixed = {s: min(proposed.get(s, ZERO), btc_amt) for s in targets}
        else:
            fixed = dict(targets)
            for s, t in targets.items():
                if abs(proposed.get(s, ZERO) - t) > tol:
                    repaired.append(f"{s} ${proposed.get(s, ZERO):.2f} -> ${t:.2f} (fixed weight)")
        floating_amt = max(ZERO, btc_amt - sum(fixed.values(), ZERO))
        ai_float = {s: proposed[s] for s in cfg["floating"] if proposed.get(s)}
        ai_float_total = sum(ai_float.values(), ZERO)
        legs.update(fixed)
        if floating_amt > 0:
            if ai_float_total > 0:
                for s, v in ai_float.items():
                    legs[s] = legs.get(s, ZERO) + floating_amt * v / ai_float_total
            else:
                legs[cfg["fold_into"]] = legs.get(cfg["fold_into"], ZERO) + floating_amt
                info["notes"].append(f"no floating split proposed: floating ${floating_amt:.2f} -> {cfg['fold_into']}")
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
        info["summary"] = f"{status} run {run_id}: " + ", ".join(f"{s} ${v:.2f}" for s, v in legs.items())
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
    now = now or datetime.now(timezone.utc)
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
            "buying_power, cash and settled (withdrawable) cash as numbers. If not found return account_number \"\".",
            {"type": "object", "required": ["account_number", "buying_power"],
             "properties": {"account_number": {"type": "string"}, "buying_power": {"type": ["number", "null"]},
                            "cash": {"type": ["number", "null"]},
                            "settled_cash": {"type": ["number", "null"]}}},
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
