"""FitDash HSA view — position, contribution pace, shoebox, sats-for-steps."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from .hsa_store import (
    SATS_PER_BTC,
    contribution_limit_usd,
    irs_limits_for,
    load_hsa,
    normalize_store,
)
from .models import StepSample
from .timeutil import local_today_iso


HDHP_COPY = (
    "HSA contributions require enrollment in a qualifying high-deductible "
    "health plan (HDHP). Confirm the plan separately — this section does not "
    "guess eligibility from labs or spend."
)


def _as_date(value: Any) -> Optional[date]:
    s = str(value or "").strip()[:10]
    if len(s) < 10:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _year_progress(as_of: date) -> Dict[str, Any]:
    days_in_year = 366 if calendar.isleap(as_of.year) else 365
    doy = as_of.timetuple().tm_yday
    return {
        "day_of_year": doy,
        "days_in_year": days_in_year,
        "elapsed_frac": round(doy / days_in_year, 6),
        "days_remaining": max(0, days_in_year - doy),
    }


def _step_rows(steps: Iterable[Any]) -> List[dict]:
    out: List[dict] = []
    for item in steps or []:
        if isinstance(item, StepSample):
            d = item.to_dict()
        elif isinstance(item, dict):
            d = item
        else:
            continue
        day = str(d.get("date") or "")[:10]
        try:
            n = int(round(float(d.get("steps") or 0)))
        except (TypeError, ValueError):
            continue
        if not day or n < 0:
            continue
        out.append({"date": day, "steps": n, "source": str(d.get("source") or "")})
    out.sort(key=lambda r: r["date"])
    return out


def _sum_steps(rows: List[dict], start: date, end: date) -> int:
    total = 0
    for row in rows:
        d = _as_date(row.get("date"))
        if d is None or d < start or d > end:
            continue
        total += int(row.get("steps") or 0)
    return total


def _position_view(store: dict) -> dict:
    pos = store.get("position") if isinstance(store.get("position"), dict) else {}
    sats = int(pos.get("btc_sats") or 0)
    cash = float(pos.get("cash_usd") or 0)
    mark = pos.get("btc_usd_mark")
    try:
        mark_f = float(mark) if mark is not None else None
    except (TypeError, ValueError):
        mark_f = None
    if mark_f is not None and mark_f <= 0:
        mark_f = None
    btc = round(sats / SATS_PER_BTC, 8) if sats else 0.0
    btc_usd = round(btc * mark_f, 2) if mark_f is not None else None
    total = round(cash + (btc_usd or 0.0), 2) if mark_f is not None else None
    entered = bool(sats or cash or pos.get("as_of"))
    return {
        "btc_sats": sats,
        "btc": btc,
        "cash_usd": round(cash, 2),
        "btc_usd_mark": mark_f,
        "btc_usd": btc_usd,
        "total_usd": total,
        "as_of": str(pos.get("as_of") or ""),
        "entered": entered,
        "unmarked_btc": bool(sats and mark_f is None),
    }


def _contribution_pace(store: dict, as_of: date) -> dict:
    year = str(as_of.year)
    limits = irs_limits_for(store, year)
    limit = contribution_limit_usd(store, year)
    ytd = round(
        sum(
            float(r.get("amount_usd") or 0)
            for r in (store.get("contributions") or [])
            if str(r.get("date") or "").startswith(year)
        ),
        2,
    )
    progress = _year_progress(as_of)
    if not limits or limit is None:
        return {
            "year": year,
            "status": "limits_missing",
            "limit_usd": None,
            "ytd_usd": ytd,
            "remaining_usd": None,
            "expected_usd": None,
            "ahead_usd": None,
            "pct_of_limit": None,
            "pace_pct": None,
            "coverage": store.get("coverage"),
            "catch_up_55": bool(store.get("catch_up_55")),
            "limits": None,
            "message": (
                f"Add IRS HSA limits for {year} — limits are year-keyed and "
                "never borrowed from another year."
            ),
            **progress,
        }
    expected = round(limit * progress["elapsed_frac"], 2)
    remaining = round(max(0.0, limit - ytd), 2)
    ahead = round(ytd - expected, 2)
    pct = round(100.0 * ytd / limit, 1) if limit else None
    pace_pct = round(100.0 * ytd / expected, 1) if expected else None
    return {
        "year": year,
        "status": "ok",
        "limit_usd": limit,
        "ytd_usd": ytd,
        "remaining_usd": remaining,
        "expected_usd": expected,
        "ahead_usd": ahead,
        "pct_of_limit": pct,
        "pace_pct": pace_pct,
        "coverage": store.get("coverage"),
        "catch_up_55": bool(store.get("catch_up_55")),
        "limits": limits,
        "message": "",
        **progress,
    }


def _shoebox_view(store: dict) -> dict:
    rows = list(store.get("shoebox") or [])
    total = round(sum(float(r.get("amount_usd") or 0) for r in rows), 2)
    by_receipt = {"on_file": 0.0, "pending": 0.0, "missing": 0.0}
    for r in rows:
        st = str(r.get("receipt") or "missing")
        if st not in by_receipt:
            st = "missing"
        by_receipt[st] = round(by_receipt[st] + float(r.get("amount_usd") or 0), 2)
    return {
        "entries": rows,
        "count": len(rows),
        "total_usd": total,
        "on_file_usd": by_receipt["on_file"],
        "pending_usd": by_receipt["pending"],
        "missing_usd": by_receipt["missing"],
        "note": (
            "Qualified medical expenses paid out of pocket. Receipts in the "
            "vault are future tax-free HSA withdrawal capacity (no time limit)."
        ),
    }


def _spend_view(store: dict, year: str) -> dict:
    rows = [
        r
        for r in (store.get("medical_spend") or [])
        if str(r.get("date") or "").startswith(year)
    ]
    ytd = round(sum(float(r.get("amount_usd") or 0) for r in rows), 2)
    toward = round(
        sum(
            float(r.get("amount_usd") or 0)
            for r in rows
            if r.get("counts_toward_deductible")
        ),
        2,
    )
    deductible = store.get("hdhp_deductible_usd")
    try:
        ded_f = float(deductible) if deductible is not None else None
    except (TypeError, ValueError):
        ded_f = None
    remaining = (
        round(max(0.0, ded_f - toward), 2) if ded_f is not None else None
    )
    pct = round(100.0 * toward / ded_f, 1) if ded_f else None
    return {
        "year": year,
        "entries": rows,
        "ytd_usd": ytd,
        "toward_deductible_usd": toward,
        "hdhp_deductible_usd": ded_f,
        "deductible_remaining_usd": remaining,
        "pct_of_deductible": pct,
    }


def _sats_for_steps(
    store: dict,
    step_rows: List[dict],
    as_of: date,
) -> dict:
    today = sum(
        int(r.get("steps") or 0) for r in step_rows if r.get("date") == as_of.isoformat()
    )
    d7 = _sum_steps(step_rows, as_of - timedelta(days=6), as_of)
    d30 = _sum_steps(step_rows, as_of - timedelta(days=29), as_of)
    rewards = list(store.get("sats_rewards") or [])
    sats_total = sum(int(r.get("sats") or 0) for r in rewards)
    sats_ytd = sum(
        int(r.get("sats") or 0)
        for r in rewards
        if str(r.get("date") or "").startswith(str(as_of.year))
    )
    challenges = []
    for ch in store.get("challenges") or []:
        start = _as_date(ch.get("start")) or as_of.replace(month=1, day=1)
        end = _as_date(ch.get("end")) or as_of
        walked = _sum_steps(step_rows, start, end)
        goal = int(ch.get("goal_steps") or 0)
        pct = round(100.0 * walked / goal, 1) if goal else None
        challenges.append(
            {
                **ch,
                "steps_in_window": walked,
                "pct": pct,
                "complete": bool(goal and walked >= goal),
            }
        )
    pending = bool(not step_rows)
    return {
        "today_steps": today,
        "steps_7d": d7,
        "steps_30d": d30,
        "days_with_steps": len(step_rows),
        "pending_steps": pending,
        "sats_earned_total": sats_total,
        "sats_earned_ytd": sats_ytd,
        "rewards": rewards,
        "challenges": challenges,
        "note": (
            "SOUND Move to Earn is campaign-based (not a standing sats/step rate). "
            "Log earned sats; FitDash walks the same Google Health step series "
            "as the rest of the dashboard."
        ),
    }


def build_hsa_view(
    store: Optional[dict] = None,
    *,
    steps: Optional[Iterable[Any]] = None,
    as_of: Optional[str] = None,
    user_id: str = "",
) -> dict:
    raw = store if store is not None else load_hsa(user_id)
    data = normalize_store(raw)
    day = _as_date(as_of) or _as_date(local_today_iso()) or date.today()
    eligibility = data.get("eligibility") or "unknown"
    eligible = eligibility == "eligible"
    step_rows = _step_rows(steps or [])
    view = {
        "ok": True,
        "provider": data.get("provider") or "SOUND HSA",
        "eligibility": eligibility,
        "eligible": eligible,
        "coverage": data.get("coverage"),
        "catch_up_55": bool(data.get("catch_up_55")),
        "hdhp_copy": HDHP_COPY,
        "api": data.get("api") or {"available": False, "note": ""},
        "fees": data.get("fees") or {},
        "irs_limits": data.get("irs_limits") or {},
        "as_of": day.isoformat(),
        "storage": data.get("storage") or "empty",
        "updated_at": data.get("updated_at") or "",
        "sats_for_steps": _sats_for_steps(data, step_rows, day),
        "csv_columns": [
            "kind",
            "date",
            "amount_usd",
            "sats",
            "category",
            "merchant",
            "receipt",
            "counts_toward_deductible",
            "steps",
            "challenge",
            "notes",
        ],
    }
    view["store"] = {
        "eligibility": data["eligibility"],
        "coverage": data["coverage"],
        "catch_up_55": data["catch_up_55"],
        "hdhp_deductible_usd": data["hdhp_deductible_usd"],
        "position": data["position"],
        "contributions": data["contributions"] if eligible else [],
        "shoebox": data["shoebox"] if eligible else [],
        "medical_spend": data["medical_spend"] if eligible else [],
        "sats_rewards": data["sats_rewards"],
        "challenges": data["challenges"],
        "irs_limits": data["irs_limits"],
        "fees": data["fees"],
    }
    if not eligible:
        view["position"] = None
        view["contribution_pace"] = None
        view["shoebox"] = None
        view["medical_spend"] = None
        view["empty_reason"] = (
            "ineligible"
            if eligibility == "ineligible"
            else "eligibility_unknown"
        )
        view["message"] = HDHP_COPY
        return view
    view["position"] = _position_view(data)
    view["contribution_pace"] = _contribution_pace(data, day)
    view["shoebox"] = _shoebox_view(data)
    view["medical_spend"] = _spend_view(data, str(day.year))
    view["empty_reason"] = ""
    view["message"] = ""
    return view


def attach_hsa(
    payload: dict,
    *,
    user_id: Optional[str] = None,
    health: Any = None,
    as_of: Optional[str] = None,
) -> dict:
    steps = []
    if health is not None:
        steps = getattr(health, "steps", None) or []
        if not steps and isinstance(health, dict):
            steps = health.get("steps") or []
    if not steps and isinstance(payload.get("health"), dict):
        steps = payload["health"].get("steps") or []
    try:
        view = build_hsa_view(
            steps=steps,
            as_of=as_of or str(payload.get("meta", {}).get("local_today") or ""),
            user_id=str(user_id or ""),
        )
    except Exception as exc:  # noqa: BLE001
        view = {
            "ok": False,
            "error": str(exc) or type(exc).__name__,
            "eligibility": "unknown",
            "eligible": False,
            "hdhp_copy": HDHP_COPY,
            "position": None,
            "contribution_pace": None,
            "shoebox": None,
            "medical_spend": None,
            "empty_reason": "error",
            "message": HDHP_COPY,
        }
    payload["hsa"] = view
    return payload
