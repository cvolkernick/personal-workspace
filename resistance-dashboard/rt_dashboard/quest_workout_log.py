"""Quest complete → today's workout log (lift leaves only).

Checking off a training/lift leaf upserts that exercise into today's session.
Non-lift quests (meals, hydration, shopping, sleep, cardio) are ignored. Numbers come
from today's plan or the quest title — never invented. Quest-seeded rows are
not auto-PR tagged. Uncheck removes the row only while it is still unedited.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .models import ExerciseEntry, Session, SetEntry
from .timeutil import local_today_iso

SESSION_TYPES = ("push", "pull", "legs")
SEED_PREFIX = "quest-seeded:"
LIFT_GROUPS = frozenset({"training", "train"})
NON_LIFT_GROUPS = frozenset(
    {"nutrition", "shopping", "sleep", "recovery", "other", "cardio"}
)

# Session-level / rest / non-exercise training actions.
_SKIP_TITLE = re.compile(
    r"^(complete today|rest day|protect |cover remaining|eat through|eat:|"
    r"cardio|walk · zone 2|sleep —|sleep -|sleep battery)",
    re.I,
)
# Quest title baked by plan_from_today_board: "DB Press (50 lb 3×10)"
_TITLE_DETAIL = re.compile(
    r"^(?P<name>.+?)\s+\((?P<detail>[^)]*)\)\s*$"
)
_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*lb", re.I)
_SETS_REPS = re.compile(r"(\d+)\s*[×x]\s*(\d+)")


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def seed_fingerprint(sets: Sequence[SetEntry]) -> str:
    if not sets:
        return f"{SEED_PREFIX}movement"
    parts = []
    for s in sets:
        w = s.weight_lbs
        w_bit = f"{w:g}" if w is not None else ""
        parts.append(f"{w_bit}/{s.sets}/{s.reps}")
    return SEED_PREFIX + ",".join(parts)


def is_unedited_seed(entry: ExerciseEntry) -> bool:
    """True only while the row is still the auto-added, unedited seed."""
    raw = str(getattr(entry, "raw", "") or "")
    flagged = bool(getattr(entry, "quest_seeded", False)) or raw.startswith(
        SEED_PREFIX
    )
    if not flagged or getattr(entry, "is_pr", False):
        return False
    if raw.startswith(SEED_PREFIX):
        return raw == seed_fingerprint(entry.sets or [])
    return flagged


def parse_quest_title(title: str) -> Dict[str, Any]:
    """Pull exercise name + optional prescription baked into the quest title."""
    text = (title or "").strip()
    out: Dict[str, Any] = {
        "name": "",
        "weight_lbs": None,
        "sets": None,
        "reps": None,
    }
    if not text:
        return out
    match = _TITLE_DETAIL.match(text)
    if match:
        out["name"] = match.group("name").strip()
        detail = match.group("detail") or ""
        w = _WEIGHT.search(detail)
        sr = _SETS_REPS.search(detail)
        if w:
            try:
                out["weight_lbs"] = float(w.group(1))
            except (TypeError, ValueError):
                out["weight_lbs"] = None
        if sr:
            try:
                out["sets"] = int(sr.group(1))
                out["reps"] = int(sr.group(2))
            except (TypeError, ValueError):
                out["sets"] = out["reps"] = None
        return out
    out["name"] = text
    return out


def _skip_title(title: str) -> bool:
    return bool(_SKIP_TITLE.search((title or "").strip()))


def looks_like_lift_quest(
    *,
    group: str = "",
    title: str = "",
    slug: str = "",
    today_workout: Optional[dict] = None,
) -> bool:
    """True for training/lift leaves; false for meals, hydration, rest, etc."""
    g = str(group or "").strip().lower()
    slug_l = str(slug or "").strip().lower()
    title_s = str(title or "").strip()
    if g in NON_LIFT_GROUPS:
        return False
    if _skip_title(title_s):
        return False
    if slug_l.startswith("ex-"):
        return True
    if g in LIFT_GROUPS:
        parsed = parse_quest_title(title_s)
        if parsed.get("name") and (
            parsed.get("weight_lbs") is not None
            or parsed.get("sets") is not None
            or _planned_match(today_workout, parsed["name"])
        ):
            return True
        # Training leaf with a human exercise name (no session-level skip).
        if parsed.get("name") and g in LIFT_GROUPS and not _skip_title(parsed["name"]):
            return True
    planned = _planned_match(today_workout, parse_quest_title(title_s).get("name") or title_s)
    return planned is not None


def ppl_session_type(*candidates: Any) -> str:
    """First push/pull/legs among values. Ignores rest / empty / junk."""
    for raw in candidates:
        st = str(raw or "").lower().strip()
        if st in SESSION_TYPES:
            return st
    return ""


def _planned_exercises(today_workout: Optional[dict]) -> List[dict]:
    if not isinstance(today_workout, dict):
        return []
    # Rest-gated Today still keeps the prescription list — match it.
    out = []
    for ex in today_workout.get("exercises") or []:
        if isinstance(ex, dict) and str(ex.get("name") or "").strip():
            out.append(ex)
    return out


def _planned_match(today_workout: Optional[dict], name: str) -> Optional[dict]:
    key = _norm(name)
    if not key:
        return None
    for ex in _planned_exercises(today_workout):
        if _norm(str(ex.get("name") or "")) == key:
            return ex
    return None


def _rx_from_plan(ex: dict) -> Dict[str, Any]:
    rx = ex.get("prescription") if isinstance(ex.get("prescription"), dict) else {}
    return {
        "weight_lbs": rx.get("weight_lbs", ex.get("weight_lbs")),
        "sets": rx.get("sets", ex.get("sets")),
        "reps": rx.get("reps", ex.get("reps")),
    }


def seed_exercise(
    name: str,
    *,
    planned: Optional[dict] = None,
    title_rx: Optional[dict] = None,
) -> ExerciseEntry:
    """Build a quest-seeded row. Full rx → copy; otherwise movement-only."""
    rx = dict(title_rx or {})
    if planned:
        plan_rx = _rx_from_plan(planned)
        for key in ("weight_lbs", "sets", "reps"):
            if plan_rx.get(key) is not None:
                rx[key] = plan_rx[key]
        name = str(planned.get("name") or name).strip() or name
    sets: List[SetEntry] = []
    w, sn, r = rx.get("weight_lbs"), rx.get("sets"), rx.get("reps")
    # All three present → seed. Any missing → movement-only (null load).
    if w is not None and sn is not None and r is not None:
        try:
            wf = float(w)
            sni = int(sn)
            ri = int(r)
        except (TypeError, ValueError):
            wf = sni = ri = None  # type: ignore[assignment]
        if wf is not None and sni is not None and ri is not None and sni >= 1 and ri >= 1:
            sets = [SetEntry(weight_lbs=wf, sets=sni, reps=ri)]
    entry = ExerciseEntry(
        name=name.strip(),
        sets=sets,
        is_pr=False,
        quest_seeded=True,
        raw=seed_fingerprint(sets),
    )
    return entry


def resolve_session_type(
    *,
    payload_st: str = "",
    today_workout: Optional[dict] = None,
    sessions: Sequence[Session] = (),
    today: str = "",
    next_session_type: str = "",
) -> str:
    tw = today_workout if isinstance(today_workout, dict) else {}
    ctx = tw.get("context") if isinstance(tw.get("context"), dict) else {}
    st = ppl_session_type(
        payload_st,
        next_session_type,
        tw.get("session_type"),
        tw.get("next_session_type"),
        ctx.get("next_session_type"),
    )
    if st:
        return st
    found = [
        s
        for s in sessions
        if s.date == today and str(s.session_type or "").lower() in SESSION_TYPES
    ]
    types = {str(s.session_type).lower() for s in found}
    if len(types) == 1:
        return next(iter(types))
    return ""


def find_today_session(
    sessions: Sequence[Session], today: str, session_type: str
) -> Optional[Session]:
    for s in sessions:
        if s.date == today and str(s.session_type or "").lower() == session_type:
            return s
    return None


def find_exercise(session: Session, name: str) -> Tuple[Optional[int], Optional[ExerciseEntry]]:
    key = _norm(name)
    if not key:
        return None, None
    for i, ex in enumerate(session.exercises or []):
        if _norm(ex.name) == key:
            return i, ex
    return None, None


def apply_quest_to_session(
    *,
    completed: bool,
    group: str = "",
    title: str = "",
    slug: str = "",
    session_type: str = "",
    today_workout: Optional[dict] = None,
    sessions: Sequence[Session] = (),
    today: str = "",
) -> Tuple[Optional[Session], Dict[str, Any]]:
    """Pure merge. Returns (session to persist or None, info)."""
    info: Dict[str, Any] = {
        "ok": True,
        "wrote": False,
        "action": "ignore",
        "reason": "",
    }
    # A checked lift leaf is work done — write even when Today is rest-gated
    # (session_type=rest). Rest / session-level titles are already not_lift.
    if not looks_like_lift_quest(
        group=group, title=title, slug=slug, today_workout=today_workout
    ):
        info["reason"] = "not_lift"
        return None, info

    parsed = parse_quest_title(title)
    name = str(parsed.get("name") or "").strip()
    planned = _planned_match(today_workout, name) if name else None
    if planned:
        name = str(planned.get("name") or name).strip()
    if not name:
        info["reason"] = "no_exercise_name"
        return None, info

    st = resolve_session_type(
        payload_st=session_type,
        today_workout=today_workout,
        sessions=sessions,
        today=today,
    )
    if st not in SESSION_TYPES:
        info["ok"] = False
        info["reason"] = "session_type_required"
        return None, info

    existing = find_today_session(sessions, today, st)
    idx, found = find_exercise(existing, name) if existing else (None, None)

    if completed:
        if found is not None:
            info["action"] = "dedupe"
            info["reason"] = "already_logged"
            info["name"] = found.name
            info["session_type"] = st
            info["exercise"] = found.to_dict()
            return None, info
        seeded = seed_exercise(name, planned=planned, title_rx=parsed)
        if existing:
            session = deepcopy(existing)
            session.exercises = list(session.exercises or [])
            session.exercises.append(seeded)
        else:
            session = Session(
                date=today,
                session_type=st,
                exercises=[seeded],
                notes="",
                source_file="",
            )
        info["action"] = "upsert"
        info["wrote"] = True
        info["name"] = seeded.name
        info["session_type"] = st
        info["quest_seeded"] = True
        info["movement_only"] = not bool(seeded.sets)
        info["exercise"] = seeded.to_dict()
        return session, info

    # Uncheck: drop only an unedited seed.
    if found is None or existing is None:
        info["action"] = "uncheck_noop"
        info["reason"] = "not_logged"
        return None, info
    if not is_unedited_seed(found):
        info["action"] = "uncheck_keep"
        info["reason"] = "edited"
        info["name"] = found.name
        return None, info
    session = deepcopy(existing)
    session.exercises = [
        ex for i, ex in enumerate(session.exercises or []) if i != idx
    ]
    info["action"] = "uncheck_remove"
    info["wrote"] = True
    info["name"] = found.name
    info["session_type"] = st
    return session, info


def persist_quest_session(user_id: str, session: Session) -> Dict[str, Any]:
    """Turso on Vercel; SQLite on Pi. Fail honest if neither can write."""
    from .turso_http import turso_enabled

    if turso_enabled():
        from .turso_repo import save_preview_session

        return save_preview_session(user_id, session)
    from .workout_repo import WorkoutRepository, use_sqlite

    if use_sqlite():
        return WorkoutRepository(user_id=user_id).upsert_session(session)
    raise RuntimeError("no workout backend")


def _default_load_sessions(user_id: str) -> List[Session]:
    try:
        from api.dashboard import _load_sessions

        sessions, _err, _src = _load_sessions(user_id)
        return list(sessions or [])
    except Exception:
        pass
    try:
        from .workout_repo import WorkoutRepository, use_sqlite

        if use_sqlite():
            return WorkoutRepository(user_id=user_id).list_sessions()
    except Exception:
        pass
    return []


def quest_log_context(
    user_id: str,
    payload: Optional[dict] = None,
    *,
    headers: Optional[dict] = None,
    load_sessions: Optional[Callable[[str], Sequence[Session]]] = None,
) -> Tuple[str, List[Session], Dict[str, Any]]:
    """Civil day + sessions + PPL slot for a quest log write.

    Viewer date from payload.date, else request TZ (not Vercel UTC).
    session_type prefers an explicit PPL value, then next_session_type —
    never ``rest``.
    """
    payload = payload if isinstance(payload, dict) else {}
    day = str(payload.get("date") or "")[:10]
    if not day:
        tz_name = None
        try:
            from api.dashboard import request_tz_name

            tz_name = request_tz_name(headers or {}, "")
        except Exception:  # noqa: BLE001
            tz_name = None
        day = local_today_iso(tz_name)
    loader = load_sessions or _default_load_sessions
    try:
        sessions = list(loader(user_id) or [])
    except Exception:  # noqa: BLE001
        sessions = []
    next_st = ""
    try:
        from .workout_store import load_workspace_goals, next_session_brief

        goals, _src = load_workspace_goals()
        next_st = str(next_session_brief(sessions, goals).get("next_session_type") or "")
    except Exception:  # noqa: BLE001
        next_st = ""
    today_workout = {
        "session_type": ppl_session_type(
            payload.get("session_type"),
            payload.get("next_session_type"),
            next_st,
        ),
        "next_session_type": ppl_session_type(
            payload.get("next_session_type"),
            next_st,
        ),
        "exercises": [],
    }
    return day, sessions, today_workout


def attach_lift_quest_log(
    result: dict,
    payload: Optional[dict],
    completed: bool,
    *,
    user_id: str,
    sessions: Optional[Sequence[Session]] = None,
    today: Optional[str] = None,
    today_workout: Optional[dict] = None,
    persist: Optional[Callable[[str, Session], Dict[str, Any]]] = None,
    load_sessions: Optional[Callable[[str], Sequence[Session]]] = None,
) -> dict:
    """After a successful GT complete: maybe upsert/remove today's lift row.

    Quest complete stays 200 even if the log write fails — GT already flipped.
    ``workout_log`` on the result is honest about the write.
    """
    payload = payload if isinstance(payload, dict) else {}
    group = str(payload.get("group") or "").strip()
    title = str(payload.get("title") or "").strip()
    slug = str(payload.get("slug") or "").strip()
    session_type = ppl_session_type(
        payload.get("session_type"),
        payload.get("next_session_type"),
    )
    tw = today_workout
    if not looks_like_lift_quest(
        group=group, title=title, slug=slug, today_workout=tw
    ):
        result["workout_log"] = {
            "ok": True,
            "wrote": False,
            "action": "ignore",
            "reason": "not_lift",
        }
        return result

    day = str(today or payload.get("date") or local_today_iso())[:10]
    try:
        if sessions is None:
            loader = load_sessions or _default_load_sessions
            sessions = loader(user_id)
        session, info = apply_quest_to_session(
            completed=bool(completed),
            group=group,
            title=title,
            slug=slug,
            session_type=session_type,
            today_workout=tw,
            sessions=list(sessions or []),
            today=day,
        )
        if session is not None:
            writer = persist or persist_quest_session
            write = writer(user_id, session)
            info["write"] = write
            if isinstance(write, dict) and write.get("ok") is False:
                info["ok"] = False
                info["wrote"] = False
                info["error"] = write.get("error") or "write_failed"
            else:
                info["wrote"] = True
        result["workout_log"] = info
    except Exception as exc:  # noqa: BLE001
        result["workout_log"] = {
            "ok": False,
            "wrote": False,
            "error": str(exc) or type(exc).__name__,
        }
    return result
