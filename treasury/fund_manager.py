#!/usr/bin/env python3
"""Agentic fund manager — policy load, sleeve weights, rebalance hints.

Scope: **Robinhood agentic account only**. Risk is bounded by capital deposited
there. No per-trade approval and no max notional in v1 (see investment/fund_manager.json).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import (  # noqa: E402
    SNAPSHOTS_DIR,
    load_config,
    load_json,
    save_json,
)

POLICY_PATH = ROOT / "investment" / "fund_manager.json"
WATCHLIST_PATH = ROOT / "investment" / "watchlist.json"
FM_SNAPSHOT = SNAPSHOTS_DIR / "fund_manager_latest.json"
DECISIONS_PATH = SNAPSHOTS_DIR / "fund_manager_decisions.jsonl"
JOURNAL_PATH = ROOT / "investment" / "fund_manager_journal.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def load_fund_policy(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or POLICY_PATH
    data = load_json(p)
    if not data:
        raise FileNotFoundError(f"fund manager policy missing: {p}")
    return data


def load_watchlist(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load thematic monitor/consider list (not holdings / not auto-buy)."""
    p = path or WATCHLIST_PATH
    data = load_json(p)
    if not data:
        return {
            "version": 0,
            "entries": [],
            "policy": {"auto_buy": False},
            "error": f"watchlist missing or empty: {p}",
        }
    return data


def watchlist_entries(watchlist: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    wl = watchlist if watchlist is not None else load_watchlist()
    entries = wl.get("entries") or []
    out: List[Dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        sym = (e.get("symbol") or "").strip().upper()
        if not sym:
            continue
        row = dict(e)
        row["symbol"] = sym
        out.append(row)
    return out


def watchlist_summary(
    policy: Optional[Dict[str, Any]] = None,
    watchlist: Optional[Dict[str, Any]] = None,
    *,
    held_symbols: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Compact watchlist for FCC / analysis — candidates only, not orders."""
    policy = policy or load_fund_policy()
    wl = watchlist if watchlist is not None else load_watchlist()
    held = {s.strip().upper() for s in (held_symbols or []) if s}
    entries = watchlist_entries(wl)
    compact = []
    for e in entries:
        sym = e["symbol"]
        compact.append(
            {
                "symbol": sym,
                "name": e.get("name"),
                "theme": e.get("theme"),
                "status": e.get("status") or "monitor",
                "priority": e.get("priority"),
                "sleeve_if_owned": e.get("sleeve_if_owned") or "stocks_growth",
                "deep_dive_required_before_buy": bool(
                    e.get("deep_dive_required_before_buy", True)
                ),
                "last_deep_dive": e.get("last_deep_dive"),
                "held": sym in held,
                "thesis_fit": e.get("thesis_fit"),
            }
        )
    wl_pol = wl.get("policy") or {}
    fm_wl = policy.get("watchlist") or {}
    return {
        "path": str((policy.get("docs") or {}).get("watchlist") or "investment/watchlist.json"),
        "auto_buy": bool(wl_pol.get("auto_buy", False)),
        "deep_dive_workflow": fm_wl.get("deep_dive_workflow")
        or wl_pol.get("deep_dive_workflow")
        or "position-deep-dive",
        "count": len(compact),
        "symbols": [c["symbol"] for c in compact],
        "entries": compact,
        "on_review": fm_wl.get("on_review")
        or "Scan watchlist each review; deep-dive before first buy when required.",
    }


def sleeve_for_symbol(symbol: str, policy: Dict[str, Any]) -> str:
    sym = (symbol or "").strip().upper()
    sleeves = policy.get("sleeves") or {}
    btc = sleeves.get("btc_digital_credit") or {}
    stocks = sleeves.get("stocks_growth") or {}
    btc_set = {s.upper() for s in (btc.get("symbols") or []) + (btc.get("optional_symbols") or [])}
    stocks_set = {s.upper() for s in (stocks.get("symbols") or [])}
    if sym in btc_set:
        return "btc_digital_credit"
    if sym in stocks_set:
        return "stocks_growth"
    # Watchlist sleeve mapping (if held without being core)
    for e in watchlist_entries():
        if e.get("symbol") == sym:
            sleeve = (e.get("sleeve_if_owned") or "stocks_growth").strip()
            if sleeve in ("btc_digital_credit", "stocks_growth"):
                return sleeve
            return "stocks_growth"
    energy = sleeves.get("energy_opportunistic") or {}
    energy_set = {
        s.upper()
        for s in (energy.get("symbols") or []) + (energy.get("watchlist_symbols") or [])
    }
    if sym in energy_set:
        return "stocks_growth"
    return "other"


def _position_market_value(pos: Dict[str, Any], quotes: Optional[Dict[str, float]] = None) -> float:
    """Estimate position MV from quantity * price if available."""
    qty = _f(pos.get("quantity") or pos.get("shares_available_for_sells"))
    if qty <= 0:
        return 0.0
    sym = (pos.get("symbol") or "").upper()
    px = None
    if quotes and sym in quotes:
        px = quotes[sym]
    for k in ("last_price", "price", "mark_price", "average_buy_price"):
        if pos.get(k) is not None and px is None:
            px = _f(pos.get(k))
    if px is None or px <= 0:
        # Fall back to cost basis so empty quotes still allocate
        px = _f(pos.get("average_buy_price"))
    return qty * px


def analyze_agentic_book(
    rh_snapshot: Dict[str, Any],
    policy: Optional[Dict[str, Any]] = None,
    *,
    quotes: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Compute sleeve weights from agentic account only."""
    policy = policy or load_fund_policy()
    targets = policy.get("targets") or {}
    tgt_btc = _f(targets.get("btc_digital_credit_pct"), 0.4)
    tgt_stocks = _f(targets.get("stocks_growth_pct"), 0.6)
    band = _f(targets.get("band_pct"), 0.05)

    agentic = rh_snapshot.get("agentic") if isinstance(rh_snapshot.get("agentic"), dict) else None
    if agentic is None and rh_snapshot.get("agentic_allowed") is True:
        agentic = rh_snapshot

    if not agentic:
        return {
            "ok": False,
            "error": "no agentic block in robinhood snapshot",
            "as_of": _now(),
            "policy_version": policy.get("version"),
        }

    cash = _f(agentic.get("cash"))
    bp = _f(agentic.get("buying_power"))
    total = _f(agentic.get("total_value"))
    positions = list(agentic.get("positions") or [])

    by_sleeve = {
        "btc_digital_credit": 0.0,
        "stocks_growth": 0.0,
        "other": 0.0,
    }
    rows: List[Dict[str, Any]] = []
    equity_mv = 0.0
    for pos in positions:
        sym = (pos.get("symbol") or "").upper()
        if not sym:
            continue
        mv = _position_market_value(pos, quotes)
        sleeve = sleeve_for_symbol(sym, policy)
        by_sleeve[sleeve] = by_sleeve.get(sleeve, 0.0) + mv
        equity_mv += mv
        rows.append(
            {
                "symbol": sym,
                "quantity": _f(pos.get("quantity")),
                "market_value": round(mv, 4),
                "sleeve": sleeve,
            }
        )

    # Prefer broker total_value; if zero but we have cash, use cash+equity
    nav = total if total > 0 else (cash + equity_mv)
    if nav <= 0 and bp > 0:
        nav = bp

    deployed = equity_mv
    unallocated = max(0.0, nav - deployed)
    # If cash is large pending, use max(cash, unallocated)
    cash_weight = (cash / nav) if nav > 0 else 0.0

    def pct(x: float) -> Optional[float]:
        if nav <= 0:
            return None
        return round(x / nav, 4)

    w_btc = pct(by_sleeve["btc_digital_credit"])
    w_stocks = pct(by_sleeve["stocks_growth"])
    w_other = pct(by_sleeve["other"])
    w_cash = pct(cash) if cash else pct(unallocated)

    def drift(actual: Optional[float], target: float) -> Optional[float]:
        if actual is None:
            return None
        return round(actual - target, 4)

    # Among *deployed* equity only, compare sleeve mix to 40/60
    if deployed > 0:
        dep_btc = by_sleeve["btc_digital_credit"] / deployed
        dep_stocks = by_sleeve["stocks_growth"] / deployed
    else:
        dep_btc = dep_stocks = None

    in_band_btc = (
        abs((dep_btc if dep_btc is not None else tgt_btc) - tgt_btc) <= band
        if dep_btc is not None
        else None
    )
    in_band_stocks = (
        abs((dep_stocks if dep_stocks is not None else tgt_stocks) - tgt_stocks) <= band
        if dep_stocks is not None
        else None
    )

    approval = policy.get("approval") or {}
    limits = policy.get("limits") or {}

    hints: List[str] = []
    if nav <= 0 or bp <= 0 and cash <= 0 and deployed <= 0:
        hints.append("Agentic book empty or still settling — wait for buying power.")
    elif deployed <= 0 and (cash > 0 or bp > 0):
        hints.append(
            f"All cash (~${max(cash, bp):.2f}). Deploy toward ~{tgt_btc:.0%} BTC-complex / "
            f"~{tgt_stocks:.0%} stocks using core allowlist at manager discretion."
        )
    else:
        if dep_btc is not None and dep_btc < tgt_btc - band:
            hints.append(
                f"BTC-complex underweight vs {tgt_btc:.0%} of deployed "
                f"(at {dep_btc:.0%}) — favor MSTR/BITA/miners/etc."
            )
        if dep_stocks is not None and dep_stocks < tgt_stocks - band:
            hints.append(
                f"Stocks sleeve underweight vs {tgt_stocks:.0%} of deployed "
                f"(at {dep_stocks:.0%}) — favor TSLA/SPCX/AI-stack."
            )
        if dep_btc is not None and dep_stocks is not None and in_band_btc and in_band_stocks:
            hints.append("Deployed mix within target bands — discretionary alpha only.")

    held_syms = [
        (r.get("symbol") or "").upper() for r in rows if (r.get("symbol") or "").strip()
    ]
    wl = watchlist_summary(policy, held_symbols=held_syms)
    if wl.get("count"):
        mon = [
            e["symbol"]
            for e in (wl.get("entries") or [])
            if (e.get("status") or "monitor") in ("monitor", "ready") and not e.get("held")
        ]
        if mon:
            hints.append(
                "Watchlist candidates (not auto-buy): "
                + ", ".join(mon)
                + " — deep-dive via /position-deep-dive before first buy when required."
            )

    return {
        "ok": True,
        "as_of": _now(),
        "policy_version": policy.get("version"),
        "mode": policy.get("mode"),
        "account_last4": agentic.get("account_number_last4"),
        "account_number": agentic.get("account_number"),
        "agentic_allowed": bool(agentic.get("agentic_allowed", True)),
        "nav_usd": round(nav, 4),
        "cash_usd": round(cash, 4),
        "buying_power_usd": round(bp, 4),
        "pending_deposits_usd": _f(agentic.get("pending_deposits"))
        if agentic.get("pending_deposits") is not None
        else None,
        "equity_market_value_usd": round(equity_mv, 4),
        "weights_of_nav": {
            "btc_digital_credit": w_btc,
            "stocks_growth": w_stocks,
            "other": w_other,
            "cash": w_cash,
        },
        "weights_of_deployed": {
            "btc_digital_credit": round(dep_btc, 4) if dep_btc is not None else None,
            "stocks_growth": round(dep_stocks, 4) if dep_stocks is not None else None,
        },
        "targets": {
            "btc_digital_credit_pct": tgt_btc,
            "stocks_growth_pct": tgt_stocks,
            "band_pct": band,
        },
        "drift_deployed": {
            "btc_digital_credit": drift(dep_btc, tgt_btc) if dep_btc is not None else None,
            "stocks_growth": drift(dep_stocks, tgt_stocks) if dep_stocks is not None else None,
        },
        "in_band": {
            "btc_digital_credit": in_band_btc,
            "stocks_growth": in_band_stocks,
        },
        "positions": rows,
        "sleeve_market_value": {k: round(v, 4) for k, v in by_sleeve.items()},
        "allowlist_core": (policy.get("allowlist") or {}).get("core") or [],
        "watchlist": wl,
        "approval": {
            "require_user_confirm": bool(approval.get("require_user_confirm")),
            "max_single_order_notional_usd": limits.get("max_single_order_notional_usd"),
        },
        "manager_hints": hints,
        "fair_game": bool(bp > 0 or cash > 0 or deployed > 0),
        "notes": (
            "Agentic-only weights. No trade approval in v1. "
            "Downside capped by agentic deposits. Order size at manager discretion. "
            "Watchlist is monitor/consider only — not auto-buy."
        ),
    }


def write_fund_manager_snapshot(result: Dict[str, Any], path: Optional[Path] = None) -> Path:
    out = path or FM_SNAPSHOT
    save_json(out, result)
    return out


def load_decision_log(
    path: Optional[Path] = None,
    *,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    """Load recent fund-manager decisions (newest last in file → return newest first)."""
    p = path or DECISIONS_PATH
    if not p.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return list(reversed(rows[-limit:]))


def append_decision(
    decision: Dict[str, Any],
    *,
    path: Optional[Path] = None,
    also_journal: bool = True,
) -> Dict[str, Any]:
    """Append one decision with rationale for human monitoring (JSONL + optional markdown).

    Expected keys (flexible): as_of, kind (deploy|rebalance|hold|rotate|error),
    summary, rationale {why_now, why_not_alternatives, thesis_tags},
    team_votes {scout, thesis, risk, critic, executor},
    actions [{symbol, side, notional_usd, status, order_id}],
    weights_before, weights_after_expected, nav_usd, buying_power_usd.
    """
    p = path or DECISIONS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = dict(decision)
    entry.setdefault("as_of", _now())
    entry.setdefault("schema_version", 1)
    entry.setdefault("account_scope", "agentic_only")
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if also_journal:
        _append_journal_markdown(entry)

    return entry


def _append_journal_markdown(entry: Dict[str, Any]) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not JOURNAL_PATH.is_file():
        JOURNAL_PATH.write_text("# Fund manager journal\n\n", encoding="utf-8")

    rat = entry.get("rationale") or {}
    if isinstance(rat, str):
        rat = {"summary": rat}
    votes = entry.get("team_votes") or {}
    actions = entry.get("actions") or []
    lines = [
        f"\n## {entry.get('as_of', _now())[:19]} — {entry.get('kind', 'decision')}\n",
        f"**Summary:** {entry.get('summary') or rat.get('summary') or '—'}\n",
    ]
    if entry.get("nav_usd") is not None:
        lines.append(
            f"**Book:** NAV ${entry.get('nav_usd')} · BP ${entry.get('buying_power_usd')}\n"
        )
    wb = entry.get("weights_before") or {}
    if wb:
        lines.append(
            f"**Weights before (deployed):** BTC-complex {wb.get('btc_digital_credit')} · "
            f"Stocks {wb.get('stocks_growth')}\n"
        )
    if rat.get("why_now"):
        lines.append(f"**Why now:** {rat['why_now']}\n")
    if rat.get("why_not_alternatives"):
        lines.append(f"**Why not alternatives:** {rat['why_not_alternatives']}\n")
    if votes:
        lines.append("**Team:**\n")
        for role in ("scout", "thesis", "risk", "critic", "executor"):
            if role in votes:
                v = votes[role]
                if isinstance(v, dict):
                    lines.append(
                        f"- **{role}:** {v.get('vote', v.get('stance', '—'))} — {v.get('note', '')}\n"
                    )
                else:
                    lines.append(f"- **{role}:** {v}\n")
    if actions:
        lines.append("**Actions:**\n")
        for a in actions:
            if isinstance(a, dict):
                lines.append(
                    f"- {a.get('side', '?').upper()} {a.get('symbol', '?')} "
                    f"${a.get('notional_usd', a.get('dollar_amount', '?'))} "
                    f"[{a.get('status', '?')}] {a.get('order_id') or ''}\n"
                )
            else:
                lines.append(f"- {a}\n")
    lines.append("\n")
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.writelines(lines)


def evaluate_fund_manager(
    *,
    rh_snapshot: Optional[Dict[str, Any]] = None,
    policy: Optional[Dict[str, Any]] = None,
    quotes: Optional[Dict[str, float]] = None,
    decision_limit: int = 20,
) -> Dict[str, Any]:
    policy = policy or load_fund_policy()
    if rh_snapshot is None:
        rh_snapshot = load_json(SNAPSHOTS_DIR / "robinhood_latest.json") or {}
    analysis = analyze_agentic_book(rh_snapshot, policy, quotes=quotes)
    cfg = load_config()
    agentic_acct = (cfg.get("robinhood") or {}).get("agentic_account_number")
    decisions = load_decision_log(limit=decision_limit)
    cadence = policy.get("cadence") or {}
    team = policy.get("team") or {}
    rationale_cfg = policy.get("rationale") or {}
    return {
        "ok": bool(analysis.get("ok")),
        "as_of": _now(),
        "agentic_account_number": agentic_acct,
        "policy_path": str(POLICY_PATH.relative_to(ROOT)),
        "analysis": analysis,
        "recent_decisions": decisions,
        "policy_summary": {
            "live": bool(policy.get("live", False)),
            "status": policy.get("status"),
            "require_user_confirm": (policy.get("approval") or {}).get("require_user_confirm"),
            "max_single_order_notional_usd": (policy.get("limits") or {}).get(
                "max_single_order_notional_usd"
            ),
            "scope": (policy.get("account") or {}).get("scope"),
            "targets": policy.get("targets"),
            "cadence": cadence,
            "team_enabled": bool(team.get("enabled")),
            "quorum": team.get("quorum"),
            "rationale_required": bool(rationale_cfg.get("required_on_every_decision", True)),
            "bootstrap_blocker": ((policy.get("bootstrap") or {}).get("initial_deploy") or {}).get(
                "blocker"
            ),
        },
    }


def rules_based_review(
    *,
    rh_snapshot: Optional[Dict[str, Any]] = None,
    policy: Optional[Dict[str, Any]] = None,
    log: bool = True,
) -> Dict[str, Any]:
    """Daily review without LLM when possible.

    - In-band deployed mix + no meaningful idle cash → HOLD (log + optional notify skip)
    - Idle cash or drift → NEED_LLM (caller may run team debate / grok)
    - live:false → OBSERVE only
    """
    policy = policy or load_fund_policy()
    fm = evaluate_fund_manager(rh_snapshot=rh_snapshot, policy=policy)
    analysis = fm.get("analysis") or {}
    live = bool(policy.get("live", False))
    targets = analysis.get("targets") or policy.get("targets") or {}
    band = _f(targets.get("band_pct"), 0.05)
    w = analysis.get("weights_of_deployed") or {}
    btc = w.get("btc_digital_credit")
    stocks = w.get("stocks_growth")
    cash = _f(analysis.get("cash_usd"))
    nav = _f(analysis.get("nav_usd"))
    bp = _f(analysis.get("buying_power_usd"))
    min_trade = _f((policy.get("limits") or {}).get("min_trade_notional_usd"), 1.0)
    # Any positive cash or BP is material — no %NAV gate (owner: deploy whenever capital is free)
    idle_capital = cash > 0 or bp > 0
    deployable = max(cash, bp)

    in_band = (
        btc is not None
        and stocks is not None
        and abs(btc - _f(targets.get("btc_digital_credit_pct"), 0.4)) <= band
        and abs(stocks - _f(targets.get("stocks_growth_pct"), 0.6)) <= band
    )
    no_deployed = btc is None or stocks is None or (btc == 0 and stocks == 0)

    if not live:
        outcome = "observe"
        kind = "hold"
        summary = "live:false — observe only, no trades"
        need_llm = False
    elif not analysis.get("ok"):
        outcome = "error"
        kind = "error"
        summary = analysis.get("error") or "agentic analysis failed"
        need_llm = False
    elif in_band and not idle_capital:
        outcome = "hold"
        kind = "hold"
        summary = (
            f"Rules HOLD: deployed mix in ±{band:.0%} band "
            f"(BTC-complex {btc:.0%}, stocks {stocks:.0%}); cash/BP ${cash:.2f}/${bp:.2f} zero"
        )
        need_llm = False
    elif idle_capital and no_deployed:
        outcome = "need_llm"
        kind = "deploy"
        summary = (
            f"Rules → need team/LLM: idle capital cash ${cash:.2f} BP ${bp:.2f} "
            f"(deployable ${deployable:.2f}) toward 40/60"
        )
        need_llm = True
    elif idle_capital:
        outcome = "need_llm"
        kind = "deploy"
        summary = (
            f"Rules → need team/LLM: free capital cash ${cash:.2f} BP ${bp:.2f} "
            f"(any >$0 triggers; min_trade ${min_trade:.2f} for dust tickets)"
        )
        need_llm = True
    elif not in_band and btc is not None:
        outcome = "need_llm"
        kind = "rebalance"
        summary = (
            f"Rules → need team/LLM: drift BTC-complex={btc:.0%} stocks={stocks:.0%} "
            f"(targets 40/60 ±{band:.0%})"
        )
        need_llm = True
    else:
        outcome = "need_llm"
        kind = "review"
        summary = "Rules → need team/LLM: ambiguous state"
        need_llm = True

    decision = {
        "kind": kind,
        "outcome": outcome,
        "need_llm": need_llm,
        "path": "rules",
        "summary": summary,
        "nav_usd": analysis.get("nav_usd"),
        "buying_power_usd": analysis.get("buying_power_usd"),
        "weights_before": {
            "btc_digital_credit": btc,
            "stocks_growth": stocks,
        },
        "weights_after_expected": {
            "btc_digital_credit": btc if kind == "hold" else _f(targets.get("btc_digital_credit_pct"), 0.4),
            "stocks_growth": stocks if kind == "hold" else _f(targets.get("stocks_growth_pct"), 0.6),
        },
        "rationale": {
            "summary": summary,
            "why_now": (
                "Scheduled daily review (rules path). Mid-session style; no day-trading."
            ),
            "why_not_alternatives": (
                "In-band + low cash → skip LLM cost/latency. "
                "Drift or deploy needs thesis/risk/critic debate before Executor trades."
                if not need_llm
                else "Quorum team should debate size/names; Executor only places after OK."
            ),
            "thesis_tags": ["rules_engine", "modernized_60_40"],
        },
        "team_votes": {
            "scout": {
                "vote": "observe",
                "note": f"NAV ${nav:.2f} BP ${bp:.2f} cash ${cash:.2f}",
            },
            "thesis": {
                "vote": "ok" if (in_band or not live) else "rebalance",
                "note": f"deployed BTC {btc} stocks {stocks}",
            },
            "risk": {
                "vote": "ok" if outcome == "hold" else "review",
                "note": "Agentic capital only; no trade if hold",
            },
            "critic": {
                "vote": "ok" if outcome == "hold" else "challenge",
                "note": "Hold preferred when bands ok — avoid churn",
            },
            "executor": {
                "vote": "hold" if not need_llm else "await_team",
                "note": "No MCP orders on pure rules HOLD",
            },
        },
        "actions": [],
    }

    logged = False
    if log and live:
        # Avoid spam: only log HOLD if last decision wasn't identical hold same day
        should_log = True
        if outcome == "hold":
            recent = load_decision_log(limit=3)
            if recent:
                last = recent[0]
                if last.get("kind") == "hold" and last.get("path") == "rules":
                    last_day = (last.get("as_of") or "")[:10]
                    if last_day == _now()[:10]:
                        should_log = False
        if should_log:
            append_decision(decision)
            logged = True

    fm["rules_review"] = {
        "outcome": outcome,
        "kind": kind,
        "need_llm": need_llm,
        "summary": summary,
        "in_band": in_band,
        "idle_capital": idle_capital,
        "deployable_usd": round(deployable, 4),
        "logged": logged,
    }
    write_fund_manager_snapshot(fm)
    return fm


def notify_if_needed(
    *,
    decision_or_review: Dict[str, Any],
    treasury_eval: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Push ntfy alert for non-HOLD decisions or stale RH (optional email later)."""
    import urllib.error
    import urllib.request

    cfg = load_config()
    ncfg = (cfg.get("notifications") or {}) if isinstance(cfg, dict) else {}
    topic = (
        ncfg.get("ntfy_topic")
        or __import__("os").environ.get("FCC_NTFY_TOPIC")
        or "cvolk-grok-7f3k9x"
    )
    enabled = ncfg.get("enabled", True)
    if not enabled and not force:
        return {"ok": False, "skipped": "notifications disabled"}

    rules = (decision_or_review.get("rules_review") or decision_or_review) if decision_or_review else {}
    outcome = rules.get("outcome") or decision_or_review.get("kind")
    need_llm = rules.get("need_llm")
    summary = rules.get("summary") or decision_or_review.get("summary") or ""

    # Stale RH from treasury eval
    stale_msgs: List[str] = []
    if treasury_eval:
        dq = (treasury_eval.get("data_quality") or {}) if isinstance(treasury_eval, dict) else {}
        for s in dq.get("stale") or []:
            if "robinhood" in str(s).lower() or "rh" in str(s).lower():
                stale_msgs.append(str(s))
        for w in dq.get("warnings") or []:
            if "robinhood" in str(w).lower() and "old" in str(w).lower():
                stale_msgs.append(str(w))

    should = force
    title = "FCC fund manager"
    body_parts: List[str] = []

    if outcome in ("need_llm", "deploy", "rebalance", "rotate") or need_llm:
        should = True
        title = "FCC · fund review needs action"
        body_parts.append(summary or "Team/LLM review recommended")
    elif outcome == "error":
        should = True
        title = "FCC · fund manager error"
        body_parts.append(summary)
    elif outcome == "hold":
        # quiet success — no notify unless forced
        pass

    if stale_msgs:
        should = True
        title = "FCC · stale RH feed"
        body_parts.extend(stale_msgs[:3])

    if not should:
        return {"ok": True, "notified": False, "reason": "quiet hold"}

    text = "\n".join(body_parts) or summary or "FCC alert"
    url = f"https://ntfy.sh/{topic}"
    try:
        req = urllib.request.Request(
            url,
            data=text.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "3",
                "Tags": "chart_with_upwards_trend,robot",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {
                "ok": True,
                "notified": True,
                "status": resp.status,
                "title": title,
            }
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"ok": False, "notified": False, "error": str(e)}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--write", action="store_true", help="Write fund_manager_latest.json")
    p.add_argument("--json", action="store_true", help="Print full JSON")
    p.add_argument(
        "--rules-review",
        action="store_true",
        help="Run rules-based daily review (HOLD if in band; else need_llm)",
    )
    p.add_argument(
        "--notify",
        action="store_true",
        help="Send ntfy if non-HOLD / stale RH",
    )
    p.add_argument("--no-log", action="store_true", help="With --rules-review, do not append journal")
    args = p.parse_args(argv)

    if args.rules_review:
        result = rules_based_review(log=not args.no_log)
        notify_result = None
        if args.notify:
            # attach treasury DQ for stale check
            tre = load_json(SNAPSHOTS_DIR / "treasury_latest.json") or {}
            ev = tre.get("evaluation") or tre
            notify_result = notify_if_needed(
                decision_or_review=result, treasury_eval=ev
            )
        out = {
            "ok": result.get("ok"),
            "rules_review": result.get("rules_review"),
            "notify": notify_result,
        }
        print(json.dumps(out, indent=2))
        rr = result.get("rules_review") or {}
        # exit 2 = need LLM team; 0 = hold/observe; 1 = error
        if rr.get("outcome") == "error":
            return 1
        if rr.get("need_llm"):
            return 2
        return 0

    result = evaluate_fund_manager()
    if args.write:
        path = write_fund_manager_snapshot(result)
        result["written"] = str(path)
    if args.notify:
        tre = load_json(SNAPSHOTS_DIR / "treasury_latest.json") or {}
        print(
            json.dumps(
                notify_if_needed(
                    decision_or_review={"kind": "hold", "summary": "manual notify check"},
                    treasury_eval=tre.get("evaluation") or tre,
                    force=False,
                ),
                indent=2,
            )
        )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        a = result.get("analysis") or {}
        print(
            json.dumps(
                {
                    "ok": result.get("ok"),
                    "nav": a.get("nav_usd"),
                    "bp": a.get("buying_power_usd"),
                    "cash": a.get("cash_usd"),
                    "weights_deployed": a.get("weights_of_deployed"),
                    "hints": a.get("manager_hints"),
                    "approval": a.get("approval"),
                },
                indent=2,
            )
        )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
