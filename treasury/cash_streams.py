"""Rolling-window income → expense Sankey for FCC Cash Streams.

Canonical sources:
- YNAB (live via ``treasury.ynab_sync``) for payee inflows and expenses
- Braiins Pool payout history (``braiins_latest.json``) for Bitcoin mining
  income, valued at Coinbase ``btc_usd_price`` stamped at first observation

No committed model file. Transfers between on-budget accounts are excluded.
Starting-balance payees and "FCC reconcile" bookkeeping (payee, memo, or
category) are excluded so setup entries and balance adjustments are not
income or spend. Coinbase→Main withdrawals (payee contains "coinbase") are
inflows excluded so mining is not double-counted when USD later hits Main.
Non-transfer outflows on the off-budget Coinbase USD tracking account are
included. The live YNAB name is "Coinbase USD (tracking)" and the account id
starts with 901c6c2e. Transfers into that account, including Main → Coinbase,
stay excluded. Positive amounts on that account are not income.

The 90-day rolling chart (`build_rolling_cash_series`) reuses this filter,
then drops any payee containing "reconcile". That wider drop is chart-only.
It does not change Sankey totals or the Glance daily-flow chip. Mining stays
on the Sankey; the rolling lines are YNAB daily sums only. Lyft, Grubhub,
and Turo lines are the same trailing mean restricted to external inflows
``classify_income_source`` accepts.
Uncategorized outflows stay an explicit node. Missing/stale Braiins or
Coinbase price feeds are a loud mining-unknown state, never a silent omit.

Cash Streams freshness is the live YNAB *transaction* pull ``as_of``, not the
age of balance snapshots (``x_money`` / ``one_card`` / ``rh_checking``). Those
files feed the main FCC cash stack; this page does not read them.

Income-node labels: ``treasury/payee_display_names.json`` maps raw YNAB payee
→ display name (map-then-group). Missing/unparseable file falls back to raw
names; the chart still builds. Coinbase inflow exclusion stays on the raw
payee. Expense nodes are YNAB categories today — same helper if payees render.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from treasury.income_sources import (
    classify_income_source,
    income_source_ids,
    income_source_public,
)

ROOT = Path(__file__).resolve().parent.parent

TOP_N_INCOME = 8
DEFAULT_DAYS = 90
ALLOWED_DAYS = (30, 60, 90, 180)
STARTING_BALANCE_PAYEES = {"starting balance", "starting balances"}
FCC_RECONCILE_MARK = "fcc reconcile"
# Off-budget YNAB tracking account whose sends are real outflows (#932).
# Live name is "Coinbase USD (tracking)". The id prefix is the budget pin.
COINBASE_USD_TRACKING_NAME = "coinbase usd"
COINBASE_USD_ACCOUNT_PREFIX = "901c6c2e"
# Chart series only. Wider than FCC_RECONCILE_MARK and payee-only.
CHART_RECONCILE_MARK = "reconcile"
ROLLING_DISPLAY_DAYS = 90
ROLLING_MEAN_DAYS = 30
# Matches window_bounds: start = end - days, both inclusive. Not an ALLOWED_DAYS
# Sankey window — 120 stays rejected by clamp_days.
ROLLING_SEED_DAYS = 120
CC_PAYMENT_NAMES = {"credit card payment", "credit card payments"}
INTERNAL_GROUP_NAMES = {"internal master category"}
UNCATEGORIZED_NAMES = {"", "uncategorized", "unassigned"}

CASH_SNAPSHOTS = (
    "x_money_latest.json",
    "one_card_latest.json",
    "rh_checking_latest.json",
)
FEED_STALE_HOURS = 6.0
MINING_NODE_ID = "in-mining"
MINING_NODE_NAME = "Bitcoin mining"
PAYEE_DISPLAY_NAMES_FILE = "payee_display_names.json"
# Phone viewers cannot run the Pi producer. Never tell them to execute
# python3 / systemctl / launchctl / braiins_sync.py.
BRAIINS_PRODUCER_HINT = (
    "Pi is the Braiins producer (braiins-refresh.timer every 4h; "
    "token at ~/.config/braiins/token). This dashboard cannot run a sync."
)
COINBASE_PRICE_HINT = (
    "Pi is the BTC/USD price producer (coinbase-price-refresh.timer every 2h; "
    "public Coinbase spot, no CLI). This dashboard cannot run a sync."
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


def _is_fcc_reconcile(tx: Dict[str, Any]) -> bool:
    """Bookkeeping adjustments, not income or spend (#908)."""
    blob = " ".join(
        str(tx.get(key) or "")
        for key in (
            "payee_name",
            "payee",
            "memo",
            "category_name",
            "category_group_name",
        )
    ).lower()
    return FCC_RECONCILE_MARK in blob


def _is_cc_payment(tx: Dict[str, Any], group_name: str) -> bool:
    cat = str(tx.get("category_name") or "").strip().lower()
    group = (group_name or "").strip().lower()
    if cat in CC_PAYMENT_NAMES:
        return True
    if group in INTERNAL_GROUP_NAMES and "credit card" in cat:
        return True
    return False


def _is_coinbase_inflow_payee(payee: str) -> bool:
    """True for Coinbase→Main withdrawal inflows (not income). Outflows stay."""
    return "coinbase" in (payee or "").strip().lower()


def _normalized_account_name(name: Any) -> str:
    return " ".join(str(name or "").split()).casefold()


def is_coinbase_usd_tracking_account(account: Dict[str, Any]) -> bool:
    """Off-budget Coinbase USD tracking account. On-budget Coinbase One Card is not."""
    if not isinstance(account, dict) or account.get("deleted") or account.get("on_budget"):
        return False
    aid = str(account.get("id") or "")
    if not aid:
        return False
    if aid.startswith(COINBASE_USD_ACCOUNT_PREFIX):
        return True
    name = _normalized_account_name(account.get("name"))
    return (
        name == COINBASE_USD_TRACKING_NAME
        or name.startswith(COINBASE_USD_TRACKING_NAME + " ")
        or name.startswith(COINBASE_USD_TRACKING_NAME + "(")
    )


def coinbase_usd_tracking_ids(accounts: Iterable[Dict[str, Any]]) -> List[str]:
    return [str(a["id"]) for a in accounts or [] if is_coinbase_usd_tracking_account(a)]


def load_payee_display_names(root: Optional[Path] = None) -> Dict[str, str]:
    """Raw YNAB payee → display label. Empty dict on missing/bad file (#669)."""
    path = (root or ROOT) / "treasury" / PAYEE_DISPLAY_NAMES_FILE
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if not isinstance(val, str):
            continue
        src = key.strip()
        dst = val.strip()
        if src and dst:
            out[src] = dst
    return out


def display_payee(raw: str, mapping: Optional[Dict[str, str]] = None) -> str:
    """Map a YNAB payee to its Cash Streams node label. Unmapped → raw."""
    name = (raw or "").strip() or "Unknown payee"
    mapped = (mapping or {}).get(name)
    if isinstance(mapped, str) and mapped.strip():
        return mapped.strip()
    return name


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


def _is_chart_reconcile_payee(tx: Dict[str, Any], row: Dict[str, Any], payee: str) -> bool:
    """Chart-only. Any payee containing 'reconcile', including the parent of a split."""
    names = (
        payee,
        str(row.get("payee_name") or row.get("payee") or ""),
        str(tx.get("payee_name") or tx.get("payee") or ""),
    )
    return any(CHART_RECONCILE_MARK in name.lower() for name in names)


def _iter_countable(
    transactions: Iterable[Dict[str, Any]],
    *,
    start: date,
    end: date,
    on_budget_ids: Optional[set],
    lookup: Dict[str, Tuple[str, str]],
    tracking_outflow_ids: Optional[set] = None,
    extra_skip: Optional[Callable[[Dict[str, Any], Dict[str, Any], str], bool]] = None,
) -> Iterable[Dict[str, Any]]:
    track_ids = {str(x) for x in (tracking_outflow_ids or ())}
    budget_ids = {str(x) for x in on_budget_ids} if on_budget_ids is not None else None
    for tx in transactions or []:
        if not isinstance(tx, dict) or tx.get("deleted"):
            continue
        day = _parse_day(tx.get("date"))
        if day is None or day < start or day > end:
            continue
        aid = str(tx.get("account_id") or "")
        from_tracking = bool(aid) and aid in track_ids
        if budget_ids is not None and aid and aid not in budget_ids and not from_tracking:
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
            if _is_fcc_reconcile(row) or _is_fcc_reconcile(tx):
                continue
            group_name, cat_name = _resolve_category(row if row.get("category_id") or row.get("category_name") else tx, lookup)
            if FCC_RECONCILE_MARK in group_name.lower() or FCC_RECONCILE_MARK in cat_name.lower():
                continue
            if _is_cc_payment(row, group_name) or _is_cc_payment(tx, group_name):
                continue
            amount = _milli_units(row.get("amount"))
            if amount == 0:
                continue
            if from_tracking and amount > 0:
                continue
            payee = str(row.get("payee_name") or row.get("payee") or parent_payee).strip()
            if amount > 0 and _is_coinbase_inflow_payee(payee):
                continue
            if extra_skip is not None and extra_skip(tx, row, payee):
                continue
            yield {
                "date": day.isoformat(),
                "payee": payee,
                "amount": amount,
                "group": group_name,
                "category": cat_name,
            }


def _snapshot_stale(
    data: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    max_hours: float = FEED_STALE_HOURS,
) -> bool:
    iso = data.get("as_of") if data else None
    if not iso:
        return True
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    current = now or datetime.now(timezone.utc)
    return (current - t).total_seconds() / 3600.0 > max_hours


def _mining_contract(mining: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not mining:
        return {
            "status": "ok",
            "error": None,
            "usd": 0.0,
            "payout_btc": 0.0,
            "payout_count": 0,
            "price_usd": None,
            "as_of": None,
            "stale": False,
        }
    status = str(mining.get("status") or "unknown")
    ok = status == "ok"
    return {
        "status": status,
        "error": mining.get("error"),
        "usd": mining.get("usd") if ok else None,
        "payout_btc": mining.get("payout_btc") if ok else None,
        "payout_count": int(mining.get("payout_count") or 0),
        "price_usd": mining.get("price_usd"),
        "as_of": mining.get("as_of"),
        "stale": bool(
            mining["stale"] if mining.get("stale") is not None else not ok
        ),
    }


def _braiins_feed_detail(brai: Dict[str, Any]) -> str:
    """payouts_error / partial_errors from the last Pi snapshot."""
    bits: List[str] = []
    pe = brai.get("payouts_error")
    if pe:
        bits.append("payouts API: " + str(pe))
    partial = brai.get("partial_errors")
    if isinstance(partial, dict):
        for key, val in partial.items():
            if key == "payouts" and pe:
                continue
            bits.append(f"{key}: {val}")
    return "; ".join(bits)


def braiins_unknown_error(kind: str, brai: Optional[Dict[str, Any]] = None) -> str:
    """Cash Streams mining-unknown copy. Pi-timer topology; no terminal hint."""
    snap = brai or {}
    as_of = str(snap.get("as_of") or "unknown")
    detail = _braiins_feed_detail(snap)
    if kind == "missing_file":
        head = "Braiins payout feed missing (no braiins_latest.json)"
    elif kind == "ok_false":
        head = "Braiins payout feed error: " + str(snap.get("error") or "ok=false")
        if detail:
            head += " (" + detail + ")"
    elif kind == "payouts_missing":
        if detail:
            head = (
                "Braiins payout history missing from snapshot ("
                + detail
                + f"; as_of {as_of})"
            )
        else:
            head = (
                "Braiins payout history missing from snapshot "
                f"(ok=true, no payouts list, as_of {as_of})"
            )
    elif kind == "stale":
        head = f"Braiins payout feed stale (as_of {as_of})"
        if detail:
            head += " (" + detail + ")"
    else:
        head = "Braiins payout feed unknown"
        if detail:
            head += " (" + detail + ")"
    return head + ". " + BRAIINS_PRODUCER_HINT


def mining_from_snapshots(
    *,
    start: date,
    end: date,
    root: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Σ(payout_btc × usd_price_at_payout) for confirmed payouts in the window.

    Loud unknown when the Braiins payout list or Coinbase price feed is
    missing/stale. Never returns a silent zero for a missing feed.
    """
    base = (root or ROOT) / "treasury" / "snapshots"
    brai = _load_snapshot(base / "braiins_latest.json")
    cb = _load_snapshot(base / "coinbase_latest.json")
    current = now or datetime.now(timezone.utc)

    def unknown(error: str) -> Dict[str, Any]:
        return {
            "status": "unknown",
            "error": error,
            "usd": None,
            "payout_btc": None,
            "payout_count": 0,
            "price_usd": None,
            "as_of": brai.get("as_of") if brai else None,
            "stale": True,
        }

    if not brai:
        return unknown(braiins_unknown_error("missing_file"))
    if not brai.get("ok"):
        return unknown(braiins_unknown_error("ok_false", brai))
    if "payouts" not in brai or not isinstance(brai.get("payouts"), list):
        return unknown(braiins_unknown_error("payouts_missing", brai))
    if _snapshot_stale(brai, now=current):
        return unknown(braiins_unknown_error("stale", brai))
    if not cb:
        return unknown("Coinbase price feed missing (no coinbase_latest.json)")
    try:
        price = float(cb.get("btc_usd_price"))
    except (TypeError, ValueError):
        price = None
    if price is None or price <= 0:
        err = "Coinbase price feed missing btc_usd_price"
        if cb.get("btc_price_error"):
            err += f": {cb.get('btc_price_error')}"
        return unknown(err)
    if _snapshot_stale(cb, now=current):
        return unknown(
            f"Coinbase price feed stale (as_of {cb.get('as_of') or 'unknown'}). "
            + COINBASE_PRICE_HINT
        )

    usd_total = 0.0
    btc_total = 0.0
    count = 0
    priced: List[Dict[str, Any]] = []
    for row in brai.get("payouts") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").lower() != "confirmed":
            continue
        day = _parse_day(row.get("at"))
        if day is None or day < start or day > end:
            continue
        try:
            btc_f = float(row.get("amount_btc"))
        except (TypeError, ValueError):
            return unknown("Braiins payout in window missing amount_btc")
        if btc_f <= 0:
            continue
        stamped = row.get("usd_price_at_payout")
        try:
            px = float(stamped) if stamped is not None else price
        except (TypeError, ValueError):
            px = price
        if px is None or px <= 0:
            return unknown(
                "Payout in window has no usd_price_at_payout and Coinbase price is unusable"
            )
        usd_total += btc_f * px
        btc_total += btc_f
        count += 1
        priced.append(
            {
                "at": row.get("at"),
                "amount_btc": btc_f,
                "usd_price_at_payout": px,
                "usd": round(btc_f * px, 2),
                "tx_id": row.get("tx_id"),
            }
        )

    return {
        "status": "ok",
        "error": None,
        "usd": _money(usd_total),
        "payout_btc": round(btc_total, 8),
        "payout_count": count,
        "price_usd": price,
        "as_of": brai.get("as_of"),
        "stale": False,
        "payouts": priced,
    }


def build_cash_streams(
    *,
    days: int = DEFAULT_DAYS,
    today: Optional[date] = None,
    transactions: Optional[Sequence[Dict[str, Any]]] = None,
    category_groups: Optional[Sequence[Dict[str, Any]]] = None,
    on_budget_ids: Optional[Iterable[str]] = None,
    tracking_outflow_ids: Optional[Iterable[str]] = None,
    ynab_stale: bool = False,
    ynab_soft_preserved: bool = False,
    ynab_as_of: Optional[str] = None,
    error: Optional[str] = None,
    mining: Optional[Dict[str, Any]] = None,
    payee_display_names: Optional[Dict[str, str]] = None,
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
    mining_out = _mining_contract(mining)
    if error:
        return {
            "ok": False,
            "error": error,
            "window": window,
            "nodes": [],
            "links": [],
            "totals": {"inflow": 0.0, "outflow": 0.0, "retained": 0.0},
            "ynab": ynab,
            "mining": mining_out,
        }

    lookup = category_lookup(category_groups or [])
    budget_ids = set(on_budget_ids) if on_budget_ids is not None else None
    tracking_ids = set(tracking_outflow_ids) if tracking_outflow_ids else set()
    inflows: Dict[str, float] = {}
    group_totals: Dict[str, float] = {}
    cat_totals: Dict[Tuple[str, str], float] = {}

    for row in _iter_countable(
        transactions or [],
        start=start,
        end=end,
        on_budget_ids=budget_ids,
        lookup=lookup,
        tracking_outflow_ids=tracking_ids,
    ):
        amt = row["amount"]
        if amt > 0:
            payee = display_payee(row["payee"] or "Unknown payee", payee_display_names)
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

    mining_usd = 0.0
    if mining_out.get("status") == "ok":
        mining_usd = _money(mining_out.get("usd") or 0.0)
        if mining_usd > 0.004:
            source_nodes.insert(0, (MINING_NODE_ID, MINING_NODE_NAME, mining_usd))

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
            "mining": mining_out,
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
        "mining": mining_out,
    }


def _rolling_dates(start: date, end: date) -> List[date]:
    days: List[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _rolling_mean(values: Sequence[float]) -> float:
    return _money(sum(values) / float(len(values)))


def build_rolling_cash_series(
    *,
    today: Optional[date] = None,
    transactions: Optional[Sequence[Dict[str, Any]]] = None,
    category_groups: Optional[Sequence[Dict[str, Any]]] = None,
    on_budget_ids: Optional[Iterable[str]] = None,
    tracking_outflow_ids: Optional[Iterable[str]] = None,
    ynab_stale: bool = False,
    ynab_as_of: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """90 displayed days of trailing-30-day mean inflow and outflow.

    Seed is ``end - 120 days`` through ``end`` (inclusive), the same bounds
    ``window_bounds(120)`` would use. Each displayed day is the mean of that
    day and the 29 before it, so quiet days count as zero. Outflows are
    positive magnitudes. Mining is not included.

    Shared exclusions stay in ``_iter_countable``. The only extra drop is a
    payee containing ``reconcile``. Lyft, Grubhub, and Turo are that same
    mean over positive amounts ``classify_income_source`` accepts. A source
    that is $0 on every displayed point while some inflow point is not is
    listed in ``source_warnings``.
    """
    end = today or date.today()
    seed_start = end - timedelta(days=ROLLING_SEED_DAYS)
    display_start = end - timedelta(days=ROLLING_DISPLAY_DAYS - 1)
    seed = {
        "start": seed_start.isoformat(),
        "end": end.isoformat(),
        "days": ROLLING_SEED_DAYS,
    }
    window = {
        "start": display_start.isoformat(),
        "end": end.isoformat(),
        "days": ROLLING_DISPLAY_DAYS,
    }
    ynab = {"stale": bool(ynab_stale), "as_of": ynab_as_of}
    base = {
        "seed": seed,
        "window": window,
        "rolling_days": ROLLING_MEAN_DAYS,
        "unit": "usd_per_day",
        "includes_mining": False,
        "chart_exclusions": [
            "shared cash-streams filter",
            "payee contains reconcile",
        ],
        "ynab": ynab,
        "sources": income_source_public(),
        "source_warnings": [],
        "points": [],
    }
    if error:
        return {"ok": False, "error": error, **base}

    seed_days = _rolling_dates(seed_start, end)
    display_index = next(i for i, day in enumerate(seed_days) if day == display_start)
    if display_index < ROLLING_MEAN_DAYS - 1:
        raise ValueError("rolling seed does not cover the first displayed window")

    source_ids = income_source_ids()
    inflow_by = {day.isoformat(): 0.0 for day in seed_days}
    outflow_by = {day.isoformat(): 0.0 for day in seed_days}
    source_by = {
        sid: {day.isoformat(): 0.0 for day in seed_days} for sid in source_ids
    }
    lookup = category_lookup(category_groups or [])
    budget_ids = set(on_budget_ids) if on_budget_ids is not None else None
    tracking_ids = set(tracking_outflow_ids) if tracking_outflow_ids else set()
    for row in _iter_countable(
        transactions or [],
        start=seed_start,
        end=end,
        on_budget_ids=budget_ids,
        lookup=lookup,
        tracking_outflow_ids=tracking_ids,
        extra_skip=_is_chart_reconcile_payee,
    ):
        if row["amount"] > 0:
            inflow_by[row["date"]] = _money(inflow_by[row["date"]] + row["amount"])
            source_id = classify_income_source(row.get("payee"), row.get("category"))
            if source_id in source_by:
                bucket = source_by[source_id]
                bucket[row["date"]] = _money(bucket[row["date"]] + row["amount"])
        elif row["amount"] < 0:
            outflow_by[row["date"]] = _money(outflow_by[row["date"]] + abs(row["amount"]))

    points: List[Dict[str, Any]] = []
    for i, day in enumerate(seed_days):
        if day < display_start:
            continue
        window_days = seed_days[i - (ROLLING_MEAN_DAYS - 1) : i + 1]
        point = {
            "date": day.isoformat(),
            "inflow": _rolling_mean([inflow_by[d.isoformat()] for d in window_days]),
            "outflow": _rolling_mean([outflow_by[d.isoformat()] for d in window_days]),
        }
        for sid in source_ids:
            point[sid] = _rolling_mean(
                [source_by[sid][d.isoformat()] for d in window_days]
            )
        points.append(point)
    if len(points) != ROLLING_DISPLAY_DAYS:
        raise ValueError(f"expected {ROLLING_DISPLAY_DAYS} rolling points, got {len(points)}")
    warnings = _source_warnings(points, source_ids)
    return {
        "ok": True,
        "error": None,
        **base,
        "source_warnings": warnings,
        "points": points,
    }


def _source_warnings(points: Sequence[Dict[str, Any]], source_ids: Sequence[str]) -> List[str]:
    """Sources that read $0 on every displayed point while inflow does not."""
    if not any(float(point.get("inflow") or 0) != 0.0 for point in points):
        return []
    warnings: List[str] = []
    for sid in source_ids:
        if all(float(point.get(sid) or 0) == 0.0 for point in points):
            warnings.append(sid)
    return warnings


def load_rolling_cash_series(
    *,
    today: Optional[date] = None,
    stale: Optional[bool] = None,
    fetch=None,
) -> Dict[str, Any]:
    """Live YNAB pull for the rolling chart. ``fetch`` is injectable for tests.

    Does not widen ``ALLOWED_DAYS`` and does not call ``load_cash_streams``.
    """
    end = today or date.today()
    seed_start = end - timedelta(days=ROLLING_SEED_DAYS)
    fetcher = fetch or fetch_ynab_window
    pulled = fetcher(seed_start.isoformat())
    if not pulled.get("ok"):
        return build_rolling_cash_series(
            today=end,
            error=str(pulled.get("error") or "YNAB fetch failed"),
            ynab_stale=True if stale is None else bool(stale),
            ynab_as_of=pulled.get("as_of"),
        )
    return build_rolling_cash_series(
        today=end,
        transactions=pulled.get("transactions") or [],
        category_groups=pulled.get("category_groups") or [],
        on_budget_ids=pulled.get("on_budget_ids"),
        tracking_outflow_ids=pulled.get("tracking_outflow_ids"),
        ynab_stale=False if stale is None else bool(stale),
        ynab_as_of=pulled.get("as_of"),
    )


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
        tracking_outflow_ids = coinbase_usd_tracking_ids(accounts)
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
        "tracking_outflow_ids": tracking_outflow_ids,
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
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Orchestrate live YNAB pull + Sankey. ``fetch`` is injectable for tests.

    ``stale`` is an explicit overlay for tests. Default freshness is the live
    pull: success → not stale; failure → stale. Balance-snapshot mtimes are
    not this page's clock (#668).
    """
    start, end, days = window_bounds(days, today=today)
    base = root or ROOT
    fetcher = fetch or fetch_ynab_window
    pulled = fetcher(start.isoformat())
    mining = mining_from_snapshots(start=start, end=end, root=base, now=now)
    names = load_payee_display_names(base)
    if not pulled.get("ok"):
        snap_as_of = ynab_as_of_from_snapshots(base)
        fail_stale = True if stale is None else bool(stale)
        return build_cash_streams(
            days=days,
            today=end,
            ynab_stale=fail_stale,
            ynab_soft_preserved=False,
            ynab_as_of=pulled.get("as_of") or snap_as_of,
            error=str(pulled.get("error") or "YNAB fetch failed"),
            mining=mining,
            payee_display_names=names,
        )
    as_of = pulled.get("as_of") or datetime.now(timezone.utc).isoformat()
    ok_stale = False if stale is None else bool(stale)
    return build_cash_streams(
        days=days,
        today=end,
        transactions=pulled.get("transactions") or [],
        category_groups=pulled.get("category_groups") or [],
        on_budget_ids=pulled.get("on_budget_ids"),
        tracking_outflow_ids=pulled.get("tracking_outflow_ids"),
        ynab_stale=ok_stale,
        ynab_soft_preserved=False,
        ynab_as_of=as_of,
        mining=mining,
        payee_display_names=names,
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
