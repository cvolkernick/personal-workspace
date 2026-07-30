"""Calorie pacing (eating window) and same-day in/out delta math for FitDash bars.

Eating window aligns with sleep-battery wake → empty (awake budget), so feeding
is paced over waking hours rather than midnight–midnight.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Union


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


def _food_log_as_dict(log: Any) -> Optional[dict]:
    if log is None:
        return None
    if isinstance(log, dict):
        return log
    if hasattr(log, "to_dict"):
        try:
            d = log.to_dict()
            return d if isinstance(d, dict) else None
        except Exception:
            pass
    # Duck-type FoodLogEntry
    date = getattr(log, "date", None)
    if not date:
        return None
    return {
        "date": date,
        "time": getattr(log, "time", None),
        "calories": getattr(log, "calories", None),
        "protein_g": getattr(log, "protein_g", None),
        "carbs_g": getattr(log, "carbs_g", None),
        "fat_g": getattr(log, "fat_g", None),
        "name": getattr(log, "name", None),
    }


def food_log_event_time(
    log: Any, *, default_tz: Optional[timezone] = None
) -> Optional[datetime]:
    """Local civil datetime for a food log (date + HH:MM when present).

    Logs without a time stamp are placed at local noon so full-day rows still
    land inside a typical multi-day wake window.
    """
    d = _food_log_as_dict(log)
    if not d:
        return None
    day = str(d.get("date") or "")[:10]
    if len(day) < 10:
        return None
    try:
        y, m, dd = int(day[0:4]), int(day[5:7]), int(day[8:10])
    except ValueError:
        return None
    tz = default_tz or datetime.now().astimezone().tzinfo or timezone.utc
    hh, mm = 12, 0  # noon fallback for date-only logs
    traw = d.get("time")
    if traw:
        ts = str(traw).strip()
        # Accept HH:MM or HH:MM:SS
        parts = ts.replace(".", ":").split(":")
        try:
            if len(parts) >= 2:
                hh = int(parts[0])
                mm = int(parts[1])
        except (TypeError, ValueError):
            hh, mm = 12, 0
    try:
        return datetime(y, m, dd, hh, mm, 0, tzinfo=tz)
    except ValueError:
        return None


def sum_intake_in_window(
    food_logs: Optional[Sequence[Any]],
    *,
    window_start: Any,
    window_end: Any,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Sum macros for food logs with event time in [window_start, cutoff].

    cutoff = min(now, window_end) so future meals past bedtime are excluded.
    Spans midnight correctly when the wake window crosses civil days.
    """
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()

    start = _parse_dt(window_start)
    end = _parse_dt(window_end)
    if start is None or end is None:
        return {
            "calories": 0.0,
            "protein_g": 0.0,
            "carbs_g": 0.0,
            "fat_g": 0.0,
            "log_count": 0,
            "source": "none",
        }
    start = start.astimezone(now.tzinfo)
    end = end.astimezone(now.tzinfo)
    cutoff = min(now, end)

    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    count = 0
    for log in food_logs or []:
        dt = food_log_event_time(log, default_tz=now.tzinfo)  # type: ignore[arg-type]
        if dt is None:
            continue
        dt = dt.astimezone(now.tzinfo)
        if dt < start or dt > cutoff:
            continue
        d = _food_log_as_dict(log) or {}
        try:
            totals["calories"] += float(d.get("calories") or 0)
            totals["protein_g"] += float(d.get("protein_g") or 0)
            totals["carbs_g"] += float(d.get("carbs_g") or 0)
            totals["fat_g"] += float(d.get("fat_g") or 0)
            count += 1
        except (TypeError, ValueError):
            continue

    return {
        "calories": round(totals["calories"], 1),
        "protein_g": round(totals["protein_g"], 1),
        "carbs_g": round(totals["carbs_g"], 1),
        "fat_g": round(totals["fat_g"], 1),
        "log_count": count,
        "source": "eating_window_logs" if count else "none",
        "window_start": start.isoformat(timespec="seconds"),
        "window_end": end.isoformat(timespec="seconds"),
        "cutoff": cutoff.isoformat(timespec="seconds"),
    }


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

    def _civil_day_window(src: str) -> Dict[str, Any]:
        local = now.astimezone()
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = start + timedelta(hours=24)
        total = max(1.0, (day_end - start).total_seconds())
        elapsed = (now - start).total_seconds()
        frac = max(0.0, min(1.0, elapsed / total))
        return {
            "fraction": round(frac, 4),
            "hours_elapsed": round(max(0.0, elapsed / 3600.0), 2),
            "hours_total": round(total / 3600.0, 2),
            "hours_left": round(max(0.0, (day_end - now).total_seconds() / 3600.0), 2),
            "window_start": start.isoformat(timespec="seconds"),
            "window_end": day_end.isoformat(timespec="seconds"),
            "source": src,
        }

    if wake is None:
        return _civil_day_window("civil_day_fallback")

    wake_local = wake.astimezone(now.tzinfo)
    if end is None:
        end = wake_local + timedelta(hours=budget)
    else:
        end = end.astimezone(now.tzinfo)

    # Wake→empty already finished (battery empty / overdue for sleep).
    # Keep pacing on the *current* civil day so we don't pin intake to the
    # completed cycle (e.g. yesterday's meals at 100% of an expired window).
    if now > end:
        return _civil_day_window("civil_day_after_empty")

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
    # Half-track scale must NOT include |delta| — otherwise large deficits/surpluses
    # all cap at 100% and are not proportionate. Fixed baseline + size of day.
    if scale_kcal is not None and float(scale_kcal) > 0:
        scale = float(scale_kcal)
    else:
        scale = max(1000.0, 0.5 * max(intake, burned_f, 1.0))
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
    food_logs: Optional[Sequence[Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Compose both bar payloads for the dashboard JSON.

    Pacing intake prefers food logs timed inside the sleep-battery eating
    window (can span midnight). Falls back to civil-day ``today_consumed``
    when no window logs are found. In/out delta still uses civil-day intake
    vs same-day burned.
    """
    civil_consumed = float((today_consumed or {}).get("calories") or 0)
    target = float((targets or {}).get("calories") or 0)
    bat = sleep_battery or {}

    window = eating_window_fraction(
        now=now,
        last_wake_at=bat.get("last_wake_at"),
        empty_at=bat.get("empty_at"),
        awake_budget_hours=float(bat.get("awake_budget_hours") or 16.0),
    )
    win_intake = sum_intake_in_window(
        food_logs,
        window_start=window.get("window_start"),
        window_end=window.get("window_end"),
        now=now,
    )
    if win_intake.get("log_count"):
        pacing_consumed = float(win_intake.get("calories") or 0)
        pacing_source = "eating_window_logs"
    else:
        pacing_consumed = civil_consumed
        pacing_source = "civil_day_fallback"

    pacing = calorie_pacing(
        consumed=pacing_consumed,
        target=target,
        window_fraction=float(window["fraction"]),
    )
    pacing["window"] = window
    pacing["intake_source"] = pacing_source
    pacing["window_intake"] = win_intake
    pacing["civil_day_consumed"] = round(civil_consumed, 1)

    delta = calorie_in_out_delta(
        intake=civil_consumed,
        burned=calories_burned_today,
    )
    return {"pacing": pacing, "delta": delta}
