"""Pure treasury policy: buckets, stress colors, priority actions, DCA governor.

No I/O. Callers pass a normalized snapshot dict.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


DEFAULT_POLICY: Dict[str, Any] = {
    "cb_target_ltv_max": 0.50,
    "cb_ltv_alert": 0.45,
    "cb_card_float_usdc": 500.0,
    "cb_loan_buffer_usdc": 1000.0,
    "cb_bridge_dry_powder_usdc": 200.0,
    "rh_bp_floor": 500.0,
    "rh_margin_use_max": 0.40,
    "excess_split_cb": 0.60,
    "excess_split_rh": 0.40,
    "bridge_max_recommend_usdc": 5000.0,
    "stale_after_hours": 6.0,
    # Strategy: prefer High Yield Morpho vault USDC (~variable yield) over idle spot USDC.
    # Spot liquid USDC may intentionally be ~0; vault holds working USDC float.
    "count_vault_toward_buffers": True,
    "min_spot_usdc_warn": 0.0,  # do not require idle spot if vault covers buffers
    # Secured One Card: available credit ≈ security deposit USDC − balance owed
    "one_card_security_deposit_usdc": 500.0,
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
    """Split liquid USDC into required floors vs excess."""
    floors = {
        "card_float": max(0.0, card_float),
        "loan_buffer": max(0.0, loan_buffer),
        "bridge_dry_powder": max(0.0, bridge_dry_powder),
    }
    required = sum(floors.values())
    shortfall = max(0.0, required - liquid_usdc)
    excess = max(0.0, liquid_usdc - required)

    remaining = liquid_usdc
    filled = {}
    gaps = {}
    for name in ("card_float", "loan_buffer", "bridge_dry_powder"):
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


def _ltv_stress(ltv: Optional[float], alert: float, max_ltv: float) -> str:
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
    card_float_gap: float,
) -> str:
    """Unknown card fields → yellow (not green). Balance with float gap → red."""
    if _is_missing(card_balance_raw) and _is_missing(card_avail_raw):
        return "yellow"
    if card_balance > 0 and card_float_gap > 0:
        return "red"
    if card_balance > 0 and card_avail is not None and card_avail < 100:
        return "yellow"
    if _is_missing(card_balance_raw) or _is_missing(card_avail_raw):
        return "yellow"
    return "green"


def _rh_stress(
    buying_power: float,
    bp_floor: float,
    margin_use: Optional[float],
    margin_max: float,
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
    if rhc.get("source") in (None, "empty"):
        warnings.append("RH Checking / YNAB snapshot missing — link in YNAB and run ynab_sync")
    elif rhc.get("live_error"):
        warnings.append(f"YNAB RH Checking: {rhc['live_error']}")
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
    if ex.get("source") not in (None, "empty") and not ex.get("live_error"):
        sources_ok += 1
    score = (manual_filled / manual_total) * 0.5 + (sources_ok / 5.0) * 0.5

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
            "expenses": ex.get("source"),
            "coinbase_as_of": cb.get("as_of"),
            "robinhood_as_of": rh.get("as_of"),
            "one_card_as_of": oc.get("as_of"),
            "rh_checking_as_of": rhc.get("as_of"),
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
    vault_raw = man.get("vault_usdc")
    vault_known = not _is_missing(vault_raw)
    vault_usdc = _f(vault_raw) if vault_known else 0.0
    # Live Morpho HY vault APY from GraphQL poller (snapshot.morpho_hy).
    # vault_apy is vault reference only — never a product chip rate.
    # Settings stay on config.coinbase_manual and are not copied here so
    # spectrum can keep live product_apy > settings fallback > seed distinct.
    mh = snapshot.get("morpho_hy") if isinstance(snapshot.get("morpho_hy"), dict) else {}
    vault_apy = None
    for key in ("vault_apy", "apy_est", "apy", "avg_net_apy"):
        if not _is_missing(mh.get(key)):
            try:
                vault_apy = float(mh.get(key))
            except (TypeError, ValueError):
                vault_apy = None
            else:
                break
    # product_apy = Coinbase One / in-app when honest. GraphQL vault fields
    # must not invent this. Settings stay on config, not copied here.
    product_apy = None
    for key in ("product_apy", "morpho_hy_product_apy"):
        if not _is_missing(mh.get(key)):
            try:
                product_apy = float(mh.get(key))
            except (TypeError, ValueError):
                product_apy = None
            else:
                break
    # Live USDG HY APY from GraphQL poller (snapshot.usdg_hy). Settings
    # stay on config.robinhood and are not copied here so
    # spectrum can keep live > settings fallback > seed distinct. Never invent
    # a post-Gold rate from a Gold-cancelled flag.
    uh = snapshot.get("usdg_hy") if isinstance(snapshot.get("usdg_hy"), dict) else {}
    rh_usdg_earn_apy_est = None
    for key in ("apy_est", "apy", "avg_net_apy"):
        if not _is_missing(uh.get(key)):
            try:
                rh_usdg_earn_apy_est = float(uh.get(key))
            except (TypeError, ValueError):
                rh_usdg_earn_apy_est = None
            else:
                break
    # Live Morpho borrow APR from GraphQL poller (snapshot.morpho_borrow).
    # Settings stay on config.coinbase_manual and are not copied here so
    # spectrum can keep live > settings fallback > seed distinct.
    mb = snapshot.get("morpho_borrow") if isinstance(snapshot.get("morpho_borrow"), dict) else {}
    variable_apr = None
    for key in ("apr", "variable_apr", "avg_borrow_apy", "apy_est", "apy"):
        if not _is_missing(mb.get(key)):
            try:
                variable_apr = float(mb.get(key))
            except (TypeError, ValueError):
                variable_apr = None
            else:
                break
    rh_usdg_earn_usdg = None
    if not _is_missing(rh.get("usdg_earn_usdg")):
        rh_usdg_earn_usdg = _f(rh.get("usdg_earn_usdg"))
    count_vault = bool(p.get("count_vault_toward_buffers", True))
    # Working USDC = idle Advanced Trade USDC + High Yield vault (when known & enabled)
    working_usdc = liquid_usdc + (vault_usdc if count_vault and vault_known else 0.0)
    card_balance_raw = man.get("card_balance")
    if _is_missing(card_balance_raw):
        card_balance_raw = one_card.get("card_balance")
        if _is_missing(card_balance_raw):
            card_balance_raw = one_card.get("balance_owed")
    card_avail_raw = man.get("card_available_credit")
    if _is_missing(card_avail_raw):
        card_avail_raw = one_card.get("card_available_credit")
        if _is_missing(card_avail_raw):
            card_avail_raw = one_card.get("available_credit")
    card_balance = _f(card_balance_raw) if not _is_missing(card_balance_raw) else 0.0
    card_avail = None if _is_missing(card_avail_raw) else _f(card_avail_raw)
    card_source = man.get("card_balance_source") or (
        "ynab" if one_card.get("source") in ("ynab", "snapshot") and not _is_missing(card_balance_raw) else None
    )
    if card_source is None and not _is_missing(man.get("card_balance")):
        card_source = "manual"

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
    # Effective cash for bill-pay: checking first, else brokerage cash
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

    # Buffers scored against *working* USDC (vault + spot), not idle spot alone
    buckets = classify_liquid_usdc(
        working_usdc,
        card_float=_f(p["cb_card_float_usdc"]),
        loan_buffer=_f(p["cb_loan_buffer_usdc"]),
        bridge_dry_powder=_f(p["cb_bridge_dry_powder_usdc"]),
    )
    buckets["liquid_spot_usdc"] = liquid_usdc
    buckets["vault_usdc"] = vault_usdc if vault_known else None
    buckets["working_usdc"] = working_usdc
    buckets["count_vault_toward_buffers"] = count_vault and vault_known
    # Spot-only view (for transparency / intentional zero idle USDC)
    spot_buckets = classify_liquid_usdc(
        liquid_usdc,
        card_float=_f(p["cb_card_float_usdc"]),
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
        "coinbase_ltv": _ltv_stress(ltv, _f(p["cb_ltv_alert"]), _f(p["cb_target_ltv_max"])),
        "coinbase_liquid": usdc_liq_stress,
        "coinbase_card": _card_stress(
            card_balance_raw=card_balance_raw,
            card_avail_raw=card_avail_raw,
            card_balance=card_balance,
            card_avail=card_avail,
            card_float_gap=buckets["gaps"]["card_float"],
        ),
        "robinhood": _rh_stress(bp, _f(p["rh_bp_floor"]), margin_use, _f(p["rh_margin_use_max"])),
        "data_quality": data_quality["status"],
    }
    order = {"green": 0, "yellow": 1, "red": 2}
    overall = max(stress.values(), key=lambda c: order.get(c, 0))
    stress["overall"] = overall

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

    if data_quality["missing_manual_fields"]:
        add(
            0,
            "fill_manual",
            "Fill missing Coinbase app fields in config / UI",
            actor="human",
            detail="Missing: " + ", ".join(data_quality["missing_manual_fields"]),
            api_reachable=False,
        )

    if ltv is None:
        add(
            1,
            "ltv_check",
            "Confirm Morpho loan LTV in Coinbase app",
            actor="human",
            detail="LTV not readable via Advanced Trade API. Update treasury config; enable loan protection.",
            api_reachable=False,
        )
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
            "Enter High Yield vault USDC (working float lives here, not idle spot)",
            actor="human",
            detail=(
                "Spot USDC often ~$0 intentionally (borrowed USDC → card paydown + High Yield vault). "
                "Enter vault balance in Settings so buffers score correctly."
            ),
            api_reachable=False,
        )
    if buckets["gaps"]["card_float"] > 0:
        add(
            2,
            "card_float",
            f"USDC float short ${buckets['gaps']['card_float']:.2f} (spot + High Yield vault)",
            actor="either",
            detail=(
                "Buffers use working USDC: Advanced Trade spot + Morpho High Yield vault. "
                "Idle spot may be ~$0 by design if vault holds the float."
            ),
            api_reachable=False,
        )
    if card_balance > 0:
        add(
            2,
            "card_paydown",
            f"One Card balance ${card_balance:.2f} — pay down in app / confirm autopay",
            actor="human",
            detail="Autopay + manual paydown only. Maximize available credit in-app.",
            api_reachable=False,
        )
    elif _is_missing(card_balance_raw):
        add(
            2,
            "card_unknown",
            "Enter One Card balance & available credit from app",
            actor="human",
            detail="Card metrics are unknown — stress cannot be green until filled.",
            api_reachable=False,
        )

    if not dca["allow_dca"]:
        add(
            3,
            "dca_pause",
            f"Pause DCA: {dca['reason']}",
            actor="agent",
            detail="API-reachable: do not place equity DCA buys while paused. Review RH portfolio/BP.",
            api_reachable=True,
        )
    elif dca["throttle"] == "slow":
        add(
            3,
            "dca_slow",
            f"Throttle DCA: {dca['reason']}",
            actor="agent",
            detail="Allow only reduced DCA size until BP recovers.",
            api_reachable=True,
        )

    if buckets["gaps"]["loan_buffer"] > 0:
        add(
            4,
            "loan_buffer",
            f"Loan buffer short ${buckets['gaps']['loan_buffer']:.2f} working USDC",
            actor="either",
            detail="Vault or free BTC/USDC for Morpho top-up; loan protection in app.",
            api_reachable=False,
        )

    if buckets["gaps"]["bridge_dry_powder"] > 0 and buckets["shortfall"] > 0:
        add(
            4,
            "bridge_powder",
            f"Bridge dry powder short ${buckets['gaps']['bridge_dry_powder']:.2f} working USDC",
            actor="either",
            detail="Small USDC sleeve (often pulled from vault when needed) for CB↔RH moves.",
            api_reachable=False,
        )

    bridge_gap_rh = max(0.0, _f(p["rh_bp_floor"]) - bp)
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
    elif bp > _f(p["rh_bp_floor"]) * 1.5 and cash > _f(p["rh_bp_floor"]) and (
        buckets["shortfall"] > 0 or (ltv is not None and ltv >= _f(p["cb_ltv_alert"]))
    ):
        amt = min(cash * 0.5, _f(p["bridge_max_recommend_usdc"]), max(buckets["shortfall"], 100.0))
        add(
            5,
            "bridge_rh_to_cb",
            f"Recommend bridge ~${amt:.2f} cash Robinhood → Coinbase",
            actor="human",
            detail="Recommend-only to refill CB card/loan buffers or LTV defense.",
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

    if vault_known and vault_usdc > 0 and buckets["shortfall"] > 0:
        add(
            4,
            "vault_pull",
            f"Pull up to ${min(vault_usdc, buckets['shortfall']):.2f} from High Yield vault for card/buffers (app)",
            actor="human",
            detail="Primary USDC home is High Yield (~variable, often higher than idle rewards). Withdraw when paying card or topping Morpho.",
            api_reachable=False,
        )
    exp_sum = (snapshot.get("expenses") or {}).get("summary") or {}
    # Upcoming estimates only (Personal tab) — not Discretionary capital targets
    cb_burn = exp_sum.get("coinbase_funded_monthly")
    rh_checking_burn = exp_sum.get("rh_funded_monthly") or exp_sum.get("rh_checking_funded_monthly")
    if cb_burn and working_usdc < float(cb_burn) * 0.25:
        add(
            3,
            "expense_burn",
            f"Est. Coinbase-funded bills ~${float(cb_burn):.0f}/mo vs working USDC ${working_usdc:.2f} (spot+vault)",
            actor="either",
            detail=(
                "Working USDC = idle spot + High Yield vault. Spot often ~$0 by design. "
                "Ensure vault (or borrow capacity) covers Coinbase-paid obligations."
            ),
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
        f"USDC working: ${working_usdc:.2f} (spot ${liquid_usdc:.2f} + vault "
        f"{('$' + format(vault_usdc, '.2f')) if vault_known else 'UNKNOWN'})"
        f" | BTC liquid: {liquid_btc:.8f} (~${liquid_btc_usd:.2f})",
        f"One Card owed: ${card_balance:.2f} (source={card_source or 'none'})"
        + (f" | 30d spend ${one_card.get('spend_30d')}" if one_card.get("spend_30d") is not None else ""),
        f"Upcoming expense estimates (sheet Personal): "
        f"${((snapshot.get('expenses') or {}).get('summary') or {}).get('upcoming_expense_monthly') or ((snapshot.get('expenses') or {}).get('summary') or {}).get('personal_monthly') or 0:.2f}/mo"
        + f" | CB-funded est ${((snapshot.get('expenses') or {}).get('summary') or {}).get('coinbase_funded_monthly') or 0:.2f}"
        + f" | RH-checking est ${((snapshot.get('expenses') or {}).get('summary') or {}).get('rh_funded_monthly') or 0:.2f}"
        + f" | Capital targets (Discretionary, not burn) "
        f"${((snapshot.get('expenses') or {}).get('summary') or {}).get('capital_targets_monthly') or ((snapshot.get('expenses') or {}).get('summary') or {}).get('discretionary_monthly') or 0:.2f}/mo"
        + " | Actual spend: YNAB",
        f"RH Checking (YNAB): ${rh_checking_cash if rh_checking_cash is not None else 'n/a'}"
        + (f" | 30d spend ${rh_checking.get('spend_30d')}" if rh_checking.get("spend_30d") is not None else "")
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
            "vault_apy": vault_apy,
            "product_apy": product_apy,
            "variable_apr": variable_apr,
            "rh_usdg_earn_apy_est": rh_usdg_earn_apy_est,
            "rh_usdg_earn_usdg": rh_usdg_earn_usdg,
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
            "expenses_capital_targets_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("capital_targets_monthly")
            or (snapshot.get("expenses") or {}).get("summary", {}).get("discretionary_monthly"),
            "expenses_discretionary_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("discretionary_monthly"),
            # combined = upcoming only (Personal); discretionary excluded from burn
            "expenses_combined_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("combined_monthly"),
            "expenses_coinbase_funded_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("coinbase_funded_monthly"),
            "expenses_rh_funded_monthly": (snapshot.get("expenses") or {})
            .get("summary", {})
            .get("rh_funded_monthly"),
            "rh_buying_power": bp,
            "rh_cash": cash,
            "rh_checking_cash": rh_checking_cash,
            "rh_checking_account": rh_checking.get("account_name"),
            "rh_checking_spend_30d": rh_checking.get("spend_30d"),
            "bill_pay_cash": bill_pay_cash,
            "rh_equity": equity,
            "rh_total_value": total_value,
            "rh_margin_use": margin_use,
        },
        "buckets": buckets,
        "dca": dca,
        "stress": stress,
        "data_quality": data_quality,
        "actions": actions,
        "next_steps": next_steps,
        "agent_brief": "\n".join(agent_brief_lines),
        "sleeves": {
            "card_float": {
                "target": _f(p["cb_card_float_usdc"]),
                "filled": buckets["filled"]["card_float"],
                "gap": buckets["gaps"]["card_float"],
            },
            "loan_buffer": {
                "target": _f(p["cb_loan_buffer_usdc"]),
                "filled": buckets["filled"]["loan_buffer"],
                "gap": buckets["gaps"]["loan_buffer"],
            },
            "yield_sleeve": {
                "vault_usdc": vault_usdc if vault_known else None,
                "note": "High Yield Morpho vault = primary USDC home (not idle spot)",
            },
            "working_usdc": {
                "spot": liquid_usdc,
                "vault": vault_usdc if vault_known else None,
                "total": working_usdc,
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
            },
        },
        "strategy_context": {
            "goal": "Keep invested (BTC collateral + RH equities) while preserving liquidity optionality",
            "usdc_model": (
                "BTC → Morpho collateral → borrow USDC → pay One Card + High Yield vault. "
                "Idle Advanced Trade USDC often ~$0 by design (lower yield than vault)."
            ),
            "priority_order": [
                "Protect Morpho LTV (<50% target)",
                "Card / buffers from working USDC (vault + spot)",
                "RH Checking float for ACH bills",
                "RH margin heat / DCA throttle",
                "Pull vault when paying card or topping collateral",
                "Bridge recommend CB↔RH",
                "Excess → vault / capital targets / DCA",
            ],
            "double_leverage_warning": (
                "Do not fund RH margin-driven DCA with freshly borrowed Coinbase USDC "
                "without an explicit risk budget. BTC and growth equities often dump together."
            ),
            "in_app_only": [
                "loan protection",
                "Morpho repay/add collateral",
                "High Yield vault deposit/withdraw",
                "One Card pay / autopay",
                "external USDC send (bridge)",
            ],
        },
    }
