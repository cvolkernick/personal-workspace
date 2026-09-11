"""Rolling-window YNAB income → expense Sankey for FCC Cash Streams.

Canonical source: live YNAB via ``treasury.ynab_sync`` (token + GET). No
committed model file. Transfers between on-budget accounts are excluded.
Uncategorized outflows stay an explicit node.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent

TOP_N_INCOME = 8
DEFAULT_DAYS = 90
ALLOWED_DAYS = (30, 60, 90, 180)
STARTING_BALANCE_PAYEES = {"starting balance", "starting balances"}
CC_PAYMENT_NAMES = {"credit card payment", "credit card payments"}
INTERNAL_GROUP_NAMES = {"internal master category"}
UNCATEGORIZED_NAMES = {"", "uncategorized", "unassigned"}

CASH_SNAPSHOTS = (
    "x_money_latest.json",
    "one_card_latest.json",
    "rh_checking_latest.json",
)


def clamp_days(raw: Any, default: int = DEFAULT_DAYS) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    if n in ALLOWED_DAYS:
        return n
    return default


def _money(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _milli_units(milli: Any) -> float:
    try:
        return round(float(milli) / 1000.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _parse_day(raw: Any) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _slug(text: str, fallback: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in (text or "").strip())
    while "--" in out:
        out = out.replace("--", "-")
    out = out.strip("-")
    return out or fallback


def window_bounds(days: int, today: Optional[date] = None) -> Tuple[date, date, int]:
    days = clamp_days(days)
    end = today or date.today()
    start = end - timedelta(days=days)
    return start, end, days


def _is_transfer(tx: Dict[str, Any]) -> bool:
    return bool(tx.get("transfer_account_id"))


def _is_starting_balance(tx: Dict[str, Any]) -> bool:
    payee = str(tx.get("payee_name") or tx.get("payee") or "").strip().lower()
    return payee in STARTING_BALANCE_PAYEES


def _is_cc_payment(tx: Dict[str, Any], group_name: str) -> bool:
    cat = str(tx.get("category_name") or "").strip().lower()
    group = (group_name or "").strip().lower()
    if cat in CC_PAYMENT_NAMES:
        return True
    if group in INTERNAL_GROUP_NAMES and "credit card" in cat:
        return True
    return False


def category_lookup(category_groups: Sequence[Dict[str, Any]]) -> Dict[str, Tuple[str, str]]:
    """category_id → (group_name, category_name)."""
    out: Dict[str, Tuple[str, str]] = {}
    for group in category_groups or []:
        if group.get("deleted"):
            continue
        gname = str(group.get("name") or "").strip() or "Uncategorized"
        for cat in group.get("categories") or []:
            if not isinstance(cat, dict) or cat.get("deleted"):
                continue
            cid = cat.get("id")
            if not cid:
                continue
            cname = str(cat.get("name") or "").strip() or "Uncategorized"
            out[str(cid)] = (gname, cname)
    return out


def _resolve_category(
    tx: Dict[str, Any],
    lookup: Dict[str, Tuple[str, str]],
) -> Tuple[str, str]:
    cid = tx.get("category_id")
    if cid and str(cid) in lookup:
        return lookup[str(cid)]
    cname = str(tx.get("category_name") or "").strip()
    gname = str(tx.get("category_group_name") or "").strip()
    if not cname or cname.lower() in UNCATEGORIZED_NAMES:
        return ("Uncategorized", "Uncategorized")
    return (gname or "Uncategorized", cname)


def _iter_countable(
    transactions: Iterable[Dict[str, Any]],
    *,
    start: date,
    end: date,
    on_budget_ids: Optional[set],
    lookup: Dict[str, Tuple[str, str]],
) -> Iterable[Dict[str, Any]]:
    for tx in transactions or []:
        if not isinstance(tx, dict) or tx.get("deleted"):
            continue
        day = _parse_day(tx.get("date"))
        if day is None or day < start or day > end:
            continue
        if on_budget_ids is not None:
            aid = tx.get("account_id")
            if aid and aid not in on_budget_ids:
                continue
        subs = tx.get("subtransactions") or []
        rows = list(subs) if subs else [tx]
        parent_payee = str(tx.get("payee_name") or tx.get("payee") or "").strip()
        for row in rows:
            if not isinstance(row, dict) or row.get("deleted"):
                continue
            if _is_transfer(row) or (not subs and _is_transfer(tx)):
                continue
            if _is_starting_balance(row) or _is_starting_balance(tx):
                continue
            group_name, cat_name = _resolve_category(row if row.get("category_id") or row.get("category_name") else tx, lookup)
            if _is_cc_payment(row, group_name) or _is_cc_payment(tx, group_name):
                continue
            amount = _milli_units(row.get("amount"))
            if amount == 0:
                continue
            payee = str(row.get("payee_name") or row.get("payee") or parent_payee).strip()
            yield {
                "date": day.isoformat(),
                "payee": payee,
                "amount": amount,
                "group": group_name,
                "category": cat_name,
            }


def build_cash_streams(
    *,
    days: int = DEFAULT_DAYS,
    today: Optional[date] = None,
    transactions: Optional[Sequence[Dict[str, Any]]] = None,
    category_groups: Optional[Sequence[Dict[str, Any]]] = None,
    on_budget_ids: Optional[Iterable[str]] = None,
    ynab_stale: bool = False,
    ynab_soft_preserved: bool = False,
    ynab_as_of: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the Sankey payload. Pure: no I/O."""
    start, end, days = window_bounds(days, today=today)
    window = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
    }
    ynab = {
        "stale": bool(ynab_stale),
        "soft_preserved": bool(ynab_soft_preserved),
        "as_of": ynab_as_of,
    }
    if error:
        return {
            "ok": False,
            "error": error,
            "window": window,
            "nodes": [],
            "links": [],
            "totals": {"inflow": 0.0, "outflow": 0.0, "retained": 0.0},
            "ynab": ynab,
        }

    lookup = category_lookup(category_groups or [])
    budget_ids = set(on_budget_ids) if on_budget_ids is not None else None
    inflows: Dict[str, float] = {}
    group_totals: Dict[str, float] = {}
    cat_totals: Dict[Tuple[str, str], float] = {}

    for row in _iter_countable(
        transactions or [],
        start=start,
        end=end,
        on_budget_ids=budget_ids,
        lookup=lookup,
    ):
        amt = row["amount"]
        if amt > 0:
            payee = row["payee"] or "Unknown payee"
            inflows[payee] = _money(inflows.get(payee, 0) + amt)
        elif amt < 0:
            spent = abs(amt)
            group = row["group"] or "Uncategorized"
            cat = row["category"] or "Uncategorized"
            if group.lower() in UNCATEGORIZED_NAMES or cat.lower() in UNCATEGORIZED_NAMES:
                group, cat = "Uncategorized", "Uncategorized"
            group_totals[group] = _money(group_totals.get(group, 0) + spent)
            key = (group, cat)
            cat_totals[key] = _money(cat_totals.get(key, 0) + spent)

    ranked = sorted(inflows.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    source_nodes: List[Tuple[str, str, float]] = []
    if len(ranked) > TOP_N_INCOME:
        keep = ranked[:TOP_N_INCOME]
        other = _money(sum(v for _, v in ranked[TOP_N_INCOME:]))
        source_nodes = [(f"in-{i}", name, _money(val)) for i, (name, val) in enumerate(keep)]
        if other > 0:
            source_nodes.append((f"in-{TOP_N_INCOME}", "Other income", other))
    else:
        source_nodes = [(f"in-{i}", name, _money(val)) for i, (name, val) in enumerate(ranked)]

    inflow = _money(sum(v for _, _, v in source_nodes))
    outflow = _money(sum(group_totals.values()))
    retained = _money(inflow - outflow)

    if inflow == 0 and outflow == 0:
        return {
            "ok": True,
            "window": window,
            "nodes": [],
            "links": [],
            "totals": {"inflow": 0.0, "outflow": 0.0, "retained": 0.0},
            "ynab": ynab,
        }

    nodes: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []

    def add_node(nid: str, name: str, layer: str, amount: float) -> None:
        nodes.append(
            {
                "id": nid,
                "name": name,
                "layer": layer,
                "amount": _money(amount),
            }
        )

    def add_link(src: str, tgt: str, value: float, pct_of_parent: float) -> None:
        val = _money(value)
        if val <= 0:
            return
        links.append(
            {
                "source": src,
                "target": tgt,
                "value": val,
                "pct_of_parent": round(pct_of_parent, 4),
            }
        )

    revenue_id = "revenue"
    revenue_amount = inflow
    if retained < 0:
        revenue_amount = outflow

    for nid, name, val in source_nodes:
        add_node(nid, name, "inflow", val)
        add_link(nid, revenue_id, val, (val / inflow) if inflow else 0.0)

    if retained < -0.004:
        deficit = _money(abs(retained))
        add_node("deficit", "Deficit", "inflow", deficit)
        add_link("deficit", revenue_id, deficit, 1.0 if not inflow else deficit / max(outflow, deficit))
        add_node(revenue_id, "Total Revenue", "revenue", revenue_amount)
    else:
        add_node(revenue_id, "Total Revenue", "revenue", inflow)

    parent_for_groups = outflow if retained < -0.004 else inflow
    for i, (gname, gval) in enumerate(sorted(group_totals.items(), key=lambda kv: (-kv[1], kv[0].lower()))):
        gid = f"grp-{i}"
        add_node(gid, gname, "group", gval)
        add_link(
            revenue_id,
            gid,
            gval,
            (gval / parent_for_groups) if parent_for_groups else 0.0,
        )
        cats = [(cat, val) for (grp, cat), val in cat_totals.items() if grp == gname]
        for j, (cname, cval) in enumerate(sorted(cats, key=lambda kv: (-kv[1], kv[0].lower()))):
            cid = f"cat-{i}-{j}"
            add_node(cid, cname, "category", cval)
            add_link(gid, cid, cval, (cval / gval) if gval else 0.0)

    if retained > 0.004:
        add_node("retained", "Retained", "retained", retained)
        add_link(revenue_id, "retained", retained, (retained / inflow) if inflow else 0.0)

    return {
        "ok": True,
        "window": window,
        "nodes": nodes,
        "links": links,
        "totals": {
            "inflow": inflow,
            "outflow": outflow,
            "retained": retained,
        },
        "ynab": ynab,
    }


def _load_snapshot(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def ynab_soft_preserved(root: Optional[Path] = None) -> bool:
    """True when a cash snapshot still has data after a failed live refresh."""
    base = (root or ROOT) / "treasury" / "snapshots"
    for name in CASH_SNAPSHOTS:
        data = _load_snapshot(base / name)
        if (
            data
            and data.get("source") not in (None, "empty")
            and data.get("live_error")
        ):
            return True
    return False


def ynab_as_of_from_snapshots(root: Optional[Path] = None) -> Optional[str]:
    base = (root or ROOT) / "treasury" / "snapshots"
    stamps: List[str] = []
    for name in CASH_SNAPSHOTS:
        data = _load_snapshot(base / name)
        iso = data.get("as_of")
        if iso:
            stamps.append(str(iso))
    if not stamps:
        return None
    return min(stamps)


def fetch_ynab_window(since: str) -> Dict[str, Any]:
    """Live YNAB transactions + categories. Token path is ynab_sync's."""
    from treasury.ynab_sync import load_ynab_token, pick_budget, ynab_get

    token, _src = load_ynab_token()
    if not token:
        return {
            "ok": False,
            "error": "no YNAB token (~/.config/ynab/token or YNAB_TOKEN)",
        }
    try:
        budgets = ynab_get("/budgets", token).get("data", {}).get("budgets") or []
        budget = pick_budget(budgets)
        bid = budget["id"]
        accounts = (
            ynab_get(f"/budgets/{bid}/accounts", token).get("data", {}).get("accounts")
            or []
        )
        on_budget_ids = [
            a.get("id")
            for a in accounts
            if a.get("id")
            and a.get("on_budget")
            and not a.get("deleted")
            and not a.get("closed")
        ]
        groups = (
            ynab_get(f"/budgets/{bid}/categories", token)
            .get("data", {})
            .get("category_groups")
            or []
        )
        txs = (
            ynab_get(
                f"/budgets/{bid}/transactions",
                token,
                params={"since_date": since},
            )
            .get("data", {})
            .get("transactions")
            or []
        )
    except Exception as e:
        return {"ok": False, "error": f"YNAB fetch failed: {e}"}
    return {
        "ok": True,
        "transactions": txs,
        "category_groups": groups,
        "on_budget_ids": on_budget_ids,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "budget_name": budget.get("name"),
    }


def load_cash_streams(
    *,
    days: int = DEFAULT_DAYS,
    today: Optional[date] = None,
    stale: Optional[bool] = None,
    root: Optional[Path] = None,
    fetch=None,
) -> Dict[str, Any]:
    """Orchestrate live YNAB pull + Sankey. ``fetch`` is injectable for tests."""
    start, end, days = window_bounds(days, today=today)
    base = root or ROOT
    stale_flag = _fallback_cash_stale(base) if stale is None else bool(stale)
    soft = ynab_soft_preserved(base)
    snap_as_of = ynab_as_of_from_snapshots(base)
    fetcher = fetch or fetch_ynab_window
    pulled = fetcher(start.isoformat())
    if not pulled.get("ok"):
        return build_cash_streams(
            days=days,
            today=end,
            ynab_stale=stale_flag,
            ynab_soft_preserved=soft,
            ynab_as_of=snap_as_of,
            error=str(pulled.get("error") or "YNAB fetch failed"),
        )
    as_of = pulled.get("as_of") or datetime.now(timezone.utc).isoformat()
    return build_cash_streams(
        days=days,
        today=end,
        transactions=pulled.get("transactions") or [],
        category_groups=pulled.get("category_groups") or [],
        on_budget_ids=pulled.get("on_budget_ids"),
        ynab_stale=stale_flag,
        ynab_soft_preserved=soft,
        ynab_as_of=as_of,
    )


def _fallback_cash_stale(root: Path, max_hours: float = 6.0) -> bool:
    now = datetime.now(timezone.utc)
    for name in CASH_SNAPSHOTS:
        data = _load_snapshot(root / "treasury" / "snapshots" / name)
        iso = data.get("as_of") if data else None
        if not iso:
            return True
        try:
            t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return True
        if (now - t).total_seconds() / 3600.0 > max_hours:
            return True
    return False
