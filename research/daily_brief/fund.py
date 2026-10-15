"""Fund watch block for the Daily Brief (#718 handover of the fund digest).

Pure and deterministic: stdlib only, no network, no credentials. The page only
renders the ``fund`` block stored in the edition, same as ``todos`` and
``mac_tasks``. This module assembles that block from inputs the generator reads:

* ``investment/consider_share.json``: the monitored set (every symbol the file
  names). Only the symbols in ``pins`` are *pinned*; the rest are *watch only*.
* Naka's daily notes file (``fund_notes.load_notes``): the developments, relevance
  lines, direction and actions text are **his**. This code never writes news,
  relevance judgments or direction, and never fills in a day he has not filed.
* holdings/drift, fund status and the newest ``naka-actions.md`` entry
  (``fund_status``).
* a macro file that Meridian fills (``fund-macro.json``); ships empty and is
  rendered as a labeled placeholder on a held fixture.

Rules: no up/down reads, no "directed to" text; the live flag and #1093 state
are context only, never permission; nothing says buy or sell.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FUND_SCHEMA = "daily_brief_fund_v1"
MACRO_SCHEMA = "daily_brief_fund_macro_v0"
FUND_TITLE = "Fund watch"
MAX_TEXT = 300

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSIDER_SHARE_PATH = REPO_ROOT / "investment" / "consider_share.json"
MACRO_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "fund-macro.json"
MACRO_FILENAME = "fund-macro.json"

CONTEXT_NOTE = "Context only. Not a trade signal."
RELEVANCE_PINNED = "pinned"
RELEVANCE_WATCH = "watch only"

MACRO_LABEL = "Horizon read"
MACRO_SOURCE = "Meridian"
MACRO_TOPICS: tuple[tuple[str, str], ...] = (
    ("btc_crypto", "BTC / crypto"),
    ("rates_fomc", "Rates / FOMC"),
    ("ai_infra", "AI infrastructure"),
)
MACRO_PLACEHOLDER = "Awaiting Meridian. No macro line supplied, so nothing is shown here."

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ------------------------------------------------------------------ helpers


def _is_http(url: Any) -> bool:
    return isinstance(url, str) and url.startswith(("https://", "http://"))


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _now(now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _clip(text: Any, n: int = MAX_TEXT) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "\u2026"


def _load_json(path: Path | str | None) -> Any:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------ monitored set


def monitored_set(consider_share: dict) -> list[dict[str, Any]]:
    """Every ticker named in consider_share.json, pins first.

    Sources inside the file: ``pins`` keys, ``reallocate`` keys and targets,
    ``guardrails.stocks_sleeve_only_symbols`` and ``loop_adjustments`` keys.
    Only symbols that have a positive numeric pin are *pinned*.
    """
    cs = consider_share if isinstance(consider_share, dict) else {}
    pins_raw = cs.get("pins") if isinstance(cs.get("pins"), dict) else {}
    pins: dict[str, float] = {}
    for k, v in pins_raw.items():
        if isinstance(k, str) and _TICKER_RE.match(k) and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            pins[k] = float(v)
    names: set[str] = {k for k in pins_raw if isinstance(k, str) and _TICKER_RE.match(k)}
    realloc = cs.get("reallocate")
    if isinstance(realloc, dict):
        for k, v in realloc.items():
            if isinstance(k, str) and _TICKER_RE.match(k):
                names.add(k)
            for t in v if isinstance(v, list) else []:
                if isinstance(t, str) and _TICKER_RE.match(t):
                    names.add(t)
    guard = cs.get("guardrails")
    if isinstance(guard, dict):
        for t in guard.get("stocks_sleeve_only_symbols") or []:
            if isinstance(t, str) and _TICKER_RE.match(t):
                names.add(t)
    adj = cs.get("loop_adjustments")
    if isinstance(adj, dict):
        for k in adj:
            if isinstance(k, str) and _TICKER_RE.match(k):
                names.add(k)
    ordered = sorted(names, key=lambda s: (s not in pins, -pins.get(s, 0.0), s))
    return [{"symbol": s, "pinned": s in pins, "pin_pct": pins.get(s)} for s in ordered]


def load_consider_share(path: Path | str | None = None) -> dict:
    data = _load_json(path or CONSIDER_SHARE_PATH)
    return data if isinstance(data, dict) else {}


def relevance_line(m: dict[str, Any]) -> str:
    """One line per holding. 'pinned' for pinned symbols, 'watch only' for the rest."""
    if m.get("pinned"):
        pct = m.get("pin_pct")
        extra = f" ({pct:g}% new-money consider share)" if isinstance(pct, (int, float)) else ""
        return f"Allocation relevance: {RELEVANCE_PINNED}{extra}"
    return f"Allocation relevance: {RELEVANCE_WATCH}"


# --------------------------------------------------------------------- macro


def load_macro(path: Path | str | None = None) -> dict[str, Any]:
    """Macro block from Meridian's file; the held empty fixture when absent or unreadable."""
    data = _load_json(path) if path else None
    if data is None:
        data = _load_json(MACRO_FIXTURE_PATH)
    return normalize_macro(data)


def normalize_macro(data: Any) -> dict[str, Any]:
    """Fixed three topics, in order. Empty text means placeholder; nothing is invented."""
    d = data if isinstance(data, dict) else {}
    by_topic: dict[str, dict] = {}
    for ln in d.get("lines") if isinstance(d.get("lines"), list) else []:
        if isinstance(ln, dict) and ln.get("topic") in dict(MACRO_TOPICS) and ln["topic"] not in by_topic:
            by_topic[ln["topic"]] = ln
    lines = []
    for topic, label in MACRO_TOPICS:
        ln = by_topic.get(topic) or {}
        text = _clip(ln.get("text"))
        link = ln.get("link") if _is_http(ln.get("link")) else None
        a = ln.get("as_of")
        asof = a if isinstance(a, str) and (_parse_ts(a) or _DATE_RE.match(a)) else None
        lines.append({"topic": topic, "label": label, "text": text, "link": link if text else None, "as_of": asof if text else None})
    filled = sum(1 for ln in lines if ln["text"])
    return {
        "label": MACRO_LABEL,
        "source": MACRO_SOURCE,
        "held": bool(d.get("held", True)),
        "as_of": d.get("as_of") if isinstance(d.get("as_of"), str) and d.get("as_of") else None,
        "state": "items" if filled else "placeholder",
        "placeholder": MACRO_PLACEHOLDER,
        "lines": lines,
    }


# --------------------------------------------------------------------- build


def build_fund(
    consider_share: dict,
    *,
    notes: Any = None,
    macro: Any = None,
    holdings: Any = None,
    status: Any = None,
    naka: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the edition's ``fund`` block. Same inputs and ``now`` give the same block.

    ``notes`` is ``fund_notes.load_notes`` output; ``holdings``/``status``/``naka`` come from
    ``fund_status``. Any absent input renders as a "no data" or "not filed" state.
    """
    n = _now(now)
    monitored = monitored_set(consider_share)
    notes = notes if isinstance(notes, dict) else {"state": "missing", "note": "Naka's fund notes not yet filed.", "edition_date": None}
    shown = notes.get("state") == "filed"
    devs = notes.get("developments") if shown and isinstance(notes.get("developments"), list) else []
    file_rel = {r["ticker"]: r["word"] for r in (notes.get("relevance") or [])} if shown else {}
    mon_syms = {m["symbol"] for m in monitored}
    rows = []
    for m in monitored:
        row_devs = [d for d in devs if d.get("ticker") == m["symbol"]]
        file_word = file_rel.get(m["symbol"])
        row = {
            "symbol": m["symbol"],
            "relevance": RELEVANCE_PINNED if m["pinned"] else RELEVANCE_WATCH,
            "relevance_line": relevance_line(m),
            "developments": row_devs,
        }
        if file_word and file_word != row["relevance"]:
            row["file_relevance_disagrees"] = file_word  # consider_share.json wins; flag the file
        rows.append(row)
    other = []
    seen: set[str] = set()
    for d in devs:
        t = d.get("ticker")
        if t and t not in mon_syms and t not in seen:
            seen.add(t)
            word = file_rel.get(t)
            other.append({"symbol": t, "file_relevance": word, "developments": [x for x in devs if x.get("ticker") == t]})
    return {
        "schema": FUND_SCHEMA,
        "generated_at": n.isoformat(timespec="seconds"),
        "note": CONTEXT_NOTE,
        "consider_share_as_of": consider_share.get("as_of") if isinstance(consider_share, dict) and isinstance(consider_share.get("as_of"), str) else None,
        "notes": notes,
        "monitored": rows,
        "other_tickers": other,
        "holdings": holdings if isinstance(holdings, dict) else {"state": "no_data", "note": "No data: holdings not supplied.", "rows": [], "sleeves": []},
        "status": status if isinstance(status, dict) else None,
        "naka": naka if isinstance(naka, dict) else {"state": "none", "note": "No Naka entry on file yet."},
        "macro": normalize_macro(macro) if macro is not None else load_macro(None),
    }


# ------------------------------------------------------------------ validate


def validate_fund(fund: Any) -> list[str]:
    """Optional ``fund`` block; absent/null is valid."""
    if fund is None:
        return []
    if not isinstance(fund, dict):
        return ["fund: object"]
    p: list[str] = []
    if fund.get("generated_at") not in (None, "") and _parse_ts(fund.get("generated_at")) is None:
        p.append("fund.generated_at: ISO-8601 timestamp or null")
    mon = fund.get("monitored", [])
    if not isinstance(mon, list):
        p.append("fund.monitored: list")
        mon = []
    for i, row in enumerate(mon):
        w = f"fund.monitored[{i}]"
        if not isinstance(row, dict) or not isinstance(row.get("symbol"), str) or not row["symbol"].strip():
            p.append(f"{w}.symbol: required string")
            continue
        if row.get("relevance") not in (RELEVANCE_PINNED, RELEVANCE_WATCH):
            p.append(f"{w}.relevance: 'pinned' or 'watch only'")
        for j, d in enumerate(row.get("developments") or []):
            wj = f"{w}.developments[{j}]"
            if not isinstance(d, dict) or not isinstance(d.get("text"), str) or not d["text"].strip():
                p.append(f"{wj}.text: required string")
            elif d.get("url") not in (None, "") and not _is_http(d.get("url")):
                p.append(f"{wj}.url: http(s) source link or null")
    notes = fund.get("notes")
    if notes is not None and (not isinstance(notes, dict) or notes.get("state") not in ("filed", "missing", "mismatch", "invalid")):
        p.append("fund.notes: object with state filed|missing|mismatch|invalid")
    h = fund.get("holdings")
    if h is not None and (not isinstance(h, dict) or h.get("state") not in ("ok", "no_data") or not isinstance(h.get("rows", []), list)):
        p.append("fund.holdings: object with state ok|no_data and rows list")
    st = fund.get("status")
    if st is not None and not isinstance(st, dict):
        p.append("fund.status: object")
    nk = fund.get("naka")
    if nk is not None and (not isinstance(nk, dict) or nk.get("state") not in ("entry", "none")):
        p.append("fund.naka: object with state entry|none")
    macro = fund.get("macro")
    if macro is not None:
        if not isinstance(macro, dict) or not isinstance(macro.get("lines", []), list):
            p.append("fund.macro: object with lines list")
        else:
            for j, ln in enumerate(macro.get("lines") or []):
                if not isinstance(ln, dict) or not isinstance(ln.get("text", ""), str):
                    p.append(f"fund.macro.lines[{j}]: object with text")
                elif ln.get("link") not in (None, "") and not _is_http(ln.get("link")):
                    p.append(f"fund.macro.lines[{j}].link: http(s) URL or null")
    return p
