"""Training day SoT: wake window / session-close, not civil midnight.

A training day is ``[last_wake_at, next_wake)``. Sessions that close in that
window belong to it. Civil-day charts may still bucket by ``session.date``.

When last_wake is missing or still in the future (planned alarm), callers
fall back to civil today — same honesty as calorie/hydration wake windows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Sequence


def parse_dt(value: Any) -> Optional[datetime]:
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


def last_wake_from(
    recovery: Any = None,
    sleep_battery: Any = None,
    last_wake_at: Any = None,
    payload: Any = None,
) -> Optional[str]:
    """Pull last_wake_at from the usual FitDash envelopes. Never invent a wake."""
    if last_wake_at not in (None, ""):
        return str(last_wake_at)
    if isinstance(sleep_battery, dict) and sleep_battery.get("last_wake_at"):
        return str(sleep_battery.get("last_wake_at"))
    if isinstance(recovery, dict):
        bat = recovery.get("sleep_battery")
        if isinstance(bat, dict) and bat.get("last_wake_at"):
            return str(bat.get("last_wake_at"))
        if recovery.get("last_wake_at"):
            return str(recovery.get("last_wake_at"))
    if isinstance(payload, dict):
        today = payload.get("today") if isinstance(payload.get("today"), dict) else {}
        wake_win = today.get("wake_window") if isinstance(today.get("wake_window"), dict) else {}
        return last_wake_from(
            recovery=payload.get("recovery"),
            sleep_battery=payload.get("sleep_battery") or wake_win,
            last_wake_at=payload.get("last_wake_at"),
        )
    return None


def _days_apart(a: str, b: str) -> Optional[int]:
    try:
        da = datetime.strptime(str(a)[:10], "%Y-%m-%d")
        db = datetime.strptime(str(b)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return abs((da - db).days)


def wake_covers_as_of(
    last_wake_at: Any,
    as_of: Optional[str],
    *,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> bool:
    """True when last_wake is this wake or yesterday (after-midnight).

    A last_wake weeks ago (stale sleep battery) must not pin today's letter
    to that old session. After-midnight finish is still delta 1.
    """
    train = training_day_iso(now=now, last_wake_at=last_wake_at, tz_name=tz_name)
    civil = str(as_of or "")[:10]
    if not civil:
        from .timeutil import local_today_iso

        civil = local_today_iso(tz_name, now=now)
    delta = _days_apart(train, civil)
    return delta is not None and delta <= 1


def wake_is_current(
    last_wake_at: Any,
    as_of: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> bool:
    """True when last_wake is known, not in the future, and covers as_of."""
    from .timeutil import local_now

    wake = parse_dt(last_wake_at)
    if wake is None:
        return False
    clock = local_now(tz_name, now=now)
    if wake.astimezone(clock.tzinfo) > clock:
        return False
    return wake_covers_as_of(
        last_wake_at, as_of, now=clock, tz_name=tz_name
    )


def day_complete_for_planning(
    train_parent_completed: bool,
    *,
    ppl_logged_today: Optional[str] = None,
    last_wake_at: Any = None,
    as_of: Optional[str] = None,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> bool:
    """``already_trained_today`` SoT for planner + stamp.

    Civil fallback (no current wake): Training parent complete is enough.
    When last_wake is current: parent complete alone must not pin — a PPL
    session has to have closed in that wake. After-midnight quest complete
    that still sits on the civil GT parent must not block the next letter.
    """
    if not train_parent_completed:
        return False
    if wake_is_current(last_wake_at, as_of, now=now, tz_name=tz_name):
        return bool(ppl_logged_today)
    return True


def training_day_iso(
    *,
    now: Optional[datetime] = None,
    last_wake_at: Any = None,
    tz_name: Optional[str] = None,
) -> str:
    """Civil date of the current training day (wake's local date).

    After midnight but before the next wake this is still yesterday's
    wake date. After a real sleep it is the new wake's date.
    """
    from .timeutil import local_now, local_today_iso

    clock = local_now(tz_name, now=now)
    wake = parse_dt(last_wake_at)
    if wake is None:
        return local_today_iso(tz_name, now=clock)
    wake_local = wake.astimezone(clock.tzinfo)
    if wake_local > clock:
        return local_today_iso(tz_name, now=clock)
    return wake_local.strftime("%Y-%m-%d")


def resolve_log_date(
    date: Any,
    *,
    now: Optional[datetime] = None,
    last_wake_at: Any = None,
    tz_name: Optional[str] = None,
) -> str:
    """Default + after-midnight remap onto the training day.

    An explicit date that is not civil today is kept (intentional backdate).
    Empty date, or civil-today while still on the prior wake, becomes the
    training day so late-night logs merge with that wake's session.
    """
    from .timeutil import local_today_iso

    raw = str(date or "").strip()[:10]
    train = training_day_iso(now=now, last_wake_at=last_wake_at, tz_name=tz_name)
    civil = local_today_iso(tz_name, now=now)
    covers = wake_covers_as_of(
        last_wake_at, civil, now=now, tz_name=tz_name
    )
    if not raw:
        return train if covers else civil
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return train if covers else civil
    if raw == civil and train != civil and covers:
        return train
    return raw


def session_close_dt(
    session: Any,
    *,
    tz_name: Optional[str] = None,
) -> Optional[datetime]:
    """When the session closed.

    Prefer ``closed_at`` / ``created_at``. Date-only rows land at local noon
    so they still sit inside a typical wake window (same as food logs).
    """
    from .timeutil import local_tz

    raw = None
    if isinstance(session, dict):
        raw = session.get("closed_at") or session.get("created_at")
        day = str(session.get("date") or "")[:10]
    else:
        raw = getattr(session, "closed_at", None) or getattr(session, "created_at", None)
        day = str(getattr(session, "date", "") or "")[:10]
    parsed = parse_dt(raw)
    if parsed is not None:
        return parsed
    if len(day) < 10:
        return None
    try:
        y, m, d = int(day[0:4]), int(day[5:7]), int(day[8:10])
        return datetime(y, m, d, 12, 0, 0, tzinfo=local_tz(tz_name))
    except ValueError:
        return None


def _session_type(session: Any) -> str:
    if isinstance(session, dict):
        return str(session.get("session_type") or session.get("type") or "").lower()
    return str(getattr(session, "session_type", "") or "").lower()


def session_in_wake(
    session: Any,
    *,
    last_wake_at: Any,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> bool:
    """True when session-close is in ``[last_wake, now]``."""
    from .timeutil import local_now

    wake = parse_dt(last_wake_at)
    if wake is None:
        return False
    clock = local_now(tz_name, now=now)
    wake_local = wake.astimezone(clock.tzinfo)
    if wake_local > clock:
        return False
    closed = session_close_dt(session, tz_name=tz_name)
    if closed is None:
        return False
    closed_local = closed.astimezone(clock.tzinfo)
    return wake_local <= closed_local <= clock


def ppl_logged_in_wake(
    sessions: Sequence[Any],
    *,
    last_wake_at: Any,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> Optional[str]:
    """PPL letter that closed in the current wake, if any. No second letter."""
    wake = parse_dt(last_wake_at)
    if wake is None:
        return None
    hits = []
    for s in sessions or []:
        st = _session_type(s)
        if st not in ("push", "pull", "legs"):
            continue
        if not session_in_wake(
            s, last_wake_at=last_wake_at, now=now, tz_name=tz_name
        ):
            continue
        closed = session_close_dt(s, tz_name=tz_name)
        hits.append((closed or datetime.min.replace(tzinfo=timezone.utc), st))
    if not hits:
        return None
    hits.sort(key=lambda item: item[0])
    return hits[0][1]


def ppl_logged_for_planning(
    sessions: Sequence[Any],
    *,
    as_of: Optional[str] = None,
    last_wake_at: Any = None,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> Optional[str]:
    """Planning pin: wake window when last_wake is known, else civil ``as_of``."""
    from .timeutil import local_now

    clock = local_now(tz_name, now=now)
    if wake_is_current(last_wake_at, as_of, now=clock, tz_name=tz_name):
        return ppl_logged_in_wake(
            sessions, last_wake_at=last_wake_at, now=clock, tz_name=tz_name
        )
    from .workout_planner import ppl_logged_on_day

    return ppl_logged_on_day(sessions or [], as_of)
