"""Sleep battery for FitDash recovery (ported from holistic time allocator).

Model: proportional charge at last wake from last-night sleep vs target, then
linear drain at full-budget rate (100% / awake_budget per hour). Short nights
start below 100% so empty_at arrives earlier — soft-capped so one bad night
cannot pull bedtime more than ``max_earlier_hours`` (default 2h) early.

Awake budget is not ``24 − sleep_target``. An 8h sleep target reserves **9h
around sleep** (30 min wind-down + 30 min sleep onset) so ``empty_at`` is
when wind-down should start, not when you must already be asleep.

Multi-day sleep debt stays in recovery/coach (zero-filled nights), not here.
Unlogged / zero nights do not create wake cycles.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from .models import SleepSample

# Soft cap: never empty more than this many hours earlier than a full charge.
DEFAULT_MAX_EARLIER_HOURS = 2.0
DEFAULT_SLEEP_TARGET_HOURS = 8.0
# 30 min wind-down + 30 min falling asleep. Empty = start of this block.
DEFAULT_ONSET_BUFFER_HOURS = 1.0
DEFAULT_AWAKE_BUDGET_HOURS = (
    24.0 - DEFAULT_SLEEP_TARGET_HOURS - DEFAULT_ONSET_BUFFER_HOURS
)  # 15.0


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
    now: Optional[datetime] = None,
) -> List[dict]:
    """Approximate sleep intervals from per-day hour totals (FitDash shape).

    Places each night ending at ``assume_wake_local_hour`` and extending
    backward by logged hours. Skips 0h / implied-zero nights.

    A synthesized wake still in the future is omitted — partial same-day
    totals must not create a completed 7am cycle before 7am.
    """
    if tz is None:
        from .timeutil import local_tz

        tz = local_tz()
    if now is None:
        from .timeutil import local_now

        now = local_now()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
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
        if wake > now:
            continue
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
        # End exclusive: at wake timestamp the cycle is complete (awake).
        if st_l <= now < en_l:
            return {
                "start": st_l,
                "end": en_l,
                "hours_so_far": (now - st_l).total_seconds() / 3600.0,
                "planned_hours": (en_l - st_l).total_seconds() / 3600.0,
                "source": row.get("source"),
            }
    return None


def start_charge_fraction(
    last_sleep_hours: float,
    *,
    sleep_target_hours: float = DEFAULT_SLEEP_TARGET_HOURS,
    awake_budget_hours: float = DEFAULT_AWAKE_BUDGET_HOURS,
    max_earlier_hours: float = DEFAULT_MAX_EARLIER_HOURS,
) -> Dict[str, float]:
    """Map last-night sleep to start-of-day charge fraction (0–1).

    Proportional to last_sleep / target, floored so empty cannot arrive more
    than ``max_earlier_hours`` sooner than a full charge (same drain rate).
    """
    target = max(0.5, float(sleep_target_hours))
    awake = max(1.0, float(awake_budget_hours))
    last = max(0.0, float(last_sleep_hours))
    max_earlier = max(0.0, float(max_earlier_hours))

    proportional = min(1.0, last / target)
    # Floor: start_frac * awake >= awake - max_earlier
    if max_earlier <= 0:
        floor_frac = 0.0
    else:
        floor_frac = max(0.0, 1.0 - (max_earlier / awake))
    start_frac = max(proportional, floor_frac) if last < target else 1.0
    # Over-target still full
    if last >= target:
        start_frac = 1.0
        proportional = 1.0

    return {
        "start_frac": start_frac,
        "proportional_frac": proportional,
        "floor_frac": floor_frac,
        "max_earlier_hours": max_earlier,
        "budget_hours_at_start": start_frac * awake,
    }


def compute_sleep_battery(
    intervals: Optional[List[dict]],
    *,
    now: Optional[datetime] = None,
    sleep_target_hours: float = DEFAULT_SLEEP_TARGET_HOURS,
    awake_hours: Optional[float] = None,
    onset_buffer_hours: float = DEFAULT_ONSET_BUFFER_HOURS,
    max_earlier_hours: float = DEFAULT_MAX_EARLIER_HOURS,
) -> Dict[str, Any]:
    """Partial-charge-at-wake / drain-over-awake battery."""
    if now is None:
        from .timeutil import local_now

        now = local_now()
    elif now.tzinfo is None:
        from .timeutil import local_tz

        now = now.replace(tzinfo=timezone.utc).astimezone(local_tz())

    sleep_target = max(0.5, float(sleep_target_hours))
    onset_buffer = max(0.0, float(onset_buffer_hours))
    if awake_hours is None:
        awake_hours = max(1.0, 24.0 - sleep_target - onset_buffer)
    else:
        awake_hours = max(1.0, float(awake_hours))

    intervals_n = normalize_intervals(intervals)
    active = _active_sleep(intervals_n, now)
    last = _latest_completed_sleep(intervals_n, now)

    mode = "no_data"
    last_wake_at: Optional[datetime] = None
    planned_wake_at: Optional[datetime] = None
    last_sleep_hours: Optional[float] = None
    hours_awake = 0.0
    hours_until_empty = awake_hours
    pct = 0.0
    level = "critical"
    summary = "No sleep cycle — sync Google Health"
    start_frac = 1.0
    proportional_frac = 1.0
    floor_frac = 0.0
    charge_hours = awake_hours  # awake hours of battery at wake
    empty_at_dt: Optional[datetime] = None

    if active is not None:
        mode = "sleeping"
        so_far = float(active["hours_so_far"])
        planned = max(0.1, float(active["planned_hours"]))
        charge = min(100.0, (so_far / planned) * 100.0)
        pct = charge
        level = "full" if charge >= 90 else ("ok" if charge >= 50 else "low")
        # last_wake_at is only a wake that has already happened. Planned
        # end (Fitbit alarm / 7am approx) lives on planned_wake_at.
        planned_end = active["end"]
        planned_wake_at = planned_end if planned_end > now else None
        if last is not None and last["end"] <= now:
            last_wake_at = last["end"]
        last_sleep_hours = so_far
        hours_awake = 0.0
        hours_until_empty = awake_hours
        summary = f"Sleeping · recharging ({so_far:.1f}h)"
    elif last is not None:
        mode = "awake"
        last_wake_at = last["end"]
        last_sleep_hours = float(last["hours"])
        hours_awake = max(0.0, (now - last_wake_at).total_seconds() / 3600.0)

        ch = start_charge_fraction(
            last_sleep_hours,
            sleep_target_hours=sleep_target,
            awake_budget_hours=awake_hours,
            max_earlier_hours=max_earlier_hours,
        )
        start_frac = float(ch["start_frac"])
        proportional_frac = float(ch["proportional_frac"])
        floor_frac = float(ch["floor_frac"])
        charge_hours = float(ch["budget_hours_at_start"])

        # Same drain rate as full battery: 100%/awake_hours per hour of wall time
        # from start_frac*100 down to 0.
        remaining_frac = start_frac - (hours_awake / awake_hours)
        pct = max(0.0, min(100.0, remaining_frac * 100.0))
        hours_until_empty = max(0.0, charge_hours - hours_awake)
        empty_at_dt = last_wake_at + timedelta(hours=charge_hours)

        short = last_sleep_hours < sleep_target - 0.05
        partial_note = ""
        if short and start_frac < 0.999:
            partial_note = f" · started {start_frac * 100:.0f}% after {last_sleep_hours:.1f}h sleep"

        if pct <= 0:
            level = "critical"
            summary = f"Empty · {hours_awake:.1f}h awake — sleep soon"
        elif pct < 25:
            level = "critical"
            summary = f"{pct:.0f}% · {hours_until_empty:.1f}h left{partial_note}"
        elif pct < 50:
            level = "low"
            summary = f"{pct:.0f}% · {hours_awake:.1f}h awake{partial_note}"
        elif pct < 85:
            level = "ok"
            summary = f"{pct:.0f}% · {hours_until_empty:.1f}h until empty{partial_note}"
        else:
            level = "full"
            summary = f"{pct:.0f}% · woke {last_wake_at.strftime('%H:%M')}{partial_note}"

    return {
        "model": "wake_partial_drain_awake",
        "mode": mode,
        "awake_budget_hours": round(awake_hours, 2),
        "sleep_target_hours": round(sleep_target, 2),
        "onset_buffer_hours": round(onset_buffer, 2),
        "sleep_around_hours": round(sleep_target + onset_buffer, 2),
        "pct_charged": round(pct, 1),
        "start_pct_charged": round(start_frac * 100.0, 1) if mode == "awake" else None,
        "proportional_start_pct": (
            round(proportional_frac * 100.0, 1) if mode == "awake" else None
        ),
        "max_earlier_hours": round(float(max_earlier_hours), 2),
        "earlier_floor_pct": (
            round(floor_frac * 100.0, 1) if mode == "awake" else None
        ),
        "charge_budget_hours": round(charge_hours, 2) if mode == "awake" else None,
        "level": level,
        "hours_awake": round(hours_awake, 2),
        "hours_until_empty": round(hours_until_empty, 2),
        "last_wake_at": last_wake_at.isoformat(timespec="seconds") if last_wake_at else None,
        "planned_wake_at": (
            planned_wake_at.isoformat(timespec="seconds") if planned_wake_at else None
        ),
        "last_sleep_hours": round(last_sleep_hours, 2)
        if last_sleep_hours is not None
        else None,
        "empty_at": (
            empty_at_dt.isoformat(timespec="seconds")
            if empty_at_dt is not None
            else (
                (last_wake_at + timedelta(hours=awake_hours)).isoformat(
                    timespec="seconds"
                )
                if last_wake_at and mode == "awake"
                else None
            )
        ),
        "summary": summary,
        "interval_count": len(intervals_n),
    }


def sleep_battery_from_fitdash_sleep(
    sleep: Sequence[SleepSample],
    *,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
    sleep_target_hours: float = DEFAULT_SLEEP_TARGET_HOURS,
    onset_buffer_hours: float = DEFAULT_ONSET_BUFFER_HOURS,
    sleep_intervals: Optional[List[dict]] = None,
    max_earlier_hours: float = DEFAULT_MAX_EARLIER_HOURS,
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
    from .timeutil import local_now

    # Viewer TZ is the same clock eating_window_fraction will use.
    if now is None or tz_name:
        now = local_now(tz_name, now=now)
    elif now.tzinfo is None:
        from .timeutil import local_tz

        now = now.replace(tzinfo=timezone.utc).astimezone(local_tz(tz_name))

    intervals = normalize_intervals(list(sleep_intervals or []))
    source = "sleep_intervals"
    if not intervals:
        intervals = intervals_from_daily_sleep(sleep, tz=now.tzinfo, now=now)
        source = "daily_sleep_approx" if intervals else "none"
    else:
        last_end: Optional[datetime] = None
        for row in intervals:
            en = _parse_dt(row.get("end"))
            if en and (last_end is None or en > last_end):
                last_end = en
        daily = intervals_from_daily_sleep(sleep, tz=now.tzinfo, now=now)
        filled = 0
        for row in daily:
            en = _parse_dt(row.get("end"))
            if not en or last_end is None:
                continue
            # Only nights whose assumed wake already happened and is
            # strictly after last timed wake. Future 7am fills must not
            # become "woke Fri 7am" at 2am.
            if last_end < en <= now:
                intervals.append(row)
                filled += 1
        if filled:
            intervals = normalize_intervals(intervals)
            source = "sleep_intervals+daily_fill"
    bat = compute_sleep_battery(
        intervals,
        now=now,
        sleep_target_hours=sleep_target_hours,
        onset_buffer_hours=onset_buffer_hours,
        max_earlier_hours=max_earlier_hours,
    )
    bat["data_source"] = source
    bat["interval_count"] = len(intervals)
    # Align field name with Time Allocator UI/meta
    bat["interval_count_stored"] = len(intervals)
    bat["pct_of_target"] = bat.get("pct_charged")
    return bat
