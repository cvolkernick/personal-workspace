"""Daily sleep/recovery quest scored from last completed overnight.

Prior night, not tonight. Wearable/interval evidence auto-completes the
leaf the way AZM does. Same-day naps can recover a short night without
rewriting last night as 8h. Missing overnight (GH lag) is pending, not
a 0h fail.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .sleep_battery import (
    DEFAULT_SLEEP_TARGET_HOURS,
    _parse_dt,
    normalize_intervals,
)

KIND_KEY = "sleep|sleep-recovery"
SLUG = "sleep-recovery"
GROUP = "sleep"

# Match sleep_battery's short-night epsilon (~3 min).
HIT_EPS_HOURS = 0.05
# Below this, a daytime interval is a nap, not last night.
MIN_OVERNIGHT_HOURS = 3.0
BATTERY_CRITICAL_PCT = 30.0

STANDARD_TITLE_PREFIX = "Sleep"

MOTIVATION = (
    "Last night's completed sleep vs the 8h target. A good night checks "
    "this at wake. A short night is nap / earlier bedtime / caffeine cutoff "
    "— not 'sleep 8h tonight.'"
)

CORRECTIVE = "Nap, earlier bedtime, caffeine cutoff."


def _civil_day(raw: Any) -> str:
    return str(raw or "")[:10]


def _as_float(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _hours_label(hours: float) -> str:
    return f"{max(0.0, float(hours)):.1f}h"


def _clock(raw: Any) -> str:
    dt = _parse_dt(raw)
    if dt is not None:
        h = dt.hour
        suffix = "AM" if h < 12 else "PM"
        return f"{h % 12 or 12}:{dt.minute:02d} {suffix}"
    s = str(raw or "")
    if len(s) >= 16 and s[11] == ":":
        try:
            h = int(s[11:13])
            mm = s[14:16]
            suffix = "AM" if h < 12 else "PM"
            return f"{h % 12 or 12}:{mm} {suffix}"
        except ValueError:
            return s[11:16]
    return ""


def _now_from_board(today: Optional[dict], as_of: str) -> datetime:
    board = today if isinstance(today, dict) else {}
    raw = board.get("now") or (board.get("sleep_battery") or {}).get("as_of")
    dt = _parse_dt(raw)
    if dt is not None:
        return dt
    day = _civil_day(as_of or board.get("date"))
    if len(day) == 10:
        try:
            y, m, d = int(day[0:4]), int(day[5:7]), int(day[8:10])
            return datetime(y, m, d, 12, 0, 0, tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _is_overnight(start: datetime, end: datetime, hours: float) -> bool:
    if hours >= 5.0:
        return True
    sh, eh = start.hour, end.hour
    started_night = sh >= 20 or sh < 10
    ended_morning = 4 <= eh <= 12
    if (started_night or ended_morning) and hours >= 1.0:
        return True
    return hours >= MIN_OVERNIGHT_HOURS and not (10 <= sh <= 17)


def _completed_rows(
    raw: Optional[Sequence[Any]], now: datetime
) -> List[dict]:
    rows: List[dict] = []
    for row in normalize_intervals(list(raw or [])):
        st = _parse_dt(row.get("start"))
        en = _parse_dt(row.get("end"))
        if not st or not en or en > now:
            continue
        st_l = st.astimezone(now.tzinfo) if st.tzinfo else st
        en_l = en.astimezone(now.tzinfo) if en.tzinfo else en
        hours = (en_l - st_l).total_seconds() / 3600.0
        if hours <= 0:
            continue
        rows.append({"start": st_l, "end": en_l, "hours": hours})
    rows.sort(key=lambda r: r["end"])
    return rows


def _pick_overnight(rows: Sequence[dict]) -> Optional[dict]:
    nights = [
        r
        for r in rows
        if _is_overnight(r["start"], r["end"], float(r["hours"]))
    ]
    if not nights:
        return None
    return nights[-1]


def _intervals_from_board(
    today: Optional[dict], battery: Optional[dict]
) -> List[dict]:
    board = today if isinstance(today, dict) else {}
    bat = battery if isinstance(battery, dict) else {}
    for blob in (board, bat, board.get("sleep") if isinstance(board.get("sleep"), dict) else {}):
        if not isinstance(blob, dict):
            continue
        for key in ("sleep_intervals", "intervals"):
            raw = blob.get(key)
            if isinstance(raw, (list, tuple)) and raw:
                return list(raw)
    health = board.get("health") if isinstance(board.get("health"), dict) else {}
    raw = health.get("sleep_intervals")
    if isinstance(raw, (list, tuple)) and raw:
        return list(raw)
    return []


def _battery_from_board(today: Optional[dict]) -> dict:
    board = today if isinstance(today, dict) else {}
    bat = board.get("sleep_battery")
    if isinstance(bat, dict):
        return bat
    rec = board.get("recovery") if isinstance(board.get("recovery"), dict) else {}
    nested = rec.get("sleep_battery")
    return nested if isinstance(nested, dict) else {}


def _met_target(hours: float, target: float) -> bool:
    return float(hours) + 1e-9 >= float(target) - HIT_EPS_HOURS


def sleep_title(
    *,
    status: str,
    last_night: Optional[float],
    extra: float,
    target: float,
    empty_at: Any = None,
    battery_critical: bool = False,
) -> str:
    tgt = _hours_label(target)
    if status == "pending":
        return "Sleep — waiting on last night (Google Health lag)"
    last = _hours_label(float(last_night or 0.0))
    if status == "hit":
        return f"{STANDARD_TITLE_PREFIX} — {last} / {tgt} last night"
    extra_h = max(0.0, float(extra))
    if status == "recovered":
        total = _hours_label(float(last_night or 0.0) + extra_h)
        nap = _hours_label(extra_h)
        return (
            f"{STANDARD_TITLE_PREFIX} — recovered {total} "
            f"({last} last night + {nap} nap) / {tgt}"
        )
    # short
    bits = [f"{STANDARD_TITLE_PREFIX} — {last} / {tgt} last night"]
    if extra_h > 0.05:
        bits = [
            f"{STANDARD_TITLE_PREFIX} — {last} last night + {_hours_label(extra_h)} nap / {tgt}"
        ]
    if battery_critical:
        clock = _clock(empty_at)
        if clock:
            bits.append(f"Battery empty ~{clock} — {CORRECTIVE.rstrip('.')}")
        else:
            bits.append(f"Battery empty — {CORRECTIVE.rstrip('.')}")
    else:
        bits.append(CORRECTIVE.rstrip("."))
    return ". ".join(bits) + "."


def score_sleep(
    *,
    last_sleep_hours: Optional[float],
    last_wake_at: Any = None,
    intervals: Optional[Sequence[Any]] = None,
    sleep_target_hours: float = DEFAULT_SLEEP_TARGET_HOURS,
    mode: str = "",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Split last completed overnight vs same-day naps. Never invents 0h."""
    target = max(0.5, float(sleep_target_hours or DEFAULT_SLEEP_TARGET_HOURS))
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    rows = _completed_rows(intervals, now)
    overnight = _pick_overnight(rows) if rows else None
    last_night: Optional[float] = None
    extra = 0.0
    overnight_end = None

    if overnight is not None:
        last_night = float(overnight["hours"])
        overnight_end = overnight["end"]
        extra = sum(
            float(r["hours"])
            for r in rows
            if r["start"] >= overnight_end and r is not overnight
        )
    elif rows:
        # Only naps / fragments — do not treat missing overnight as 0h.
        last_night = None
        extra = sum(float(r["hours"]) for r in rows)
    else:
        # Battery last_sleep_hours is last completed interval. During an
        # active sleep it is hours-so-far, not last night — ignore then.
        if str(mode or "").strip().lower() != "sleeping":
            hrs = _as_float(last_sleep_hours)
            if hrs is not None and hrs > 0:
                last_night = hrs
        extra = 0.0
        overnight_end = _parse_dt(last_wake_at)

    if last_night is None:
        return {
            "status": "pending",
            "last_night_hours": None,
            "extra_hours": round(extra, 2),
            "total_hours": round(extra, 2) if extra else None,
            "target_hours": round(target, 2),
            "hit": False,
            "overnight_end": overnight_end.isoformat(timespec="seconds")
            if overnight_end
            else None,
        }

    total = last_night + extra
    if _met_target(last_night, target):
        status = "hit"
    elif extra > 0.05 and _met_target(total, target):
        status = "recovered"
    else:
        status = "short"
    return {
        "status": status,
        "last_night_hours": round(last_night, 2),
        "extra_hours": round(extra, 2),
        "total_hours": round(total, 2),
        "target_hours": round(target, 2),
        "hit": status in ("hit", "recovered"),
        "overnight_end": overnight_end.isoformat(timespec="seconds")
        if overnight_end
        else None,
    }


def sleep_spec(
    today: Optional[dict] = None,
    *,
    sleep_battery: Optional[dict] = None,
    intervals: Optional[Sequence[Any]] = None,
    as_of: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Stable sleep|sleep-recovery prescription for this civil day."""
    board = today if isinstance(today, dict) else {}
    bat = sleep_battery if isinstance(sleep_battery, dict) else _battery_from_board(board)
    day = _civil_day(as_of or board.get("date"))
    iv = (
        list(intervals)
        if intervals is not None
        else _intervals_from_board(board, bat)
    )
    clock = now or _now_from_board(board, day)
    target = _as_float(bat.get("sleep_target_hours")) or DEFAULT_SLEEP_TARGET_HOURS
    scored = score_sleep(
        last_sleep_hours=_as_float(bat.get("last_sleep_hours")),
        last_wake_at=bat.get("last_wake_at"),
        intervals=iv,
        sleep_target_hours=target,
        mode=str(bat.get("mode") or ""),
        now=clock,
    )
    pct = _as_float(bat.get("pct_charged"))
    critical = (
        scored["status"] == "short"
        and pct is not None
        and pct < BATTERY_CRITICAL_PCT
        and str(bat.get("mode") or "") == "awake"
    )
    title = sleep_title(
        status=scored["status"],
        last_night=scored["last_night_hours"],
        extra=scored["extra_hours"],
        target=scored["target_hours"],
        empty_at=bat.get("empty_at"),
        battery_critical=critical,
    )
    priority = 5
    if scored["status"] == "pending":
        priority = 4
    elif scored["status"] == "short":
        priority = 2 if critical else 3
    return {
        "kind": KIND_KEY,
        "slug": SLUG,
        "group": GROUP,
        "date": day or None,
        "status": scored["status"],
        "last_night_hours": scored["last_night_hours"],
        "extra_hours": scored["extra_hours"],
        "total_hours": scored["total_hours"],
        "target_hours": scored["target_hours"],
        "hit": bool(scored["hit"]),
        "battery_critical": critical,
        "title": title,
        "motivation": MOTIVATION,
        "priority": priority,
    }
