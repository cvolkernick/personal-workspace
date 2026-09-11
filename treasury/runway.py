"""FCC Runway: live YNAB cash-flow forecast (today → +N days).

Canonical source: YNAB transactions + scheduled via ``treasury.ynab_sync``.
Computed per request; never stored. Transfers excluded (same rule as Cash
Streams). Credit-card balances are not part of the starting buffer.
"""

from __future__ import annotations

import calendar
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from treasury.cash_streams import (  # noqa: E402
    ALLOWED_DAYS,
    DEFAULT_DAYS,
    _fallback_cash_stale,
    _is_transfer,
    _iter_countable,
    _milli_units,
    _parse_day,
    category_lookup,
    clamp_days,
    ynab_as_of_from_snapshots,
    ynab_soft_preserved,
)
from treasury.ynab_sync import account_balance_units  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

LOOKBACK_DAYS = 90
DEFAULT_THRESHOLD = 500.0
MAX_THRESHOLD = 1_000_000.0
MIN_DETECT_N = 3
AMOUNT_CV_MAX = 0.3
MIN_CADENCE_DAYS = 6
INTERVAL_CV_MAX = 0.4
LIQUID_TYPES = {"checking", "savings", "cash"}
MAX_OCCURRENCES = 400


def clamp_threshold(raw: Any, default: float = DEFAULT_THRESHOLD) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    if v < 0:
        return 0.0
    if v > MAX_THRESHOLD:
        return MAX_THRESHOLD
    return round(v, 2)


def lookback_bounds(today: Optional[date] = None) -> Tuple[date, date, int]:
    end = today or date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    return start, end, LOOKBACK_DAYS


def horizon_dates(days: int, today: Optional[date] = None) -> List[date]:
    days = clamp_days(days)
    start = today or date.today()
    return [start + timedelta(days=i) for i in range(days)]


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def _next_occurrence(d: date, frequency: str) -> Optional[date]:
    freq = (frequency or "never").strip()
    if freq == "never":
        return None
    if freq == "daily":
        return d + timedelta(days=1)
    if freq == "weekly":
        return d + timedelta(days=7)
    if freq == "everyOtherWeek":
        return d + timedelta(days=14)
    if freq == "every4Weeks":
        return d + timedelta(days=28)
    if freq == "twiceAMonth":
        return d + timedelta(days=15)
    if freq == "monthly":
        return _add_months(d, 1)
    if freq == "everyOtherMonth":
        return _add_months(d, 2)
    if freq == "every3Months":
        return _add_months(d, 3)
    if freq == "every4Months":
        return _add_months(d, 4)
    if freq == "twiceAYear":
        return _add_months(d, 6)
    if freq == "yearly":
        return _add_months(d, 12)
    if freq == "everyOtherYear":
        return _add_months(d, 24)
    return None


def iter_occurrences(
    start: date,
    frequency: str,
    *,
    today: date,
    horizon_end: date,
) -> Iterable[date]:
    d = start
    guard = 0
    while d <= horizon_end and guard < MAX_OCCURRENCES:
        if d >= today:
            yield d
        nxt = _next_occurrence(d, frequency)
        if nxt is None or nxt <= d:
            break
        d = nxt
        guard += 1


def starting_buffer(accounts: Optional[Sequence[Dict[str, Any]]]) -> Tuple[float, List[Dict[str, Any]]]:
    """Sum of open on-budget checking/savings/cash. Credit cards excluded."""
    liquid: List[Dict[str, Any]] = []
    total = 0.0
    for acct in accounts or []:
        if not isinstance(acct, dict):
            continue
        if acct.get("deleted") or acct.get("closed") or not acct.get("on_budget"):
            continue
        typ = str(acct.get("type") or "").strip()
        if typ not in LIQUID_TYPES:
            continue
        bal = round(account_balance_units(acct), 2)
        total += bal
        liquid.append(
            {
                "id": str(acct.get("id") or ""),
                "name": str(acct.get("name") or "").strip() or "Account",
                "type": typ,
                "balance": bal,
            }
        )
    return round(total, 2), liquid


def _empty(
    *,
    days: int,
    today: date,
    threshold: float,
    error: Optional[str],
    ynab_stale: bool,
    ynab_soft_preserved: bool,
    ynab_as_of: Optional[str],
) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": error or "Runway failed",
        "today": today.isoformat(),
        "days": days,
        "threshold": threshold,
        "starting_buffer": 0.0,
        "daily": [],
        "bill_events": [],
        "assumptions": {},
        "ynab": {
            "stale": bool(ynab_stale),
            "soft_preserved": bool(ynab_soft_preserved),
            "as_of": ynab_as_of,
        },
    }


def _bill_event(day: date, payee: str, amount: float, category: str = "") -> Dict[str, Any]:
    return {
        "date": day.isoformat(),
        "payee": payee or (category or "Bill"),
        "amount": round(abs(amount), 2),
        "category": category or "",
    }


def expand_scheduled(
    scheduled: Sequence[Dict[str, Any]],
    *,
    today: date,
    horizon_end: date,
    on_budget_ids: Optional[set],
    lookup: Dict[str, Tuple[str, str]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (inflow events, outflow/bill events) expanded over the horizon."""
    inflows: List[Dict[str, Any]] = []
    outflows: List[Dict[str, Any]] = []
    for stx in scheduled or []:
        if not isinstance(stx, dict) or stx.get("deleted"):
            continue
        if on_budget_ids is not None:
            aid = stx.get("account_id")
            if aid and aid not in on_budget_ids:
                continue
        subs = stx.get("subtransactions") or []
        rows = list(subs) if subs else [stx]
        parent_payee = str(stx.get("payee_name") or stx.get("payee") or "").strip()
        freq = str(stx.get("frequency") or "never")
        start_d = _parse_day(stx.get("date_next") or stx.get("date"))
        if start_d is None:
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("deleted"):
                continue
            if _is_transfer(row) or (not subs and _is_transfer(stx)):
                continue
            amount = _milli_units(row.get("amount"))
            if amount == 0:
                continue
            payee = str(row.get("payee_name") or row.get("payee") or parent_payee).strip()
            cid = row.get("category_id") or stx.get("category_id")
            if cid and str(cid) in lookup:
                _group, cat = lookup[str(cid)]
            else:
                cat = str(row.get("category_name") or stx.get("category_name") or "").strip()
            for day in iter_occurrences(
                start_d, freq, today=today, horizon_end=horizon_end
            ):
                event = _bill_event(day, payee, amount, cat)
                if amount > 0:
                    inflows.append(event)
                else:
                    outflows.append(event)
    return inflows, outflows


def detect_recurring_bills(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stable amount (CV < 0.3) + regular cadence ≥ 6 days, grouped by category."""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if float(row.get("amount") or 0) >= 0:
            continue
        cat = str(row.get("category") or "").strip() or "Uncategorized"
        grouped[cat].append(row)

    detected: List[Dict[str, Any]] = []
    for cat, items in grouped.items():
        dated: List[Tuple[date, Dict[str, Any]]] = []
        for row in items:
            day = _parse_day(row.get("date"))
            if day is None:
                continue
            dated.append((day, row))
        dated.sort(key=lambda pair: pair[0])
        if len(dated) < MIN_DETECT_N:
            continue
        amounts = [abs(float(row["amount"])) for _d, row in dated]
        mean_amt = statistics.mean(amounts)
        if mean_amt <= 0:
            continue
        cv = statistics.pstdev(amounts) / mean_amt
        if cv >= AMOUNT_CV_MAX:
            continue
        days_list = [d for d, _row in dated]
        intervals = [(b - a).days for a, b in zip(days_list, days_list[1:]) if (b - a).days > 0]
        if not intervals:
            continue
        cadence = statistics.median(intervals)
        if cadence < MIN_CADENCE_DAYS:
            continue
        mean_iv = statistics.mean(intervals)
        if mean_iv > 0 and (statistics.pstdev(intervals) / mean_iv) > INTERVAL_CV_MAX:
            continue
        payees = [str(row.get("payee") or cat).strip() or cat for _d, row in dated]
        payee = Counter(payees).most_common(1)[0][0]
        detected.append(
            {
                "category": cat,
                "payee": payee,
                "amount": round(mean_amt, 2),
                "cadence_days": int(round(float(cadence))),
                "last_date": days_list[-1],
            }
        )
    return detected


def expand_detected(
    detected: Sequence[Dict[str, Any]],
    *,
    today: date,
    horizon_end: date,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for bill in detected:
        cadence = int(bill.get("cadence_days") or 0)
        if cadence < MIN_CADENCE_DAYS:
            continue
        last = bill["last_date"]
        if not isinstance(last, date):
            parsed = _parse_day(last)
            if parsed is None:
                continue
            last = parsed
        d = last + timedelta(days=cadence)
        guard = 0
        while d < today and guard < MAX_OCCURRENCES:
            d += timedelta(days=cadence)
            guard += 1
        while d <= horizon_end and guard < MAX_OCCURRENCES:
            events.append(
                _bill_event(
                    d,
                    str(bill.get("payee") or ""),
                    float(bill.get("amount") or 0),
                    str(bill.get("category") or ""),
                )
            )
            d += timedelta(days=cadence)
            guard += 1
    return events


def build_runway(
    *,
    days: int = DEFAULT_DAYS,
    threshold: Any = DEFAULT_THRESHOLD,
    today: Optional[date] = None,
    transactions: Optional[Sequence[Dict[str, Any]]] = None,
    scheduled: Optional[Sequence[Dict[str, Any]]] = None,
    category_groups: Optional[Sequence[Dict[str, Any]]] = None,
    accounts: Optional[Sequence[Dict[str, Any]]] = None,
    on_budget_ids: Optional[Iterable[str]] = None,
    ynab_stale: bool = False,
    ynab_soft_preserved: bool = False,
    ynab_as_of: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the forecast payload. Pure: no I/O."""
    days = clamp_days(days)
    threshold_f = clamp_threshold(threshold)
    end = today or date.today()
    if error:
        return _empty(
            days=days,
            today=end,
            threshold=threshold_f,
            error=error,
            ynab_stale=ynab_stale,
            ynab_soft_preserved=ynab_soft_preserved,
            ynab_as_of=ynab_as_of,
        )

    lookback_start, lookback_end, lookback_days = lookback_bounds(end)
    dates = horizon_dates(days, today=end)
    horizon_end = dates[-1] if dates else end
    lookup = category_lookup(category_groups or [])
    budget_ids = set(on_budget_ids) if on_budget_ids is not None else None

    countable = list(
        _iter_countable(
            transactions or [],
            start=lookback_start,
            end=lookback_end,
            on_budget_ids=budget_ids,
            lookup=lookup,
        )
    )
    inflows = [row for row in countable if float(row["amount"]) > 0]
    outflows = [row for row in countable if float(row["amount"]) < 0]
    income_total = round(sum(float(r["amount"]) for r in inflows), 2)
    income_rate = round(income_total / float(lookback_days), 2) if lookback_days else 0.0

    scheduled_in, scheduled_out = expand_scheduled(
        scheduled or [],
        today=end,
        horizon_end=horizon_end,
        on_budget_ids=budget_ids,
        lookup=lookup,
    )

    if scheduled_out:
        bill_events = scheduled_out
        bill_source = "scheduled"
    else:
        detected = detect_recurring_bills(outflows)
        bill_events = expand_detected(detected, today=end, horizon_end=horizon_end)
        bill_source = "detected" if bill_events else "none"

    bill_categories = {
        str(ev.get("category") or "").strip()
        for ev in bill_events
        if str(ev.get("category") or "").strip()
    }
    variable_rows = [
        row
        for row in outflows
        if str(row.get("category") or "").strip() not in bill_categories
    ]
    variable_total = round(sum(abs(float(r["amount"])) for r in variable_rows), 2)
    variable_rate = (
        round(variable_total / float(lookback_days), 2) if lookback_days else 0.0
    )

    bills_by_day: Dict[str, float] = defaultdict(float)
    for ev in bill_events:
        bills_by_day[ev["date"]] += float(ev["amount"])

    income_extra: Dict[str, float] = defaultdict(float)
    for ev in scheduled_in:
        income_extra[ev["date"]] += float(ev["amount"])

    seed, liquid = starting_buffer(accounts)
    daily: List[Dict[str, Any]] = []
    prev = seed
    for day in dates:
        key = day.isoformat()
        income = round(income_rate + income_extra.get(key, 0.0), 2)
        bills = round(bills_by_day.get(key, 0.0), 2)
        variable = variable_rate
        net = round(income - bills - variable, 2)
        buffer = round(prev + net, 2)
        daily.append(
            {
                "date": key,
                "income": income,
                "bills": bills,
                "variable": variable,
                "net": net,
                "buffer": buffer,
            }
        )
        prev = buffer

    source_label = {
        "scheduled": "YNAB scheduled transactions",
        "detected": "detected from history (stable amount, cadence ≥ 6d)",
        "none": "none (no scheduled or detectable bills)",
    }[bill_source]

    return {
        "ok": True,
        "today": end.isoformat(),
        "days": days,
        "threshold": threshold_f,
        "starting_buffer": seed,
        "daily": daily,
        "bill_events": [
            {"date": ev["date"], "payee": ev["payee"], "amount": ev["amount"]}
            for ev in sorted(bill_events, key=lambda e: (e["date"], e["payee"]))
        ],
        "assumptions": {
            "lookback_days": lookback_days,
            "lookback_start": lookback_start.isoformat(),
            "lookback_end": lookback_end.isoformat(),
            "income_rate_daily": income_rate,
            "variable_rate_daily": variable_rate,
            "bill_source": bill_source,
            "bill_source_label": source_label,
            "scheduled_inflow_count": len(scheduled_in),
            "bill_event_count": len(bill_events),
            "starting_buffer": seed,
            "liquid_accounts": liquid,
            "horizon_end": horizon_end.isoformat(),
        },
        "ynab": {
            "stale": bool(ynab_stale),
            "soft_preserved": bool(ynab_soft_preserved),
            "as_of": ynab_as_of,
        },
    }


def fetch_ynab_runway(since: str) -> Dict[str, Any]:
    """Live YNAB transactions + scheduled + accounts. Token path is ynab_sync's."""
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
        scheduled = (
            ynab_get(f"/budgets/{bid}/scheduled_transactions", token)
            .get("data", {})
            .get("scheduled_transactions")
            or []
        )
    except Exception as e:
        return {"ok": False, "error": f"YNAB fetch failed: {e}"}
    return {
        "ok": True,
        "transactions": txs,
        "scheduled": scheduled,
        "category_groups": groups,
        "accounts": accounts,
        "on_budget_ids": on_budget_ids,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "budget_name": budget.get("name"),
    }


def load_runway(
    *,
    days: int = DEFAULT_DAYS,
    threshold: Any = DEFAULT_THRESHOLD,
    today: Optional[date] = None,
    stale: Optional[bool] = None,
    root: Optional[Path] = None,
    fetch=None,
) -> Dict[str, Any]:
    """Orchestrate live YNAB pull + forecast. ``fetch`` is injectable for tests."""
    days = clamp_days(days)
    end = today or date.today()
    lookback_start, _lookback_end, _n = lookback_bounds(end)
    base = root or ROOT
    stale_flag = _fallback_cash_stale(base) if stale is None else bool(stale)
    soft = ynab_soft_preserved(base)
    snap_as_of = ynab_as_of_from_snapshots(base)
    fetcher = fetch or fetch_ynab_runway
    pulled = fetcher(lookback_start.isoformat())
    if not pulled.get("ok"):
        return build_runway(
            days=days,
            threshold=threshold,
            today=end,
            ynab_stale=stale_flag,
            ynab_soft_preserved=soft,
            ynab_as_of=snap_as_of,
            error=str(pulled.get("error") or "YNAB fetch failed"),
        )
    as_of = pulled.get("as_of") or datetime.now(timezone.utc).isoformat()
    return build_runway(
        days=days,
        threshold=threshold,
        today=end,
        transactions=pulled.get("transactions") or [],
        scheduled=pulled.get("scheduled") or [],
        category_groups=pulled.get("category_groups") or [],
        accounts=pulled.get("accounts") or [],
        on_budget_ids=pulled.get("on_budget_ids"),
        ynab_stale=stale_flag,
        ynab_soft_preserved=soft,
        ynab_as_of=as_of,
    )
