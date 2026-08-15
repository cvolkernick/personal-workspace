"""Hydration pacing over the same wake window as calorie eating-window pacing.

Window = sleep-battery wake → empty (awake budget). Day target = 35 ml/kg from
latest weight (forward-filled as of as_of). Actual = civil-day water total
(Hidrate Day / Google Health) — sip timestamps not available in v1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

from .calorie_bars import eating_window_fraction, pace_vs_expected

# Common athletic baseline ≈ 0.5 oz/lb. Not heat/sweat individualized.
ML_PER_KG = 35.0
LB_PER_KG = 2.2046226218
# Coach fallback when no weight sample exists (matches coach adherence default).
DEFAULT_HYDRATION_GOAL_ML = 2500.0


def hydration_target_ml_from_lbs(weight_lbs: Any) -> Optional[float]:
    """Return day water target (ml) from body weight in pounds, or None."""
    try:
        lbs = float(weight_lbs)
    except (TypeError, ValueError):
        return None
    if lbs <= 0:
        return None
    kg = lbs / LB_PER_KG
    return round(kg * ML_PER_KG, 0)


def _weight_lbs(sample: Any) -> Optional[float]:
    if sample is None:
        return None
    if isinstance(sample, dict):
        raw = sample.get("weight_lbs")
        if raw is None:
            raw = sample.get("lbs")
    else:
        raw = getattr(sample, "weight_lbs", None)
        if raw is None:
            raw = getattr(sample, "lbs", None)
    try:
        lbs = float(raw)
    except (TypeError, ValueError):
        return None
    return lbs if lbs > 0 else None


def _sample_date(sample: Any) -> str:
    if sample is None:
        return ""
    if isinstance(sample, dict):
        return str(sample.get("date") or "")[:10]
    return str(getattr(sample, "date", "") or "")[:10]


def latest_weight_lbs(
    weight: Optional[Sequence[Any]],
    *,
    as_of: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Most recent weight on or before as_of (ISO date)."""
    if as_of is None:
        as_of = datetime.now().astimezone().date().isoformat()
    as_of = str(as_of)[:10]
    best: Optional[Dict[str, Any]] = None
    for w in weight or []:
        d = _sample_date(w)
        if not d or d > as_of:
            continue
        lbs = _weight_lbs(w)
        if lbs is None:
            continue
        if best is None or d > best["date"]:
            best = {"date": d, "weight_lbs": round(lbs, 2)}
    return best


def water_ml_for_day(
    hydration: Optional[Sequence[Any]],
    *,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """Civil-day water total for as_of from hydration series."""
    if as_of is None:
        as_of = datetime.now().astimezone().date().isoformat()
    as_of = str(as_of)[:10]
    for h in hydration or []:
        d = _sample_date(h)
        if d != as_of:
            continue
        if isinstance(h, dict):
            raw = h.get("water_ml")
            src = h.get("source") or "unknown"
        else:
            raw = getattr(h, "water_ml", None)
            src = getattr(h, "source", None) or "unknown"
        try:
            ml = float(raw or 0)
        except (TypeError, ValueError):
            ml = 0.0
        return {
            "date": as_of,
            "water_ml": round(max(0.0, ml), 1),
            "source": str(src),
        }
    return {"date": as_of, "water_ml": 0.0, "source": "none"}


def hydration_pacing(
    *,
    consumed_ml: float,
    target_ml: float,
    window_fraction: float,
) -> Dict[str, Any]:
    """Paced budget vs actual water for the wake-window progress bar.

    paced_budget = target_ml * clamp(window_fraction, 0, 1)
    on_pace band: |delta| ≤ max(100 ml, 5% of day target)
    """
    consumed = max(0.0, float(consumed_ml or 0))
    target = max(0.0, float(target_ml or 0))
    frac = max(0.0, min(1.0, float(window_fraction or 0)))

    if target <= 0:
        return {
            "consumed_ml": round(consumed, 1),
            "target_ml": 0.0,
            "window_fraction": frac,
            "paced_budget_ml": 0.0,
            "delta_vs_pace": round(consumed, 1),
            "fill_pct": 0.0,
            "expected_pct": round(frac * 100.0, 1),
            "status": "no_target",
            "summary": "Need weight for a dynamic water target (35 ml/kg).",
        }

    paced = target * frac
    fill_pct = min(150.0, (consumed / target) * 100.0)
    expected_pct = frac * 100.0
    delta = consumed - paced  # + ahead, − behind
    on_band = max(100.0, target * 0.05)

    if frac <= 0.02 and consumed <= 0:
        status = "start"
        summary = f"Wake window just opened · target {target:g} ml"
    elif abs(delta) <= on_band:
        status = "on_pace"
        summary = (
            f"{consumed:g} / {target:g} ml · on pace "
            f"(budget ~{paced:.0f} at {frac * 100:.0f}% of window)"
        )
    elif delta > 0:
        status = "ahead"
        summary = (
            f"{consumed:g} / {target:g} ml · {delta:.0f} ahead of pace "
            f"(~{paced:.0f} expected now)"
        )
    else:
        status = "behind"
        summary = (
            f"{consumed:g} / {target:g} ml · {abs(delta):.0f} behind pace "
            f"(~{paced:.0f} expected now)"
        )

    return {
        "consumed_ml": round(consumed, 1),
        "target_ml": round(target, 1),
        # Aliases so the calorie-bar renderer can stay generic if needed
        "consumed": round(consumed, 1),
        "target": round(target, 1),
        "window_fraction": round(frac, 4),
        "paced_budget_ml": round(paced, 1),
        "paced_budget": round(paced, 1),
        "delta_vs_pace": round(delta, 1),
        "fill_pct": round(fill_pct, 1),
        "expected_pct": round(expected_pct, 1),
        "status": status,
        "summary": summary,
    }


def build_hydration_bars_payload(
    *,
    hydration: Optional[Sequence[Any]] = None,
    weight: Optional[Sequence[Any]] = None,
    sleep_battery: Optional[dict] = None,
    as_of: Optional[str] = None,
    now: Optional[datetime] = None,
    target_ml_override: Optional[float] = None,
) -> Dict[str, Any]:
    """Compose hydration pacing payload for the dashboard JSON.

    Target precedence:
      1. target_ml_override (explicit)
      2. 35 ml/kg from latest weight on/before as_of
      3. DEFAULT_HYDRATION_GOAL_ML (2500)
    """
    if as_of is None:
        if now is not None:
            as_of = now.astimezone().date().isoformat()
        else:
            as_of = datetime.now().astimezone().date().isoformat()
    as_of = str(as_of)[:10]

    bat = sleep_battery or {}
    window = eating_window_fraction(
        now=now,
        last_wake_at=bat.get("last_wake_at"),
        empty_at=bat.get("empty_at"),
        awake_budget_hours=float(bat.get("awake_budget_hours") or 15.0),
    )
    frac = float(window["fraction"])

    day = water_ml_for_day(hydration, as_of=as_of)
    consumed = float(day.get("water_ml") or 0)

    wt = latest_weight_lbs(weight, as_of=as_of)
    target_source = "default"
    weight_lbs: Optional[float] = None
    weight_date: Optional[str] = None
    if target_ml_override is not None and float(target_ml_override) > 0:
        target = float(target_ml_override)
        target_source = "override"
    elif wt is not None:
        weight_lbs = float(wt["weight_lbs"])
        weight_date = wt["date"]
        derived = hydration_target_ml_from_lbs(weight_lbs)
        if derived is not None and derived > 0:
            target = float(derived)
            target_source = "weight_35ml_kg"
        else:
            target = DEFAULT_HYDRATION_GOAL_ML
            target_source = "default"
    else:
        target = DEFAULT_HYDRATION_GOAL_ML
        target_source = "default"

    pacing = hydration_pacing(
        consumed_ml=consumed,
        target_ml=target,
        window_fraction=frac,
    )
    pacing["window"] = window
    pacing["intake_source"] = day.get("source") or "none"
    pacing["as_of"] = as_of
    pacing["target_source"] = target_source
    pacing["weight_lbs"] = weight_lbs
    pacing["weight_date"] = weight_date
    pacing["ml_per_kg"] = ML_PER_KG

    # Severity band (same relative ladder as calories)
    band = pace_vs_expected(
        consumed=consumed,
        target=target,
        window_fraction=frac,
        kind="hydration",
    )
    pacing["band"] = band.get("band")
    pacing["color"] = band.get("color")
    pacing["rel_error"] = band.get("rel_error")

    return {
        "pacing": pacing,
        "day": day,
        "target_ml": round(target, 1),
        "target_source": target_source,
        "weight_lbs": weight_lbs,
        "weight_date": weight_date,
        "window": window,
    }
