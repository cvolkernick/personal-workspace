"""Pure treasury policy: buckets, stress colors, priority actions, DCA governor.

No I/O. Callers pass a normalized snapshot dict.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


DEFAULT_POLICY: Dict[str, Any] = {
    # Morpho LTV bands (liq ~86%): target home → alert defense → hard max
    "cb_ltv_target": 0.38,  # cool operating home; below → park principal in HY LTV buffer
    "cb_ltv_alert": 0.45,  # warm — HY → BTC defense starts
    "cb_target_ltv_max": 0.50,  # hot hard ceiling — no new Morpho principal
    # Spot-only optional reserve (rarely used). Card paydown is Morpho refinance, not HY.
    "cb_card_float_usdc": 0.0,
    # HY LTV Buffer floor (generic HY — venue may be USDC/USDG/etc.; not product-specific)
    "cb_loan_buffer_usdc": 1000.0,
    # Retired 2026-08-11: bridge residual is served by HY LTV Buffer, not a separate floor.
    "cb_bridge_dry_powder_usdc": 0.0,
    "rh_bp_floor": 0.0,  # MO 2026-08-02: no RH BP floor — any in-account BP deployable
    # RH margin-use bands (call ~50%): target home → alert defense → hard max
    "rh_margin_use_target": 0.28,  # cool operating home; below → park in HY LTV buffer
    "rh_margin_use_alert": 0.35,  # warm — HY → stock defense starts
    "rh_margin_use_max": 0.40,  # hot hard ceiling — do not raise toward 50% call
    "excess_split_cb": 0.60,
    "excess_split_rh": 0.40,
    "bridge_max_recommend_usdc": 5000.0,
    "stale_after_hours": 6.0,
    # Capital Flows MO: HY LTV Buffer is generic high-yield (not USDC/USDG-specific);
    # One Card = Morpho refinance (~5% vs ~29%). Vault counts toward HY LTV floor only.
    "count_vault_toward_buffers": True,
    "count_vault_toward_card_float": False,
    "min_spot_usdc_warn": 0.0,  # do not require idle spot if vault covers LTV floors
    # Secured One Card: available credit ≈ security deposit USDC − balance owed
    "one_card_security_deposit_usdc": 500.0,
    # Illustrative APR spread for refinance actions (not live quotes)
    "morpho_borrow_apr_est": 0.05,
    "one_card_apr_est": 0.29,
}

MANUAL_FIELDS = (
    "loan_principal_usdc",
    "collateral_btc_usd",
    "ltv",
    "vault_usdc",
    "card_balance",
    "card_available_credit",
)


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _is_missing(x: Any) -> bool:
    return x is None or x == ""


def classify_liquid_usdc(
    liquid_usdc: float,
    *,
    card_float: float,
    loan_buffer: float,
    bridge_dry_powder: float,
) -> Dict[str, Any]:
    """Split liquid USDC into required floors vs excess.

    Floor fill order (Capital Flows MO): loan_buffer (HY LTV Buffer) →
    card_float (optional spot reserve). Bridge dry powder is retired (default 0) —
    CB↔RH residual is served by the HY LTV Buffer, not a separate cash floor.
    Card paydown is *not* scored here when callers pass card_float=0.
    """
    floors = {
        "loan_buffer": max(0.0, loan_buffer),
        "bridge_dry_powder": max(0.0, bridge_dry_powder),  # legacy key; keep 0
        "card_float": max(0.0, card_float),
    }
    required = sum(floors.values())
    shortfall = max(0.0, required - liquid_usdc)
    excess = max(0.0, liquid_usdc - required)

    remaining = liquid_usdc
    filled = {}
    gaps = {}
    # LTV / HY buffer first, then optional spot card reserve (bridge floor is 0)
    for name in ("loan_buffer", "bridge_dry_powder", "card_float"):
        need = floors[name]
        take = min(remaining, need)
        filled[name] = take
        gaps[name] = max(0.0, need - take)
        remaining -= take

    return {
        "liquid_usdc": liquid_usdc,
        "required_total": required,
        "shortfall": shortfall,
        "excess": excess,
        "floors": floors,
        "filled": filled,
        "gaps": gaps,
        "status": "red" if shortfall > 0 else ("yellow" if excess == 0 else "green"),
    }


def dca_governor(
    buying_power: float,
    *,
    bp_floor: float,
    margin_use: Optional[float] = None,
    margin_use_max: float = 0.40,
    cash: Optional[float] = None,
) -> Dict[str, Any]:
    """Decide whether DCA buys are allowed given RH buying power / margin heat."""
    bp = _f(buying_power)
    floor = max(0.0, _f(bp_floor))
    mu = None if margin_use is None else _f(margin_use)
    mu_max = _f(margin_use_max, 0.40)

    if mu is not None and mu > mu_max:
        return {
            "allow_dca": False,
            "throttle": "pause",
            "reason": f"margin use {mu:.0%} exceeds max {mu_max:.0%}",
            "buying_power": bp,
            "bp_floor": floor,
            "margin_use": mu,
        }
    if bp < floor:
        return {
            "allow_dca": False,
            "throttle": "pause",
            "reason": f"buying power {bp:.2f} below floor {floor:.2f}",
            "buying_power": bp,
            "bp_floor": floor,
            "margin_use": mu,
        }
    if floor > 0 and bp < floor * 1.25:
        return {
            "allow_dca": True,
            "throttle": "slow",
            "reason": f"buying power {bp:.2f} only slightly above floor {floor:.2f}",
            "buying_power": bp,
            "bp_floor": floor,
            "margin_use": mu,
            "cash": cash,
        }
    return {
        "allow_dca": True,
        "throttle": "normal",
        "reason": f"buying power {bp:.2f} above floor {floor:.2f}",
        "buying_power": bp,
        "bp_floor": floor,
        "margin_use": mu,
        "cash": cash,
    }


def leverage_band(
    value: Optional[float],
    *,
    target: float,
    alert: float,
    hard_max: float,
) -> Dict[str, Any]:
    """Map a leverage ratio into cool/warm/hot(+critical) for HY ↔ borrow rotation.

    Bands (inclusive upper edges escalate):
      cool     — value < target  → green  (park principal in HY)
      warm     — target ≤ value < alert → green (near home; no free-risk expansion)
      hot      — alert ≤ value < hard_max → yellow (HY → collateral defense)
      critical — value ≥ hard_max → red (no new borrow; force buffer/repay)
    """
    tgt = _f(target)
    al = _f(alert)
    mx = _f(hard_max)
    if value is None:
        return {
            "value": None,
            "band": "unknown",
            "color": "yellow",
            "target": tgt,
            "alert": al,
            "hard_max": mx,
            "stance": "confirm_reading",
        }
    v = _f(value)
    if v >= mx:
        band, color, stance = "critical", "red", "no_new_borrow_force_buffer"
    elif v >= al:
        band, color, stance = "hot", "yellow", "hy_to_collateral_defense"
    elif v >= tgt:
        band, color, stance = "warm", "green", "hold_near_target"
    else:
        band, color, stance = "cool", "green", "principal_to_hy"
    return {
        "value": v,
        "band": band,
        "color": color,
        "target": tgt,
        "alert": al,
        "hard_max": mx,
        "stance": stance,
    }


def _ltv_stress(
    ltv: Optional[float],
    alert: float,
    max_ltv: float,
    *,
    target: Optional[float] = None,
) -> str:
    """Traffic light for Morpho LTV (green until alert; yellow at alert; red at max)."""
    del target  # target affects band/stance only, not the hard traffic light
    if ltv is None:
        return "yellow"
    if ltv >= max_ltv:
        return "red"
    if ltv >= alert:
        return "yellow"
    return "green"


def _card_stress(
    *,
    card_balance_raw: Any,
    card_avail_raw: Any,
    card_balance: float,
    card_avail: Optional[float],
    card_float_gap: float = 0.0,
) -> str:
    """Card health from deposit/credit — not vault float gap.

    Balance owed → yellow (Morpho refinance path). Near-maxed available credit → red.
    Vault shortfall must not paint card red (Capital Flows: HY is LTV buffer only).
    """
    del card_float_gap  # retained for call-site compat; unused under refinance MO
    if _is_missing(card_balance_raw) and _is_missing(card_avail_raw):
        return "yellow"
    if card_balance > 0 and card_avail is not None and card_avail < 50:
        return "red"
    if card_balance > 0:
        return "yellow"  # refinance recommended; not a vault-float failure
    if _is_missing(card_balance_raw) or _is_missing(card_avail_raw):
        return "yellow"
    return "green"


def _rh_stress(
    buying_power: float,
    bp_floor: float,
    margin_use: Optional[float],
    margin_max: float,
    *,
    margin_alert: Optional[float] = None,
) -> str:
    gov = dca_governor(
        buying_power,
        bp_floor=bp_floor,
        margin_use=margin_use,
        margin_use_max=margin_max,
    )
    if not gov["allow_dca"] and "margin" in gov["reason"]:
        return "red"
    if not gov["allow_dca"]:
        return "yellow"
    # Warm/hot margin use (above alert, still under hard max) → yellow
    if (
        margin_use is not None
        and margin_alert is not None
        and _f(margin_use) >= _f(margin_alert)
    ):
        return "yellow"
    if gov["throttle"] == "slow":
        return "yellow"
    return "green"


def _parse_as_of(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def assess_data_quality(
    snapshot: Dict[str, Any],
    *,
    ltv: Optional[float],
    stale_after_hours: float = 6.0,
) -> Dict[str, Any]:
    """Report missing manual fields, empty sources, and stale snapshots."""
    man = snapshot.get("coinbase_manual") or {}
    cb = snapshot.get("coinbase") or {}
    rh = snapshot.get("robinhood") or {}
    meta = snapshot.get("meta") or {}

    missing_manual = [k for k in MANUAL_FIELDS if _is_missing(man.get(k))]
    # If ltv derived, don't mark ltv missing
    if ltv is not None and "ltv" in missing_manual:
        missing_manual = [k for k in missing_manual if k != "ltv"]
    # principal+collateral can substitute for ltv
    if (
        not _is_missing(man.get("loan_principal_usdc"))
        and not _is_missing(man.get("collateral_btc_usd"))
        and "ltv" in missing_manual
    ):
        missing_manual = [k for k in missing_manual if k != "ltv"]

    warnings: List[str] = []
    # Card fields filled via YNAB one_card count as present for DQ
    oc = snapshot.get("one_card") or {}
    if oc.get("card_balance") is not None or oc.get("balance_owed") is not None:
        missing_manual = [k for k in missing_manual if k != "card_balance"]
    if not _is_missing(man.get("card_balance")):
        missing_manual = [k for k in missing_manual if k != "card_balance"]
    if oc.get("card_available_credit") is not None or oc.get("available_credit") is not None:
        missing_manual = [k for k in missing_manual if k != "card_available_credit"]
    # Computed available credit from security deposit also counts
    dep = man.get("one_card_security_deposit_usdc")
    if _is_missing(dep):
        dep = (snapshot.get("policy_overrides") or {}).get("one_card_security_deposit_usdc")
    if not _is_missing(dep) and (
        oc.get("card_balance") is not None
        or oc.get("balance_owed") is not None
        or not _is_missing(man.get("card_balance"))
    ):
        missing_manual = [k for k in missing_manual if k != "card_available_credit"]

    if missing_manual:
        warnings.append(
            "Missing Coinbase app fields: " + ", ".join(missing_manual)
        )
    if oc.get("source") in (None, "empty"):
        warnings.append("One Card / YNAB snapshot missing — run treasury/ynab_sync.py")
    elif oc.get("live_error"):
        warnings.append(f"YNAB One Card: {oc['live_error']}")
    rhc = snapshot.get("rh_checking") or {}
    xm = snapshot.get("x_money") or {}
    if rhc.get("source") in (None, "empty"):
        warnings.append("RH Checking / YNAB snapshot missing — link in YNAB and run ynab_sync")
    elif rhc.get("live_error"):
        warnings.append(f"YNAB RH Checking: {rhc['live_error']}")
    if xm.get("source") in (None, "empty"):
        warnings.append("X Money / YNAB snapshot missing — link in YNAB and run ynab_sync")
    elif xm.get("live_error"):
        warnings.append(f"YNAB X Money: {xm['live_error']}")
    ex = snapshot.get("expenses") or {}
    if ex.get("source") in (None, "empty"):
        warnings.append("Expense sheet missing — run treasury/expenses_sync.py")
    elif ex.get("live_error"):
        warnings.append(f"Expense sheet: {ex['live_error']}")
    if cb.get("source") in (None, "empty"):
        warnings.append("Coinbase liquid balances unavailable (no live or snapshot)")
    if rh.get("source") in (None, "empty"):
        warnings.append("Robinhood portfolio unavailable — write snapshots/robinhood_latest.json")
    if cb.get("live_error"):
        warnings.append(f"Coinbase live error: {cb['live_error']}")
    if rh.get("live_error"):
        warnings.append(f"Robinhood note: {rh['live_error']}")

    now = datetime.now(timezone.utc)
    stale: List[str] = []
    for label, src in (
        ("coinbase", cb),
        ("robinhood", rh),
        ("one_card", oc),
        ("rh_checking", rhc),
        ("x_money", xm),
        ("expenses", ex),
    ):
        as_of = _parse_as_of(src.get("as_of"))
        if as_of is None:
            continue
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        age_h = (now - as_of).total_seconds() / 3600.0
        if age_h > stale_after_hours:
            stale.append(f"{label} data {age_h:.1f}h old (>{stale_after_hours}h)")
            warnings.append(stale[-1])

    # Completeness score: manual fields filled / total + live sources present
    manual_total = len(MANUAL_FIELDS)
    still_missing = list(missing_manual)
    manual_filled = manual_total - len(still_missing)
    if ltv is not None and "ltv" not in still_missing:
        pass
    elif ltv is not None:
        manual_filled = min(manual_total, manual_filled + 1)
    sources_ok = 0
    if cb.get("source") not in (None, "empty"):
        sources_ok += 1
    if rh.get("source") not in (None, "empty"):
        sources_ok += 1
    if oc.get("source") not in (None, "empty") and not oc.get("live_error"):
        sources_ok += 1
    if rhc.get("source") not in (None, "empty") and not rhc.get("live_error"):
        sources_ok += 1
    if xm.get("source") not in (None, "empty") and not xm.get("live_error"):
        sources_ok += 1
    if ex.get("source") not in (None, "empty") and not ex.get("live_error"):
        sources_ok += 1
    score = (manual_filled / manual_total) * 0.5 + (sources_ok / 6.0) * 0.5

    status = "green"
    if missing_manual or stale:
        status = "yellow"
    if sources_ok == 0 or score < 0.35:
        status = "red"

    return {
        "status": status,
        "completeness_score": round(score, 3),
        "missing_manual_fields": missing_manual,
        "manual_filled": manual_filled,
        "manual_total": manual_total,
        "sources": {
            "coinbase": cb.get("source"),
            "robinhood": rh.get("source"),
            "one_card": oc.get("source"),
            "rh_checking": rhc.get("source"),
            "x_money": xm.get("source"),
            "expenses": ex.get("source"),
            "coinbase_as_of": cb.get("as_of"),
            "robinhood_as_of": rh.get("as_of"),
            "one_card_as_of": oc.get("as_of"),
            "rh_checking_as_of": rhc.get("as_of"),
            "x_money_as_of": xm.get("as_of"),
            "expenses_as_of": ex.get("as_of"),
        },
        "stale": stale,
        "warnings": warnings,
        "notes": [
            "Morpho LTV, High Yield vault, and One Card are app-only — not Advanced Trade API.",
            "RH snapshot is written by agent MCP (get_portfolio), not live CLI.",
            "Liquid CB balances exclude vault/collateral locked on Morpho.",
        ],
        "config_path": meta.get("config_path"),
        "rh_accounts": meta.get("rh_accounts") or {},
    }



def _parse_sheet_due(value: Any) -> Optional[datetime]:
    """Parse expense sheet due dates (ISO or M/D/YYYY). Pure — no expenses_sync import."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    if "T" in s:
        s_iso = s.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s_iso)
        except ValueError:
            s = s.split("T", 1)[0]
    try:
        return datetime.fromisoformat(s[:10])
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10] if len(s) >= 8 else s, fmt)
        except ValueError:
            continue
    return None


def expense_due_window(
    snapshot: Dict[str, Any],
    *,
    today: Optional[datetime] = None,
    due_within_days: int = 7,
) -> Dict[str, Any]:
    """Essential-sheet pressure: overdue + due-soon line items (not Discretionary)."""
    now = today or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today_d = now.date()
    ex = snapshot.get("expenses") or {}
    tabs = ex.get("tabs") or {}
    # Tab renamed Personal → Essential (2026-08-10); accept both keys.
    personal = tabs.get("Essential") or tabs.get("Personal") or {}
    items = personal.get("items") or personal.get("upcoming_by_date") or []
    critical: List[Dict[str, Any]] = []
    overdue_total = 0.0
    due_soon_total = 0.0
    overdue_count = 0
    due_soon_count = 0
    for raw in items:
        if not isinstance(raw, dict):
            continue
        amt = abs(_f(raw.get("amount_due"), _f(raw.get("monthly"))))
        if amt <= 0:
            continue
        due_dt = _parse_sheet_due(raw.get("due_date") or raw.get("date"))
        if due_dt is None:
            continue
        due_d = due_dt.date() if hasattr(due_dt, "date") else due_dt
        days = (due_d - today_d).days
        if days > due_within_days:
            continue
        row = {
            "item": raw.get("item") or raw.get("name") or "—",
            "amount": round(amt, 2),
            "due_date": due_d.isoformat(),
            "days_until_due": days,
            "overdue": days < 0,
            "from": raw.get("from") or raw.get("pay_from"),
        }
        critical.append(row)
        if days < 0:
            overdue_total += amt
            overdue_count += 1
        else:
            due_soon_total += amt
            due_soon_count += 1
    critical_total = overdue_total + due_soon_total
    return {
        "as_of_date": today_d.isoformat(),
        "due_within_days": due_within_days,
        "overdue_count": overdue_count,
        "due_soon_count": due_soon_count,
        "overdue_total": round(overdue_total, 2),
        "due_soon_total": round(due_soon_total, 2),
        "critical_total": round(critical_total, 2),
        "items": critical[:12],
        "sheet_source": ex.get("source"),
    }


def _sheet_items_monthly(tab: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize discretionary-style sheet items with monthly > 0."""
    out: List[Dict[str, Any]] = []
    for raw in tab.get("items") or tab.get("top_targets") or []:
        if not isinstance(raw, dict):
            continue
        amt = _f(
            raw.get("monthly"),
            _f(raw.get("annually")) / 12.0 if raw.get("annually") else 0.0,
        )
        name = raw.get("item") or raw.get("name") or "—"
        if amt <= 0:
            continue
        out.append({"item": name, "monthly": round(amt, 2)})
    return out


def discretionary_capex_targets(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Productive + Consumer discretionary tabs (not expense burn).

    Productive Discretionary = capital outlay growing productive assets (priority).
    Consumer Discretionary = wishlist / consumer goods (lower priority).
    Both fund from collateralized margin when deployed — not free-dollar residual.
    Legacy tab key ``Discretionary`` maps to productive.
    """
    ex = snapshot.get("expenses") or {}
    tabs = ex.get("tabs") or {}
    summary = ex.get("summary") or {}
    productive = (
        tabs.get("Productive Discretionary")
        or tabs.get("Discretionary")
        or {}
    )
    consumer = tabs.get("Consumer Discretionary") or {}

    prod_items = _sheet_items_monthly(productive)
    cons_items = _sheet_items_monthly(consumer)
    prod_sum = sum(i["monthly"] for i in prod_items)
    cons_sum = sum(i["monthly"] for i in cons_items)

    productive_monthly = _f(
        summary.get("productive_discretionary_monthly"),
        _f(
            summary.get("capital_targets_monthly"),
            _f(summary.get("discretionary_monthly"), prod_sum),
        ),
    )
    if productive_monthly <= 0 and prod_sum > 0:
        productive_monthly = prod_sum
    consumer_monthly = _f(
        summary.get("consumer_discretionary_monthly"),
        cons_sum,
    )
    if consumer_monthly <= 0 and cons_sum > 0:
        consumer_monthly = cons_sum

    # Capex guidance primary need = productive (ops expansion)
    monthly = productive_monthly
    return {
        "monthly": round(monthly, 2),
        "productive_monthly": round(productive_monthly, 2),
        "consumer_monthly": round(consumer_monthly, 2),
        "item_count": len(prod_items) or int(productive.get("item_count") or 0),
        "items": prod_items[:12],
        "consumer_items": cons_items[:12],
        "priority_order": ["productive", "consumer"],
        "note": (
            "Productive Discretionary = capital outlay growing productive assets "
            "(priority over Consumer). Consumer Discretionary = wishlist / qualitative. "
            "Fund from collateralized margin (RH BP), not free-dollar cash stack."
        ),
    }


def cashflow_allocation_guidance(
    *,
    expense_window: Dict[str, Any],
    card_balance: float,
    buckets: Dict[str, Any],
    working_usdc: float,
    bank_cash: float,
    rh_buying_power: float,
    dca: Dict[str, Any],
    discretionary: Dict[str, Any],
    excess_split_cb: float = 0.60,
    excess_split_rh: float = 0.40,
    free_dollar_red: bool = False,
) -> Dict[str, Any]:
    """Simplified free-dollar waterfall + margin-only capex guidance.

    Priority (free dollars):
      1. Essential expenses paid & current (overdue / ≤7d window)
      2. Coinbase One Card balance paid down
      3. HY LTV Buffer — not card float from vault; bridge powder retired
      4. Excess beyond floors → collateral; HY→Collateral only under LTV heat

    Capex (Productive Discretionary first, then Consumer) is **not** free-dollar
    residual — fund from collateralized margin (RH buying power) only.
    Card paydown is Morpho refinance (not free-dollar residual from HY).
    """
    free_start = max(0.0, _f(working_usdc)) + max(0.0, _f(bank_cash))
    remaining = free_start

    exp_need = max(0.0, _f(expense_window.get("critical_total")))
    exp_overdue = int(expense_window.get("overdue_count") or 0)
    exp_soon = int(expense_window.get("due_soon_count") or 0)
    exp_alloc = min(remaining, exp_need)
    exp_gap = max(0.0, exp_need - exp_alloc)
    remaining = max(0.0, remaining - exp_alloc)
    if exp_need <= 0.01 and exp_overdue == 0 and exp_soon == 0:
        exp_status = "met"
    elif exp_gap <= 0.01 and exp_overdue == 0:
        exp_status = "met" if exp_soon == 0 else "partial"
    elif exp_alloc > 0 and exp_gap > 0:
        exp_status = "partial"
    else:
        exp_status = "gap"

    card_need = max(0.0, _f(card_balance))
    card_alloc = min(remaining, card_need)
    card_gap = max(0.0, card_need - card_alloc)
    remaining = max(0.0, remaining - card_alloc)
    if card_need <= 0.01:
        card_status = "met"
    elif card_gap <= 0.01:
        card_status = "met"
    elif card_alloc > 0:
        card_status = "partial"
    else:
        card_status = "gap"

    floors_required = max(0.0, _f(buckets.get("required_total")))
    buffer_shortfall = max(0.0, _f(buckets.get("shortfall")))
    buffer_excess = max(0.0, _f(buckets.get("excess")))
    gaps = buckets.get("gaps") or {}
    # Residual free after expenses+card should still cover buffer shortfall
    buf_need = buffer_shortfall
    # If floors already met in working USDC, no free-dollar need for buffers
    if buffer_shortfall <= 0.01:
        buf_need = 0.0
    buf_alloc = min(remaining, buf_need)
    buf_gap = max(0.0, buf_need - buf_alloc)
    remaining = max(0.0, remaining - buf_alloc)
    if buf_need <= 0.01:
        buf_status = "met"
    elif buf_gap <= 0.01:
        buf_status = "met"
    elif buf_alloc > 0:
        buf_status = "partial"
    else:
        buf_status = "gap"

    priors_clear = exp_status == "met" and card_status == "met" and buf_status == "met"
    # Excess for collateral: residual free after stack OR classified bucket excess when priors clear
    if priors_clear:
        coll_amt = max(remaining, buffer_excess)
        coll_status = "ready" if coll_amt > 0.01 else "met"
    else:
        coll_amt = 0.0
        coll_status = "blocked"
    split_cb = max(0.0, _f(excess_split_cb))
    split_rh = max(0.0, _f(excess_split_rh))
    split_sum = split_cb + split_rh
    if split_sum <= 0:
        split_cb, split_rh, split_sum = 0.6, 0.4, 1.0
    to_btc = round(coll_amt * (split_cb / split_sum), 2) if coll_amt > 0 else 0.0
    to_rh = round(coll_amt * (split_rh / split_sum), 2) if coll_amt > 0 else 0.0

    # Capex from margin BP only (not free dollars).
    # Productive Discretionary is the primary need; Consumer is secondary/wishlist.
    productive_monthly = max(
        0.0,
        _f(discretionary.get("productive_monthly"), _f(discretionary.get("monthly"))),
    )
    consumer_monthly = max(0.0, _f(discretionary.get("consumer_monthly")))
    capex_monthly = productive_monthly  # guidance primary = productive
    bp = max(0.0, _f(rh_buying_power))
    dca_ok = bool((dca or {}).get("allow_dca", True))
    margin_heat = (not dca_ok) and "margin" in str((dca or {}).get("reason") or "").lower()
    if capex_monthly <= 0.01:
        capex_status = "none"
    elif margin_heat:
        capex_status = "blocked_margin"
    elif bp < 1:
        capex_status = "no_bp"
    elif free_dollar_red and not dca_ok:
        # free-dollar red does not block existing BP; only margin heat does above
        capex_status = "available"
    else:
        capex_status = "available"

    def step(
        rank: int,
        sid: str,
        title: str,
        status: str,
        *,
        need: float = 0.0,
        allocated: float = 0.0,
        gap: float = 0.0,
        detail: str = "",
        fund_from: str = "free_dollar",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "rank": rank,
            "id": sid,
            "title": title,
            "status": status,
            "need": round(need, 2),
            "allocated": round(allocated, 2),
            "gap": round(gap, 2),
            "detail": detail,
            "fund_from": fund_from,
            "meta": meta or {},
        }

    steps = [
        step(
            1,
            "expenses",
            "Expenses paid & current",
            exp_status,
            need=exp_need,
            allocated=exp_alloc,
            gap=exp_gap,
            detail=(
                f"{exp_overdue} overdue · {exp_soon} due ≤{expense_window.get('due_within_days', 7)}d · "
                f"critical ${exp_need:,.0f}"
                if exp_need > 0
                else "No overdue / due-soon Essential sheet lines"
            ),
            fund_from="venue_cash",
            meta={
                "overdue_count": exp_overdue,
                "due_soon_count": exp_soon,
                "overdue_total": expense_window.get("overdue_total"),
                "due_soon_total": expense_window.get("due_soon_total"),
            },
        ),
        step(
            2,
            "card_paydown",
            "Coinbase One Card paydown",
            card_status,
            need=card_need,
            allocated=card_alloc,
            gap=card_gap,
            detail=(
                f"Owed ${card_need:,.2f} — pay down before buffer residual risk"
                if card_need > 0
                else "Card balance clear"
            ),
            fund_from="free_dollar",
            meta={"card_balance": round(card_need, 2)},
        ),
        step(
            3,
            "cash_buffers",
            "Cash stack buffers",
            buf_status,
            need=buf_need,
            allocated=buf_alloc,
            gap=buf_gap,
            detail=(
                f"Floors ${floors_required:,.0f} · working USDC ${working_usdc:,.0f} · "
                f"shortfall ${buffer_shortfall:,.0f}"
            ),
            fund_from="free_dollar",
            meta={
                "floors_required": round(floors_required, 2),
                "working_usdc": round(_f(working_usdc), 2),
                "shortfall": round(buffer_shortfall, 2),
                "gaps": {
                    "card_float": round(_f(gaps.get("card_float")), 2),
                    "loan_buffer": round(_f(gaps.get("loan_buffer")), 2),
                    "bridge_dry_powder": round(_f(gaps.get("bridge_dry_powder")), 2),
                },
            },
        ),
        step(
            4,
            "collateral",
            "Collateral (excess free dollars)",
            coll_status,
            need=coll_amt,
            allocated=coll_amt if coll_status == "ready" else 0.0,
            gap=0.0 if priors_clear else max(exp_gap + card_gap + buf_gap, 0.0),
            detail=(
                f"Deploy ~${to_btc:,.0f} BTC path · ~${to_rh:,.0f} RH securities"
                if coll_status == "ready" and coll_amt > 0
                else (
                    "Floors full — no excess free dollars yet"
                    if coll_status == "met"
                    else "Blocked until expenses, card, and buffers are clear"
                )
            ),
            fund_from="free_dollar_excess",
            meta={
                "to_btc_collateral": to_btc,
                "to_rh_securities": to_rh,
                "split_cb": split_cb / split_sum,
                "split_rh": split_rh / split_sum,
                "priors_clear": priors_clear,
            },
        ),
        step(
            5,
            "capex_margin",
            "Capex · Productive Discretionary (margin)",
            capex_status,
            need=capex_monthly,
            allocated=min(bp, capex_monthly) if capex_status == "available" else 0.0,
            gap=max(0.0, capex_monthly - bp) if capex_monthly > 0 else 0.0,
            detail=(
                f"Productive ~${productive_monthly:,.0f}/mo"
                + (f" · Consumer wishlist ~${consumer_monthly:,.0f}/mo" if consumer_monthly > 0 else "")
                + f" · RH BP ${bp:,.2f} · "
                + (
                    "margin heat — pause"
                    if capex_status == "blocked_margin"
                    else (
                        "no BP"
                        if capex_status == "no_bp"
                        else (
                            "no productive targets"
                            if capex_status == "none"
                            else "productive first, then consumer — margin only (not free cash)"
                        )
                    )
                )
            ),
            fund_from="collateralized_margin",
            meta={
                "rh_buying_power": round(bp, 2),
                "dca_allow": dca_ok,
                "dca_reason": (dca or {}).get("reason"),
                "items": (discretionary.get("items") or [])[:8],
                "consumer_items": (discretionary.get("consumer_items") or [])[:6],
                "productive_monthly": round(productive_monthly, 2),
                "consumer_monthly": round(consumer_monthly, 2),
                "priority_order": discretionary.get("priority_order")
                or ["productive", "consumer"],
            },
        ),
    ]

    # Active free-dollar step = first incomplete among 1–4
    active_id = None
    for s in steps:
        if s["id"] == "capex_margin":
            continue
        if s["status"] not in ("met", "ready"):
            active_id = s["id"]
            break
    if active_id is None:
        active_id = "collateral" if coll_status == "ready" else "expenses"

    # Next free dollar label
    next_map = {
        "expenses": "Pay Essential sheet obligations (overdue / due soon)",
        "card_paydown": "Refinance One Card via Morpho principal (not HY pull)",
        "cash_buffers": "Fill cash stack buffers (float · loan · bridge)",
        "collateral": "Deploy excess to collateral (BTC or RH securities)",
    }
    next_free = next_map.get(active_id, next_map["expenses"])
    if free_dollar_red and active_id == "collateral":
        next_free = "Red-mode: do not add new free-dollar risk — hold excess for stack defense"
        for s in steps:
            if s["id"] == "collateral" and s["status"] == "ready":
                s["status"] = "hold_red"
                s["detail"] = "Excess exists but overall free-dollar stress is red — prefer stack defense"

    return {
        "version": 1,
        "priority_order": [
            "expenses_paid_current",
            "card_paydown",
            "cash_buffers",
            "collateral_excess",
            "capex_from_margin",
        ],
        "free_liquid_start": round(free_start, 2),
        "free_liquid_residual": round(remaining, 2),
        "working_usdc": round(_f(working_usdc), 2),
        "bank_cash": round(_f(bank_cash), 2),
        "free_dollar_red": bool(free_dollar_red),
        "active_step_id": active_id,
        "next_free_dollar": next_free,
        "steps": steps,
        "expense_window": {
            "critical_total": expense_window.get("critical_total"),
            "overdue_count": exp_overdue,
            "due_soon_count": exp_soon,
            "as_of_date": expense_window.get("as_of_date"),
        },
        "capex": {
            "fund_from": "collateralized_margin",
            "monthly": round(capex_monthly, 2),
            "rh_buying_power": round(bp, 2),
            "status": capex_status,
            "note": discretionary.get("note"),
        },
    }


def evaluate_treasury(
    snapshot: Dict[str, Any],
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate dual-venue treasury and return stress + priority-ordered actions."""
    p = {**DEFAULT_POLICY, **(policy or {})}
    cb = snapshot.get("coinbase") or {}
    man = snapshot.get("coinbase_manual") or {}
    rh = snapshot.get("robinhood") or {}

    liquid_usdc = _f(cb.get("liquid_usdc"))
    liquid_btc = _f(cb.get("liquid_btc"))
    btc_usd_price = cb.get("btc_usd_price")
    btc_usd_price = None if btc_usd_price is None else _f(btc_usd_price)
    liquid_btc_usd = (
        liquid_btc * btc_usd_price if btc_usd_price is not None else _f(cb.get("liquid_btc_usd"))
    )

    ltv = man.get("ltv")
    if not _is_missing(ltv):
        ltv = _f(ltv)
    else:
        ltv = None
    principal = _f(man.get("loan_principal_usdc")) if not _is_missing(man.get("loan_principal_usdc")) else 0.0
    coll_usd = _f(man.get("collateral_btc_usd")) if not _is_missing(man.get("collateral_btc_usd")) else 0.0
    if ltv is None and principal > 0 and coll_usd > 0:
        ltv = principal / coll_usd

    one_card = snapshot.get("one_card") or {}
    rh_checking = snapshot.get("rh_checking") or {}
    x_money = snapshot.get("x_money") or {}
    vault_raw = man.get("vault_usdc")
    vault_known = not _is_missing(vault_raw)
    vault_usdc = _f(vault_raw) if vault_known else 0.0
    count_vault = bool(p.get("count_vault_toward_buffers", True))
    count_vault_card = bool(p.get("count_vault_toward_card_float", False))
    # Working USDC = idle Advanced Trade USDC + High Yield vault (LTV buffer sleeve)
    working_usdc = liquid_usdc + (vault_usdc if count_vault and vault_known else 0.0)
    # Floors scored against vault: loan + bridge only unless explicitly counting card float
    card_float_for_vault = (
        _f(p["cb_card_float_usdc"]) if count_vault_card else 0.0
    )
    # Prefer YNAB/snapshot one_card over manual card_balance so a stale UI/config
    # override cannot pin owed balance (2026-08-05: $499.23 manual beat live $440.18).
    ynab_card_raw = one_card.get("card_balance")
    if _is_missing(ynab_card_raw):
        ynab_card_raw = one_card.get("balance_owed")
    ynab_card_ok = (
        one_card.get("source") in ("ynab", "snapshot")
        and not one_card.get("live_error")
        and not _is_missing(ynab_card_raw)
    )
    if ynab_card_ok:
        card_balance_raw = ynab_card_raw
        card_source = "ynab"
    else:
        card_balance_raw = man.get("card_balance")
        if _is_missing(card_balance_raw):
            card_balance_raw = ynab_card_raw
        if man.get("card_balance_source"):
            card_source = man.get("card_balance_source")
        elif not _is_missing(man.get("card_balance")):
            card_source = "manual"
        elif one_card.get("source") in ("ynab", "snapshot") and not _is_missing(card_balance_raw):
            card_source = "ynab"
        else:
            card_source = None
    card_avail_raw = man.get("card_available_credit")
    if _is_missing(card_avail_raw):
        card_avail_raw = one_card.get("card_available_credit")
        if _is_missing(card_avail_raw):
            card_avail_raw = one_card.get("available_credit")
    card_balance = _f(card_balance_raw) if not _is_missing(card_balance_raw) else 0.0
    card_avail = None if _is_missing(card_avail_raw) else _f(card_avail_raw)

    # Secured One Card available credit ≈ USDC security deposit − balance owed
    deposit_raw = man.get("one_card_security_deposit_usdc")
    if _is_missing(deposit_raw):
        deposit_raw = p.get("one_card_security_deposit_usdc")
    card_deposit = None if _is_missing(deposit_raw) else _f(deposit_raw)
    card_avail_source = None
    if card_avail is not None:
        card_avail_source = "manual_or_ynab"
    elif card_deposit is not None and not _is_missing(card_balance_raw):
        card_avail = max(0.0, card_deposit - card_balance)
        card_avail_source = "deposit_minus_balance"
        card_avail_raw = card_avail  # treat as known for stress/DQ

    bp = _f(rh.get("buying_power"))
    # Brokerage cash from RH trading MCP (may be sparse)
    cash = _f(rh.get("cash"))
    # Prefer YNAB RH Checking for ACH / bill-pay float when present
    rh_checking_cash = None
    if not _is_missing(rh_checking.get("cash")):
        rh_checking_cash = _f(rh_checking.get("cash"))
    elif not _is_missing(rh_checking.get("available")):
        rh_checking_cash = _f(rh_checking.get("available"))
    x_money_cash = None
    if not _is_missing(x_money.get("cash")):
        x_money_cash = _f(x_money.get("cash"))
    elif not _is_missing(x_money.get("available")):
        x_money_cash = _f(x_money.get("available"))
    bank_cash = (rh_checking_cash or 0.0) + (x_money_cash or 0.0)
    if rh_checking_cash is None and x_money_cash is None:
        bank_cash_known = False
    else:
        bank_cash_known = True
    # Effective cash for bill-pay: RH Checking first (ACH), else brokerage cash
    bill_pay_cash = rh_checking_cash if rh_checking_cash is not None else cash
    equity = _f(rh.get("equity_value", rh.get("total_value")))
    total_value = _f(rh.get("total_value", equity))
    margin_use = rh.get("margin_use")
    if margin_use is not None:
        margin_use = _f(margin_use)
    elif equity > 0 and bp >= 0:
        unlev = rh.get("unleveraged_buying_power")
        if unlev is not None and _f(unlev) > 0 and bp > _f(unlev):
            margin_use = min(1.0, max(0.0, (bp - _f(unlev)) / equity))

    # LTV buffers scored against *working* USDC (vault + spot). Card float excluded by default.
    buckets = classify_liquid_usdc(
        working_usdc,
        card_float=card_float_for_vault,
        loan_buffer=_f(p["cb_loan_buffer_usdc"]),
        bridge_dry_powder=_f(p["cb_bridge_dry_powder_usdc"]),
    )
    buckets["liquid_spot_usdc"] = liquid_usdc
    buckets["vault_usdc"] = vault_usdc if vault_known else None
    buckets["working_usdc"] = working_usdc
    buckets["count_vault_toward_buffers"] = count_vault and vault_known
    buckets["count_vault_toward_card_float"] = count_vault_card
    # Spot-only view (optional card reserve lives here if configured)
    spot_buckets = classify_liquid_usdc(
        liquid_usdc,
        card_float=_f(p["cb_card_float_usdc"]) if count_vault_card else 0.0,
        loan_buffer=_f(p["cb_loan_buffer_usdc"]),
        bridge_dry_powder=_f(p["cb_bridge_dry_powder_usdc"]),
    )
    buckets["spot_only_status"] = spot_buckets["status"]
    buckets["spot_only_shortfall"] = spot_buckets["shortfall"]

    dca = dca_governor(
        bp,
        bp_floor=_f(p["rh_bp_floor"]),
        margin_use=margin_use,
        margin_use_max=_f(p["rh_margin_use_max"]),
        cash=cash,
    )

    data_quality = assess_data_quality(
        snapshot,
        ltv=ltv,
        stale_after_hours=_f(p.get("stale_after_hours"), 6.0),
    )

    morpho_band = leverage_band(
        ltv,
        target=_f(p.get("cb_ltv_target"), 0.38),
        alert=_f(p["cb_ltv_alert"]),
        hard_max=_f(p["cb_target_ltv_max"]),
    )
    margin_band = leverage_band(
        margin_use,
        target=_f(p.get("rh_margin_use_target"), 0.28),
        alert=_f(p.get("rh_margin_use_alert"), 0.35),
        hard_max=_f(p["rh_margin_use_max"]),
    )
    leverage_bands = {
        "morpho_ltv": morpho_band,
        "rh_margin_use": margin_band,
        "joint": {
            "both_cool": morpho_band["band"] == "cool" and margin_band["band"] == "cool",
            "either_hot_or_worse": morpho_band["band"] in ("hot", "critical")
            or margin_band["band"] in ("hot", "critical"),
            "morpho_at_hard_max": morpho_band["band"] == "critical",
            "stance": (
                "block_new_dual_extract"
                if morpho_band["band"] in ("hot", "critical")
                or margin_band["band"] in ("hot", "critical")
                else (
                    "hy_parks_ok"
                    if morpho_band["band"] in ("cool", "warm")
                    and margin_band["band"] in ("cool", "warm", "unknown")
                    else "monitor"
                )
            ),
        },
    }

    # USDC liquidity stress: red only if working USDC short (or vault unknown + spot empty)
    if not vault_known and liquid_usdc < 1.0 and count_vault:
        usdc_liq_stress = "yellow"  # need vault number to know true float
    else:
        usdc_liq_stress = buckets["status"]
    # Idle spot ~0 is fine when vault covers — never mark red solely for low spot
    if (
        usdc_liq_stress == "red"
        and count_vault
        and vault_known
        and vault_usdc >= buckets["required_total"]
    ):
        usdc_liq_stress = "green"

    stress = {
        "coinbase_ltv": _ltv_stress(
            ltv,
            _f(p["cb_ltv_alert"]),
            _f(p["cb_target_ltv_max"]),
            target=_f(p.get("cb_ltv_target"), 0.38),
        ),
        "coinbase_liquid": usdc_liq_stress,
        "coinbase_card": _card_stress(
            card_balance_raw=card_balance_raw,
            card_avail_raw=card_avail_raw,
            card_balance=card_balance,
            card_avail=card_avail,
            card_float_gap=0.0,  # never vault-float gap (refinance MO)
        ),
        "robinhood": _rh_stress(
            bp,
            _f(p["rh_bp_floor"]),
            margin_use,
            _f(p["rh_margin_use_max"]),
            margin_alert=_f(p.get("rh_margin_use_alert"), 0.35),
        ),
        "data_quality": data_quality["status"],
        "morpho_band": morpho_band["band"],
        "rh_margin_band": margin_band["band"],
    }
    order = {"green": 0, "yellow": 1, "red": 2}
    # Overall ops stress: material dimensions only (exclude pure data_quality from
    # forcing red — missing Morpho LTV stays yellow on coinbase_ltv, not a hard red overall
    # unless liquid/card/RH is red).
    material = [
        stress["coinbase_ltv"],
        stress["coinbase_liquid"],
        stress["coinbase_card"],
        stress["robinhood"],
    ]
    overall = max(material, key=lambda c: order.get(c, 0))
    # If only LTV unknown (yellow) and liquid is green/yellow from vault known, keep yellow
    if overall == "red" and stress["coinbase_liquid"] != "red" and stress["coinbase_card"] != "red":
        # RH BP below floor is yellow not red usually; if something else made red, leave it
        pass
    stress["overall"] = overall
    stress["dimensions"] = dict(stress)

    actions: List[Dict[str, Any]] = []

    def add(
        priority: int,
        kind: str,
        title: str,
        *,
        actor: str,
        detail: str,
        api_reachable: bool,
    ) -> None:
        actions.append(
            {
                "priority": priority,
                "kind": kind,
                "title": title,
                "actor": actor,
                "detail": detail,
                "api_reachable": api_reachable,
            }
        )

    missing = data_quality["missing_manual_fields"]
    morpho_only = set(missing) <= {
        "loan_principal_usdc",
        "collateral_btc_usd",
        "ltv",
    } and bool(missing)
    if missing and not morpho_only:
        add(
            0,
            "fill_manual",
            "Fill missing Coinbase app fields in config / UI",
            actor="human",
            detail="Missing: " + ", ".join(missing),
            api_reachable=False,
        )
    elif morpho_only:
        add(
            1,
            "fill_morpho",
            "Enter Morpho loan principal / collateral / LTV (Settings)",
            actor="human",
            detail=(
                "Missing: "
                + ", ".join(missing)
                + ". Open Coinbase Borrow app → copy principal, collateral USD, LTV → FCC Settings. "
                "Loan protection recommended. Fund-manager book is separate capital."
            ),
            api_reachable=False,
        )

    if ltv is None and not morpho_only:
        add(
            1,
            "ltv_check",
            "Confirm Morpho loan LTV in Coinbase app",
            actor="human",
            detail="LTV not readable via Advanced Trade API. Update treasury config; enable loan protection.",
            api_reachable=False,
        )
    elif ltv is None and morpho_only:
        pass  # covered by fill_morpho
    elif ltv >= _f(p["cb_target_ltv_max"]):
        add(
            1,
            "ltv_protect",
            f"LTV {ltv:.1%} at/above max {_f(p['cb_target_ltv_max']):.0%} — repay USDC or add collateral",
            actor="human",
            detail="In-app only: repay loan or add BTC collateral. Pre-stage liquid BTC/USDC if available.",
            api_reachable=False,
        )
    elif ltv >= _f(p["cb_ltv_alert"]):
        add(
            1,
            "ltv_watch",
            f"LTV {ltv:.1%} above alert {_f(p['cb_ltv_alert']):.0%} — prepare buffer",
            actor="either",
            detail="Agent can hold liquid USDC/BTC; human enables loan protection / tops up in app.",
            api_reachable=False,
        )

    if count_vault and not vault_known:
        add(
            0,
            "vault_unknown",
            "Enter HY LTV Buffer balance (venue HY vault)",
            actor="human",
            detail=(
                "HY LTV Buffer is the leverage defense sleeve (generic HY — not product-branded). "
                "Not One Card float. Enter vault balance so the LTV floor scores."
            ),
            api_reachable=False,
        )

    card_dep = _f(
        man.get("one_card_security_deposit_usdc")
        or p.get("one_card_security_deposit_usdc"),
        500.0,
    )
    card_util = (card_balance / card_dep) if card_dep > 0 and card_balance > 0 else None
    morpho_apr = _f(p.get("morpho_borrow_apr_est"), 0.05)
    card_apr = _f(p.get("one_card_apr_est"), 0.29)

    # One Card owed → Morpho refinance (not vault pull)
    if card_balance > 0.01 and not _is_missing(card_balance_raw):
        add(
            1,
            "card_refinance",
            f"Refinance One Card ${card_balance:.0f} via Morpho principal "
            f"(~{morpho_apr:.0%} vs ~{card_apr:.0%} APR)",
            actor="human",
            detail=(
                "Capital Flows: Morpho → One Card. Borrow additional BTC-backed USDC principal "
                "and pay the card in-app. Do not pull HY LTV Buffer for card. "
                "Watch LTV after principal increases."
            ),
            api_reachable=False,
        )
    elif _is_missing(card_balance_raw) and not (card_balance > 0):
        add(
            2,
            "card_unknown",
            "Enter One Card balance",
            actor="human",
            detail="Card unknown — stress cannot go green until filled (YNAB or Settings).",
            api_reachable=False,
        )

    # Morpho LTV heat (alert+): HY LTV Buffer → BTC collateral (control valve)
    if (
        ltv is not None
        and ltv >= _f(p["cb_ltv_alert"])
        and vault_known
        and vault_usdc > 0
    ):
        add(
            1,
            "hy_collateral_defense",
            f"LTV {ltv:.1%} {morpho_band['band']} (alert {_f(p['cb_ltv_alert']):.0%}) — "
            f"HY LTV Buffer → BTC collateral defense (up to ${vault_usdc:.0f})",
            actor="human",
            detail=(
                "Capital Flows: HY LTV Buffer → BTC when Morpho LTV ≥ alert; else HY stays floor. "
                f"Bands: target {_f(p.get('cb_ltv_target'), 0.38):.0%} · "
                f"alert {_f(p['cb_ltv_alert']):.0%} · hard {_f(p['cb_target_ltv_max']):.0%}. "
                "In-app: buy/add BTC collateral and/or repay Morpho. Not One Card funding."
            ),
            api_reachable=False,
        )
    elif (
        ltv is not None
        and ltv < _f(p.get("cb_ltv_target"), 0.38)
        and morpho_band["band"] == "cool"
    ):
        add(
            3,
            "hy_park_cool",
            f"LTV {ltv:.1%} cool (< target {_f(p.get('cb_ltv_target'), 0.38):.0%}) — "
            "prefer Morpho principal → HY LTV Buffer",
            actor="either",
            detail=(
                "HY ↔ borrow rotation: below target, park additional principal in HY LTV Buffer "
                "rather than expanding free-dollar risk. Not a forced action."
            ),
            api_reachable=False,
        )

    # RH margin heat at alert (not only hard max): HY → stock defense
    mu_alert = _f(p.get("rh_margin_use_alert"), 0.35)
    mu_max = _f(p["rh_margin_use_max"])
    if margin_use is not None and margin_use >= mu_alert:
        add(
            1,
            "usdg_margin_defense",
            f"RH margin use {margin_use:.0%} {margin_band['band']} "
            f"(alert {mu_alert:.0%} / max {mu_max:.0%}) — HY LTV Buffer → Agentic/Stock defense only",
            actor="human",
            detail=(
                "Capital Flows · Equities: HY LTV Buffer is the margin governor "
                "(same job as Morpho LTV buffer — generic HY, not product-specific). "
                f"Bands: target {_f(p.get('rh_margin_use_target'), 0.28):.0%} · "
                f"alert {mu_alert:.0%} · hard {mu_max:.0%} (call ~50%). "
                "Defense-only under heat; else HY stays buffer floor. "
                "Do not extract margin loan as 'income' to fund dual leverage."
            ),
            api_reachable=False,
        )
    elif (
        margin_use is not None
        and margin_use < _f(p.get("rh_margin_use_target"), 0.28)
        and margin_band["band"] == "cool"
    ):
        add(
            3,
            "usdg_park_cool",
            f"RH margin {margin_use:.0%} cool "
            f"(< target {_f(p.get('rh_margin_use_target'), 0.28):.0%}) — prefer principal → HY LTV Buffer",
            actor="either",
            detail=(
                "HY ↔ borrow rotation: below target, park residual margin capacity in HY LTV Buffer "
                "rather than re-levering. Not a forced action."
            ),
            api_reachable=False,
        )

    # HY LTV Buffer shortfall — separate from card refinance
    need_ltv_stack = buckets["gaps"]["loan_buffer"] > 0 or buckets["shortfall"] > 0
    if need_ltv_stack and buckets["shortfall"] > 0:
        bits: List[str] = []
        if buckets["gaps"]["loan_buffer"] > 0:
            bits.append(f"HY LTV Buffer −${buckets['gaps']['loan_buffer']:.0f}")
        title = (
            "Restore HY LTV Buffer · " + " · ".join(bits)
            if bits
            else "Restore HY LTV Buffer"
        )
        actions.append(
            {
                "priority": 2,
                "kind": "ltv_buffer_stack",
                "title": title,
                "actor": "human",
                "detail": (
                    "HY LTV Buffer + spot vs Morpho LTV floor (Capital Flows). "
                    "Bridge powder retired — residual is served by HY. "
                    "Top HY LTV Buffer in-app; do not treat HY as One Card float."
                ),
                "api_reachable": False,
                "meta": {
                    "working_usdc": working_usdc,
                    "floors_required": buckets["required_total"],
                    "shortfall": buckets["shortfall"],
                    "vault_usdc": vault_usdc if vault_known else None,
                    "gaps": dict(buckets["gaps"]),
                },
            }
        )

    if not dca["allow_dca"]:
        # Floor=0: only margin-heat (or explicit floor) should pause — not dust BP
        add(
            3,
            "dca_pause",
            f"Pause DCA: {dca['reason']}",
            actor="agent",
            detail="Do not place equity DCA while paused (margin heat or BP floor).",
            api_reachable=True,
        )
    elif dca["throttle"] == "slow":
        add(
            3,
            "dca_slow",
            f"Throttle DCA: {dca['reason']}",
            actor="agent",
            detail="Reduced DCA size until BP recovers above floor band.",
            api_reachable=True,
        )

    bp_floor = _f(p["rh_bp_floor"])
    # With MO floor=0, never invent CB→RH bridge just to pad BP
    bridge_gap_rh = max(0.0, bp_floor - bp) if bp_floor > 0 else 0.0
    if bridge_gap_rh > 0 and buckets["excess"] > 0:
        amt = min(
            bridge_gap_rh,
            buckets["excess"],
            _f(p["bridge_max_recommend_usdc"]),
        )
        if amt > 0:
            add(
                5,
                "bridge_cb_to_rh",
                f"Recommend bridge ${amt:.2f} USDC Coinbase → Robinhood",
                actor="human",
                detail="Recommend-only. Advanced Trade transfer is portfolio-internal only.",
                api_reachable=False,
            )
    elif (
        bp_floor > 0
        and bp > bp_floor * 1.5
        and cash > bp_floor
        and (
            buckets["shortfall"] > 0
            or (ltv is not None and ltv >= _f(p["cb_ltv_alert"]))
        )
    ):
        amt = min(cash * 0.5, _f(p["bridge_max_recommend_usdc"]), max(buckets["shortfall"], 100.0))
        add(
            5,
            "bridge_rh_to_cb",
            f"Recommend bridge ~${amt:.2f} cash Robinhood → Coinbase",
            actor="human",
            detail="Recommend-only to refill HY LTV Buffer.",
            api_reachable=False,
        )
    elif (
        bp_floor <= 0
        and cash >= 100
        and (
            buckets["shortfall"] > 0
            or (ltv is not None and ltv >= _f(p["cb_ltv_alert"]))
        )
    ):
        # No BP floor: still allow RH→CB recommend when HY LTV Buffer short / LTV hot
        amt = min(cash * 0.5, _f(p["bridge_max_recommend_usdc"]), max(buckets["shortfall"], 100.0))
        if amt >= 50:
            add(
                5,
                "bridge_rh_to_cb",
                f"Recommend bridge ~${amt:.2f} RH → Coinbase",
                actor="human",
                detail="Recommend-only — refill HY LTV Buffer, not card float.",
                api_reachable=False,
            )

    if buckets["excess"] > 0 and stress["coinbase_ltv"] == "green" and stress["robinhood"] != "red":
        to_cb = buckets["excess"] * _f(p["excess_split_cb"])
        to_rh = buckets["excess"] * _f(p["excess_split_rh"])
        add(
            6,
            "excess_allocate",
            f"Excess liquid USDC ${buckets['excess']:.2f}: ~${to_cb:.2f} CB yield/BTC path, ~${to_rh:.2f} RH cash/DCA",
            actor="either",
            detail="Vault deposit is in-app. Agent may buy liquid BTC or leave USDC; DCA only if governor allows.",
            api_reachable=True,
        )
        if dca["allow_dca"]:
            add(
                6,
                "dca_ok",
                "DCA allowed under current RH buying power policy",
                actor="agent",
                detail=dca["reason"],
                api_reachable=True,
            )

    exp_sum = (snapshot.get("expenses") or {}).get("summary") or {}
    # Upcoming estimates only (Essential tab) — not Discretionary capital targets
    cb_burn = exp_sum.get("coinbase_funded_monthly")
    rh_checking_burn = exp_sum.get("rh_funded_monthly") or exp_sum.get("rh_checking_funded_monthly")
    has_primary_stack = any(
        a.get("kind") in ("ltv_buffer_stack", "card_refinance", "hy_collateral_defense")
        for a in actions
    )
    if (
        cb_burn
        and working_usdc < float(cb_burn) * 0.25
        and not has_primary_stack
    ):
        add(
            3,
            "expense_burn",
            f"Bills ~${float(cb_burn):.0f}/mo vs USDC ${working_usdc:.0f}",
            actor="either",
            detail=(
                "Working USDC (spot+vault LTV sleeve) thin vs Coinbase-funded sheet bills. "
                "Ops bills via X Money; card via Morpho refinance."
            ),
            api_reachable=False,
        )
    elif cb_burn and working_usdc < float(cb_burn) * 0.25:
        add(
            5,
            "expense_burn",
            f"Bills ~${float(cb_burn):.0f}/mo vs USDC ${working_usdc:.0f}",
            actor="either",
            detail="Secondary — vault/spot runway vs sheet bills (not card float).",
            api_reachable=False,
        )
    if rh_checking_burn and bill_pay_cash is not None and float(bill_pay_cash) < float(rh_checking_burn) * 0.15:
        src = "YNAB RH Checking" if rh_checking_cash is not None else "RH brokerage cash (MCP)"
        add(
            3,
            "rh_checking_float",
            f"Est. RH Checking–funded bills ~${float(rh_checking_burn):.0f}/mo vs checking float ${float(bill_pay_cash):.2f} ({src})",
            actor="either",
            detail=(
                "Upcoming sheet bills marked RH Checking. "
                "Actual checking balance/txs from YNAB when linked; top up before ACH due dates."
            ),
            api_reachable=True,
        )
    if rh_checking.get("source") not in (None, "empty") and not rh_checking.get("live_error"):
        if rh_checking_cash is not None and rh_checking_cash < 50 and (rh_checking_burn or 0) > 0:
            add(
                3,
                "rh_checking_low",
                f"RH Checking balance low (${float(rh_checking_cash):.2f}) via YNAB",
                actor="human",
                detail=f"Account {rh_checking.get('account_name') or 'RH Checking'}: fund for ACH drafts.",
                api_reachable=False,
            )

    actions.sort(key=lambda a: a["priority"])

    # Simplified cashflow allocation guidance (free-dollar waterfall + margin capex)
    _exp_window = expense_due_window(snapshot)
    _disc = discretionary_capex_targets(snapshot)
    _free_dollar_red = overall == "red"
    cashflow_allocation = cashflow_allocation_guidance(
        expense_window=_exp_window,
        card_balance=card_balance,
        buckets=buckets,
        working_usdc=working_usdc,
        bank_cash=bank_cash if bank_cash_known else (bank_cash or 0.0),
        rh_buying_power=bp,
        dca=dca,
        discretionary=_disc,
        excess_split_cb=_f(p["excess_split_cb"]),
        excess_split_rh=_f(p["excess_split_rh"]),
        free_dollar_red=_free_dollar_red,
    )


    next_steps = [
        {
            "n": i + 1,
            "actor": a["actor"],
            "title": a["title"],
            "api_reachable": a["api_reachable"],
        }
        for i, a in enumerate(actions[:8])
    ]

    agent_brief_lines = [
        f"Overall stress: {overall}",
        f"LTV: {ltv if ltv is not None else 'UNKNOWN'}",
        f"HY LTV sleeve: ${working_usdc:.2f} (spot ${liquid_usdc:.2f} + HY vault "
        f"{('$' + format(vault_usdc, '.2f')) if vault_known else 'UNKNOWN'} — leverage defense, not card float)"
        f" | BTC liquid: {liquid_btc:.8f} (~${liquid_btc_usd:.2f})",
        f"One Card owed: ${card_balance:.2f} (source={card_source or 'none'})"
        + (f" | 30d spend ${one_card.get('spend_30d')}" if one_card.get("spend_30d") is not None else "")
        + " | path=Morpho refinance (not HY pull)",
        f"Engines: Morpho LTV={ltv if ltv is not None else 'UNKNOWN'}"
        + f" [{morpho_band['band']}]"
        + f" (tgt {_f(p.get('cb_ltv_target'), 0.38):.0%}/alert {_f(p['cb_ltv_alert']):.0%}/max {_f(p['cb_target_ltv_max']):.0%})"
        + f" | RH margin_use={margin_use if margin_use is not None else 'n/a'}"
        + f" [{margin_band['band']}]"
        + f" (tgt {_f(p.get('rh_margin_use_target'), 0.28):.0%}/alert {_f(p.get('rh_margin_use_alert'), 0.35):.0%}/max {_f(p['rh_margin_use_max']):.0%})"
        + f" | joint={leverage_bands['joint']['stance']}",
        f"Upcoming expense estimates (sheet Essential+Fleet): "
        f"${((snapshot.get('expenses') or {}).get('summary') or {}).get('upcoming_expense_monthly') or ((snapshot.get('expenses') or {}).get('summary') or {}).get('personal_monthly') or 0:.2f}/mo"
        + f" (personal ${((snapshot.get('expenses') or {}).get('summary') or {}).get('personal_monthly') or 0:.2f}"
        + f" + fleet ${((snapshot.get('expenses') or {}).get('summary') or {}).get('fleet_monthly') or 0:.2f})"
        + f" | CB-funded est ${((snapshot.get('expenses') or {}).get('summary') or {}).get('coinbase_funded_monthly') or 0:.2f}"
        + f" | RH-checking est ${((snapshot.get('expenses') or {}).get('summary') or {}).get('rh_funded_monthly') or 0:.2f}"
        + f" | X Money est ${((snapshot.get('expenses') or {}).get('summary') or {}).get('x_money_funded_monthly') or 0:.2f}"
        + f" | Collateral investments (not burn) $"
        f"{((snapshot.get('expenses') or {}).get('summary') or {}).get('collateral_investments_monthly') or ((snapshot.get('expenses') or {}).get('summary') or {}).get('collateral_monthly') or 0:.2f}/mo"
        + f" | Productive Discretionary (capital, not burn) "
        f"${((snapshot.get('expenses') or {}).get('summary') or {}).get('capital_targets_monthly') or ((snapshot.get('expenses') or {}).get('summary') or {}).get('productive_discretionary_monthly') or ((snapshot.get('expenses') or {}).get('summary') or {}).get('discretionary_monthly') or 0:.2f}/mo"
        + f" | Consumer Discretionary $"
        f"{((snapshot.get('expenses') or {}).get('summary') or {}).get('consumer_discretionary_monthly') or 0:.2f}/mo"
        + " | Actual spend: YNAB",
        f"RH Checking (YNAB): ${rh_checking_cash if rh_checking_cash is not None else 'n/a'}"
        + (f" | 30d spend ${rh_checking.get('spend_30d')}" if rh_checking.get("spend_30d") is not None else "")
        + f" | X Money: ${x_money_cash if x_money_cash is not None else 'n/a'}"
        + (f" ({x_money.get('account_name')})" if x_money.get("account_name") else "")
        + f" | RH brokerage BP: ${bp:.2f} cash: ${cash:.2f} equity: ${equity:.2f}",
        f"DCA: {'ALLOW' if dca['allow_dca'] else 'PAUSE'} ({dca['throttle']}) — {dca['reason']}",
        f"Data quality: {data_quality['status']} score={data_quality['completeness_score']}",
        "Top actions:",
    ]
    for a in actions[:6]:
        agent_brief_lines.append(
            f"  P{a['priority']} [{a['actor']}|{'API' if a['api_reachable'] else 'manual'}] {a['title']}"
        )
    agent_brief_lines.append(
        "Do not auto-bridge USDC or touch Morpho/vault/card via Advanced Trade transfer."
    )

    return {
        "policy": deepcopy(p),
        "inputs": {
            "liquid_usdc": liquid_usdc,
            "working_usdc": working_usdc,
            "vault_known": vault_known,
            "count_vault_toward_buffers": count_vault and vault_known,
            "liquid_btc": liquid_btc,
            "liquid_btc_usd": liquid_btc_usd,
            "btc_usd_price": btc_usd_price,
            "ltv": ltv,
            "loan_principal_usdc": principal if principal else None,
            "collateral_btc_usd": coll_usd if coll_usd else None,
            "vault_usdc": vault_usdc if vault_known else None,
            "card_balance": card_balance if not _is_missing(card_balance_raw) else None,
            "card_available_credit": card_avail,
            "card_security_deposit_usdc": card_deposit,
            "card_available_credit_source": card_avail_source,
            "card_source": card_source,
            "one_card_spend_30d": one_card.get("spend_30d"),
            "one_card_account": one_card.get("account_name"),
            "expenses_upcoming_monthly": (snapshot.get("expenses") or {}).get("summary", {}).get(
                "upcoming_expense_monthly"
            )
            or (snapshot.get("expenses") or {}).get("summary", {}).get("personal_monthly"),
            "expenses_personal_monthly": (snapshot.get("expenses") or {}).get("summary", {}).get(
                "personal_monthly"
            ),
            "expenses_fleet_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("fleet_monthly"),
            "expenses_collateral_investments_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("collateral_investments_monthly")
            or (snapshot.get("expenses") or {}).get("summary", {}).get("collateral_monthly"),
            "expenses_capital_targets_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("capital_targets_monthly")
            or (snapshot.get("expenses") or {}).get("summary", {}).get("discretionary_monthly"),
            "expenses_discretionary_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("discretionary_monthly")
            or (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("productive_discretionary_monthly"),
            "expenses_productive_discretionary_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("productive_discretionary_monthly")
            or (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("capital_targets_monthly"),
            "expenses_consumer_discretionary_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("consumer_discretionary_monthly"),
            # combined = Essential + Fleet burn; capital tabs excluded
            "expenses_combined_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("combined_monthly"),
            "expenses_coinbase_funded_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("coinbase_funded_monthly"),
            "expenses_rh_funded_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("rh_funded_monthly"),
            "expenses_x_money_funded_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("x_money_funded_monthly"),
            "rh_buying_power": bp,
            "rh_cash": cash,
            "rh_checking_cash": rh_checking_cash,
            "rh_checking_account": rh_checking.get("account_name"),
            "rh_checking_spend_30d": rh_checking.get("spend_30d"),
            "x_money_cash": x_money_cash,
            "x_money_account": x_money.get("account_name"),
            "x_money_spend_30d": x_money.get("spend_30d"),
            "x_money_apy_est": _f(x_money.get("apy_est"))
            if not _is_missing(x_money.get("apy_est"))
            else None,
            "bank_cash": bank_cash if bank_cash_known else None,
            "bill_pay_cash": bill_pay_cash,
            "rh_equity": equity,
            "rh_total_value": total_value,
            "rh_margin_use": margin_use,
            "rh_account_last4": rh.get("account_number_last4"),
            "rh_agentic_allowed_primary": rh.get("agentic_allowed"),
            "rh_agentic_account_last4": (rh.get("agentic") or {}).get("account_number_last4"),
            "rh_agentic_buying_power": _f((rh.get("agentic") or {}).get("buying_power"))
            if (rh.get("agentic") or {}).get("buying_power") is not None
            else None,
            "rh_agentic_cash": _f((rh.get("agentic") or {}).get("cash"))
            if (rh.get("agentic") or {}).get("cash") is not None
            else None,
            "rh_agentic_total_value": _f((rh.get("agentic") or {}).get("total_value"))
            if (rh.get("agentic") or {}).get("total_value") is not None
            else None,
            "rh_agentic_mcp_connected": bool((rh.get("mcp") or {}).get("connected")),
            "rh_positions_count": len(rh.get("positions") or []),
            "rh_agentic_positions_count": len((rh.get("agentic") or {}).get("positions") or []),
            # Robinhood Earn (USDG via Morpho) — manual; may not appear as separate MCP cash
            "rh_usdg_earn_usdg": _f(rh.get("usdg_earn_usdg"))
            if not _is_missing(rh.get("usdg_earn_usdg"))
            else None,
            "rh_usdg_earn_apy_est": _f(rh.get("usdg_earn_apy_est"))
            if not _is_missing(rh.get("usdg_earn_apy_est"))
            else None,
            "rh_margin_loan_usd": _f(rh.get("margin_loan_usd"))
            if not _is_missing(rh.get("margin_loan_usd"))
            else None,
            "rh_equity_collateral_usd": _f(rh.get("equity_collateral_usd"))
            if not _is_missing(rh.get("equity_collateral_usd"))
            else None,
        },
        "buckets": buckets,
        "dca": dca,
        "stress": stress,
        "leverage_bands": leverage_bands,
        "data_quality": data_quality,
        "actions": actions,
        "next_steps": next_steps,
        "agent_brief": "\n".join(agent_brief_lines),
        "cashflow_allocation": cashflow_allocation,
        "sleeves": {
            "card_float": {
                "target": _f(p["cb_card_float_usdc"]),
                "filled": buckets["filled"]["card_float"],
                "gap": buckets["gaps"]["card_float"],
                "note": (
                    "Optional spot reserve only; default 0. Card paydown = Morpho refinance, "
                    "not HY LTV Buffer. count_vault_toward_card_float="
                    f"{count_vault_card}"
                ),
            },
            "loan_buffer": {
                "target": _f(p["cb_loan_buffer_usdc"]),
                "filled": buckets["filled"]["loan_buffer"],
                "gap": buckets["gaps"]["loan_buffer"],
                "note": "HY LTV Buffer — generic high-yield leverage defense (not USDC/USDG-branded)",
            },
            "yield_sleeve": {
                "vault_usdc": vault_usdc if vault_known else None,
                "note": (
                    "HY LTV Buffer (Liquidity Engine). HY→Collateral only when LTV/margin hot; "
                    "else buffer floor. Not One Card funder. Venue product is incidental."
                ),
            },
            "working_usdc": {
                "spot": liquid_usdc,
                "vault": vault_usdc if vault_known else None,
                "total": working_usdc,
                "role": "ltv_buffer_sleeve",
            },
            "one_card": {
                "balance": card_balance if not _is_missing(card_balance_raw) else None,
                "available_credit": card_avail,
                "deposit": card_deposit,
                "path": "morpho_refinance",
                "note": "Morpho new principal → pay card (~5% vs ~29%); raises LTV deliberately",
            },
            "rh_cash_bp": {
                "buying_power": bp,
                "cash": cash,
                "equity": equity,
                "total_value": total_value,
            },
            "bridge_dry_powder": {
                "target": _f(p["cb_bridge_dry_powder_usdc"]),
                "filled": buckets["filled"]["bridge_dry_powder"],
                "gap": buckets["gaps"]["bridge_dry_powder"],
                "note": "Retired 2026-08-11 — floor=0; CB↔RH residual served by HY LTV Buffer",
            },
        },
        "strategy_context": {
            "goal": "Keep invested (BTC + Agentic equities) via LE credit cards: Digital Credit + Margin",
            "usdc_model": (
                "Capital Flows: Income → LE (X Money · Digital Credit · Margin) → Deploy. "
                "HY LTV Buffer is generic high-yield leverage defense (not USDC- or USDG-specific). "
                "Digital Credit = Morpho/BTC engine; Margin = Agentic/stocks engine; "
                "each rotates principal ↔ HY LTV Buffer vs borrow. "
                "X Money is cash hub. Morpho LTV bands: target 38% / alert 45% / hard 50%. "
                "RH margin bands: target 28% / alert 35% / hard 40% (call ~50%). "
                "Card refinance ~5% Morpho vs ~29% card APR."
            ),
            "leverage_bands": {
                "morpho_ltv": {
                    "target": _f(p.get("cb_ltv_target"), 0.38),
                    "alert": _f(p["cb_ltv_alert"]),
                    "hard_max": _f(p["cb_target_ltv_max"]),
                    "liq_ref": 0.86,
                },
                "rh_margin_use": {
                    "target": _f(p.get("rh_margin_use_target"), 0.28),
                    "alert": _f(p.get("rh_margin_use_alert"), 0.35),
                    "hard_max": _f(p["rh_margin_use_max"]),
                    "call_ref": 0.50,
                },
            },
            "priority_order": [
                "Essential + Fleet expenses current (sheet burn)",
                "One Card: Morpho refinance when balance owed (not HY pull)",
                "Morpho: cool (<38%) principal→HY LTV Buffer; hot (≥45%) HY→BTC; hard 50%",
                "X Money float for Deploy + scheduled capital pipes",
                "Productive Discretionary before Consumer wishlist",
                "RH margin: cool (<28%)→HY LTV Buffer; hot (≥35%) HY→stock; hard 40%",
                "Either engine hot → no new dual extract",
            ],
            "double_leverage_warning": (
                "DUAL ENGINE RISK: Do not fund RH margin growth with freshly "
                "borrowed Coinbase Morpho USDC without an explicit risk budget. "
                "BTC and equities often dump together — Morpho + RH margin stack liquidation risk. "
                "Margin loan → LE/CB extract is not free income."
            ),
            "in_app_only": [
                "loan protection",
                "Morpho repay/add BTC collateral (CB)",
                "Morpho borrow / One Card pay (refinance)",
                "HY LTV Buffer deposit/withdraw (venue HY)",
                "RH margin borrow / repay",
                "X Money → RH Agentic scheduled transfer",
                "external USDC send (bridge)",
            ],
        },
    }
