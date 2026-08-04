"""Recovery status from weight, sleep, and recent training volume."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Sequence

from .models import RecoveryStatus, Session, SleepSample, WeightSample
from .analytics import recent_training_volume
from .sleep_series import calendar_avg_sleep_hours


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
        },
    )
