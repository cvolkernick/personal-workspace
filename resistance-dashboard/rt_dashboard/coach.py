"""Coach layer: adherence, weekly review, today board, brief text."""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence

from .models import (
    HealthSnapshot,
    HydrationDay,
    NutritionDay,
    RecoveryStatus,
    Session,
    SleepSample,
    WeightSample,
)
from .test_noise import filter_sessions
from .timeutil import local_today_iso


def _parse(d: str) -> Optional[datetime]:
    try:
        return datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        return None


def _dates_back(as_of: str, n: int) -> List[str]:
    end = _parse(as_of)
    if not end:
        return []
    return [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n - 1, -1, -1)]


def _nutrition_by_date(nutrition: Sequence[NutritionDay]) -> Dict[str, NutritionDay]:
    return {n.date: n for n in nutrition if n.date}


def _sleep_by_date(sleep: Sequence[SleepSample]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for s in sleep:
        out[s.date] = float(s.sleep_hours)
    return out


def _hydration_by_date(hydration: Sequence[HydrationDay]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for h in hydration:
        out[h.date] = float(h.water_ml)
    return out


def compute_adherence_7d(
    *,
    targets: dict,
    nutrition: Sequence[NutritionDay],
    sleep: Sequence[SleepSample],
    hydration: Sequence[HydrationDay],
    as_of: Optional[str] = None,
    sleep_goal_h: float = 7.0,
    hydration_goal_ml: float = 2500.0,
    protein_tol: float = 0.85,
    calorie_tol: float = 0.90,
) -> dict:
    """Rolling 7d adherence vs targets / simple sleep & water goals."""
    day = as_of or local_today_iso()
    window = _dates_back(day, 7)
    by_n = _nutrition_by_date(nutrition)
    by_s = _sleep_by_date(sleep)
    by_h = _hydration_by_date(hydration)

    tgt_p = float(targets.get("protein_g") or 0)
    tgt_cals = float(targets.get("calories") or 0)

    protein_hits = 0
    cal_hits = 0
    sleep_hits = 0
    water_hits = 0
    protein_days = 0
    cal_days = 0
    sleep_days = 0
    water_days = 0
    daily: List[dict] = []

    for d in window:
        n = by_n.get(d)
        row: Dict[str, Any] = {"date": d}
        if n and (n.protein_g is not None or n.calories is not None):
            if n.protein_g is not None and tgt_p > 0:
                protein_days += 1
                ok_p = float(n.protein_g) >= tgt_p * protein_tol
                if ok_p:
                    protein_hits += 1
                row["protein_g"] = n.protein_g
                row["protein_ok"] = ok_p
            if n.calories is not None and tgt_cals > 0:
                cal_days += 1
                # Within ±15% or above floor of tol
                c = float(n.calories)
                ok_c = (tgt_cals * calorie_tol) <= c <= (tgt_cals * 1.15)
                if ok_c:
                    cal_hits += 1
                row["calories"] = c
                row["calories_ok"] = ok_c
        sh = by_s.get(d)
        if sh is not None:
            sleep_days += 1
            ok_s = sh >= sleep_goal_h
            if ok_s:
                sleep_hits += 1
            row["sleep_h"] = round(sh, 2)
            row["sleep_ok"] = ok_s
        wh = by_h.get(d)
        if wh is not None:
            water_days += 1
            ok_w = wh >= hydration_goal_ml
            if ok_w:
                water_hits += 1
            row["water_ml"] = round(wh, 0)
            row["water_ok"] = ok_w
        daily.append(row)

    def pct(hits: int, total: int) -> Optional[float]:
        if total <= 0:
            return None
        return round(100.0 * hits / total, 1)

    return {
        "as_of": day,
        "window_days": 7,
        "protein": {
            "hits": protein_hits,
            "days_logged": protein_days,
            "pct": pct(protein_hits, protein_days),
            "target_g": tgt_p,
            "tolerance": protein_tol,
        },
        "calories": {
            "hits": cal_hits,
            "days_logged": cal_days,
            "pct": pct(cal_hits, cal_days),
            "target": tgt_cals,
        },
        "sleep": {
            "hits": sleep_hits,
            "days_logged": sleep_days,
            "pct": pct(sleep_hits, sleep_days),
            "goal_h": sleep_goal_h,
        },
        "hydration": {
            "hits": water_hits,
            "days_logged": water_days,
            "pct": pct(water_hits, water_days),
            "goal_ml": hydration_goal_ml,
        },
        "daily": daily,
    }


def compute_weekly_review(
    *,
    sessions: Sequence[Session],
    recovery: RecoveryStatus,
    weight: Sequence[WeightSample],
    sleep: Sequence[SleepSample],
    nutrition: Sequence[NutritionDay],
    targets: dict,
    adherence: dict,
    as_of: Optional[str] = None,
) -> dict:
    day = as_of or local_today_iso()
    end = _parse(day)
    if not end:
        return {"as_of": day, "bullets": ["No valid date for review."]}
    start = end - timedelta(days=6)
    clean = filter_sessions(list(sessions))
    week_sess = []
    vol = 0.0
    for s in clean:
        d = _parse(s.date)
        if d and start <= d <= end:
            week_sess.append(s)
            vol += float(s.volume)

    prs = []
    for s in week_sess:
        for ex in s.exercises:
            if ex.is_pr:
                prs.append(f"{ex.name} ({s.date})")

    sleep_vals = []
    for s in sleep:
        d = _parse(s.date)
        if d and start <= d <= end:
            sleep_vals.append(float(s.sleep_hours))
    avg_sleep = round(mean(sleep_vals), 2) if sleep_vals else None

    w_sorted = sorted(weight, key=lambda w: w.date)
    w_delta = None
    if len(w_sorted) >= 2:
        recent = [w for w in w_sorted if (d := _parse(w.date)) and start <= d <= end]
        if len(recent) >= 2:
            w_delta = round(recent[-1].weight_lbs - recent[0].weight_lbs, 2)
        elif len(w_sorted) >= 2:
            w_delta = round(w_sorted[-1].weight_lbs - w_sorted[-2].weight_lbs, 2)

    p_pct = (adherence.get("protein") or {}).get("pct")
    s_pct = (adherence.get("sleep") or {}).get("pct")
    h_pct = (adherence.get("hydration") or {}).get("pct")

    bullets: List[str] = []
    bullets.append(
        f"Training: {len(week_sess)} sessions · {vol:,.0f} lb volume"
        + (f" · PRs: {', '.join(prs[:4])}" if prs else " · no auto-PRs logged")
    )
    if avg_sleep is not None:
        bullets.append(
            f"Sleep: avg {avg_sleep} h over {len(sleep_vals)} nights"
            + (f" · goal hit {s_pct}% of logged nights" if s_pct is not None else "")
        )
    else:
        bullets.append("Sleep: no nights logged in the last 7 days")
    if p_pct is not None:
        bullets.append(
            f"Protein: hit target (≥{int((adherence.get('protein') or {}).get('tolerance', 0.85)*100)}%) "
            f"on {p_pct}% of food-log days ({(adherence.get('protein') or {}).get('hits')}/"
            f"{(adherence.get('protein') or {}).get('days_logged')})"
        )
    else:
        bullets.append("Protein: no food logs with protein in the last 7 days")
    if h_pct is not None:
        bullets.append(
            f"Hydration: hit ≥{(adherence.get('hydration') or {}).get('goal_ml')} ml on {h_pct}% of logged days"
        )
    if w_delta is not None:
        sign = "+" if w_delta >= 0 else ""
        bullets.append(f"Weight: {sign}{w_delta} lb over recent weigh-ins")
    bullets.append(
        f"Recovery now: {recovery.label} ({recovery.score:.0f}/100) — "
        + (recovery.reasons[0] if recovery.reasons else "no detail")
    )

    # Coach nudge
    if recovery.score < 40:
        bullets.append("Focus: prioritize sleep and a lighter session or rest tomorrow.")
    elif p_pct is not None and p_pct < 50:
        bullets.append("Focus: protein is the biggest nutrition gap — load inventory staples first.")
    elif len(week_sess) < 3:
        bullets.append("Focus: consistency — aim for your next planned PPL day soon.")
    else:
        bullets.append("Focus: keep the streak — execute today’s plan and hit protein remaining.")

    return {
        "as_of": day,
        "sessions": len(week_sess),
        "volume": round(vol, 1),
        "prs": prs,
        "avg_sleep_h": avg_sleep,
        "weight_delta_lb": w_delta,
        "bullets": bullets,
    }


def build_today_board(
    *,
    as_of: str,
    recovery: RecoveryStatus,
    workout_plan: dict,
    meal_plan: dict,
    consumed: dict,
    targets: dict,
    adherence: dict,
) -> dict:
    wp = workout_plan or {}
    mp = meal_plan or {}
    rem = mp.get("remaining_before_plan") or {}
    if not rem and targets:
        rem = {
            k: max(0.0, float(targets.get(k) or 0) - float(consumed.get(k) or 0))
            for k in ("calories", "protein_g", "carbs_g", "fat_g")
        }
    exercises = []
    for ex in wp.get("exercises") or []:
        if not isinstance(ex, dict):
            continue
        rx = ex.get("prescription") or {}
        exercises.append(
            {
                "name": ex.get("name"),
                "weight_lbs": rx.get("weight_lbs"),
                "sets": rx.get("sets"),
                "reps": rx.get("reps"),
                "primary_muscles": ex.get("primary_muscles"),
            }
        )
    rec_label = "rest" if wp.get("is_rest_day") else "train"
    if recovery.score < 40:
        rec_label = "rest"
    elif recovery.score < 55 and not wp.get("is_rest_day"):
        rec_label = "easy"

    return {
        "date": as_of,
        "recommendation": rec_label,
        "recovery": {
            "label": recovery.label,
            "score": recovery.score,
            "reasons": recovery.reasons[:4],
        },
        "workout": {
            "session_type": wp.get("session_type"),
            "is_rest_day": bool(wp.get("is_rest_day")),
            "message": wp.get("message"),
            "exercises": exercises,
        },
        "nutrition": {
            "consumed": consumed,
            "targets": targets,
            "remaining": rem,
            "meal_plan_message": mp.get("message"),
        },
        "adherence_7d": {
            "protein_pct": (adherence.get("protein") or {}).get("pct"),
            "sleep_pct": (adherence.get("sleep") or {}).get("pct"),
            "hydration_pct": (adherence.get("hydration") or {}).get("pct"),
            "calories_pct": (adherence.get("calories") or {}).get("pct"),
        },
    }


def build_coach_brief(
    *,
    today: dict,
    weekly: dict,
    recovery: RecoveryStatus,
) -> dict:
    """Deterministic morning-style brief (no model call)."""
    lines: List[str] = []
    rec = today.get("recommendation") or "train"
    wo = today.get("workout") or {}
    nut = today.get("nutrition") or {}
    rem = nut.get("remaining") or {}
    adh = today.get("adherence_7d") or {}

    if rec == "rest":
        lines.append(
            f"**Today: rest / recovery.** Status **{recovery.label}** ({recovery.score:.0f}/100)."
        )
    elif rec == "easy":
        lines.append(
            f"**Today: easy day.** Recovery **{recovery.label}** ({recovery.score:.0f}/100) — keep intensity moderate."
        )
    else:
        st = (wo.get("session_type") or "session").upper()
        n_ex = len(wo.get("exercises") or [])
        lines.append(
            f"**Today: {st}** ({n_ex} lifts). Recovery **{recovery.label}** ({recovery.score:.0f}/100)."
        )

    if rem:
        lines.append(
            f"**Nutrition remaining:** {rem.get('calories', 0):.0f} kcal · "
            f"P {rem.get('protein_g', 0):.0f} g · C {rem.get('carbs_g', 0):.0f} g · F {rem.get('fat_g', 0):.0f} g."
        )
    bits = []
    if adh.get("protein_pct") is not None:
        bits.append(f"protein {adh['protein_pct']}%")
    if adh.get("sleep_pct") is not None:
        bits.append(f"sleep {adh['sleep_pct']}%")
    if adh.get("hydration_pct") is not None:
        bits.append(f"water {adh['hydration_pct']}%")
    if bits:
        lines.append("**7-day adherence:** " + " · ".join(bits) + ".")

    bullets = (weekly or {}).get("bullets") or []
    if bullets:
        lines.append("**Week so far:** " + bullets[0])
        if len(bullets) > 1:
            lines.append(bullets[-1] if "Focus:" in bullets[-1] else bullets[1])

    lines.append(
        "Use **Log this plan** when you train, and Ask Grok for deeper questions "
        "(or try: `set stock chicken-breast off`, `refresh meal plan`)."
    )
    return {
        "title": "Coach brief",
        "markdown": "\n\n".join(lines),
        "recommendation": rec,
    }


def build_coach_payload(
    *,
    health: HealthSnapshot,
    sessions: Sequence[Session],
    recovery: RecoveryStatus,
    targets: dict,
    consumed: dict,
    meal_plan: dict,
    workout_plan: dict,
    as_of: Optional[str] = None,
) -> dict:
    day = as_of or local_today_iso()
    adherence = compute_adherence_7d(
        targets=targets or {},
        nutrition=health.nutrition,
        sleep=health.sleep,
        hydration=health.hydration,
        as_of=day,
    )
    weekly = compute_weekly_review(
        sessions=sessions,
        recovery=recovery,
        weight=health.weight,
        sleep=health.sleep,
        nutrition=health.nutrition,
        targets=targets or {},
        adherence=adherence,
        as_of=day,
    )
    today = build_today_board(
        as_of=day,
        recovery=recovery,
        workout_plan=workout_plan or {},
        meal_plan=meal_plan or {},
        consumed=consumed or {},
        targets=targets or {},
        adherence=adherence,
    )
    brief = build_coach_brief(today=today, weekly=weekly, recovery=recovery)
    return {
        "today": today,
        "adherence_7d": adherence,
        "weekly_review": weekly,
        "brief": brief,
    }
