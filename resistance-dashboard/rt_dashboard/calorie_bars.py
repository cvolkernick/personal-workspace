"""Calorie pacing (eating window) and same-day in/out delta math for FitDash bars.

Eating window aligns with sleep-battery wake → empty (awake budget), so feeding
is paced over waking hours rather than midnight–midnight.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def eating_window_fraction(
    *,
    now: Optional[datetime] = None,
    last_wake_at: Any = None,
    empty_at: Any = None,
    awake_budget_hours: float = 16.0,
) -> Dict[str, Any]:
    """Fraction of the eating/wake window elapsed in [0, 1].

    Window start = last wake; end = empty_at or wake + awake_budget_hours.
    Fallback (no wake): use local civil day so far (midnight → now / 24h).
    """
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()

    wake = _parse_dt(last_wake_at)
    end = _parse_dt(empty_at)
    budget = max(1.0, float(awake_budget_hours or 16.0))
    source = "sleep_battery"

    if wake is None:
        # Civil-day fallback
        local = now.astimezone()
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=24)
        wake = start
        source = "civil_day_fallback"
    else:
        wake_local = wake.astimezone(now.tzinfo)
        if end is None:
            end = wake_local + timedelta(hours=budget)
        else:
            end = end.astimezone(now.tzinfo)
        wake = wake_local

    total_sec = max(1.0, (end - wake).total_seconds())
    elapsed_sec = (now - wake).total_seconds()
    frac = max(0.0, min(1.0, elapsed_sec / total_sec))
    hours_elapsed = max(0.0, elapsed_sec / 3600.0)
    hours_total = total_sec / 3600.0
    hours_left = max(0.0, (end - now).total_seconds() / 3600.0)

    return {
        "fraction": round(frac, 4),
        "hours_elapsed": round(hours_elapsed, 2),
        "hours_total": round(hours_total, 2),
        "hours_left": round(hours_left, 2),
        "window_start": wake.isoformat(timespec="seconds"),
        "window_end": end.isoformat(timespec="seconds"),
        "source": source,
    }


def calorie_pacing(
    *,
    consumed: float,
    target: float,
    window_fraction: float,
) -> Dict[str, Any]:
    """Paced budget vs actual intake for the eating-window progress bar.

    paced_budget = target * clamp(window_fraction, 0, 1)
    fill_pct = consumed / target (0–100+, capped display later)
    expected_pct = window_fraction * 100  (marker on the track)
    """
    consumed = max(0.0, float(consumed or 0))
    target = max(0.0, float(target or 0))
    frac = max(0.0, min(1.0, float(window_fraction or 0)))

    if target <= 0:
        return {
            "consumed": round(consumed, 1),
            "target": 0.0,
            "window_fraction": frac,
            "paced_budget": 0.0,
            "delta_vs_pace": round(consumed, 1),
            "fill_pct": 0.0,
            "expected_pct": round(frac * 100.0, 1),
            "status": "no_target",
            "summary": "Set a calorie target to pace intake.",
        }

    paced = target * frac
    fill_pct = min(150.0, (consumed / target) * 100.0)
    expected_pct = frac * 100.0
    delta = consumed - paced  # + ahead of pace, − behind

    if frac <= 0.02 and consumed <= 0:
        status = "start"
        summary = f"Eating window just opened · target {target:g} kcal"
    elif abs(delta) <= max(50.0, target * 0.05):
        status = "on_pace"
        summary = (
            f"{consumed:g} / {target:g} kcal · on pace "
            f"(budget ~{paced:.0f} at {frac * 100:.0f}% of window)"
        )
    elif delta > 0:
        status = "ahead"
        summary = (
            f"{consumed:g} / {target:g} kcal · {delta:.0f} ahead of pace "
            f"(~{paced:.0f} expected now)"
        )
    else:
        status = "behind"
        summary = (
            f"{consumed:g} / {target:g} kcal · {abs(delta):.0f} behind pace "
            f"(~{paced:.0f} expected now)"
        )

    return {
        "consumed": round(consumed, 1),
        "target": round(target, 1),
        "window_fraction": round(frac, 4),
        "paced_budget": round(paced, 1),
        "delta_vs_pace": round(delta, 1),
        "fill_pct": round(fill_pct, 1),
        "expected_pct": round(expected_pct, 1),
        "status": status,
        "summary": summary,
    }


def calorie_in_out_delta(
    *,
    intake: float,
    burned: float,
    scale_kcal: Optional[float] = None,
) -> Dict[str, Any]:
    """Signed intake − burned for bidirectional bar (midpoint 0).

    delta > 0 → surplus (right, green)
    delta < 0 → deficit (left, red)
    delta == 0 → equilibrium
    bar_pct is 0–100 of half-track toward that side (capped).
    """
    intake = max(0.0, float(intake or 0))
    burned_raw = burned
    try:
        burned_f = float(burned) if burned is not None else None
    except (TypeError, ValueError):
        burned_f = None

    if burned_f is None:
        return {
            "intake": round(intake, 1),
            "burned": None,
            "delta": None,
            "side": "none",
            "color": "muted",
            "bar_pct": 0.0,
            "scale_kcal": float(scale_kcal or 1000),
            "status": "no_burned",
            "summary": "No same-day burned calories yet — delta unavailable.",
        }

    burned_f = max(0.0, burned_f)
    delta = intake - burned_f
    # Scale: at least 500 kcal half-width, prefer max(|delta|, 25% of larger of in/out)
    auto_scale = max(500.0, abs(delta), 0.25 * max(intake, burned_f, 1.0))
    scale = float(scale_kcal) if scale_kcal and scale_kcal > 0 else auto_scale
    scale = max(100.0, scale)
    bar_pct = min(100.0, (abs(delta) / scale) * 100.0)

    if abs(delta) < 1.0:
        side = "equilibrium"
        color = "neutral"
        summary = f"Equilibrium · in {intake:g} · out {burned_f:g}"
    elif delta < 0:
        side = "deficit"
        color = "red"
        summary = f"Deficit {delta:.0f} kcal · in {intake:g} · out {burned_f:g}"
    else:
        side = "surplus"
        color = "green"
        summary = f"Surplus +{delta:.0f} kcal · in {intake:g} · out {burned_f:g}"

    return {
        "intake": round(intake, 1),
        "burned": round(burned_f, 1),
        "delta": round(delta, 1),
        "side": side,
        "color": color,
        "bar_pct": round(bar_pct, 1),
        "scale_kcal": round(scale, 1),
        "status": "ok",
        "summary": summary,
    }


def build_calorie_bars_payload(
    *,
    today_consumed: Optional[dict] = None,
    targets: Optional[dict] = None,
    sleep_battery: Optional[dict] = None,
    calories_burned_today: Optional[float] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Compose both bar payloads for the dashboard JSON."""
    consumed = float((today_consumed or {}).get("calories") or 0)
    target = float((targets or {}).get("calories") or 0)
    bat = sleep_battery or {}

    window = eating_window_fraction(
        now=now,
        last_wake_at=bat.get("last_wake_at"),
        empty_at=bat.get("empty_at"),
        awake_budget_hours=float(bat.get("awake_budget_hours") or 16.0),
    )
    pacing = calorie_pacing(
        consumed=consumed,
        target=target,
        window_fraction=float(window["fraction"]),
    )
    pacing["window"] = window

    delta = calorie_in_out_delta(
        intake=consumed,
        burned=calories_burned_today,
    )
    return {"pacing": pacing, "delta": delta}
