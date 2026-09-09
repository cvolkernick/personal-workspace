"""Parse the FitDash workout log POST body (Pi + Vercel).

UI ``submitWorkout`` posts ``{session_type, date, notes, exercises}``
(``static/app.js``). Flat ``{name, weight_lbs, sets, reps}`` is also accepted
so the README / Pi form stay valid.

Log-tab save unions with an existing same-day same-type session (quest
checkoffs) instead of replacing the row. Incoming log weights win on name
match; exercises only on the existing session are kept.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional, Sequence

from .models import ExerciseEntry, Session, SetEntry


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def find_same_day_session(
    sessions: Sequence[Session], date: str, session_type: str
) -> Optional[Session]:
    st = str(session_type or "").lower().strip()
    day = str(date or "")[:10]
    for s in sessions or []:
        if str(s.date)[:10] == day and str(s.session_type or "").lower() == st:
            return s
    return None


def merge_same_day_session(
    incoming: Session, existing: Optional[Session]
) -> Session:
    """Union exercises for the same civil day + PPL type.

    Incoming (Log tab) wins on normalized name — those are the loads Chris
    typed. Quest-only rows stay. Empty incoming notes do not wipe existing
    notes. Different date or session_type is not merged.
    """
    if existing is None:
        return incoming
    in_st = str(incoming.session_type or "").lower()
    ex_st = str(existing.session_type or "").lower()
    if str(existing.date)[:10] != str(incoming.date)[:10] or in_st != ex_st:
        return incoming
    by_key: Dict[str, ExerciseEntry] = {}
    order: list[str] = []
    for ex in existing.exercises or []:
        key = _norm_name(ex.name)
        if not key:
            continue
        if key not in by_key:
            order.append(key)
        by_key[key] = ex
    for ex in incoming.exercises or []:
        key = _norm_name(ex.name)
        if not key:
            continue
        if key not in by_key:
            order.append(key)
        by_key[key] = ex
    notes = (incoming.notes or "").strip() or (existing.notes or "")
    source = (incoming.source_file or "").strip() or (existing.source_file or "")
    closed = (existing.closed_at or "").strip() or (incoming.closed_at or "")
    return Session(
        date=incoming.date,
        session_type=incoming.session_type,
        exercises=[by_key[k] for k in order],
        notes=notes,
        source_file=source,
        closed_at=closed or None,
    )


def merge_log_with_history(
    incoming: Session, history: Sequence[Session]
) -> Session:
    existing = find_same_day_session(
        history, incoming.date, incoming.session_type
    )
    return merge_same_day_session(incoming, existing)


def parse_log_body(data: dict, *, now=None) -> Session:
    payload: Dict[str, Any] = data if isinstance(data, dict) else {}
    st = str(payload.get("session_type", "")).lower().strip()
    date = str(payload.get("date", "")).strip()
    if st not in ("push", "pull", "legs"):
        raise ValueError("session_type must be push, pull, or legs")
    from .training_day import last_wake_from, resolve_log_date
    from .timeutil import local_now_iso

    tz_name = str(payload.get("tz") or "") or None
    date = resolve_log_date(
        date,
        now=now,
        last_wake_at=last_wake_from(payload=payload),
        tz_name=tz_name,
    )
    # validate date
    datetime.strptime(date, "%Y-%m-%d")
    exercises_in = payload.get("exercises") or []
    if not exercises_in:
        raise ValueError("exercises required")
    exercises = []
    for ex in exercises_in:
        if not isinstance(ex, dict):
            raise ValueError("exercise must be an object")
        name = str(ex.get("name", "")).strip()
        if not name:
            raise ValueError("exercise name required")
        # Flat form: {name, weight_lbs, sets, reps}
        # Nested form: {name, sets: [{weight_lbs, sets, reps}, ...]}
        raw_sets = ex.get("sets")
        if isinstance(raw_sets, list):
            sets_in = raw_sets
        elif all(k in ex for k in ("weight_lbs", "reps")):
            sets_in = [
                {
                    "weight_lbs": ex["weight_lbs"],
                    "sets": int(ex.get("sets") or 1),
                    "reps": ex["reps"],
                }
            ]
        else:
            sets_in = []
        set_entries = []
        for s in sets_in:
            if not isinstance(s, dict):
                continue
            try:
                w = float(s.get("weight_lbs"))
                sn = int(s.get("sets") if s.get("sets") is not None else 1)
                r = int(s.get("reps"))
            except (TypeError, ValueError) as e:
                raise ValueError(f"invalid set for {name}: {e}") from e
            if sn < 1 or r < 1:
                raise ValueError(f"sets and reps must be >= 1 for {name}")
            set_entries.append(SetEntry(weight_lbs=w, sets=sn, reps=r))
        quest_seeded = bool(ex.get("quest_seeded") or ex.get("movement_only"))
        if not set_entries:
            # Honest empty log: quest seed / movement-only. Manual log still
            # requires sets so we never invent a placeholder load.
            if not quest_seeded:
                raise ValueError(f"no sets for exercise {name}")
        exercises.append(
            ExerciseEntry(
                name=name,
                sets=set_entries,
                is_pr=False,  # set by apply_auto_prs after history is loaded
                quest_seeded=quest_seeded,
                raw=str(ex.get("raw") or ""),
            )
        )
    notes = str(payload.get("notes") or "")
    closed = str(payload.get("closed_at") or "").strip() or local_now_iso()
    return Session(
        date=date,
        session_type=st,
        exercises=exercises,
        notes=notes,
        closed_at=closed,
    )
