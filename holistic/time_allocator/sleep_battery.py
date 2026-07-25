"""Sleep battery: full at wake, drains over awake period until next sleep.

Concept (aligned with 8h sleep / 16h wake for a 24h day):
  - Battery is **100% full** at the *end* of the last logged sleep cycle (wake).
  - It **drains linearly** over ``awake_hours`` (default 16h = 24 − 8 sleep target).
  - At 0% you are due to sleep and recharge.
  - Helps maintain a rhythm consistent with a rolling 7-day ~8h sleep average.

Still reports hours-asleep-in-last-24h as a secondary metric for KPIs/context.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_dt(value: str | datetime | None) -> datetime | None:
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


def _overlap_seconds(
    a0: datetime, a1: datetime, b0: datetime, b1: datetime
) -> float:
    """Seconds of overlap between [a0,a1) and [b0,b1)."""
    start = max(a0, b0)
    end = min(a1, b1)
    if end <= start:
        return 0.0
    return (end - start).total_seconds()


def normalize_intervals(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in raw or []:
        st = _parse_dt(row.get("start") or row.get("startTime"))
        en = _parse_dt(row.get("end") or row.get("endTime"))
        if not st or not en or en <= st:
            continue
        if (en - st).total_seconds() > 36 * 3600:
            continue
        out.append(
            {
                "start": st.isoformat(timespec="seconds"),
                "end": en.isoformat(timespec="seconds"),
                "source": str(row.get("source") or "unknown"),
            }
        )
    seen: set[tuple[str, str]] = set()
    uniq: list[dict[str, Any]] = []
    for r in sorted(out, key=lambda x: x["start"]):
        key = (r["start"], r["end"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def intervals_from_daily_logs(
    logs: list[dict[str, Any]],
    *,
    target_id: str = "sleep",
    assume_wake_local_hour: int = 7,
    tz: timezone | None = None,
) -> list[dict[str, Any]]:
    """Approximate intervals when only per-day hour totals exist.

    Places each night ending at ``assume_wake_local_hour`` local and extending
    backward by the logged hours. Prefer real Google intervals when available.
    """
    tz = tz or datetime.now().astimezone().tzinfo or timezone.utc
    out: list[dict[str, Any]] = []
    for lg in logs or []:
        if str(lg.get("target_id")) != target_id:
            continue
        hours = float(lg.get("value") or 0)
        if hours <= 0:
            continue
        day = str(lg.get("date") or "")[:10]
        if len(day) < 10:
            continue
        try:
            y, m, d = int(day[0:4]), int(day[5:7]), int(day[8:10])
        except ValueError:
            continue
        wake = datetime(y, m, d, assume_wake_local_hour, 0, 0, tzinfo=tz)
        start = wake - timedelta(hours=hours)
        out.append(
            {
                "start": start.isoformat(timespec="seconds"),
                "end": wake.isoformat(timespec="seconds"),
                "source": str(lg.get("note") or "daily_log_approx"),
            }
        )
    return normalize_intervals(out)


def _latest_completed_sleep(
    intervals: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any] | None:
    """Most recent sleep segment that has already ended (wake time ≤ now)."""
    best = None
    best_end: datetime | None = None
    for row in normalize_intervals(intervals):
        st = _parse_dt(row["start"])
        en = _parse_dt(row["end"])
        if not st or not en:
            continue
        en_local = en.astimezone(now.tzinfo) if en.tzinfo else en
        if en_local > now:
            # Still asleep — treat "now" as provisional end only if started already
            if st.astimezone(now.tzinfo) <= now:
                # actively sleeping: charge toward full; wake not yet
                continue
            continue
        if best_end is None or en_local > best_end:
            best_end = en_local
            best = {
                "start": st,
                "end": en_local,
                "hours": (en_local - st.astimezone(now.tzinfo)).total_seconds() / 3600.0,
                "source": row.get("source"),
            }
    return best


def _active_sleep(
    intervals: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any] | None:
    """Sleep interval containing now (currently asleep)."""
    for row in normalize_intervals(intervals):
        st = _parse_dt(row["start"])
        en = _parse_dt(row["end"])
        if not st or not en:
            continue
        st_l = st.astimezone(now.tzinfo)
        en_l = en.astimezone(now.tzinfo)
        if st_l <= now <= en_l:
            return {
                "start": st_l,
                "end": en_l,
                "hours_so_far": (now - st_l).total_seconds() / 3600.0,
                "planned_hours": (en_l - st_l).total_seconds() / 3600.0,
                "source": row.get("source"),
            }
    return None


def hours_asleep_in_window(
    intervals: list[dict[str, Any]] | None,
    *,
    now: datetime,
    window_hours: float = 24.0,
) -> tuple[float, list[dict[str, Any]]]:
    """Secondary metric: hours asleep in trailing window."""
    window = max(0.1, float(window_hours))
    win_start = now - timedelta(hours=window)
    win_end = now
    segs: list[dict[str, Any]] = []
    total_sec = 0.0
    for row in normalize_intervals(intervals):
        st = _parse_dt(row["start"])
        en = _parse_dt(row["end"])
        if not st or not en:
            continue
        if en > win_end:
            en = win_end
        if st >= win_end or en <= win_start:
            continue
        ov = _overlap_seconds(st, en, win_start, win_end)
        if ov <= 0:
            continue
        total_sec += ov
        segs.append(
            {
                "start": max(st, win_start).isoformat(timespec="seconds"),
                "end": min(en, win_end).isoformat(timespec="seconds"),
                "hours": round(ov / 3600.0, 3),
                "source": row.get("source"),
            }
        )
    return total_sec / 3600.0, segs


def compute_sleep_battery(
    intervals: list[dict[str, Any]] | None,
    *,
    now: datetime | None = None,
    sleep_target_hours: float = 8.0,
    awake_hours: float | None = None,
    window_hours: float = 24.0,
    target_hours: float | None = None,  # alias for sleep_target_hours (compat)
) -> dict[str, Any]:
    """Wake-full / drain-over-awake battery.

    pct_charged: 100% at last wake, 0% after ``awake_hours`` (default 16 =
    24 − sleep_target).
    """
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()

    if target_hours is not None:
        sleep_target_hours = float(target_hours)
    sleep_target = max(0.5, float(sleep_target_hours))
    # Default awake budget = rest of the day for 24h rhythm
    if awake_hours is None:
        awake_hours = max(1.0, 24.0 - sleep_target)
    else:
        awake_hours = max(1.0, float(awake_hours))

    intervals_n = normalize_intervals(intervals)
    asleep_24h, segs = hours_asleep_in_window(
        intervals_n, now=now, window_hours=window_hours
    )

    active = _active_sleep(intervals_n, now)
    last = _latest_completed_sleep(intervals_n, now)

    # Timeline for UI: from last wake (or now-awake) across awake period
    mode = "no_data"
    last_wake_at: datetime | None = None
    last_sleep_hours: float | None = None
    hours_awake = 0.0
    hours_until_empty = awake_hours
    pct = 0.0
    level = "critical"
    summary = "No sleep intervals — sync Google Health or log sleep"

    if active is not None:
        # Currently asleep: battery recharging toward full at planned wake
        mode = "sleeping"
        so_far = float(active["hours_so_far"])
        planned = max(0.1, float(active["planned_hours"]))
        # Charge: 0% at sleep start → 100% at wake (or high if oversleeping)
        charge = min(100.0, (so_far / planned) * 100.0)
        # While sleeping show charge progress (inverse of drain metaphor for UI fill)
        pct = charge
        level = "full" if charge >= 90 else ("ok" if charge >= 50 else "low")
        last_wake_at = active["end"]  # expected wake
        last_sleep_hours = so_far
        hours_awake = 0.0
        hours_until_empty = awake_hours
        summary = (
            f"Sleeping — recharging ({so_far:.1f}h so far). "
            f"Battery full at wake (~{active['end'].strftime('%H:%M')})."
        )
    elif last is not None:
        mode = "awake"
        last_wake_at = last["end"]
        last_sleep_hours = float(last["hours"])
        hours_awake = max(0.0, (now - last_wake_at).total_seconds() / 3600.0)
        # Linear drain: full at wake, empty after awake_hours
        remaining_frac = 1.0 - (hours_awake / awake_hours)
        pct = max(0.0, min(100.0, remaining_frac * 100.0))
        hours_until_empty = max(0.0, awake_hours - hours_awake)
        if pct <= 0:
            level = "critical"
            summary = (
                f"Battery empty — {hours_awake:.1f}h awake since wake "
                f"({last_wake_at.strftime('%a %H:%M')}). Time to sleep and recharge "
                f"(~{sleep_target:g}h target for 7d avg)."
            )
        elif pct < 25:
            level = "critical"
            summary = (
                f"Battery low ({pct:.0f}%) — {hours_until_empty:.1f}h until empty. "
                f"Plan bedtime soon (woke {last_wake_at.strftime('%H:%M')}, "
                f"last sleep {last_sleep_hours:.1f}h)."
            )
        elif pct < 50:
            level = "low"
            summary = (
                f"Battery {pct:.0f}% — {hours_awake:.1f}h awake, "
                f"{hours_until_empty:.1f}h of wake budget left "
                f"(16h awake for 8h sleep rhythm)."
            )
        elif pct < 85:
            level = "ok"
            summary = (
                f"Battery {pct:.0f}% — {hours_awake:.1f}h since wake "
                f"({last_wake_at.strftime('%H:%M')}). "
                f"~{hours_until_empty:.1f}h until drained."
            )
        else:
            level = "full"
            summary = (
                f"Battery full ({pct:.0f}%) — recently woke "
                f"({last_wake_at.strftime('%H:%M')}, slept {last_sleep_hours:.1f}h). "
                f"Drains over the next {awake_hours:g}h awake."
            )
    # else no_data defaults above

    # Drain rate for UI: % lost per hour while awake
    drain_pct_per_hour = 100.0 / awake_hours if awake_hours else 0.0

    win_start = now - timedelta(hours=window_hours)
    return {
        "model": "wake_full_drain_awake",
        "mode": mode,
        "awake_budget_hours": round(awake_hours, 2),
        "sleep_target_hours": round(sleep_target, 2),
        # Primary battery charge (what the fill bar shows)
        "pct_charged": round(pct, 1),
        "pct_of_target": round(pct, 1),  # UI compat: fill uses this field
        "level": level,
        "hours_awake": round(hours_awake, 2),
        "hours_until_empty": round(hours_until_empty, 2),
        "drain_pct_per_hour": round(drain_pct_per_hour, 2),
        "last_wake_at": last_wake_at.isoformat(timespec="seconds") if last_wake_at else None,
        "last_sleep_hours": round(last_sleep_hours, 2) if last_sleep_hours is not None else None,
        "empty_at": (
            (last_wake_at + timedelta(hours=awake_hours)).isoformat(timespec="seconds")
            if last_wake_at and mode == "awake"
            else None
        ),
        # Secondary: trailing window sleep (for context / 7d alignment messaging)
        "asleep_hours": round(asleep_24h, 2),
        "asleep_minutes": int(round(asleep_24h * 60)),
        "target_hours": round(sleep_target, 2),
        "window_hours": window_hours,
        "window_start": win_start.isoformat(timespec="seconds"),
        "window_end": now.isoformat(timespec="seconds"),
        "segments_in_window": segs,
        # Compat: hours of awake budget consumed per clock hour while awake
        "discharge_next_hour_hours": 1.0 if mode == "awake" else 0.0,
        "summary": summary,
    }


def sleep_battery_for_state(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    target_hours: float | None = None,
) -> dict[str, Any]:
    """Build battery from state.sleep_intervals, falling back to daily logs."""
    state = state or {}
    intervals = list(state.get("sleep_intervals") or [])
    source = "sleep_intervals"
    if not intervals:
        intervals = intervals_from_daily_logs(list(state.get("logs") or []))
        source = "daily_log_approx" if intervals else "none"

    if target_hours is None:
        target_hours = 8.0
        for t in state.get("targets") or []:
            if str(t.get("id")) == "sleep" and t.get("target") is not None:
                try:
                    target_hours = float(t["target"])
                except (TypeError, ValueError):
                    pass
                break

    # Allow target-level override for awake budget
    awake = None
    for t in state.get("targets") or []:
        if str(t.get("id")) == "sleep" and t.get("awake_hours") is not None:
            try:
                awake = float(t["awake_hours"])
            except (TypeError, ValueError):
                pass
            break

    battery = compute_sleep_battery(
        intervals,
        now=now,
        sleep_target_hours=float(target_hours),
        awake_hours=awake,
    )
    battery["data_source"] = source
    battery["interval_count_stored"] = len(state.get("sleep_intervals") or [])
    return battery


def merge_sleep_intervals(
    state: dict[str, Any], new_intervals: list[dict[str, Any]]
) -> dict[str, Any]:
    """Union new intervals into state (deduped)."""
    out = deepcopy(state) if state else {}
    existing = list(out.get("sleep_intervals") or [])
    out["sleep_intervals"] = normalize_intervals(existing + list(new_intervals or []))
    return out
