"""Weekly proper-set tracker and priority budget (#1193).

Read-only. Nothing here writes a log, a plan, or a store. Everything is
recomputed from the logged sessions each time, so it can be undone by
reverting the code.

* **Proper set.** A logged working set whose reps sit in the lift's band (or
  one rep either side of it) and whose load is at least ``PROPER_LOAD_FRACTION``
  of that lift's working load. Warm-up and ramp sets, and planner status rows
  such as ``Already trained today``, never count.
* **Muscle credit.** A proper set gives 1.0 to each primary group and 0.5 to
  each secondary group, from ``muscle_credit`` on the catalog row.
* **Week.** Monday through Sunday on the civil dates the sessions carry (ET).
* **Budget.** A weekly total-set cap that follows the real 4-week average, plus
  per-group targets (priority 8-10, middle 4-7, deprioritized floor 4).
  Group credits and the cap use the same unit: credited proper sets.

Sessions may be ``Session`` objects or ``to_dict()`` payloads. Both are read
through ``_session_dict``; no attribute access on a payload dict (#1242).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

# The 14 groups, in display order.
GROUPS: Tuple[str, ...] = (
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
    "chest": "Chest",
    "front_delts": "Front delts",
    "side_delts": "Side delts",
    "rear_delts": "Rear delts",
    "lats_upper_back": "Lats / upper back",
    "traps": "Traps",
    "biceps": "Biceps",
    "triceps": "Triceps",
    "quads": "Quads",
    "hamstrings": "Hamstrings",
    "glutes": "Glutes",
    "calves": "Calves",
    "abs": "Abs",
    "lower_back": "Lower back",
}

PRIMARY_CREDIT = 1.0
SECONDARY_CREDIT = 0.5

# Set must reach about 85% of the lift's working load. Drops warm-ups and ramps.
PROPER_LOAD_FRACTION = 0.85
# Reps may sit one rep outside the lift's band.
REP_BAND_SLACK = 1

# Tiers on a group's weekly proper sets.
TIER_GROWTH_FLOOR = 4.0  # below this: red
TIER_NEAR_MAX = 8.0  # this or more: blue

TREND_WEEKS = 4
# Completed weeks the cap recursion walks back through (seed of the +-10% clamp).
CAP_RECURSION_WEEKS = 6
CAP_MAX_WEEKLY_CHANGE = 0.10
# Weeks of log the cap walk can read: the recursion plus its 4-week average.
HISTORY_WEEKS = CAP_RECURSION_WEEKS + TREND_WEEKS

PRIORITY_TARGET = (8, 10)
MIDDLE_TARGET = (4, 7)
FLOOR_TARGET = (4, 4)

THURSDAY = 3  # datetime.weekday()

# Coach decisions (Frankenfit, 2026-10-14 on #1193). ``goals.set_budget`` overrides.
DEFAULT_SET_BUDGET: Dict[str, Any] = {
    "priority_groups": ["side_delts", "lats_upper_back", "chest"],
    "middle_groups": ["hamstrings", "rear_delts", "biceps", "triceps"],
    # None = every group that is neither priority nor middle.
    "deprioritized_groups": None,
    "priority_target": list(PRIORITY_TARGET),
    "middle_target": list(MIDDLE_TARGET),
    "maintenance_floor": int(FLOOR_TARGET[0]),
    "max_weekly_change_pct": 10,
    "review_every_weeks": 6,
    # Manual cap. Empty by default; the cap follows the 4-week average.
    "cap_override": None,
}

_WARMUP_KEYS = ("warmup", "is_warmup", "warm_up", "is_warm_up")
_WARMUP_KINDS = {"warmup", "warm_up", "warm-up", "ramp", "ramp_up"}


# ---------------------------------------------------------------- helpers


def _f(value: Any) -> Optional[float]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:
        return None
    return num


def _day(value: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def week_bounds(day: Any) -> Optional[Tuple[str, str]]:
    """Monday and Sunday (civil dates) of the week holding ``day``."""
    dt = _day(day)
    if dt is None:
        return None
    monday = dt - timedelta(days=dt.weekday())
    return _iso(monday), _iso(monday + timedelta(days=6))


def _session_dict(session: Any) -> Optional[Dict[str, Any]]:
    if isinstance(session, dict):
        return session
    if hasattr(session, "to_dict"):
        try:
            raw = session.to_dict()
        except Exception:  # noqa: BLE001
            return None
        return raw if isinstance(raw, dict) else None
    return None


def _ex_dict(ex: Any) -> Optional[Dict[str, Any]]:
    if isinstance(ex, dict):
        return ex
    if hasattr(ex, "to_dict"):
        try:
            raw = ex.to_dict()
        except Exception:  # noqa: BLE001
            return None
        return raw if isinstance(raw, dict) else None
    return None


def _norm_group(name: Any) -> Optional[str]:
    key = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    return key if key in GROUPS else None


def catalog_credit(row: Optional[dict]) -> Dict[str, float]:
    """``{group: credit}`` for one catalog row. A primary wins over a secondary."""
    if not isinstance(row, dict):
        return {}
    raw = row.get("muscle_credit")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for name in raw.get("primary") or []:
        group = _norm_group(name)
        if group:
            out[group] = PRIMARY_CREDIT
    for name in raw.get("secondary") or []:
        group = _norm_group(name)
        if group and group not in out:
            out[group] = SECONDARY_CREDIT
    return out


def _catalog_row(name: str, catalog: Optional[dict]) -> Optional[dict]:
    """Catalog row for a logged name, or None. Unknown lifts get no credit."""
    from .workout_planner import _catalog_row_for_name

    row = _catalog_row_for_name(name, catalog)
    if isinstance(row, dict) and row.get("id"):
        return row
    return None


def _is_warmup_row(row: Dict[str, Any]) -> bool:
    for key in _WARMUP_KEYS:
        if row.get(key) is True:
            return True
    for key in ("set_type", "kind", "type", "role"):
        if str(row.get(key) or "").strip().lower() in _WARMUP_KINDS:
            return True
    return False


def _row_load(row: Dict[str, Any], *, assisted: bool) -> Optional[float]:
    """Load for the 85% check. Assisted sets use effective load (#1156)."""
    if assisted:
        if row.get("effective_load_source") not in (None, "nearest_weighin"):
            return None
        eff = _f(row.get("effective_load_lbs"))
        return eff  # None when no weigh-in: counts by reps alone
    return _f(row.get("weight_lbs"))


def _band(row: Dict[str, Any]) -> Tuple[int, int]:
    from .workout_planner import progression_band

    lo, hi = progression_band(row)
    return int(lo), int(hi)


def reps_in_band(reps: Any, row: Dict[str, Any]) -> bool:
    n = _f(reps)
    if n is None:
        return False
    lo, hi = _band(row)
    return (lo - REP_BAND_SLACK) <= n <= (hi + REP_BAND_SLACK)


# ------------------------------------------------------- proper-set count


def proper_sets_for_exercise(
    ex: Any,
    row: Optional[dict],
    *,
    planned_load: Optional[float] = None,
    session_top: Optional[float] = None,
) -> int:
    """Count proper sets in one logged exercise entry.

    ``planned_load`` is the lift's current planned working load; it is the
    reference when given. Otherwise the reference is ``session_top``, the
    heaviest non-warm-up load logged for that lift in the session.
    """
    from .models import is_planner_status_exercise_name

    raw = _ex_dict(ex)
    if raw is None or not isinstance(row, dict):
        return 0
    name = str(raw.get("name") or raw.get("exercise") or "")
    if is_planner_status_exercise_name(name):
        return 0
    from .assisted_load import catalog_is_assisted

    assisted = catalog_is_assisted(row)
    reference = planned_load if (planned_load is not None and planned_load > 0) else session_top
    total = 0
    for st in _set_rows(raw):
        if _is_warmup_row(st):
            continue
        count = _f(st.get("sets"))
        if count is None or count <= 0:
            continue
        if not reps_in_band(st.get("reps"), row):
            continue
        load = _row_load(st, assisted=assisted)
        if load is not None and reference is not None and reference > 0:
            if load < PROPER_LOAD_FRACTION * reference:
                continue
        total += int(count)
    return total


def _set_rows(raw_ex: Dict[str, Any]) -> List[Dict[str, Any]]:
    sets = raw_ex.get("sets")
    if isinstance(sets, list):
        out = []
        for st in sets:
            if isinstance(st, dict):
                out.append(st)
            elif hasattr(st, "to_dict"):
                try:
                    out.append(st.to_dict())
                except Exception:  # noqa: BLE001
                    continue
        return out
    # Flattened feed row: one weight x sets x reps triple.
    if sets is not None and raw_ex.get("reps") is not None:
        return [
            {
                "weight_lbs": raw_ex.get("weight_lbs"),
                "sets": sets,
                "reps": raw_ex.get("reps"),
            }
        ]
    return []


def _top_load(raw_ex: Dict[str, Any], *, assisted: bool) -> Optional[float]:
    best: Optional[float] = None
    for st in _set_rows(raw_ex):
        if _is_warmup_row(st):
            continue
        load = _row_load(st, assisted=assisted)
        if load is not None and (best is None or load > best):
            best = load
    return best


def session_credits(
    session: Any,
    catalog: Optional[dict],
    *,
    planned_loads: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, float], int]:
    """``({group: credited proper sets}, proper set count)`` for one session.

    ``planned_loads`` maps catalog id to the current planned working load. It
    applies to this session only (the caller passes it for the plan's day).
    """
    from .assisted_load import catalog_is_assisted
    from .test_noise import is_test_exercise_name

    raw = _session_dict(session)
    if raw is None:
        return {}, 0
    entries: List[Tuple[Dict[str, Any], dict]] = []
    for ex in raw.get("exercises") or []:
        exd = _ex_dict(ex)
        if exd is None:
            continue
        name = str(exd.get("name") or exd.get("exercise") or "")
        if not name.strip() or is_test_exercise_name(name):
            continue
        row = _catalog_row(name, catalog)
        if row is None:
            continue
        entries.append((exd, row))
    tops: Dict[str, float] = {}
    for exd, row in entries:
        top = _top_load(exd, assisted=catalog_is_assisted(row))
        if top is not None:
            eid = str(row["id"])
            tops[eid] = max(tops.get(eid, 0.0), top)
    credits: Dict[str, float] = {}
    n_proper = 0
    for exd, row in entries:
        eid = str(row["id"])
        weights = catalog_credit(row)
        if not weights:
            continue
        planned = (planned_loads or {}).get(eid)
        n = proper_sets_for_exercise(
            exd, row, planned_load=planned, session_top=tops.get(eid)
        )
        if n <= 0:
            continue
        n_proper += n
        for group, weight in weights.items():
            credits[group] = credits.get(group, 0.0) + n * weight
    return credits, n_proper


def weekly_credits(
    sessions: Sequence[Any],
    catalog: Optional[dict],
    *,
    planned_loads: Optional[Dict[str, float]] = None,
    planned_day: Optional[str] = None,
) -> Dict[str, Dict[str, float]]:
    """``{week_monday: {group: credits}}`` over every dated session."""
    weeks: Dict[str, Dict[str, float]] = {}
    for session in sessions or []:
        raw = _session_dict(session)
        if raw is None:
            continue
        day = str(raw.get("date") or "")[:10]
        bounds = week_bounds(day)
        if bounds is None:
            continue
        use_plan = planned_loads if (planned_day and day == str(planned_day)[:10]) else None
        credits, _n = session_credits(raw, catalog, planned_loads=use_plan)
        bucket = weeks.setdefault(bounds[0], {})
        for group, value in credits.items():
            bucket[group] = bucket.get(group, 0.0) + value
    return weeks


# ------------------------------------------------------------------- tiers


def tier_for(proper_sets: float) -> str:
    """``red`` below 4, ``green`` for 4-7, ``blue`` for 8 and up."""
    value = float(proper_sets)
    if value < TIER_GROWTH_FLOOR:
        return "red"
    if value < TIER_NEAR_MAX:
        return "green"
    return "blue"


TIER_LABELS = {
    "red": "below growth",
    "green": "growing",
    "blue": "near max",
}


# ------------------------------------------------------------------ config


def _int_pair(raw: Any, default: Tuple[int, int]) -> Tuple[int, int]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        lo, hi = _f(raw[0]), _f(raw[1])
        if lo is not None and hi is not None and 0 < lo <= hi:
            return int(lo), int(hi)
    return default


def resolve_config(goals: Optional[dict]) -> Dict[str, Any]:
    """Merge ``goals.set_budget`` over the coach defaults. Bad values fall back."""
    raw = goals.get("set_budget") if isinstance(goals, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    warnings: List[str] = []

    def groups(key: str) -> Optional[List[str]]:
        val = raw.get(key)
        if val is None:
            return None
        out: List[str] = []
        for item in val if isinstance(val, list) else []:
            group = _norm_group(item)
            if group and group not in out:
                out.append(group)
        return out

    priority = groups("priority_groups")
    if priority is None:
        priority = list(DEFAULT_SET_BUDGET["priority_groups"])
    if len(priority) > 4:
        warnings.append("More than 4 priority groups. Using the first 4.")
        priority = priority[:4]
    if len(priority) < 2:
        warnings.append("Fewer than 2 priority groups set.")
    middle = groups("middle_groups")
    if middle is None:
        middle = list(DEFAULT_SET_BUDGET["middle_groups"])
    middle = [g for g in middle if g not in priority]
    depri = groups("deprioritized_groups")
    if depri is None:
        depri = [g for g in GROUPS if g not in priority and g not in middle]
    else:
        depri = [g for g in depri if g not in priority and g not in middle]
        # A group in no list holds the floor too.
        depri += [g for g in GROUPS if g not in priority and g not in middle and g not in depri]
    floor = _f(raw.get("maintenance_floor"))
    floor_i = int(floor) if floor is not None and floor > 0 else int(FLOOR_TARGET[0])
    change = _f(raw.get("max_weekly_change_pct"))
    change_pct = change if change is not None and 0 <= change <= 100 else 10.0
    override = _f(raw.get("cap_override"))
    return {
        "priority_groups": priority,
        "middle_groups": middle,
        "deprioritized_groups": depri,
        "priority_target": _int_pair(raw.get("priority_target"), PRIORITY_TARGET),
        "middle_target": _int_pair(raw.get("middle_target"), MIDDLE_TARGET),
        "maintenance_floor": floor_i,
        "max_weekly_change": change_pct / 100.0,
        "review_every_weeks": int(_f(raw.get("review_every_weeks")) or 6),
        "cap_override": override if override is not None and override > 0 else None,
        "warnings": warnings,
    }


def group_targets(cfg: Dict[str, Any], *, stretch: bool = False) -> Dict[str, Dict[str, Any]]:
    """``{group: {role, low, high}}``. ``stretch`` marks priority groups as 10+ ok."""
    out: Dict[str, Dict[str, Any]] = {}
    plo, phi = cfg["priority_target"]
    mlo, mhi = cfg["middle_target"]
    floor = int(cfg["maintenance_floor"])
    for group in GROUPS:
        if group in cfg["priority_groups"]:
            row = {"role": "priority", "low": plo, "high": phi}
            if stretch:
                row["stretch"] = True
        elif group in cfg["middle_groups"]:
            row = {"role": "middle", "low": mlo, "high": mhi}
        else:
            row = {"role": "deprioritized", "low": floor, "high": floor}
        out[group] = row
    return out


# --------------------------------------------------------------- the cap


def _week_total(bucket: Optional[Dict[str, float]]) -> float:
    return float(sum((bucket or {}).values()))


def _prior_mondays(monday: str, count: int) -> List[str]:
    dt = _day(monday)
    return [_iso(dt - timedelta(days=7 * (i + 1))) for i in range(count)]


def rolling_avg_total(
    weeks: Dict[str, Dict[str, float]],
    monday: str,
    *,
    first_week: Optional[str],
) -> Optional[float]:
    """Mean weekly proper-set total over the 4 completed weeks before ``monday``.

    Weeks before the first logged session are not zeros; they do not exist.
    None when no completed week with a logged session sits in the window.
    """
    totals: List[float] = []
    for prior in _prior_mondays(monday, TREND_WEEKS):
        if first_week is None or prior < first_week:
            continue
        totals.append(_week_total(weeks.get(prior)))
    if not totals:
        return None
    return sum(totals) / len(totals)


def _clamp_cap(raw: int, prev: Optional[int], pct: float) -> int:
    if prev is None:
        return raw
    lo = math.ceil(prev * (1.0 - pct) - 1e-9)
    hi = math.floor(prev * (1.0 + pct) + 1e-9)
    if lo > hi:
        return prev
    return max(lo, min(hi, raw))


def weekly_cap(
    weeks: Dict[str, Dict[str, float]],
    monday: str,
    cfg: Dict[str, Any],
    *,
    first_week: Optional[str],
    hold_up: bool,
) -> Dict[str, Any]:
    """The cap for the week starting ``monday``.

    Each Monday it is the rounded 4-week rolling average, and it moves at most
    +-``max_weekly_change`` per week. It never goes up while ``hold_up`` is true
    (tonnage guardrail red or a deload called). The walk is recomputed from the
    log each call; there is no stored cap. Past holds are not reconstructed.
    """
    override = cfg.get("cap_override")
    avg_now = rolling_avg_total(weeks, monday, first_week=first_week)
    if override is not None:
        return {
            "cap": int(round(override)),
            "source": "manual_override",
            "avg_4w": None if avg_now is None else round(avg_now, 1),
            "held": False,
        }
    if avg_now is None:
        return {"cap": None, "source": "none", "avg_4w": None, "held": False}
    # Walk forward from the oldest week in the recursion window.
    chain = list(reversed(_prior_mondays(monday, CAP_RECURSION_WEEKS))) + [monday]
    prev: Optional[int] = None
    for week in chain:
        avg = rolling_avg_total(weeks, week, first_week=first_week)
        if avg is None:
            prev = None
            continue
        raw = int(round(avg))
        nxt = _clamp_cap(raw, prev, cfg["max_weekly_change"])
        if week == monday and hold_up and prev is not None and nxt > prev:
            return {
                "cap": prev,
                "source": "rolling_4w_avg",
                "avg_4w": round(avg_now, 1),
                "held": True,
            }
        prev = nxt
    return {
        "cap": prev,
        "source": "rolling_4w_avg",
        "avg_4w": round(avg_now, 1),
        "held": False,
    }


def budget_plan(
    cfg: Dict[str, Any],
    targets: Dict[str, Dict[str, Any]],
    cap: Optional[int],
    avg_by_group: Dict[str, Optional[float]],
) -> Dict[str, Any]:
    """Sum of target lows against the cap. Never silently overruns."""
    planned = float(sum(t["low"] for t in targets.values()))
    out: Dict[str, Any] = {
        "planned_total": planned,
        "cap": cap,
        "overrun": 0.0,
        "warning": None,
    }
    if cap is None:
        return out
    overrun = planned - cap
    if overrun <= 0:
        return out
    priority = list(cfg["priority_groups"])
    mlo = cfg["middle_target"][0]
    plo = cfg["priority_target"][0]
    saving = max(0, plo - mlo)  # dropping a priority group back to the middle band
    # Drop the group furthest short of its target first (largest gap, last-listed wins ties).
    ranked = sorted(
        enumerate(priority),
        key=lambda iv: (
            -(plo - (avg_by_group.get(iv[1]) or 0.0)),
            -iv[0],
        ),
    )
    order = [group for _i, group in ranked]
    remaining = overrun
    drops: List[str] = []
    for group in order:
        if remaining <= 0:
            break
        drops.append(group)
        remaining -= saving
    fits = remaining <= 0 and saving > 0
    first = drops[0] if drops else None
    label = GROUP_LABELS.get(first, first) if first else None
    if first and fits and len(drops) == 1:
        advice = f"Drop {label} from priority to fit the cap."
    elif first and fits:
        names = ", ".join(GROUP_LABELS.get(g, g) for g in drops)
        advice = f"Drop {names} from priority to fit the cap. Start with {label}."
    elif first:
        advice = (
            f"Drop {label} first. Dropping every priority group still leaves "
            f"{max(0.0, remaining):g} sets over; raise the cap by hand or lower the floor."
        )
    else:
        advice = "No priority group to drop. Raise the cap by hand or lower the floor."
    out["overrun"] = round(overrun, 1)
    out["warning"] = {
        "code": "targets_exceed_cap",
        "message": (
            f"Priority targets plus floors need {planned:g} proper sets a week; "
            f"the cap is {cap}. {advice}"
        ),
        "overrun": round(overrun, 1),
        "suggest_drop": first,
        "drop_order": drops,
        "fits_after_drops": bool(fits),
    }
    return out


# ----------------------------------------------------------- the builder


def _trend(weeks: Dict[str, Dict[str, float]], monday: str, group: str, first_week: Optional[str]) -> Dict[str, Any]:
    rows = []
    for prior in reversed(_prior_mondays(monday, TREND_WEEKS)):
        if first_week is None or prior < first_week:
            continue
        rows.append({"week_start": prior, "proper_sets": round(weeks.get(prior, {}).get(group, 0.0), 1)})
    vals = [r["proper_sets"] for r in rows]
    avg = round(sum(vals) / len(vals), 1) if vals else None
    direction = None
    if len(vals) >= 2:
        delta = vals[-1] - vals[0]
        direction = "up" if delta >= 1 else "down" if delta <= -1 else "flat"
    return {"weeks": rows, "avg": avg, "direction": direction}


def _tonnage_status(guard: Any) -> Optional[str]:
    if not isinstance(guard, dict):
        return None
    for tile in guard.get("tiles") or []:
        if isinstance(tile, dict) and tile.get("id") == "tonnage":
            return tile.get("status")
    return None


def _hold_up(payload: Dict[str, Any], day: str, guardrails: Optional[dict]) -> Tuple[bool, Optional[str]]:
    """True while the tonnage guardrail is red or a deload is called today."""
    guard = guardrails if isinstance(guardrails, dict) else payload.get("guardrails")
    tonnage = _tonnage_status(guard)
    if tonnage == "red":
        return True, "tonnage_red"
    end = _day(day)
    if end is not None:
        try:
            from .guardrails import _deload_dates

            if _iso(end) in _deload_dates(payload, end):
                return True, "deload"
        except Exception:  # noqa: BLE001
            pass
    return False, None


def planned_loads_from_rows(rows: Optional[Sequence[Any]], catalog: Optional[dict]) -> Dict[str, float]:
    """``{catalog id: planned working load}`` from plan rows. Assisted rows use effective load."""
    from .assisted_load import catalog_is_assisted

    out: Dict[str, float] = {}
    for item in rows or []:
        row = _ex_dict(item)
        if row is None:
            continue
        name = str(row.get("name") or row.get("exercise") or "")
        cat = _catalog_row(name, catalog) if name else None
        if cat is None:
            continue
        rx = row.get("prescription") if isinstance(row.get("prescription"), dict) else {}
        if catalog_is_assisted(cat):
            load = _f(row.get("effective_load_lbs"))
            if load is None:
                load = _f(rx.get("effective_load_lbs"))
        else:
            load = _f(row.get("weight_lbs"))
            if load is None:
                load = _f(row.get("load"))
            if load is None:
                load = _f(rx.get("weight_lbs"))
        if load is not None and load > 0:
            out[str(cat["id"])] = load
    return out


def _goals_for(payload: Dict[str, Any]) -> Dict[str, Any]:
    store = payload.get("workout_store") if isinstance(payload.get("workout_store"), dict) else {}
    goals = store.get("goals") if isinstance(store.get("goals"), dict) else None
    if goals is None and isinstance(payload.get("goals"), dict):
        goals = payload.get("goals")
    goals = dict(goals or {})
    if not isinstance(goals.get("set_budget"), dict):
        try:
            from .workout_store import load_workspace_goals

            loaded, _src = load_workspace_goals()
            if isinstance(loaded, dict) and isinstance(loaded.get("set_budget"), dict):
                goals["set_budget"] = loaded["set_budget"]
        except Exception:  # noqa: BLE001
            pass
    return goals


def _catalog_for(payload: Dict[str, Any]) -> Dict[str, Any]:
    store = payload.get("workout_store") if isinstance(payload.get("workout_store"), dict) else {}
    catalog = store.get("catalog") if isinstance(store.get("catalog"), dict) else None
    if catalog and isinstance(catalog.get("exercises"), list) and catalog["exercises"]:
        # A stored catalog without credit tags falls back to the workspace file.
        if any(isinstance(e, dict) and e.get("muscle_credit") for e in catalog["exercises"]):
            return catalog
    from .workout_store import load_workspace_catalog

    loaded, _src = load_workspace_catalog()
    return loaded if isinstance(loaded, dict) else {"exercises": []}


def _suggest_lifts(group: str, catalog: Optional[dict], limit: int = 3) -> List[str]:
    names: List[str] = []
    for ex in (catalog or {}).get("exercises") or []:
        if not isinstance(ex, dict) or not ex.get("available", True):
            continue
        weights = catalog_credit(ex)
        if weights.get(group) == PRIMARY_CREDIT and ex.get("name"):
            names.append(str(ex["name"]))
    return names[:limit]


def thursday_flags(
    day: str,
    groups: Dict[str, Dict[str, Any]],
    cfg: Dict[str, Any],
    catalog: Optional[dict],
) -> Dict[str, Any]:
    """Thursday-only suggestions. Never adds a session."""
    dt = _day(day)
    out: Dict[str, Any] = {
        "active": False,
        "weekday": dt.strftime("%A") if dt else None,
        "suggestions_only": True,
        "flags": [],
    }
    if dt is None or dt.weekday() != THURSDAY:
        return out
    out["active"] = True
    elapsed = dt.weekday() + 1  # Monday through Thursday
    flags: List[Dict[str, Any]] = []
    for group in GROUPS:
        row = groups[group]
        target = row["target"]
        name = GROUP_LABELS[group]
        lifts = _suggest_lifts(group, catalog)
        done = row["proper_sets"]
        if target["role"] == "priority":
            pace = round(done * 7.0 / elapsed, 1)
            if pace < target["low"]:
                msg = (
                    f"{name}: {done:g} proper sets through Thursday, on pace for {pace:g}. "
                    f"Priority target is {target['low']}-{target['high']}."
                )
                if lifts:
                    msg += " Options: " + ", ".join(lifts) + "."
                else:
                    msg += " No available lift credits this group yet."
                flags.append(
                    {
                        "kind": "priority_below_pace",
                        "group": group,
                        "label": name,
                        "proper_sets": done,
                        "pace": pace,
                        "target_low": target["low"],
                        "message": msg,
                        "suggested_lifts": lifts,
                    }
                )
        avg = row["trend_4w"]["avg"]
        if avg is not None and avg < TIER_GROWTH_FLOOR:
            msg = f"{name}: 4-week average {avg:g} proper sets a week, under {TIER_GROWTH_FLOOR:g}."
            if lifts:
                msg += " Options: " + ", ".join(lifts) + "."
            else:
                msg += " No available lift credits this group yet."
            flags.append(
                {
                    "kind": "trending_under_4",
                    "group": group,
                    "label": name,
                    "avg_4w": avg,
                    "message": msg,
                    "suggested_lifts": lifts,
                }
            )
    out["flags"] = flags
    return out


def build_muscle_sets(
    payload: Optional[Dict[str, Any]],
    day: str,
    *,
    plan_rows: Optional[Sequence[Any]] = None,
    guardrails: Optional[dict] = None,
) -> Dict[str, Any]:
    """The weekly tracker, budget and Thursday flags for the week holding ``day``.

    Returns ``{"muscle_sets", "muscle_sets_meta", "muscle_budget", "muscle_coach"}``.
    """
    data = payload if isinstance(payload, dict) else {}
    bounds = week_bounds(day)
    if bounds is None:
        return {}
    monday, sunday = bounds
    catalog = _catalog_for(data)
    goals = _goals_for(data)
    cfg = resolve_config(goals)
    sessions = data.get("sessions") if isinstance(data.get("sessions"), list) else []
    if plan_rows is None:
        store = data.get("workout_store") if isinstance(data.get("workout_store"), dict) else {}
        plan = store.get("plan") if isinstance(store.get("plan"), dict) else {}
        plan_rows = plan.get("exercises") if isinstance(plan.get("exercises"), list) else []
    planned = planned_loads_from_rows(plan_rows, catalog)
    horizon = _iso(_day(monday) - timedelta(days=7 * HISTORY_WEEKS))
    recent = [
        sd
        for sd in sessions
        if str((_session_dict(sd) or {}).get("date") or "")[:10] >= horizon
    ]
    weeks = weekly_credits(recent, catalog, planned_loads=planned, planned_day=day)

    dated = [
        str(_session_dict(s).get("date") or "")[:10]
        for s in sessions
        if _session_dict(s) is not None
    ]
    dated = [d for d in dated if _day(d)]
    first_week = week_bounds(min(dated))[0] if dated else None

    hold, hold_reason = _hold_up(data, str(day)[:10], guardrails)
    cap_info = weekly_cap(weeks, monday, cfg, first_week=first_week, hold_up=hold)
    cap = cap_info["cap"]

    this_week = weeks.get(monday, {})
    avg_by_group: Dict[str, Optional[float]] = {}
    trends: Dict[str, Dict[str, Any]] = {}
    for group in GROUPS:
        trends[group] = _trend(weeks, monday, group, first_week)
        avg_by_group[group] = trends[group]["avg"]

    guard = guardrails if isinstance(guardrails, dict) else data.get("guardrails")
    tonnage_green = _tonnage_status(guard) == "green"
    base_targets = group_targets(cfg)
    base_plan = budget_plan(cfg, base_targets, cap, avg_by_group)
    # Priority groups may reach 10+ only with tonnage green and the cap permitting.
    plo, phi = cfg["priority_target"]
    high_total = base_plan["planned_total"] + (phi - plo) * len(cfg["priority_groups"])
    stretch_ok = bool(
        tonnage_green and cap is not None and not hold and high_total <= cap
    )
    targets = group_targets(cfg, stretch=stretch_ok)
    plan = budget_plan(cfg, targets, cap, avg_by_group)

    groups: Dict[str, Dict[str, Any]] = {}
    for group in GROUPS:
        value = float(this_week.get(group, 0.0))
        tier = tier_for(value)
        groups[group] = {
            "label": GROUP_LABELS[group],
            "proper_sets": round(value, 1),
            "tier": tier,
            "tier_label": TIER_LABELS[tier],
            "target": dict(targets[group]),
            "trend_4w": trends[group],
        }
    total = round(sum(float(v) for v in this_week.values()), 1)
    meta = {
        "week_start": monday,
        "week_end": sunday,
        "as_of": str(day)[:10],
        "timezone": "America/New_York",
        "proper_set_total": total,
        "unit": "credited proper sets (primary 1.0, secondary 0.5)",
        "weeks_with_history": TREND_WEEKS if first_week is None else sum(
            1 for p in _prior_mondays(monday, TREND_WEEKS) if p >= first_week
        ),
    }
    budget = {
        "cap": cap,
        "cap_source": cap_info["source"],
        "cap_avg_4w": cap_info["avg_4w"],
        "cap_held": bool(cap_info["held"]),
        "cap_hold_reason": hold_reason if cap_info["held"] else None,
        "cap_override": cfg["cap_override"],
        "max_weekly_change_pct": round(cfg["max_weekly_change"] * 100, 1),
        "priority_groups": list(cfg["priority_groups"]),
        "middle_groups": list(cfg["middle_groups"]),
        "deprioritized_groups": list(cfg["deprioritized_groups"]),
        "maintenance_floor": cfg["maintenance_floor"],
        "planned_total": plan["planned_total"],
        "overrun": plan["overrun"],
        "warning": plan["warning"],
        "stretch_ok": stretch_ok,
        "review_every_weeks": cfg["review_every_weeks"],
        "config_warnings": list(cfg["warnings"]),
    }
    coach = thursday_flags(str(day)[:10], groups, cfg, catalog)
    return {
        "muscle_sets": groups,
        "muscle_sets_meta": meta,
        "muscle_budget": budget,
        "muscle_coach": coach,
    }


def attach_muscle_sets(payload: dict, *, as_of: Optional[str] = None) -> dict:
    """Dashboard payload key ``muscle_sets_week`` for the Trends tile."""
    day = str(as_of or "")[:10]
    if not day:
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        coach = payload.get("coach") if isinstance(payload.get("coach"), dict) else {}
        today = coach.get("today") if isinstance(coach.get("today"), dict) else {}
        day = str(meta.get("local_today") or today.get("date") or "")[:10]
    built = build_muscle_sets(payload, day) if day else {}
    payload["muscle_sets_week"] = built or None
    return payload


# ---------------------------------------------------- planner tiebreaks


def planner_budget(
    sessions: Sequence[Any],
    catalog: Optional[dict],
    goals: Optional[dict],
    day: str,
) -> Optional[Dict[str, Any]]:
    """Targets and this week's progress for the planner tiebreak. None when unusable."""
    bounds = week_bounds(day)
    if bounds is None or not isinstance(catalog, dict):
        return None
    cfg = resolve_config(goals)
    monday = bounds[0]
    this_week = []
    for session in sessions or []:
        stamp = (
            session.get("date")
            if isinstance(session, dict)
            else getattr(session, "date", None)
        )
        if str(stamp or "")[:10] >= monday:
            this_week.append(session)
    weeks = weekly_credits(this_week, catalog)
    done = weeks.get(monday, {})
    targets = group_targets(cfg)
    return {
        "targets": targets,
        "done": {g: float(done.get(g, 0.0)) for g in GROUPS},
    }


_ROLE_WEIGHT = {"priority": 3.0, "middle": 2.0, "deprioritized": 1.0}


def selection_need(
    row: Optional[dict],
    budget: Optional[Dict[str, Any]],
    planned: Optional[Dict[str, float]] = None,
) -> float:
    """How much this lift helps groups still short of their target. Tiebreak only."""
    if not budget:
        return 0.0
    weights = catalog_credit(row)
    total = 0.0
    for group, credit in weights.items():
        target = budget["targets"].get(group)
        if not target:
            continue
        have = float(budget["done"].get(group, 0.0)) + float((planned or {}).get(group, 0.0))
        gap = max(0.0, float(target["low"]) - have)
        total += credit * gap * _ROLE_WEIGHT.get(target["role"], 1.0)
    return round(total, 4)


def adjust_accessory_sets(
    hard: int,
    row: Optional[dict],
    budget: Optional[Dict[str, Any]],
    planned: Optional[Dict[str, float]],
    *,
    room: int,
    prior_sets: Optional[int] = None,
) -> Tuple[int, Optional[str]]:
    """Set-count tiebreak for an accessory. Never raises a count past the lift's last one.

    * A priority group still short of its target gets the sets back that the
      4-8 week band trimmed (up to ``prior_sets``), while the week stays at or
      under the priority ceiling and the session has room.
    * A floor group that already holds its floor gives one set up (min 1).

    ``room`` is the session's remaining set budget. Returns ``(sets, note)``.
    """
    if not budget or hard < 1:
        return hard, None
    weights = catalog_credit(row)
    prim = [g for g, w in weights.items() if w == PRIMARY_CREDIT]
    if not prim:
        return hard, None
    targets = budget["targets"]

    def have(group: str) -> float:
        return float(budget["done"].get(group, 0.0)) + float((planned or {}).get(group, 0.0))

    short_priority = [
        g
        for g in prim
        if targets.get(g, {}).get("role") == "priority" and have(g) < float(targets[g]["low"])
    ]
    if short_priority and prior_sets is not None and hard < int(prior_sets):
        g = short_priority[0]
        t = targets[g]
        new = hard
        while (
            new < int(prior_sets)
            and new + 1 <= room
            and have(g) + weights[g] * (new + 1) <= float(t["high"]) + 0.5
        ):
            new += 1
        if new > hard:
            return new, (
                f"Set budget: {new} sets, {GROUP_LABELS[g]} priority at "
                f"{have(g):g}/{t['low']} proper sets this week."
            )
        return hard, None
    if hard > 1 and all(
        targets.get(g, {}).get("role") == "deprioritized" and have(g) >= float(targets[g]["low"])
        for g in prim
    ):
        g = prim[0]
        t = targets[g]
        return hard - 1, (
            f"Set budget: -1 set, {GROUP_LABELS[g]} already holds its floor "
            f"({have(g):g}/{t['low']})."
        )
    return hard, None
