"""FCC Runway: live cash-flow forecast (today → +N days).

Bills SoT: Personal Expense Sheet via ``treasury.expenses_sync`` (Essential +
funded unique Fleet). YNAB scheduled only if the sheet is unreachable;
recurrence detection last. Stale/missing sheet is a loud error, never a
silent fallback. Income from YNAB actuals; transfers excluded. Forecast is
computed per request and never stored.

Min-buffer SoT: ``treasury/config.json`` policy ``min_liquid_buffer_usd``
(daily forecast floor). Hardcoded ``DEFAULT_THRESHOLD`` is a labeled fallback
only (config missing/unparseable) — never a silent second default.
``?threshold=`` is a per-view override.

Sheet Forecast tab "Buffer target" is the monthly planning floor, not the
Runway default. Config and sheet may differ; disagreement is visible drift
on the Assumptions card, never papered over. Distinct from HY LTV
(``cb_loan_buffer_usdc``), card float, and ``rh_bp_floor``.
"""

from __future__ import annotations

import calendar
import csv
import io
import re
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
DEFAULT_THRESHOLD = 200.0  # labeled fallback only; canonical is policy.min_liquid_buffer_usd
MAX_THRESHOLD = 1_000_000.0
POLICY_MIN_BUFFER_KEY = "min_liquid_buffer_usd"
FORECAST_TAB = "Forecast"
_BUFFER_TARGET_LABEL = re.compile(r"^buffer\s*target$", re.I)
_UNSET = object()
MIN_DETECT_N = 3
AMOUNT_CV_MAX = 0.3
MIN_CADENCE_DAYS = 6
INTERVAL_CV_MAX = 0.4
LIQUID_TYPES = {"checking", "savings", "cash"}
MAX_OCCURRENCES = 400
SHEET_STALE_HOURS = 6.0
PROGRESSIVE_MIN = 500.0
PROGRESSIVE_MAX = 1500.0
MONTH_TOKEN = re.compile(
    r"\b("
    r"january|february|march|april|may|june|july|august|september|"
    r"october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec"
    r")\b",
    re.I,
)
SOURCE_LABELS = {
    "sheet": "Personal Expense Sheet (Essential + funded unique Fleet)",
    "scheduled": "YNAB scheduled transactions (sheet unreachable)",
    "detected": "detected from history (last resort; sheet unreachable)",
    "none": "none (sheet unreachable; no scheduled or detectable bills)",
}


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


def _fmt_usd(n: float) -> str:
    if abs(n - round(n)) < 1e-9:
        return f"${int(round(n))}"
    return f"${n:.2f}"


def _parse_money_cell(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("none", "nan", "-"):
        return None
    s = s.replace("$", "").replace(",", "").replace("%", "").strip()
    if not s:
        return None
    try:
        return round(float(s), 2)
    except (TypeError, ValueError):
        return None


def policy_min_buffer(policy: Optional[Dict[str, Any]]) -> Tuple[Optional[float], str]:
    """Return (value, status) for policy.min_liquid_buffer_usd.

    status: ok | missing | unparseable
    """
    if not isinstance(policy, dict) or POLICY_MIN_BUFFER_KEY not in policy:
        return None, "missing"
    raw = policy.get(POLICY_MIN_BUFFER_KEY)
    if raw is None or raw == "":
        return None, "missing"
    parsed = _parse_money_cell(raw)
    if parsed is None:
        return None, "unparseable"
    return clamp_threshold(parsed), "ok"


def parse_forecast_buffer_target(csv_text: str) -> Optional[float]:
    """First numeric cell on the Forecast-tab 'Buffer target' row."""
    if not csv_text or not str(csv_text).strip():
        return None
    text = str(csv_text).lstrip("\ufeff")
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        label = (row[0] or "").strip()
        if not _BUFFER_TARGET_LABEL.match(label):
            continue
        for cell in row[1:]:
            parsed = _parse_money_cell(cell)
            if parsed is not None:
                return parsed
    return None


def forecast_buffer_from_expenses(expenses: Optional[Dict[str, Any]]) -> Optional[float]:
    """Read a Forecast-tab buffer target already present on an expenses snapshot."""
    if not isinstance(expenses, dict):
        return None
    tabs = expenses.get("tabs")
    if not isinstance(tabs, dict):
        return None
    block = tabs.get(FORECAST_TAB)
    if not isinstance(block, dict):
        return None
    for key in ("buffer_target_usd", "buffer_target", "Buffer target"):
        parsed = _parse_money_cell(block.get(key))
        if parsed is not None:
            return parsed
    return None


def fetch_forecast_buffer_target(*, root: Optional[Path] = None) -> Optional[float]:
    """Live-fetch Forecast tab Buffer target. None on any failure (never raises)."""
    try:
        from treasury.adapters import load_config
        from treasury.expenses_sync import DEFAULT_SHEET_ID, fetch_sheet_csv
    except Exception:
        return None
    try:
        cfg_path = (root / "treasury" / "config.json") if root is not None else None
        cfg = load_config(cfg_path)
        gcfg = (cfg or {}).get("expenses_sheet") or {}
        sid = gcfg.get("sheet_id") or DEFAULT_SHEET_ID
        csv_text = fetch_sheet_csv(sid, FORECAST_TAB, timeout=8.0)
        return parse_forecast_buffer_target(csv_text)
    except Exception:
        return None


def resolve_min_buffer(
    *,
    explicit: Any = None,
    policy: Optional[Dict[str, Any]] = None,
    sheet_forecast_buffer: Any = None,
) -> Dict[str, Any]:
    """Pick the Runway min-buffer and name its source.

    Config policy is canonical for the daily forecast floor. An explicit
    ``?threshold=`` overrides per view. Hardcoded DEFAULT_THRESHOLD is used
    only when the policy key is missing or unparseable, and that fallback is
    labeled. Sheet Forecast Buffer target is compared for drift; it does not
    drive the default.
    """
    config_usd, config_status = policy_min_buffer(policy)
    has_explicit = explicit is not None and str(explicit).strip() != ""
    if has_explicit:
        default_for_clamp = config_usd if config_status == "ok" else DEFAULT_THRESHOLD
        used = clamp_threshold(explicit, default=default_for_clamp)
        source = "explicit"
        label = (
            f"Min buffer {_fmt_usd(used)} from ?threshold= (per-view override)"
        )
        if config_status == "ok" and config_usd is not None:
            label += f"; policy default {_fmt_usd(config_usd)}"
        elif config_status == "unparseable":
            label += "; policy min_liquid_buffer_usd unparseable"
        else:
            label += "; policy min_liquid_buffer_usd missing"
    elif config_status == "ok" and config_usd is not None:
        used = config_usd
        source = "treasury_policy"
        label = (
            f"Min buffer {_fmt_usd(used)} from treasury policy "
            f"({POLICY_MIN_BUFFER_KEY})"
        )
    else:
        used = DEFAULT_THRESHOLD
        source = "fallback_default"
        why = "unparseable" if config_status == "unparseable" else "missing"
        label = (
            f"Min buffer {_fmt_usd(used)} hardcoded fallback "
            f"(treasury policy {POLICY_MIN_BUFFER_KEY} {why})"
        )

    sheet_usd = _parse_money_cell(sheet_forecast_buffer)
    drift = (
        sheet_usd is not None
        and config_usd is not None
        and abs(sheet_usd - config_usd) > 0.009
    )
    return {
        "usd": used,
        "source": source,
        "source_label": label,
        "config_usd": config_usd,
        "config_status": config_status,
        "fallback_usd": DEFAULT_THRESHOLD,
        "policy_key": POLICY_MIN_BUFFER_KEY,
        "role": "daily forecast floor (canonical: treasury/config.json policy)",
        "sheet_forecast_buffer_usd": sheet_usd,
        "sheet_forecast_role": (
            "monthly planning floor (Forecast tab Buffer target; not the Runway default)"
        ),
        "sheet_config_drift": drift,
    }


def min_buffer_assumptions(resolved: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "min_buffer_usd": resolved["usd"],
        "min_buffer_source": resolved["source"],
        "min_buffer_source_label": resolved["source_label"],
        "min_buffer_config_usd": resolved["config_usd"],
        "min_buffer_fallback_usd": resolved["fallback_usd"],
        "min_buffer_policy_key": resolved["policy_key"],
        "min_buffer_role": resolved["role"],
        "sheet_forecast_buffer_usd": resolved["sheet_forecast_buffer_usd"],
        "sheet_forecast_role": resolved["sheet_forecast_role"],
        "sheet_config_drift": resolved["sheet_config_drift"],
    }


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
    sheet: Optional[Dict[str, Any]] = None,
    assumptions: Optional[Dict[str, Any]] = None,
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
        "assumptions": assumptions or {},
        "ynab": {
            "stale": bool(ynab_stale),
            "soft_preserved": bool(ynab_soft_preserved),
            "as_of": ynab_as_of,
        },
        "sheet": sheet
        or {
            "stale": False,
            "missing": False,
            "unreachable": False,
            "as_of": None,
            "error": error,
        },
    }


def _parse_as_of(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except (TypeError, ValueError):
        return None


def _amounts_close(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= max(1.0, 0.08 * max(abs(float(a)), abs(float(b)), 1.0))


def sheet_item_core_name(name: str) -> str:
    s = MONTH_TOKEN.sub(" ", name or "")
    s = re.sub(r"[^a-z0-9\s]", " ", s.lower())
    return " ".join(s.split())


def native_sheet_cadence(item: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    """Pick one native cadence. Finer columns are conversions of the same obligation."""
    daily = item.get("daily")
    weekly = item.get("weekly")
    biweekly = item.get("biweekly")
    monthly = item.get("monthly")
    quarterly = item.get("quarterly")
    annually = item.get("annually")
    if monthly is not None and annually is not None and _amounts_close(monthly, annually):
        return ("once", float(monthly))
    if annually is not None and monthly is not None and _amounts_close(annually, float(monthly) * 12):
        return ("monthly", float(monthly))
    if annually is not None and quarterly is not None and _amounts_close(annually, float(quarterly) * 4):
        return ("quarterly", float(quarterly))
    if annually is not None and biweekly is not None and _amounts_close(annually, float(biweekly) * 26):
        return ("biweekly", float(biweekly))
    if annually is not None and weekly is not None and _amounts_close(annually, float(weekly) * 52):
        return ("weekly", float(weekly))
    if annually is not None and daily is not None and _amounts_close(annually, float(daily) * 365):
        return ("daily", float(daily))
    if annually is not None and float(annually) > 0:
        return ("annually", float(annually))
    if quarterly is not None and float(quarterly) > 0:
        return ("quarterly", float(quarterly))
    if monthly is not None and float(monthly) > 0:
        return ("monthly", float(monthly))
    if biweekly is not None and float(biweekly) > 0:
        return ("biweekly", float(biweekly))
    if weekly is not None and float(weekly) > 0:
        return ("weekly", float(weekly))
    if daily is not None and float(daily) > 0:
        return ("daily", float(daily))
    return None


def _advance_sheet_cadence(d: date, cadence: str) -> Optional[date]:
    if cadence == "daily":
        return d + timedelta(days=1)
    if cadence == "weekly":
        return d + timedelta(days=7)
    if cadence == "biweekly":
        return d + timedelta(days=14)
    if cadence == "monthly":
        return _add_months(d, 1)
    if cadence == "quarterly":
        return _add_months(d, 3)
    if cadence == "annually":
        return _add_months(d, 12)
    return None


def sheet_bill_items(expenses: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Essential + funded unique Fleet. Capital/wishlist tabs are not burn bills."""
    from treasury.expenses_sync import (
        FLEET_TAB,
        essential_tab_block,
        funded_unique_fleet_items,
    )

    tabs = (expenses or {}).get("tabs") or {}
    essential = essential_tab_block(tabs)
    ess_items = [i for i in (essential.get("items") or []) if isinstance(i, dict)]
    fleet_block = tabs.get(FLEET_TAB) if isinstance(tabs.get(FLEET_TAB), dict) else {}
    fleet_items = [i for i in (fleet_block.get("items") or []) if isinstance(i, dict)]
    unique_fleet = funded_unique_fleet_items(ess_items, fleet_items)
    out: List[Dict[str, Any]] = []
    for i in ess_items:
        rec = dict(i)
        rec.setdefault("tab", "Essential")
        out.append(rec)
    for i in unique_fleet:
        rec = dict(i)
        rec.setdefault("tab", "Fleet")
        out.append(rec)
    return out


def expand_sheet_bills(
    items: Sequence[Dict[str, Any]],
    *,
    today: date,
    horizon_end: date,
) -> List[Dict[str, Any]]:
    from treasury.expenses_sync import parse_sheet_date

    events: List[Dict[str, Any]] = []
    for item in items:
        native = native_sheet_cadence(item)
        if not native:
            continue
        cadence, amount = native
        if amount <= 0:
            continue
        payee = str(item.get("item") or "").strip() or "Sheet bill"
        cat = sheet_item_core_name(payee)
        parsed = parse_sheet_date(item.get("date"))
        start = parsed.date() if parsed else None
        if cadence == "once":
            if start is not None and today <= start <= horizon_end:
                events.append(_bill_event(start, payee, amount, cat))
            continue
        d = start or today
        guard = 0
        while d < today and guard < MAX_OCCURRENCES:
            nxt = _advance_sheet_cadence(d, cadence)
            if nxt is None or nxt <= d:
                break
            d = nxt
            guard += 1
        while d <= horizon_end and guard < MAX_OCCURRENCES:
            if d >= today:
                events.append(_bill_event(d, payee, amount, cat))
            nxt = _advance_sheet_cadence(d, cadence)
            if nxt is None or nxt <= d:
                break
            d = nxt
            guard += 1
    return events


def classify_sheet(
    expenses: Optional[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """usable / stale / missing / unreachable. Stale/missing is never a silent fallback."""
    from treasury.expenses_sync import essential_tab_block

    now = now or datetime.now(timezone.utc)
    if not expenses or expenses.get("source") in (None, "empty"):
        err = "Personal Expense Sheet missing"
        if expenses:
            err = str(expenses.get("live_error") or err)
        unreachable = bool(expenses and expenses.get("live_error"))
        return {
            "usable": False,
            "unreachable": unreachable,
            "stale": False,
            "missing": True,
            "error": (
                f"{err}. Sheet unreachable — falling back to YNAB scheduled."
                if unreachable
                else "Personal Expense Sheet missing. Not a silent fallback."
            ),
            "as_of": (expenses or {}).get("as_of"),
            "snapshot": None,
        }
    as_of_raw = expenses.get("as_of")
    as_of = _parse_as_of(as_of_raw)
    age_h = None
    if as_of is not None:
        age_h = (now - as_of).total_seconds() / 3600.0
    stale = age_h is None or age_h > SHEET_STALE_HOURS
    tabs = expenses.get("tabs") or {}
    has_essential = bool(essential_tab_block(tabs))
    if stale:
        return {
            "usable": False,
            "unreachable": False,
            "stale": True,
            "missing": False,
            "error": (
                f"Personal Expense Sheet stale as_of {as_of_raw or 'unknown'} "
                f"(>{SHEET_STALE_HOURS:.0f}h). Not a silent fallback."
            ),
            "as_of": as_of_raw,
            "snapshot": expenses,
        }
    if not has_essential:
        return {
            "usable": False,
            "unreachable": False,
            "stale": False,
            "missing": True,
            "error": "Personal Expense Sheet missing Essential tab. Not a silent fallback.",
            "as_of": as_of_raw,
            "snapshot": expenses,
        }
    return {
        "usable": True,
        "unreachable": False,
        "stale": False,
        "missing": False,
        "error": None,
        "as_of": as_of_raw,
        "snapshot": expenses,
    }


def split_progressive_one_offs(
    inflows: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Exclude the $1k Progressive one-off from the trailing income average."""
    kept: List[Dict[str, Any]] = []
    flagged: List[Dict[str, Any]] = []
    for row in inflows:
        payee = str(row.get("payee") or "")
        amt = float(row.get("amount") or 0)
        if "progressive" in payee.lower() and PROGRESSIVE_MIN <= amt <= PROGRESSIVE_MAX:
            flagged.append(
                {
                    "date": row.get("date"),
                    "payee": payee,
                    "amount": round(amt, 2),
                    "reason": "one-off excluded from income average",
                }
            )
        else:
            kept.append(row)
    return kept, flagged


def _is_bill_like_row(row: Dict[str, Any], bill_keys: set) -> bool:
    cat = str(row.get("category") or "").strip().lower()
    payee = str(row.get("payee") or "").strip().lower()
    for key in bill_keys:
        if not key:
            continue
        if key == cat or key in cat or (cat and cat in key):
            return True
        if key in payee or (payee and payee in key):
            return True
    return False


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
    threshold: Any = None,
    today: Optional[date] = None,
    transactions: Optional[Sequence[Dict[str, Any]]] = None,
    scheduled: Optional[Sequence[Dict[str, Any]]] = None,
    category_groups: Optional[Sequence[Dict[str, Any]]] = None,
    accounts: Optional[Sequence[Dict[str, Any]]] = None,
    on_budget_ids: Optional[Iterable[str]] = None,
    expenses: Optional[Dict[str, Any]] = None,
    sheet_unreachable: bool = False,
    ynab_stale: bool = False,
    ynab_soft_preserved: bool = False,
    ynab_as_of: Optional[str] = None,
    error: Optional[str] = None,
    policy: Optional[Dict[str, Any]] = None,
    sheet_forecast_buffer: Any = None,
) -> Dict[str, Any]:
    """Build the forecast payload. Pure: no I/O."""
    days = clamp_days(days)
    sheet_floor = _parse_money_cell(sheet_forecast_buffer)
    if sheet_floor is None:
        sheet_floor = forecast_buffer_from_expenses(expenses)
    resolved = resolve_min_buffer(
        explicit=threshold,
        policy=policy,
        sheet_forecast_buffer=sheet_floor,
    )
    threshold_f = float(resolved["usd"])
    buffer_assumptions = min_buffer_assumptions(resolved)
    end = today or date.today()
    sheet_state = classify_sheet(expenses) if expenses is not None else {
        "usable": False,
        "unreachable": bool(sheet_unreachable),
        "stale": False,
        "missing": not sheet_unreachable,
        "error": (
            None
            if sheet_unreachable
            else "Personal Expense Sheet missing. Not a silent fallback."
        ),
        "as_of": None,
        "snapshot": None,
    }
    if sheet_unreachable:
        sheet_state["unreachable"] = True
        sheet_state["usable"] = False
    sheet_payload = {
        "stale": bool(sheet_state.get("stale")),
        "missing": bool(sheet_state.get("missing")),
        "unreachable": bool(sheet_state.get("unreachable")),
        "as_of": sheet_state.get("as_of"),
        "error": sheet_state.get("error"),
    }
    if error:
        return _empty(
            days=days,
            today=end,
            threshold=threshold_f,
            error=error,
            ynab_stale=ynab_stale,
            ynab_soft_preserved=ynab_soft_preserved,
            ynab_as_of=ynab_as_of,
            sheet=sheet_payload,
            assumptions=buffer_assumptions,
        )
    if not sheet_state.get("usable") and not sheet_state.get("unreachable"):
        return _empty(
            days=days,
            today=end,
            threshold=threshold_f,
            error=str(sheet_state.get("error") or "Personal Expense Sheet unavailable"),
            ynab_stale=ynab_stale,
            ynab_soft_preserved=ynab_soft_preserved,
            ynab_as_of=ynab_as_of,
            sheet=sheet_payload,
            assumptions=buffer_assumptions,
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
    income_kept, income_one_offs = split_progressive_one_offs(inflows)
    income_total = round(sum(float(r["amount"]) for r in income_kept), 2)
    income_rate = round(income_total / float(lookback_days), 2) if lookback_days else 0.0

    scheduled_in, scheduled_out = expand_scheduled(
        scheduled or [],
        today=end,
        horizon_end=horizon_end,
        on_budget_ids=budget_ids,
        lookup=lookup,
    )

    if sheet_state.get("usable"):
        sheet_items = sheet_bill_items(sheet_state.get("snapshot") or expenses)
        bill_events = expand_sheet_bills(sheet_items, today=end, horizon_end=horizon_end)
        bill_source = "sheet"
    elif scheduled_out:
        bill_events = scheduled_out
        bill_source = "scheduled"
    else:
        detected = detect_recurring_bills(outflows)
        bill_events = expand_detected(detected, today=end, horizon_end=horizon_end)
        bill_source = "detected" if bill_events else "none"

    bill_keys = {
        str(ev.get("category") or "").strip().lower()
        for ev in bill_events
        if str(ev.get("category") or "").strip()
    }
    for ev in bill_events:
        core = sheet_item_core_name(str(ev.get("payee") or ""))
        if core:
            bill_keys.add(core)
    variable_rows = [
        row for row in outflows if not _is_bill_like_row(row, bill_keys)
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

    source_label = SOURCE_LABELS[bill_source]

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
            "income_one_offs": income_one_offs,
            "income_one_offs_excluded": round(
                sum(float(x["amount"]) for x in income_one_offs), 2
            ),
            **buffer_assumptions,
        },
        "ynab": {
            "stale": bool(ynab_stale),
            "soft_preserved": bool(ynab_soft_preserved),
            "as_of": ynab_as_of,
        },
        "sheet": sheet_payload,
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
    threshold: Any = None,
    today: Optional[date] = None,
    stale: Optional[bool] = None,
    root: Optional[Path] = None,
    fetch=None,
    expenses_fetch=None,
    policy: Optional[Dict[str, Any]] = None,
    forecast_fetch: Any = _UNSET,
) -> Dict[str, Any]:
    """Orchestrate sheet + YNAB pull + forecast. Fetchers are injectable for tests."""
    days = clamp_days(days)
    end = today or date.today()
    lookback_start, _lookback_end, _n = lookback_bounds(end)
    base = root or ROOT
    stale_flag = _fallback_cash_stale(base) if stale is None else bool(stale)
    soft = ynab_soft_preserved(base)
    snap_as_of = ynab_as_of_from_snapshots(base)
    exp_fetcher = expenses_fetch
    if exp_fetcher is None:
        from treasury.expenses_sync import fetch_expenses

        exp_fetcher = fetch_expenses
    expenses = exp_fetcher()
    if policy is None:
        from treasury.adapters import load_config

        cfg = load_config(base / "treasury" / "config.json")
        raw_pol = (cfg or {}).get("policy") if isinstance(cfg, dict) else None
        policy = raw_pol if isinstance(raw_pol, dict) else {}
    sheet_forecast = forecast_buffer_from_expenses(expenses)
    if sheet_forecast is None:
        if forecast_fetch is _UNSET:
            sheet_forecast = fetch_forecast_buffer_target(root=base)
        elif callable(forecast_fetch):
            try:
                sheet_forecast = forecast_fetch()
            except Exception:
                sheet_forecast = None
    sheet_state = classify_sheet(expenses)
    ynab_kwargs = {
        "ynab_stale": stale_flag,
        "ynab_soft_preserved": soft,
        "ynab_as_of": snap_as_of,
        "expenses": expenses,
        "sheet_unreachable": bool(sheet_state.get("unreachable")),
        "policy": policy,
        "sheet_forecast_buffer": sheet_forecast,
    }
    if not sheet_state.get("usable") and not sheet_state.get("unreachable"):
        return build_runway(
            days=days,
            threshold=threshold,
            today=end,
            error=str(sheet_state.get("error") or "Personal Expense Sheet unavailable"),
            **ynab_kwargs,
        )
    fetcher = fetch or fetch_ynab_runway
    pulled = fetcher(lookback_start.isoformat())
    if pulled.get("ok") and pulled.get("as_of"):
        ynab_kwargs["ynab_as_of"] = pulled.get("as_of")
    if not pulled.get("ok"):
        return build_runway(
            days=days,
            threshold=threshold,
            today=end,
            error=str(pulled.get("error") or "YNAB fetch failed"),
            **ynab_kwargs,
        )
    return build_runway(
        days=days,
        threshold=threshold,
        today=end,
        transactions=pulled.get("transactions") or [],
        scheduled=pulled.get("scheduled") or [],
        category_groups=pulled.get("category_groups") or [],
        accounts=pulled.get("accounts") or [],
        on_budget_ids=pulled.get("on_budget_ids"),
        **ynab_kwargs,
    )
