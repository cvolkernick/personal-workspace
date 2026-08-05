#!/usr/bin/env python3
"""Sync Personal Expense Google Sheet into treasury snapshots for FCC.

Sheet: Personal Expense Sheet
  Personal                   — estimated *upcoming* expenses (ballpark OK), due dates
                               and funding account (From). Forward-looking, not actual spend.
  Productive Discretionary   — capital outlay that grows productive asset base (priority).
                               Quantitative ops expansion; not expense burn.
  Consumer Discretionary     — consumer goods / personal wishlist (qualitative; lower priority).

Legacy tab name "Discretionary" is accepted as an alias for Productive Discretionary.

YNAB is the source of *actual* spending (esp. Coinbase One Card). Do not double-count.

Default fetch uses Google Sheets gviz CSV export (works when link-shared).

Usage:
  python3 treasury/expenses_sync.py
  python3 treasury/expenses_sync.py --offline
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import SNAPSHOTS_DIR, load_config, save_json  # noqa: E402

DEFAULT_SHEET_ID = "15ZU7843pTSLSEI0U-taFZ4Qwk3bTQx6cWh2Ex0d7NJQ"
DEFAULT_TABS = ("Personal", "Productive Discretionary", "Consumer Discretionary")
LEGACY_PRODUCTIVE_TAB = "Discretionary"
PRODUCTIVE_TAB = "Productive Discretionary"
CONSUMER_TAB = "Consumer Discretionary"



def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_money(val: Any) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("none", "nan", "-"):
        return None
    s = s.replace("$", "").replace(",", "").replace("%", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_pct(val: Any) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if s.endswith("%"):
        n = parse_money(s)
        return (n / 100.0) if n is not None else None
    n = parse_money(s)
    if n is None:
        return None
    return n / 100.0 if n > 1 else n


def fetch_sheet_csv(sheet_id: str, sheet_name: str, *, timeout: float = 30.0) -> str:
    """Fetch a tab as CSV via Google Visualization export."""
    q = urllib.parse.urlencode({"tqx": "out:csv", "sheet": sheet_name})
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "personal-workspace-fcc/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Sheet HTTP {e.code} for tab {sheet_name!r}") from e


def rows_from_csv(text: str) -> List[Dict[str, str]]:
    # Strip BOM / normalize
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        cleaned = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None}
        rows.append(cleaned)
    return rows


def parse_personal_rows(rows: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
    items: List[Dict[str, Any]] = []
    totals: Dict[str, float] = {}
    for row in rows:
        item = (row.get("Item") or "").strip()
        if not item:
            continue
        daily = parse_money(row.get("Daily"))
        weekly = parse_money(row.get("Weekly"))
        biweekly = parse_money(row.get("Bi-Weekly"))
        monthly = parse_money(row.get("Monthly"))
        annually = parse_money(row.get("Annually"))
        alloc = parse_pct(row.get("Budget Allocation"))
        if item.lower() == "total":
            totals = {
                "daily": daily or 0.0,
                "weekly": weekly or 0.0,
                "biweekly": biweekly or 0.0,
                "monthly": monthly or 0.0,
                "annually": annually or 0.0,
            }
            continue
        items.append(
            {
                "date": row.get("Date") or None,
                "from": row.get("From") or None,
                "item": item,
                "daily": daily,
                "weekly": weekly,
                "biweekly": biweekly,
                "monthly": monthly,
                "annually": annually,
                "budget_allocation": alloc,
            }
        )
    if not totals and items:
        totals = {
            "daily": sum(i["daily"] or 0 for i in items),
            "weekly": sum(i["weekly"] or 0 for i in items),
            "biweekly": sum(i["biweekly"] or 0 for i in items),
            "monthly": sum(i["monthly"] or 0 for i in items),
            "annually": sum(i["annually"] or 0 for i in items),
        }
    return items, totals


def parse_discretionary_rows(rows: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
    items: List[Dict[str, Any]] = []
    totals: Dict[str, float] = {}
    for row in rows:
        item = (row.get("Item") or "").strip()
        if not item:
            continue
        daily = parse_money(row.get("Daily"))
        weekly = parse_money(row.get("Weekly"))
        biweekly = parse_money(row.get("Bi-Weekly"))
        monthly = parse_money(row.get("Monthly"))
        annually = parse_money(row.get("Annually"))
        if item.lower() == "total":
            totals = {
                "daily": daily or 0.0,
                "weekly": weekly or 0.0,
                "biweekly": biweekly or 0.0,
                "monthly": monthly or 0.0,
                "annually": annually or 0.0,
            }
            continue
        items.append(
            {
                "item": item,
                "date": row.get("Date") or None,
                "daily": daily,
                "weekly": weekly,
                "biweekly": biweekly,
                "monthly": monthly,
                "annually": annually,
                "from": row.get("From") or None,
                "to": row.get("To") or None,
            }
        )
    if not totals and items:
        totals = {
            "daily": sum(i["daily"] or 0 for i in items),
            "weekly": sum(i["weekly"] or 0 for i in items),
            "biweekly": sum(i["biweekly"] or 0 for i in items),
            "monthly": sum(i["monthly"] or 0 for i in items),
            "annually": sum(i["annually"] or 0 for i in items),
        }
    return items, totals


def by_source(items: List[Dict[str, Any]]) -> Dict[str, float]:
    """Sum monthly amounts by funding source (From column)."""
    out: Dict[str, float] = {}
    for i in items:
        src = (i.get("from") or "Unspecified").strip() or "Unspecified"
        out[src] = out.get(src, 0.0) + float(i.get("monthly") or 0.0)
    return {k: round(v, 2) for k, v in sorted(out.items(), key=lambda x: -x[1])}


def top_items(items: List[Dict[str, Any]], n: int = 10) -> List[Dict[str, Any]]:
    ranked = sorted(items, key=lambda x: float(x.get("monthly") or 0), reverse=True)
    return [
        {
            "item": i["item"],
            "monthly": i.get("monthly"),
            "from": i.get("from"),
            "date": i.get("date"),
            "budget_allocation": i.get("budget_allocation"),
        }
        for i in ranked[:n]
        if (i.get("monthly") or 0) > 0
    ]


def parse_sheet_date(val: Any) -> Optional[datetime]:
    """Parse sheet dates like 7/17/2026, 07/17/26, or 2026-07-17 (UTC midnight)."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _upcoming_sorted(items: List[Dict[str, Any]], n: int = 20) -> List[Dict[str, Any]]:
    """Sort personal expenses by due date (chronological; missing dates last)."""
    far = datetime(9999, 12, 31, tzinfo=timezone.utc)

    def key(i: Dict[str, Any]):
        dt = parse_sheet_date(i.get("date"))
        if dt is None:
            return (1, far, i.get("item") or "")
        return (0, dt, i.get("item") or "")

    ranked = sorted(items, key=key)
    return [
        {
            "date": i.get("date"),
            "item": i.get("item"),
            "from": i.get("from"),
            "monthly": i.get("monthly"),
            "weekly": i.get("weekly"),
        }
        for i in ranked[:n]
    ]


def _tab_block(
    *,
    role: str,
    items: List[Dict[str, Any]],
    totals: Dict[str, float],
    kind: str = "targets",
) -> Dict[str, Any]:
    top = top_items(items, 12)
    block: Dict[str, Any] = {
        "role": role,
        "item_count": len(items),
        "totals": {k: round(v, 2) for k, v in totals.items()},
        "items": items,
    }
    if kind == "personal":
        block["by_source_monthly"] = by_source(items)
        block["top_monthly"] = top
        block["upcoming_by_date"] = _upcoming_sorted(items, 20)
    else:
        block["top_targets"] = top
        block["top_monthly"] = top  # alias for older UI
    return block


def build_expenses_snapshot(
    personal_csv: str,
    productive_csv: str = "",
    consumer_csv: Optional[str] = None,
    *,
    sheet_id: str,
    source: str = "google_sheets",
    # Backward-compat keyword used by older callers/tests
    discretionary_csv: Optional[str] = None,
) -> Dict[str, Any]:
    """Build expenses snapshot from Personal + Productive (+ optional Consumer) CSVs.

    ``productive_csv`` is preferred; ``discretionary_csv`` remains as a legacy alias
    for the productive tab content.
    """
    if discretionary_csv is not None and not productive_csv:
        productive_csv = discretionary_csv
    # Positional 2nd arg historically was Discretionary CSV
    personal_items, personal_totals = parse_personal_rows(rows_from_csv(personal_csv))
    prod_items, prod_totals = parse_discretionary_rows(
        rows_from_csv(productive_csv or "")
    )
    cons_items: List[Dict[str, Any]] = []
    cons_totals: Dict[str, float] = {
        "daily": 0.0,
        "weekly": 0.0,
        "biweekly": 0.0,
        "monthly": 0.0,
        "annually": 0.0,
    }
    if consumer_csv:
        cons_items, cons_totals = parse_discretionary_rows(rows_from_csv(consumer_csv))

    personal_monthly = personal_totals.get("monthly") or 0.0
    productive_monthly = prod_totals.get("monthly") or 0.0
    consumer_monthly = cons_totals.get("monthly") or 0.0
    # Capital targets for ops expansion = Productive only (not consumer wishlist)
    capital_target_monthly = productive_monthly

    cb_monthly = sum(
        float(i.get("monthly") or 0)
        for i in personal_items
        if (i.get("from") or "").lower().startswith("coinbase")
    )
    rh_monthly = sum(
        float(i.get("monthly") or 0)
        for i in personal_items
        if "rh" in (i.get("from") or "").lower() or "robinhood" in (i.get("from") or "").lower()
    )

    productive_block = _tab_block(
        role="productive_capital_outlay",
        items=prod_items,
        totals=prod_totals,
    )
    productive_block["priority"] = 1
    productive_block["label"] = PRODUCTIVE_TAB
    productive_block["description"] = (
        "Capital outlay that grows productive asset base (quantitative). "
        "Priority over Consumer Discretionary. Fund from collateralized margin."
    )

    consumer_block = _tab_block(
        role="consumer_wishlist",
        items=cons_items,
        totals=cons_totals,
    )
    consumer_block["priority"] = 2
    consumer_block["label"] = CONSUMER_TAB
    consumer_block["description"] = (
        "Consumer goods / personal wishlist (qualitative). "
        "Lower priority than Productive Discretionary. Not expense burn."
    )

    return {
        "source": source,
        "as_of": _now(),
        "sheet_id": sheet_id,
        "sheet_name": "Personal Expense Sheet",
        "semantics": {
            "personal": (
                "Estimated upcoming expenses (ballpark OK) with due dates and funding account. "
                "Forward-looking plan — not a record of what already spent."
            ),
            "productive_discretionary": (
                "Capital outlay that grows productive asset base. Quantitative targets. "
                "Priority over consumer wishlist. Not expense burn."
            ),
            "consumer_discretionary": (
                "Consumer goods / personal wishlist (qualitative). Lower priority. "
                "Not expense burn and not ops expansion capital targets."
            ),
            # Legacy key kept for older consumers
            "discretionary": (
                "Alias for Productive Discretionary (legacy name). "
                "Capital outlay that grows productive asset base — not expense burn."
            ),
            "actual_spend": "YNAB (and brokers) own realized transactions; do not double-count with Personal.",
            "priority_order": [
                "Personal expenses paid & current",
                "Productive Discretionary (margin-funded capex)",
                "Consumer Discretionary (wishlist; after productive)",
            ],
        },
        "tabs": {
            "Personal": _tab_block(
                role="upcoming_expense_estimates",
                items=personal_items,
                totals=personal_totals,
                kind="personal",
            ),
            PRODUCTIVE_TAB: productive_block,
            CONSUMER_TAB: consumer_block,
            # Backward-compatible alias → productive data (same object shape)
            LEGACY_PRODUCTIVE_TAB: {
                **productive_block,
                "role": "excess_capital_targets",  # legacy role string
                "alias_of": PRODUCTIVE_TAB,
            },
        },
        "summary": {
            "upcoming_expense_monthly": round(personal_monthly, 2),
            "personal_monthly": round(personal_monthly, 2),
            "personal_daily": round(personal_totals.get("daily") or 0.0, 2),
            "personal_weekly": round(personal_totals.get("weekly") or 0.0, 2),
            "personal_annually": round(personal_totals.get("annually") or 0.0, 2),
            # Productive = capital targets for ops expansion
            "capital_targets_monthly": round(capital_target_monthly, 2),
            "productive_discretionary_monthly": round(productive_monthly, 2),
            "discretionary_monthly": round(productive_monthly, 2),  # legacy alias → productive
            "discretionary_daily": round(prod_totals.get("daily") or 0.0, 2),
            "consumer_discretionary_monthly": round(consumer_monthly, 2),
            "consumer_discretionary_daily": round(cons_totals.get("daily") or 0.0, 2),
            # Burn = Personal only
            "combined_monthly": round(personal_monthly, 2),
            "combined_daily": round(personal_totals.get("daily") or 0.0, 2),
            "coinbase_funded_monthly": round(cb_monthly, 2),
            "rh_funded_monthly": round(rh_monthly, 2),
            "rh_checking_funded_monthly": round(rh_monthly, 2),
        },
        "notes": (
            "Personal = estimated future bills by pay-from account. "
            "Productive Discretionary = capital outlay growing productive assets "
            "(priority; margin-funded). "
            "Consumer Discretionary = wishlist / consumer goods (lower priority). "
            "Neither discretionary tab is expense burn. Actual spend = YNAB."
        ),
    }


def _resolve_tab_names(tabs: List[str]) -> Dict[str, str]:
    """Map logical roles → configured sheet tab names (with legacy fallbacks)."""
    personal = "Personal"
    productive = PRODUCTIVE_TAB
    consumer = CONSUMER_TAB
    for t in tabs:
        tl = (t or "").strip()
        low = tl.lower()
        if low == "personal":
            personal = tl
        elif "productive" in low and "discretionary" in low:
            productive = tl
        elif low == "discretionary" or (
            "discretionary" in low and "consumer" not in low and "productive" not in low
        ):
            # bare "Discretionary" = productive (legacy)
            productive = tl
        elif "consumer" in low and "discretionary" in low:
            consumer = tl
    return {
        "personal": personal,
        "productive": productive,
        "consumer": consumer,
    }


def _fetch_tab_optional(sheet_id: str, name: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (csv, error). Missing tab → (None, err) without raising."""
    try:
        return fetch_sheet_csv(sheet_id, name), None
    except Exception as e:
        return None, str(e)


def sync_expenses(
    *,
    sheet_id: Optional[str] = None,
    prefer_live: bool = True,
) -> Dict[str, Any]:
    cfg = load_config()
    gcfg = cfg.get("expenses_sheet") or cfg.get("google_sheet") or {}
    sid = sheet_id or gcfg.get("sheet_id") or DEFAULT_SHEET_ID
    tabs = list(gcfg.get("tabs") or list(DEFAULT_TABS))
    names = _resolve_tab_names(tabs)

    if not prefer_live:
        from treasury.adapters import load_json

        cached = load_json(SNAPSHOTS_DIR / "expenses_latest.json")
        if cached:
            cached = dict(cached)
            cached.setdefault("source", "snapshot")
            return cached
        return {
            "source": "empty",
            "as_of": _now(),
            "live_error": "no expenses snapshot and offline mode",
        }

    try:
        personal_csv = fetch_sheet_csv(sid, names["personal"])
        # Productive: try configured name, then legacy "Discretionary"
        productive_csv, prod_err = _fetch_tab_optional(sid, names["productive"])
        if productive_csv is None and names["productive"] != LEGACY_PRODUCTIVE_TAB:
            productive_csv, prod_err2 = _fetch_tab_optional(sid, LEGACY_PRODUCTIVE_TAB)
            if productive_csv is not None:
                prod_err = None
            else:
                prod_err = prod_err or prod_err2
        if productive_csv is None:
            raise RuntimeError(
                prod_err
                or f"Productive Discretionary tab not found ({names['productive']!r})"
            )
        consumer_csv, cons_err = _fetch_tab_optional(sid, names["consumer"])
        snap = build_expenses_snapshot(
            personal_csv,
            productive_csv,
            consumer_csv=consumer_csv,
            sheet_id=sid,
            source="google_sheets",
        )
        if cons_err and consumer_csv is None:
            snap.setdefault("tab_warnings", []).append(
                f"Consumer Discretionary tab unavailable: {cons_err}"
            )
        return snap
    except Exception as e:
        from treasury.adapters import load_json

        cached = load_json(SNAPSHOTS_DIR / "expenses_latest.json")
        if cached:
            out = dict(cached)
            out["live_error"] = str(e)
            out.setdefault("source", "snapshot")
            return out
        return {
            "source": "empty",
            "as_of": _now(),
            "live_error": str(e),
            "sheet_id": sid,
        }


def write_expenses_snapshot(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    out = path or (SNAPSHOTS_DIR / "expenses_latest.json")
    save_json(out, data)
    return out


def fetch_expenses(*, prefer_live: bool = True) -> Dict[str, Any]:
    data = sync_expenses(prefer_live=prefer_live)
    if data.get("source") not in (None, "empty") and not (
        data.get("live_error") and data.get("source") == "empty"
    ):
        if prefer_live and data.get("source") == "google_sheets":
            write_expenses_snapshot(data)
    return data


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sync Personal Expense Sheet for FCC")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--sheet-id", help="Override spreadsheet id")
    args = parser.parse_args(argv)
    data = sync_expenses(sheet_id=args.sheet_id, prefer_live=not args.offline)
    if data.get("source") == "empty" and data.get("live_error"):
        print(json.dumps({"ok": False, "error": data["live_error"]}, indent=2), file=sys.stderr)
        return 1
    path = write_expenses_snapshot(data)
    s = data.get("summary") or {}
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(path),
                "upcoming_expense_monthly": s.get("upcoming_expense_monthly")
                or s.get("personal_monthly"),
                "capital_targets_monthly": s.get("capital_targets_monthly")
                or s.get("discretionary_monthly"),
                "coinbase_funded_monthly": s.get("coinbase_funded_monthly"),
                "rh_checking_funded_monthly": s.get("rh_funded_monthly"),
                "items_upcoming": (data.get("tabs") or {}).get("Personal", {}).get("item_count"),
                "items_productive": (data.get("tabs") or {})
                .get(PRODUCTIVE_TAB, (data.get("tabs") or {}).get("Discretionary", {}))
                .get("item_count"),
                "items_consumer": (data.get("tabs") or {})
                .get(CONSUMER_TAB, {})
                .get("item_count"),
                "items_capital_targets": (data.get("tabs") or {})
                .get(PRODUCTIVE_TAB, (data.get("tabs") or {}).get("Discretionary", {}))
                .get("item_count"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
