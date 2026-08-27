"""Deterministic coach recommendations for calorie/macro targets.

Recommend on every dashboard load. Never write ``targets.json``.
Applied values change only on explicit apply (Kitchen form, ``set targets``,
or ``apply coach targets``). Formula v1: ``fitness/nutrition/COACH_TARGETS.md``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence

from .models import CaloriesBurnedDay, HealthSnapshot, NutritionDay, RecoveryStatus, WeightSample
from .timeutil import local_today_iso

PHASES = ("cut", "maintain", "slow_bulk")
TDEE_WINDOW_DAYS = 14
TDEE_MIN_DAYS = 5
WEIGHT_WINDOW_DAYS = 14


def _parse(d: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _dates_back(as_of: str, n: int) -> List[str]:
    end = _parse(as_of)
    if not end:
        return []
    return [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n - 1, -1, -1)]


def round_kcal(n: float) -> int:
    return int(round(float(n) / 50.0) * 50)


def round_g(n: float) -> int:
    return int(round(float(n) / 5.0) * 5)


def clamp(n: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, n))


def _present_mean(rows: Sequence[Any], *, dates: Sequence[str], getter) -> tuple[Optional[float], int]:
    vals: List[float] = []
    for row in rows or []:
        day = str(getattr(row, "date", "") or "")[:10]
        if day not in dates:
            continue
        raw = getter(row)
        if raw is None or raw == "":
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v != v:  # NaN
            continue
        vals.append(v)
    if not vals:
        return None, 0
    return float(mean(vals)), len(vals)


def _latest_weight(weights: Sequence[WeightSample], *, as_of: str, window: int) -> Optional[WeightSample]:
    allowed = set(_dates_back(as_of, window))
    in_win = [
        w
        for w in (weights or [])
        if w
        and str(w.date or "")[:10] in allowed
        and float(getattr(w, "weight_lbs", 0) or 0) > 0
    ]
    if not in_win:
        return None
    in_win.sort(key=lambda w: str(w.date)[:10])
    return in_win[-1]


def _weight_trend(
    weights: Sequence[WeightSample], *, as_of: str, window: int
) -> tuple[Optional[float], Optional[float], Optional[int]]:
    """Return (delta_lb, weekly_lb, span_days) over first→last weigh-in in window."""
    start = _dates_back(as_of, window)
    if not start:
        return None, None, None
    lo, hi = start[0], start[-1]
    in_win = [
        w
        for w in (weights or [])
        if w
        and lo <= str(w.date or "")[:10] <= hi
        and float(getattr(w, "weight_lbs", 0) or 0) > 0
    ]
    in_win.sort(key=lambda w: str(w.date)[:10])
    if len(in_win) < 2:
        return None, None, None
    first, last = in_win[0], in_win[-1]
    span = (_parse(str(last.date)[:10]) - _parse(str(first.date)[:10])).days
    if span < 5:
        return None, None, None
    delta = float(last.weight_lbs) - float(first.weight_lbs)
    weekly = delta * 7.0 / span
    return delta, weekly, span


def infer_phase(targets: dict, current_lb: Optional[float]) -> str:
    raw = str((targets or {}).get("phase") or "").strip().lower().replace("-", "_")
    if raw in ("bulk", "gain"):
        raw = "slow_bulk"
    if raw in PHASES:
        return raw
    notes = str((targets or {}).get("notes") or "").lower()
    if any(k in notes for k in ("cut", "deficit", "loss", "lean")):
        return "cut"
    if any(k in notes for k in ("bulk", "surplus", "gain", "mass")):
        return "slow_bulk"
    goal = (targets or {}).get("weight_goal_lbs")
    try:
        goal_f = float(goal) if goal not in (None, "") else None
    except (TypeError, ValueError):
        goal_f = None
    if current_lb is not None and goal_f is not None:
        gap = current_lb - goal_f
        if gap >= 3:
            return "cut"
        if gap <= -3:
            return "slow_bulk"
        return "maintain"
    return "maintain"


def _macros_for(
    *,
    calories: int,
    current_lb: Optional[float],
    phase: str,
) -> Dict[str, int]:
    if current_lb and current_lb > 0:
        p_per = 1.0 if phase == "cut" else 0.9
        protein = int(clamp(round_g(p_per * current_lb), 160, 230))
        fat = int(clamp(round_g(0.35 * current_lb), 45, 80))
    else:
        protein, fat = 160, 45
    remainder = calories - protein * 4 - fat * 9
    carbs = remainder / 4.0
    if carbs < 100:
        # Cut fat toward 45 before dropping carbs under 100.
        need = (100 * 4) - remainder
        fat_cut = min(max(0.0, fat - 45), max(0.0, need / 9.0))
        fat = int(clamp(round_g(fat - fat_cut), 45, 80))
        remainder = calories - protein * 4 - fat * 9
        carbs = remainder / 4.0
    carbs = int(clamp(round_g(carbs), 100, 350))
    return {"protein_g": protein, "carbs_g": carbs, "fat_g": fat}


def recommend_nutrition_targets(
    *,
    health: Optional[HealthSnapshot] = None,
    targets: Optional[dict] = None,
    recovery: Optional[RecoveryStatus] = None,
    adherence_7d: Optional[dict] = None,
    as_of: Optional[str] = None,
) -> dict:
    """Pure recommendation. Does not write files."""
    day = as_of or local_today_iso()
    applied_in = targets or {}
    applied = {
        "calories": round(float(applied_in.get("calories") or 2100)),
        "protein_g": round(float(applied_in.get("protein_g") or 210)),
        "carbs_g": round(float(applied_in.get("carbs_g") or 180)),
        "fat_g": round(float(applied_in.get("fat_g") or 55)),
    }
    snap = health or HealthSnapshot()
    reasons: List[str] = []
    dates14 = _dates_back(day, TDEE_WINDOW_DAYS)

    latest = _latest_weight(snap.weight, as_of=day, window=WEIGHT_WINDOW_DAYS)
    current_lb = float(latest.weight_lbs) if latest else None
    if current_lb:
        reasons.append(f"scale {current_lb:.1f} lb ({latest.date})")
    else:
        reasons.append("no recent weigh-in — calorie change abstains")

    goal = applied_in.get("weight_goal_lbs")
    try:
        goal_lb = float(goal) if goal not in (None, "") else None
    except (TypeError, ValueError):
        goal_lb = None

    phase = infer_phase(applied_in, current_lb)
    tdee, tdee_days = _present_mean(
        snap.calories_burned,
        dates=set(dates14),
        getter=lambda r: getattr(r, "calories", None),
    )
    intake_mean, intake_days = _present_mean(
        snap.nutrition,
        dates=set(dates14),
        getter=lambda r: getattr(r, "calories", None),
    )

    tdee_thin = tdee is None or tdee_days < TDEE_MIN_DAYS
    no_weigh_in = current_lb is None
    # No recent weigh-in: calorie rec stays applied (do not invent gap_lb).
    calorie_abstain = tdee_thin or no_weigh_in
    tdee_hat = None
    if tdee_thin:
        reasons.append(
            f"TDEE abstains — need ≥{TDEE_MIN_DAYS} present burned days in {TDEE_WINDOW_DAYS}d "
            f"(have {tdee_days})"
        )
    else:
        tdee_hat = round_kcal(tdee)
        reasons.append(
            f"{TDEE_WINDOW_DAYS}d mean wearable burn {tdee_hat} kcal "
            f"({tdee_days} present days; wearable is an estimate)"
        )
        if intake_mean is not None and intake_days:
            reasons.append(
                f"logged {TDEE_WINDOW_DAYS}d mean {round_kcal(intake_mean)} kcal "
                f"({intake_days} present days) — cross-check only"
            )

    rec_cal = int(applied["calories"])
    if calorie_abstain:
        rec_cal = int(applied["calories"])
    else:
        rec_cal = tdee_hat
        if phase == "cut":
            gap_lb = (current_lb - goal_lb) if (current_lb and goal_lb) else 10.0
            gap_lb = max(0.0, gap_lb)
            deficit = int(clamp(gap_lb * 15.0, 250, 500))
            rec_cal = tdee_hat - deficit
            reasons.append(f"phase=cut; deficit {deficit} kcal (clamp gap×15 to 250–500)")
            _delta, weekly, _span = _weight_trend(
                snap.weight, as_of=day, window=WEIGHT_WINDOW_DAYS
            )
            if weekly is not None:
                reasons.append(f"14d scale weekly {weekly:+.2f} lb/week")
                if weekly <= -1.5:
                    rec_cal = min(tdee_hat, rec_cal + 150)
                    reasons.append("loss faster than 1.5 lb/week — raise toward TDEE")
                elif gap_lb > 3 and weekly > -0.2:
                    rec_cal = rec_cal - 100
                    reasons.append("loss slower than 0.2 lb/week with gap > 3 lb — deepen 100")
            rec_cal = int(clamp(rec_cal, tdee_hat - 500, tdee_hat - 250))
            floor = max(1800, round_kcal(11 * current_lb) if current_lb else 1800)
            if rec_cal < floor:
                rec_cal = floor
                reasons.append(f"cut floor {floor} kcal")
        elif phase == "slow_bulk":
            rec_cal = tdee_hat + 200
            ceiling = tdee_hat + 400
            rec_cal = min(rec_cal, ceiling)
            reasons.append("phase=slow_bulk; +200 kcal (ceiling TDEE+400)")
        else:
            rec_cal = tdee_hat
            reasons.append("phase=maintain; calories = TDEE hat")

        rec_score = float(recovery.score) if recovery is not None else None
        if rec_score is not None and rec_score < 40 and rec_cal < applied["calories"]:
            rec_cal = int(applied["calories"])
            reasons.append(
                f"recovery {rec_score:.0f} < 40 — will not deepen below applied {applied['calories']}"
            )
        prot_pct = None
        if isinstance(adherence_7d, dict):
            prot_pct = (adherence_7d.get("protein") or {}).get("pct")
        try:
            prot_pct_f = float(prot_pct) if prot_pct is not None else None
        except (TypeError, ValueError):
            prot_pct_f = None
        if (
            prot_pct_f is not None
            and prot_pct_f < 50
            and rec_cal < applied["calories"]
        ):
            rec_cal = int(applied["calories"])
            reasons.append(
                f"protein hit rate {prot_pct_f:.0f}% < 50% — compliance, not a deeper cut"
            )

        rec_cal = round_kcal(rec_cal)

    if current_lb:
        macros = _macros_for(calories=int(rec_cal), current_lb=current_lb, phase=phase)
        reasons.append(
            f"protein {'1.0' if phase == 'cut' else '0.9'} g/lb current → {macros['protein_g']} g"
        )
    else:
        # No bodyweight: do not invent protein/fat; calories already applied.
        macros = {
            "protein_g": int(applied["protein_g"]),
            "carbs_g": int(applied["carbs_g"]),
            "fat_g": int(applied["fat_g"]),
        }

    recommended = {
        "calories": int(rec_cal),
        "protein_g": macros["protein_g"],
        "carbs_g": macros["carbs_g"],
        "fat_g": macros["fat_g"],
    }
    delta = {
        "calories": recommended["calories"] - applied["calories"],
        "protein_g": recommended["protein_g"] - applied["protein_g"],
        "carbs_g": recommended["carbs_g"] - applied["carbs_g"],
        "fat_g": recommended["fat_g"] - applied["fat_g"],
    }
    abstain = bool(calorie_abstain)

    return {
        "as_of": day,
        "phase": phase,
        "tdee_kcal": int(tdee_hat) if tdee_hat is not None else None,
        "tdee_days": tdee_days,
        "current_weight_lbs": round(current_lb, 1) if current_lb else None,
        "weight_goal_lbs": goal_lb,
        "applied": applied,
        "recommended": recommended,
        "delta": delta,
        "abstain": abstain,
        "reasons": reasons,
    }


def merge_recommended_into_applied(applied: dict, rec: dict) -> dict:
    """Build a targets dict for ``update_targets``. Preserves weight_goal_lbs."""
    base = dict(applied or {})
    recd = (rec or {}).get("recommended") or {}
    for k in ("calories", "protein_g", "carbs_g", "fat_g"):
        if recd.get(k) is not None:
            base[k] = recd[k]
    if rec.get("phase") in PHASES:
        base["phase"] = rec["phase"]
    notes = str(base.get("notes") or "")
    stamp = rec.get("as_of") or local_today_iso()
    tag = f"Coach apply {stamp} phase={rec.get('phase')}"
    if "Coach apply" in notes:
        notes = notes.split("Coach apply")[0].rstrip(" |")
    base["notes"] = (notes + " | " + tag).strip(" |") if notes else tag
    return base
