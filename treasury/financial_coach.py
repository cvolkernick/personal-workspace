#!/usr/bin/env python3
"""Financial coach: rank due expenses and allocate available cash to pay on time.

Pure ranking/allocation over plain dict snapshots (no network). CLI loads
latest snapshots under treasury/snapshots/ and prints JSON.

Usage:
  python3 -m treasury.financial_coach
  python3 -m treasury.financial_coach --snapshots-dir path/to/fixtures
  python3 -m treasury.financial_coach --as-of 2026-07-27
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import SNAPSHOTS_DIR, load_json  # noqa: E402
from treasury.expenses_sync import parse_sheet_date  # noqa: E402

# Map sheet/YNAB "from" labels → venue keys used for cash buckets
VENUE_ALIASES = {
    "coinbase": "coinbase",
    "cb": "coinbase",
    "x money": "x_money",
    "xmoney": "x_money",
    "xm": "x_money",
    "rh checking": "rh_checking",
    "rh_checking": "rh_checking",
    "robinhood checking": "rh_checking",
    "checking": "rh_checking",
    "rh": "rh_checking",
    "robinhood": "rh_checking",
}


def _f(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_as_of(s: Optional[str] = None) -> date:
    if s:
        raw = str(s).strip()
        if "T" in raw:
            raw = raw.split("T", 1)[0]
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            pass
        dt = parse_sheet_date(raw)
        if dt:
            return dt.date() if hasattr(dt, "date") else dt
    return datetime.now(timezone.utc).date()


def normalize_venue(from_label: Any) -> str:
    s = str(from_label or "").strip().lower()
    if not s:
        return "unknown"
    if s in VENUE_ALIASES:
        return VENUE_ALIASES[s]
    for key, venue in VENUE_ALIASES.items():
        if key in s:
            return venue
    return "unknown"


def days_until_due(due: Optional[date], today: date) -> Optional[int]:
    if due is None:
        return None
    return (due - today).days


def due_urgency_class(days_until: Optional[int]) -> str:
    """Match FCC bill-row CSS: due-red / due-yellow / due-green / due-unknown.

    Rules (same as financial-command/index.html dueUrgency):
      overdue or ≤7d → due-red; 8–14d → due-yellow; >14d → due-green; no date → due-unknown
    """
    if days_until is None:
        return "due-unknown"
    if days_until <= 7:
        return "due-red"
    if days_until <= 14:
        return "due-yellow"
    return "due-green"


def urgency_key(
    item: Dict[str, Any], today: date
) -> Tuple[int, int, int, float]:
    """Sort key: overdue first (more overdue earlier), then soonest due, then larger $.

    Returns a tuple suitable for ascending sort (smaller = more urgent).
    """
    due_raw = item.get("due_date") or item.get("date")
    due_dt = parse_sheet_date(due_raw) if due_raw else None
    due_d: Optional[date] = None
    if due_dt is not None:
        due_d = due_dt.date() if hasattr(due_dt, "date") else due_dt  # type: ignore[assignment]

    days = days_until_due(due_d, today)
    amount = abs(_f(item.get("amount_due"), _f(item.get("monthly"))))

    if days is None:
        # undated last among dated; still before nothing
        return (2, 10_000, 0, -amount)
    if days < 0:
        # overdue: tier 0; more overdue → smaller key via -days inverted
        return (0, days, 0, -amount)  # days negative; -30 before -1 when sorted ascending... wait
        # sorted ascending: -30 < -1, so more overdue first. Good.
    # future
    return (1, days, 0, -amount)


def rank_obligations(
    items: List[Dict[str, Any]], *, today: Optional[date] = None
) -> List[Dict[str, Any]]:
    """Return new list of obligations sorted by payment urgency."""
    t = today or datetime.now(timezone.utc).date()
    enriched: List[Dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        due_raw = row.get("date") or row.get("due_date")
        due_dt = parse_sheet_date(due_raw) if due_raw else None
        due_d: Optional[date] = None
        if due_dt is not None:
            due_d = due_dt.date() if hasattr(due_dt, "date") else due_dt  # type: ignore[assignment]
        days = days_until_due(due_d, t)
        monthly = _f(row.get("monthly"))
        amount = _f(row.get("amount_due"), monthly)
        is_estimate = row.get("amount_due") is None and monthly > 0
        venue = normalize_venue(row.get("from") or row.get("pay_from"))
        overdue = days is not None and days < 0
        enriched.append(
            {
                "id": row.get("id")
                or f"{venue}:{row.get('item') or row.get('name') or 'item'}:{due_raw or 'nodate'}",
                "item": row.get("item") or row.get("name") or "—",
                "from_label": row.get("from") or row.get("pay_from") or "",
                "venue": venue,
                "due_date": due_d.isoformat() if due_d else None,
                "due_date_raw": due_raw,
                "days_until_due": days,
                "overdue": overdue,
                "amount_due": round(amount, 2),
                "amount_is_estimate": bool(is_estimate),
                "monthly_estimate": round(monthly, 2) if monthly else None,
            }
        )
    enriched.sort(key=lambda r: urgency_key(r, t))
    for i, r in enumerate(enriched):
        r["urgency_rank"] = i + 1
    return enriched


def extract_venues(snapshots: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Build venue cash buckets from treasury + YNAB/coinbase snaps.

    Coinbase pay-from pool = liquid spot USDC + High Yield vault USDC (working float),
    with spot vs vault broken out so vault is not silently treated as free without note.
    """
    tre = snapshots.get("treasury") or {}
    ev = tre.get("evaluation") or tre
    inp = ev.get("inputs") or {}
    cb = snapshots.get("coinbase") or {}
    xm = snapshots.get("x_money") or {}
    rhc = snapshots.get("rh_checking") or {}

    liquid_spot = _f(inp.get("liquid_usdc"), _f(cb.get("liquid_usdc")))
    vault = _f(inp.get("vault_usdc"))
    if vault <= 0 and isinstance(cb.get("by_currency"), dict):
        # no vault in coinbase liquid snap typically
        pass
    working = _f(inp.get("working_usdc"), liquid_spot + vault)
    # Prefer explicit working; else sum spot+vault
    if working <= 0 and (liquid_spot > 0 or vault > 0):
        working = liquid_spot + vault

    x_cash = _f(inp.get("x_money_cash"), _f(xm.get("cash"), _f(xm.get("available"))))
    rh_cash = _f(
        inp.get("rh_checking_cash"), _f(rhc.get("cash"), _f(rhc.get("available")))
    )

    venues = {
        "coinbase": {
            "label": "Coinbase",
            "available": round(max(0.0, working), 2),
            "liquid_spot_usdc": round(max(0.0, liquid_spot), 2),
            "vault_usdc": round(max(0.0, vault), 2),
            "as_of": (snapshots.get("coinbase") or {}).get("as_of")
            or (tre.get("snapshot") or {}).get("as_of")
            or tre.get("as_of"),
            "notes": (
                "Working USDC = spot liquid + High Yield vault. "
                "Vault may require an app withdraw before external ACH."
                if vault > 0
                else "Spot liquid USDC only (no vault balance in snapshot)."
            ),
        },
        "x_money": {
            "label": "X Money",
            "available": round(max(0.0, x_cash), 2),
            "as_of": xm.get("as_of"),
            "notes": "YNAB Checking balance (X Money).",
        },
        "rh_checking": {
            "label": "RH Checking",
            "available": round(max(0.0, rh_cash), 2),
            "as_of": rhc.get("as_of"),
            "notes": "YNAB RH Checking float.",
        },
    }
    return venues


def allocate(
    obligations: List[Dict[str, Any]],
    venues: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, float], List[Dict[str, Any]]]:
    """Greedy: fund highest urgency first from matching venue cash.

    Returns (lines with allocation fields, residuals by venue, unfunded summary).
    """
    residual = {
        k: float(v.get("available") or 0) for k, v in venues.items() if k != "unknown"
    }
    residual.setdefault("unknown", 0.0)

    lines: List[Dict[str, Any]] = []
    unfunded: List[Dict[str, Any]] = []

    for ob in obligations:
        due = float(ob.get("amount_due") or 0)
        venue = ob.get("venue") or "unknown"
        if venue not in residual:
            residual[venue] = 0.0
        avail = residual[venue]
        allocated = min(max(0.0, due), max(0.0, avail))
        residual[venue] = round(avail - allocated, 2)
        gap = round(max(0.0, due - allocated), 2)
        status = (
            "funded"
            if gap <= 0.005 and due > 0
            else ("partial" if allocated > 0 else ("unfunded" if due > 0 else "zero"))
        )
        line = {
            **ob,
            "allocated": round(allocated, 2),
            "gap": gap,
            "status": status,
            "venue_available_before": round(avail, 2),
            "venue_available_after": residual[venue],
        }
        lines.append(line)
        if gap > 0.005:
            unfunded.append(
                {
                    "item": ob.get("item"),
                    "venue": venue,
                    "gap": gap,
                    "due_date": ob.get("due_date"),
                    "overdue": ob.get("overdue"),
                }
            )

    residuals_out = {k: round(v, 2) for k, v in residual.items()}
    return lines, residuals_out, unfunded


def _age_hours(iso: Any) -> Optional[float]:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return None


def infer_habits(
    snapshots: Dict[str, Any],
    obligations: List[Dict[str, Any]],
    venues: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    exp = snapshots.get("expenses") or {}
    summary = exp.get("summary") or {}
    tre = snapshots.get("treasury") or {}
    ev = tre.get("evaluation") or tre
    inp = ev.get("inputs") or {}
    xm = snapshots.get("x_money") or {}
    oc = snapshots.get("one_card") or {}
    rhc = snapshots.get("rh_checking") or {}

    # Burn daily = Personal + Fleet (combined_daily); fall back to personal_daily.
    personal_daily = _f(
        summary.get("combined_daily"), _f(summary.get("personal_daily"))
    )
    personal_monthly = _f(
        summary.get("upcoming_expense_monthly"),
        _f(summary.get("combined_monthly"), _f(summary.get("personal_monthly"))),
    )
    if personal_daily <= 0 and personal_monthly > 0:
        personal_daily = personal_monthly / 30.0

    total_liquid = sum(float(v.get("available") or 0) for v in venues.values())
    runway_days = (
        round(total_liquid / personal_daily, 1) if personal_daily > 0 else None
    )

    xm_spend_30d = _f(xm.get("spend_30d"), _f(inp.get("x_money_spend_30d")))
    oc_spend_30d = _f(
        oc.get("spend_30d"), _f(inp.get("one_card_spend_30d"))
    )
    rh_spend_30d = _f(rhc.get("spend_30d"), _f(inp.get("rh_checking_spend_30d")))
    xm_inflow_30d = _f(xm.get("inflow_30d"), _f(inp.get("x_money_inflow_30d")))

    overdue_n = sum(1 for o in obligations if o.get("overdue"))
    due_7 = sum(
        1
        for o in obligations
        if o.get("days_until_due") is not None
        and 0 <= int(o["days_until_due"]) <= 7
    )
    gap_total = sum(
        float(o.get("gap") or 0)
        for o in obligations
        if o.get("gap") is not None
    )

    by_src = (exp.get("summary") or {}).get("by_source_monthly") or {}
    if not by_src:
        # Merge Personal + Fleet when summary lacks combined pay-from
        by_src = {}
        for tab_name in ("Personal", "Fleet"):
            part = ((exp.get("tabs") or {}).get(tab_name) or {}).get(
                "by_source_monthly"
            ) or {}
            for k, v in part.items():
                by_src[k] = (by_src.get(k) or 0) + float(v or 0)

    return {
        "personal_daily_burn_est": round(personal_daily, 2) if personal_daily else None,
        "personal_monthly_est": round(personal_monthly, 2) if personal_monthly else None,
        "total_liquid_available": round(total_liquid, 2),
        "runway_days_at_sheet_burn": runway_days,
        "ynab_spend_30d": {
            "x_money": round(xm_spend_30d, 2),
            "one_card": round(oc_spend_30d, 2),
            "rh_checking": round(rh_spend_30d, 2),
            "combined_card_and_cash": round(
                xm_spend_30d + oc_spend_30d + rh_spend_30d, 2
            ),
        },
        "ynab_inflow_30d": {"x_money": round(xm_inflow_30d, 2)},
        "sheet_by_source_monthly": {
            k: round(_f(v), 2) for k, v in by_src.items() if v is not None
        },
        "obligation_pressure": {
            "overdue_count": overdue_n,
            "due_within_7d_count": due_7,
            "open_gap_total": round(gap_total, 2) if gap_total else 0.0,
        },
        "card_balance_owed": round(
            _f(inp.get("card_balance"), _f(oc.get("balance_owed"))), 2
        ),
        "notes": [
            "Sheet burn is forward-looking estimates (Personal + Fleet tabs), not YNAB actuals.",
            "YNAB spend_30d is realized card/checking outflow where present.",
            "Runway uses total liquid across Coinbase working + X Money + RH Checking vs sheet daily burn.",
        ],
    }


def collect_data_requests(
    snapshots: Dict[str, Any],
    obligations: List[Dict[str, Any]],
    venues: Dict[str, Dict[str, Any]],
) -> List[Dict[str, str]]:
    reqs: List[Dict[str, str]] = []
    tre = snapshots.get("treasury") or {}
    ev = tre.get("evaluation") or tre
    inp = ev.get("inputs") or {}
    exp = snapshots.get("expenses") or {}

    missing_dates = [
        o.get("item") for o in obligations if not o.get("due_date")
    ]
    if missing_dates:
        reqs.append(
            {
                "field": "due_dates",
                "why": "Some obligations lack parseable due dates and sort last.",
                "how": "Fill Date column on Personal Expense Sheet for: "
                + ", ".join(str(x) for x in missing_dates[:8]),
            }
        )

    estimates = [o for o in obligations if o.get("amount_is_estimate")]
    if estimates:
        reqs.append(
            {
                "field": "exact_amount_due",
                "why": "Allocations use monthly sheet estimates when amount_due is absent.",
                "how": "Confirm actual invoice amounts for high-priority lines (rent, insurance, loans).",
            }
        )

    if inp.get("ltv") is None and inp.get("loan_principal_usdc") is None:
        reqs.append(
            {
                "field": "morpho_ltv_principal",
                "why": "Cannot judge whether vault USDC should stay as loan buffer vs bill pay.",
                "how": "Enter Morpho principal/collateral/LTV in FCC Settings.",
            }
        )

    # Cash feeds go stale faster (6h); expenses/coinbase 12h; RH 12h
    for name, snap, max_h in (
        ("expenses", exp, 12.0),
        ("x_money", snapshots.get("x_money") or {}, 6.0),
        ("one_card", snapshots.get("one_card") or {}, 6.0),
        ("rh_checking", snapshots.get("rh_checking") or {}, 6.0),
        ("coinbase", snapshots.get("coinbase") or {}, 12.0),
        ("robinhood", snapshots.get("robinhood") or {}, 12.0),
    ):
        age = _age_hours(snap.get("as_of")) if snap else None
        if snap and age is not None and age > max_h:
            reqs.append(
                {
                    "field": f"{name}_freshness",
                    "why": (
                        f"{name} snapshot is ~{age:.0f}h old (warn >{max_h:.0f}h); "
                        "balances may be wrong until YNAB/sync refresh."
                    ),
                    "how": (
                        "FCC Refresh (live) or python3 treasury/ynab_sync.py"
                        if name in ("x_money", "one_card", "rh_checking")
                        else f"Refresh via FCC or matching sync for {name}."
                    ),
                }
            )
        if name != "robinhood" and not snap:
            reqs.append(
                {
                    "field": f"{name}_snapshot",
                    "why": f"No {name} snapshot loaded.",
                    "how": f"Run sync to produce treasury/snapshots/{name}_latest.json.",
                }
            )

    if (venues.get("coinbase") or {}).get("available", 0) <= 0.01:
        reqs.append(
            {
                "field": "coinbase_liquidity",
                "why": "Little/no Coinbase working USDC while many bills pay-from Coinbase.",
                "how": "Convert income to USDC, or withdraw vault / add Morpho float after LTV check.",
            }
        )

    rh = snapshots.get("robinhood") or {}
    if rh:
        age = _age_hours(rh.get("as_of"))
        if age is not None and age > 12:
            reqs.append(
                {
                    "field": "rh_trade_snapshot",
                    "why": "Robinhood trade snapshot stale — agentic BP/cash advisory may be wrong.",
                    "how": "Run treasury/rh_refresh.sh or live MCP rh_sync.",
                }
            )

    return reqs


def extract_expense_items(expenses: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Personal + Fleet burn items; fall back to upcoming_by_date or flat list.

    Fleet is auto-fleet obligations (loans/insurance/ops). Collateral and
    discretionary tabs are capital — not included in the pay plan.
    """
    tabs = expenses.get("tabs") or {}
    out: List[Dict[str, Any]] = []
    for tab_name in ("Personal", "Fleet"):
        tab = tabs.get(tab_name) or {}
        items = tab.get("items")
        if isinstance(items, list) and items:
            for i in items:
                if isinstance(i, dict):
                    rec = dict(i)
                    rec.setdefault("tab", tab_name)
                    out.append(rec)
            continue
        upcoming = tab.get("upcoming_by_date")
        if isinstance(upcoming, list) and upcoming:
            for i in upcoming:
                if isinstance(i, dict):
                    rec = dict(i)
                    rec.setdefault("tab", tab_name)
                    out.append(rec)
    if out:
        return out
    # flat list on root
    if isinstance(expenses.get("items"), list):
        return [i for i in expenses["items"] if isinstance(i, dict)]
    return []


def build_coach_plan(
    snapshots: Dict[str, Any],
    *,
    today: Optional[date] = None,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """Pure builder: snapshots dict → coach plan JSON."""
    t = today or _parse_as_of(as_of)
    expenses = snapshots.get("expenses") or {}
    raw_items = extract_expense_items(expenses)
    ranked = rank_obligations(raw_items, today=t)
    venues = extract_venues(snapshots)
    lines, residuals, unfunded = allocate(ranked, venues)
    # re-attach gaps onto lines already done in allocate
    habits = infer_habits(snapshots, lines, venues)
    data_requests = collect_data_requests(snapshots, lines, venues)

    total_due = round(sum(float(x.get("amount_due") or 0) for x in lines), 2)
    total_alloc = round(sum(float(x.get("allocated") or 0) for x in lines), 2)
    total_gap = round(sum(float(x.get("gap") or 0) for x in lines), 2)

    advice: List[str] = []
    overdue = [x for x in lines if x.get("overdue")]
    if overdue:
        advice.append(
            f"Pay {len(overdue)} overdue line(s) first "
            f"(${sum(float(x.get('gap') or x.get('amount_due') or 0) for x in overdue):.2f} still open)."
        )
    if total_gap > 0.01:
        advice.append(
            f"${total_gap:.2f} remains unfunded after allocating ${total_alloc:.2f} "
            f"of ${total_due:.2f} timed obligations — prioritize next income to highest urgency gaps."
        )
    else:
        advice.append(
            "All dated obligations with known amounts are fully covered by current venue cash mapping."
        )
    if (venues.get("coinbase") or {}).get("vault_usdc", 0) > 0:
        advice.append(
            "Coinbase pool includes High Yield vault USDC; withdraw in-app before ACH if the payee needs external cash."
        )
    rd = habits.get("runway_days_at_sheet_burn")
    if rd is not None and rd < 14:
        advice.append(
            f"Liquid runway ~{rd} days at sheet daily burn — avoid discretionary capital targets until buffers refill."
        )

    return {
        "ok": True,
        "as_of_plan": t.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "obligation_count": len(lines),
            "total_due": total_due,
            "total_allocated": total_alloc,
            "total_gap": total_gap,
            "overdue_count": sum(1 for x in lines if x.get("overdue")),
            "venues": {
                k: {
                    "label": v.get("label"),
                    "available": v.get("available"),
                    "residual": residuals.get(k),
                    "notes": v.get("notes"),
                    "liquid_spot_usdc": v.get("liquid_spot_usdc"),
                    "vault_usdc": v.get("vault_usdc"),
                    "as_of": v.get("as_of"),
                    "age_hours": (
                        round(_age_hours(v.get("as_of")), 1)
                        if _age_hours(v.get("as_of")) is not None
                        else None
                    ),
                }
                for k, v in venues.items()
            },
        },
        "obligations": lines,
        "residuals": residuals,
        "unfunded": unfunded,
        "habits": habits,
        "data_requests": data_requests,
        "advice": advice,
        "methodology": {
            "sort": "overdue (more overdue first) → soonest future due → larger amount",
            "allocation": "greedy per-line from matching pay-from venue until cash exhausted",
            "amounts": "prefer amount_due; else monthly sheet estimate (flagged)",
            "no_auto_pay": True,
        },
    }


def load_snapshots(directory: Path) -> Dict[str, Any]:
    d = Path(directory)
    out: Dict[str, Any] = {}
    mapping = {
        "expenses": "expenses_latest.json",
        "x_money": "x_money_latest.json",
        "one_card": "one_card_latest.json",
        "rh_checking": "rh_checking_latest.json",
        "coinbase": "coinbase_latest.json",
        "robinhood": "robinhood_latest.json",
        "treasury": "treasury_latest.json",
    }
    for key, fname in mapping.items():
        p = d / fname
        # treasury_latest also lives under financial-command/
        if key == "treasury" and not p.is_file():
            alt = ROOT / "financial-command" / "treasury_latest.json"
            if alt.is_file():
                p = alt
        data = load_json(p)
        if data:
            out[key] = data
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--snapshots-dir",
        type=Path,
        default=SNAPSHOTS_DIR,
        help=f"Directory of *_latest.json (default {SNAPSHOTS_DIR})",
    )
    p.add_argument("--as-of", type=str, default=None, help="Plan date YYYY-MM-DD")
    p.add_argument("--out", type=Path, default=None, help="Write JSON to path")
    p.add_argument("--pretty", action="store_true", help="Indent JSON on stdout")
    args = p.parse_args(argv)

    snaps = load_snapshots(args.snapshots_dir)
    plan = build_coach_plan(snaps, as_of=args.as_of)
    text = json.dumps(plan, indent=2 if args.pretty else None) + (
        "\n" if args.pretty or args.out else ""
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(plan, indent=2) + "\n", encoding="utf-8"
        )
    # always print for CLI capture
    if args.pretty or not args.out:
        sys.stdout.write(json.dumps(plan, indent=2) + "\n" if args.pretty else json.dumps(plan) + "\n")
    elif args.out:
        # still print one-line summary
        s = plan.get("summary") or {}
        print(
            json.dumps(
                {
                    "ok": plan.get("ok"),
                    "out": str(args.out),
                    "total_due": s.get("total_due"),
                    "total_allocated": s.get("total_allocated"),
                    "total_gap": s.get("total_gap"),
                    "obligations": s.get("obligation_count"),
                }
            )
        )
    return 0 if plan.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
