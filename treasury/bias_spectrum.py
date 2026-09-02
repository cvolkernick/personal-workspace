"""Position bias spectrum for FCC.

Axis unit is **new-money consider-share** — relative standing bias when
new funds are allocated — not current book weight.

  above = BTC / digital-credit sleeve (~40% of new money)
  below = stocks / growth sleeve (~60% of new money, incl. energy
          opportunistic names via sleeve_if_owned)

Consider set = core allowlist ∪ ready watchlist (private off-axis).
Role scores come from written policy only:

  preferred_core (STRC/SATA) 5 · core 4 · watch high 3 · med 2 · low 1

Within each sleeve, share = role_score / sleeve_score_sum, then scaled
by the sleeve's target budget so chips sum to ~100% of new money.

Optional consider-share stamps (investment/consider_share.json, or a
watchlist/policy `consider_share_stamps` block) pin named chips, then
proportionally rescale the rest so the list still sums to 100%. Those
pins are residual-mix / relative preference on this axis only — NOT a
live NAV target, NOT a sleeve target, NOT an order. Autopilot and
Monday residual must not chase them.

Live book % is an annotation (held badge), never the axis. This is not
an order ticket — the fund manager still research/rotates at deploy.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
FM_POLICY = ROOT / "investment" / "fund_manager.json"
WATCHLIST_PATH = ROOT / "investment" / "watchlist.json"
CONSIDER_SHARE_PATH = ROOT / "investment" / "consider_share.json"
FM_SNAPSHOT = ROOT / "treasury" / "snapshots" / "fund_manager_latest.json"
TREASURY_FCC = ROOT / "financial-command" / "treasury_latest.json"
TREASURY_SNAP = ROOT / "treasury" / "snapshots" / "treasury_latest.json"
TREASURY_WORKTREE = Path.home() / "personal-workspace-worktrees" / "treasury"

ROLE_SCORE = {
    "preferred_core": 5.0,
    "core": 4.0,
    "watch_high": 3.0,
    "watch_med": 2.0,
    "watch_low": 1.0,
}
PRIORITY_TO_ROLE = {
    "high": "watch_high",
    "med": "watch_med",
    "medium": "watch_med",
    "low": "watch_low",
}
SLEEVE_BTC = "btc_digital_credit"
SLEEVE_STOCKS = "stocks_growth"
READY_STATUSES = {"ready"}
DEFAULT_BTC_BUDGET = 0.4
DEFAULT_STOCKS_BUDGET = 0.6
_GIT_JSON_TTL_S = 30.0
_GIT_JSON_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _sym(value: Any) -> str:
    return str(value or "").strip().upper()


def _money(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def _syms(values: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in _as_list(values):
        s = _sym(item)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _analysis_from_fm(fm: Dict[str, Any]) -> Dict[str, Any]:
    an = fm.get("analysis")
    if isinstance(an, dict) and (an.get("ok") or an.get("positions") or an.get("watchlist")):
        return an
    if fm.get("ok") and (fm.get("positions") or fm.get("watchlist")):
        return fm
    return {}


def _load_fund_manager(
    fund_manager: Optional[Dict[str, Any]] = None,
    treasury: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if isinstance(fund_manager, dict) and _analysis_from_fm(fund_manager):
        return fund_manager
    if isinstance(treasury, dict):
        fm = treasury.get("fund_manager")
        if isinstance(fm, dict) and _analysis_from_fm(fm):
            return fm
        ev = treasury.get("evaluation")
        if isinstance(ev, dict):
            fm = ev.get("fund_manager")
            if isinstance(fm, dict) and _analysis_from_fm(fm):
                return fm
    for root in _treasury_roots():
        snap = _load_json(root / "treasury" / "snapshots" / "fund_manager_latest.json")
        if _analysis_from_fm(snap):
            return snap
        for rel in (
            Path("financial-command") / "treasury_latest.json",
            Path("treasury") / "snapshots" / "treasury_latest.json",
        ):
            tre = _load_json(root / rel)
            fm = tre.get("fund_manager") if isinstance(tre.get("fund_manager"), dict) else {}
            if _analysis_from_fm(fm):
                return fm
    return {}


def _treasury_roots() -> List[Path]:
    roots = [ROOT]
    env = (os.environ.get("FCC_WORKTREE_ROOT") or "").strip()
    if env:
        roots.append(Path(env).expanduser())
    roots.append(TREASURY_WORKTREE)
    seen: set[str] = set()
    out: List[Path] = []
    for p in roots:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _first_json(rel: str) -> Dict[str, Any]:
    for root in _treasury_roots():
        data = _load_json(root / rel)
        if data:
            return data
    return {}


def _git_show_json(rel: str) -> Dict[str, Any]:
    """Pi FCC runs master; finance policy lives on origin/work/treasury."""
    now = time.monotonic()
    cached = _GIT_JSON_CACHE.get(rel)
    if cached and now - cached[0] < _GIT_JSON_TTL_S:
        return cached[1]
    data: Dict[str, Any] = {}
    try:
        raw = subprocess.check_output(
            ["git", "-C", str(ROOT), "show", f"origin/work/treasury:{rel}"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        parsed = json.loads(raw.decode("utf-8"))
        if isinstance(parsed, dict):
            data = parsed
    except (
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
    ):
        data = {}
    _GIT_JSON_CACHE[rel] = (now, data)
    return data


def _policy_usable(pol: Dict[str, Any]) -> bool:
    if not isinstance(pol, dict) or not pol:
        return False
    return bool(_core_allowlist(pol) or _preferred_core(pol) or _sleeve_membership(pol))


def _policy_from_snapshot(fm: Dict[str, Any]) -> Dict[str, Any]:
    """Last-resort consider-set when fund_manager.json is not on this checkout."""
    analysis = _analysis_from_fm(fm)
    core = _syms(analysis.get("allowlist_core"))
    targets = _as_dict(analysis.get("targets")) or _as_dict(
        _as_dict(fm.get("policy_summary")).get("targets")
    )
    btc_syms: List[str] = []
    stocks_syms: List[str] = []
    btc_watch: List[str] = []
    stocks_watch: List[str] = []
    for p in _as_list(analysis.get("positions")):
        if not isinstance(p, dict):
            continue
        sym = _sym(p.get("symbol"))
        sleeve = str(p.get("sleeve") or "").strip()
        if not sym or sleeve not in (SLEEVE_BTC, SLEEVE_STOCKS):
            continue
        if sleeve == SLEEVE_BTC:
            btc_syms.append(sym)
        else:
            stocks_syms.append(sym)
    for e in _as_list(_as_dict(analysis.get("watchlist")).get("entries")):
        if not isinstance(e, dict):
            continue
        sym = _sym(e.get("symbol"))
        sleeve = str(e.get("sleeve_if_owned") or e.get("sleeve") or "").strip()
        if not sym or sleeve not in (SLEEVE_BTC, SLEEVE_STOCKS):
            continue
        if sleeve == SLEEVE_BTC:
            btc_watch.append(sym)
        else:
            stocks_watch.append(sym)
    if not core and not btc_syms and not stocks_syms and not btc_watch and not stocks_watch:
        return {}
    btc_budget = _money(targets.get("btc_digital_credit_pct"))
    stocks_budget = _money(targets.get("stocks_growth_pct"))
    return {
        "as_of": analysis.get("as_of") or fm.get("as_of"),
        "targets": {
            "btc_digital_credit_pct": btc_budget if btc_budget is not None else DEFAULT_BTC_BUDGET,
            "stocks_growth_pct": stocks_budget if stocks_budget is not None else DEFAULT_STOCKS_BUDGET,
            "band_pct": targets.get("band_pct"),
        },
        "allowlist": {"core": core},
        "sleeves": {
            SLEEVE_BTC: {
                "target_pct": btc_budget if btc_budget is not None else DEFAULT_BTC_BUDGET,
                "symbols": btc_syms,
                "watchlist_symbols": btc_watch,
            },
            SLEEVE_STOCKS: {
                "target_pct": stocks_budget if stocks_budget is not None else DEFAULT_STOCKS_BUDGET,
                "symbols": stocks_syms,
                "watchlist_symbols": stocks_watch,
            },
        },
    }


def _load_policy(
    policy: Optional[Dict[str, Any]] = None,
    fund_manager: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if isinstance(policy, dict):
        return policy
    pol = _first_json("investment/fund_manager.json")
    if _policy_usable(pol):
        return pol
    pol = _git_show_json("investment/fund_manager.json")
    if _policy_usable(pol):
        return pol
    return _policy_from_snapshot(fund_manager or {})


def _load_watchlist(watchlist: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if isinstance(watchlist, dict):
        return watchlist
    wl = _first_json("investment/watchlist.json")
    if "entries" in wl:
        return wl
    git_wl = _git_show_json("investment/watchlist.json")
    if "entries" in git_wl:
        return git_wl
    return wl


def _preferred_core(policy: Dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for sleeve in _as_dict(policy.get("sleeves")).values():
        if not isinstance(sleeve, dict):
            continue
        for sub in _as_dict(sleeve.get("sub_sleeves")).values():
            if not isinstance(sub, dict):
                continue
            found.update(_syms(sub.get("preferred_core")))
    return found


def _core_allowlist(policy: Dict[str, Any]) -> set[str]:
    return set(_syms(_as_dict(policy.get("allowlist")).get("core")))


def _sleeve_membership(policy: Dict[str, Any]) -> Dict[str, str]:
    """Map symbol → sleeve for named btc/stocks lists. Energy is open."""
    mapping: Dict[str, str] = {}
    for name, sleeve in _as_dict(policy.get("sleeves")).items():
        if not isinstance(sleeve, dict):
            continue
        if name not in (SLEEVE_BTC, SLEEVE_STOCKS):
            continue
        for sym in _syms(sleeve.get("symbols")) + _syms(sleeve.get("watchlist_symbols")):
            mapping.setdefault(sym, name)
    return mapping


def _sleeve_budgets(policy: Dict[str, Any]) -> Tuple[float, float]:
    targets = _as_dict(policy.get("targets"))
    btc = _money(targets.get("btc_digital_credit_pct"))
    stocks = _money(targets.get("stocks_growth_pct"))
    if btc is None:
        btc = _money(_as_dict(_as_dict(policy.get("sleeves")).get(SLEEVE_BTC)).get("target_pct"))
    if stocks is None:
        stocks = _money(
            _as_dict(_as_dict(policy.get("sleeves")).get(SLEEVE_STOCKS)).get("target_pct")
        )
    btc_f = float(btc) if btc is not None else DEFAULT_BTC_BUDGET
    stocks_f = float(stocks) if stocks is not None else DEFAULT_STOCKS_BUDGET
    total = btc_f + stocks_f
    if total <= 0:
        return DEFAULT_BTC_BUDGET, DEFAULT_STOCKS_BUDGET
    return btc_f / total, stocks_f / total


def _watch_entries(
    watchlist: Dict[str, Any], analysis: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    entries: Dict[str, Dict[str, Any]] = {}
    # File / injected watchlist is SoT when it includes `entries` (even empty).
    # Snapshot watchlist is fallback only when no file/inject list exists.
    if "entries" in watchlist:
        sources = [_as_list(watchlist.get("entries"))]
    else:
        sources = [
            _as_list(_as_dict(analysis.get("watchlist")).get("entries")),
            _as_list(watchlist.get("entries")),
        ]
    for src in sources:
        for e in src:
            if not isinstance(e, dict):
                continue
            sym = _sym(e.get("symbol"))
            if not sym:
                continue
            entries[sym] = e
    return entries


def _held_books(analysis: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    positions = [p for p in _as_list(analysis.get("positions")) if isinstance(p, dict)]
    equity = _money(analysis.get("equity_market_value_usd"))
    if not equity or equity <= 0:
        equity = 0.0
        for p in positions:
            mv = _money(p.get("market_value"))
            if mv and mv > 0:
                equity += mv
    books: Dict[str, Dict[str, Any]] = {}
    if not equity or equity <= 0:
        return books
    for p in positions:
        sym = _sym(p.get("symbol"))
        mv = _money(p.get("market_value"))
        if not sym or mv is None or mv <= 0:
            continue
        books[sym] = {
            "market_value": round(mv, 4),
            "quantity": _money(p.get("quantity")),
            "book_pct": round(100.0 * mv / equity, 2),
            "sleeve": str(p.get("sleeve") or ""),
        }
    return books


def _role_for(
    sym: str,
    *,
    preferred: set[str],
    core: set[str],
    watch: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, float, str]]:
    if sym in preferred:
        return "preferred_core", ROLE_SCORE["preferred_core"], "preferred_core"
    if sym in core:
        return "core", ROLE_SCORE["core"], "core"
    if not watch:
        return None
    status = str(watch.get("status") or "").strip().lower()
    if status and status not in READY_STATUSES:
        return None
    if not status:
        # File entries without status still count as ready consider-set
        # only when they have a priority (owner-named).
        if not watch.get("priority"):
            return None
    pri_key = str(watch.get("priority") or "low").strip().lower()
    role = PRIORITY_TO_ROLE.get(pri_key, "watch_low")
    return role, ROLE_SCORE[role], pri_key if pri_key != "medium" else "med"


def _sleeve_for(
    sym: str,
    *,
    membership: Dict[str, str],
    watch: Optional[Dict[str, Any]],
    held: Optional[Dict[str, Any]],
) -> str:
    if sym in membership:
        return membership[sym]
    if watch:
        owned = str(watch.get("sleeve_if_owned") or watch.get("sleeve") or "").strip()
        if owned in (SLEEVE_BTC, SLEEVE_STOCKS):
            return owned
    if held:
        hs = str(held.get("sleeve") or "").strip()
        if hs in (SLEEVE_BTC, SLEEVE_STOCKS):
            return hs
    return SLEEVE_STOCKS


def _axis_max(placed: List[Dict[str, Any]]) -> float:
    if not placed:
        return 30.0
    top = max(float(c.get("weight_pct") or 0) for c in placed)
    if top <= 10:
        return 10.0
    stepped = float(int((top + 4.999) // 5) * 5)
    return min(max(stepped, 10.0), 100.0)


def _ticks(max_pct: float) -> List[float]:
    step = 5.0 if max_pct <= 40 else 10.0
    out = []
    n = 0.0
    while n <= max_pct + 0.01:
        out.append(round(n, 2))
        n += step
    if out[-1] < max_pct:
        out.append(round(max_pct, 2))
    return out


def _pins_from(value: Any) -> Dict[str, float]:
    """Extract symbol → pct pins. Accepts `{pins: {...}}` or a flat map."""
    if not isinstance(value, dict) or not value:
        return {}
    raw = value.get("pins") if isinstance(value.get("pins"), dict) else value
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for key, val in raw.items():
        if str(key).strip().lower() in {
            "pins",
            "notes",
            "schema",
            "as_of",
            "unit",
            "sum_to",
            "not_a_nav_target",
            "not_a_sleeve_target",
            "not_an_order",
            "not_for_autopilot",
            "not_for_monday_residual",
        }:
            continue
        sym = _sym(key)
        pct = _money(val)
        if not sym or pct is None or pct <= 0:
            continue
        out[sym] = float(pct)
    return out


def _load_consider_share_stamps(
    stamps: Optional[Dict[str, Any]],
    watchlist: Dict[str, Any],
    policy: Dict[str, Any],
    *,
    watchlist_injected: bool,
) -> Dict[str, float]:
    """Stamps are Bias Spectrum residual-mix only. Never a fill/NAV target.

    Injected watchlist/policy without a stamps block stays stamp-free so
    role-score fixtures keep their 40/60 math. Disk/git file is the live SoT.
    """
    if stamps is not None:
        return _pins_from(stamps)
    for block in (
        _as_dict(watchlist).get("consider_share_stamps"),
        _as_dict(policy).get("consider_share_stamps"),
    ):
        pins = _pins_from(block)
        if pins:
            return pins
    if watchlist_injected:
        return {}
    for data in (
        _first_json("investment/consider_share.json"),
        _git_show_json("investment/consider_share.json"),
    ):
        pins = _pins_from(data)
        if pins:
            return pins
    return {}


def _apply_consider_share_stamps(
    chips: List[Dict[str, Any]], pins: Dict[str, float]
) -> List[str]:
    """Pin named chips, rescale every other chip so the list sums to 100%.

    Returns the symbols that were actually stamped. Does not write NAV or
    sleeve targets. Rank order among unpinned names is preserved.
    """
    if not chips or not pins:
        return []
    present = [c for c in chips if c.get("symbol") in pins]
    if not present:
        return []
    applied = [str(c["symbol"]) for c in present]
    pin_total = sum(float(pins[c["symbol"]]) for c in present)
    others = [c for c in chips if c.get("symbol") not in pins]
    other_sum = sum(float(c.get("weight_pct") or 0) for c in others)
    remainder = 100.0 - pin_total
    for chip in present:
        pct = round(float(pins[chip["symbol"]]), 2)
        chip["weight_pct"] = pct
        chip["weight_basis"] = "consider_share_stamp"
        chip["consider_share_stamp"] = True
        chip["notes"] = (
            f"Consideration-list stamp {pct:g}% — residual mix / relative "
            f"preference on the 100% Bias Spectrum list. NOT a live NAV "
            f"target, NOT a sleeve target, NOT an order. Flatten-only: do "
            f"not trim and do not top up. Next residual (Monday $25) stays "
            f"theme-gap, not {chip['symbol']}."
        )
    if others and other_sum > 0 and remainder >= 0:
        scaled = []
        for chip in others:
            raw = float(chip.get("weight_pct") or 0) * remainder / other_sum
            chip["weight_pct"] = round(raw, 2)
            scaled.append(chip)
        drift = round(remainder - sum(float(c["weight_pct"]) for c in scaled), 2)
        if drift and scaled:
            # Park leftover cents on the smallest unpinned chip so equal-score
            # peer sets (NVDA=GOOGL=BE=PLTR) stay tied after the TSLA/SPCX lift.
            anchor = min(scaled, key=lambda c: (float(c["weight_pct"]), c["symbol"]))
            anchor["weight_pct"] = round(float(anchor["weight_pct"]) + drift, 2)
    chips.sort(key=lambda c: (-float(c.get("weight_pct") or 0), str(c.get("symbol") or "")))
    return applied


def _candidate_symbols(
    *,
    core: set[str],
    preferred: set[str],
    membership: Dict[str, str],
    watch_entries: Dict[str, Dict[str, Any]],
) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for sym in list(sorted(preferred)) + list(sorted(core)) + list(sorted(membership)) + list(
        sorted(watch_entries)
    ):
        if sym in seen:
            continue
        seen.add(sym)
        ordered.append(sym)
    return ordered


def build_bias_spectrum(
    *,
    fund_manager: Optional[Dict[str, Any]] = None,
    treasury: Optional[Dict[str, Any]] = None,
    policy: Optional[Dict[str, Any]] = None,
    watchlist: Optional[Dict[str, Any]] = None,
    consider_share_stamps: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble new-money consider-share chips on a 40/60 two-lane axis."""
    treasury_from_disk = not isinstance(treasury, dict)
    watchlist_injected = isinstance(watchlist, dict)
    if treasury_from_disk:
        treasury = _load_json(TREASURY_FCC if TREASURY_FCC.is_file() else TREASURY_SNAP)
    fm = _load_fund_manager(fund_manager, treasury)
    analysis = _analysis_from_fm(fm)
    pol = _load_policy(policy, fund_manager=fm)
    wl = _load_watchlist(watchlist)
    stamps = _load_consider_share_stamps(
        consider_share_stamps, wl, pol, watchlist_injected=watchlist_injected
    )
    preferred = _preferred_core(pol)
    core = _core_allowlist(pol)
    membership = _sleeve_membership(pol)
    watch_entries = _watch_entries(wl, analysis)
    books = _held_books(analysis)
    btc_budget, stocks_budget = _sleeve_budgets(pol)

    scored: Dict[str, Dict[str, Any]] = {}
    for sym in _candidate_symbols(
        core=core,
        preferred=preferred,
        membership=membership,
        watch_entries=watch_entries,
    ):
        watch = watch_entries.get(sym)
        role_info = _role_for(sym, preferred=preferred, core=core, watch=watch)
        if role_info is None:
            continue
        role, score, pri = role_info
        sleeve = _sleeve_for(sym, membership=membership, watch=watch, held=books.get(sym))
        if sleeve not in (SLEEVE_BTC, SLEEVE_STOCKS):
            sleeve = SLEEVE_STOCKS
        held = books.get(sym)
        scored[sym] = {
            "symbol": sym,
            "role": role,
            "score": score,
            "priority": pri if role.startswith("watch_") else None,
            "sleeve": sleeve,
            "watch": watch,
            "held": held,
        }

    sleeve_totals = {SLEEVE_BTC: 0.0, SLEEVE_STOCKS: 0.0}
    for row in scored.values():
        sleeve_totals[row["sleeve"]] += float(row["score"])

    chips: List[Dict[str, Any]] = []
    for sym, row in scored.items():
        sleeve = row["sleeve"]
        total = sleeve_totals[sleeve]
        budget = btc_budget if sleeve == SLEEVE_BTC else stocks_budget
        if total <= 0:
            continue
        pct = round(100.0 * budget * float(row["score"]) / total, 2)
        watch = row["watch"] or {}
        held = row["held"]
        role = row["role"]
        notes = (
            f"New-money consider-share from policy role {role}"
            f"{' / watchlist ' + row['priority'] if row['priority'] else ''} "
            f"inside the {'40% BTC/digital-credit' if sleeve == SLEEVE_BTC else '60% stocks/growth'} "
            f"sleeve budget. Not current book weight, not an order."
        )
        chip = {
            "id": f"bias-{sym}",
            "symbol": sym,
            "label": watch.get("name") or sym,
            "venue": sym,
            "kind": "held" if held else "consider",
            "lane": "above" if sleeve == SLEEVE_BTC else "below",
            "sleeve": sleeve,
            "role": role,
            "weight_pct": pct,
            "weight_basis": "new_money_consider_share",
            "role_score": row["score"],
            "priority": row["priority"],
            "theme": watch.get("theme"),
            "status": watch.get("status"),
            "source": "policy_consider_set",
            "held": bool(held),
            "book_pct": None if not held else held["book_pct"],
            "market_value": None if not held else held["market_value"],
            "quantity": None if not held else held["quantity"],
            "notes": notes,
            "deep_link": f"position.html?symbol={sym}",
        }
        chips.append(chip)

    chips.sort(key=lambda c: (-float(c["weight_pct"]), c["symbol"]))
    stamped = _apply_consider_share_stamps(chips, stamps)
    placed = [c for c in chips if c.get("weight_pct") is not None]
    max_pct = _axis_max(placed)
    targets = _as_dict(analysis.get("targets")) or _as_dict(pol.get("targets"))
    nav = _money(analysis.get("nav_usd"))
    equity = _money(analysis.get("equity_market_value_usd"))
    error = None
    if not pol:
        error = "no fund_manager.json policy — cannot build new-money consider-set"
    elif not chips:
        error = "empty consider-set (core allowlist + ready watchlist)"
    btc_count = sum(1 for c in chips if c["lane"] == "above")
    stocks_count = sum(1 for c in chips if c["lane"] == "below")
    return {
        "ok": True,
        "title": "Bias Spectrum",
        "brand": "FCC",
        "as_of": analysis.get("as_of") or fm.get("as_of") or pol.get("as_of") or _now(),
        "error": error,
        "axis": {
            "layout": "two_lane",
            "left": "0%",
            "right": f"~{int(max_pct)}%",
            "min_pct": 0.0,
            "max_pct": max_pct,
            "held_lane": "above",
            "consider_lane": "below",
            "btc_lane": "above",
            "stocks_lane": "below",
            "unit": "new_money_consider_share_pct",
            "ticks": _ticks(max_pct),
        },
        "chips": chips,
        "placed": placed,
        "held_count": sum(1 for c in chips if c.get("held")),
        "consider_count": sum(1 for c in chips if not c.get("held")),
        "btc_count": btc_count,
        "stocks_count": stocks_count,
        "nav_usd": nav,
        "equity_market_value_usd": equity,
        "sleeve_weights_deployed": _as_dict(analysis.get("weights_of_deployed")),
        "sleeve_weights_nav": _as_dict(analysis.get("weights_of_nav")),
        "sleeve_budgets": {
            "btc_digital_credit_pct": btc_budget,
            "stocks_growth_pct": stocks_budget,
        },
        "targets": {
            "btc_digital_credit_pct": targets.get("btc_digital_credit_pct", btc_budget),
            "stocks_growth_pct": targets.get("stocks_growth_pct", stocks_budget),
            "band_pct": targets.get("band_pct"),
        },
        "policy": {
            "apr_apy_axis": False,
            "invented_targets": False,
            "held_is_book_weight": False,
            "consider_is_priority_share": False,
            "axis_is_new_money_consider_share": True,
            "book_pct_is_annotation": True,
            "role_score": dict(ROLE_SCORE),
            "private_watchlist_on_axis": False,
            "sleeve_targets_are_new_money_budget": True,
            "forbid_held_only": True,
            "consider_share_stamps_applied": bool(stamped),
            "consider_share_stamps_are_not_nav_targets": True,
            "consider_share_stamps_are_not_sleeve_targets": True,
            "consider_share_stamps_are_not_orders": True,
        },
        "consider_share_stamps": stamped,
        "notes": [
            "Chips = standing new-money consider-share from written policy.",
            "First split is sleeve budget (~40% BTC/digital-credit above, ~60% stocks/growth below).",
            "Then relative role inside the sleeve: preferred-core > core > watchlist high/med/low.",
            "Current book % is annotation only (held badge). Not an order ticket.",
            "Private watchlist stays off-axis.",
            *(
                [
                    "TSLA/SPCX 10% chips are consideration-list stamps (residual mix), "
                    "not live NAV/sleeve targets and not orders. Flatten-only: no trim, "
                    "no top-up. Monday residual stays theme-gap."
                ]
                if stamped
                else []
            ),
        ],
    }
