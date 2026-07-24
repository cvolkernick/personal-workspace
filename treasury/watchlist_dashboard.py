"""Aggregate watchlist + deep-dive reports for FCC watchlist dashboard."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_PATH = ROOT / "investment" / "watchlist.json"
RESEARCH_DIR = ROOT / "investment" / "research"
FM_POLICY = ROOT / "investment" / "fund_manager.json"
FM_SNAPSHOT = ROOT / "treasury" / "snapshots" / "fund_manager_latest.json"
RH_SNAPSHOT = ROOT / "treasury" / "snapshots" / "robinhood_latest.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _extract_section(md: str, heading_pat: str, max_chars: int = 2500) -> str:
    """Pull body under a markdown heading until next same-or-higher level heading."""
    m = re.search(heading_pat, md, flags=re.IGNORECASE | re.MULTILINE)
    if not m:
        return ""
    start = m.end()
    rest = md[start:]
    # Stop at next ## or # (but not ###)
    stop = re.search(r"\n#{1,2} ", rest)
    body = rest[: stop.start()] if stop else rest
    body = body.strip()
    if len(body) > max_chars:
        body = body[: max_chars - 1] + "…"
    return body


def _parse_deep_dive(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "path": str(path.relative_to(ROOT)) if path else None}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return {"exists": False, "error": str(e), "path": str(path.relative_to(ROOT))}

    # Status line like **Status:** `monitor`
    status_m = re.search(
        r"\*\*Status:\*\*\s*`?([^`\n*]+)`?", text, flags=re.IGNORECASE
    )
    as_of_m = re.search(
        r"\*\*(?:As of|Report run):\*\*\s*([^\n*]+)", text, flags=re.IGNORECASE
    )
    one_line = re.search(
        r"\*\*One-line conclusion[^*]*\*\*[^\n]*\n+([^\n#]+)",
        text,
        flags=re.IGNORECASE,
    )
    # Fallback: first line after ## Verdict
    verdict_sum = _extract_section(text, r"^##\s+Verdict[^\n]*\n", max_chars=1200)
    if not one_line and verdict_sum:
        for line in verdict_sum.splitlines():
            line = line.strip()
            if line and not line.startswith("|") and not line.startswith("#"):
                one_line_text = line
                break
        else:
            one_line_text = ""
    else:
        one_line_text = one_line.group(1).strip() if one_line else ""

    conclusions = _extract_section(
        text, r"^##\s+Conclusions[^\n]*\n|^###\s+Conclusions[^\n]*\n", max_chars=2000
    )
    if not conclusions:
        conclusions = _extract_section(
            text, r"^##\s+Executive findings[^\n]*\n", max_chars=2500
        )

    return {
        "exists": True,
        "path": str(path.relative_to(ROOT)),
        "as_of_report": (as_of_m.group(1).strip() if as_of_m else None),
        "status_line": (status_m.group(1).strip() if status_m else None),
        "one_line_conclusion": one_line_text or None,
        "executive_findings": _extract_section(
            text, r"^##\s+Executive findings[^\n]*\n", max_chars=3000
        )
        or None,
        "conclusions": conclusions or None,
        "thesis_fit_section": _extract_section(
            text, r"^##\s+Thesis fit[^\n]*\n", max_chars=1500
        )
        or None,
        "next_actions": _extract_section(
            text, r"^##\s+Next actions[^\n]*\n", max_chars=1500
        )
        or None,
        "char_count": len(text),
        "full_markdown": text,
    }


def _held_symbols() -> Dict[str, Any]:
    rh = _load_json(RH_SNAPSHOT)
    agentic = rh.get("agentic") if isinstance(rh.get("agentic"), dict) else {}
    held = []
    for p in agentic.get("positions") or []:
        if not isinstance(p, dict):
            continue
        sym = (p.get("symbol") or "").strip().upper()
        if sym:
            held.append(sym)
    return {
        "symbols": held,
        "account_last4": agentic.get("account_number_last4"),
        "as_of": rh.get("as_of") or agentic.get("as_of"),
    }


def build_watchlist_dashboard() -> Dict[str, Any]:
    wl = _load_json(WATCHLIST_PATH)
    policy = _load_json(FM_POLICY)
    fm_snap = _load_json(FM_SNAPSHOT)
    held_info = _held_symbols()
    held_set = set(held_info["symbols"])

    entries_out: List[Dict[str, Any]] = []
    for e in wl.get("entries") or []:
        if not isinstance(e, dict):
            continue
        sym = (e.get("symbol") or "").strip().upper()
        if not sym:
            continue
        rel = e.get("last_deep_dive_path") or f"investment/research/{sym}_deep_dive.md"
        dive_path = ROOT / rel
        if not dive_path.is_file():
            # try standard path
            dive_path = RESEARCH_DIR / f"{sym}_deep_dive.md"
        dive = _parse_deep_dive(dive_path)
        # Don't ship full markdown in list payload (size); deep-dive endpoint has it
        dive_summary = {k: v for k, v in dive.items() if k != "full_markdown"}

        sleeve = e.get("sleeve_if_owned") or "stocks_growth"
        theme = e.get("theme")
        entries_out.append(
            {
                "symbol": sym,
                "name": e.get("name"),
                "theme": theme,
                "themes": e.get("themes") or ([theme] if theme else []),
                "status": e.get("status") or "monitor",
                "priority": e.get("priority"),
                "sleeve_if_owned": sleeve,
                "thesis_fit": e.get("thesis_fit"),
                "added": e.get("added"),
                "added_by": e.get("added_by"),
                "deep_dive_required_before_buy": bool(
                    e.get("deep_dive_required_before_buy", True)
                ),
                "last_deep_dive": e.get("last_deep_dive"),
                "last_deep_dive_path": rel,
                "last_verdict": e.get("last_verdict"),
                "next_catalyst": e.get("next_catalyst"),
                "notes": e.get("notes"),
                "held_in_agentic": sym in held_set,
                "deep_dive": dive_summary,
                "strategy_fit": {
                    "book": "agentic_only",
                    "sleeve": sleeve,
                    "modernized_60_40": (
                        "~40% BTC/digital-credit complex · ~60% stocks/growth"
                    ),
                    "role": (
                        "Opportunistic satellite under stocks/growth"
                        if sleeve == "stocks_growth"
                        else f"Maps to sleeve {sleeve}"
                    ),
                    "auto_buy": False,
                    "core_allowlist": False,
                },
            }
        )

    # Sort: priority high first, then symbol
    pri = {"high": 0, "med": 1, "medium": 1, "low": 2}

    def sort_key(x: Dict[str, Any]) -> tuple:
        return (pri.get(str(x.get("priority") or "").lower(), 9), x.get("symbol") or "")

    entries_out.sort(key=sort_key)

    fm_wl = (fm_snap.get("analysis") or {}).get("watchlist") or policy.get("watchlist") or {}
    sleeves = (policy.get("sleeves") or {}) if policy else {}

    return {
        "ok": True,
        "as_of": _now(),
        "watchlist_as_of": wl.get("as_of"),
        "purpose": wl.get("purpose"),
        "policy": wl.get("policy") or {},
        "fund_policy": {
            "watchlist": policy.get("watchlist"),
            "allowlist_strict": (policy.get("allowlist") or {}).get("strict"),
            "energy_opportunistic": sleeves.get("energy_opportunistic"),
            "targets": policy.get("targets"),
        },
        "agentic_held": held_info,
        "fund_manager_watchlist": fm_wl,
        "entries": entries_out,
        "count": len(entries_out),
        "research_dir": "investment/research",
        "workflows": {
            "deep_dive": "position-deep-dive",
            "portfolio_research": "fund-manager-research",
        },
    }


def get_deep_dive_markdown(symbol: str) -> Dict[str, Any]:
    sym = (symbol or "").strip().upper()
    if not sym or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", sym):
        return {"ok": False, "error": "invalid symbol"}
    wl = _load_json(WATCHLIST_PATH)
    path = RESEARCH_DIR / f"{sym}_deep_dive.md"
    for e in wl.get("entries") or []:
        if isinstance(e, dict) and (e.get("symbol") or "").upper() == sym:
            rel = e.get("last_deep_dive_path")
            if rel:
                cand = ROOT / rel
                if cand.is_file():
                    path = cand
            break
    dive = _parse_deep_dive(path)
    return {
        "ok": bool(dive.get("exists")),
        "symbol": sym,
        "path": dive.get("path"),
        "markdown": dive.get("full_markdown") if dive.get("exists") else None,
        "summary": {k: v for k, v in dive.items() if k != "full_markdown"},
        "error": dive.get("error") or (None if dive.get("exists") else "deep dive not found"),
    }
