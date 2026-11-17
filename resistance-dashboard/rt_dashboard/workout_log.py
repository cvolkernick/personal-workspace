"""Parse the FitDash workout log POST body (Pi + Vercel).

UI ``submitWorkout`` posts ``{session_type, date, notes, exercises}``
(``static/app.js``). Flat ``{name, weight_lbs, sets, reps}`` is also accepted
so the README / Pi form stay valid.

Log-tab save unions with an existing same-day same-type session (quest
checkoffs) instead of replacing the row. A second log of an exercise
already stored appends sets (#963). A quest seed is still replaced by
the typed log inside that same session. ``set_index`` updates that
stored set and leaves the others. Date moves and other cross-session
merges keep every set: the target's sets stay first, then the moved
sets, in list order. A quest tick in that union becomes a real logged
set. A retry of the same source session does not append again. A new
session later logged on that source day still merges. Exercises only
on one session are kept.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Optional, Sequence

from .models import ExerciseEntry, Session, SetEntry, is_planner_status_exercise_name


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


# Recorded on ``source_file`` so a retried date move can see that this
# source session was already absorbed. Not a path. Log-tab saves leave
# the lines in place when they have no source_file of their own.
#
# Identity is the persist stamp (``created_at`` when the row has one,
# otherwise ``closed_at``) plus an order-independent hash of the source
# sets. A day-only line ``fitdash-merged-from:YYYY-MM-DD`` is still
# parsed for older rows. It is never enough, by itself, to treat a
# later move as a retry.
_MERGED_FROM_PREFIX = "fitdash-merged-from:"
_IDENTITY_RE = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2}) id=(?P<digest>[0-9a-f]{64})@(?P<stamp>.*)$"
)
_LOG = logging.getLogger("fitdash.workout_log")


def session_persist_stamp(session: Session) -> str:
    """``created_at`` when that attribute is set, otherwise ``closed_at``."""
    for attr in ("created_at", "closed_at"):
        text = str(getattr(session, attr, None) or "").strip()
        if text:
            return text.replace("\r", "").replace("\n", "")
    return ""


def _set_index_token(entry: SetEntry) -> str:
    idx = getattr(entry, "set_index", None)
    if idx is None or isinstance(idx, bool):
        return ""
    try:
        return str(int(idx))
    except (TypeError, ValueError):
        return str(idx).replace("\r", "").replace("\n", "")


def _canon_weight(value: Any) -> str:
    number = float(value)
    if number == 0:
        return "0"
    text = format(number, ".10f").rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


def sets_digest(session: Session) -> str:
    """Hash of exercise, weight, reps, sets, set_index, and quest flag.

    Order does not change the digest. Two identical sets both count.
    """
    rows = []
    for ex in session.exercises or []:
        name = _norm_name(getattr(ex, "name", ""))
        quest = "1" if getattr(ex, "quest_seeded", False) else "0"
        for entry in ex.sets or []:
            rows.append(
                [
                    name,
                    _canon_weight(getattr(entry, "weight_lbs", 0)),
                    str(int(getattr(entry, "reps", 0))),
                    str(int(getattr(entry, "sets", 0))),
                    _set_index_token(entry),
                    quest,
                ]
            )
    rows.sort()
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_merged_marker(line: str) -> Optional[dict]:
    text = line.strip()
    if not text.startswith(_MERGED_FROM_PREFIX):
        return None
    rest = text[len(_MERGED_FROM_PREFIX) :].strip()
    ident = _IDENTITY_RE.fullmatch(rest)
    if ident:
        return {
            "day": ident.group("day"),
            "digest": ident.group("digest"),
            "stamp": ident.group("stamp"),
            "legacy": False,
            "token": text,
        }
    day = rest[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return {
            "day": day,
            "digest": "",
            "stamp": "",
            "legacy": True,
            "token": f"{_MERGED_FROM_PREFIX}{day}",
        }
    return None


def _marker_tokens(source_file: str) -> list[str]:
    seen = set()
    tokens = []
    for line in str(source_file or "").splitlines():
        parsed = _parse_merged_marker(line)
        if parsed is None or parsed["token"] in seen:
            continue
        seen.add(parsed["token"])
        tokens.append(parsed["token"])

    def sort_key(token: str):
        parsed = _parse_merged_marker(token) or {}
        return (
            0 if parsed.get("legacy") else 1,
            parsed.get("day") or "",
            parsed.get("digest") or "",
            parsed.get("stamp") or "",
        )

    return sorted(tokens, key=sort_key)


def merged_from_days(source_file: str) -> set[str]:
    """Civil days named on merged-from lines, including legacy day-only lines.

    A day in this set is not proof that a particular session was merged.
    """
    days: set[str] = set()
    for line in str(source_file or "").splitlines():
        parsed = _parse_merged_marker(line)
        if parsed is not None:
            days.add(parsed["day"])
    return days


def _source_file_body(source_file: str) -> str:
    kept = [
        line
        for line in str(source_file or "").splitlines()
        if _parse_merged_marker(line) is None
    ]
    return "\n".join(kept).strip()


def _with_markers(body: str, tokens: list[str]) -> str:
    return "\n".join(part for part in (body, *tokens) if part)


def mark_merged_from(source_file: str, day: str, session: Session) -> str:
    """Remember this source session was unioned into the row.

    The new line is keyed by persist stamp and set hash. A bare day is
    not written. Existing path text and older day-only lines stay.
    """
    day = str(day or "")[:10]
    tokens = _marker_tokens(source_file)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        token = (
            f"{_MERGED_FROM_PREFIX}{day} id={sets_digest(session)}"
            f"@{session_persist_stamp(session)}"
        )
        if token not in tokens:
            tokens.append(token)
        tokens = _marker_tokens("\n".join(tokens))
    return _with_markers(_source_file_body(source_file), tokens)


def source_identity_already_merged(
    source_file: str, day: str, session: Session
) -> bool:
    """True when this session's stamp and set hash are already recorded.

    A legacy ``fitdash-merged-from:YYYY-MM-DD`` line is not proof.
    """
    day = str(day or "")[:10]
    stamp = session_persist_stamp(session)
    digest = sets_digest(session)
    for line in str(source_file or "").splitlines():
        parsed = _parse_merged_marker(line)
        if parsed is None or parsed["legacy"]:
            continue
        if (
            parsed["day"] == day
            and parsed["stamp"] == stamp
            and parsed["digest"] == digest
        ):
            return True
    return False


def _combine_source_files(incoming: str, existing: str) -> str:
    """Incoming path wins. Merged-from lines from either side stay."""
    body = _source_file_body(incoming) or _source_file_body(existing)
    tokens = _marker_tokens(f"{incoming}\n{existing}")
    return _with_markers(body, tokens)


def _logged_set_bag(session: Optional[Session]) -> Counter:
    """Logged triples. ``set_index`` is not on the destination after a union."""
    bag: Counter = Counter()
    if session is None:
        return bag
    for ex in session.exercises or []:
        name = _norm_name(getattr(ex, "name", ""))
        for entry in ex.sets or []:
            bag[
                (
                    name,
                    _canon_weight(getattr(entry, "weight_lbs", 0)),
                    int(getattr(entry, "sets", 0)),
                    int(getattr(entry, "reps", 0)),
                )
            ] += 1
    return bag


def ensure_source_sets_on_readback(
    source: Session, dest: Optional[Session]
) -> None:
    """Raise unless every source set is present on ``dest``.

    Callers delete the source row only after this returns. Quest ticks
    are stored on the destination as real sets, so the quest flag is not
    required to match. ``set_index`` is an edit handle and is not copied
    by a cross-session union, so it is not required either. Multiplicity
    counts and order does not.
    """
    missing = []
    have = _logged_set_bag(dest)
    for key, count in _logged_set_bag(source).items():
        gap = count - have.get(key, 0)
        if gap <= 0:
            continue
        name, weight, sets, reps = key
        label = f"{name} {weight}x{sets}x{reps}"
        if gap > 1:
            label = f"{label} x{gap}"
        missing.append(label)
    if not missing:
        return
    detail = ", ".join(missing)
    day = str(getattr(source, "date", "") or "")[:10]
    kind = str(getattr(source, "session_type", "") or "")
    _LOG.error(
        "refusing to delete source %s %s; destination readback is missing %s",
        day,
        kind,
        detail,
    )
    raise RuntimeError(
        "source sets missing on destination readback; source row kept: " + detail
    )


def find_same_day_session(
    sessions: Sequence[Session], date: str, session_type: str
) -> Optional[Session]:
    st = str(session_type or "").lower().strip()
    day = str(date or "")[:10]
    for s in sessions or []:
        if str(s.date)[:10] == day and str(s.session_type or "").lower() == st:
            return s
    return None


def _parse_set_index(raw: Any, name: str) -> Optional[int]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or isinstance(raw, float):
        raise ValueError(f"invalid set_index for {name}")
    if isinstance(raw, int):
        parsed = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        parsed = int(raw.strip())
    else:
        raise ValueError(f"invalid set_index for {name}")
    if parsed < 0:
        raise ValueError(f"set_index must be >= 0 for {name}")
    return parsed


def _copy_set(entry: SetEntry) -> SetEntry:
    return SetEntry(
        weight_lbs=float(entry.weight_lbs),
        sets=int(entry.sets),
        reps=int(entry.reps),
    )


def _combine_logged_exercise(
    existing: ExerciseEntry, incoming: ExerciseEntry
) -> ExerciseEntry:
    """Quest seed is replaced by the typed log. A real log appends.

    ``set_index`` updates that stored set and leaves the others. The index
    refers to sets already saved, not to sets appended in this same save.
    """
    if existing.quest_seeded:
        return incoming
    stored = [_copy_set(s) for s in (existing.sets or [])]
    base_len = len(stored)
    extra: list[SetEntry] = []
    for inc in incoming.sets or []:
        idx = getattr(inc, "set_index", None)
        fresh = _copy_set(inc)
        if idx is None:
            extra.append(fresh)
            continue
        if not isinstance(idx, int) or isinstance(idx, bool) or idx >= base_len:
            raise ValueError(f"set_index {idx} is outside {existing.name}")
        stored[idx] = fresh
    return ExerciseEntry(
        name=existing.name,
        sets=stored + extra,
        is_pr=False,
        raw=existing.raw or "",
        quest_seeded=False,
    )


def _copy_union_set(entry: SetEntry) -> SetEntry:
    """One logged set for a cross-session union.

    ``set_index`` is not copied. Two sessions can both store index 0, and
    that index is not an identity. Order after the union is list position.
    """
    return SetEntry(
        weight_lbs=float(entry.weight_lbs),
        sets=int(entry.sets),
        reps=int(entry.reps),
        warmup=bool(getattr(entry, "warmup", False)),
        rir=getattr(entry, "rir", None),
    )


def _real_log_raw(*candidates: str) -> str:
    """Keep a typed note. Drop a quest-seed fingerprint so the row is real."""
    from .quest_workout_log import SEED_PREFIX

    for part in candidates:
        text = str(part or "")
        if text.startswith(SEED_PREFIX):
            continue
        if text.strip():
            return text
    return ""


def _union_cross_session_exercise(
    existing: ExerciseEntry, incoming: ExerciseEntry
) -> ExerciseEntry:
    """Target sets first, then the moved sets. Quest ticks become real logs.

    Same-session ``set_index`` edits do not apply here. A moved set is
    appended even when its index collides with a set already on the target.
    """
    sets = [_copy_union_set(s) for s in (existing.sets or [])]
    sets.extend(_copy_union_set(s) for s in (incoming.sets or []))
    return ExerciseEntry(
        name=existing.name,
        sets=sets,
        is_pr=bool(existing.is_pr or incoming.is_pr),
        raw=_real_log_raw(existing.raw, incoming.raw),
        quest_seeded=False,
    )


def logged_session_visible(expected: Session, read: Optional[Session]) -> bool:
    """True when ``read`` is the merged row: same day, type, and set list."""
    if read is None or expected is None:
        return False
    if str(read.date)[:10] != str(expected.date)[:10]:
        return False
    if str(read.session_type or "").lower() != str(expected.session_type or "").lower():
        return False
    got = list(read.exercises or [])
    want = list(expected.exercises or [])
    if len(got) != len(want):
        return False
    for left, right in zip(got, want):
        if _norm_name(left.name) != _norm_name(right.name):
            return False
        if bool(getattr(left, "quest_seeded", False)) != bool(
            getattr(right, "quest_seeded", False)
        ):
            return False
        left_sets = list(left.sets or [])
        right_sets = list(right.sets or [])
        if len(left_sets) != len(right_sets):
            return False
        for a, b in zip(left_sets, right_sets):
            if (
                float(a.weight_lbs) != float(b.weight_lbs)
                or int(a.sets) != int(b.sets)
                or int(a.reps) != int(b.reps)
            ):
                return False
    return True


def merge_same_day_session(
    incoming: Session,
    existing: Optional[Session],
    *,
    same_name: str = "union",
) -> Session:
    """Union exercises for the same civil day + PPL type.

    ``same_name="union"`` (date moves and any other cross-session merge)
    keeps the target's sets, then appends the moved sets. Order is list
    position, never ``set_index``. Quest-seeded sets in that union become
    real logged sets. ``same_name="append"`` (Log tab, same session) adds
    sets onto a real log and still lets a typed log replace a quest seed.
    Empty incoming notes do not wipe existing notes. Different date or
    session_type is not merged.
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
            continue
        if same_name == "append":
            by_key[key] = _combine_logged_exercise(by_key[key], ex)
        else:
            by_key[key] = _union_cross_session_exercise(by_key[key], ex)
    notes = (incoming.notes or "").strip() or (existing.notes or "")
    source = _combine_source_files(incoming.source_file or "", existing.source_file or "")
    closed = (existing.closed_at or "").strip() or (incoming.closed_at or "")
    return Session(
        date=incoming.date,
        session_type=incoming.session_type,
        exercises=[by_key[k] for k in order],
        notes=notes,
        source_file=source,
        closed_at=closed or None,
        status=existing.status,
        submission_id=existing.submission_id,
        plan_objective=existing.plan_objective,
        skipped=existing.skipped,
        substitutions=existing.substitutions,
    )


def merge_log_with_history(
    incoming: Session, history: Sequence[Session]
) -> Session:
    existing = find_same_day_session(
        history, incoming.date, incoming.session_type
    )
    return merge_same_day_session(incoming, existing, same_name="append")


def _civil_day(value: Any, *, tz_name: Optional[str] = None, now=None) -> str:
    """Bare YYYY-MM-DD is kept. A timestamp uses the 4 AM ET training day."""
    from .session_work import log_day_from_value
    from .training_day import parse_dt

    raw = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        datetime.strptime(raw, "%Y-%m-%d")
        return raw
    if raw and parse_dt(raw) is not None:
        day = log_day_from_value(raw, now=now, tz_name=tz_name)
        datetime.strptime(day, "%Y-%m-%d")
        return day
    raise ValueError("date must be YYYY-MM-DD")


def plan_session_date_move(
    history: Sequence[Session],
    *,
    session_type: str,
    from_date: str,
    to_date: str,
    tz_name: Optional[str] = None,
    now=None,
) -> Session:
    """Move a logged session's civil date. Sets/loads are unchanged.

    Explicit edit — does not remap through training-day SoT. Same-letter
    already on the target day unions into that session. An exercise on
    both days keeps the target's sets first, then the moved sets. Quest
    ticks in that union become real logged sets. Dest-only rows stay
    (#771). Applying the same move again is a no-op when the destination
    already records this source session (persist stamp and set hash).
    A marker that names only the source day is not that record.
    """
    st = str(session_type or "").lower().strip()
    if st not in ("push", "pull", "legs"):
        raise ValueError("session_type must be push, pull, or legs")
    try:
        src_day = _civil_day(from_date, tz_name=tz_name, now=now)
        dest_day = _civil_day(to_date, tz_name=tz_name, now=now)
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    src = find_same_day_session(history, src_day, st)
    if src is None:
        raise ValueError("session not found")
    incoming = Session(
        date=dest_day,
        session_type=st,
        exercises=list(src.exercises or []),
        notes=src.notes or "",
        source_file=src.source_file or "",
        closed_at=src.closed_at,
    )
    if src_day == dest_day:
        return incoming
    dest = find_same_day_session(history, dest_day, st)
    if dest is None:
        return incoming
    # Retry after the dest row was written and before the source row was
    # deleted. The same session is already on dest; unioning again would
    # double its sets. A day-only marker is not this session.
    if source_identity_already_merged(dest.source_file or "", src_day, src):
        return dest
    merged = merge_same_day_session(incoming, dest, same_name="union")
    merged.source_file = mark_merged_from(merged.source_file, src_day, src)
    return merged


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
        # Planner status is not a lift. Drop it instead of storing a 0-volume row (#1110).
        if is_planner_status_exercise_name(name):
            continue
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
            from .session_work import apply_set_flags

            apply_set_flags(set_entries[-1], s)
            idx = _parse_set_index(s.get("set_index"), name)
            if idx is not None:
                set_entries[-1].set_index = idx
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
    if not exercises:
        raise ValueError("exercises required")
    notes = str(payload.get("notes") or "")
    closed = str(payload.get("closed_at") or "").strip() or local_now_iso()
    return Session(
        date=date,
        session_type=st,
        exercises=exercises,
        notes=notes,
        closed_at=closed,
        submission_id=str(payload.get("submission_id") or "").strip() or None,
    )


def finalize_logged_session(
    session: Session,
    *,
    existing: Optional[Session] = None,
    plan_exercises: Optional[Sequence[Any]] = None,
    finish: bool = False,
    submission_id: str = "",
    catalog: Optional[dict] = None,
    week_sessions: Optional[Sequence[Session]] = None,
) -> Session:
    """Reassess after a save. Does not force a new session open or closed.

    Finish workout marks uncovered lifts skipped when something was covered.
    Zero covered lifts (an empty Finish) is a missed session: no streak and
    no rotation credit. A legacy finished row stays finished. A quest-seed
    row plus a manual log stays open until the plan is covered or Finish.
    """
    from .session_work import (
        STATUS_COMPLETE,
        STATUS_MISSED,
        STATUS_OPEN,
        freeze_plan,
        reassess_adjust_enabled,
        reassess_plan,
        resolve_catalog,
        session_work_done,
        skipped_from_uncovered,
        stored_status,
        week_window,
        _any_working_set,
        _plan_leaves,
    )
    from .training_day import session_has_close_stamp, session_is_quest_seed_only

    catalog = resolve_catalog(catalog)
    if submission_id:
        session.submission_id = submission_id
    if not session.plan_objective and plan_exercises:
        frozen = freeze_plan(plan_exercises, session.session_type)
        if frozen:
            session.plan_objective = frozen
    window = week_window(week_sessions, session.date)
    if (
        session.plan_objective
        and reassess_adjust_enabled()
        and not finish
    ):
        adjusted = reassess_plan(
            session,
            session.plan_objective,
            enabled=True,
            catalog=catalog,
            week_sessions=window,
        )
        session.plan_objective = adjusted.get("plan") or session.plan_objective
    decision = session_work_done(session, session.plan_objective, catalog)
    if decision.substitutions:
        session.substitutions = decision.substitutions
    if finish:
        # Nothing covered: missed, not a finished day. Do not credit streak
        # or rotation, and do not invent skipped failures.
        if decision.covered_count == 0 and (
            not _any_working_set(session) or _plan_leaves(session.plan_objective)
        ):
            session.status = STATUS_MISSED
            session.skipped = None
            return session
        session.status = STATUS_COMPLETE
        session.skipped = skipped_from_uncovered(decision.uncovered) or None
        return session
    prior = stored_status(existing) if existing is not None else None
    prior_finished = prior in (STATUS_COMPLETE, "partial")
    if existing is not None and prior is None:
        prior_finished = bool(
            session_has_close_stamp(existing)
            and not session_is_quest_seed_only(existing)
        )
    if prior_finished:
        # A day already rolled to partial (or a finished row) keeps that
        # close. The skipped list still has to match what is covered now,
        # or a late log leaves the newly logged lift marked skipped.
        session.status = prior or STATUS_COMPLETE
        session.skipped = skipped_from_uncovered(decision.uncovered) or None
        return session
    if decision.done:
        session.status = STATUS_COMPLETE
        session.skipped = []
    else:
        session.status = STATUS_OPEN
    return session
