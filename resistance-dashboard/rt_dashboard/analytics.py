"""Volume, strength trends, and chart series from parsed sessions."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import Session


def session_volume(session: Session) -> float:
    return float(session.volume)


def exercise_volume(session: Session, exercise_name: str) -> float:
    total = 0.0
    target = exercise_name.lower()
    for ex in session.exercises:
        if ex.name.lower() == target:
            total += ex.volume
    return total


def best_e1rm(session: Session, exercise_name: str) -> Optional[float]:
    target = exercise_name.lower()
    best: Optional[float] = None
    for ex in session.exercises:
        if ex.name.lower() == target:
            val = ex.best_e1rm
            if best is None or val > best:
                best = val
    return best


def best_working_weight(session: Session, exercise_name: str) -> Optional[float]:
    target = exercise_name.lower()
    best: Optional[float] = None
    for ex in session.exercises:
        if ex.name.lower() == target:
            val = ex.best_working_weight
            if best is None or val > best:
                best = val
    return best


def volume_by_session(sessions: Sequence[Session]) -> List[Dict[str, Any]]:
    rows = []
    for s in sorted(sessions, key=lambda x: x.date):
        rows.append(
            {
                "date": s.date,
                "session_type": s.session_type,
                "volume": session_volume(s),
            }
        )
    return rows


def volume_by_week(sessions: Sequence[Session]) -> List[Dict[str, Any]]:
    buckets: Dict[str, float] = defaultdict(float)
    for s in sessions:
        dt = datetime.strptime(s.date, "%Y-%m-%d")
        # ISO week start Monday
        week_start = dt - timedelta(days=dt.weekday())
        key = week_start.strftime("%Y-%m-%d")
        buckets[key] += session_volume(s)
    return [
        {"week_start": k, "volume": buckets[k]}
        for k in sorted(buckets.keys())
    ]


def volume_by_month(sessions: Sequence[Session]) -> List[Dict[str, Any]]:
    """Aggregate total volume by calendar month (YYYY-MM)."""
    buckets: Dict[str, float] = defaultdict(float)
    for s in sessions:
        try:
            key = s.date[:7]  # YYYY-MM
        except Exception:
            continue
        if len(key) != 7 or key[4] != "-":
            continue
        buckets[key] += session_volume(s)
    return [
        {"month": k, "volume": buckets[k]}
        for k in sorted(buckets.keys())
    ]


def volume_by_day(
    sessions: Sequence[Session],
    *,
    days: int = 90,
    as_of: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Daily total volume for a contiguous calendar window (default 90 days).

    Days with no sessions still appear with volume 0 so the chart is a full
    span (one bar per day). If ``as_of`` is omitted and the last ``days`` from
    local today have no volume, the window ends on the most recent session
    date so the chart still shows recent training.
    """
    from .timeutil import local_today_iso

    days = max(1, int(days))
    today = local_today_iso()
    end_s = as_of or today

    def _window(end_iso: str) -> List[Dict[str, Any]]:
        end = datetime.strptime(end_iso, "%Y-%m-%d")
        start = end - timedelta(days=days - 1)
        buckets: Dict[str, float] = defaultdict(float)
        for s in sessions:
            try:
                d = datetime.strptime(s.date, "%Y-%m-%d")
            except ValueError:
                continue
            if start <= d <= end:
                buckets[s.date] += session_volume(s)
        out: List[Dict[str, Any]] = []
        cur = start
        while cur <= end:
            key = cur.strftime("%Y-%m-%d")
            out.append({"date": key, "volume": float(buckets.get(key, 0.0))})
            cur += timedelta(days=1)
        return out

    rows = _window(end_s)
    if as_of is None and sessions and sum(r["volume"] for r in rows) <= 0:
        last = max(s.date for s in sessions)
        rows = _window(last)
    return rows


def _norm_lift(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def _display_lift(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip())


def lift_alias_table(raw: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Case-insensitive alias → canonical display name. Empty input is no map."""
    table: Dict[str, str] = {}
    if not isinstance(raw, dict):
        return table
    for key, val in raw.items():
        norm = _norm_lift(str(key))
        display = _display_lift(str(val))
        if norm and display:
            table[norm] = display
    return table


def canonical_lift_name(name: str, table: Optional[Dict[str, str]] = None) -> str:
    display = _display_lift(name)
    if not display:
        return ""
    mapped = (table or {}).get(_norm_lift(display))
    return _display_lift(mapped) if mapped else display


def _coerce_set(st: Any) -> Optional[Tuple[float, int, int]]:
    if isinstance(st, bool):
        return None
    if isinstance(st, dict):
        weight, count, reps = st.get("weight_lbs"), st.get("sets"), st.get("reps")
    else:
        weight = getattr(st, "weight_lbs", None)
        count = getattr(st, "sets", None)
        reps = getattr(st, "reps", None)
    if isinstance(weight, bool) or isinstance(count, bool) or isinstance(reps, bool):
        return None
    try:
        return float(weight), int(count), int(reps)
    except (TypeError, ValueError):
        return None


def _epley(weight_lbs: float, reps: int) -> float:
    if reps <= 0:
        return 0.0
    if reps == 1:
        return float(weight_lbs)
    return float(weight_lbs) * (1.0 + reps / 30.0)


def _session_date(session: Any) -> str:
    if isinstance(session, dict):
        return str(session.get("date") or "")[:10]
    return str(getattr(session, "date", "") or "")[:10]


def _session_type_name(session: Any) -> str:
    if isinstance(session, dict):
        return str(session.get("session_type") or session.get("type") or "")
    return str(getattr(session, "session_type", "") or "")


def _session_exercises(session: Any) -> List[Any]:
    if isinstance(session, dict):
        raw = session.get("exercises") or []
    else:
        raw = getattr(session, "exercises", None) or []
    return list(raw) if isinstance(raw, list) else []


def _exercise_name(ex: Any) -> str:
    if isinstance(ex, dict):
        return str(ex.get("name") or "")
    return str(getattr(ex, "name", "") or "")


def _exercise_sets(ex: Any) -> List[Tuple[float, int, int]]:
    if isinstance(ex, dict):
        raw = ex.get("sets") or []
    else:
        raw = getattr(ex, "sets", None) or []
    if not isinstance(raw, list):
        return []
    parsed: List[Tuple[float, int, int]] = []
    for st in raw:
        row = _coerce_set(st)
        if row is not None:
            parsed.append(row)
    return parsed


def _set_payload(weight_lbs: float, reps: int) -> Dict[str, Any]:
    weight = float(weight_lbs)
    if abs(weight - round(weight)) < 1e-9:
        weight = float(int(round(weight)))
    return {"weight_lbs": weight, "reps": int(reps)}


def strength_trend(
    sessions: Sequence[Any],
    exercise_name: str,
    aliases: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Per-session first working set, top set, tonnage, and Epley e1RM.

    Alias-equivalent names collapse onto ``exercise_name``'s canonical
    spelling. The first logged set is the working set double progression
    judges (#954). Later sets still count toward tonnage and the top set.
    """
    from .test_noise import is_test_exercise_name

    table = lift_alias_table(aliases)
    want = canonical_lift_name(exercise_name, table)
    want_key = _norm_lift(want)
    if not want_key:
        return []
    points: List[Dict[str, Any]] = []
    ordered = sorted(sessions or [], key=_session_date)
    for session in ordered:
        groups: List[List[Tuple[float, int, int]]] = []
        for ex in _session_exercises(session):
            logged = _exercise_name(ex)
            if not logged or is_test_exercise_name(logged):
                continue
            if _norm_lift(canonical_lift_name(logged, table)) != want_key:
                continue
            sets = _exercise_sets(ex)
            if sets:
                groups.append(sets)
        if not groups:
            continue
        flat = [row for group in groups for row in group]
        first = groups[0][0]
        top = max(flat, key=lambda row: (row[0], row[2], row[1]))
        tonnage = sum(weight * count * reps for weight, count, reps in flat)
        set_count = sum(count for _weight, count, _reps in flat)
        best_weight = max(row[0] for row in flat)
        best_est = max(_epley(weight, reps) for weight, _count, reps in flat)
        points.append(
            {
                "date": _session_date(session),
                "session_type": _session_type_name(session),
                "exercise": want,
                "best_e1rm": best_est,
                "best_working_weight": best_weight,
                "first_set": _set_payload(first[0], first[2]),
                "top_set": _set_payload(top[0], top[2]),
                "lift_tonnage": float(tonnage),
                "set_count": int(set_count),
            }
        )
    return points


def _in_lift_window(day: str, end: str, days: int) -> bool:
    if len(str(day)) != 10 or len(str(end)) < 10:
        return False
    try:
        end_d = datetime.strptime(str(end)[:10], "%Y-%m-%d")
        cur = datetime.strptime(str(day), "%Y-%m-%d")
    except ValueError:
        return False
    start = end_d - timedelta(days=max(1, int(days)) - 1)
    return start <= cur <= end_d


def ordered_lift_names(
    sessions: Sequence[Any],
    main_lifts: Optional[Sequence[str]] = None,
    aliases: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Pinned main lifts first, then every other logged lift by frequency."""
    from .test_noise import is_test_exercise_name

    table = lift_alias_table(aliases)
    preferred: Dict[str, str] = {}
    pinned: List[str] = []
    for raw in main_lifts or []:
        display = canonical_lift_name(str(raw), table)
        key = _norm_lift(display)
        if not display or key in preferred:
            continue
        preferred[key] = display
        pinned.append(display)
    counts: Counter[str] = Counter()
    spellings: Dict[str, Counter[str]] = defaultdict(Counter)
    for session in sessions or []:
        for ex in _session_exercises(session):
            logged = _exercise_name(ex)
            if not logged or is_test_exercise_name(logged):
                continue
            canon = canonical_lift_name(logged, table)
            key = _norm_lift(canon)
            if not key:
                continue
            counts[key] += 1
            if key not in preferred:
                spellings[key][canon] += 1
    others: List[str] = []
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], preferred.get(kv[0], kv[0])))
    for key, _n in ranked:
        if key in preferred:
            continue
        spelling = spellings[key].most_common(1)[0][0]
        others.append(spelling)
    return pinned + others


def main_lift_series(
    sessions: Sequence[Any],
    goals: Optional[dict],
    *,
    end: Optional[str] = None,
    days: int = 90,
) -> Dict[str, List[Dict[str, Any]]]:
    """One series per configured main lift. Missing logs stay an empty list."""
    raw = goals if isinstance(goals, dict) else {}
    lifts = raw.get("main_lifts") if isinstance(raw.get("main_lifts"), list) else []
    alias_raw = raw.get("lift_aliases") if isinstance(raw.get("lift_aliases"), dict) else {}
    table = lift_alias_table(alias_raw)
    out: Dict[str, List[Dict[str, Any]]] = {}
    seen = set()
    for item in lifts:
        name = canonical_lift_name(str(item), table)
        key = _norm_lift(name)
        if not name or key in seen:
            continue
        seen.add(key)
        series = strength_trend(sessions, name, aliases=alias_raw)
        if end:
            series = [
                point
                for point in series
                if _in_lift_window(str(point.get("date") or ""), str(end), days)
            ]
        out[name] = series
    return out


def linear_slope(points: Sequence[Tuple[float, float]]) -> Optional[float]:
    """Simple least-squares slope dy/dx. Returns None if <2 points."""
    n = len(points)
    if n < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None
    return num / den


def exercise_strength_slope_lbs_per_day(
    sessions: Sequence[Any],
    exercise_name: str,
    aliases: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    trend = strength_trend(sessions, exercise_name, aliases=aliases)
    if len(trend) < 2:
        return None
    base = datetime.strptime(trend[0]["date"], "%Y-%m-%d")
    pts: List[Tuple[float, float]] = []
    for p in trend:
        d = datetime.strptime(p["date"], "%Y-%m-%d")
        x = (d - base).days
        y = float(p["best_working_weight"] or 0.0)
        pts.append((float(x), y))
    return linear_slope(pts)


def recent_training_volume(
    sessions: Sequence[Session], as_of: str, window_days: int = 7
) -> float:
    end = datetime.strptime(as_of, "%Y-%m-%d")
    start = end - timedelta(days=window_days - 1)
    total = 0.0
    for s in sessions:
        d = datetime.strptime(s.date, "%Y-%m-%d")
        if start <= d <= end:
            total += session_volume(s)
    return total


def top_exercises(sessions: Sequence[Session], limit: int = 30) -> List[str]:
    """Rank exercises by how often they appear in sessions (most logged first)."""
    counts: Dict[str, int] = defaultdict(int)
    for s in sessions:
        for e in s.exercises:
            counts[e.name] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _ in ranked[:limit]]


def dashboard_payload(
    sessions: Sequence[Session], *, goals: Optional[dict] = None
) -> Dict[str, Any]:
    from .test_noise import filter_sessions
    from .workout_planner import normalize_goals

    if goals is None:
        try:
            from .workout_store import load_workspace_goals

            goals, _src = load_workspace_goals()
        except Exception:  # noqa: BLE001
            goals = {}
    goals = normalize_goals(goals if isinstance(goals, dict) else None)
    main_lifts = list(goals.get("main_lifts") or [])
    lift_aliases = dict(goals.get("lift_aliases") or {})
    clean = filter_sessions(sessions)
    exercises = ordered_lift_names(clean, main_lifts, lift_aliases)
    trends = {
        name: strength_trend(clean, name, aliases=lift_aliases) for name in exercises
    }
    slopes = {
        name: exercise_strength_slope_lbs_per_day(clean, name, aliases=lift_aliases)
        for name in exercises
    }
    return {
        # Keep raw sessions for history (includes everything logged);
        # charts/trends use cleaned series above.
        "sessions": [s.to_dict() for s in sessions],
        "volume_by_session": volume_by_session(clean),
        "volume_by_week": volume_by_week(clean),
        "volume_by_month": volume_by_month(clean),
        "volume_by_day": volume_by_day(clean, days=90),
        "top_exercises": exercises,
        "main_lifts": main_lifts,
        "lift_aliases": lift_aliases,
        "strength_trends": trends,
        "strength_slopes": slopes,
        "session_count": len(sessions),
        "total_volume": sum(session_volume(s) for s in sessions),
        "session_count_clean": len(clean),
    }
