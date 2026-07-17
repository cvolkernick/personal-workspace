"""Pure treasury policy: buckets, stress colors, priority actions, DCA governor.

No I/O. Callers pass a normalized snapshot dict.
"""

from __future__ import annotations

from copy import deepcopy
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
}


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


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

    # Allocate liquid to floors in priority order until exhausted
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
    """Decide whether DCA buys are allowed given RH buying power / margin heat.

    Returns allow_dca bool, reason, and throttle level.
    """
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
    # Soft throttle when within 25% of floor
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
        return "yellow"  # unknown — needs human check
    if ltv >= max_ltv:
        return "red"
    if ltv >= alert:
        return "yellow"
    return "green"


def _rh_stress(buying_power: float, bp_floor: float, margin_use: Optional[float], margin_max: float) -> str:
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


def evaluate_treasury(
    snapshot: Dict[str, Any],
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate dual-venue treasury and return stress + priority-ordered actions.

    Expected snapshot shape (all optional numbers default safely):
      coinbase.liquid_usdc, coinbase.liquid_btc
      coinbase_manual.loan_principal_usdc, collateral_btc_usd, ltv,
                     vault_usdc, card_balance, card_available_credit
      robinhood.buying_power, cash, equity_value, total_value, margin_use
    """
    p = {**DEFAULT_POLICY, **(policy or {})}
    cb = snapshot.get("coinbase") or {}
    man = snapshot.get("coinbase_manual") or {}
    rh = snapshot.get("robinhood") or {}

    liquid_usdc = _f(cb.get("liquid_usdc"))
    liquid_btc = _f(cb.get("liquid_btc"))
    ltv = man.get("ltv")
    if ltv is not None:
        ltv = _f(ltv)
    # Derive LTV proxy if principal + collateral USD provided and ltv missing
    principal = _f(man.get("loan_principal_usdc"))
    coll_usd = _f(man.get("collateral_btc_usd"))
    if ltv is None and principal > 0 and coll_usd > 0:
        ltv = principal / coll_usd

    vault_usdc = _f(man.get("vault_usdc"))
    card_balance = _f(man.get("card_balance"))
    card_avail = man.get("card_available_credit")
    card_avail = None if card_avail is None else _f(card_avail)

    bp = _f(rh.get("buying_power"))
    cash = _f(rh.get("cash"))
    equity = _f(rh.get("equity_value", rh.get("total_value")))
    margin_use = rh.get("margin_use")
    if margin_use is not None:
        margin_use = _f(margin_use)
    elif equity > 0 and bp >= 0:
        # Rough proxy: if unleveraged BP available, prefer that; else leave None
        unlev = rh.get("unleveraged_buying_power")
        if unlev is not None and _f(unlev) > 0 and bp > _f(unlev):
            # margin component of BP / equity as crude heat
            margin_use = min(1.0, max(0.0, (bp - _f(unlev)) / equity))

    buckets = classify_liquid_usdc(
        liquid_usdc,
        card_float=_f(p["cb_card_float_usdc"]),
        loan_buffer=_f(p["cb_loan_buffer_usdc"]),
        bridge_dry_powder=_f(p["cb_bridge_dry_powder_usdc"]),
    )
    dca = dca_governor(
        bp,
        bp_floor=_f(p["rh_bp_floor"]),
        margin_use=margin_use,
        margin_use_max=_f(p["rh_margin_use_max"]),
        cash=cash,
    )

    stress = {
        "coinbase_ltv": _ltv_stress(ltv, _f(p["cb_ltv_alert"]), _f(p["cb_target_ltv_max"])),
        "coinbase_liquid": buckets["status"],
        "coinbase_card": (
            "red"
            if card_balance > 0 and buckets["gaps"]["card_float"] > 0
            else ("yellow" if card_balance > 0 and card_avail is not None and card_avail < 100 else "green")
        ),
        "robinhood": _rh_stress(bp, _f(p["rh_bp_floor"]), margin_use, _f(p["rh_margin_use_max"])),
    }
    # Overall = worst of children
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
                "actor": actor,  # agent | human | either
                "detail": detail,
                "api_reachable": api_reachable,
            }
        )

    # 1) Protect LTV
    if ltv is None:
        add(
            1,
            "ltv_check",
            "Confirm Morpho loan LTV in Coinbase app",
            actor="human",
            detail="LTV not readable via Advanced Trade API. Update treasury config after checking app; enable loan protection.",
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

    # 2) Card float
    if buckets["gaps"]["card_float"] > 0:
        add(
            2,
            "card_float",
            f"Fund card float: need ${buckets['gaps']['card_float']:.2f} more liquid USDC",
            actor="either",
            detail="Keep liquid USDC for One Card autopay. Pay card and manage available credit in Coinbase app (no card API).",
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

    # 3) RH margin heat / DCA
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

    # 4) Loan buffer gap
    if buckets["gaps"]["loan_buffer"] > 0:
        add(
            4,
            "loan_buffer",
            f"Loan buffer short ${buckets['gaps']['loan_buffer']:.2f} liquid USDC",
            actor="either",
            detail="Hold free USDC/BTC for Morpho top-up; apply in app or via loan protection.",
            api_reachable=False,
        )

    # 5) Bridge recommend (never execute via portfolio transfer alone)
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
                detail="Recommend-only. Advanced Trade transfer is portfolio-internal only; use app Send or allowlisted Send Money API.",
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

    # 6) Excess allocation
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

    if vault_usdc > 0 and buckets["shortfall"] > 0:
        add(
            4,
            "vault_pull",
            f"Consider withdrawing up to ${min(vault_usdc, buckets['shortfall']):.2f} from High Yield vault (app)",
            actor="human",
            detail="Coinbase Lend withdraw is app-only; use to refill liquid floors.",
            api_reachable=False,
        )

    actions.sort(key=lambda a: a["priority"])

    return {
        "policy": deepcopy(p),
        "inputs": {
            "liquid_usdc": liquid_usdc,
            "liquid_btc": liquid_btc,
            "ltv": ltv,
            "loan_principal_usdc": principal,
            "collateral_btc_usd": coll_usd,
            "vault_usdc": vault_usdc,
            "card_balance": card_balance,
            "card_available_credit": card_avail,
            "rh_buying_power": bp,
            "rh_cash": cash,
            "rh_equity": equity,
            "rh_margin_use": margin_use,
        },
        "buckets": buckets,
        "dca": dca,
        "stress": stress,
        "actions": actions,
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
            "yield_sleeve": {"vault_usdc": vault_usdc, "note": "manual / app only"},
            "rh_cash_bp": {"buying_power": bp, "cash": cash, "equity": equity},
            "bridge_dry_powder": {
                "target": _f(p["cb_bridge_dry_powder_usdc"]),
                "filled": buckets["filled"]["bridge_dry_powder"],
                "gap": buckets["gaps"]["bridge_dry_powder"],
            },
        },
    }
