"""Volume, strength trends, and chart series from parsed sessions."""

from __future__ import annotations

from collections import defaultdict
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
    days: int = 30,
    as_of: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Daily total volume for a contiguous calendar window (default 30 days).

    Days with no sessions still appear with volume 0 so the chart is a full
    30-bar month span. If ``as_of`` is omitted and the last 30 days from
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


def strength_trend(
    sessions: Sequence[Session], exercise_name: str
) -> List[Dict[str, Any]]:
    """Per-session best e1RM and best working weight for an exercise."""
    points: List[Dict[str, Any]] = []
    for s in sorted(sessions, key=lambda x: x.date):
        e1 = best_e1rm(s, exercise_name)
        bw = best_working_weight(s, exercise_name)
        if e1 is None and bw is None:
            continue
        points.append(
            {
                "date": s.date,
                "session_type": s.session_type,
                "exercise": exercise_name,
                "best_e1rm": e1,
                "best_working_weight": bw,
            }
        )
    return points


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
    sessions: Sequence[Session], exercise_name: str
) -> Optional[float]:
    trend = strength_trend(sessions, exercise_name)
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


def dashboard_payload(sessions: Sequence[Session]) -> Dict[str, Any]:
    from .test_noise import filter_sessions, is_test_exercise_name

    clean = filter_sessions(sessions)
    # Default 30 so secondary lifts (e.g. Tricep Pushdowns) are not dropped
    # before the UI can show them; UI previously also sliced to 8.
    exercises = [
        n for n in top_exercises(clean, limit=30) if not is_test_exercise_name(n)
    ]
    trends = {name: strength_trend(clean, name) for name in exercises}
    slopes = {
        name: exercise_strength_slope_lbs_per_day(clean, name)
        for name in exercises
    }
    return {
        # Keep raw sessions for history (includes everything logged);
        # charts/trends use cleaned series above.
        "sessions": [s.to_dict() for s in sessions],
        "volume_by_session": volume_by_session(clean),
        "volume_by_week": volume_by_week(clean),
        "volume_by_month": volume_by_month(clean),
        "volume_by_day": volume_by_day(clean, days=30),
        "top_exercises": exercises,
        "strength_trends": trends,
        "strength_slopes": slopes,
        "session_count": len(sessions),
        "total_volume": sum(session_volume(s) for s in sessions),
        "session_count_clean": len(clean),
    }
