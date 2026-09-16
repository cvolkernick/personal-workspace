"""Recovery status from weight, sleep, RHR, and recent training volume."""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, List, Optional, Sequence

from .models import (
    RecoveryStatus,
    RestingHeartRateDay,
    Session,
    SleepSample,
    WeightSample,
)
from .analytics import recent_training_volume
from .sleep_series import calendar_avg_sleep_hours

# Elevated RHR vs personal median is a fatigue/illness flag (Banister / Firstbeat).
# +5 bpm = under-recovered (easy intensity). +8 bpm = rest day.
RHR_UNDER_RECOVERED_BPM = 5.0
RHR_REST_BPM = 8.0
RHR_SCORE_UNDER = 12.0
RHR_SCORE_REST = 8.0  # extra on top of UNDER when delta >= REST
RHR_MIN_SAMPLES_14 = 7
RHR_MIN_SAMPLES_7 = 4


def rhr_readiness(
    rhr: Sequence[RestingHeartRateDay],
    as_of: str,
) -> Dict[str, Any]:
    """Compare today's RHR to a 14d (else 7d) median of prior days.

    Silent skip when today is missing or the baseline window is too thin.
    Never invents bpm. HRV / VO2 / SpO2 / respiratory rate are out of scope.
    """
    empty: Dict[str, Any] = {
        "skipped": True,
        "under_recovered": False,
        "today_bpm": None,
        "baseline_bpm": None,
        "baseline_days": None,
        "delta_bpm": None,
        "n": 0,
    }
    by: Dict[str, float] = {}
    for row in rhr or []:
        date = str(getattr(row, "date", "") or "")[:10]
        bpm = getattr(row, "bpm", None)
        if not date or bpm is None:
            continue
        try:
            val = float(bpm)
        except (TypeError, ValueError):
            continue
        if val <= 0:
            continue
        by[date] = val
    today = by.get(as_of)
    if today is None:
        return empty
    try:
        as_of_d = datetime.strptime(as_of, "%Y-%m-%d")
    except ValueError:
        return empty

    def _prior(days: int) -> List[float]:
        start = (as_of_d - timedelta(days=days)).strftime("%Y-%m-%d")
        return [bpm for d, bpm in by.items() if start <= d < as_of]

    vals14 = _prior(14)
    vals7 = _prior(7)
    if len(vals14) >= RHR_MIN_SAMPLES_14:
        baseline = float(median(vals14))
        window = 14
        n = len(vals14)
    elif len(vals7) >= RHR_MIN_SAMPLES_7:
        baseline = float(median(vals7))
        window = 7
        n = len(vals7)
    else:
        out = dict(empty)
        out["today_bpm"] = round(today, 1)
        out["n"] = max(len(vals14), len(vals7))
        return out
    delta = today - baseline
    return {
        "skipped": False,
        "under_recovered": delta >= RHR_UNDER_RECOVERED_BPM,
        "today_bpm": round(today, 1),
        "baseline_bpm": round(baseline, 1),
        "baseline_days": window,
        "delta_bpm": round(delta, 1),
        "n": n,
    }


def _avg_sleep_hours(
    sleep: Sequence[SleepSample],
    days: int = 7,
    as_of: Optional[str] = None,
) -> Optional[float]:
    """Mean sleep over the last ``days`` *calendar* days (unlogged = 0h)."""
    if as_of is None:
        from .timeutil import local_today_iso

        as_of = local_today_iso()
    return calendar_avg_sleep_hours(sleep, as_of=as_of, days=days)


def _latest_weight(weight: Sequence[WeightSample]) -> Optional[float]:
    if not weight:
        return None
    ordered = sorted(weight, key=lambda w: w.date, reverse=True)
    return ordered[0].weight_lbs


def _weight_delta_7d(weight: Sequence[WeightSample]) -> Optional[float]:
    if len(weight) < 2:
        return None
    ordered = sorted(weight, key=lambda w: w.date)
    latest = ordered[-1]
    end = datetime.strptime(latest.date, "%Y-%m-%d")
    start = end - timedelta(days=7)
    older = [w for w in ordered if datetime.strptime(w.date, "%Y-%m-%d") <= start]
    if not older:
        # compare to earliest available if window short
        if len(ordered) >= 2:
            return latest.weight_lbs - ordered[0].weight_lbs
        return None
    return latest.weight_lbs - older[-1].weight_lbs


def compute_recovery_status(
    weight: Sequence[WeightSample],
    sleep: Sequence[SleepSample],
    sessions: Sequence[Session],
    as_of: Optional[str] = None,
    high_volume_threshold: float = 25000.0,
    rhr: Sequence[RestingHeartRateDay] = (),
) -> RecoveryStatus:
    """
    Produce an explicit recovery-status suggestion from health + training context.

    Labels (score bands):
      - Ready (75-100)
      - Moderate (50-74)
      - Caution (30-49)
      - Needs Rest (0-29)
    """
    # Default to local civil today so "last 7d volume" matches the host timezone
    # (not UTC midnight, and not the most recent historical log date).
    if as_of is None:
        from .timeutil import local_today_iso

        as_of = local_today_iso()

    avg_sleep = _avg_sleep_hours(sleep, days=7, as_of=as_of)
    latest_w = _latest_weight(weight)
    w_delta = _weight_delta_7d(weight)
    vol_7d = recent_training_volume(sessions, as_of=as_of, window_days=7)

    score = 70.0  # neutral baseline when sparse data
    reasons: List[str] = []

    # Unlogged nights count as 0h (sleep debt) — always have a 7d calendar mean.
    if avg_sleep is None:
        reasons.append("No sleep window available — score starts from neutral baseline")
    else:
        from .sleep_series import expand_sleep_calendar

        filled7 = expand_sleep_calendar(sleep, as_of=as_of, window_days=7)
        zero_nights = sum(1 for s in filled7 if float(s.sleep_hours or 0) <= 0)

        if avg_sleep >= 8.0:
            score += 15
            reasons.append(f"Strong sleep avg {avg_sleep:.1f}h (7 calendar days; unlogged=0)")
        elif avg_sleep >= 7.0:
            score += 8
            reasons.append(f"Adequate sleep avg {avg_sleep:.1f}h (7 calendar days; unlogged=0)")
        elif avg_sleep >= 6.0:
            score -= 10
            reasons.append(f"Borderline sleep avg {avg_sleep:.1f}h (7 calendar days; unlogged=0)")
        else:
            score -= 25
            reasons.append(f"Low sleep avg {avg_sleep:.1f}h (7 calendar days; unlogged=0)")
        if zero_nights:
            score -= min(15, zero_nights * 5)
            reasons.append(
                f"{zero_nights} night(s) with no sleep log counted as 0h (sleep debt)"
            )

    if vol_7d >= high_volume_threshold * 1.25:
        score -= 18
        reasons.append(f"Very high training volume last 7d ({vol_7d:,.0f} lb)")
    elif vol_7d >= high_volume_threshold:
        score -= 10
        reasons.append(f"Elevated training volume last 7d ({vol_7d:,.0f} lb)")
    elif vol_7d > 0:
        score += 5
        reasons.append(f"Manageable training volume last 7d ({vol_7d:,.0f} lb)")
    else:
        reasons.append("No logged training volume in last 7 days")

    if w_delta is not None:
        if w_delta <= -2.0:
            score -= 12
            reasons.append(f"Rapid weight drop {w_delta:+.1f} lb over ~7d — monitor recovery/fueling")
        elif w_delta >= 2.5:
            score -= 4
            reasons.append(f"Weight up {w_delta:+.1f} lb over ~7d (possible inflammation/water)")
        else:
            score += 3
            reasons.append(f"Weight stable ({w_delta:+.1f} lb ~7d)")

    if latest_w is not None:
        reasons.append(f"Latest body weight {latest_w:.1f} lb")

    rhr_sig = rhr_readiness(rhr, as_of)
    if not rhr_sig.get("skipped"):
        today_bpm = rhr_sig.get("today_bpm")
        baseline = rhr_sig.get("baseline_bpm")
        window = rhr_sig.get("baseline_days")
        delta = rhr_sig.get("delta_bpm")
        if rhr_sig.get("under_recovered"):
            score -= RHR_SCORE_UNDER
            if delta is not None and float(delta) >= RHR_REST_BPM:
                score -= RHR_SCORE_REST
            sign = "+" if float(delta or 0) >= 0 else ""
            reasons.append(
                f"RHR {today_bpm:.0f} bpm is {sign}{float(delta):.0f} vs "
                f"{window}d median {baseline:.0f} — under-recovered"
            )
        # On-pace RHR stays off the reason list (Trends owns the series).

    score = max(0.0, min(100.0, score))

    if score >= 75:
        label = "Ready"
    elif score >= 50:
        label = "Moderate"
    elif score >= 30:
        label = "Caution"
    else:
        label = "Needs Rest"

    return RecoveryStatus(
        label=label,
        score=round(score, 1),
        reasons=reasons,
        inputs={
            "as_of": as_of,
            "avg_sleep_hours_7d": avg_sleep,
            "latest_weight_lbs": latest_w,
            "weight_delta_7d_lbs": w_delta,
            "training_volume_7d": vol_7d,
            "rhr_skipped": bool(rhr_sig.get("skipped")),
            "rhr_today_bpm": rhr_sig.get("today_bpm"),
            "rhr_baseline_bpm": rhr_sig.get("baseline_bpm"),
            "rhr_baseline_days": rhr_sig.get("baseline_days"),
            "rhr_delta_bpm": rhr_sig.get("delta_bpm"),
            "rhr_under_recovered": bool(rhr_sig.get("under_recovered")),
        },
    )
