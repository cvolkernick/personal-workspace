"""Weekly proper-set volume by muscle group (#1193).

A proper set is a logged working set in the lift's rep window (band ± 1)
at about 85% of that lift's planned working load. Warm-ups, ramps, quest
seeds, and ``Already trained today`` rows do not count. Assisted lifts use
effective load (#1156) and still count on reps when that load is missing.

Credit is 1.0 to each primary group and 0.5 to each secondary. It is not
split across primaries. The tile and the agent feed count a rolling 7
ET civil days (today and the six days before it, #1297). The cap, the
Thursday flags, and the planner still use Monday–Sunday America/New_York.
``et_week_bounds`` stays the calendar-week helper for those and for
tonnage.

Priority groups (8–10): side delts, lats/upper back, chest.
Middle (4–7): hamstrings, rear delts, biceps, triceps.
Every other group holds a maintenance floor of 4.

The weekly cap is the rounded 4-week average of proper-set totals. It
moves at most ±10% from the prior Monday and does not rise while the
tonnage guardrail is red or Restore has called a deload. A manual
override field stays empty unless goals set ``muscle_set_cap_override``.
Priority groups may pass 10 only when tonnage is green and the cap still
has room. Thursday flags are suggestions. Nothing is auto-added.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

VOLUME_GROUPS: Tuple[str, ...] = (
    "chest",
    "front_delts",
    "side_delts",
    "rear_delts",
    "lats_upper_back",
    "traps",
    "biceps",
    "triceps",
    "quads",
    "hamstrings",
    "glutes",
    "calves",
    "abs",
    "lower_back",
)
GROUP_LABELS: Dict[str, str] = {
    "chest": "chest",
    "front_delts": "front delts",
    "side_delts": "side delts",
    "rear_delts": "rear delts",
    "lats_upper_back": "lats/upper back",
    "traps": "traps",
    "biceps": "biceps",
    "triceps": "triceps",
    "quads": "quads",
    "hamstrings": "hamstrings",
    "glutes": "glutes",
    "calves": "calves",
    "abs": "abs",
    "lower_back": "lower back",
}

# Shape drivers first. Chest is the first priority to drop on a tie.
PRIORITY_GROUPS: Tuple[str, ...] = ("side_delts", "lats_upper_back", "chest")
MIDDLE_GROUPS: Tuple[str, ...] = ("hamstrings", "rear_delts", "biceps", "triceps")
DROP_ORDER: Tuple[str, ...] = ("chest", "lats_upper_back", "side_delts")

PRIORITY_MIN = 8
PRIORITY_MAX = 10
MIDDLE_MIN = 4
MIDDLE_MAX = 7
FLOOR_MIN = 4
LOAD_FRACTION = 0.85
REP_SLACK = 1
# Sessions older than this many weeks before the open Monday are not read.
# Four weeks feed the average and the rest is the cap's ±10% walk (#1267).
HISTORY_WEEKS = 10
# Largest manual weekly cap (proper sets) that is read. A real week is far
# below this, and anything above it is treated as a typo and ignored (#1267).
MAX_CAP_OVERRIDE = 1000

# Catalog tags that are not one of the 14 groups. Forearms and adductors
# stay off the tracker. ``back`` on a hinge is lower back only when the
# row's volume_secondary says so; the generic fallback treats ``back`` as
# lats/upper back because that is the row pattern.
_TAG_TO_GROUP = {
    "chest": "chest",
    "pecs": "chest",
    "front_delts": "front_delts",
    "front_delt": "front_delts",
    "side_delts": "side_delts",
    "side_delt": "side_delts",
    "lateral_delts": "side_delts",
    "rear_delts": "rear_delts",
    "rear_delt": "rear_delts",
    "shoulders": "front_delts",
    "shoulder": "front_delts",
    "delts": "front_delts",
    "lats": "lats_upper_back",
    "lat": "lats_upper_back",
    "back": "lats_upper_back",
    "upper_back": "lats_upper_back",
    "mid_upper_back": "lats_upper_back",
    "lats_upper_back": "lats_upper_back",
    "traps": "traps",
    "biceps": "biceps",
    "triceps": "triceps",
    "quads": "quads",
    "hamstrings": "hamstrings",
    "glutes": "glutes",
    "calves": "calves",
    "abs": "abs",
    "core": "abs",
    "lower_back": "lower_back",
}


def _round_half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def clean_cap_override(raw: Any) -> Optional[int]:
    """A manual weekly cap as a whole number, or None.

    Only a finite number from 1 to ``MAX_CAP_OVERRIDE`` is read. Booleans,
    text that is not a number, NaN, infinities, zero, negatives, and values
    above the ceiling are dropped without raising.
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or value <= 0 or value > MAX_CAP_OVERRIDE:
        return None
    whole = int(value + 0.5)
    return whole if whole >= 1 else None


def _round_sets(value: float) -> float:
    return round(float(value) + 0.0, 2)


def et_civil_date(moment: Any) -> Optional[date]:
    """Civil date in America/New_York. A ``YYYY-MM-DD`` string is already civil."""
    if moment is None:
        return None
    if isinstance(moment, date) and not isinstance(moment, datetime):
        return moment
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ET)
        return moment.astimezone(ET).date()
    text = str(moment).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        day_part = text[:10]
        if len(text) == 10:
            try:
                return date.fromisoformat(day_part)
            except ValueError:
                return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                return date.fromisoformat(day_part)
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ET)
        return parsed.astimezone(ET).date()
    return None


def et_week_bounds(moment: Any) -> Tuple[Optional[str], Optional[str]]:
    """Monday and Sunday (inclusive) of the America/New_York week."""
    day = et_civil_date(moment)
    if day is None:
        return None, None
    monday = day - timedelta(days=day.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def rolling_7d_bounds(moment: Any) -> Tuple[Optional[str], Optional[str]]:
    """Today and the six America/New_York civil days before it, inclusive.

    ``end`` is the ET civil date of ``moment``. A UTC timestamp that is
    already the next calendar day still belongs to the previous ET date
    until 00:00 ET.
    """
    day = et_civil_date(moment)
    if day is None:
        return None, None
    start = day - timedelta(days=6)
    return start.isoformat(), day.isoformat()


def rolling_7d_window(moment: Any) -> Dict[str, Any]:
    """Window object for the tile and ``/api/agent/*``."""
    start, end = rolling_7d_bounds(moment)
    return {
        "type": "rolling_7d",
        "start": start,
        "end": end,
        "label": "rolling 7 days",
    }


def tier_for(proper_sets: float) -> str:
    """0–3 red, 4–7 green, 8+ blue."""
    done = float(proper_sets or 0)
    if done < 4:
        return "red"
    if done < 8:
        return "green"
    return "blue"


def _token(name: str) -> str:
    return str(name or "").strip().lower().replace("-", "_").replace(" ", "_")


def _groups_from_tags(tags: Sequence[Any], *, name: str, movement: str) -> List[str]:
    out: List[str] = []
    for raw in tags or []:
        token = _token(str(raw))
        if token in ("shoulders", "shoulder", "delts", "delt"):
            lname = str(name or "").lower()
            if movement == "isolation" and any(
                bit in lname for bit in ("lateral", "side delt", "side-delt")
            ):
                group = "side_delts"
            else:
                group = "front_delts"
        else:
            group = _TAG_TO_GROUP.get(token)
        if group and group not in out:
            out.append(group)
    return out


def volume_groups(ex: Optional[dict]) -> Tuple[List[str], List[str]]:
    """Primary and secondary volume groups for a catalog row.

    Explicit ``volume_primary`` / ``volume_secondary`` win. Missing tags
    fall back to the catalog muscle list. Unknown tags (forearms,
    adductors) contribute nothing.
    """
    if not isinstance(ex, dict):
        return [], []
    name = str(ex.get("name") or "")
    movement = str(ex.get("movement") or "compound").strip().lower()
    raw_p = ex.get("volume_primary")
    raw_s = ex.get("volume_secondary")
    if isinstance(raw_p, list) or isinstance(raw_s, list):
        primary = [g for g in (_token(x) for x in (raw_p or [])) if g in VOLUME_GROUPS]
        secondary = [
            g
            for g in (_token(x) for x in (raw_s or []))
            if g in VOLUME_GROUPS and g not in primary
        ]
        return list(dict.fromkeys(primary)), list(dict.fromkeys(secondary))
    primary = _groups_from_tags(ex.get("primary_muscles") or [], name=name, movement=movement)
    secondary = [
        g
        for g in _groups_from_tags(
            ex.get("secondary_muscles") or [], name=name, movement=movement
        )
        if g not in primary
    ]
    return primary, secondary


def credit_proper_sets(
    primary: Sequence[str],
    secondary: Sequence[str],
    proper_sets: float,
) -> Dict[str, float]:
    """1.0 to each primary and 0.5 to each secondary. No split."""
    count = float(proper_sets or 0)
    if count <= 0:
        return {}
    prim: List[str] = []
    for raw in primary:
        group = _token(str(raw))
        if group in VOLUME_GROUPS and group not in prim:
            prim.append(group)
    sec: List[str] = []
    for raw in secondary:
        group = _token(str(raw))
        if group in VOLUME_GROUPS and group not in prim and group not in sec:
            sec.append(group)
    credits: Dict[str, float] = {}
    for group in prim:
        credits[group] = count * 1.0
    for group in sec:
        credits[group] = count * 0.5
    return credits


def rep_window(movement: str) -> Tuple[int, int]:
    """Compound 5–9, isolation 8–15, each expanded by one rep."""
    kind = str(movement or "compound").strip().lower()
    if kind == "isolation":
        lo, hi = 8, 15
    else:
        lo, hi = 5, 9
    return lo - REP_SLACK, hi + REP_SLACK


def reps_in_window(reps: int, movement: str) -> bool:
    lo, hi = rep_window(movement)
    return lo <= int(reps) <= hi


def _set_rows(ex: Any) -> List[dict]:
    if isinstance(ex, dict):
        raw_sets = ex.get("sets")
        weight = ex.get("weight_lbs")
        reps = ex.get("reps")
        count = ex.get("sets")
    else:
        raw_sets = getattr(ex, "sets", None)
        weight = getattr(ex, "weight_lbs", None)
        reps = getattr(ex, "reps", None)
        count = None
    rows: List[dict] = []
    if isinstance(raw_sets, list):
        for st in raw_sets:
            if isinstance(st, dict):
                rows.append(st)
            elif hasattr(st, "weight_lbs"):
                rows.append(
                    {
                        "weight_lbs": getattr(st, "weight_lbs", None),
                        "sets": getattr(st, "sets", 1),
                        "reps": getattr(st, "reps", 0),
                        "effective_load_lbs": getattr(st, "tonnage_weight_lbs", None),
                        "effective_load_source": getattr(st, "effective_load_source", None),
                    }
                )
        if rows:
            return rows
    if weight is not None and reps is not None and not isinstance(count, list):
        try:
            n = int(count or 1)
        except (TypeError, ValueError, OverflowError):
            n = 1
        return [{"weight_lbs": weight, "sets": n, "reps": reps}]
    return []


def _row_count(st: dict) -> int:
    try:
        return max(0, int(st.get("sets") or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _row_reps(st: dict) -> Optional[int]:
    try:
        return int(st.get("reps"))
    except (TypeError, ValueError, OverflowError):
        return None


def _is_assisted(ex: Optional[dict]) -> bool:
    if not isinstance(ex, dict):
        return False
    try:
        from .assisted_load import catalog_is_assisted

        return bool(catalog_is_assisted(ex))
    except Exception:  # noqa: BLE001
        return bool(ex.get("assisted"))


def row_load(st: dict, *, assisted: bool) -> Optional[float]:
    """Load used for the 85% test. Assisted rows use effective load."""
    source = st.get("effective_load_source")
    if assisted or source:
        if source == "nearest_weighin" and st.get("effective_load_lbs") is not None:
            try:
                return float(st["effective_load_lbs"])
            except (TypeError, ValueError):
                return None
        if source == "none" or assisted:
            return None
    if st.get("effective_load_lbs") is not None and assisted:
        try:
            return float(st["effective_load_lbs"])
        except (TypeError, ValueError):
            return None
    if st.get("weight_lbs") is None:
        return None
    try:
        return float(st["weight_lbs"])
    except (TypeError, ValueError):
        return None


def proper_set_count(
    rows: Sequence[dict],
    *,
    movement: str,
    planned_load: Optional[float],
    assisted: bool,
) -> int:
    """How many sets in these rows are proper.

    Rows outside the rep window never count. When a planned load is known,
    a row also needs load ≥ 85% of it. Assisted rows with no effective load
    count on reps alone. With no plan, the heaviest in-window row is the
    reference so ramps under that top set drop out.
    """
    qualified: List[Tuple[dict, Optional[float]]] = []
    for st in rows:
        reps = _row_reps(st)
        if reps is None or _row_count(st) <= 0:
            continue
        if not reps_in_window(reps, movement):
            continue
        qualified.append((st, row_load(st, assisted=assisted)))
    if not qualified:
        return 0
    reference = planned_load
    if reference is None or float(reference) <= 0:
        loads = [load for _st, load in qualified if load is not None and load > 0]
        reference = max(loads) if loads else None
    total = 0
    floor = None if reference is None or float(reference) <= 0 else LOAD_FRACTION * float(reference)
    for st, load in qualified:
        if floor is not None and load is not None and load + 1e-9 < floor:
            continue
        total += _row_count(st)
    return total


def _exercise_name(ex: Any) -> str:
    if isinstance(ex, dict):
        return str(ex.get("name") or ex.get("exercise") or "")
    return str(getattr(ex, "name", "") or "")


def _norm_name(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def _session_date(session: Any) -> Any:
    """Session date as stored.

    A civil ``YYYY-MM-DD`` stays a civil date. A timestamp is left intact so
    ``et_civil_date`` can place a UTC instant that is already the next
    calendar day on the America/New_York date (#1297). Slicing to 10
    characters would keep the UTC day.
    """
    if isinstance(session, dict):
        raw = session.get("date")
    else:
        raw = getattr(session, "date", None)
    if isinstance(raw, (date, datetime)):
        return raw
    return str(raw or "")


def _session_type(session: Any) -> str:
    if isinstance(session, dict):
        return str(session.get("session_type") or "").strip().lower()
    return str(getattr(session, "session_type", "") or "").strip().lower()


def _session_notes(session: Any) -> str:
    if isinstance(session, dict):
        return str(session.get("notes") or "")
    return str(getattr(session, "notes", "") or "")


def _session_exercises(session: Any) -> List[Any]:
    if isinstance(session, dict):
        raw = session.get("exercises") or []
    else:
        raw = getattr(session, "exercises", None) or []
    return list(raw)


def _is_deload_session(session: Any) -> bool:
    if isinstance(session, dict):
        flagged = bool(session.get("deload") or session.get("deload_week"))
    else:
        flagged = bool(getattr(session, "deload", False) or getattr(session, "deload_week", False))
    kind = _session_type(session)
    notes = _session_notes(session).lower()
    return flagged or kind == "deload" or "deload" in notes


def _catalog_index(catalog: Optional[dict]) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    by_id: Dict[str, dict] = {}
    by_name: Dict[str, dict] = {}
    for raw in (catalog or {}).get("exercises") or []:
        if not isinstance(raw, dict):
            continue
        eid = str(raw.get("id") or "").strip()
        if eid:
            by_id[eid] = raw
        name = _norm_name(str(raw.get("name") or ""))
        if name:
            by_name.setdefault(name, raw)
    return by_id, by_name


def _lookup_exercise(
    ex: Any,
    by_id: Mapping[str, dict],
    by_name: Mapping[str, dict],
    aliases: Mapping[str, str],
) -> Optional[dict]:
    if isinstance(ex, dict):
        eid = str(ex.get("id") or ex.get("exercise_id") or "").strip()
        if eid and eid in by_id:
            return by_id[eid]
    name = _norm_name(_exercise_name(ex))
    if name in by_name:
        return by_name[name]
    aliased = aliases.get(name) or aliases.get(_exercise_name(ex))
    if aliased:
        hit = by_name.get(_norm_name(aliased))
        if hit:
            return hit
    return None


def _movement_of(cat: Optional[dict], ex: Any) -> str:
    if isinstance(cat, dict) and cat.get("movement"):
        return str(cat.get("movement") or "compound").strip().lower()
    if isinstance(ex, dict) and ex.get("movement"):
        return str(ex.get("movement") or "compound").strip().lower()
    return "compound"


def _planned_load_index(payload: Mapping[str, Any]) -> Dict[str, float]:
    """Name → planned working load. Assisted plans prefer effective load."""
    store = payload.get("workout_store") if isinstance(payload.get("workout_store"), dict) else {}
    plan = store.get("plan") if isinstance(store.get("plan"), dict) else {}
    if not plan:
        workout = payload.get("workout") if isinstance(payload.get("workout"), dict) else {}
        plan = workout
    out: Dict[str, float] = {}
    for raw in plan.get("exercises") or []:
        if not isinstance(raw, dict):
            continue
        name = _norm_name(str(raw.get("name") or ""))
        if not name:
            continue
        load = raw.get("effective_load_lbs")
        if load is None:
            rx = raw.get("prescription") if isinstance(raw.get("prescription"), dict) else {}
            load = raw.get("weight_lbs")
            if load is None:
                load = rx.get("weight_lbs") or raw.get("load")
        try:
            if load is None:
                continue
            value = float(load)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            out[name] = value
    return out


def _alias_map(payload: Mapping[str, Any]) -> Dict[str, str]:
    store = payload.get("workout_store") if isinstance(payload.get("workout_store"), dict) else {}
    goals = store.get("goals") if isinstance(store.get("goals"), dict) else {}
    if not isinstance(goals.get("lift_aliases"), dict):
        raw_goals = payload.get("goals") if isinstance(payload.get("goals"), dict) else {}
        goals = raw_goals if isinstance(raw_goals.get("lift_aliases"), dict) else goals
    out: Dict[str, str] = {}
    for src, dst in (goals.get("lift_aliases") or {}).items():
        if src and dst:
            out[_norm_name(str(src))] = str(dst)
    return out


def _goals(payload: Mapping[str, Any]) -> dict:
    store = payload.get("workout_store") if isinstance(payload.get("workout_store"), dict) else {}
    goals = store.get("goals") if isinstance(store.get("goals"), dict) else {}
    if goals:
        return goals
    raw = payload.get("goals") if isinstance(payload.get("goals"), dict) else {}
    return raw


def _override_value(goals: Mapping[str, Any]) -> Optional[int]:
    return clean_cap_override(goals.get("muscle_set_cap_override"))


def _skip_exercise(ex: Any) -> bool:
    from .models import is_zero_volume_status_log
    from .quest_workout_log import exercise_is_real_log

    try:
        if is_zero_volume_status_log(ex):
            return True
        if not exercise_is_real_log(ex):
            return True
    except (TypeError, ValueError, OverflowError):
        # A row with a non-finite or unreadable count is not a countable log.
        return True
    return False


def exercise_proper_sets(
    ex: Any,
    cat: Optional[dict],
    *,
    planned_load: Optional[float],
) -> int:
    if _skip_exercise(ex):
        return 0
    movement = _movement_of(cat, ex)
    if movement in ("cardio", "conditioning"):
        return 0
    assisted = _is_assisted(cat)
    rows = _set_rows(ex)
    if assisted:
        # Compare effective load to an effective plan. A pin-sized plan
        # number is not a working load, so drop it and use the top set.
        if planned_load is not None:
            loads = [
                row_load(st, assisted=True)
                for st in rows
                if _row_reps(st) is not None and reps_in_window(int(_row_reps(st) or 0), movement)
            ]
            known = [v for v in loads if v is not None]
            if known and planned_load < min(known) * 0.5:
                planned_load = None
    return proper_set_count(
        rows,
        movement=movement,
        planned_load=planned_load,
        assisted=assisted,
    )


def _use_plan(day: date, as_of: Optional[str], monday: str) -> bool:
    """Today's planned load applies only inside the open week."""
    if not as_of:
        return True
    start, end = et_week_bounds(as_of)
    if not start or not end:
        return monday == (et_week_bounds(day)[0] or "")
    return start <= day.isoformat() <= end


def week_proper_totals(
    sessions: Sequence[Any],
    catalog: Optional[dict],
    *,
    planned_loads: Optional[Mapping[str, float]] = None,
    aliases: Optional[Mapping[str, str]] = None,
    as_of: Optional[str] = None,
) -> Dict[str, int]:
    """Monday ISO → proper-set count. Each set counts once."""
    by_id, by_name = _catalog_index(catalog)
    aliases = aliases or {}
    planned_loads = planned_loads or {}
    totals: Dict[str, int] = {}
    for session in sessions or []:
        if _session_type(session) in ("cardio", "conditioning"):
            continue
        day = et_civil_date(_session_date(session))
        if day is None:
            continue
        monday, _sunday = et_week_bounds(day)
        if not monday:
            continue
        apply_plan = _use_plan(day, as_of, monday)
        count = 0
        for ex in _session_exercises(session):
            cat = _lookup_exercise(ex, by_id, by_name, aliases)
            name = _norm_name(_exercise_name(ex))
            planned = None
            if apply_plan:
                planned = planned_loads.get(name)
                if planned is None and cat is not None:
                    planned = planned_loads.get(_norm_name(str(cat.get("name") or "")))
            count += exercise_proper_sets(ex, cat, planned_load=planned)
        if count:
            totals[monday] = totals.get(monday, 0) + count
    return totals


def week_group_credits(
    sessions: Sequence[Any],
    catalog: Optional[dict],
    *,
    week_start: str,
    planned_loads: Optional[Mapping[str, float]] = None,
    aliases: Optional[Mapping[str, str]] = None,
    as_of: Optional[str] = None,
) -> Tuple[Dict[str, float], int]:
    """Group credits and the unsplit proper-set total for one Monday week."""
    by_id, by_name = _catalog_index(catalog)
    aliases = aliases or {}
    planned_loads = planned_loads or {}
    credits: Dict[str, float] = {group: 0.0 for group in VOLUME_GROUPS}
    proper = 0
    try:
        start = date.fromisoformat(week_start)
        end = start + timedelta(days=6)
    except ValueError:
        return credits, 0
    for session in sessions or []:
        if _session_type(session) in ("cardio", "conditioning"):
            continue
        day = et_civil_date(_session_date(session))
        if day is None or day < start or day > end:
            continue
        apply_plan = _use_plan(day, as_of, week_start)
        for ex in _session_exercises(session):
            cat = _lookup_exercise(ex, by_id, by_name, aliases)
            name = _norm_name(_exercise_name(ex))
            planned = None
            if apply_plan:
                planned = planned_loads.get(name)
                if planned is None and cat is not None:
                    planned = planned_loads.get(_norm_name(str(cat.get("name") or "")))
            count = exercise_proper_sets(ex, cat, planned_load=planned)
            if count <= 0:
                continue
            proper += count
            primary, secondary = volume_groups(cat)
            for group, amount in credit_proper_sets(primary, secondary, count).items():
                credits[group] = credits.get(group, 0.0) + amount
    return credits, proper


def _completed_average(
    counts: Mapping[str, int],
    monday: date,
) -> Optional[float]:
    """Mean proper-set total of up to four weeks that ended before ``monday``.

    Weeks inside the logged span with no entry count as zero. Weeks before
    the first logged week are left out so a short history does not invent
    a tiny cap.
    """
    known = []
    for key in counts:
        try:
            known.append(date.fromisoformat(str(key)[:10]))
        except ValueError:
            continue
    known = [day for day in known if day < monday]
    if not known:
        return None
    earliest = min(known)
    window = [monday - timedelta(days=7 * i) for i in range(1, 5)]
    present = [day for day in window if day >= earliest]
    if not present:
        return None
    total = 0.0
    for day in present:
        total += float(counts.get(day.isoformat(), 0) or 0)
    return total / float(len(present))


def _tonnage_status(payload: Optional[Mapping[str, Any]], as_of: str) -> str:
    if not isinstance(payload, Mapping):
        return "insufficient"
    try:
        from .guardrails import build_guardrails

        report = build_guardrails(dict(payload), as_of=as_of)
    except Exception:  # noqa: BLE001
        return "insufficient"
    for tile in report.get("tiles") or []:
        if isinstance(tile, dict) and tile.get("id") == "tonnage":
            return str(tile.get("status") or "insufficient")
    return "insufficient"


def _deload_called(payload: Optional[Mapping[str, Any]], as_of: str) -> bool:
    if not isinstance(payload, Mapping):
        return False
    try:
        end = datetime.strptime(as_of[:10], "%Y-%m-%d")
    except ValueError:
        return False
    try:
        from .guardrails import _deload_dates

        dates = _deload_dates(dict(payload), end)
    except Exception:  # noqa: BLE001
        dates = set()
    monday, sunday = et_week_bounds(as_of)
    if not monday or not sunday:
        return False
    return any(monday <= day <= sunday for day in dates)


def resolve_weekly_cap(
    counts: Mapping[str, int],
    *,
    as_of: str,
    blocked_mondays: Optional[Iterable[str]] = None,
    override: Optional[int] = None,
) -> Dict[str, Any]:
    """Monday cap from the 4-week average, stepped at most ±10%, empty override.

    The cap is the rounded average of the last four completed weeks. Once a
    full four-week average exists for the previous Monday, it may not move
    more than 10% from that average, and it does not rise above it while the
    week is blocked (red tonnage, a called deload, or a deload session).
    """
    monday_s, _sunday = et_week_bounds(as_of)
    blocked = {str(day)[:10] for day in (blocked_mondays or [])}
    raw_avg = None
    raw = None
    if monday_s:
        try:
            raw_avg = _completed_average(counts, date.fromisoformat(monday_s))
        except ValueError:
            raw_avg = None
        if raw_avg is not None:
            raw = _round_half_up(raw_avg)
    if override is not None:
        return {
            "cap": int(override),
            "cap_raw": raw,
            "cap_override": int(override),
            "cap_source": "override",
        }
    if monday_s is None or raw is None:
        return {
            "cap": None,
            "cap_raw": raw,
            "cap_override": None,
            "cap_source": "none",
        }
    current = date.fromisoformat(monday_s)
    cap_f = float(raw_avg)
    base = _step_base(counts, current)
    if base is not None:
        # Step limit: ±10% of the average this cap was set from a week ago.
        cap_f = max(base * 0.9, min(base * 1.1, cap_f))
        if current.isoformat() in blocked:
            cap_f = min(cap_f, base)
    return {
        "cap": _round_half_up(cap_f),
        "cap_raw": raw,
        "cap_override": None,
        "cap_source": "rolling",
    }


def _step_base(counts: Mapping[str, int], current: date) -> Optional[float]:
    """Average the previous Monday's cap was set from, or None.

    The step limit needs a real 4-week average to compare against. Until one
    exists (fewer than four completed weeks behind the previous Monday) the
    cap is the plain average and nothing is clamped or frozen. The base is
    kept unrounded so a small cap can still move: the old walk clamped the
    rounded cap, and for a cap of 4 or less ``round(cap × 1.1)`` is the cap
    itself, so a small seed never moved (#1270).
    """
    previous = current - timedelta(days=7)
    known = []
    for key in counts:
        try:
            known.append(date.fromisoformat(str(key)[:10]))
        except ValueError:
            continue
    known = [day for day in known if day < previous]
    if not known:
        return None
    if min(known) > previous - timedelta(days=28):
        return None
    return _completed_average(counts, previous)


def _role(group: str) -> str:
    if group in PRIORITY_GROUPS:
        return "priority"
    if group in MIDDLE_GROUPS:
        return "middle"
    return "floor"


def _target_label(group: str, *, ceiling: Optional[int]) -> str:
    role = _role(group)
    if role == "priority":
        return "8-10+" if ceiling is None else "8-10"
    if role == "middle":
        return "4-7"
    return "4"


def _target_min(group: str) -> int:
    if _role(group) == "priority":
        return PRIORITY_MIN
    if _role(group) == "middle":
        return MIDDLE_MIN
    return FLOOR_MIN


def demand_min() -> int:
    """Sum of the minimum target for every group."""
    total = 0
    for group in VOLUME_GROUPS:
        total += _target_min(group)
    return total


def drop_suggestion(credits: Mapping[str, float]) -> str:
    """Priority group to take off the 8–10 list.

    Prefer the priority group already closest to its target. Ties drop
    chest before lats/upper back before side delts.
    """
    best = DROP_ORDER[0]
    best_key = None
    for group in DROP_ORDER:
        done = float(credits.get(group) or 0)
        # Higher current volume is less in need of the priority bump.
        key = (-done, DROP_ORDER.index(group))
        if best_key is None or key < best_key:
            best_key = key
            best = group
    return best


def budget_warning(
    cap: Optional[int],
    suggestion: str,
    *,
    credit_cap: Optional[float] = None,
    drop_closes_gap: Optional[bool] = None,
) -> Optional[str]:
    """Warn when the targets need more set-credits than the cap allows.

    ``cap`` counts proper sets. ``demand_min`` counts set-credits, so the cap
    is compared in credits: ``credit_cap`` is the cap at the recent
    credits-per-set ratio. Without one the cap is compared as given.
    ``drop_closes_gap=False`` means taking every priority group down to the
    floor would still not fit, so the targets sit well above recent volume
    and the message says so instead of implying the list is the problem.
    """
    need = demand_min()
    if cap is None:
        return None
    limit = float(cap) if credit_cap is None else float(credit_cap)
    if need <= limit + 1e-9:
        return None
    label = GROUP_LABELS.get(suggestion, suggestion)
    if credit_cap is None:
        room = f"the weekly cap is {cap} proper sets"
    else:
        room = (
            f"the weekly cap is {cap} proper sets, about "
            f"{float(credit_cap):.0f} set-credits at your recent mix"
        )
    if drop_closes_gap is False:
        return (
            f"Priority targets plus maintenance floors are {need} set-credits "
            f"and {room}. "
            f"Dropping {label} from the 8–10 list would not close the gap: "
            "the targets sit well above recent volume."
        )
    return (
        f"Priority targets plus maintenance floors are {need} set-credits "
        f"and {room}. "
        f"Drop {label} from the 8–10 list."
    )


def demand_after_priority_drops() -> int:
    """Demand if every priority group fell back to the maintenance floor."""
    return demand_min() - len(PRIORITY_GROUPS) * (PRIORITY_MIN - FLOOR_MIN)


def _credited_average(
    history: Mapping[str, Mapping[str, float]],
    monday: date,
) -> Optional[float]:
    """Mean credited total of up to four completed weeks before ``monday``.

    Same window rule as ``_completed_average``: a week inside the logged span
    with no entry counts as zero, and weeks before the first logged week are
    left out.
    """
    known = []
    for key in history:
        try:
            known.append(date.fromisoformat(str(key)[:10]))
        except ValueError:
            continue
    known = [day for day in known if day < monday]
    if not known:
        return None
    earliest = min(known)
    window = [monday - timedelta(days=7 * i) for i in range(1, 5)]
    present = [day for day in window if day >= earliest]
    if not present:
        return None
    total = 0.0
    for day in present:
        slot = history.get(day.isoformat()) or {}
        total += sum(float(v or 0) for v in slot.values())
    return total / float(len(present))


def _trim_history(sessions: Sequence[Any], monday: str) -> List[Any]:
    """Keep sessions from ``HISTORY_WEEKS`` weeks before ``monday`` onward."""
    try:
        start = date.fromisoformat(monday) - timedelta(weeks=int(HISTORY_WEEKS))
    except ValueError:
        return list(sessions or [])
    kept: List[Any] = []
    for session in sessions or []:
        day = et_civil_date(_session_date(session))
        if day is not None and day >= start:
            kept.append(session)
    return kept


def _trend(
    counts_by_group: Mapping[str, Mapping[str, float]],
    group: str,
    monday: date,
) -> Optional[float]:
    weeks = [monday - timedelta(days=7 * i) for i in range(1, 5)]
    present = [day for day in weeks if day.isoformat() in counts_by_group]
    if not present:
        return None
    total = 0.0
    for day in present:
        total += float((counts_by_group.get(day.isoformat()) or {}).get(group) or 0)
    return _round_sets(total / float(len(present)))


def thursday_flags(
    *,
    as_of: str,
    credits: Mapping[str, float],
    trends: Mapping[str, Optional[float]],
) -> Dict[str, Any]:
    day = et_civil_date(as_of)
    if day is None or day.weekday() != 3:
        return {"active": False, "flags": [], "suggestions_only": True}
    elapsed = day.weekday() + 1  # Thursday = 4
    flags: List[dict] = []
    for group in PRIORITY_GROUPS:
        done = float(credits.get(group) or 0)
        projected = done * 7.0 / float(elapsed)
        if projected + 1e-9 < PRIORITY_MIN:
            flags.append(
                {
                    "group": group,
                    "label": GROUP_LABELS[group],
                    "kind": "priority_pace",
                    "proper_sets": _round_sets(done),
                    "projected": _round_sets(projected),
                    "suggestion": (
                        f"{GROUP_LABELS[group]} is on pace for {projected:.1f} "
                        f"proper sets. Aim is 8–10. Add a set only if you want to."
                    ),
                }
            )
    for group in VOLUME_GROUPS:
        trend = trends.get(group)
        if trend is None:
            continue
        if float(trend) < FLOOR_MIN:
            flags.append(
                {
                    "group": group,
                    "label": GROUP_LABELS[group],
                    "kind": "under_floor",
                    "trend_4w": _round_sets(float(trend)),
                    "suggestion": (
                        f"{GROUP_LABELS[group]} is trending {float(trend):.1f} "
                        "over the last completed weeks, under the floor of 4."
                    ),
                }
            )
    return {"active": True, "flags": flags, "suggestions_only": True}


def _group_history(
    sessions: Sequence[Any],
    catalog: Optional[dict],
    *,
    as_of: str,
    planned_loads: Mapping[str, float],
    aliases: Mapping[str, str],
) -> Dict[str, Dict[str, float]]:
    """Completed Mondays → group credits. The open week is not included."""
    monday_s, _sunday = et_week_bounds(as_of)
    if not monday_s:
        return {}
    by_id, by_name = _catalog_index(catalog)
    buckets: Dict[str, Dict[str, float]] = {}
    for session in sessions or []:
        if _session_type(session) in ("cardio", "conditioning"):
            continue
        day = et_civil_date(_session_date(session))
        if day is None:
            continue
        start, _end = et_week_bounds(day)
        if not start or start >= monday_s:
            continue
        slot = buckets.setdefault(start, {group: 0.0 for group in VOLUME_GROUPS})
        for ex in _session_exercises(session):
            cat = _lookup_exercise(ex, by_id, by_name, aliases)
            count = exercise_proper_sets(ex, cat, planned_load=None)
            if count <= 0:
                continue
            primary, secondary = volume_groups(cat)
            for group, amount in credit_proper_sets(primary, secondary, count).items():
                slot[group] = slot.get(group, 0.0) + amount
    return buckets


def _blocked_mondays(
    payload: Optional[Mapping[str, Any]],
    sessions: Sequence[Any],
    *,
    as_of: str,
    counts: Mapping[str, int],
    tonnage: Optional[str] = None,
    deload_called: Optional[bool] = None,
) -> List[str]:
    monday_s, sunday_s = et_week_bounds(as_of)
    blocked: List[str] = []
    if not monday_s:
        return blocked
    for key in list(counts) + [monday_s]:
        try:
            monday = date.fromisoformat(str(key)[:10])
        except ValueError:
            continue
        sunday = monday + timedelta(days=6)
        hit = False
        for session in sessions or []:
            day = et_civil_date(_session_date(session))
            if day is None or day < monday or day > sunday:
                continue
            if _is_deload_session(session):
                hit = True
                break
        if str(key)[:10] == monday_s and isinstance(payload, Mapping):
            if tonnage is None:
                tonnage = _tonnage_status(payload, as_of)
            if deload_called is None:
                deload_called = _deload_called(payload, as_of)
            if tonnage == "red" or deload_called:
                hit = True
        if hit:
            blocked.append(monday.isoformat())
    if sunday_s and monday_s not in blocked:
        pass
    return sorted(set(blocked))


def build_muscle_set_report(
    payload: Optional[Mapping[str, Any]] = None,
    *,
    as_of: Optional[str] = None,
    catalog: Optional[dict] = None,
) -> Dict[str, Any]:
    """Additive weekly report. Existing dashboard fields are not read back."""
    data: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
    day = as_of or ""
    if not day:
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        coach = data.get("coach") if isinstance(data.get("coach"), dict) else {}
        today = coach.get("today") if isinstance(coach.get("today"), dict) else {}
        day = str(today.get("date") or meta.get("local_today") or "")[:10]
    monday, sunday = et_week_bounds(day) if day else (None, None)
    if catalog is None:
        store = data.get("workout_store") if isinstance(data.get("workout_store"), dict) else {}
        if isinstance(store.get("catalog"), dict):
            catalog = store.get("catalog")
        else:
            try:
                from .workout_planner import default_catalog

                catalog = default_catalog()
            except Exception:  # noqa: BLE001
                catalog = {"exercises": []}
    sessions = data.get("sessions") if isinstance(data.get("sessions"), list) else []
    if monday:
        sessions = _trim_history(sessions, monday)
    aliases = _alias_map(data)
    planned = _planned_load_index(data)
    goals = _goals(data)
    override = _override_value(goals)
    if not monday:
        return {
            "week_start": None,
            "week_end": None,
            "window": rolling_7d_window(day),
            "muscle_sets": {},
            "budget": {
                "cap": None,
                "cap_raw": None,
                "cap_override": None,
                "demand_min": demand_min(),
                "overrun": False,
                "drop_suggestion": None,
                "warning": None,
                "tonnage_status": "insufficient",
                "deload": False,
                "priority_ceiling": PRIORITY_MAX,
                "week_proper_sets": 0,
            },
            "coach": {"active": False, "flags": [], "suggestions_only": True},
        }
    credits, proper = week_group_credits(
        sessions,
        catalog,
        week_start=monday,
        planned_loads=planned,
        aliases=aliases,
        as_of=day,
    )
    # Display counts. The Monday totals above stay the weekly plan.
    roll_start, _roll_end = rolling_7d_bounds(day)
    rolling_credits, _rolling_proper = week_group_credits(
        sessions,
        catalog,
        week_start=roll_start or monday,
        planned_loads=planned,
        aliases=aliases,
        as_of=day,
    )
    totals = week_proper_totals(
        sessions, catalog, planned_loads=planned, aliases=aliases, as_of=day
    )
    # The open week is not an input to the average. Keep it out of the cap walk.
    completed = {key: value for key, value in totals.items() if key < monday}
    # Guardrail reads go to the whole payload and run once per report.
    tonnage = _tonnage_status(data, day)
    deload_called = _deload_called(data, day)
    blocked = _blocked_mondays(
        data,
        sessions,
        as_of=day,
        counts=completed,
        tonnage=tonnage,
        deload_called=deload_called,
    )
    cap_info = resolve_weekly_cap(
        completed,
        as_of=day,
        blocked_mondays=blocked,
        override=override,
    )
    deload = deload_called or any(
        _is_deload_session(session)
        and (lambda d: d is not None and monday <= d.isoformat() <= sunday)(
            et_civil_date(_session_date(session))
        )
        for session in sessions
    )
    ceiling: Optional[int] = PRIORITY_MAX
    cap = cap_info.get("cap")
    if tonnage == "green" and isinstance(cap, int) and proper < cap:
        ceiling = None
    history = _group_history(
        sessions,
        catalog,
        as_of=day,
        planned_loads=planned,
        aliases=aliases,
    )
    trends = {
        group: _trend(history, group, date.fromisoformat(monday)) for group in VOLUME_GROUPS
    }
    muscle_sets = {}
    for group in VOLUME_GROUPS:
        done = _round_sets(rolling_credits.get(group) or 0)
        muscle_sets[group] = {
            "proper_sets": done,
            "tier": tier_for(done),
            "target": _target_label(group, ceiling=ceiling),
            "trend_4w": trends.get(group),
        }
    suggestion = drop_suggestion(credits)
    cap_sets = cap if isinstance(cap, int) else None
    credited_avg = _credited_average(history, date.fromisoformat(monday))
    sets_avg = _completed_average(completed, date.fromisoformat(monday))
    credit_cap: Optional[float] = None
    if cap_sets is not None:
        ratio = 1.0
        if credited_avg is not None and sets_avg:
            ratio = credited_avg / float(sets_avg)
        credit_cap = cap_sets * ratio
    drop_closes_gap: Optional[bool] = None
    if credit_cap is not None:
        drop_closes_gap = demand_after_priority_drops() <= credit_cap + 1e-9
    warning = budget_warning(
        cap_sets, suggestion, credit_cap=credit_cap, drop_closes_gap=drop_closes_gap
    )
    overrun = warning is not None
    coach = thursday_flags(as_of=day, credits=credits, trends=trends)
    return {
        "week_start": monday,
        "week_end": sunday,
        "window": rolling_7d_window(day),
        # Monday-week credits. The planner tiebreak reads these so a
        # rolling display count does not change the weekly plan (#1297).
        "calendar_credits": {
            group: _round_sets(credits.get(group) or 0) for group in VOLUME_GROUPS
        },
        "muscle_sets": muscle_sets,
        "budget": {
            "cap": cap,
            "cap_raw": cap_info.get("cap_raw"),
            "cap_override": cap_info.get("cap_override"),
            "cap_source": cap_info.get("cap_source"),
            "demand_min": demand_min(),
            "credited_avg_4w": None if credited_avg is None else _round_sets(credited_avg),
            "credit_cap": None if credit_cap is None else round(credit_cap, 1),
            "drop_closes_gap": drop_closes_gap if warning else None,
            "overrun": overrun,
            "drop_suggestion": suggestion if overrun else None,
            "warning": warning,
            "tonnage_status": tonnage,
            "deload": bool(deload),
            "priority_ceiling": ceiling,
            "week_proper_sets": proper,
            "priority_groups": list(PRIORITY_GROUPS),
            "middle_groups": list(MIDDLE_GROUPS),
        },
        "coach": coach,
    }


def attach_muscle_sets(payload: dict, *, as_of: Optional[str] = None) -> dict:
    """Dashboard field for the Trends tile. Does not rewrite other keys."""
    payload["muscle_sets"] = build_muscle_set_report(payload, as_of=as_of)
    return payload


def is_plan_accessory(ex: Optional[dict], goals: Optional[dict]) -> bool:
    """Pattern-family compounds and named main lifts are not accessories."""
    if not isinstance(ex, dict):
        return False
    from .workout_planner import _norm_name as norm
    from .workout_planner import pattern_family

    if pattern_family(ex):
        return False
    goals = goals or {}
    names = {norm(str(n)) for n in (goals.get("main_lifts") or []) if str(n).strip()}
    aliases = goals.get("lift_aliases") if isinstance(goals.get("lift_aliases"), dict) else {}
    for src, dst in aliases.items():
        if src:
            names.add(norm(str(src)))
        if dst:
            names.add(norm(str(dst)))
    if norm(str(ex.get("name") or "")) in names:
        return False
    return True


def accessory_tiebreak(
    ex: Optional[dict],
    report: Optional[Mapping[str, Any]],
    goals: Optional[dict] = None,
) -> float:
    """Higher when an accessory's primaries are under their weekly minimum.

    Main lifts and pattern-family compounds return 0 so this only breaks
    accessory ties. It does not outrank the planner's volume score.
    """
    if not isinstance(ex, dict) or not isinstance(report, Mapping):
        return 0.0
    if not is_plan_accessory(ex, goals):
        return 0.0
    primary, _secondary = volume_groups(ex)
    if not primary:
        return 0.0
    calendar = report.get("calendar_credits")
    calendar = calendar if isinstance(calendar, dict) else None
    muscles = report.get("muscle_sets") if isinstance(report.get("muscle_sets"), dict) else {}
    score = 0.0
    for group in primary:
        if calendar is not None:
            done = float(calendar.get(group) or 0)
        else:
            row = muscles.get(group) if isinstance(muscles.get(group), dict) else {}
            done = float(row.get("proper_sets") or 0)
        minimum = _target_min(group)
        if done >= minimum:
            continue
        weight = 3.0 if group in PRIORITY_GROUPS else 1.0
        score += (minimum - done) * weight
    return score


def accessory_sets_allowed(hard: int, room: Optional[int]) -> int:
    """Shrink an accessory so the week's proper-set total stays inside the cap.

    ``room`` is remaining proper sets. None means no cap. Zero means skip
    the accessory rather than overrun.
    """
    sets = max(0, int(hard or 0))
    if room is None:
        return sets
    return max(0, min(sets, int(room)))


