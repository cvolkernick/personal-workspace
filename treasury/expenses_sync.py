#!/usr/bin/env python3
"""Sync Personal Expense Google Sheet into treasury snapshots for FCC.

Sheet: Personal Expense Sheet
  Essential                  — estimated *upcoming* essential expenses (ballpark OK),
                               due dates and funding account (From). Not actual spend.
                               (Legacy tab title: "Personal" — still accepted.)
  Fleet                      — auto fleet ops (notes, insurance, DIMO, planned units).
                               Snapshot role fleet_ops. FCC burn adds only funded
                               Fleet lines whose name is not already on Essential.
                               Empty-From (planned) stays on the tab, out of burn.
  Collateral                 — collateral / productive capital investments (not burn).
  Productive Discretionary   — capital outlay that grows productive asset base (priority).
                               Not expense burn.
  Consumer Discretionary     — consumer goods / personal wishlist (lower priority).
                               Not expense burn.

Legacy tab names: "Personal" → Essential; "Discretionary" → Productive Discretionary.

YNAB is the source of *actual* spending (esp. Coinbase One Card). Do not double-count.

Fetch prefers export-by-gid (reliable multi-tab); falls back to gviz sheet name.

Usage:
  python3 treasury/expenses_sync.py
  python3 treasury/expenses_sync.py --offline
"""

from __future__ import annotations

import argparse
import csv
import io
import json
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
ESSENTIAL_TAB = "Essential"
# Pre-2026-08-10 sheet tab title; still accepted on fetch and as snapshot alias.
LEGACY_ESSENTIAL_TAB = "Personal"
FLEET_TAB = "Fleet"
COLLATERAL_TAB = "Collateral"
PRODUCTIVE_TAB = "Productive Discretionary"
CONSUMER_TAB = "Consumer Discretionary"
LEGACY_PRODUCTIVE_TAB = "Discretionary"
# Back-compat for importers that still reference PERSONAL_TAB
PERSONAL_TAB = ESSENTIAL_TAB

DEFAULT_TABS = (
    ESSENTIAL_TAB,
    FLEET_TAB,
    COLLATERAL_TAB,
    PRODUCTIVE_TAB,
    CONSUMER_TAB,
)

# Stable sheet gids (export?format=csv&gid=) — gviz by name often returns sheet 0 only.
DEFAULT_TAB_GIDS: Dict[str, str] = {
    ESSENTIAL_TAB: "0",
    LEGACY_ESSENTIAL_TAB: "0",
    FLEET_TAB: "1189472679",
    COLLATERAL_TAB: "1072275501",
    PRODUCTIVE_TAB: "1837986973",
    CONSUMER_TAB: "192074825",
}


def essential_tab_block(tabs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return Essential (or legacy Personal) tab block from an expenses snapshot."""
    t = tabs or {}
    block = t.get(ESSENTIAL_TAB) or t.get(LEGACY_ESSENTIAL_TAB) or {}
    return block if isinstance(block, dict) else {}


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


def fetch_sheet_csv(
    sheet_id: str,
    sheet_name: str,
    *,
    gid: Optional[str] = None,
    timeout: float = 30.0,
) -> str:
    """Fetch a tab as CSV. Prefer gid export; fall back to gviz by sheet name."""
    headers = {"User-Agent": "personal-workspace-fcc/1.0"}
    if gid is not None and str(gid).strip() != "":
        url = (
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/export"
            f"?format=csv&gid={urllib.parse.quote(str(gid))}"
        )
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Sheet HTTP {e.code} for tab {sheet_name!r} (gid={gid})"
            ) from e

    q = urllib.parse.urlencode({"tqx": "out:csv", "sheet": sheet_name})
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?{q}"
    req = urllib.request.Request(url, headers=headers)
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


def _empty_totals() -> Dict[str, float]:
    return {
        "daily": 0.0,
        "weekly": 0.0,
        "biweekly": 0.0,
        "monthly": 0.0,
        "annually": 0.0,
    }


def parse_personal_rows(rows: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
    """Parse expense-style tabs (Date, From, Item, Daily…Budget Allocation)."""
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
        quarterly = parse_money(row.get("Quarterly"))
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
            if quarterly is not None:
                totals["quarterly"] = quarterly
            continue
        rec: Dict[str, Any] = {
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
        if quarterly is not None:
            rec["quarterly"] = quarterly
        items.append(rec)
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
    """Parse capital-target tabs (Item, Date, Daily… From, To) or expense-layout capital."""
    # If tab uses expense layout (Date/From first with Budget Allocation), reuse personal parser.
    if rows:
        keys = {k for k in rows[0].keys() if k}
        if "Budget Allocation" in keys and "From" in keys and "Item" in keys:
            return parse_personal_rows(rows)

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


def normalize_item_name(val: Any) -> str:
    """Casefold + collapse whitespace for Essential/Fleet overlap checks."""
    return " ".join(str(val or "").strip().split()).casefold()


def item_has_from(item: Dict[str, Any]) -> bool:
    src = item.get("from")
    if src is None:
        return False
    return bool(str(src).strip())


def funded_unique_fleet_items(
    essential_items: List[Dict[str, Any]],
    fleet_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Fleet lines that enter FCC burn: have From, name not already on Essential.

    Empty-From (e.g. planned Rivian) stays on tabs.Fleet and stays out of
    combined_monthly. Name overlap is counted once on Essential.
    """
    seen = {
        normalize_item_name(i.get("item"))
        for i in essential_items
        if normalize_item_name(i.get("item"))
    }
    out: List[Dict[str, Any]] = []
    for i in fleet_items:
        name = normalize_item_name(i.get("item"))
        if not name or not item_has_from(i) or name in seen:
            continue
        out.append(i)
    return out


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
            **({"tab": i["tab"]} if i.get("tab") else {}),
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
    """Sort expenses by due date (chronological; missing dates last)."""
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
            **({"tab": i["tab"]} if i.get("tab") else {}),
        }
        for i in ranked[:n]
    ]


def _tag_items(items: List[Dict[str, Any]], tab: str) -> List[Dict[str, Any]]:
    out = []
    for i in items:
        rec = dict(i)
        rec["tab"] = tab
        out.append(rec)
    return out


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
    if kind == "expense":
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
    fleet_csv: Optional[str] = None,
    collateral_csv: Optional[str] = None,
    *,
    sheet_id: str,
    source: str = "google_sheets",
    # Backward-compat keyword used by older callers/tests
    discretionary_csv: Optional[str] = None,
) -> Dict[str, Any]:
    """Build expenses snapshot from Essential + optional Fleet/Collateral/disc tabs.

    ``productive_csv`` is preferred; ``discretionary_csv`` remains as a legacy alias
    for the productive tab content.
    """
    if discretionary_csv is not None and not productive_csv:
        productive_csv = discretionary_csv

    personal_items, personal_totals = parse_personal_rows(rows_from_csv(personal_csv))
    personal_items = _tag_items(personal_items, ESSENTIAL_TAB)

    fleet_items: List[Dict[str, Any]] = []
    fleet_totals = _empty_totals()
    if fleet_csv:
        fleet_items, fleet_totals = parse_personal_rows(rows_from_csv(fleet_csv))
        fleet_items = _tag_items(fleet_items, FLEET_TAB)

    coll_items: List[Dict[str, Any]] = []
    coll_totals = _empty_totals()
    if collateral_csv:
        coll_items, coll_totals = parse_discretionary_rows(rows_from_csv(collateral_csv))
        coll_items = _tag_items(coll_items, COLLATERAL_TAB)

    prod_items, prod_totals = parse_discretionary_rows(
        rows_from_csv(productive_csv or "")
    )
    prod_items = _tag_items(prod_items, PRODUCTIVE_TAB)

    cons_items: List[Dict[str, Any]] = []
    cons_totals = _empty_totals()
    if consumer_csv:
        cons_items, cons_totals = parse_discretionary_rows(rows_from_csv(consumer_csv))
        cons_items = _tag_items(cons_items, CONSUMER_TAB)

    personal_monthly = personal_totals.get("monthly") or 0.0
    fleet_monthly = fleet_totals.get("monthly") or 0.0
    coll_monthly = coll_totals.get("monthly") or 0.0
    productive_monthly = prod_totals.get("monthly") or 0.0
    consumer_monthly = cons_totals.get("monthly") or 0.0

    # Burn = Essential + funded unique Fleet. Empty-From / name overlap stay out.
    # Capital tabs never enter burn. tabs.Fleet still holds the full ops tab.
    fleet_burn_items = funded_unique_fleet_items(personal_items, fleet_items)
    burn_items = personal_items + fleet_burn_items
    fleet_burn_monthly = sum(float(i.get("monthly") or 0) for i in fleet_burn_items)
    fleet_burn_daily = sum(float(i.get("daily") or 0) for i in fleet_burn_items)
    burn_monthly = personal_monthly + fleet_burn_monthly
    burn_daily = (personal_totals.get("daily") or 0.0) + fleet_burn_daily
    # Capital targets for ops expansion = Productive only (not consumer / collateral)
    capital_target_monthly = productive_monthly

    cb_monthly = sum(
        float(i.get("monthly") or 0)
        for i in burn_items
        if (i.get("from") or "").lower().startswith("coinbase")
    )
    rh_monthly = sum(
        float(i.get("monthly") or 0)
        for i in burn_items
        if "rh" in (i.get("from") or "").lower() or "robinhood" in (i.get("from") or "").lower()
    )
    x_money_monthly = sum(
        float(i.get("monthly") or 0)
        for i in burn_items
        if "x money" in (i.get("from") or "").lower() or (i.get("from") or "").lower() == "x"
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

    collateral_block = _tab_block(
        role="collateral_investments",
        items=coll_items,
        totals=coll_totals,
    )
    collateral_block["priority"] = 1
    collateral_block["label"] = COLLATERAL_TAB
    collateral_block["description"] = (
        "Collateral / productive capital investments. Not expense burn. "
        "Separate from recurring Essential obligations and Fleet ops."
    )

    fleet_block = _tab_block(
        role="fleet_ops",
        items=fleet_items,
        totals=fleet_totals,
        kind="expense",
    )
    fleet_block["label"] = FLEET_TAB
    fleet_block["description"] = (
        "Auto fleet ops (loans, insurance, wash, DIMO, planned units). "
        "Full tab stays here. FCC combined_monthly adds only funded lines "
        "whose normalized name is not already on Essential; empty-From stays out."
    )

    essential_block = _tab_block(
        role="upcoming_expense_estimates",
        items=personal_items,
        totals=personal_totals,
        kind="expense",
    )
    essential_block["label"] = ESSENTIAL_TAB
    essential_block["description"] = (
        "Estimated upcoming essential expenses (ballpark OK) with due dates and funding account. "
        "Forward-looking plan — not actual spend."
    )

    return {
        "source": source,
        "as_of": _now(),
        "sheet_id": sheet_id,
        "sheet_name": "Personal Expense Sheet",
        "semantics": {
            "essential": (
                "Estimated upcoming essential expenses (ballpark OK) with due dates and funding account. "
                "Forward-looking plan — not a record of what already spent."
            ),
            "personal": (
                "Alias for Essential (legacy tab title). "
                "Estimated upcoming essential expenses — not actual spend."
            ),
            "fleet": (
                "Auto fleet ops (loans, insurance, DIMO, planned units). "
                "Role fleet_ops. FCC burn adds funded unique names only; "
                "empty-From (planned) and Essential name overlap stay out."
            ),
            "collateral": (
                "Collateral / capital investments. Not expense burn."
            ),
            "productive_discretionary": (
                "Capital outlay that grows productive asset base. Quantitative targets. "
                "Priority over consumer wishlist. Not expense burn."
            ),
            "consumer_discretionary": (
                "Consumer goods / personal wishlist (qualitative). Lower priority. "
                "Not expense burn and not ops expansion capital targets."
            ),
            "discretionary": (
                "Alias for Productive Discretionary (legacy name). "
                "Capital outlay that grows productive asset base — not expense burn."
            ),
            "actual_spend": "YNAB (and brokers) own realized transactions; do not double-count with Essential.",
            "priority_order": [
                "Essential + funded unique Fleet current (empty-From / name overlap out of burn)",
                "Collateral investments (as planned)",
                "Productive Discretionary (margin-funded capex)",
                "Consumer Discretionary (wishlist; after productive)",
            ],
        },
        "tabs": {
            ESSENTIAL_TAB: essential_block,
            FLEET_TAB: fleet_block,
            COLLATERAL_TAB: collateral_block,
            PRODUCTIVE_TAB: productive_block,
            CONSUMER_TAB: consumer_block,
            # Backward-compatible aliases
            LEGACY_ESSENTIAL_TAB: {
                **essential_block,
                "alias_of": ESSENTIAL_TAB,
            },
            LEGACY_PRODUCTIVE_TAB: {
                **productive_block,
                "role": "excess_capital_targets",  # legacy role string
                "alias_of": PRODUCTIVE_TAB,
            },
        },
        "summary": {
            # Burn = Essential + funded unique Fleet. fleet_monthly is the full tab.
            "upcoming_expense_monthly": round(burn_monthly, 2),
            "essential_monthly": round(personal_monthly, 2),
            "personal_monthly": round(personal_monthly, 2),  # legacy alias → essential
            "personal_daily": round(personal_totals.get("daily") or 0.0, 2),
            "essential_daily": round(personal_totals.get("daily") or 0.0, 2),
            "personal_weekly": round(personal_totals.get("weekly") or 0.0, 2),
            "personal_annually": round(personal_totals.get("annually") or 0.0, 2),
            "fleet_monthly": round(fleet_monthly, 2),
            "fleet_daily": round(fleet_totals.get("daily") or 0.0, 2),
            "fleet_annually": round(fleet_totals.get("annually") or 0.0, 2),
            # Collateral investments (not burn)
            "collateral_investments_monthly": round(coll_monthly, 2),
            "collateral_monthly": round(coll_monthly, 2),  # short alias
            # Productive = capital targets for ops expansion
            "capital_targets_monthly": round(capital_target_monthly, 2),
            "productive_discretionary_monthly": round(productive_monthly, 2),
            "discretionary_monthly": round(productive_monthly, 2),  # legacy alias → productive
            "discretionary_daily": round(prod_totals.get("daily") or 0.0, 2),
            "consumer_discretionary_monthly": round(consumer_monthly, 2),
            "consumer_discretionary_daily": round(cons_totals.get("daily") or 0.0, 2),
            # Burn aggregates
            "combined_monthly": round(burn_monthly, 2),
            "combined_daily": round(burn_daily, 2),
            "by_source_monthly": by_source(burn_items),
            "coinbase_funded_monthly": round(cb_monthly, 2),
            "rh_funded_monthly": round(rh_monthly, 2),
            "rh_checking_funded_monthly": round(rh_monthly, 2),
            "x_money_funded_monthly": round(x_money_monthly, 2),
        },
        "notes": (
            "Essential = estimated essential bills by pay-from account (legacy tab: Personal). "
            "Fleet = auto fleet ops (role fleet_ops). "
            "Collateral = collateral/capital investments (not burn). "
            "Productive Discretionary = capital outlay growing productive assets "
            "(priority; margin-funded). "
            "Consumer Discretionary = wishlist / consumer goods (lower priority). "
            "Burn = Essential + funded unique Fleet (empty-From and name overlap out). "
            "Actual spend = YNAB."
        ),
    }


def _resolve_tab_names(tabs: List[str]) -> Dict[str, str]:
    """Map logical roles → configured sheet tab names (with legacy fallbacks)."""
    names = {
        "personal": ESSENTIAL_TAB,  # logical role key stays "personal" for config compat
        "fleet": FLEET_TAB,
        "collateral": COLLATERAL_TAB,
        "productive": PRODUCTIVE_TAB,
        "consumer": CONSUMER_TAB,
    }
    for t in tabs:
        tl = (t or "").strip()
        low = tl.lower()
        if low in ("essential", "personal"):
            names["personal"] = tl
        elif low in ("fleet", "auto fleet", "auto fleet expenses"):
            names["fleet"] = tl
        elif low == "collateral" or low.startswith("collateral "):
            names["collateral"] = tl
        elif "productive" in low and "discretionary" in low:
            names["productive"] = tl
        elif low == "discretionary" or (
            "discretionary" in low and "consumer" not in low and "productive" not in low
        ):
            names["productive"] = tl
        elif "consumer" in low and "discretionary" in low:
            names["consumer"] = tl
    return names


def _resolve_tab_gids(gcfg: Dict[str, Any]) -> Dict[str, str]:
    """Merge config tab_gids with defaults (string values)."""
    out = {k: str(v) for k, v in DEFAULT_TAB_GIDS.items()}
    raw = gcfg.get("tab_gids") or gcfg.get("gids") or {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if v is None or str(v).strip() == "":
                continue
            out[str(k)] = str(v)
    return out


def _fetch_tab_optional(
    sheet_id: str,
    name: str,
    *,
    gid: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Return (csv, error). Missing tab → (None, err) without raising."""
    try:
        return fetch_sheet_csv(sheet_id, name, gid=gid), None
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
    gids = _resolve_tab_gids(gcfg)

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
        personal_csv, pers_err = _fetch_tab_optional(
            sid,
            names["personal"],
            gid=gids.get(names["personal"])
            or gids.get(ESSENTIAL_TAB)
            or gids.get(LEGACY_ESSENTIAL_TAB),
        )
        # If config/default still says Essential but sheet not yet renamed (or vice versa)
        if personal_csv is None and names["personal"] == ESSENTIAL_TAB:
            personal_csv, pers_err2 = _fetch_tab_optional(
                sid,
                LEGACY_ESSENTIAL_TAB,
                gid=gids.get(LEGACY_ESSENTIAL_TAB) or gids.get(ESSENTIAL_TAB),
            )
            if personal_csv is not None:
                pers_err = None
            else:
                pers_err = pers_err or pers_err2
        elif personal_csv is None and names["personal"] == LEGACY_ESSENTIAL_TAB:
            personal_csv, pers_err2 = _fetch_tab_optional(
                sid,
                ESSENTIAL_TAB,
                gid=gids.get(ESSENTIAL_TAB) or gids.get(LEGACY_ESSENTIAL_TAB),
            )
            if personal_csv is not None:
                pers_err = None
            else:
                pers_err = pers_err or pers_err2
        if personal_csv is None:
            raise RuntimeError(
                pers_err
                or f"Essential tab not found (tried {names['personal']!r} / Essential / Personal)"
            )

        fleet_csv, fleet_err = _fetch_tab_optional(
            sid, names["fleet"], gid=gids.get(names["fleet"]) or gids.get(FLEET_TAB)
        )

        coll_csv, coll_err = _fetch_tab_optional(
            sid,
            names["collateral"],
            gid=gids.get(names["collateral"]) or gids.get(COLLATERAL_TAB),
        )

        productive_csv, prod_err = _fetch_tab_optional(
            sid,
            names["productive"],
            gid=gids.get(names["productive"]) or gids.get(PRODUCTIVE_TAB),
        )
        if productive_csv is None and names["productive"] != LEGACY_PRODUCTIVE_TAB:
            productive_csv, prod_err2 = _fetch_tab_optional(
                sid,
                LEGACY_PRODUCTIVE_TAB,
                gid=gids.get(LEGACY_PRODUCTIVE_TAB),
            )
            if productive_csv is not None:
                prod_err = None
            else:
                prod_err = prod_err or prod_err2
        if productive_csv is None:
            # Productive optional if empty sheet mid-migration — use empty CSV
            productive_csv = "Item,Date,Daily,Weekly,Bi-Weekly,Monthly,Annually,From,To\n"
            prod_err = prod_err or "Productive Discretionary tab unavailable; using empty"

        consumer_csv, cons_err = _fetch_tab_optional(
            sid,
            names["consumer"],
            gid=gids.get(names["consumer"]) or gids.get(CONSUMER_TAB),
        )

        snap = build_expenses_snapshot(
            personal_csv,
            productive_csv,
            consumer_csv=consumer_csv,
            fleet_csv=fleet_csv,
            collateral_csv=coll_csv,
            sheet_id=sid,
            source="google_sheets",
        )
        warnings: List[str] = []
        if fleet_err and fleet_csv is None:
            warnings.append(f"Fleet tab unavailable: {fleet_err}")
        if coll_err and coll_csv is None:
            warnings.append(f"Collateral tab unavailable: {coll_err}")
        if prod_err and (
            "empty" in prod_err.lower() or "unavailable" in prod_err.lower()
        ):
            warnings.append(prod_err)
        if cons_err and consumer_csv is None:
            warnings.append(f"Consumer Discretionary tab unavailable: {cons_err}")
        if warnings:
            snap["tab_warnings"] = warnings
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
    tabs = data.get("tabs") or {}
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(path),
                "upcoming_expense_monthly": s.get("upcoming_expense_monthly")
                or s.get("personal_monthly"),
                "personal_monthly": s.get("personal_monthly"),
                "fleet_monthly": s.get("fleet_monthly"),
                "collateral_investments_monthly": s.get("collateral_investments_monthly"),
                "capital_targets_monthly": s.get("capital_targets_monthly")
                or s.get("discretionary_monthly"),
                "coinbase_funded_monthly": s.get("coinbase_funded_monthly"),
                "rh_checking_funded_monthly": s.get("rh_funded_monthly"),
                "x_money_funded_monthly": s.get("x_money_funded_monthly"),
                "items_essential": essential_tab_block(tabs).get("item_count"),
                "items_personal": essential_tab_block(tabs).get("item_count"),  # legacy key
                "items_fleet": tabs.get(FLEET_TAB, {}).get("item_count"),
                "items_collateral": tabs.get(COLLATERAL_TAB, {}).get("item_count"),
                "items_productive": tabs.get(
                    PRODUCTIVE_TAB, tabs.get(LEGACY_PRODUCTIVE_TAB, {})
                ).get("item_count"),
                "items_consumer": tabs.get(CONSUMER_TAB, {}).get("item_count"),
                "tab_warnings": data.get("tab_warnings"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
