"""Hydration pacing over the same wake window as calorie eating-window pacing.

Window = sleep-battery last_wake_at → empty_at (may cross a civil day).
After empty_at the wake clock stays at fraction 1.0 (source
``sleep_battery_after_empty``) — do not switch hydration to civil /24.
Day target = 35 ml/kg from latest weight (2500 fallback). Wake-bar actual =
sum of timestamped Sip samples in [wake_start, wake_end] (cutoff min(now, end)).
Pace = actual vs target × window fraction — only when sip times exist.
Civil-day totals (``water_ml_for_day``) stay for Trends — never split across
midnight, and never enough to call pace green / on-pace.
Date-only rows are skipped. No wake / no timed sips → honest unknown.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from .calorie_bars import _parse_dt, eating_window_fraction, pace_vs_expected

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


def _sample_as_dict(sample: Any) -> Optional[Dict[str, Any]]:
    if sample is None:
        return None
    if isinstance(sample, dict):
        return sample
    if hasattr(sample, "to_dict"):
        try:
            d = sample.to_dict()
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    out: Dict[str, Any] = {}
    for key in (
        "logged_at",
        "timestamp",
        "startTime",
        "start_time",
        "time",
        "date",
        "water_ml",
        "amount",
        "milliliters",
        "ml",
        "source",
    ):
        if hasattr(sample, key):
            out[key] = getattr(sample, key)
    return out or None


def _parse_time_value(value: Any) -> Optional[datetime]:
    """Parse a full datetime. Clock-only strings (HH:MM) are not enough."""
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return _parse_dt(value.get("iso") or value.get("iso8601"))
    s = str(value).strip()
    if not s:
        return None
    # HH:MM / HH:MM:SS without a date is not a sip timestamp — do not invent a day.
    if "T" not in s and "-" not in s and ":" in s and len(s) <= 8:
        return None
    return _parse_dt(s)


def water_sample_event_time(
    sample: Any, *, default_tz: Any = None
) -> Optional[datetime]:
    """Event time for a water sample. Date-only rows return None (honest skip)."""
    d = _sample_as_dict(sample)
    if not d:
        return None
    for key in ("logged_at", "timestamp", "startTime", "start_time", "time"):
        if key not in d or d.get(key) in (None, ""):
            continue
        dt = _parse_time_value(d.get(key))
        if dt is None:
            continue
        if default_tz is not None:
            dt = dt.astimezone(default_tz)
        return dt

    # date + clock (HH:MM) when both are present — not invented.
    day = str(d.get("date") or "")[:10]
    clock = d.get("time")
    if len(day) == 10 and clock not in (None, ""):
        clock_s = str(clock).strip()
        if "T" not in clock_s and len(clock_s) <= 8 and ":" in clock_s:
            parts = clock_s.replace(".", ":").split(":")
            try:
                hh, mm = int(parts[0]), int(parts[1])
            except (TypeError, ValueError, IndexError):
                return None
            composed = _parse_dt(f"{day}T{hh:02d}:{mm:02d}:00")
            if composed is None:
                return None
            if default_tz is not None:
                if composed.tzinfo is None:
                    composed = composed.replace(tzinfo=default_tz)
                else:
                    composed = composed.astimezone(default_tz)
            return composed
    return None


def _sample_ml(sample: Any) -> Optional[float]:
    d = _sample_as_dict(sample)
    if not d:
        return None
    for key in ("water_ml", "amount", "milliliters", "ml"):
        raw = d.get(key)
        if raw is None or raw == "":
            continue
        try:
            ml = float(raw)
        except (TypeError, ValueError):
            continue
        if ml != ml or ml < 0:
            continue
        return ml
    return None


def water_ml_for_window(
    samples: Optional[Sequence[Any]],
    *,
    window_start: Any,
    window_end: Any = None,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Sum water samples with timestamps in [wake_start, wake_end].

    cutoff = min(now, wake_end) so future-dated sips are not counted. Date-only
    civil-day rows are skipped — no invented sip time or cross-midnight split.
    Missing wake (no window_start) or no in-window samples → honest 0.
    """
    if now is None or tz_name:
        from .timeutil import local_now

        now = local_now(tz_name, now=now)
    elif now.tzinfo is None:
        from .timeutil import local_tz

        now = now.replace(tzinfo=timezone.utc).astimezone(local_tz(tz_name))

    start = _parse_dt(window_start)
    if start is None:
        return {"water_ml": 0.0, "sample_count": 0, "source": "none"}
    start = start.astimezone(now.tzinfo)
    end = _parse_dt(window_end)
    if end is not None:
        end = end.astimezone(now.tzinfo)
        cutoff = min(now, end)
    else:
        cutoff = now

    total = 0.0
    count = 0
    sources: List[str] = []
    for sample in samples or []:
        dt = water_sample_event_time(sample, default_tz=now.tzinfo)
        if dt is None:
            continue
        dt = dt.astimezone(now.tzinfo)
        if dt < start or dt > cutoff:
            continue
        ml = _sample_ml(sample)
        if ml is None:
            continue
        total += ml
        count += 1
        d = _sample_as_dict(sample) or {}
        src = str(d.get("source") or "").strip()
        if src:
            sources.append(src)

    source = "none"
    if count:
        uniq: List[str] = []
        for src in sources:
            if src not in uniq:
                uniq.append(src)
        source = uniq[0] if len(uniq) == 1 else "wake_window"

    return {
        "water_ml": round(total, 1),
        "sample_count": count,
        "source": source,
        "window_start": start.isoformat(timespec="seconds"),
        "window_end": (end or cutoff).isoformat(timespec="seconds"),
        "cutoff": cutoff.isoformat(timespec="seconds"),
    }


def timed_sip_samples(
    samples: Optional[Sequence[Any]] = None,
    extra: Optional[Sequence[Any]] = None,
) -> List[Any]:
    """Rows with a real event time and ml. Date-only civil totals are skipped."""
    out: List[Any] = []
    for src in (samples, extra):
        for sample in src or []:
            if water_sample_event_time(sample) is None:
                continue
            if _sample_ml(sample) is None:
                continue
            out.append(sample)
    return out


def _unknown_hydration_pacing(
    *,
    target_ml: float,
    window_fraction: float,
) -> Dict[str, Any]:
    """Honest empty pace when Hidrate has no sip timestamps."""
    target = max(0.0, float(target_ml or 0))
    frac = max(0.0, min(1.0, float(window_fraction or 0)))
    return {
        "consumed_ml": None,
        "consumed": None,
        "target_ml": round(target, 1),
        "target": round(target, 1),
        "window_fraction": round(frac, 4),
        "paced_budget_ml": None,
        "paced_budget": None,
        "delta_vs_pace": None,
        "fill_pct": None,
        "expected_pct": round(frac * 100.0, 1),
        "status": "unknown",
        "summary": "No sip timestamps — wake-window pace unknown.",
        "sip_aware": False,
        "sip_count": 0,
    }


def hydration_pacing(
    *,
    consumed_ml: float,
    target_ml: float,
    window_fraction: float,
    sip_aware: bool = True,
) -> Dict[str, Any]:
    """Paced budget vs actual water for the wake-window progress bar.

    paced_budget = target_ml * clamp(window_fraction, 0, 1)
    on_pace band: |delta| ≤ max(100 ml, 5% of day target)
    Without timed sips (``sip_aware=False``) status is unknown — never on-pace.
    """
    if not sip_aware:
        return _unknown_hydration_pacing(
            target_ml=target_ml, window_fraction=window_fraction
        )

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
            "sip_aware": True,
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
        "sip_aware": True,
    }


def build_hydration_bars_payload(
    *,
    hydration: Optional[Sequence[Any]] = None,
    samples: Optional[Sequence[Any]] = None,
    weight: Optional[Sequence[Any]] = None,
    sleep_battery: Optional[dict] = None,
    as_of: Optional[str] = None,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
    target_ml_override: Optional[float] = None,
) -> Dict[str, Any]:
    """Compose hydration pacing payload for the dashboard JSON.

    Target precedence:
      1. target_ml_override (explicit)
      2. 35 ml/kg from latest weight on/before as_of
      3. DEFAULT_HYDRATION_GOAL_ML (2500)

    Wake-bar ``consumed`` = sum of timestamped Sip samples in
    ``[last_wake_at, empty_at]`` (may cross a civil day). After empty_at,
    ``window_fraction`` stays 1.0 and pace is vs the full wake target.
    Civil-day ``hydration`` is only used for ``day`` / Trends — never split
    or summed across midnight to fake a wake actual. No wake or no timed
    sips → status ``unknown`` (not on-pace).
    """
    if now is None or tz_name:
        from .timeutil import local_now

        now = local_now(tz_name, now=now)
    elif now.tzinfo is None:
        from .timeutil import local_tz

        now = now.replace(tzinfo=timezone.utc).astimezone(local_tz(tz_name))

    if as_of is None:
        from .timeutil import local_today_iso

        as_of = local_today_iso(tz_name, now=now)
    as_of = str(as_of)[:10]

    bat = sleep_battery or {}
    window = eating_window_fraction(
        now=now,
        tz_name=tz_name,
        last_wake_at=bat.get("last_wake_at"),
        empty_at=bat.get("empty_at"),
        awake_budget_hours=float(bat.get("awake_budget_hours") or 15.0),
        mode="hydration",
    )
    frac = float(window["fraction"])

    day = water_ml_for_day(hydration, as_of=as_of)
    # Only timestamped sips count. Civil-day Day.totalAmount is Trends-only.
    timed = timed_sip_samples(samples, hydration)
    sip_aware = bool(timed)
    # Actual follows sleep-battery wake→empty (held after empty_at).
    # Fraction uses mode=hydration so it does not switch to civil /24.
    wake_start = bat.get("last_wake_at")
    wake_end = bat.get("empty_at")
    if wake_end is None and wake_start:
        wake_dt = _parse_dt(wake_start)
        if wake_dt is not None:
            budget = max(1.0, float(bat.get("awake_budget_hours") or 15.0))
            wake_end = (wake_dt + timedelta(hours=budget)).isoformat(
                timespec="seconds"
            )
    if not wake_start or not sip_aware:
        win_intake = {"water_ml": None, "sample_count": 0, "source": "none"}
        consumed = None
        intake_source = "none"
        sip_aware = False
    else:
        win_intake = water_ml_for_window(
            timed,
            window_start=wake_start,
            window_end=wake_end,
            now=now,
            tz_name=tz_name,
        )
        consumed = float(win_intake.get("water_ml") or 0)
        intake_source = str(win_intake.get("source") or "none")

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
        consumed_ml=consumed if consumed is not None else 0.0,
        target_ml=target,
        window_fraction=frac,
        sip_aware=sip_aware,
    )
    pacing["window"] = window
    pacing["intake_source"] = intake_source
    pacing["window_intake"] = win_intake
    pacing["civil_day_ml"] = float(day.get("water_ml") or 0)
    pacing["as_of"] = as_of
    pacing["target_source"] = target_source
    pacing["weight_lbs"] = weight_lbs
    pacing["weight_date"] = weight_date
    pacing["ml_per_kg"] = ML_PER_KG
    pacing["sip_aware"] = sip_aware
    pacing["sip_count"] = int(win_intake.get("sample_count") or 0)

    # Severity band only when sip times exist. Civil midnight must not go green.
    if sip_aware and consumed is not None:
        band = pace_vs_expected(
            consumed=consumed,
            target=target,
            window_fraction=frac,
            kind="hydration",
        )
        pacing["band"] = band.get("band")
        pacing["color"] = band.get("color")
        pacing["rel_error"] = band.get("rel_error")
    else:
        pacing["band"] = "muted"
        pacing["color"] = None
        pacing["rel_error"] = None

    return {
        "pacing": pacing,
        "day": day,
        "target_ml": round(target, 1),
        "target_source": target_source,
        "weight_lbs": weight_lbs,
        "weight_date": weight_date,
        "window": window,
    }
