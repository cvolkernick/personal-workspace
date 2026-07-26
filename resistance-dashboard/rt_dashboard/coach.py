"""Coach layer: adherence, weekly review, today board, brief text."""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence

from .models import (
    FoodLogEntry,
    HealthSnapshot,
    HydrationDay,
    NutritionDay,
    RecoveryStatus,
    Session,
    SleepSample,
    WeightSample,
)
from .labs_store import labs_summary_for_coach
from .test_noise import filter_sessions
from .timeutil import local_today_iso

# Rough daily intake targets used only for micro coaching (not medical RDAs).
_MICRO_DAILY_HINTS_G = {
    "DIETARY_FIBER": 25.0,
    "SODIUM": 2.3,  # grams (~2300 mg)
    "SUGAR": 50.0,
    "POTASSIUM": 3.4,
    "CALCIUM": 1.0,
    "MAGNESIUM": 0.4,
    "IRON": 0.018,
    "ZINC": 0.011,
    "VITAMIN_C": 0.09,
    "VITAMIN_D": 0.00002,  # ~20 mcg as grams
    "SATURATED_FAT": 20.0,
}


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
        # Unlogged nights = 0h (sleep debt), always scored in the 7d window
        sh = by_s.get(d)
        if sh is None:
            sh = 0.0
            row["sleep_implied_zero"] = True
        sleep_days += 1
        ok_s = float(sh) >= sleep_goal_h
        if ok_s:
            sleep_hits += 1
        row["sleep_h"] = round(float(sh), 2)
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

    # Calendar mean over the week (missing nights = 0 via expand when present;
    # also fill gaps here so weekly review is debt-aware).
    from .sleep_series import expand_sleep_calendar

    week_filled = expand_sleep_calendar(
        sleep, as_of=day, window_days=7
    )
    sleep_vals = [float(s.sleep_hours) for s in week_filled]
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

    bullets.append(
        "Volume model: ≈4–8 hard sets per major muscle/week (compound overlap counts); "
        "10–20+/muscle is usually unnecessary. "
        "Ask: “auto focus” to prioritize lagging muscles from your logs."
    )

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


def build_food_commentary(
    *,
    food_logs: Sequence[FoodLogEntry],
    nutrition: Sequence[NutritionDay],
    targets: dict,
    consumed: dict,
    adherence: dict,
    labs: Optional[dict] = None,
    as_of: Optional[str] = None,
    window_days: int = 7,
) -> dict:
    """Rolling coach assessment of logged foods, macros, and micronutrients.

    Deterministic (no model call). Surfaces what is working and what to improve.
    Labs (bi-annual/quarterly) are optional long-horizon context.
    """
    day = as_of or local_today_iso()
    window = set(_dates_back(day, window_days))
    logs = [f for f in food_logs if f.date in window]
    today_logs = [f for f in food_logs if f.date == day]

    working: List[str] = []
    improve: List[str] = []
    notes: List[str] = []

    # --- Macro adherence (7d) ---
    p = adherence.get("protein") or {}
    c = adherence.get("calories") or {}
    if p.get("pct") is not None:
        if p["pct"] >= 70:
            working.append(
                f"Protein hit rate is solid at {p['pct']}% of logged days "
                f"({p.get('hits')}/{p.get('days_logged')})."
            )
        else:
            improve.append(
                f"Protein only hit target on {p['pct']}% of food-log days — "
                f"prioritize a high-protein meal earlier."
            )
    if c.get("pct") is not None:
        if c["pct"] >= 60:
            working.append(f"Calorie control is on track ({c['pct']}% of days in range).")
        elif c.get("days_logged", 0) >= 3:
            improve.append(
                f"Calories were in-range only {c['pct']}% of days — check evening snacks "
                f"and liquid calories."
            )

    # --- Today's remaining ---
    tgt_p = float(targets.get("protein_g") or 0)
    tgt_cals = float(targets.get("calories") or 0)
    rem_p = max(0.0, tgt_p - float(consumed.get("protein_g") or 0))
    rem_c = max(0.0, tgt_cals - float(consumed.get("calories") or 0))
    if today_logs:
        notes.append(
            f"Today: {len(today_logs)} food log{'s' if len(today_logs) != 1 else ''} · "
            f"{float(consumed.get('calories') or 0):.0f} kcal · "
            f"P {float(consumed.get('protein_g') or 0):.0f} g so far."
        )
        if rem_p > 40:
            improve.append(
                f"Still need ~{rem_p:.0f} g protein today ({rem_c:.0f} kcal left) — "
                f"lean meat, dairy, or whey fits the remaining budget."
            )
        elif rem_p <= 15 and rem_c > 200:
            improve.append(
                f"Protein is nearly done; fill remaining ~{rem_c:.0f} kcal with "
                f"fiber-heavy carbs/veg rather than more dense protein."
            )
        elif rem_p <= 15 and rem_c <= 150:
            working.append("Today’s macros are essentially closed out — nice logging discipline.")
    elif tgt_p > 0:
        notes.append("No meal-level food logs for today yet (daily rollup may still have totals).")

    # --- Food frequency / quality signals ---
    name_counts: Dict[str, int] = {}
    name_protein: Dict[str, float] = {}
    for f in logs:
        key = (f.name or "Unknown").strip()
        if not key:
            continue
        name_counts[key] = name_counts.get(key, 0) + 1
        name_protein[key] = name_protein.get(key, 0.0) + float(f.protein_g or 0)

    if name_counts:
        top = sorted(name_counts.items(), key=lambda x: (-x[1], x[0]))[:5]
        top_s = ", ".join(f"{n}×{c}" for n, c in top[:3])
        notes.append(f"Most logged foods ({window_days}d): {top_s}.")
        # High-protein staples
        staples = sorted(name_protein.items(), key=lambda x: -x[1])[:3]
        if staples and staples[0][1] >= 50:
            working.append(
                f"Protein staples showing up: {', '.join(n for n, _ in staples if _ > 0)}."
            )
        # Ultra-frequent single item (monotony / possible over-reliance)
        if top and top[0][1] >= 8:
            improve.append(
                f"“{top[0][0]}” logged {top[0][1]}× in {window_days}d — fine as a staple, "
                f"but rotate sides for micronutrient variety."
            )

    # Low protein density foods dominating
    dense_hits = 0
    sparse_hits = 0
    for f in logs:
        cal = float(f.calories or 0)
        prot = float(f.protein_g or 0)
        if cal < 80:
            continue
        dens = prot / cal
        if dens >= 0.08:  # ~32g protein / 400 kcal
            dense_hits += 1
        elif dens < 0.03 and cal >= 150:
            sparse_hits += 1
    if dense_hits >= 5 and dense_hits >= sparse_hits:
        working.append(
            f"Protein density looks good on {dense_hits} substantial logs in the window."
        )
    if sparse_hits >= 5 and sparse_hits > dense_hits:
        improve.append(
            f"{sparse_hits} higher-calorie / lower-protein logs — swap some for denser protein."
        )

    # --- Micronutrient aggregates (when present on logs) ---
    micro_sums: Dict[str, float] = {}
    micro_days: Dict[str, set] = {}
    for f in logs:
        for k, v in (f.nutrients or {}).items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            micro_sums[k] = micro_sums.get(k, 0.0) + fv
            micro_days.setdefault(k, set()).add(f.date)

    micro_avg: Dict[str, float] = {}
    for k, total in micro_sums.items():
        nd = max(1, len(micro_days.get(k) or []))
        micro_avg[k] = total / nd

    micro_notes: List[str] = []
    if micro_avg:
        # Fiber
        fiber = micro_avg.get("DIETARY_FIBER")
        if fiber is not None:
            if fiber >= 20:
                working.append(f"Fiber avg ~{fiber:.0f} g/day on logged days — good for satiety.")
            elif fiber < 15:
                improve.append(
                    f"Fiber avg only ~{fiber:.0f} g/day — add veg, berries, oats, or legumes."
                )
            micro_notes.append(f"fiber ~{fiber:.0f} g")
        sodium = micro_avg.get("SODIUM")
        if sodium is not None:
            # Google stores grams; if value looks like mg-scale (>20), treat as mg→g
            na = sodium if sodium < 20 else sodium / 1000.0
            if na > 3.0:
                improve.append(
                    f"Sodium running high (~{na:.1f} g/day avg) — watch sauces, deli, and packaged foods."
                )
            micro_notes.append(f"Na ~{na:.1f} g")
        sugar = micro_avg.get("SUGAR")
        if sugar is not None and sugar > 60:
            improve.append(f"Sugar avg ~{sugar:.0f} g/day — trim sweet drinks/snacks if cutting.")
        sat = micro_avg.get("SATURATED_FAT")
        if sat is not None and sat > 25:
            improve.append(f"Saturated fat avg ~{sat:.0f} g/day — leaner cuts help if lipids are a focus.")
        for key in ("POTASSIUM", "MAGNESIUM", "IRON", "CALCIUM", "VITAMIN_D", "VITAMIN_C"):
            if key not in micro_avg:
                continue
            target = _MICRO_DAILY_HINTS_G.get(key)
            if not target:
                continue
            avg = micro_avg[key]
            # Skip near-zero noisy micros
            if avg <= 0:
                continue
            if avg < target * 0.5:
                label = key.replace("_", " ").title()
                improve.append(
                    f"{label} looks light in food logs vs a rough daily target — "
                    f"confirm with variety or your latest labs."
                )

    # --- Labs (optional, slow-changing) ---
    lab_sum = labs_summary_for_coach(labs)
    if lab_sum.get("has_labs"):
        flags = lab_sum.get("flags") or []
        notes.append(
            f"Latest labs: {lab_sum.get('date')}"
            + (f" ({lab_sum.get('lab')})" if lab_sum.get("lab") else "")
            + f" · {lab_sum.get('marker_count')} markers."
        )
        for fl in flags[:4]:
            marker = str(fl.get("marker") or "").replace("_", " ")
            improve.append(
                f"Lab flag: {marker} = {fl.get('value')} ({fl.get('status')}; "
                f"ref {fl.get('ref_low')}–{fl.get('ref_high')}) — "
                f"diet may support this but retest with your clinician."
            )
        if not flags:
            working.append(
                f"Latest lab panel ({lab_sum.get('date')}) has no out-of-range flags "
                f"vs coach reference hints."
            )
    else:
        notes.append(
            "Labs: none on file yet — drop bi-annual results into fitness/data/labs.json "
            "when you have them."
        )

    if not logs and not (adherence.get("protein") or {}).get("days_logged"):
        improve.append(
            "No recent food logs — log meals in Fitbit/Google Health so coach feedback can go food-specific."
        )

    # Deduplicate while preserving order
    def _dedupe(items: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for x in items:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    working = _dedupe(working)[:6]
    improve = _dedupe(improve)[:8]
    notes = _dedupe(notes)[:6]

    # Markdown for UI
    lines = ["### Nutrition Coach"]
    if notes:
        lines.append("**Snapshot:** " + " ".join(notes))
    if working:
        lines.append("**Working well**")
        for w in working:
            lines.append(f"- {w}")
    if improve:
        lines.append("**Can improve**")
        for w in improve:
            lines.append(f"- {w}")
    if not working and not improve:
        lines.append("_Not enough food-log detail yet for a specific assessment._")

    # Top foods payload for UI list
    top_foods = [
        {"name": n, "count": c, "protein_g": round(name_protein.get(n, 0.0), 1)}
        for n, c in sorted(name_counts.items(), key=lambda x: (-x[1], x[0]))[:8]
    ]

    return {
        "as_of": day,
        "window_days": window_days,
        "log_count": len(logs),
        "today_log_count": len(today_logs),
        "working_well": working,
        "can_improve": improve,
        "notes": notes,
        "top_foods": top_foods,
        "micro_avg_g": {k: round(v, 4) for k, v in sorted(micro_avg.items())[:20]},
        "labs": lab_sum,
        "markdown": "\n".join(lines),
    }


def build_coach_brief(
    *,
    today: dict,
    weekly: dict,
    recovery: RecoveryStatus,
    food_commentary: Optional[dict] = None,
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
    fc = food_commentary or {}
    improve = fc.get("can_improve") or []
    working = fc.get("working_well") or []
    if improve:
        lines.append("**Nutrition Coach:** " + improve[0])
    elif working:
        lines.append("**Nutrition Coach:** " + working[0])
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
    labs: Optional[dict] = None,
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
    food_commentary = build_food_commentary(
        food_logs=health.food_logs or [],
        nutrition=health.nutrition,
        targets=targets or {},
        consumed=consumed or {},
        adherence=adherence,
        labs=labs,
        as_of=day,
    )
    brief = build_coach_brief(
        today=today,
        weekly=weekly,
        recovery=recovery,
        food_commentary=food_commentary,
    )
    return {
        "today": today,
        "adherence_7d": adherence,
        "weekly_review": weekly,
        "food_commentary": food_commentary,
        "brief": brief,
    }
