"""Training day SoT: wake window / session-close, not civil midnight.

A training day is ``[last_wake_at, next_wake)``. Sessions that close in that
window belong to it. Civil-day charts may still bucket by ``session.date``.

When last_wake is missing or still in the future (planned alarm), callers
fall back to civil today — same honesty as calorie/hydration wake windows.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional, Sequence

_LIFT_SLUG_RE = re.compile(r"[^a-z0-9]+")
_TITLE_DETAIL_RE = re.compile(r"^(?P<name>.+?)\s+\((?P<detail>[^)]*)\)\s*$")
_WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*lb", re.I)
_SETS_REPS_RE = re.compile(r"(\d+)\s*[×xX]\s*(\d+)")


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


# Training-day boundary (#1304). Lift logs and session closes after civil
# midnight but before this local clock time count toward the previous
# training day for split rotation, quests and the plan. Change it here only.
# The zone is the viewer zone (``timeutil.resolve_tz_name``), which falls back
# to America/New_York.
TRAINING_DAY_CUTOFF = time(4, 0)


def training_day_for(
    value: Any, *, tz_name: Optional[str] = None
) -> Optional[str]:
    """Training day (YYYY-MM-DD) of one instant, using ``TRAINING_DAY_CUTOFF``.

    00:30 local belongs to the previous day. 04:00 and later is the new day.
    A naive datetime is treated as UTC, the same as ``parse_dt``.
    """
    from .timeutil import local_tz

    dt = parse_dt(value)
    if dt is None:
        return None
    local = dt.astimezone(local_tz(tz_name))
    day = local.date()
    if local.time() < TRAINING_DAY_CUTOFF:
        day = day - timedelta(days=1)
    return day.isoformat()


def session_training_day(session: Any, *, tz_name: Optional[str] = None) -> str:
    """Training day a session counts toward.

    A close stamp (``closed_at`` / ``created_at``) before the cutoff moves the
    row to the previous day. A date-only row keeps its ``date``. A stamp on a
    different civil date from ``date`` (a back-dated picker log) keeps ``date``.
    """
    if isinstance(session, dict):
        raw = session.get("closed_at") or session.get("created_at")
        day = str(session.get("date") or "")[:10]
    else:
        raw = getattr(session, "closed_at", None) or getattr(session, "created_at", None)
        day = str(getattr(session, "date", "") or "")[:10]
    if not raw:
        return day
    from .timeutil import local_tz

    dt = parse_dt(raw)
    if dt is None:
        return day
    civil = dt.astimezone(local_tz(tz_name)).date().isoformat()
    if day and civil != day:
        return day
    return training_day_for(dt, tz_name=tz_name) or day


# Civil morning with yesterday's wake still advertised (sleep battery lag)
# must not keep last night's close in the planning window (#914). After
# midnight before dawn, delta 1 still counts so a 00:30 finish stays on
# the prior training day.
_AFTER_MIDNIGHT_END_HOUR = TRAINING_DAY_CUTOFF.hour


def wake_covers_as_of(
    last_wake_at: Any,
    as_of: Optional[str],
    *,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> bool:
    """True when last_wake is this wake, or yesterday only before dawn.

    A last_wake weeks ago (stale sleep battery) must not pin today's letter
    to that old session. After-midnight finish (delta 1, before TRAINING_DAY_CUTOFF) still
    counts. A civil morning (~07:51 gym book) with yesterday's wake no
    longer covers — fall back to civil day so last night's log does not
    mirror into today (#914).
    """
    train = training_day_iso(now=now, last_wake_at=last_wake_at, tz_name=tz_name)
    civil = str(as_of or "")[:10]
    if not civil:
        from .timeutil import local_today_iso

        civil = local_today_iso(tz_name, now=now)
    delta = _days_apart(train, civil)
    if delta is None or delta > 1:
        return False
    if delta == 0:
        return True
    from .timeutil import local_now

    clock = local_now(tz_name, now=now)
    return clock.time() < TRAINING_DAY_CUTOFF


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
    remainder_open: bool = False,
) -> bool:
    """``already_trained_today`` SoT for planner + stamp.

    Civil fallback (no current wake): Training parent complete is enough.
    When last_wake is current: parent complete alone must not pin — a PPL
    session has to have closed in that wake. After-midnight quest complete
    that still sits on the civil GT parent must not block the next letter.

    ``remainder_open`` is the #1109 gate: a frozen plan that still has
    lifts or volume left is not a finished day, even if the Training
    parent is checked. An empty frozen plan is open too.
    """
    if remainder_open:
        return False
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
    if wake is None or wake.astimezone(clock.tzinfo) > clock:
        # No usable wake: before the cutoff is still yesterday (#1304).
        return training_day_for(clock, tz_name=tz_name) or local_today_iso(
            tz_name, now=clock
        )
    return wake.astimezone(clock.tzinfo).strftime("%Y-%m-%d")


def resolve_log_date(
    date: Any,
    *,
    now: Optional[datetime] = None,
    last_wake_at: Any = None,
    tz_name: Optional[str] = None,
) -> str:
    """Empty date defaults to the training day; explicit YYYY-MM-DD is kept.

    The log date picker is authoritative (#771). Civil-today after midnight
    is no longer remapped onto the prior wake — that ate explicit 09/16
    selections and stored 09/15. Agent/quest posts with no date still land
    on the current training day so a late-night write without a picker
    stays on this wake.
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


def session_has_close_stamp(session: Any) -> bool:
    """True when the row was persisted (``closed_at`` or ``created_at``).

    A date-only row is a partial log. First persist is the session close.
    """
    if isinstance(session, dict):
        raw = session.get("closed_at") or session.get("created_at")
    else:
        raw = getattr(session, "closed_at", None) or getattr(session, "created_at", None)
    return bool(str(raw or "").strip())


def session_is_quest_seed_only(session: Any) -> bool:
    """True when every exercise on the row is an unedited quest seed.

    Checking one lift persists a session, so ``created_at`` is set. That
    stamp must not count as a finished workout (#999). A real logged
    exercise still can.
    """
    if isinstance(session, dict):
        exercises = session.get("exercises") or []
    else:
        exercises = getattr(session, "exercises", None) or []
    if not exercises:
        return False
    for ex in exercises:
        if isinstance(ex, dict):
            if ex.get("quest_seeded") or ex.get("movement_only"):
                continue
            raw = str(ex.get("raw") or "")
        else:
            if getattr(ex, "quest_seeded", False):
                continue
            raw = str(getattr(ex, "raw", "") or "")
        if str(raw).startswith("quest-seeded:"):
            continue
        return False
    return True


def lift_leaf_key(name: str) -> str:
    """Quest leaf slug for a lift name. Matches ``daily_plan_tasks`` ``ex-`` keys."""
    slug = _LIFT_SLUG_RE.sub("-", (name or "").lower()).strip("-")
    return "ex-" + ((slug or "item")[:48])


def _optional_number(value: Any, cast):
    if value is None or value == "":
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def leaf_target(raw: Any) -> Optional[dict]:
    """Planned sets and tonnage for one frozen leaf.

    Explicit ``sets`` / ``reps`` / ``weight_lbs`` / ``volume`` win. Otherwise
    the quest title ``Name (100 lb 3×8)`` is the prescription. A leaf with
    neither sets nor volume is not a target.
    """
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    name = title.split(" (", 1)[0].strip()
    detail = ""
    match = _TITLE_DETAIL_RE.match(title)
    if match:
        name = match.group("name").strip()
        detail = match.group("detail")
    slug = str(raw.get("slug") or "").strip().lower()
    if not slug.startswith("ex-"):
        if not name:
            return None
        slug = lift_leaf_key(name)
    sets = _optional_number(raw.get("sets"), int)
    reps = _optional_number(raw.get("reps"), int)
    weight = _optional_number(raw.get("weight_lbs"), float)
    if sets is None or reps is None:
        found = _SETS_REPS_RE.search(detail or title)
        if found:
            sets = int(found.group(1))
            reps = int(found.group(2))
    if weight is None:
        found = _WEIGHT_RE.search(detail or title)
        if found:
            weight = float(found.group(1))
    sets_n = max(0, int(sets or 0))
    reps_n = max(0, int(reps or 0))
    weight_n = float(weight or 0)
    volume = _optional_number(raw.get("volume"), float)
    if volume is None:
        volume = weight_n * sets_n * reps_n
    volume_n = max(0.0, float(volume))
    if sets_n <= 0 and volume_n <= 1e-9:
        return None
    if not name:
        name = slug[3:].replace("-", " ").title()
    return {
        "slug": slug,
        "name": name,
        "sets": sets_n,
        "reps": reps_n,
        "weight_lbs": weight_n,
        "volume": volume_n,
        "notes_extra": str(raw.get("notes_extra") or ""),
    }


def frozen_prescription_fields(raw: Any) -> dict:
    """Numeric prescription to store on a frozen leaf. Empty when unparseable."""
    target = leaf_target(raw if isinstance(raw, dict) else {})
    if target is None:
        return {}
    return {
        "sets": target["sets"],
        "reps": target["reps"],
        "weight_lbs": target["weight_lbs"],
        "volume": target["volume"],
    }


def objective_targets(objective: Any) -> Optional[list]:
    """Frozen lift targets, or ``[]`` for an explicit empty plan.

    ``None`` means no frozen plan is in play and callers keep the #951 close.
    """
    if not isinstance(objective, dict):
        return None
    leaves = [
        raw
        for raw in (objective.get("leaves") or [])
        if isinstance(raw, dict)
        and str(raw.get("slug") or "").lower().startswith("ex-")
    ]
    targets = []
    for raw in leaves:
        target = leaf_target(raw)
        if target is not None:
            targets.append(target)
    if targets:
        return targets
    if objective.get("frozen") is True:
        return []
    return None


def _exercise_rows(session: Any) -> list:
    if isinstance(session, dict):
        return list(session.get("exercises") or [])
    return list(getattr(session, "exercises", None) or [])


def _session_day(session: Any) -> str:
    """Training day of the row (``TRAINING_DAY_CUTOFF``), else its date."""
    return session_training_day(session)


def exercise_logged_work(ex: Any) -> Optional[tuple]:
    """``(name, sets, volume)`` for one real logged exercise.

    Quest seeds, movement-only rows, and planner status copy contribute
    nothing (#999). Volume is tonnage (load × sets × reps).
    """
    from .models import is_planner_status_exercise_name, is_zero_volume_status_log
    from .quest_workout_log import exercise_is_real_log

    if is_zero_volume_status_log(ex) or not exercise_is_real_log(ex):
        return None
    if isinstance(ex, dict):
        if ex.get("quest_seeded") or ex.get("movement_only"):
            return None
        name = str(ex.get("name") or "")
        raw = str(ex.get("raw") or "")
        raw_sets = ex.get("sets") or []
    else:
        if getattr(ex, "quest_seeded", False):
            return None
        name = str(getattr(ex, "name", "") or "")
        raw = str(getattr(ex, "raw", "") or "")
        raw_sets = getattr(ex, "sets", None) or []
    if is_planner_status_exercise_name(name) or not str(name).strip():
        return None
    if raw.startswith("quest-seeded:"):
        return None
    sets_n = 0
    volume = 0.0
    if not isinstance(raw_sets, list):
        return None
    for st in raw_sets:
        if isinstance(st, dict):
            try:
                count = int(st.get("sets") or 0)
                reps = int(st.get("reps") or 0)
            except (TypeError, ValueError):
                continue
            if st.get("exclude_from_load_math"):
                weight = None
            elif st.get("tonnage_weight_lbs") is not None:
                weight = _optional_number(st.get("tonnage_weight_lbs"), float)
            else:
                weight = _optional_number(st.get("weight_lbs"), float) or 0.0
        else:
            try:
                count = int(getattr(st, "sets", 0) or 0)
                reps = int(getattr(st, "reps", 0) or 0)
            except (TypeError, ValueError):
                continue
            if getattr(st, "exclude_from_load_math", False):
                weight = None
            else:
                override = getattr(st, "tonnage_weight_lbs", None)
                weight = (
                    float(override)
                    if override is not None
                    else float(getattr(st, "weight_lbs", 0) or 0)
                )
        if count <= 0 or reps <= 0 or weight is None:
            continue
        sets_n += count
        volume += float(weight) * count * reps
    if sets_n <= 0 and volume <= 1e-9:
        return None
    return name, sets_n, volume


def logged_work_by_lift(sessions: Sequence[Any]) -> dict:
    """Sum real sets and tonnage per lift slug. Quest-seed-only rows are skipped."""
    totals: dict = {}
    for session in sessions or []:
        if session_is_quest_seed_only(session):
            continue
        for ex in _exercise_rows(session):
            work = exercise_logged_work(ex)
            if work is None:
                continue
            name, sets_n, volume = work
            key = lift_leaf_key(name)
            row = totals.setdefault(key, {"sets": 0, "volume": 0.0})
            row["sets"] += int(sets_n)
            row["volume"] += float(volume)
    return totals


def frozen_plan_remainder(objective: Any, sessions: Sequence[Any]) -> Optional[dict]:
    """Frozen plan minus real logged work, per lift, in sets and tonnage.

    ``None`` when no frozen plan is in play. An explicit empty plan
    (frozen, no lifts) stays open and has no remainder lifts. A lift
    drops out only when both remaining sets and remaining volume are
    zero. The session is done only then, and only when the plan had work.
    """
    targets = objective_targets(objective)
    if targets is None:
        return None
    if not targets:
        return {
            "empty_plan": True,
            "done": False,
            "volume": 0.0,
            "lifts": [],
        }
    logged = logged_work_by_lift(sessions)
    lifts = []
    total = 0.0
    for target in targets:
        got = logged.get(target["slug"]) or {"sets": 0, "volume": 0.0}
        rem_sets = max(0, int(target["sets"]) - int(got["sets"]))
        rem_volume = float(target["volume"]) - float(got["volume"])
        if rem_volume <= 1e-6:
            rem_volume = 0.0
        else:
            rem_volume = max(0.0, rem_volume)
        if rem_sets <= 0 and rem_volume <= 0:
            continue
        total += rem_volume
        lifts.append(
            {
                "slug": target["slug"],
                "name": target["name"],
                "sets": rem_sets,
                "reps": target["reps"],
                "weight_lbs": target["weight_lbs"],
                "volume": rem_volume,
                "planned_sets": target["sets"],
                "planned_reps": target["reps"],
                "planned_weight_lbs": target["weight_lbs"],
                "planned_volume": target["volume"],
                "notes_extra": target["notes_extra"],
            }
        )
    return {
        "empty_plan": False,
        "done": not lifts and total <= 0,
        "volume": total,
        "lifts": lifts,
    }


def remainder_public(remainder: Optional[dict]) -> Optional[dict]:
    """JSON-safe remainder stamped onto a plan."""
    if not isinstance(remainder, dict):
        return None
    lifts = []
    for lift in remainder.get("lifts") or []:
        if not isinstance(lift, dict):
            continue
        lifts.append(
            {
                "slug": lift.get("slug"),
                "name": lift.get("name"),
                "sets": lift.get("sets"),
                "reps": lift.get("reps"),
                "weight_lbs": lift.get("weight_lbs"),
                "volume": lift.get("volume"),
                "planned_sets": lift.get("planned_sets"),
                "planned_volume": lift.get("planned_volume"),
            }
        )
    return {
        "done": bool(remainder.get("done")),
        "empty_plan": bool(remainder.get("empty_plan")),
        "volume": float(remainder.get("volume") or 0),
        "lifts": lifts,
    }


def remainder_is_history(window: Sequence[Any], as_of: Any, remainder: Any) -> bool:
    """True when an open frozen remainder comes only from sessions that
    closed on an earlier civil date than the plan day ``as_of`` (#1304).

    After midnight but before ``TRAINING_DAY_CUTOFF`` the planning window is
    still last night's wake. A session that closed before midnight is done:
    its leftover frozen targets must not keep that letter open for the new
    plan day, so the plan rolls to the next letter.
    """
    if not isinstance(remainder, dict) or remainder.get("done"):
        return False
    day = str(as_of or "")[:10]
    if not day or not window:
        return False
    from .timeutil import local_tz

    for session in window:
        if not session_has_close_stamp(session):
            return False
        closed = session_close_dt(session)
        if closed is None or closed.astimezone(local_tz()).date().isoformat() >= day:
            return False
    return True


def planning_window_sessions(
    sessions: Sequence[Any],
    *,
    as_of: Optional[str] = None,
    last_wake_at: Any = None,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> list:
    """PPL rows in the same window ``closed_ppl_for_planning`` uses.

    A partial log with no close stamp still counts so the remainder can
    see it. Quest-seed-only rows stay in the list; their exercises are
    not logged work.
    """
    from .timeutil import local_now

    clock = local_now(tz_name, now=now)
    use_wake = wake_is_current(last_wake_at, as_of, now=clock, tz_name=tz_name)
    civil = str(as_of or "")[:10]
    out = []
    for session in sessions or []:
        if _session_type(session) not in ("push", "pull", "legs"):
            continue
        if use_wake:
            if not session_in_wake(
                session, last_wake_at=last_wake_at, now=clock, tz_name=tz_name
            ):
                continue
        elif _session_day(session) != civil:
            continue
        out.append(session)
    return out


def closed_ppl_for_planning(
    sessions: Sequence[Any],
    *,
    as_of: Optional[str] = None,
    last_wake_at: Any = None,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
    training_objective: Any = None,
) -> Optional[str]:
    """Latest PPL letter with a persisted close in the planning window (#951).

    Same TRAINING_DAY_CUTOFF (04:00) window as ``ppl_logged_for_planning``. A partial row
    (no close stamp) does not roll the next letter. When more than one
    letter closed in the window, the latest close wins. An overnight
    close before TRAINING_DAY_CUTOFF still belongs to that training day.

    When a frozen plan is in play, the letter closes only if the remainder
    has no lifts and no volume left (#1109). One real set does not cover
    a lift. An empty frozen plan never closes. Quest-seed-only rows still
    never close. No frozen plan keeps the persist close.
    """
    from .timeutil import local_now

    clock = local_now(tz_name, now=now)
    use_wake = wake_is_current(last_wake_at, as_of, now=clock, tz_name=tz_name)
    civil = str(as_of or "")[:10]
    hits = []
    for session in sessions or []:
        letter = _session_type(session)
        if letter not in ("push", "pull", "legs"):
            continue
        if not session_has_close_stamp(session):
            continue
        if session_is_quest_seed_only(session):
            continue
        if use_wake:
            if not session_in_wake(
                session, last_wake_at=last_wake_at, now=clock, tz_name=tz_name
            ):
                continue
        elif _session_day(session) != civil:
            continue
        closed = session_close_dt(session, tz_name=tz_name)
        hits.append((closed or datetime.min.replace(tzinfo=timezone.utc), letter))
    window = planning_window_sessions(
        sessions,
        as_of=as_of,
        last_wake_at=last_wake_at,
        now=clock,
        tz_name=tz_name,
    )
    remainder = frozen_plan_remainder(training_objective, window)
    if remainder_is_history(window, as_of, remainder):
        remainder = None
    if remainder is not None and not remainder["done"]:
        return None
    if not hits:
        return None
    hits.sort(key=lambda item: item[0])
    return hits[-1][1]
