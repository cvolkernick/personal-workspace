"""Read-only daily steps history for the Trends steps chart (plan 2026-10-26).

``GET /api/agent/steps_history?weeks=8&end=YYYY-MM-DD`` -> ``fitdash.steps_history.v1``.

Pure: reads the dashboard health snapshot. Week math (Mon-Sun, >= MIN_DAYS
present days) matches the weekly coach. The step target comes from the
Goal 3b coach card (``cut_coach.coach_card_for_payload`` + ``today_targets``),
read-only. Never writes, never refreshes upstream.

Rules: missing or <=0 days are ``null`` (never 0). The rolling 7-day mean
needs >= MIN_DAYS present days in the trailing 7 calendar days, else null.
Today is partial: flagged, excluded from rolling and weekly averages.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs

SCHEMA = "fitdash.steps_history.v1"
TZ_NAME = "America/New_York"
DEFAULT_WEEKS = 8
MAX_WEEKS = 13


MIN_DAYS = 5  # present days a week (or 7-day window) needs to count


def _d(s: str) -> datetime:
    return datetime.strptime(s[:10], "%Y-%m-%d")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def monday_of(day: str) -> str:
    dt = _d(day)
    return _iso(dt - timedelta(days=dt.weekday()))


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _health(data: Dict[str, Any]) -> Dict[str, Any]:
    h = data.get("health") if isinstance(data, dict) else None
    if hasattr(h, "to_dict"):
        try:
            h = h.to_dict()
        except Exception:  # noqa: BLE001
            h = {}
    return h if isinstance(h, dict) else {}


def daily_steps(health: Any) -> Dict[str, Dict[str, Any]]:
    """{date: {date, steps, source}}. Drops missing and <=0; last row per date wins."""
    rows = []
    if isinstance(health, dict):
        rows = health.get("steps") or []
    elif health is not None:
        rows = getattr(health, "steps", None) or []
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        raw = row.to_dict() if hasattr(row, "to_dict") else row
        if not isinstance(raw, dict):
            continue
        day = str(raw.get("date") or "")[:10]
        n = _num(raw.get("steps"))
        if len(day) != 10 or n is None or n <= 0:
            continue  # zero rollups are not a logged day
        out[day] = {"date": day, "steps": int(n), "source": raw.get("source")}
    return out


def summarize_week(week_start: str, steps: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Mon-Sun step summary. ``steps_used`` needs >= MIN_DAYS present days."""
    days = [_iso(_d(week_start) + timedelta(days=i)) for i in range(7)]
    vals = [steps[d]["steps"] for d in days if d in steps]
    avg = round(sum(vals) / len(vals), 0) if vals else None
    return {
        "week_start": week_start,
        "week_end": days[-1],
        "step_days": len(vals),
        "avg_steps": avg,
        "steps_used": len(vals) >= MIN_DAYS,
    }


def coach_step_target(data: Dict[str, Any], today: str) -> Optional[Any]:
    """Step target from the Goal 3b coach card. Read-only; None when unavailable."""
    try:
        from .cut_coach import coach_card_for_payload, today_targets

        card = coach_card_for_payload(data, as_of=today)
        return today_targets(card).get("step_target")
    except Exception:  # noqa: BLE001 - the chart must still answer
        return None


def parse_params(query: str, today: str) -> Tuple[Optional[Tuple[int, str]], Optional[Dict[str, Any]]]:
    """(weeks, end) or a 400 body. Future ``end`` clamps to today (ET)."""
    q = parse_qs(query or "")
    raw_w = (q.get("weeks") or [""])[0].strip()
    if raw_w == "":
        weeks = DEFAULT_WEEKS
    else:
        try:
            weeks = int(raw_w)
        except ValueError:
            weeks = -1
        if not 1 <= weeks <= MAX_WEEKS:
            return None, {"ok": False, "error": "bad_weeks", "message": f"weeks must be an integer 1-{MAX_WEEKS}"}
    raw_e = (q.get("end") or [""])[0].strip()
    if raw_e == "":
        end = today
    else:
        try:
            if len(raw_e) != 10:
                raise ValueError(raw_e)
            end = _iso(datetime.strptime(raw_e, "%Y-%m-%d"))
        except ValueError:
            return None, {"ok": False, "error": "bad_end", "message": "end must be YYYY-MM-DD"}
        if end > today:
            end = today
    return (weeks, end), None


def build_steps_history(
    health: Any,
    *,
    end: str,
    weeks: int,
    today: str,
    step_target: Optional[Any] = None,
    last_adjustment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    weeks = max(1, min(int(weeks), MAX_WEEKS))
    end = min(end, today)
    start = _iso(_d(monday_of(end)) - timedelta(days=7 * (weeks - 1)))
    steps = daily_steps(health)  # drops missing and <=0; last row per date wins
    complete_steps = {d: v for d, v in steps.items() if d != today}  # today is partial

    days = []
    cur = _d(start)
    last = _d(end)
    present = missing = 0
    while cur <= last:
        day = _iso(cur)
        row = steps.get(day)
        window = [_iso(cur - timedelta(days=i)) for i in range(7)]
        vals = [complete_steps[w]["steps"] for w in window if w in complete_steps]
        rolling = round(sum(vals) / len(vals), 1) if len(vals) >= MIN_DAYS else None
        if row:
            present += 1
        else:
            missing += 1
        days.append({
            "date": day,
            "steps": row["steps"] if row else None,
            "source": row.get("source") if row else None,
            "partial": day == today,
            "rolling_7d": rolling,
            "rolling_days": len(vals),
        })
        cur += timedelta(days=1)

    week_rows = []
    ws = _d(start)
    while ws <= last:
        s = summarize_week(_iso(ws), complete_steps)
        week_end = _iso(ws + timedelta(days=6))
        week_rows.append({
            "week_start": s["week_start"],
            "week_end": week_end,
            "avg_steps": s["avg_steps"],
            "step_days": s["step_days"],
            "steps_used": s["steps_used"],
            "complete": week_end < today,
        })
        ws += timedelta(days=7)

    adj = last_adjustment if isinstance(last_adjustment, dict) and last_adjustment.get("lever") == "steps" else None
    return {
        "ok": True,
        "schema": SCHEMA,
        "tz": TZ_NAME,
        "as_of": today,
        "from": start,
        "to": end,
        "days": days,
        "weeks": week_rows,
        "step_target": {"value": step_target, "source": "coach_card.step_target", "last_adjustment": adj},
        "coverage": {
            "days_present": present,
            "days_missing": missing,
            "oldest_available": min(steps) if steps else None,
        },
    }


def steps_history_response(data: Dict[str, Any], query: str, *, today: str) -> Tuple[int, Dict[str, Any]]:
    """(status, body) for the route. Reads the coach card; never saves anything."""
    params, err = parse_params(query, today)
    if err:
        return 400, err
    weeks, end = params
    return 200, build_steps_history(
        _health(data), end=end, weeks=weeks, today=today,
        step_target=coach_step_target(data, today), last_adjustment=None,
    )
