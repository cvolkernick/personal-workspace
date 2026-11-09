"""Staged priority-volume ramp for lats, then chest, then quads.

The weekly ceiling for the active muscle is last Mon–Sun week's logged
hard-set credits plus one or two, and never a jump to the destination in
one step. Other major muscles stay in the 4–8 band. This does not use
``focus_muscles`` and does not drop them to maintenance.

Credits come from ``credit_sets_for_exercise`` via ``weekly_set_tally``.
That formula is not modified here.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence

# Required on the workout message for main compounds. Not a 3-0-1-0 scheme.
LOWERING_CUE = (
    "On main lifts (bench, rows, overhead press, squat, hack squat, leg press, "
    "deadlift, RDL, and pull-ups), lower the weight under control for 2–3 seconds, "
    "then lift as usual on the way up. No extra pause at the bottom unless the lift already has one."
)

# Logged credits at or above this gap under the ceiling count as finished.
_NEAR = 0.5

# Planner muscle → #1193 display group. Side delts and the middle band stay put.
DISPLAY_GROUP_MUSCLE = {
    "lats_upper_back": "lats",
    "chest": "chest",
    "quads": "quads",
}

# Session letter that can take extra sets for the active muscle.
MUSCLE_SESSION = {"lats": "pull", "chest": "push", "quads": "legs"}

# Trim these isolations first when a priority set would break the session cap.
_TRIM_BITS = (
    ("shrug", 0),
    ("pushdown", 1),
    ("push-down", 1),
    ("push down", 1),
    ("curl", 2),
    ("calf", 3),
)


def _day(value: Any) -> Optional[date]:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def week_monday(value: Any) -> Optional[date]:
    day = _day(value)
    if day is None:
        return None
    return day - timedelta(days=day.weekday())


def prior_sunday(value: Any) -> Optional[str]:
    monday = week_monday(value)
    if monday is None:
        return None
    return (monday - timedelta(days=1)).isoformat()


def ramp_week_number(as_of: Any, started_on: Any) -> int:
    """1-based Mon–Sun week index from ``priority_ramp_started_on``."""
    start = week_monday(started_on)
    current = week_monday(as_of)
    if start is None or current is None:
        return 1
    return max(1, (current - start).days // 7 + 1)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


def ramp_configured(goals: Optional[dict]) -> bool:
    raw = goals or {}
    if not _as_bool(raw.get("priority_keep_others_in_band"), False):
        return False
    return bool(str(raw.get("priority_ramp_started_on") or "")[:10])


def ramp_is_live(goals: Optional[dict], as_of: Any) -> bool:
    """True once ``as_of`` is on or after the configured Monday."""
    raw = goals or {}
    if not ramp_configured(raw):
        return False
    started = str(raw.get("priority_ramp_started_on") or "")[:10]
    day = str(as_of or "")[:10]
    return bool(day) and day >= started


def _in_cut(phase: Optional[str]) -> bool:
    # Omitted phase follows the approved cut. A named non-cut phase does not.
    if phase is None or str(phase).strip() == "":
        return True
    return str(phase).strip().lower() in ("cut", "recomp")


def _bad_lifts(status: Optional[str], in_cut: bool) -> bool:
    return bool(in_cut) and str(status or "").strip().lower() in ("yellow", "red")


def _band(staged: float, floor: float) -> Dict[str, float]:
    hi = float(staged)
    lo = max(float(floor), hi - 2.0)
    if lo > hi:
        lo = hi
    return {"min": lo, "max": hi, "priority": True}


def _plain(lo: float, hi: float) -> Dict[str, float]:
    return {"min": float(lo), "max": float(hi), "priority": False}


def _planner():
    from . import workout_planner as wp

    return wp


def _coerce_sessions(sessions: Optional[Sequence[Any]]) -> list:
    """Dict logs become Session rows so ``weekly_set_tally`` can read them."""
    from .models import ExerciseEntry, Session, SetEntry

    out = []
    for raw in sessions or []:
        if not isinstance(raw, dict):
            out.append(raw)
            continue
        exercises = []
        for ex in raw.get("exercises") or []:
            if not isinstance(ex, dict):
                exercises.append(ex)
                continue
            sets = []
            for st in ex.get("sets") or []:
                if not isinstance(st, dict):
                    continue
                try:
                    n_sets = int(st.get("sets") or 0)
                except (TypeError, ValueError):
                    n_sets = 0
                try:
                    reps = int(st.get("reps") or 0)
                except (TypeError, ValueError):
                    reps = 0
                weight = st.get("weight_lbs")
                try:
                    weight_f = float(weight) if weight is not None and weight != "" else 0.0
                except (TypeError, ValueError):
                    weight_f = 0.0
                sets.append(SetEntry(weight_lbs=weight_f, sets=n_sets, reps=reps))
            exercises.append(ExerciseEntry(name=str(ex.get("name") or ""), sets=sets))
        out.append(
            Session(
                date=str(raw.get("date") or "")[:10],
                session_type=str(raw.get("session_type") or ""),
                exercises=exercises,
                notes=str(raw.get("notes") or ""),
            )
        )
    return out


def _tally_credits(
    sessions: Optional[Sequence[Any]],
    catalog: Optional[dict],
    *,
    as_of: str,
    window_days: int,
    secondary_fraction: float,
) -> Dict[str, float]:
    wp = _planner()
    tally = wp.weekly_set_tally(
        _coerce_sessions(sessions),
        catalog or {"exercises": []},
        as_of=as_of,
        window_days=max(1, int(window_days)),
        secondary_fraction=float(secondary_fraction),
    )
    return {str(k): float(v) for k, v in (tally.get("by_muscle") or {}).items()}


def prior_week_credits(
    sessions: Optional[Sequence[Any]],
    catalog: Optional[dict],
    as_of: Any,
    secondary_fraction: float = 0.5,
) -> Dict[str, float]:
    """Hard-set credits for the previous America/New_York Mon–Sun week."""
    end = prior_sunday(as_of)
    if not end:
        return {}
    return _tally_credits(
        sessions,
        catalog,
        as_of=end,
        window_days=7,
        secondary_fraction=secondary_fraction,
    )


def this_week_credits(
    sessions: Optional[Sequence[Any]],
    catalog: Optional[dict],
    as_of: Any,
    secondary_fraction: float = 0.5,
) -> Dict[str, float]:
    """Hard-set credits from this week's Monday through ``as_of``."""
    day = _day(as_of)
    monday = week_monday(as_of)
    if day is None or monday is None:
        return {}
    window = (day - monday).days + 1
    return _tally_credits(
        sessions,
        catalog,
        as_of=day.isoformat(),
        window_days=window,
        secondary_fraction=secondary_fraction,
    )


def session_flags_restore(session: Any) -> bool:
    if isinstance(session, dict):
        notes = str(session.get("notes") or "")
        kind = str(session.get("session_type") or "")
        flagged = bool(
            session.get("deload")
            or session.get("deload_week")
            or session.get("recovery_week")
        )
    else:
        notes = str(getattr(session, "notes", "") or "")
        kind = str(getattr(session, "session_type", "") or "")
        flagged = bool(
            getattr(session, "deload", False)
            or getattr(session, "deload_week", False)
            or getattr(session, "recovery_week", False)
        )
    blob = f"{notes} {kind}".lower()
    return flagged or "deload" in blob or "recovery week" in blob


def _sessions_flag_restore(sessions: Optional[Sequence[Any]], start: date, end: date) -> bool:
    for session in sessions or []:
        raw = session.get("date") if isinstance(session, dict) else getattr(session, "date", "")
        day = _day(raw)
        if day is None or day < start or day > end:
            continue
        if session_flags_restore(session):
            return True
    return False


def prior_week_restore(sessions: Optional[Sequence[Any]], as_of: Any) -> bool:
    monday = week_monday(as_of)
    if monday is None:
        return False
    return _sessions_flag_restore(sessions, monday - timedelta(days=7), monday - timedelta(days=1))


def this_week_restore(sessions: Optional[Sequence[Any]], as_of: Any) -> bool:
    """A deload or recovery-week log already in this Mon–Sun week."""
    monday = week_monday(as_of)
    day = _day(as_of)
    if monday is None or day is None:
        return False
    return _sessions_flag_restore(sessions, monday, day)


def lifts_chip_status(sessions: Optional[Sequence[Any]], goals: Optional[dict], as_of: Any) -> str:
    day = _day(as_of)
    if day is None:
        return "insufficient"
    try:
        from datetime import datetime

        from .guardrails import _lift_tile

        tile = _lift_tile(list(sessions or []), goals or {}, datetime(day.year, day.month, day.day))
        return str((tile or {}).get("status") or "insufficient")
    except Exception:  # noqa: BLE001 - a missing chip must not blank the plan
        return "insufficient"


def goals_flag_restore(goals: Optional[dict], recovery_label: Optional[str] = None) -> bool:
    raw = goals or {}
    if raw.get("deload") or raw.get("deload_week") or raw.get("recovery_week"):
        return True
    label = str(recovery_label or "").lower()
    return "deload" in label or "recovery week" in label


def resolve_priority_ramp(
    goals: Optional[dict],
    *,
    as_of: Optional[str] = None,
    prior_credits: Optional[Dict[str, float]] = None,
    lifts_status: Optional[str] = None,
    prior_lifts_status: Optional[str] = None,
    restore_flag: bool = False,
    prior_restore_flag: bool = False,
    phase: Optional[str] = None,
    session_cap: Optional[int] = None,
) -> Dict[str, Any]:
    """Staged bands for one Mon–Sun week. ``live`` is false before the start date."""
    wp = _planner()
    raw = dict(goals or {})
    balanced_only = bool(raw.get("_balanced_bands_only"))
    normalized = wp.normalize_goals(raw)
    if as_of is None:
        from .timeutil import local_today_iso

        day = local_today_iso()
    else:
        day = str(as_of)[:10]
    if balanced_only or not ramp_is_live(normalized, day):
        return {"live": False, "as_of": day, "targets": {}}

    lo = float(normalized.get("sets_per_muscle_week_min") or 4)
    hi = float(normalized.get("sets_per_muscle_week_max") or 8)
    ceiling = float(normalized.get("priority_sets_target") or normalized.get("sets_per_muscle_week_priority_max") or 10)
    try:
        increment = int(normalized.get("priority_ramp_increment") or 2)
    except (TypeError, ValueError):
        increment = 2
    increment = 1 if increment <= 1 else 2
    ordered = [
        wp.normalize_muscle(m)
        for m in (normalized.get("priority_muscles_ordered") or ["lats", "chest", "quads"])
    ]
    ordered = [m for m in ordered if m in wp.MAJOR_MUSCLES]
    if not ordered:
        ordered = ["lats", "chest", "quads"]
    prior = {
        wp.normalize_muscle(k): float(v or 0)
        for k, v in (prior_credits or {}).items()
    }
    in_cut = _in_cut(phase)
    current_bad = _bad_lifts(lifts_status, in_cut) or bool(restore_flag)
    prior_bad = _bad_lifts(prior_lifts_status, in_cut) or bool(prior_restore_flag)
    week = ramp_week_number(day, normalized.get("priority_ramp_started_on"))
    # Week 1 starts on approval. A yellow week after that holds the next week too.
    rollback = current_bad if week <= 1 else (current_bad or prior_bad)

    def reached(muscle: str) -> bool:
        return float(prior.get(muscle) or 0) + 1e-9 >= ceiling - _NEAR

    held: List[str] = []
    waiting: List[str] = []
    active: Optional[str] = None
    for muscle in ordered:
        if reached(muscle):
            held.append(muscle)
        elif active is None:
            active = muscle
        else:
            waiting.append(muscle)

    targets: Dict[str, Dict[str, float]] = {
        m: _plain(lo, hi) for m in wp.MAJOR_MUSCLES
    }
    if rollback and len(held) > 1:
        for muscle in held:
            targets[muscle] = _band(hi, lo)
        if active:
            targets[active] = _band(float(prior.get(active) or 0), lo)
    elif rollback:
        recent = active or (held[-1] if held else ordered[0])
        if recent in held:
            targets[recent] = _band(hi, lo)
        else:
            targets[recent] = _band(float(prior.get(recent) or 0), lo)
        for muscle in held:
            if muscle != recent:
                targets[muscle] = _band(ceiling, lo)
    else:
        for muscle in held:
            targets[muscle] = _band(ceiling, lo)
        if active:
            staged = min(ceiling, float(prior.get(active) or 0) + increment)
            targets[active] = _band(staged, lo)

    cap = int(session_cap or normalized.get("session_working_set_cap") or 14)
    today_line = _today_line(
        active=active,
        held=held,
        week=week,
        ceiling=ceiling,
        cap=cap,
        rollback=rollback,
    )
    return {
        "live": True,
        "rollback": rollback,
        "as_of": day,
        "week": week,
        "increment": increment,
        "ceiling": ceiling,
        "active_muscle": active,
        "held": held,
        "waiting": waiting,
        "ordered": ordered,
        "targets": targets,
        "today_line": today_line,
        "session_cap": cap,
    }


def _today_line(
    *,
    active: Optional[str],
    held: Sequence[str],
    week: int,
    ceiling: float,
    cap: int,
    rollback: bool,
) -> str:
    dest = int(round(ceiling))
    if rollback:
        who = active or (held[-1] if held else "lats")
        return (
            f"Priority volume: {who} ramp paused (week {week}); "
            f"other muscles 4–8. Session cap ~{cap}."
        )
    if active:
        return (
            f"Priority volume: {active} toward ~{dest} hard sets/week "
            f"(week {week} of ramp); other muscles 4–8. Session cap ~{cap}."
        )
    names = ", ".join(held) if held else "priority muscles"
    return (
        f"Priority volume: {names} holding ~{dest} hard sets/week "
        f"(week {week} of ramp); other muscles 4–8. Session cap ~{cap}."
    )


def ramp_public(ramp: Optional[dict]) -> Dict[str, Any]:
    ramp = ramp or {}
    if not ramp.get("live"):
        return {"live": False}
    targets = ramp.get("targets") or {}
    return {
        "live": True,
        "rollback": bool(ramp.get("rollback")),
        "active_muscle": ramp.get("active_muscle"),
        "week": ramp.get("week"),
        "increment": ramp.get("increment"),
        "ceiling": ramp.get("ceiling"),
        "today_line": ramp.get("today_line") or "",
        "targets": {
            m: {"min": band["min"], "max": band["max"], "priority": bool(band.get("priority"))}
            for m, band in targets.items()
            if isinstance(band, dict)
        },
    }


def _fmt_band(band: Dict[str, float]) -> str:
    lo = float(band.get("min") or 0)
    hi = float(band.get("max") or 0)
    if abs(lo - hi) < 0.05:
        return f"{hi:g}"
    return f"{lo:g}-{hi:g}"


def display_targets(
    goals: Optional[dict],
    sessions: Optional[Sequence[Any]],
    catalog: Optional[dict],
    *,
    as_of: Optional[str] = None,
) -> Dict[str, str]:
    """#1193 labels for lats, chest, and quads. Empty when the ramp is not live."""
    wp = _planner()
    normalized = wp.normalize_goals(goals)
    if as_of is None:
        from .timeutil import local_today_iso

        day = local_today_iso()
    else:
        day = str(as_of)[:10]
    if not ramp_is_live(normalized, day):
        return {}
    fraction = float(normalized.get("secondary_set_fraction") or 0.5)
    prior = prior_week_credits(sessions, catalog, day, fraction)
    ramp = resolve_priority_ramp(normalized, as_of=day, prior_credits=prior)
    if not ramp.get("live"):
        return {}
    targets = ramp.get("targets") or {}
    out: Dict[str, str] = {}
    for group, muscle in DISPLAY_GROUP_MUSCLE.items():
        band = targets.get(muscle)
        if isinstance(band, dict):
            out[group] = _fmt_band(band)
    return out


def prompt_volume_fields(
    goals: Optional[dict],
    sessions: Optional[Sequence[Any]] = None,
    catalog: Optional[dict] = None,
    *,
    as_of: Optional[str] = None,
    lifts_status: Optional[str] = None,
    prior_lifts_status: Optional[str] = None,
    restore_flag: bool = False,
    prior_restore_flag: bool = False,
    phase: Optional[str] = None,
) -> Dict[str, Any]:
    """Per-muscle targets and the workout-message rules for the model planner."""
    wp = _planner()
    normalized = wp.normalize_goals(goals)
    if as_of is None:
        from .timeutil import local_today_iso

        day = local_today_iso()
    else:
        day = str(as_of)[:10]
    fraction = float(normalized.get("secondary_set_fraction") or 0.5)
    prior: Dict[str, float] = {}
    if ramp_is_live(normalized, day):
        prior = prior_week_credits(sessions, catalog, day, fraction)
    ramp = resolve_priority_ramp(
        normalized,
        as_of=day,
        prior_credits=prior,
        lifts_status=lifts_status,
        prior_lifts_status=prior_lifts_status,
        restore_flag=restore_flag,
        prior_restore_flag=prior_restore_flag,
        phase=phase,
        session_cap=normalized.get("session_working_set_cap"),
    )
    if ramp.get("live"):
        targets = ramp.get("targets") or {}
        summary = ramp.get("today_line") or ""
    else:
        targets = wp.muscle_targets({**normalized, "_balanced_bands_only": True}, as_of=day)
        summary = "4-8 hard sets per major muscle per week"
    muscle_targets = {
        m: {
            "min": float(band["min"]),
            "max": float(band["max"]),
            "priority": bool(band.get("priority")),
        }
        for m, band in targets.items()
        if isinstance(band, dict)
    }
    clause = (
        f"{summary} Per-muscle weekly targets are in volume_caps.muscle_targets. "
        "Do not encode a tempo in sets, reps, or weight_lbs."
    )
    return {
        "sets_per_muscle_week": summary,
        "muscle_targets": muscle_targets,
        "priority_volume": ramp.get("today_line") if ramp.get("live") else None,
        "lowering_cue": LOWERING_CUE,
        "volume_clause": clause,
        "message_rule": (
            "The workout message must include this sentence for main lifts: "
            + LOWERING_CUE
        ),
        "ramp": ramp_public(ramp),
    }


def ensure_workout_message(message: Optional[str], ramp: Optional[dict], *, has_lifts: bool) -> str:
    """Add the priority line and the lowering cue when they belong on the message."""
    text = str(message or "").strip()
    ramp = ramp or {}
    line = str(ramp.get("today_line") or "").strip()
    if ramp.get("live") and line and "Priority volume:" not in text:
        text = f"{text} · {line}".strip(" ·")
    if has_lifts and "2–3 seconds" not in text and "2-3 seconds" not in text:
        text = f"{text} · {LOWERING_CUE}".strip(" ·")
    return text


def _norm_name(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def _trim_rank(row: dict, active: str) -> Optional[int]:
    if str(row.get("movement") or "") == "compound":
        return None
    prim = set()
    wp = _planner()
    for muscle in row.get("primary_muscles") or []:
        prim.add(wp.normalize_muscle(muscle))
    if active and active in prim:
        return None
    name = _norm_name(str(row.get("name") or ""))
    for bit, rank in _TRIM_BITS:
        if bit in name:
            return rank
    if "traps" in prim:
        return 0
    if "triceps" in prim:
        return 1
    if "biceps" in prim:
        return 2
    if "calves" in prim:
        return 3
    return None


def _sets_of(row: dict) -> int:
    rx = row.get("prescription") if isinstance(row.get("prescription"), dict) else {}
    try:
        return int(rx.get("sets") or 0)
    except (TypeError, ValueError):
        return 0


def _set_sets(row: dict, n: int) -> None:
    rx = dict(row.get("prescription") or {})
    rx["sets"] = int(n)
    row["prescription"] = rx


def _refresh_credits(row: dict, secondary_fraction: float) -> None:
    wp = _planner()
    credits = wp.credit_sets_for_exercise(
        row.get("primary_muscles") or [],
        row.get("secondary_muscles") or [],
        _sets_of(row),
        secondary_fraction=secondary_fraction,
    )
    row["set_credits"] = {k: round(v, 2) for k, v in credits.items()}


def _planned_from(rows: Sequence[dict], secondary_fraction: float) -> Dict[str, float]:
    planned: Dict[str, float] = {}
    for row in rows:
        _refresh_credits(row, secondary_fraction)
        credits = row.get("set_credits") or {}
        for muscle, credit in credits.items():
            planned[muscle] = planned.get(muscle, 0.0) + float(credit)
    return planned


def _muscle_credit(row: dict, muscle: str) -> float:
    return float((row.get("set_credits") or {}).get(muscle) or 0)


def _find_priority_compound(rows: Sequence[dict], muscle: str) -> Optional[int]:
    wp = _planner()
    for i, row in enumerate(rows):
        if str(row.get("movement") or "") != "compound":
            continue
        prim = [wp.normalize_muscle(m) for m in (row.get("primary_muscles") or [])]
        if muscle in prim:
            return i
    return None


def _trim_one(rows: List[dict], active: str) -> bool:
    ranked = []
    for i, row in enumerate(rows):
        rank = _trim_rank(row, active)
        if rank is None or _sets_of(row) < 1:
            continue
        ranked.append((rank, i))
    if not ranked:
        return False
    ranked.sort()
    i = ranked[0][1]
    nxt = _sets_of(rows[i]) - 1
    if nxt <= 0:
        rows.pop(i)
    else:
        _set_sets(rows[i], nxt)
    return True


def _compounds_first(rows: Sequence[dict]) -> List[dict]:
    compounds = [r for r in rows if str(r.get("movement") or "") == "compound"]
    rest = [r for r in rows if str(r.get("movement") or "") != "compound"]
    return compounds + rest


def bump_priority_sets(
    plan_ex: Sequence[dict],
    *,
    muscle: str,
    increment: int,
    weekly_max: float,
    logged: float,
    secondary_fraction: float = 0.5,
    session_cap: int = 14,
    make_exercise: Optional[Callable[[], Optional[dict]]] = None,
) -> Dict[str, Any]:
    """Add at most ``increment`` credits on the active muscle, staying within the caps.

    Prefer +1 set on an existing compound for ``muscle``. A new exercise is
    built only when no current priority compound can take a set. Non-priority
    isolation (shrugs, pushdowns, curls, calf raises) is trimmed before a
    main compound, and a main compound never drops below 2 sets.
    """
    rows: List[dict] = [dict(r) for r in plan_ex or []]
    for row in rows:
        if isinstance(row.get("prescription"), dict):
            row["prescription"] = dict(row["prescription"])
    added = 0.0
    tried_new = False
    cap = max(0, int(session_cap))

    def total_sets() -> int:
        return sum(max(0, _sets_of(row)) for row in rows)

    def muscle_total() -> float:
        return float(logged) + sum(_muscle_credit(row, muscle) for row in rows)

    while added + 1e-6 < float(increment):
        if weekly_max - muscle_total() < 0.49:
            break
        idx = _find_priority_compound(rows, muscle)
        if idx is None:
            if tried_new or make_exercise is None:
                break
            tried_new = True
            fresh = make_exercise()
            if not isinstance(fresh, dict):
                break
            fresh = dict(fresh)
            if isinstance(fresh.get("prescription"), dict):
                fresh["prescription"] = dict(fresh["prescription"])
            need = max(1, _sets_of(fresh))
            while total_sets() + need > cap:
                if not _trim_one(rows, muscle):
                    fresh = None
                    break
            if not fresh:
                break
            _refresh_credits(fresh, secondary_fraction)
            before = muscle_total()
            # Keep compounds ahead of isolation.
            insert_at = 0
            for i, row in enumerate(rows):
                if str(row.get("movement") or "") == "compound":
                    insert_at = i + 1
            rows.insert(insert_at, fresh)
            gained = muscle_total() - before
            if gained <= 1e-6:
                rows.pop(insert_at)
                break
            added += gained
            continue
        while total_sets() + 1 > cap:
            if not _trim_one(rows, muscle):
                idx = None
                break
        if idx is None:
            break
        idx = _find_priority_compound(rows, muscle)
        if idx is None:
            continue
        before = _muscle_credit(rows[idx], muscle)
        _set_sets(rows[idx], _sets_of(rows[idx]) + 1)
        _refresh_credits(rows[idx], secondary_fraction)
        gained = _muscle_credit(rows[idx], muscle) - before
        if gained <= 1e-6:
            _set_sets(rows[idx], _sets_of(rows[idx]) - 1)
            _refresh_credits(rows[idx], secondary_fraction)
            break
        note = "Priority ramp: +1 hard set."
        rationale = str(rows[idx].get("rationale") or "")
        if note not in rationale:
            rows[idx]["rationale"] = f"{rationale} {note}".strip()
        added += gained
    rows = _compounds_first(rows)
    planned = _planned_from(rows, secondary_fraction)
    return {
        "exercises": rows,
        "planned_credits": planned,
        "session_sets": sum(max(0, _sets_of(row)) for row in rows),
        "added_credits": round(added, 2),
    }
