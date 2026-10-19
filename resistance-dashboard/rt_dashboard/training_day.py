"""Training day SoT: wake window / session-close, not civil midnight.

A training day is ``[last_wake_at, next_wake)``. Sessions that close in that
window belong to it. Civil-day charts may still bucket by ``session.date``.

When last_wake is missing or still in the future (planned alarm), callers
fall back to civil today — same honesty as calorie/hydration wake windows.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

_LIFT_SLUG_RE = re.compile(r"[^a-z0-9]+")
_VOLUME_EPS = 1e-6


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


# Civil morning with yesterday's wake still advertised (sleep battery lag)
# must not keep last night's close in the planning window (#914). After
# midnight before dawn, delta 1 still counts so a 00:30 finish stays on
# the prior training day.
_AFTER_MIDNIGHT_END_HOUR = 5


def wake_covers_as_of(
    last_wake_at: Any,
    as_of: Optional[str],
    *,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> bool:
    """True when last_wake is this wake, or yesterday only before dawn.

    A last_wake weeks ago (stale sleep battery) must not pin today's letter
    to that old session. After-midnight finish (delta 1, hour < 5) still
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
    return clock.hour < _AFTER_MIDNIGHT_END_HOUR


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
    below_substantive_bar: bool = False,
) -> bool:
    """``already_trained_today`` SoT for planner + stamp.

    Civil fallback (no current wake): Training parent complete is enough.
    When last_wake is current: parent complete alone must not pin — a PPL
    session has to have closed in that wake. After-midnight quest complete
    that still sits on the civil GT parent must not block the next letter.

    ``below_substantive_bar`` is the #1109 gate: a checked Training parent
    is not a finished day while the frozen target still has sets or volume
    left, or while that target is an empty plan.
    """
    if below_substantive_bar:
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


def _exercise_rows(session: Any) -> list:
    if isinstance(session, dict):
        return list(session.get("exercises") or [])
    return list(getattr(session, "exercises", None) or [])


def exercise_is_real_work(ex: Any) -> bool:
    """True for logged sets. Quest seeds and status copy are not work (#999, #1110)."""
    from .models import is_planner_status_exercise_name, is_zero_volume_status_log
    from .quest_workout_log import exercise_is_real_log

    if is_zero_volume_status_log(ex):
        return False
    if isinstance(ex, dict):
        name = str(ex.get("name") or "")
    else:
        name = str(getattr(ex, "name", "") or "")
    if is_planner_status_exercise_name(name) or not name.strip():
        return False
    return exercise_is_real_log(ex)


def _entry_sets_volume(ex: Any) -> tuple:
    from .quest_workout_log import _as_exercise_entry

    entry = _as_exercise_entry(ex)
    if entry is None:
        return 0, 0.0
    sets = 0
    volume = 0.0
    for st in entry.sets or []:
        try:
            count = int(st.sets)
            reps = int(st.reps)
        except (TypeError, ValueError):
            continue
        if count <= 0 or reps <= 0:
            continue
        sets += count
        volume += float(st.volume)
    return sets, volume


def logged_work_by_leaf(sessions: Sequence[Any]) -> dict:
    """Real sets and volume in this window, keyed by lift leaf. Seeds add nothing."""
    totals: dict = {}
    for session in sessions or []:
        if session_is_quest_seed_only(session):
            continue
        for ex in _exercise_rows(session):
            if not exercise_is_real_work(ex):
                continue
            if isinstance(ex, dict):
                name = str(ex.get("name") or "")
            else:
                name = str(getattr(ex, "name", "") or "")
            sets, volume = _entry_sets_volume(ex)
            if sets <= 0 and volume <= _VOLUME_EPS:
                continue
            bucket = totals.setdefault(lift_leaf_key(name), {"sets": 0, "volume": 0.0})
            bucket["sets"] += sets
            bucket["volume"] += volume
    return totals


def _leaf_number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def leaf_prescription(raw: dict) -> dict:
    """Planned sets and volume for one frozen leaf.

    Title form is ``Name (40 lb 3×8)`` from the quest board. Explicit
    ``sets`` / ``reps`` / ``weight_lbs`` / ``volume`` on the leaf win.
    A leaf with no sets and no volume is not measurable, so one logged
    set does not finish it (#1109).
    """
    from .quest_workout_log import parse_quest_title

    title = str(raw.get("title") or "")
    parsed = parse_quest_title(title)
    name = str(raw.get("name") or parsed.get("name") or "").strip() or title.strip()
    slug = str(raw.get("slug") or "").strip().lower()
    if not slug.startswith("ex-"):
        slug = lift_leaf_key(name)
    sets_src = raw.get("sets") if raw.get("sets") is not None else parsed.get("sets")
    reps_src = raw.get("reps") if raw.get("reps") is not None else parsed.get("reps")
    weight_src = (
        raw.get("weight_lbs")
        if raw.get("weight_lbs") is not None
        else parsed.get("weight_lbs")
    )
    try:
        sets_i = int(sets_src) if sets_src is not None else 0
    except (TypeError, ValueError):
        sets_i = 0
    try:
        reps_i = int(reps_src) if reps_src is not None else 0
    except (TypeError, ValueError):
        reps_i = 0
    weight = _leaf_number(weight_src)
    explicit_volume = _leaf_number(raw.get("volume"))
    if explicit_volume is None:
        planned_volume = 0.0 if weight is None else float(weight) * sets_i * reps_i
    else:
        planned_volume = float(explicit_volume)
    measurable = sets_src is not None or explicit_volume is not None
    return {
        "slug": slug,
        "name": name or slug,
        "sets": sets_i,
        "reps": reps_i,
        "weight_lbs": weight,
        "volume": planned_volume,
        "measurable": measurable,
    }


def objective_leaves(objective: Any) -> list:
    if not isinstance(objective, dict):
        return []
    leaves = []
    seen = set()
    for raw in objective.get("leaves") or []:
        if not isinstance(raw, dict):
            continue
        rx = leaf_prescription(raw)
        if not rx["slug"] or rx["slug"] in seen:
            continue
        seen.add(rx["slug"])
        leaves.append(rx)
    return leaves


def session_remainder(objective: Any, sessions: Sequence[Any]) -> dict:
    """Frozen target minus logged real work, per lift, in sets and volume.

    ``applies`` is false when there is no objective, so #951 persist-close
    stays for a day that never froze a target. An explicit frozen target
    with no leaves is an empty plan: not done, and nothing to prescribe.
    A session is done only when every measurable leaf has zero sets and
    zero volume left. No one-set rule and no volume percent (#1109).
    """
    if not isinstance(objective, dict):
        return {
            "known": False,
            "applies": False,
            "empty_plan": False,
            "lifts": [],
            "volume": 0.0,
            "empty": True,
            "done": False,
        }
    leaves = objective_leaves(objective)
    if not leaves:
        frozen = bool(objective.get("frozen"))
        return {
            "known": True,
            "applies": frozen,
            "empty_plan": frozen,
            "lifts": [],
            "volume": 0.0,
            "empty": True,
            "done": False,
        }
    logged = logged_work_by_leaf(sessions)
    lifts = []
    total = 0.0
    for leaf in leaves:
        got = logged.get(leaf["slug"]) or {"sets": 0, "volume": 0.0}
        if not leaf["measurable"]:
            lifts.append(
                {
                    "slug": leaf["slug"],
                    "name": leaf["name"],
                    "sets": leaf["sets"],
                    "reps": leaf["reps"],
                    "weight_lbs": leaf["weight_lbs"],
                    "volume": leaf["volume"],
                    "planned_sets": leaf["sets"],
                    "planned_reps": leaf["reps"],
                    "planned_weight_lbs": leaf["weight_lbs"],
                    "planned_volume": leaf["volume"],
                    "logged_sets": int(got["sets"]),
                    "logged_volume": float(got["volume"]),
                    "sets_remaining": leaf["sets"],
                    "volume_remaining": leaf["volume"],
                    "measurable": False,
                }
            )
            total += float(leaf["volume"])
            continue
        rem_sets = max(0, int(leaf["sets"]) - int(got["sets"]))
        rem_vol = max(0.0, float(leaf["volume"]) - float(got["volume"]))
        if rem_vol <= _VOLUME_EPS:
            rem_vol = 0.0
        if rem_sets == 0 and rem_vol == 0.0:
            continue
        total += rem_vol
        lifts.append(
            {
                "slug": leaf["slug"],
                "name": leaf["name"],
                "sets": rem_sets,
                "reps": leaf["reps"],
                "weight_lbs": leaf["weight_lbs"],
                "volume": rem_vol,
                "planned_sets": leaf["sets"],
                "planned_reps": leaf["reps"],
                "planned_weight_lbs": leaf["weight_lbs"],
                "planned_volume": leaf["volume"],
                "logged_sets": int(got["sets"]),
                "logged_volume": float(got["volume"]),
                "sets_remaining": rem_sets,
                "volume_remaining": rem_vol,
                "measurable": True,
            }
        )
    return {
        "known": True,
        "applies": True,
        "empty_plan": False,
        "lifts": lifts,
        "volume": total,
        "empty": not lifts,
        "done": not lifts,
    }


def remainder_view(rem: dict) -> dict:
    lifts = list(rem.get("lifts") or [])
    return {
        "lifts": lifts,
        "volume": rem.get("volume") or 0.0,
        "empty": not lifts,
        "done": bool(rem.get("done")),
        "empty_plan": bool(rem.get("empty_plan")),
    }


def planning_window_sessions(
    sessions: Sequence[Any],
    *,
    as_of: Optional[str] = None,
    last_wake_at: Any = None,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> list:
    """Sessions in the same window ``closed_ppl_for_planning`` uses.

    Quest-seed-only rows are omitted. A partial log with no close stamp
    still counts so the remainder can see it.
    """
    from .timeutil import local_now

    clock = local_now(tz_name, now=now)
    use_wake = wake_is_current(last_wake_at, as_of, now=clock, tz_name=tz_name)
    civil = str(as_of or "")[:10]
    out = []
    for session in sessions or []:
        if session_is_quest_seed_only(session):
            continue
        if use_wake:
            if not session_in_wake(
                session, last_wake_at=last_wake_at, now=clock, tz_name=tz_name
            ):
                continue
        else:
            if isinstance(session, dict):
                day = str(session.get("date") or "")[:10]
            else:
                day = str(getattr(session, "date", "") or "")[:10]
            if day != civil:
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

    Same 5 AM wake window as ``ppl_logged_for_planning``. A partial row
    (no close stamp) does not roll the next letter. When more than one
    letter closed in the window, the latest close wins. An overnight
    close before 05:00 still belongs to that training day.

    When ``training_objective`` applies (#1109), the letter closes only
    if the regenerated remainder has no lifts and no volume left. Two
    lifts against a five-lift target stay open. An empty frozen target
    does not close. Quest-seed-only rows still never close.
    """
    window = planning_window_sessions(
        sessions,
        as_of=as_of,
        last_wake_at=last_wake_at,
        now=now,
        tz_name=tz_name,
    )
    rem = session_remainder(training_objective, window)
    if rem.get("applies") and not rem.get("done"):
        return None
    hits = []
    for session in window:
        letter = _session_type(session)
        if letter not in ("push", "pull", "legs"):
            continue
        if not session_has_close_stamp(session):
            continue
        closed = session_close_dt(session, tz_name=tz_name)
        hits.append((closed or datetime.min.replace(tzinfo=timezone.utc), letter))
    if not hits:
        return None
    hits.sort(key=lambda item: item[0])
    return hits[-1][1]
