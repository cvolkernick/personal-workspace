"""Sleep battery for FitDash recovery (ported from holistic time allocator).

Model: 100% at last wake, linear drain over awake budget (default 16h =
24 − 8h sleep target). Unlogged / zero nights do not create wake cycles.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from .models import SleepSample


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


def normalize_intervals(raw: Optional[List[dict]]) -> List[dict]:
    out: List[dict] = []
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
    seen: set = set()
    uniq: List[dict] = []
    for r in sorted(out, key=lambda x: x["start"]):
        key = (r["start"], r["end"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def intervals_from_daily_sleep(
    sleep: Sequence[SleepSample],
    *,
    assume_wake_local_hour: int = 7,
    tz: Optional[timezone] = None,
) -> List[dict]:
    """Approximate sleep intervals from per-day hour totals (FitDash shape).

    Places each night ending at ``assume_wake_local_hour`` and extending
    backward by logged hours. Skips 0h / implied-zero nights.
    """
    tz = tz or datetime.now().astimezone().tzinfo or timezone.utc
    out: List[dict] = []
    for s in sleep or []:
        hours = float(s.sleep_hours or 0)
        if hours <= 0:
            continue
        if (s.source or "") == "implied_zero":
            continue
        day = str(s.date or "")[:10]
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
                "source": str(s.source or "daily_sleep"),
            }
        )
    return normalize_intervals(out)


def _latest_completed_sleep(
    intervals: List[dict], now: datetime
) -> Optional[dict]:
    best = None
    best_end: Optional[datetime] = None
    for row in normalize_intervals(intervals):
        st = _parse_dt(row["start"])
        en = _parse_dt(row["end"])
        if not st or not en:
            continue
        en_local = en.astimezone(now.tzinfo) if en.tzinfo else en
        if en_local > now:
            continue
        st_l = st.astimezone(now.tzinfo)
        if best_end is None or en_local > best_end:
            best_end = en_local
            best = {
                "start": st_l,
                "end": en_local,
                "hours": (en_local - st_l).total_seconds() / 3600.0,
                "source": row.get("source"),
            }
    return best


def _active_sleep(intervals: List[dict], now: datetime) -> Optional[dict]:
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


def compute_sleep_battery(
    intervals: Optional[List[dict]],
    *,
    now: Optional[datetime] = None,
    sleep_target_hours: float = 8.0,
    awake_hours: Optional[float] = None,
) -> Dict[str, Any]:
    """Wake-full / drain-over-awake battery (same model as holistic)."""
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()

    sleep_target = max(0.5, float(sleep_target_hours))
    if awake_hours is None:
        awake_hours = max(1.0, 24.0 - sleep_target)
    else:
        awake_hours = max(1.0, float(awake_hours))

    intervals_n = normalize_intervals(intervals)
    active = _active_sleep(intervals_n, now)
    last = _latest_completed_sleep(intervals_n, now)

    mode = "no_data"
    last_wake_at: Optional[datetime] = None
    last_sleep_hours: Optional[float] = None
    hours_awake = 0.0
    hours_until_empty = awake_hours
    pct = 0.0
    level = "critical"
    summary = "No sleep cycle — sync Google Health"

    if active is not None:
        mode = "sleeping"
        so_far = float(active["hours_so_far"])
        planned = max(0.1, float(active["planned_hours"]))
        charge = min(100.0, (so_far / planned) * 100.0)
        pct = charge
        level = "full" if charge >= 90 else ("ok" if charge >= 50 else "low")
        last_wake_at = active["end"]
        last_sleep_hours = so_far
        hours_awake = 0.0
        hours_until_empty = awake_hours
        summary = f"Sleeping · recharging ({so_far:.1f}h)"
    elif last is not None:
        mode = "awake"
        last_wake_at = last["end"]
        last_sleep_hours = float(last["hours"])
        hours_awake = max(0.0, (now - last_wake_at).total_seconds() / 3600.0)
        remaining_frac = 1.0 - (hours_awake / awake_hours)
        pct = max(0.0, min(100.0, remaining_frac * 100.0))
        hours_until_empty = max(0.0, awake_hours - hours_awake)
        if pct <= 0:
            level = "critical"
            summary = f"Empty · {hours_awake:.1f}h awake — sleep soon"
        elif pct < 25:
            level = "critical"
            summary = f"{pct:.0f}% · {hours_until_empty:.1f}h left"
        elif pct < 50:
            level = "low"
            summary = f"{pct:.0f}% · {hours_awake:.1f}h awake"
        elif pct < 85:
            level = "ok"
            summary = f"{pct:.0f}% · {hours_until_empty:.1f}h until empty"
        else:
            level = "full"
            summary = f"{pct:.0f}% · woke {last_wake_at.strftime('%H:%M')}"

    return {
        "model": "wake_full_drain_awake",
        "mode": mode,
        "awake_budget_hours": round(awake_hours, 2),
        "sleep_target_hours": round(sleep_target, 2),
        "pct_charged": round(pct, 1),
        "level": level,
        "hours_awake": round(hours_awake, 2),
        "hours_until_empty": round(hours_until_empty, 2),
        "last_wake_at": last_wake_at.isoformat(timespec="seconds") if last_wake_at else None,
        "last_sleep_hours": round(last_sleep_hours, 2)
        if last_sleep_hours is not None
        else None,
        "empty_at": (
            (last_wake_at + timedelta(hours=awake_hours)).isoformat(timespec="seconds")
            if last_wake_at and mode == "awake"
            else None
        ),
        "summary": summary,
        "interval_count": len(intervals_n),
    }


def sleep_battery_from_fitdash_sleep(
    sleep: Sequence[SleepSample],
    *,
    now: Optional[datetime] = None,
    sleep_target_hours: float = 8.0,
    sleep_intervals: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """Build battery preferring timed Google intervals (Time Allocator style).

    Falls back to daily-hour approximation (fixed 7am wake) only when no
    timed intervals are available — that approximation is known to skew
    hours_awake / % when real wake is much later than 7am.

    When timed intervals exist but lag behind newer daily sleep totals,
    append daily approximations only for nights that end *after* the last
    timed wake so a stale interval set cannot strand the battery on an
    old wake cycle.
    """
    intervals = normalize_intervals(list(sleep_intervals or []))
    source = "sleep_intervals"
    if not intervals:
        intervals = intervals_from_daily_sleep(sleep)
        source = "daily_sleep_approx" if intervals else "none"
    else:
        last_end: Optional[datetime] = None
        for row in intervals:
            en = _parse_dt(row.get("end"))
            if en and (last_end is None or en > last_end):
                last_end = en
        daily = intervals_from_daily_sleep(sleep)
        filled = 0
        for row in daily:
            en = _parse_dt(row.get("end"))
            if not en or last_end is None:
                continue
            # Only nights whose wake is strictly after last timed wake
            if en > last_end:
                intervals.append(row)
                filled += 1
        if filled:
            intervals = normalize_intervals(intervals)
            source = "sleep_intervals+daily_fill"
    bat = compute_sleep_battery(
        intervals, now=now, sleep_target_hours=sleep_target_hours
    )
    bat["data_source"] = source
    bat["interval_count"] = len(intervals)
    # Align field name with Time Allocator UI/meta
    bat["interval_count_stored"] = len(intervals)
    bat["pct_of_target"] = bat.get("pct_charged")
    return bat
