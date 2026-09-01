"""Position bias spectrum for FCC.

Two-lane axis like Interest Spectrum, but the unit is relative weight,
not APR/APY:

  above = held book % of deployed equity (live fund_manager.analysis)
  below = unheld watchlist consider-set relative priority share

Priority share uses the existing watchlist priority field only:
high=3, med/medium=2, low=1, missing=1. That is relative consideration
among the consider-set — not capital, not an invented target weight.

No per-name target weights. Sleeve 40/60 is legend only.
Private watchlist stays off-axis (not deployable).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
FM_SNAPSHOT = ROOT / "treasury" / "snapshots" / "fund_manager_latest.json"
TREASURY_FCC = ROOT / "financial-command" / "treasury_latest.json"
TREASURY_SNAP = ROOT / "treasury" / "snapshots" / "treasury_latest.json"

PRIORITY_SCORE = {
    "high": 3.0,
    "med": 2.0,
    "medium": 2.0,
    "low": 1.0,
}
DEFAULT_PRIORITY_SCORE = 1.0
ALLOWED_KINDS = ("held", "consider")
SLEEVE_BTC = "btc_digital_credit"
SLEEVE_STOCKS = "stocks_growth"


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
    snap = _load_json(FM_SNAPSHOT)
    if _analysis_from_fm(snap):
        return snap
    tre = treasury if isinstance(treasury, dict) else _load_json(
        TREASURY_FCC if TREASURY_FCC.is_file() else TREASURY_SNAP
    )
    fm = tre.get("fund_manager") if isinstance(tre.get("fund_manager"), dict) else {}
    return fm if _analysis_from_fm(fm) else {}


def _priority_score(raw: Any) -> Tuple[float, str, bool]:
    key = str(raw or "").strip().lower()
    if key in PRIORITY_SCORE:
        return PRIORITY_SCORE[key], key if key != "medium" else "med", False
    return DEFAULT_PRIORITY_SCORE, "low", True


def _held_chips(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    positions = [p for p in _as_list(analysis.get("positions")) if isinstance(p, dict)]
    equity = _money(analysis.get("equity_market_value_usd"))
    if not equity or equity <= 0:
        equity = 0.0
        for p in positions:
            mv = _money(p.get("market_value"))
            if mv and mv > 0:
                equity += mv
    chips: List[Dict[str, Any]] = []
    if not equity or equity <= 0:
        return chips
    for p in positions:
        sym = _sym(p.get("symbol"))
        mv = _money(p.get("market_value"))
        if not sym or mv is None or mv <= 0:
            continue
        pct = round(100.0 * mv / equity, 2)
        sleeve = str(p.get("sleeve") or "other")
        chips.append(
            {
                "id": f"held-{sym}",
                "symbol": sym,
                "label": sym,
                "venue": sym,
                "kind": "held",
                "lane": "above",
                "sleeve": sleeve,
                "weight_pct": pct,
                "weight_basis": "pct_of_deployed_equity",
                "market_value": round(mv, 4),
                "quantity": _money(p.get("quantity")),
                "source": "books",
                "held": True,
                "notes": "Live agentic book weight. Not a target.",
                "deep_link": "watchlist.html",
            }
        )
    chips.sort(key=lambda c: (-float(c["weight_pct"]), c["symbol"]))
    return chips


def _consider_chips(analysis: Dict[str, Any], held_symbols: set[str]) -> List[Dict[str, Any]]:
    wl = _as_dict(analysis.get("watchlist"))
    entries = [e for e in _as_list(wl.get("entries")) if isinstance(e, dict)]
    scored: List[Tuple[Dict[str, Any], float, str, bool]] = []
    for e in entries:
        sym = _sym(e.get("symbol"))
        if not sym or sym in held_symbols:
            continue
        score, pri, defaulted = _priority_score(e.get("priority"))
        scored.append((e, score, pri, defaulted))
    total = sum(s for _, s, _, _ in scored)
    chips: List[Dict[str, Any]] = []
    if total <= 0:
        return chips
    for e, score, pri, defaulted in scored:
        sym = _sym(e.get("symbol"))
        pct = round(100.0 * score / total, 2)
        sleeve = str(e.get("sleeve_if_owned") or e.get("sleeve") or "other")
        notes = (
            f"Consider-set share from watchlist priority {pri}"
            f"{' (default low)' if defaulted else ''} "
            f"— not capital, not a target weight."
        )
        chips.append(
            {
                "id": f"consider-{sym}",
                "symbol": sym,
                "label": e.get("name") or sym,
                "venue": sym,
                "kind": "consider",
                "lane": "below",
                "sleeve": sleeve,
                "weight_pct": pct,
                "weight_basis": "priority_share",
                "priority": pri,
                "priority_score": score,
                "theme": e.get("theme"),
                "status": e.get("status"),
                "source": "watchlist_priority",
                "held": False,
                "notes": notes,
                "deep_link": "watchlist.html",
            }
        )
    chips.sort(key=lambda c: (-float(c["weight_pct"]), c["symbol"]))
    return chips


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


def build_bias_spectrum(
    *,
    fund_manager: Optional[Dict[str, Any]] = None,
    treasury: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble held / consider chips on a shared 0→max% two-lane axis."""
    treasury_from_disk = not isinstance(treasury, dict)
    if treasury_from_disk:
        treasury = _load_json(TREASURY_FCC if TREASURY_FCC.is_file() else TREASURY_SNAP)
    fm = _load_fund_manager(fund_manager, treasury)
    analysis = _analysis_from_fm(fm)
    held = _held_chips(analysis)
    held_syms = {c["symbol"] for c in held}
    consider = _consider_chips(analysis, held_syms)
    chips = held + consider
    for chip in chips:
        if chip.get("kind") not in ALLOWED_KINDS:
            chip["kind"] = "held" if chip.get("held") else "consider"
        chip["lane"] = "above" if chip["kind"] == "held" else "below"
    placed = [c for c in chips if c.get("weight_pct") is not None]
    max_pct = _axis_max(placed)
    targets = _as_dict(analysis.get("targets")) or _as_dict(
        _as_dict(fm.get("policy_summary")).get("targets")
    )
    nav = _money(analysis.get("nav_usd"))
    equity = _money(analysis.get("equity_market_value_usd"))
    error = None
    if not analysis:
        error = "no fund_manager.analysis — refresh RH snapshot on Mac"
    return {
        "ok": True,
        "title": "Bias Spectrum",
        "brand": "FCC",
        "as_of": analysis.get("as_of") or fm.get("as_of") or _now(),
        "error": error,
        "axis": {
            "layout": "two_lane",
            "left": "0%",
            "right": f"~{int(max_pct)}%",
            "min_pct": 0.0,
            "max_pct": max_pct,
            "held_lane": "above",
            "consider_lane": "below",
            "unit": "relative_weight_pct",
            "ticks": _ticks(max_pct),
        },
        "chips": chips,
        "placed": placed,
        "held_count": len(held),
        "consider_count": len(consider),
        "nav_usd": nav,
        "equity_market_value_usd": equity,
        "sleeve_weights_deployed": _as_dict(analysis.get("weights_of_deployed")),
        "sleeve_weights_nav": _as_dict(analysis.get("weights_of_nav")),
        "targets": {
            "btc_digital_credit_pct": targets.get("btc_digital_credit_pct"),
            "stocks_growth_pct": targets.get("stocks_growth_pct"),
            "band_pct": targets.get("band_pct"),
        },
        "policy": {
            "apr_apy_axis": False,
            "invented_targets": False,
            "held_is_book_weight": True,
            "consider_is_priority_share": True,
            "priority_score": dict(PRIORITY_SCORE),
            "private_watchlist_on_axis": False,
            "sleeve_targets_are_legend_only": True,
        },
        "notes": [
            "Held chips = % of deployed agentic equity (live books).",
            "Consider chips = relative watchlist priority share among unheld names.",
            "Do not read consider % as a capital allocation.",
            "Sleeve 40/60 is the only target mix; no per-name targets.",
        ],
    }
