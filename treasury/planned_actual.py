"""Display-only planned (sheet) vs YNAB-actual flag strip for FCC.

SoT:
  planned — Personal Expense Sheet burn rows (Essential/Personal + Fleet).
  actual  — this-month YNAB in-map spend by payee.
  join    — sheet Item → one YNAB category_id (map name / sheet_item / payee).

Never writes money, never nudges the coach, never touches Interest Spectrum.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from treasury.adapters import SNAPSHOTS_DIR
from treasury.financial_coach import load_snapshots, normalize_venue
from treasury.ynab_category_map import (
    MAP_PATH,
    enabled_category_ids,
    load_category_map,
)

FLAG_ON = "on"
FLAG_NOT_YET = "not-yet"
FLAG_TWO_CHARGE = "two-charge"
FLAG_CADENCE_LUMP = "cadence-lump"
FLAG_OFF_BOOK = "off-book From"
FLAGS = (
    FLAG_ON,
    FLAG_NOT_YET,
    FLAG_TWO_CHARGE,
    FLAG_CADENCE_LUMP,
    FLAG_OFF_BOOK,
)

# YNAB-synced pay-from venues. Coinbase wallet / NFCU / Zelle are off-book.
YNAB_SYNCED_VENUES = frozenset({"x_money", "rh_checking"})

# Sheet tabs that are planned burn. Discretionary / collateral stay out.
PLANNED_TABS = (
    ("Essential", "Essential"),
    ("Personal", "Essential"),
    ("Fleet", "Fleet"),
)

# Never render these even if a name join would succeed.
_OFF_MAP_ITEM_NEEDLES = (
    "coinbase one card",
    "santander",
    "capital one",
    "cap one",
    "gm financial",
    "rivian",
    "agentic fund",
    "asic fleet opex",
    "asic opex",
    "robinhood gold",
    "rh gold",
)

_UNLABELED_CAT_NAMES = frozenset(
    {
        "uncategorized",
        "inflow: ready to assign",
        "ready to assign",
    }
)

_MONTH_PREFIX = re.compile(
    r"^(january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+",
    re.I,
)

_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002700-\U000027bf"
    "\U0001f900-\U0001f9ff"
    "\U00002600-\U000026ff"
    "]+",
    flags=re.UNICODE,
)


def _f(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _near(a: float, b: float, *, tol: float = 0.02) -> bool:
    return abs(float(a) - float(b)) <= tol


def _now_date() -> date:
    return datetime.now(timezone.utc).date()


def _parse_as_of(val: Any) -> date:
    if not val:
        return _now_date()
    raw = str(val).strip()
    if "T" in raw:
        raw = raw.split("T", 1)[0]
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return _now_date()


def normalize_name(val: Any) -> str:
    """Casefold, strip emoji, collapse punctuation — join key only."""
    s = unicodedata.normalize("NFKC", str(val or ""))
    s = _EMOJI_RE.sub(" ", s)
    s = s.replace("&", " and ").replace("/", " ")
    s = re.sub(r"[^\w\s]+", " ", s, flags=re.UNICODE)
    s = " ".join(s.casefold().split())
    return s


def item_match_key(val: Any) -> str:
    """Normalize plus drop a leading month so 'August Rent' joins Rent."""
    key = normalize_name(val)
    return _MONTH_PREFIX.sub("", key).strip()


def payee_matches_item(payee: Any, item_name: Any) -> bool:
    p = normalize_name(payee)
    i = item_match_key(item_name)
    if not p or not i:
        return False
    if p == i:
        return True
    if p.startswith(i + " ") or i.startswith(p + " "):
        return True
    # token containment both ways (FilterEasy / Filter Easy)
    p_toks = set(p.split())
    i_toks = set(i.split())
    if i_toks and i_toks <= p_toks:
        return True
    if p_toks and p_toks <= i_toks and len(p_toks) >= 1:
        return True
    return False


def names_join(item_name: Any, category_name: Any) -> bool:
    a = item_match_key(item_name)
    b = item_match_key(category_name)
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        # require the shorter key to be a whole-token hit
        short, long = (a, b) if len(a) <= len(b) else (b, a)
        return short in long.split() or long.startswith(short + " ") or long.endswith(" " + short)
    return False


def is_off_map_item(item_name: Any) -> bool:
    key = item_match_key(item_name)
    if not key:
        return True
    return any(n in key for n in _OFF_MAP_ITEM_NEEDLES)


def is_off_book_from(from_label: Any) -> bool:
    """True when sheet From is a venue YNAB does not sync (e.g. Coinbase)."""
    raw = str(from_label or "").strip()
    if not raw:
        return False
    venue = normalize_venue(raw)
    return venue not in YNAB_SYNCED_VENUES


def planned_sheet_items(expenses: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per Essential/Personal + Fleet sheet item. No discretionary."""
    tabs = (expenses or {}).get("tabs") or {}
    out: List[Dict[str, Any]] = []
    seen_tabs: Set[str] = set()
    seen_names: Set[str] = set()
    for tab_key, label in PLANNED_TABS:
        if tab_key == "Personal" and "Essential" in seen_tabs:
            continue
        tab = tabs.get(tab_key) or {}
        if not tab:
            continue
        seen_tabs.add(tab_key)
        items = tab.get("items")
        if not isinstance(items, list) or not items:
            items = tab.get("upcoming_by_date") or []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("item") or raw.get("name") or "").strip()
            if not name or name.lower() == "total":
                continue
            key = item_match_key(name)
            if key in seen_names:
                continue
            seen_names.add(key)
            rec = dict(raw)
            rec["item"] = name
            rec.setdefault("tab", label)
            rec["monthly"] = _f(rec.get("monthly"))
            out.append(rec)
    return out


def _cat_rows(category_map: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for row in category_map.get("categories") or []:
        if isinstance(row, dict) and str(row.get("id") or "").strip():
            rows.append(row)
    return rows


def enabled_ids(category_map: Dict[str, Any]) -> Set[str]:
    return set(enabled_category_ids(category_map))


def _ids_equal(a: Any, b: Any) -> bool:
    sa, sb = str(a or "").strip(), str(b or "").strip()
    if not sa or not sb:
        return False
    return sa == sb


def resolve_tx_category_id(tx: Dict[str, Any], category_map: Dict[str, Any]) -> Optional[str]:
    """Map a YNAB tx onto an enabled category id. Never invents ids."""
    enabled = enabled_ids(category_map)
    cid = str(tx.get("category_id") or "").strip()
    if cid and cid in enabled:
        return cid
    cname = tx.get("category_name") or tx.get("category")
    if not cname:
        return None
    for row in _cat_rows(category_map):
        rid = str(row.get("id") or "").strip()
        if rid not in enabled:
            continue
        if names_join(cname, row.get("name")) or normalize_name(cname) == normalize_name(row.get("name")):
            return rid
    return None


def is_skipped_tx(tx: Dict[str, Any], category_map: Dict[str, Any]) -> bool:
    """Payment / transfer / unlabeled leftover — never becomes actual."""
    if tx.get("transfer_account_id") or tx.get("transfer_transaction_id"):
        return True
    payee = str(tx.get("payee") or tx.get("payee_name") or "")
    pl = payee.casefold()
    cat = str(tx.get("category_name") or tx.get("category") or "").casefold()
    if "credit card payment" in cat or "credit card payments" in cat:
        return True
    if pl in ("starting balance", "starting balances"):
        return True
    if pl.startswith("transfer") or " transfer " in f" {pl} " or pl.startswith("payment"):
        return True
    if "payment" in pl and ("card" in pl or "coinbase one" in pl):
        return True
    if normalize_name(cat) in _UNLABELED_CAT_NAMES:
        return True
    if not resolve_tx_category_id(tx, category_map):
        return True
    return False


def is_spend_tx(tx: Dict[str, Any]) -> bool:
    amt = _f(tx.get("amount"), _f(tx.get("amount_display")))
    return amt < -0.001


def tx_spend_amount(tx: Dict[str, Any]) -> float:
    amt = _f(tx.get("amount"), _f(tx.get("amount_display")))
    return round(abs(amt), 2) if amt < 0 else 0.0


def tx_in_month(tx: Dict[str, Any], month: date) -> bool:
    raw = str(tx.get("date") or "")
    if not raw:
        return False
    try:
        d = date.fromisoformat(raw[:10])
    except ValueError:
        return False
    return d.year == month.year and d.month == month.month


def collect_ynab_txs(snapshots: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for key in ("one_card", "rh_checking", "x_money"):
        feed = snapshots.get(key) or {}
        txs = feed.get("transactions") or []
        if not isinstance(txs, list):
            continue
        for tx in txs:
            if isinstance(tx, dict) and not tx.get("deleted"):
                rec = dict(tx)
                rec.setdefault("payee", rec.get("payee_name"))
                rec["_feed"] = key
                out.append(rec)
    return out


def join_item_category(
    item: Dict[str, Any],
    category_map: Dict[str, Any],
    txs: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Sheet Item → one enabled YNAB category. None = off-map, do not render."""
    name = item.get("item") or item.get("name")
    if is_off_map_item(name):
        return None
    enabled = enabled_ids(category_map)
    hits: List[Dict[str, Any]] = []
    for row in _cat_rows(category_map):
        rid = str(row.get("id") or "").strip()
        if rid not in enabled:
            continue
        sheet_item = row.get("sheet_item")
        if sheet_item and names_join(name, sheet_item):
            hits.append(row)
            continue
        if names_join(name, row.get("name")):
            hits.append(row)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        # Prefer exact key match, then shortest category name.
        exact = [r for r in hits if item_match_key(r.get("name")) == item_match_key(name)]
        pool = exact or hits
        pool.sort(key=lambda r: len(normalize_name(r.get("name"))))
        return pool[0]
    # Payee → unique in-map category (FilterEasy recat → Subscriptions).
    cat_ids: List[str] = []
    by_id = {str(r.get("id")): r for r in _cat_rows(category_map)}
    for tx in txs:
        if is_skipped_tx(tx, category_map):
            continue
        if not payee_matches_item(tx.get("payee") or tx.get("payee_name"), name):
            continue
        cid = resolve_tx_category_id(tx, category_map)
        if cid and cid not in cat_ids:
            cat_ids.append(cid)
    if len(cat_ids) == 1:
        return by_id.get(cat_ids[0]) or {"id": cat_ids[0], "name": ""}
    return None


def classify_flag(
    *,
    off_book: bool,
    planned: float,
    txs: Sequence[Dict[str, Any]],
) -> str:
    if off_book:
        return FLAG_OFF_BOOK
    if not txs:
        return FLAG_NOT_YET
    amounts = [tx_spend_amount(t) for t in txs]
    n = len(amounts)
    if planned > 0.005 and n >= 2:
        unit = amounts[0]
        same = all(_near(a, unit) for a in amounts)
        if same and _near(unit, planned):
            return FLAG_CADENCE_LUMP
    if planned > 0.005 and n == 1:
        amt = amounts[0]
        multiple = round(amt / planned) if planned else 0
        if 2 <= multiple <= 4 and _near(amt, multiple * planned):
            return FLAG_CADENCE_LUMP
    if n >= 2:
        return FLAG_TWO_CHARGE
    return FLAG_ON


def _row_payload(
    item: Dict[str, Any],
    cat: Dict[str, Any],
    *,
    planned: float,
    actual: float,
    flag: str,
    tx_count: int,
    month: date,
) -> Dict[str, Any]:
    from_label = item.get("from") or item.get("pay_from") or ""
    payload = {
        "item": item.get("item") or item.get("name"),
        "tab": item.get("tab"),
        "from": from_label or None,
        "planned": round(planned, 2),
        "actual": round(actual, 2),
        "flag": flag,
        "category_id": str(cat.get("id") or ""),
        "category_name": cat.get("name") or "",
        "tx_count": tx_count,
        "month": month.isoformat()[:7],
        "display_only": True,
    }
    if flag == FLAG_OFF_BOOK:
        payload["from_venue"] = from_label or "unknown"
        payload["actual"] = 0.0
    return payload


def build_planned_actual_strip(
    snapshots: Dict[str, Any],
    category_map: Optional[Dict[str, Any]] = None,
    *,
    as_of: Optional[Any] = None,
) -> Dict[str, Any]:
    """Pure builder. Display-only; no coach / spectrum / money writes."""
    cmap = category_map if category_map is not None else load_category_map(MAP_PATH)
    month = _parse_as_of(as_of)
    expenses = snapshots.get("expenses") or {}
    items = planned_sheet_items(expenses)
    txs = collect_ynab_txs(snapshots)
    rows: List[Dict[str, Any]] = []
    skipped_leftover = 0
    for tx in txs:
        if tx_in_month(tx, month) and is_skipped_tx(tx, cmap):
            skipped_leftover += 1

    for item in items:
        cat = join_item_category(item, cmap, txs)
        if cat is None:
            continue
        planned = _f(item.get("monthly"))
        off_book = is_off_book_from(item.get("from") or item.get("pay_from"))
        matched: List[Dict[str, Any]] = []
        if not off_book:
            for tx in txs:
                if not tx_in_month(tx, month):
                    continue
                if is_skipped_tx(tx, cmap):
                    continue
                if not is_spend_tx(tx):
                    continue
                if payee_matches_item(tx.get("payee") or tx.get("payee_name"), item.get("item")):
                    matched.append(tx)
        actual = 0.0 if off_book else round(sum(tx_spend_amount(t) for t in matched), 2)
        flag = classify_flag(off_book=off_book, planned=planned, txs=matched)
        rows.append(
            _row_payload(
                item,
                cat,
                planned=planned,
                actual=actual,
                flag=flag,
                tx_count=0 if off_book else len(matched),
                month=month,
            )
        )

    counts = {f: 0 for f in FLAGS}
    for r in rows:
        counts[r["flag"]] = counts.get(r["flag"], 0) + 1

    return {
        "ok": True,
        "display_only": True,
        "coach_wired": False,
        "spectrum_trigger": False,
        "month": month.isoformat()[:7],
        "rows": rows,
        "summary": {
            "row_count": len(rows),
            "flag_counts": counts,
            "skipped_leftover_txs": skipped_leftover,
        },
        "notes": (
            "Display only. planned = sheet monthly; actual = this-month YNAB "
            "in-map spend by payee. two-charge and cadence-lump are not lifestyle over."
        ),
    }


def load_planned_actual(
    *,
    snapshots_dir: Optional[Path] = None,
    category_map: Optional[Dict[str, Any]] = None,
    as_of: Optional[Any] = None,
) -> Dict[str, Any]:
    snaps = load_snapshots(Path(snapshots_dir) if snapshots_dir else SNAPSHOTS_DIR)
    cmap = category_map if category_map is not None else load_category_map(MAP_PATH)
    return build_planned_actual_strip(snaps, cmap, as_of=as_of)
