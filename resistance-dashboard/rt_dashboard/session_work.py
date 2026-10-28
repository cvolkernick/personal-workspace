"""Partial-session completion. One place the coach can retune later.

``session_work_done(session, plan)`` is the reassess decision. A save
(quest check-off or Add to log) does not force the session open or
closed; it records the sets, then asks this function. ``Finish workout``
is the only manual override and always completes.

Legacy rows (no ``status``) stay complete. Nothing here rewrites them.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import ExerciseEntry, Session, SetEntry

STATUS_OPEN = "in_progress"
STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_MISSED = "missed"
_EXPLICIT = (STATUS_OPEN, STATUS_COMPLETE, STATUS_PARTIAL, STATUS_MISSED)

# Shipped default. Rule 6 trim/add stays off until the owner turns it on.
REASSESS_ADJUST = {"enabled": False}
WEEKLY_SET_FLOOR = 4
WARMUP_LOAD_FRACTION = 0.5

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LOG = logging.getLogger("fitdash.session_work")


class WorkDecision:
    """Bool-like completion decision plus the short reason and the lift lists."""

    def __init__(
        self,
        done: bool,
        reason: str,
        *,
        covered: Optional[List[dict]] = None,
        uncovered: Optional[List[dict]] = None,
        substitutions: Optional[List[dict]] = None,
    ):
        self.done = bool(done)
        self.reason = reason
        self.covered = list(covered or [])
        self.uncovered = list(uncovered or [])
        self.substitutions = list(substitutions or [])

    def __bool__(self) -> bool:
        return self.done

    @property
    def covered_count(self) -> int:
        return len(self.covered)

    @property
    def planned_count(self) -> int:
        return len(self.covered) + len(self.uncovered)


def reassess_adjust_enabled() -> bool:
    """Rule 6. Default off. ``FITDASH_REASSESS_ADJUST=1`` turns trim/add on."""
    raw = (os.environ.get("FITDASH_REASSESS_ADJUST") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return bool(REASSESS_ADJUST.get("enabled"))


def stored_status(session: Any) -> Optional[str]:
    if isinstance(session, dict):
        raw = session.get("status")
    else:
        raw = getattr(session, "status", None)
    text = str(raw or "").strip().lower()
    if text in _EXPLICIT:
        return text
    return None


def _session_field(session: Any, name: str, default: Any = None) -> Any:
    if isinstance(session, dict):
        return session.get(name, default)
    return getattr(session, name, default)


def _exercises(session: Any) -> list:
    rows = _session_field(session, "exercises", None) or []
    return list(rows)


def _plan_leaves(plan: Any) -> List[dict]:
    if not isinstance(plan, dict):
        return []
    leaves = []
    for raw in plan.get("leaves") or []:
        if isinstance(raw, dict) and str(raw.get("slug") or raw.get("name") or "").strip():
            leaves.append(raw)
    return leaves


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def _leaf_name(leaf: dict) -> str:
    name = str(leaf.get("name") or "").strip()
    if name:
        return name
    title = str(leaf.get("title") or "").strip()
    if " (" in title:
        return title.split(" (", 1)[0].strip()
    return title


def _leaf_sets(leaf: dict) -> int:
    try:
        n = int(leaf.get("sets") or 0)
    except (TypeError, ValueError):
        n = 0
    return max(0, n)


def _num(value: Any) -> Optional[float]:
    if value is None or value is False or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _set_weight(st: Any) -> float:
    if isinstance(st, dict):
        return float(_num(st.get("weight_lbs")) or 0)
    return float(_num(getattr(st, "weight_lbs", None)) or 0)


def _set_count(st: Any) -> int:
    raw = st.get("sets") if isinstance(st, dict) else getattr(st, "sets", 0)
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _set_reps(st: Any) -> int:
    raw = st.get("reps") if isinstance(st, dict) else getattr(st, "reps", 0)
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _set_flag(st: Any, *names: str) -> Any:
    for name in names:
        if isinstance(st, dict) and st.get(name) is not None:
            return st.get(name)
        if not isinstance(st, dict) and getattr(st, name, None) is not None:
            return getattr(st, name)
    return None


def is_warmup_set(st: Any, working_load: Optional[float]) -> bool:
    """Flagged warm-up, or under half of that exercise's working load."""
    flag = _set_flag(st, "warmup", "is_warmup", "warm_up")
    if flag in (True, 1, "1", "true", "yes"):
        return True
    load = _num(working_load)
    if load is None or load <= 0:
        return False
    return _set_weight(st) + 1e-9 < WARMUP_LOAD_FRACTION * load


def _exercise_sets(ex: Any) -> list:
    if isinstance(ex, ExerciseEntry) or hasattr(ex, "sets"):
        return list(getattr(ex, "sets", None) or [])
    if isinstance(ex, dict):
        raw = ex.get("sets")
        if isinstance(raw, list):
            return raw
    return []


def _exercise_name(ex: Any) -> str:
    if isinstance(ex, dict):
        return str(ex.get("name") or ex.get("exercise") or "")
    return str(getattr(ex, "name", "") or "")


def _is_seed(ex: Any) -> bool:
    if isinstance(ex, dict):
        if ex.get("quest_seeded") or ex.get("movement_only"):
            return True
        return str(ex.get("raw") or "").startswith("quest-seeded:")
    if getattr(ex, "quest_seeded", False):
        return True
    return str(getattr(ex, "raw", "") or "").startswith("quest-seeded:")


def working_sets(ex: Any, planned_weight: Optional[float] = None) -> list:
    """Logged sets that count. Warm-ups are dropped. Quest checks count.

    The 50% line uses the heaviest non-flagged load that was actually
    logged, not the planned weight. A heavier later set does not become
    the first working set when the earlier set is still at least half.
    ``planned_weight`` is ignored and kept so older callers stay valid.
    """
    del planned_weight
    rows = []
    for st in _exercise_sets(ex):
        if _set_count(st) <= 0 or _set_reps(st) <= 0:
            continue
        rows.append(st)
    load = 0.0
    for st in rows:
        if is_warmup_set(st, None):
            continue
        load = max(load, _set_weight(st))
    out = []
    for st in rows:
        if is_warmup_set(st, load if load > 0 else None):
            continue
        out.append(st)
    return out


def working_set_count(ex: Any, planned_weight: Optional[float] = None) -> int:
    return sum(_set_count(st) for st in working_sets(ex, planned_weight))


def sets_required(planned_sets: int) -> int:
    """Covered at max(1, planned working sets − 1)."""
    try:
        n = int(planned_sets or 0)
    except (TypeError, ValueError):
        n = 0
    return max(1, n - 1)


_CATALOG_CACHE: Dict[str, Any] = {"mtime": None, "path": "", "data": None}


def _catalog_source() -> Tuple[Optional[float], str]:
    """Mtime of the catalog file the workspace loader would read."""
    try:
        from .workout_planner import CATALOG_PATH
        from .workout_store import _workspace_file_candidates
    except Exception:
        _LOG.warning("catalog path lookup failed", exc_info=True)
        return None, ""
    for path in _workspace_file_candidates(CATALOG_PATH):
        try:
            if path.is_file():
                return path.stat().st_mtime, str(path)
        except OSError:
            _LOG.warning("catalog stat failed for %s", path, exc_info=True)
    return None, ""


def load_exercise_catalog() -> dict:
    """Exercise catalog, reloaded when the file mtime changes.

    A failed load is logged and not cached, so the next call tries again.
    Tests may pass an override to ``resolve_catalog`` instead.
    """
    global _CATALOG_CACHE
    mtime, path = _catalog_source()
    cached = _CATALOG_CACHE.get("data") if isinstance(_CATALOG_CACHE, dict) else None
    if (
        isinstance(cached, dict)
        and cached.get("exercises")
        and path
        and _CATALOG_CACHE.get("mtime") == mtime
        and _CATALOG_CACHE.get("path") == path
    ):
        return cached
    cat: dict = {}
    try:
        from .workout_store import load_workspace_catalog

        loaded, _src = load_workspace_catalog()
        if isinstance(loaded, dict):
            cat = loaded
    except Exception:
        _LOG.warning("catalog load failed", exc_info=True)
        cat = {}
    if not isinstance(cat, dict) or not cat.get("exercises"):
        try:
            from .workout_planner import default_catalog

            loaded = default_catalog()
            if isinstance(loaded, dict):
                cat = loaded
        except Exception:
            _LOG.warning("bundled catalog load failed", exc_info=True)
            cat = {}
    if not isinstance(cat, dict) or not cat.get("exercises"):
        if isinstance(cached, dict) and cached.get("exercises"):
            _LOG.warning("catalog reload empty; keeping the previous catalog")
            return cached
        _LOG.warning("catalog load produced no exercises")
        return {"exercises": []}
    _CATALOG_CACHE = {"mtime": mtime, "path": path, "data": cat}
    return cat


def resolve_catalog(catalog: Optional[dict] = None) -> dict:
    """Caller override wins. Otherwise the mtime-cached workspace catalog."""
    if isinstance(catalog, dict) and catalog.get("exercises"):
        return catalog
    return load_exercise_catalog()


def _catalog_index(catalog: Optional[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for ex in (catalog or {}).get("exercises") or []:
        if isinstance(ex, dict):
            key = _norm(str(ex.get("name") or ex.get("id") or ""))
            if key:
                out[key] = ex
            eid = _norm(str(ex.get("id") or ""))
            if eid:
                out[eid] = ex
    return out


def _catalog_row(name: str, catalog: Optional[dict]) -> dict:
    idx = _catalog_index(catalog)
    hit = idx.get(_norm(name))
    return hit if isinstance(hit, dict) else {}


def _same_slot(planned_name: str, logged_name: str, catalog: Optional[dict]) -> bool:
    """Catalog guard: same primary muscle and the same movement pattern."""
    if _norm(planned_name) == _norm(logged_name):
        return True
    catalog = resolve_catalog(catalog)
    if not catalog.get("exercises"):
        return False
    left = _catalog_row(planned_name, catalog)
    right = _catalog_row(logged_name, catalog)
    if not left or not right:
        return False
    from .workout_planner import normalize_muscle, pattern_family

    lp = {normalize_muscle(m) for m in (left.get("primary_muscles") or []) if m}
    rp = {normalize_muscle(m) for m in (right.get("primary_muscles") or []) if m}
    if not lp or not rp or not (lp & rp):
        return False
    lf = pattern_family(left)
    rf = pattern_family(right)
    if lf and rf:
        return lf == rf
    lm = str(left.get("movement") or "").lower()
    rm = str(right.get("movement") or "").lower()
    return bool(lm and lm == rm)


def _match_exercise(
    leaf: dict,
    exercises: Sequence[Any],
    catalog: Optional[dict],
    used: Optional[set] = None,
):
    """Return (exercise, substitution or None). One log covers one slot."""
    name = _leaf_name(leaf)
    want = _norm(name)
    taken = used or set()
    for ex in exercises:
        if id(ex) in taken:
            continue
        if _norm(_exercise_name(ex)) == want:
            return ex, None
    for ex in exercises:
        if id(ex) in taken:
            continue
        logged = _exercise_name(ex)
        if logged and _same_slot(name, logged, catalog):
            return ex, {
                "planned_name": name,
                "planned_slug": str(leaf.get("slug") or ""),
                "logged_name": logged,
                "disposition": "substitution",
            }
    return None, None


def session_work_done(session: Any, plan: Any, catalog: Optional[dict] = None) -> WorkDecision:
    """True only when every planned lift is covered.

    No plan → not done (one hand-logged lift must not close the day).
    A different weight or reps still covers. Warm-ups do not.
    A swap covers the slot only through the catalog guard and is recorded
    as a substitution; it is not a log of the original lift.
    """
    leaves = _plan_leaves(plan)
    if not leaves:
        return WorkDecision(False, "no_plan")
    catalog = resolve_catalog(catalog)
    exercises = _exercises(session)
    covered: List[dict] = []
    uncovered: List[dict] = []
    subs: List[dict] = []
    used: set = set()
    for leaf in leaves:
        name = _leaf_name(leaf)
        need = sets_required(_leaf_sets(leaf))
        planned_w = _num(leaf.get("weight_lbs"))
        ex, sub = _match_exercise(leaf, exercises, catalog, used)
        if ex is not None:
            used.add(id(ex))
        got = working_set_count(ex, planned_w) if ex is not None else 0
        row = {
            "name": name,
            "slug": str(leaf.get("slug") or ""),
            "planned_sets": _leaf_sets(leaf),
            "required_sets": need,
            "logged_sets": got,
        }
        if ex is not None and got >= need:
            if sub:
                row["substitution"] = sub
                subs.append(sub)
            covered.append(row)
        else:
            uncovered.append(row)
    if uncovered:
        return WorkDecision(
            False,
            "planned_lifts_uncovered",
            covered=covered,
            uncovered=uncovered,
            substitutions=subs,
        )
    return WorkDecision(
        True,
        "all_planned_lifts_covered",
        covered=covered,
        uncovered=[],
        substitutions=subs,
    )


def skipped_from_uncovered(uncovered: Sequence[dict]) -> List[dict]:
    """Uncovered lifts are skipped, never failed. No load drop."""
    out = []
    for row in uncovered or []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "name": row.get("name") or "",
                "slug": row.get("slug") or "",
                "disposition": "skipped",
            }
        )
    return out


def _session_day(session: Any) -> str:
    return str(_session_field(session, "date", "") or "")[:10]


def _training_today(now: Any = None, tz_name: Optional[str] = None) -> str:
    from .training_day import training_day_iso

    return training_day_iso(now=now, tz_name=tz_name or "America/New_York")


def day_has_rolled(session: Any, *, now: Any = None, tz_name: Optional[str] = None) -> bool:
    """True once the app's 4 AM ET training day is after the session's day."""
    day = _session_day(session)
    if len(day) < 10:
        return False
    return day < _training_today(now, tz_name)


def _any_working_set(session: Any) -> bool:
    for ex in _exercises(session):
        if working_set_count(ex) > 0:
            return True
    return False


def effective_status(
    session: Any,
    *,
    now: Any = None,
    tz_name: Optional[str] = None,
    catalog: Optional[dict] = None,
) -> str:
    """Read-time status. Open sessions roll at 4 AM. Legacy stays complete.

    ``legacy`` — saved before this field existed (still a finished session).
    ``open_legacy`` — a date-only or quest-seed row that was never a finish.
    """
    raw = stored_status(session)
    if raw == STATUS_OPEN and day_has_rolled(session, now=now, tz_name=tz_name):
        plan = _session_field(session, "plan_objective", None)
        catalog = resolve_catalog(catalog)
        if _plan_leaves(plan):
            decision = session_work_done(session, plan, catalog)
            covered = decision.covered_count
        else:
            covered = 1 if _any_working_set(session) else 0
        return STATUS_PARTIAL if covered >= 1 else STATUS_MISSED
    if raw:
        return raw
    from .training_day import session_has_close_stamp, session_is_quest_seed_only

    if not session_has_close_stamp(session) or session_is_quest_seed_only(session):
        return "open_legacy"
    return "legacy"


def session_counts_for_streak(
    session: Any, *, now: Any = None, tz_name: Optional[str] = None
) -> bool:
    """Complete, partial, and legacy history credit the streak.

    Open and missed do not. A date-only row from before ``status`` existed
    is still history. An explicit in-progress row is not, until the 4 AM
    roll turns it partial.
    """
    from .training_day import session_is_quest_seed_only

    status = effective_status(session, now=now, tz_name=tz_name)
    if status in (STATUS_OPEN, STATUS_MISSED):
        return False
    if status == "open_legacy":
        return not session_is_quest_seed_only(session)
    return True


def session_advances_rotation(
    session: Any, *, now: Any = None, tz_name: Optional[str] = None
) -> bool:
    """Same set as the streak. A miss does not move the rotation."""
    return session_counts_for_streak(session, now=now, tz_name=tz_name)


def session_counts_for_progression(
    session: Any, *, now: Any = None, tz_name: Optional[str] = None
) -> bool:
    """Finished sessions only. Explicit open and missed rows are ignored.

    Date-only legacy rows (no close stamp, no status) still count. Stored
    history is built that way in the planner, and a persisted row always
    gains ``created_at`` on read. Dropping them here would ignore real logs.
    """
    return session_counts_for_streak(session, now=now, tz_name=tz_name)


def annotate_day_roll(
    session: Session,
    *,
    now: Any = None,
    tz_name: Optional[str] = None,
    catalog: Optional[dict] = None,
) -> Session:
    """Apply the 4 AM roll on the object.

    Sets ``day_roll_pending`` when status moved off ``in_progress`` so the
    repository can write partial/missed and skipped lifts. Callers that
    only hold a dict still see the rolled object.
    """
    if stored_status(session) != STATUS_OPEN:
        return session
    if not day_has_rolled(session, now=now, tz_name=tz_name):
        return session
    catalog = resolve_catalog(catalog)
    plan = session.plan_objective
    if _plan_leaves(plan):
        decision = session_work_done(session, plan, catalog)
        if decision.covered_count >= 1:
            session.status = STATUS_PARTIAL
            session.skipped = skipped_from_uncovered(decision.uncovered)
            session.substitutions = decision.substitutions or session.substitutions
        else:
            session.status = STATUS_MISSED
    elif _any_working_set(session):
        session.status = STATUS_PARTIAL
    else:
        session.status = STATUS_MISSED
    if session.status in (STATUS_PARTIAL, STATUS_MISSED):
        setattr(session, "day_roll_pending", True)
    return session


def progress_label(session: Any, plan: Any, catalog: Optional[dict] = None) -> str:
    decision = session_work_done(session, plan, catalog)
    if decision.planned_count:
        return f"{decision.covered_count} of {decision.planned_count} exercises"
    n = len([ex for ex in _exercises(session) if _exercise_name(ex)])
    return f"{n} of {n} exercises" if n else "0 of 0 exercises"


def workout_progress(
    session: Any,
    plan: Any,
    *,
    now: Any = None,
    tz_name: Optional[str] = None,
    catalog: Optional[dict] = None,
) -> Optional[dict]:
    status = effective_status(session, now=now, tz_name=tz_name, catalog=catalog)
    if status != STATUS_OPEN:
        return None
    decision = session_work_done(session, plan or _session_field(session, "plan_objective"), catalog)
    if not decision.planned_count and not _exercises(session):
        return None
    label = progress_label(session, plan or _session_field(session, "plan_objective"), catalog)
    return {
        "in_progress": True,
        "label": label,
        "covered": decision.covered_count,
        "planned": decision.planned_count,
        "status": STATUS_OPEN,
    }


def freeze_plan(exercises: Sequence[Any], session_type: str) -> Optional[dict]:
    """Snapshot the plan that was on screen when the session started."""
    from .training_day import lift_leaf_key

    leaves = []
    for raw in exercises or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("exercise") or "").strip()
        if not name:
            continue
        rx = raw.get("prescription") if isinstance(raw.get("prescription"), dict) else {}
        sets = raw.get("sets")
        if isinstance(sets, list):
            sets_n = 0
            for st in sets:
                if isinstance(st, dict):
                    sets_n += int(st.get("sets") or 0)
            sets = sets_n or None
        if sets is None:
            sets = rx.get("sets") if rx.get("sets") is not None else raw.get("planned_sets")
        reps = raw.get("reps")
        if reps is None:
            reps = rx.get("reps")
        weight = raw.get("weight_lbs")
        if weight is None:
            weight = rx.get("weight_lbs") if rx.get("weight_lbs") is not None else raw.get("load")
        try:
            sets_i = max(0, int(sets or 0))
        except (TypeError, ValueError):
            sets_i = 0
        try:
            reps_i = max(0, int(reps or 0))
        except (TypeError, ValueError):
            reps_i = 0
        weight_f = _num(weight) or 0.0
        if sets_i <= 0:
            sets_i = 1
        leaves.append(
            {
                "slug": lift_leaf_key(name),
                "name": name,
                "title": str(raw.get("title") or name)[:200],
                "sets": sets_i,
                "reps": reps_i,
                "weight_lbs": weight_f,
                "volume": weight_f * sets_i * reps_i,
                "movement": str(raw.get("movement") or ""),
                "primary_muscles": list(raw.get("primary_muscles") or []),
            }
        )
    if not leaves:
        return None
    return {
        "frozen": True,
        "session_type": str(session_type or "").lower(),
        "target_n": len(leaves),
        "leaves": leaves,
    }


def week_window(sessions: Optional[Sequence[Any]], day: str) -> List[Any]:
    """Sessions in the 7 civil days ending on ``day``, inclusive."""
    from datetime import datetime, timedelta

    try:
        end = datetime.strptime(str(day or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return list(sessions or [])
    start = end - timedelta(days=6)
    out = []
    for sess in sessions or []:
        raw = _session_day(sess)
        try:
            got = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        if start <= got <= end:
            out.append(sess)
    return out


def session_plan_objective(
    sessions: Sequence[Any], *, day: str = "", letter: str = ""
) -> Optional[dict]:
    """Frozen plan carried on the session, if this day started one."""
    want_day = str(day or "")[:10]
    want = str(letter or "").lower()
    for session in sessions or []:
        if want_day and _session_day(session) != want_day:
            continue
        st = str(_session_field(session, "session_type", "") or "").lower()
        if want and st != want:
            continue
        plan = _session_field(session, "plan_objective", None)
        if _plan_leaves(plan):
            return plan
    return None


def _muscle_of(name: str, catalog: Optional[dict]) -> str:
    row = _catalog_row(name, catalog)
    muscles = row.get("primary_muscles") or []
    if muscles:
        from .workout_planner import normalize_muscle

        return normalize_muscle(muscles[0])
    return ""


def _is_compound(name: str, catalog: Optional[dict], leaf: Optional[dict] = None) -> bool:
    if isinstance(leaf, dict) and str(leaf.get("movement") or "").lower() == "compound":
        return True
    row = _catalog_row(name, catalog)
    return str(row.get("movement") or "").lower() == "compound"


def _first_working(ex: Any, planned_weight: Optional[float]) -> Optional[Any]:
    rows = working_sets(ex, planned_weight)
    return rows[0] if rows else None


def reassess_plan(
    session: Any,
    plan: Any,
    *,
    enabled: bool = False,
    catalog: Optional[dict] = None,
    week_sessions: Optional[Sequence[Any]] = None,
) -> dict:
    """Rule 6. Off by default: the plan's sets and loads stay put.

    On: trim one set of remaining same-muscle work when a compound's first
    working set was ≤4 reps or 0 RIR, without dropping that muscle under
    4 sets for the week. Add at most one lift, and only to fill a muscle
    under that floor. Loads on existing lifts are not changed.
    """
    base = plan if isinstance(plan, dict) else {}
    if not enabled:
        return {"enabled": False, "changed": False, "plan": base, "reason": "reassess_adjust disabled"}
    catalog = resolve_catalog(catalog)
    import copy

    nxt = copy.deepcopy(base)
    leaves = [x for x in (nxt.get("leaves") or []) if isinstance(x, dict)]
    nxt["leaves"] = leaves
    notes: List[str] = []
    decision = session_work_done(session, nxt, catalog)
    uncovered_names = {_norm(row.get("name") or "") for row in decision.uncovered}

    def weekly(muscle: str) -> int:
        total = 0
        current_key = (
            _session_day(session),
            str(_session_field(session, "session_type", "") or "").lower(),
        )
        seen = set()
        ordered = []
        for sess in list(week_sessions or []):
            key = (
                _session_day(sess),
                str(_session_field(sess, "session_type", "") or "").lower(),
            )
            if key == current_key or key in seen:
                continue
            seen.add(key)
            ordered.append(sess)
        ordered.append(session)
        for sess in ordered:
            for ex in _exercises(sess):
                if _muscle_of(_exercise_name(ex), catalog) != muscle:
                    continue
                total += working_set_count(ex)
        for leaf in leaves:
            if _norm(_leaf_name(leaf)) not in uncovered_names:
                continue
            if _muscle_of(_leaf_name(leaf), catalog) == muscle:
                total += _leaf_sets(leaf)
        return total

    trimmed = False
    for ex in _exercises(session):
        name = _exercise_name(ex)
        if not _is_compound(name, catalog):
            continue
        leaf = next((x for x in leaves if _norm(_leaf_name(x)) == _norm(name)), None)
        planned_w = _num(leaf.get("weight_lbs")) if isinstance(leaf, dict) else None
        first = _first_working(ex, planned_w)
        if first is None:
            continue
        reps = _set_reps(first)
        rir = _set_flag(first, "rir")
        try:
            rir_n = int(rir) if rir is not None and rir != "" else None
        except (TypeError, ValueError):
            rir_n = None
        if reps > 4 and rir_n != 0:
            continue
        muscle = _muscle_of(name, catalog)
        if not muscle:
            continue
        for leaf in leaves:
            if _norm(_leaf_name(leaf)) not in uncovered_names:
                continue
            if _muscle_of(_leaf_name(leaf), catalog) != muscle:
                continue
            if _leaf_sets(leaf) <= 1:
                continue
            if weekly(muscle) - 1 < WEEKLY_SET_FLOOR:
                continue
            leaf["sets"] = _leaf_sets(leaf) - 1
            w = _num(leaf.get("weight_lbs")) or 0.0
            leaf["volume"] = w * int(leaf["sets"]) * int(leaf.get("reps") or 0)
            notes.append(f"trim 1 set {leaf.get('name')}")
            trimmed = True
            break
        if trimmed:
            break

    added = False
    session_type = str(_session_field(session, "session_type", "") or "").lower()
    if catalog.get("exercises") and not added:
        seen = {_norm(_leaf_name(x)) for x in leaves}
        muscles_open = []
        for ex in (catalog.get("exercises") or []):
            if not isinstance(ex, dict):
                continue
            types = ex.get("session_types") or ex.get("session_type") or []
            if isinstance(types, str):
                types = [types]
            if session_type not in {str(t).lower() for t in types}:
                continue
            muscle = ""
            prim = ex.get("primary_muscles") or []
            if prim:
                from .workout_planner import normalize_muscle

                muscle = normalize_muscle(prim[0])
            if not muscle or weekly(muscle) >= WEEKLY_SET_FLOOR:
                continue
            name = str(ex.get("name") or "").strip()
            if not name or _norm(name) in seen:
                continue
            muscles_open.append((muscle, ex))
        if muscles_open:
            muscle, ex = muscles_open[0]
            from .training_day import lift_leaf_key

            name = str(ex.get("name") or "").strip()
            sets_i = 2
            reps_i = int(ex.get("default_reps") or 8)
            leaves.append(
                {
                    "slug": lift_leaf_key(name),
                    "name": name,
                    "title": name,
                    "sets": sets_i,
                    "reps": reps_i,
                    "weight_lbs": 0.0,
                    "volume": 0.0,
                    "movement": str(ex.get("movement") or ""),
                    "primary_muscles": list(ex.get("primary_muscles") or []),
                    "added_by_reassess": True,
                }
            )
            notes.append(f"add {name} for {muscle}")
            added = True
    nxt["target_n"] = len(leaves)
    nxt["frozen"] = bool(leaves) or bool(nxt.get("frozen"))
    changed = bool(notes)
    return {
        "enabled": True,
        "changed": changed,
        "plan": nxt,
        "reason": "; ".join(notes) if notes else "no adjustment",
    }


def apply_set_flags(entry: SetEntry, raw: dict) -> None:
    if raw.get("warmup") or raw.get("is_warmup") or raw.get("warm_up"):
        entry.warmup = True
    if raw.get("rir") is not None and raw.get("rir") != "":
        try:
            entry.rir = int(raw.get("rir"))
        except (TypeError, ValueError):
            entry.rir = None


def log_day_from_value(value: Any, *, now: Any = None, tz_name: Optional[str] = None) -> str:
    """Civil training day in the app zone. A UTC timestamp is not sliced.

    A bare ``YYYY-MM-DD`` is the picker's day and is kept. A datetime is
    the 4 AM training day in America/New_York (or the request zone), so
    23:36 ET stays on that evening instead of the next UTC date.
    """
    from .training_day import parse_dt, training_day_for, training_day_iso

    tz = tz_name or "America/New_York"
    raw = str(value or "").strip()
    if _DATE_ONLY.match(raw):
        return raw
    if raw:
        dt = parse_dt(raw)
        if dt is not None:
            return training_day_for(dt, tz_name=tz) or training_day_iso(now=dt, tz_name=tz)
    return training_day_iso(now=now, tz_name=tz)
