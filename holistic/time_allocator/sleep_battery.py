"""Rolling 24h sleep battery: hours asleep in [now − 24h, now].

Unlike a calendar-day total (binary-ish “did I sleep last night?”), this
intersects real sleep intervals with a moving window so:

  - new sleep *charges* the battery while (and after) you sleep
  - as the window’s trailing edge advances past old sleep, those hours
    *discharge* one continuous second at a time (practically: recompute at now)

Example: slept 22:00→06:00. At 18:00 the same day the full 8h is still inside
the window. After 22:00 the next evening, hours start falling off (by 23:00
only 7h remain from that night).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


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
        # Cap absurd intervals (>24h single segment)
        if (en - st).total_seconds() > 36 * 3600:
            continue
        out.append(
            {
                "start": st.isoformat(timespec="seconds"),
                "end": en.isoformat(timespec="seconds"),
                "source": str(row.get("source") or "unknown"),
            }
        )
    # Merge duplicates by start/end
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

    Places each night ending at `assume_wake_local_hour` local and extending
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
        # Wake at assume_wake on that calendar date (local)
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


def compute_sleep_battery(
    intervals: list[dict[str, Any]] | None,
    *,
    now: datetime | None = None,
    window_hours: float = 24.0,
    target_hours: float = 8.0,
) -> dict[str, Any]:
    """Hours asleep inside the trailing window [now − window, now]."""
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()

    window = max(0.1, float(window_hours))
    target = max(0.1, float(target_hours))
    win_start = now - timedelta(hours=window)
    win_end = now

    segs: list[dict[str, Any]] = []
    total_sec = 0.0
    for row in normalize_intervals(intervals):
        st = _parse_dt(row["start"])
        en = _parse_dt(row["end"])
        if not st or not en:
            continue
        # Clip open-ended future sleep at "now" (still asleep)
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

    hours = total_sec / 3600.0
    pct = min(100.0, (hours / target) * 100.0)
    # Discharge rate if no more sleep: hours leave as window moves (max 1h/h)
    # Estimate hours that will leave in the next hour (overlap of intervals with
    # [win_start, win_start+1h)).
    leave_sec = 0.0
    leave_end = win_start + timedelta(hours=1)
    for row in normalize_intervals(intervals):
        st = _parse_dt(row["start"])
        en = _parse_dt(row["end"])
        if not st or not en:
            continue
        leave_sec += _overlap_seconds(st, en, win_start, leave_end)
    discharge_next_hour = leave_sec / 3600.0

    level = "critical" if hours < target * 0.5 else (
        "low" if hours < target * 0.75 else (
            "ok" if hours < target else "full"
        )
    )

    return {
        "window_hours": window,
        "window_start": win_start.isoformat(timespec="seconds"),
        "window_end": win_end.isoformat(timespec="seconds"),
        "asleep_hours": round(hours, 2),
        "asleep_minutes": int(round(total_sec / 60.0)),
        "target_hours": target,
        "pct_of_target": round(pct, 1),
        "level": level,
        "segments_in_window": segs,
        "discharge_next_hour_hours": round(discharge_next_hour, 2),
        "summary": (
            f"{hours:.1f}h asleep in last {window:g}h "
            f"(target {target:g}h · {pct:.0f}%)"
        ),
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

    # Target from sleep rolling_avg target if present
    if target_hours is None:
        target_hours = 8.0
        for t in state.get("targets") or []:
            if str(t.get("id")) == "sleep" and t.get("target") is not None:
                try:
                    target_hours = float(t["target"])
                except (TypeError, ValueError):
                    pass
                break

    battery = compute_sleep_battery(
        intervals, now=now, window_hours=24.0, target_hours=float(target_hours)
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
