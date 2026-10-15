"""Holdings/drift, fund status and Naka-actions inputs for the Fund watch block.

* ``holdings_block``: live weights and drift of the agentic book against the
  50/50 stocks-sleeve pins, from the Robinhood snapshot the #518 producer keeps
  on prism (``treasury/snapshots/robinhood_latest.json``). Sleeve math is the
  repo's own ``treasury.fund_manager.analyze_agentic_book``. A snapshot older
  than the stale limit (``automation.rh_stale_warn_hours`` in
  ``investment/fund_manager.json``, default 6) or a missing one yields a clear
  ``no_data`` state that still shows the snapshot's ``as_of``.
* ``status_block``: ``fund_manager.live`` and the open/closed state of #1093,
  context only. Neither is permission to trade and neither is a signal.
* ``parse_naka_actions``: newest ``## Naka: actions taken`` entry from the
  append-only ``investment/naka-actions.md``.

Weights are percentages only. No dollar balances, quantities or account ids
reach the block, because the Markdown copy is committed to a public repo.
Position weights use quantity x price from the snapshot (a ``last_price``/``price``
if present, else average buy price, the same estimate the fund manager uses), so
they are labeled as an estimate unless the snapshot carries live prices.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from research.daily_brief.fund import _clip, _now, _parse_ts

ISSUE_NO = 1093
ISSUE_URL = f"https://github.com/cvolkernick/personal-workspace/issues/{ISSUE_NO}"
STALE_HOURS_DEFAULT = 6.0
STATUS_NOTE = "Status for context only. Not permission to trade, and not a trade signal."
NAKA_HEADING = "## Naka: actions taken"
MAX_NAKA_CHARS = 1500

_PRICE_KEYS = ("last_price", "price", "mark_price")


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _pct(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x * 100.0, nd)


def stale_limit_hours(policy: Any) -> float:
    auto = policy.get("automation") if isinstance(policy, dict) else None
    v = _num(auto.get("rh_stale_warn_hours")) if isinstance(auto, dict) else None
    return v if v and v > 0 else STALE_HOURS_DEFAULT


def _no_data(note: str, as_of: str | None, age: float | None, limit: float) -> dict[str, Any]:
    return {"state": "no_data", "note": note, "as_of": as_of, "age_hours": age,
            "stale_after_hours": limit, "rows": [], "sleeves": []}


def holdings_block(
    snapshot: Any,
    policy: Any,
    consider_share: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Weights and drift for the agentic book; ``no_data`` when missing or stale."""
    n = _now(now)
    limit = stale_limit_hours(policy)
    agentic = snapshot.get("agentic") if isinstance(snapshot, dict) and isinstance(snapshot.get("agentic"), dict) else None
    if agentic is None:
        return _no_data("No data: Robinhood agentic snapshot not found.", None, None, limit)
    as_of_raw = agentic.get("as_of") if isinstance(agentic.get("as_of"), str) else None
    as_of = _parse_ts(as_of_raw)
    if as_of is None:
        return _no_data("No data: snapshot has no as_of, so freshness cannot be checked.", None, None, limit)
    as_of_utc = as_of.astimezone(timezone.utc)
    age = round((n - as_of_utc).total_seconds() / 3600.0, 1)
    as_of_iso = as_of_utc.isoformat(timespec="seconds")
    if age > limit:
        return _no_data(
            f"No data: Robinhood snapshot is stale (as_of {as_of_iso}, {age:g}h old; limit {limit:g}h).",
            as_of_iso, age, limit,
        )

    from treasury.fund_manager import analyze_agentic_book  # lazy: repo's own sleeve math

    analysis = analyze_agentic_book(snapshot, policy if isinstance(policy, dict) else None)
    if not analysis.get("ok"):
        return _no_data("No data: " + _clip(analysis.get("error") or "analysis failed", 120), as_of_iso, age, limit)

    nav = _num(agentic.get("total_value")) or 0.0
    pos_rows = [r for r in analysis.get("positions") or [] if isinstance(r, dict) and r.get("symbol")]
    priced = all(any(_num(p.get(k)) for k in _PRICE_KEYS) for p in (agentic.get("positions") or []) if isinstance(p, dict)) \
        and bool(agentic.get("positions"))
    pins_raw = consider_share.get("pins") if isinstance(consider_share, dict) and isinstance(consider_share.get("pins"), dict) else {}
    pins = {k: float(v) for k, v in pins_raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0}
    pin_total = sum(pins.values())
    stocks_mv = sum(float(r["market_value"]) for r in pos_rows if r.get("sleeve") == "stocks_growth")

    rows = []
    for r in sorted(pos_rows, key=lambda r: (-float(r["market_value"]), r["symbol"])):
        sym, mv = r["symbol"], float(r["market_value"])
        row: dict[str, Any] = {
            "symbol": sym,
            "sleeve": r.get("sleeve"),
            "pinned": sym in pins,
            "weight_nav_pct": _pct(mv / nav) if nav > 0 else None,
        }
        if sym in pins and pin_total > 0 and stocks_mv > 0:
            tgt = pins[sym] / pin_total * 100.0
            w = mv / stocks_mv * 100.0
            row["stocks_sleeve_weight_pct"] = round(w, 1)
            row["target_pct"] = round(tgt, 1)
            row["drift_pp"] = round(w - tgt, 1)
        rows.append(row)
    for sym in sorted(pins, key=lambda s: (-pins[s], s)):  # pinned but not held: weight 0, full drift
        if sym not in {r["symbol"] for r in rows} and pin_total > 0 and stocks_mv > 0:
            tgt = pins[sym] / pin_total * 100.0
            rows.append({"symbol": sym, "sleeve": "stocks_growth", "pinned": True, "weight_nav_pct": 0.0,
                         "stocks_sleeve_weight_pct": 0.0, "target_pct": round(tgt, 1), "drift_pp": round(-tgt, 1)})

    tg = analysis.get("targets") or {}
    wd, dd, ib = analysis.get("weights_of_deployed") or {}, analysis.get("drift_deployed") or {}, analysis.get("in_band") or {}
    sleeves = []
    for key, label, tkey in (("btc_digital_credit", "BTC & digital credit", "btc_digital_credit_pct"),
                             ("stocks_growth", "Stocks / growth", "stocks_growth_pct")):
        if wd.get(key) is None:
            continue
        sleeves.append({
            "sleeve": key, "label": label,
            "weight_deployed_pct": _pct(wd.get(key)), "target_pct": _pct(_num(tg.get(tkey))),
            "drift_pp": _pct(dd.get(key)), "in_band": ib.get(key),
        })
    return {
        "state": "ok",
        "note": "" if priced else "Weights are an estimate: the snapshot has no live per-position prices, so cost basis stands in for price.",
        "as_of": as_of_iso,
        "age_hours": age,
        "stale_after_hours": limit,
        "basis": "snapshot prices" if priced else "cost-basis estimate",
        "stocks_target": "pins from consider_share.json" if pins else "no pins",
        "cash_weight_pct": _pct((_num(agentic.get("cash")) or 0.0) / nav) if nav > 0 else None,
        "rows": rows,
        "sleeves": sleeves,
    }


# -------------------------------------------------------------------- status


def status_block(policy: Any, issue: Any = None) -> dict[str, Any]:
    """fund_manager.live plus the #1093 issue state. ``issue`` = GitHub issue JSON, or None for no data."""
    p = policy if isinstance(policy, dict) else {}
    live = p.get("live") if isinstance(p.get("live"), bool) else None
    if isinstance(issue, dict) and issue.get("state") in ("open", "closed"):
        iss = {
            "number": ISSUE_NO, "state": issue["state"], "url": ISSUE_URL,
            "title": _clip(issue.get("title"), 160) or None,
            "updated_at": issue.get("updated_at") if isinstance(issue.get("updated_at"), str) else None,
        }
    else:
        iss = {"number": ISSUE_NO, "state": "no_data", "url": ISSUE_URL, "title": None, "updated_at": None,
               "note": "No data: GitHub was not reachable."}
    return {
        "fund_manager": {"live": live, "status": p.get("status") if isinstance(p.get("status"), str) else None,
                         "source": "investment/fund_manager.json"},
        "issue": iss,
        "note": STATUS_NOTE,
    }


# ---------------------------------------------------------------------- naka

_HEAD_RE = re.compile(r"^## Naka: actions taken\s*(?:[\u2014\u2013-]\s*(\d{4}-\d{2}-\d{2}))\s*$")


def parse_naka_actions(text: Any) -> dict[str, Any]:
    """Newest dated ``## Naka: actions taken`` entry (ignores fenced examples).

    Returns ``{state: "entry", date, text}`` or ``{state: "none", note}``. Newest means the
    latest date; on a tie, the later entry in the file.
    """
    if not isinstance(text, str):
        return {"state": "none", "note": "No Naka entries file found."}
    entries: list[tuple[str, int, list[str]]] = []
    cur: tuple[str, int, list[str]] | None = None
    fenced = False
    for i, line in enumerate(text.splitlines()):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            if cur:
                cur[2].append(line)
            continue
        m = None if fenced else _HEAD_RE.match(line.rstrip())
        if m:
            try:
                datetime.strptime(m.group(1), "%Y-%m-%d")
            except ValueError:
                m = None
        if m:
            cur = (m.group(1), i, [])
            entries.append(cur)
        elif not fenced and line.startswith("## "):
            cur = None  # some other heading ends the entry
        elif cur:
            cur[2].append(line)
    if not entries:
        return {"state": "none", "note": "No Naka entry on file yet."}
    date_s, _, body = max(entries, key=lambda e: (e[0], e[1]))
    body_s = "\n".join(body).strip()
    if len(body_s) > MAX_NAKA_CHARS:
        body_s = body_s[: MAX_NAKA_CHARS - 1].rstrip() + "\u2026"
    return {"state": "entry", "date": date_s, "text": body_s or "(empty entry)"}


# ---------------------------------------------------------------- issue state

ISSUE_API = "https://api.github.com/repos/cvolkernick/personal-workspace/issues/{n}"


def gather_issue(number: int = ISSUE_NO, *, fetch: Callable[[str], str | None] | None = None) -> dict[str, Any] | None:
    """Public GitHub issue JSON (state/title/updated_at), or None when unreachable. No token used."""
    url = ISSUE_API.format(n=number)
    if fetch is None:
        from urllib.request import Request, urlopen

        def fetch(u: str) -> str | None:  # noqa: F811
            try:
                req = Request(u, headers={"User-Agent": "DailyBrief/1 (+fund watch)", "Accept": "application/vnd.github+json"})
                with urlopen(req, timeout=12) as r:
                    return r.read().decode("utf-8", errors="replace")
            except (OSError, ValueError):
                return None
    body = fetch(url)
    if not body:
        return None
    try:
        d = json.loads(body)
    except ValueError:
        return None
    return d if isinstance(d, dict) and d.get("state") in ("open", "closed") else None
