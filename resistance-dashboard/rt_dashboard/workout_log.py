"""Parse the FitDash workout log POST body (Pi + Vercel).

UI ``submitWorkout`` posts ``{session_type, date, notes, exercises}``
(``static/app.js``). Flat ``{name, weight_lbs, sets, reps}`` is also accepted
so the README / Pi form stay valid.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from .models import ExerciseEntry, Session, SetEntry
from .timeutil import local_today_iso


def parse_log_body(data: dict) -> Session:
    payload: Dict[str, Any] = data if isinstance(data, dict) else {}
    st = str(payload.get("session_type", "")).lower().strip()
    date = str(payload.get("date", "")).strip()
    if st not in ("push", "pull", "legs"):
        raise ValueError("session_type must be push, pull, or legs")
    if not date:
        date = local_today_iso()
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
        if not set_entries:
            raise ValueError(f"no sets for exercise {name}")
        exercises.append(
            ExerciseEntry(
                name=name,
                sets=set_entries,
                is_pr=False,  # set by apply_auto_prs after history is loaded
            )
        )
    notes = str(payload.get("notes") or "")
    return Session(
        date=date,
        session_type=st,
        exercises=exercises,
        notes=notes,
    )
