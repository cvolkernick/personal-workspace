#!/usr/bin/env python3
"""Deterministic order gate for the agentic fund manager (#1093).

Chairman standing order (2026-09-22/23): the stocks book is held and the 40%
BTC / digital-credit target is restored with **new cash only**. The fund
manager must never produce or execute a SELL to rebalance.

This module is the single choke point between "an analysis / LLM proposal"
and "an order":

* ``evaluate_intent`` / ``gate_orders``  -- pure verdicts, fail closed.
* ``execute_intents``                    -- the ONLY order-submit path. A broker
  adapter is called only with a ``GatedOrder`` that this module minted.
* ``guard_decision_entry``               -- decision-log level check used by
  ``fund_manager.append_decision`` (detects/marks sells in any record).
* ``grok_deny_args``                     -- permission deny rules the shell
  runners pass to ``grok --yolo`` so the LLM cannot place orders around us.

Fail closed means: an unreadable/disabled policy, a malformed intent, an
unknown side, an unknown purpose, an unknown amount or an unknown cash balance
blocks the order. Nothing here can be loosened by config: if
``guardrails.no_sell_to_rebalance`` is not literally ``true`` the gate blocks
EVERYTHING (it does not allow sells).

No network and no broker call happens in this module on its own. Alerts and
decision logging are lazy-imported from ``treasury.fund_manager``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

POLICY_PATH = ROOT / "investment" / "fund_manager.json"
OVERRIDE_PATH = ROOT / "investment" / "fund_manager_sell_override.json"
INTENTS_PATH = ROOT / "treasury" / "snapshots" / "fund_manager_intents.json"
RH_SNAPSHOT_PATH = ROOT / "treasury" / "snapshots" / "robinhood_latest.json"

BLOCK_STATUS = "blocked_no_sell_guard"
EXECUTED_STATUSES = frozenset(
    {
        "filled",
        "partially_filled",
        "placed",
        "submitted",
        "executed",
        "confirmed",
        "queued",
        "open",
        "pending",
        "accepted",
        "sent",
    }
)
MAX_OVERRIDE_HOURS = 24
BROKER_ADAPTER_ENV = "FM_BROKER_ADAPTER"  # "pkg.module:callable"; none = place nothing

# Reason codes (stable; tests and #701 comments key on them).
R_POLICY = "policy_unavailable"
R_BAD_INTENT = "malformed_intent"
R_UNCLASSIFIABLE = "unclassifiable_side"
R_REBALANCE_SELL = "rebalance_sell"
R_UNCLASSIFIED_SELL = "sell_purpose_unclassified"
R_SELL_NO_OVERRIDE = "sell_without_valid_override"
R_SHORT = "short_or_option_write"
R_OPTION = "option_order"
R_CRYPTO_SELL = "crypto_sell"
R_BLOCKED_SYMBOL = "blocked_symbol"
R_BAD_AMOUNT = "invalid_or_unknown_amount"
R_CASH_UNKNOWN = "deployable_cash_unknown"
R_OVER_CASH = "over_deployable_cash"

# A sell is only ever *eligible* for an override when its purpose is one of
# these explicit, non-rebalance purposes. Anything else is blocked.
NON_REBALANCE_PURPOSES = frozenset(
    {"risk_exit", "stop_loss", "tax", "withdrawal", "chairman_manual"}
)
_REBALANCE_RE = re.compile(
    r"(re-?balanc|rotat|\btrim|sell[ _-]?to[ _-]?(buy|fund)|reallocat|raise[ _-]?cash|"
    r"free[ _-]?(up[ _-]?)?cash|\btidy|\bdrift|toward[s]? (the )?(40|60)|"
    r"fund (the )?buy|exit[ _-]?(other|non))",
    re.IGNORECASE,
)
_SIDE_KEYS = ("side", "order_side", "action", "direction", "transaction_type")
_AMOUNT_KEYS = ("notional_usd", "amount_usd", "dollar_amount", "notional")
_TEXT_KEYS = ("purpose", "reason", "rationale", "note", "notes", "summary", "kind")
_OPTION_KEYS = ("strike", "strike_price", "expiration", "expiration_date", "legs", "contract", "option", "option_type")
_BUY = {"buy", "bid", "long", "buy_to_open"}
_SELL_WORDS = ("sell", "short", "trim", "reduce", "exit", "close", "liquidat", "write", "dump", "rotate", "unwind")


# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------


@dataclass
class Verdict:
    allowed: bool
    code: str  # "ok" or a reason code
    reason: str
    side: str = "unknown"  # buy | sell | short | unknown
    override_used: bool = False
    status: str = ""

    def __post_init__(self) -> None:
        if not self.status:
            if self.allowed:
                self.status = "gate_approved"
            else:
                # sells / shorts / unclassifiable => the no-sell status; a plain
                # buy rejected for cap/amount/symbol reasons is not a sell block.
                self.status = BLOCK_STATUS if self.side != "buy" else "blocked_order_gate"


_MINT = object()


class GatedOrder:
    """An order the gate approved. Only ``_mint_gated`` creates one."""

    __slots__ = ("intent", "verdict")

    def __init__(self, intent: Dict[str, Any], verdict: Verdict, _token: object = None):
        if _token is not _MINT:
            raise TypeError("GatedOrder can only be created by the order gate")
        self.intent = dict(intent)
        self.verdict = verdict

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.intent)


def _mint_gated(intent: Dict[str, Any], verdict: Verdict) -> GatedOrder:
    return GatedOrder(intent, verdict, _MINT)


@dataclass
class GatePolicy:
    ok: bool
    error: str = ""
    allowed_sides: Tuple[str, ...] = ()
    blocked_symbols: Tuple[str, ...] = ()
    live: bool = False
    min_trade_usd: float = 1.0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool) or val == "":
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _norm_token(val: Any) -> str:
    return re.sub(r"[\s\-]+", "_", str(val).strip().lower())


def _parse_ts(val: Any) -> Optional[datetime]:
    if not isinstance(val, str) or not val.strip():
        return None
    s = val.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:  # naive = ambiguous = malformed
        return None
    return dt.astimezone(timezone.utc)


def load_gate_policy(path: Optional[Path] = None) -> GatePolicy:
    """Read ``guardrails`` from fund_manager.json. Any problem => ok=False."""
    p = path or POLICY_PATH
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return GatePolicy(False, f"cannot load policy: {type(exc).__name__}")
    if not isinstance(data, dict):
        return GatePolicy(False, "policy is not a JSON object")
    g = data.get("guardrails")
    if not isinstance(g, dict):
        return GatePolicy(False, "guardrails block missing")
    if g.get("no_sell_to_rebalance") is not True:
        return GatePolicy(
            False, "guardrails.no_sell_to_rebalance must be true (gate refuses to run otherwise)"
        )
    sides = g.get("allowed_sides")
    if not isinstance(sides, list) or not all(isinstance(s, str) for s in sides):
        return GatePolicy(False, "guardrails.allowed_sides missing or malformed")
    # Config can only NARROW the gate. Sell/short can never be added here.
    allowed = tuple(sorted({_norm_token(s) for s in sides} & {"buy"}))
    blocked = g.get("blocked_symbols") or []
    if not isinstance(blocked, list):
        return GatePolicy(False, "guardrails.blocked_symbols malformed")
    limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
    min_trade = _num(limits.get("min_trade_notional_usd"))
    return GatePolicy(
        True,
        "",
        allowed,
        tuple(str(s).strip().upper() for s in blocked),
        live=data.get("live") is True,
        min_trade_usd=min_trade if min_trade and min_trade > 0 else 1.0,
    )


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def classify_side(intent: Any) -> Tuple[str, str]:
    """Return (side, detail); side in buy | sell | short | unknown. Fail closed.

    * every side-like field present must agree; a mix of buy and sell is unknown
    * anything that is not exactly a buy token and not recognisably a sell/short
      word is unknown (also blocked)
    """
    if not isinstance(intent, dict):
        return "unknown", "intent is not an object"
    tokens = [_norm_token(intent[k]) for k in _SIDE_KEYS if intent.get(k) not in (None, "")]
    if not tokens:
        return "unknown", "no side field"
    kinds = set()
    for t in tokens:
        if t in _BUY:
            kinds.add("buy")
        elif "short" in t or "write" in t or t in ("sell_to_open",):
            kinds.add("short")
        elif any(w in t for w in _SELL_WORDS):
            kinds.add("sell")
        else:
            kinds.add("unknown")
    if kinds == {"buy"}:
        return "buy", ""
    if "unknown" in kinds or ("buy" in kinds and len(kinds) > 1):
        return "unknown", f"conflicting or unrecognised side {sorted(set(tokens))}"
    return ("short" if "short" in kinds else "sell"), ""


def _is_option(intent: Dict[str, Any]) -> bool:
    for k in ("asset_class", "instrument", "instrument_type", "security_type", "type"):
        if "option" in _norm_token(intent.get(k, "")):
            return True
    return any(intent.get(k) not in (None, "", [], {}) for k in _OPTION_KEYS)


def _is_crypto(intent: Dict[str, Any]) -> bool:
    for k in ("asset_class", "instrument", "instrument_type", "security_type"):
        if "crypto" in _norm_token(intent.get(k, "")):
            return True
    return False


def classify_purpose(intent: Dict[str, Any]) -> str:
    """rebalance | <non-rebalance purpose> | unclassified.

    Any rebalance-ish wording anywhere in the free text wins over an explicit
    purpose (conservative: ``purpose=risk_exit`` + "to rebalance" => rebalance).
    """
    for k in _TEXT_KEYS:
        v = intent.get(k)
        if isinstance(v, str) and _REBALANCE_RE.search(v):
            return "rebalance"
    purpose = _norm_token(intent.get("purpose", "")) if intent.get("purpose") else ""
    if purpose in NON_REBALANCE_PURPOSES:
        return purpose
    return "unclassified"


def intent_symbol(intent: Dict[str, Any]) -> str:
    return str(intent.get("symbol") or intent.get("ticker") or "").strip().upper()


def intent_qty(intent: Dict[str, Any]) -> Optional[float]:
    for k in ("quantity", "qty", "shares"):
        n = _num(intent.get(k))
        if n is not None:
            return n
    return None


def intent_notional(intent: Dict[str, Any]) -> Optional[float]:
    for k in _AMOUNT_KEYS:
        n = _num(intent.get(k))
        if n is not None:
            return n
    qty = intent_qty(intent)
    px = None
    for k in ("limit_price", "price"):
        px = _num(intent.get(k))
        if px is not None:
            break
    if qty is not None and px is not None:
        return qty * px
    return None


# --------------------------------------------------------------------------
# Override (explicit Chris approval; <= 24h)
# --------------------------------------------------------------------------


def load_override(path: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """(override, error). Missing file => (None, "") i.e. simply no override."""
    p = Path(path or OVERRIDE_PATH)
    if not p.is_file():
        return None, ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "override file unreadable/malformed"
    if not isinstance(data, dict):
        return None, "override is not a JSON object"
    return data, ""


def validate_override(
    override: Any, *, symbol: str, qty: Optional[float], notional: Optional[float], now: datetime
) -> Tuple[bool, str]:
    """Is this override valid, unexpired, and does it cover THIS sell?"""
    if not isinstance(override, dict):
        return False, "no override"
    if str(override.get("approver") or "").strip().lower() != "chris":
        return False, "override approver is not Chris"
    reason = override.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return False, "override has no reason"
    syms = override.get("symbols")
    if not isinstance(syms, list) or not syms or not all(isinstance(s, str) and s.strip() for s in syms):
        return False, "override symbols missing/malformed"
    exp = _parse_ts(override.get("expires_at"))
    if exp is None:
        return False, "override expires_at missing/malformed (timezone required)"
    if exp <= now:
        return False, "override expired"
    if exp > now + timedelta(hours=MAX_OVERRIDE_HOURS):
        return False, f"override expires more than {MAX_OVERRIDE_HOURS}h out"
    if override.get("approved_at") is not None:
        appr = _parse_ts(override.get("approved_at"))
        if appr is None or appr > now or exp - appr > timedelta(hours=MAX_OVERRIDE_HOURS):
            return False, "override approved_at invalid or window > 24h"
    max_qty = _num(override.get("max_qty"))
    max_notional = _num(override.get("max_notional_usd"))
    if (max_qty is None or max_qty <= 0) and (max_notional is None or max_notional <= 0):
        return False, "override needs a positive max_qty or max_notional_usd"
    if symbol not in {s.strip().upper() for s in syms}:
        return False, "symbol not covered by override"
    if max_qty is not None and max_qty > 0:
        if qty is None or qty <= 0 or qty > max_qty + 1e-12:
            return False, "qty missing or above override max_qty"
    if max_notional is not None and max_notional > 0:
        if notional is None or notional <= 0 or notional > max_notional + 1e-9:
            return False, "notional missing or above override max_notional_usd"
    return True, "override ok"


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------


def _block(code: str, reason: str, side: str = "unknown") -> Verdict:
    return Verdict(False, code, reason, side=side)


def evaluate_sell(
    intent: Dict[str, Any],
    side: str,
    *,
    override: Any = None,
    now: Optional[datetime] = None,
) -> Verdict:
    """Verdict for something already classified as sell/short."""
    now = now or _now()
    sym = intent_symbol(intent)
    if side == "short" or intent.get("short") is True:
        return _block(R_SHORT, f"short/option-write {sym or '?'} is never allowed", "short")
    if _is_option(intent):
        return _block(R_OPTION, f"option order {sym or '?'} is never allowed", "sell")
    if _is_crypto(intent):
        return _block(R_CRYPTO_SELL, f"crypto sell {sym or '?'} is never allowed", "sell")
    purpose = classify_purpose(intent)
    if purpose == "rebalance":
        return _block(
            R_REBALANCE_SELL,
            f"sell {sym or '?'} to rebalance is forbidden (no-sell-to-rebalance; "
            "rebalance with new cash only). Not overridable.",
            "sell",
        )
    if purpose == "unclassified":
        return _block(
            R_UNCLASSIFIED_SELL,
            f"sell {sym or '?'} has no explicit non-rebalance purpose "
            f"({', '.join(sorted(NON_REBALANCE_PURPOSES))}); failing closed",
            "sell",
        )
    if not sym:
        return _block(R_BAD_INTENT, "sell without a symbol", "sell")
    ok, why = validate_override(
        override, symbol=sym, qty=intent_qty(intent), notional=intent_notional(intent), now=now
    )
    if not ok:
        return _block(R_SELL_NO_OVERRIDE, f"sell {sym} blocked: {why}", "sell")
    return Verdict(True, "ok", f"sell {sym} allowed by explicit Chris override ({purpose})", "sell", True)


def evaluate_intent(
    intent: Any,
    *,
    policy: Optional[GatePolicy] = None,
    override: Any = None,
    now: Optional[datetime] = None,
    deployable_cash: Optional[float] = None,
    committed_buy_usd: float = 0.0,
) -> Verdict:
    """Single-order verdict. Never raises: any error is a block."""
    try:
        return _evaluate_intent(intent, policy, override, now, deployable_cash, committed_buy_usd)
    except Exception as exc:  # fail closed
        return _block(R_BAD_INTENT, f"gate error {type(exc).__name__}: failing closed")


def _evaluate_intent(intent, policy, override, now, deployable_cash, committed_buy_usd) -> Verdict:
    policy = policy or load_gate_policy()
    if not policy.ok:
        return _block(R_POLICY, f"gate policy unavailable ({policy.error}); placing nothing")
    if not isinstance(intent, dict):
        return _block(R_BAD_INTENT, "intent is not an object")
    side, detail = classify_side(intent)
    if side == "unknown":
        return _block(R_UNCLASSIFIABLE, f"cannot classify order side ({detail}); failing closed")
    if side in ("sell", "short"):
        return evaluate_sell(intent, side, override=override, now=now)
    # ---- buys ----
    if "buy" not in policy.allowed_sides:
        return _block(R_POLICY, "policy allows no sides; placing nothing", "buy")
    if _is_option(intent):
        return _block(R_OPTION, "option order is never allowed", "buy")
    sym = intent_symbol(intent)
    if not sym:
        return _block(R_BAD_INTENT, "buy without a symbol", "buy")
    if sym in policy.blocked_symbols:
        return _block(R_BLOCKED_SYMBOL, f"{sym} is in guardrails.blocked_symbols", "buy")
    amt = intent_notional(intent)
    if amt is None or amt <= 0:
        return _block(R_BAD_AMOUNT, f"buy {sym} has no positive known notional", "buy")
    if deployable_cash is None:
        return _block(R_CASH_UNKNOWN, "deployable cash unknown; buys fail closed", "buy")
    if committed_buy_usd + amt > deployable_cash + 1e-9:
        return _block(
            R_OVER_CASH,
            f"buy {sym} ${amt:.2f} would exceed deployable cash (no margin, no sell-to-fund)",
            "buy",
        )
    return Verdict(True, "ok", f"buy {sym} ${amt:.2f} within deployable cash", "buy")


@dataclass
class BatchResult:
    allowed: List[GatedOrder] = field(default_factory=list)
    blocked: List[Tuple[Any, Verdict]] = field(default_factory=list)

    @property
    def blocked_sells(self) -> List[Tuple[Any, Verdict]]:
        return [(i, v) for i, v in self.blocked if v.side in ("sell", "short") or v.code in (R_UNCLASSIFIABLE,)]


def gate_orders(
    intents: Any,
    *,
    deployable_cash: Optional[float],
    policy: Optional[GatePolicy] = None,
    override: Any = None,
    now: Optional[datetime] = None,
) -> BatchResult:
    """Gate a batch. Sum of allowed buys is capped at deployable cash."""
    res = BatchResult()
    policy = policy or load_gate_policy()
    if not isinstance(intents, list):
        res.blocked.append((intents, _block(R_BAD_INTENT, "intents payload is not a list")))
        return res
    committed = 0.0
    sell_budget: Dict[str, Dict[str, float]] = {}
    for it in intents:
        v = evaluate_intent(
            it, policy=policy, override=override, now=now,
            deployable_cash=deployable_cash, committed_buy_usd=committed,
        )
        if v.allowed and v.side == "buy":
            committed += intent_notional(it) or 0.0
        if v.allowed and v.side == "sell":
            # override caps are per batch, not per order
            sym = intent_symbol(it)
            b = sell_budget.setdefault(sym, {"qty": 0.0, "usd": 0.0})
            b["qty"] += intent_qty(it) or 0.0
            b["usd"] += intent_notional(it) or 0.0
            ok, why = validate_override(
                override, symbol=sym, qty=b["qty"] or None, notional=b["usd"] or None,
                now=now or _now(),
            )
            if not ok:
                v = _block(R_SELL_NO_OVERRIDE, f"sell {sym} blocked: batch total exceeds override ({why})", "sell")
        if v.allowed:
            res.allowed.append(_mint_gated(it, v))
        else:
            res.blocked.append((it, v))
    return res


# --------------------------------------------------------------------------
# Execution (the only submit path)
# --------------------------------------------------------------------------

Submit = Callable[[GatedOrder], Dict[str, Any]]


def load_broker_adapter() -> Optional[Submit]:
    """Optional ``module:callable`` from FM_BROKER_ADAPTER. None => place nothing."""
    spec = (os.environ.get(BROKER_ADAPTER_ENV) or "").strip()
    if not spec or ":" not in spec:
        return None
    mod, _, fn = spec.partition(":")
    try:
        return getattr(importlib.import_module(mod), fn)
    except Exception:
        return None


def _action_row(intent: Any, verdict: Verdict, status: str) -> Dict[str, Any]:
    it = intent if isinstance(intent, dict) else {"raw": str(intent)[:80]}
    row: Dict[str, Any] = {
        "symbol": intent_symbol(it) or "?",
        "side": str(it.get("side") or it.get("action") or "?"),
        "status": status,
        "reason_code": verdict.code,
        "reason": verdict.reason,
    }
    n = intent_notional(it)
    q = intent_qty(it)
    if n is not None:
        row["notional_usd"] = round(n, 4)
    if q is not None:
        row["quantity"] = q
    if it.get("purpose"):
        row["purpose"] = str(it.get("purpose"))[:60]
    return row


def execute_intents(
    intents: Any,
    *,
    deployable_cash: Optional[float],
    submit: Optional[Submit] = None,
    policy: Optional[GatePolicy] = None,
    override: Any = None,
    now: Optional[datetime] = None,
    dry_run: Optional[bool] = None,
    record: bool = True,
    record_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> Dict[str, Any]:
    """Gate, log, and (only for approved orders) submit.

    ``submit`` is never called with anything but a ``GatedOrder``. With
    ``live:false`` in policy (or ``dry_run``) nothing is submitted; approved
    orders are reported as ``would_place``. No adapter => nothing submitted.
    """
    policy = policy or load_gate_policy()
    res = gate_orders(intents, deployable_cash=deployable_cash, policy=policy, override=override, now=now)
    live = bool(policy.ok and policy.live)
    if dry_run is None:
        dry_run = not live
    out: Dict[str, Any] = {
        "live": live,
        "dry_run": bool(dry_run),
        "placed": [],
        "would_place": [],
        "blocked": [],
        "broker_calls": 0,
    }
    actions: List[Dict[str, Any]] = []
    for gated in res.allowed:
        if dry_run:
            status = "would_place"
        elif submit is None:
            status = "not_submitted_no_broker_adapter"
        else:
            try:
                result = submit(gated)
                status = str((result or {}).get("status") or "submitted")
                out["broker_calls"] += 1
            except Exception as exc:
                status = f"submit_error_{type(exc).__name__}"
        row = _action_row(gated.intent, gated.verdict, status)
        actions.append(row)
        out["would_place" if status == "would_place" else "placed"].append(row)
    for it, v in res.blocked:
        row = _action_row(it, v, v.status)
        actions.append(row)
        out["blocked"].append(row)
        _log_blocked_line(row)
    if record and res.blocked:
        any_sell = bool(res.blocked_sells)
        entry = {
            "kind": "blocked_sell" if any_sell else "blocked_order",
            "outcome": "blocked",
            "path": "order_gate",
            "need_llm": False,
            "summary": (
                f"Order gate blocked {len(res.blocked)} order(s): "
                + "; ".join(f"{r['symbol']} {r['side']} [{r['reason_code']}]" for r in out["blocked"])
            ),
            "rationale": {
                "why_now": "Fund-manager intents passed through the no-sell-to-rebalance order gate (#1093).",
                "why_not_alternatives": "Rebalance with new cash only; sells need an explicit unexpired Chris override.",
                "thesis_tags": ["no_sell_guard", "order_gate"],
            },
            "actions": actions,
            "blocked_sell": any_sell,
        }
        try:
            (record_fn or _default_record)(entry)
        except Exception as exc:  # logging must never turn into an order
            out["record_error"] = type(exc).__name__
    return out


def _log_blocked_line(row: Dict[str, Any]) -> None:
    print(
        f"[fund-manager order gate] {BLOCK_STATUS}: {row.get('side')} {row.get('symbol')} "
        f"[{row.get('reason_code')}] {row.get('reason')}",
        file=sys.stderr,
    )


def _default_record(entry: Dict[str, Any]) -> Any:
    from treasury.fund_manager import append_decision

    return append_decision(entry)


# --------------------------------------------------------------------------
# Decision-log level guard (used by fund_manager.append_decision)
# --------------------------------------------------------------------------

_QUIET_STATUS = frozenset({"", "none", "hold", "skipped", "not_sent", "no_action", "cancelled", "canceled"})


def guard_decision_entry(
    entry: Dict[str, Any], *, override: Any = None, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Return a copy of a decision record with sell-type actions marked/blocked.

    Detective for anything already executed (``guard_violation``), preventive
    marking for proposals. Cancels and non-order rows are left alone. Never
    raises; on internal error the record gains ``guard_error`` and is kept.
    """
    out = dict(entry)
    try:
        violations: List[Dict[str, Any]] = []
        new_actions: List[Any] = []
        for a in list(entry.get("actions") or []):
            if not isinstance(a, dict):
                new_actions.append(a)
                continue
            kind_tok = _norm_token(a.get("action") or a.get("type") or "")
            status = _norm_token(a.get("status") or "")
            if kind_tok.startswith("cancel") or status in ("cancelled", "canceled"):
                new_actions.append(a)
                continue
            side, detail = classify_side(a)
            if side == "buy":
                new_actions.append(a)
                continue
            if side == "unknown" and not any(a.get(k) for k in _SIDE_KEYS) and status in _QUIET_STATUS:
                new_actions.append(a)
                continue
            verdict = (
                evaluate_sell(a, side, override=override, now=now)
                if side in ("sell", "short")
                else _block(R_UNCLASSIFIABLE, f"cannot classify order side ({detail})")
            )
            if verdict.allowed:
                new_actions.append(a)
                continue
            b = dict(a)
            executed = status in EXECUTED_STATUSES
            b["guard_prev_status"] = a.get("status")
            b["status"] = BLOCK_STATUS if not executed else a.get("status")
            b["reason_code"] = verdict.code
            b["reason"] = verdict.reason
            if executed:
                b["guard_violation"] = True
            violations.append({"symbol": intent_symbol(a) or "?", "code": verdict.code, "executed": executed})
            new_actions.append(b)
        if violations:
            out["actions"] = new_actions
            out["blocked_sell"] = True
            out["guard"] = {"status": BLOCK_STATUS, "violations": violations}
            tag = "SELL EXECUTED PAST GUARD" if any(v["executed"] for v in violations) else "BLOCKED SELL"
            summ = str(out.get("summary") or "")
            if not summ.startswith("[" + tag):
                out["summary"] = f"[{tag}] {summ}".strip()
    except Exception as exc:  # pragma: no cover - defensive
        out["guard_error"] = type(exc).__name__
    return out


# --------------------------------------------------------------------------
# grok runner hardening
# --------------------------------------------------------------------------

RH_SERVER = "robinhood-trading"
# Order-mutating RH MCP tool name globs. cancel_* is deliberately NOT here.
RH_ORDER_TOOL_GLOBS = (
    "place_*",
    "replace_*",
    "modify_*",
    "submit_*",
    "create_*order*",
    "*sell*",
    "*short*",
    "*transfer*",
    "*withdraw*",
)


def grok_deny_args() -> List[str]:
    """Flags for ``grok -p … --yolo``. Deny rules still apply under --yolo."""
    args: List[str] = []
    for g in RH_ORDER_TOOL_GLOBS:
        args += ["--deny", f"MCPTool({RH_SERVER}__{g})"]
    return args


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _deployable_cash_from_snapshot(path: Optional[Path] = None) -> Optional[float]:
    p = Path(path or RH_SNAPSHOT_PATH)
    try:
        snap = json.loads(p.read_text(encoding="utf-8"))
        ag = snap.get("agentic") if isinstance(snap.get("agentic"), dict) else None
        if ag is None:
            return None
        cash, bp = _num(ag.get("cash")), _num(ag.get("buying_power"))
        if cash is None or bp is None:
            return None
        return max(0.0, min(cash, bp))
    except (OSError, ValueError, AttributeError):
        return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print-grok-deny-args", action="store_true", help="one deny arg per line")
    ap.add_argument("--execute-intents", metavar="FILE", help="gate + (maybe) submit intents JSON")
    ap.add_argument("--dry-run", action="store_true", help="never submit, even when live")
    ap.add_argument("--no-record", action="store_true", help="skip decision log/#701 (tests)")
    args = ap.parse_args(argv)
    if args.print_grok_deny_args:
        print("\n".join(grok_deny_args()))
        return 0
    if args.execute_intents:
        p = Path(args.execute_intents)
        if not p.is_file():
            print(json.dumps({"ok": True, "intents": 0, "note": "no intents file; nothing to do"}))
            return 0
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and "orders" in payload:
                payload = payload["orders"]
        except (OSError, ValueError):
            payload = "unreadable"
        override, oerr = load_override()
        policy = load_gate_policy()
        result = execute_intents(
            payload,
            deployable_cash=_deployable_cash_from_snapshot(),
            submit=load_broker_adapter(),
            policy=policy,
            override=override,
            dry_run=args.dry_run or None,
            record=not args.no_record,
        )
        try:  # consumed: never replay a stale intents file
            p.rename(p.with_suffix(".consumed.json"))
        except OSError:
            pass
        if oerr:
            result["override_error"] = oerr
        print(json.dumps(result, indent=2, default=str))
        return 3 if result["blocked"] else 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
