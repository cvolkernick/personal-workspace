"""Read-only calories-vs-weight rows for agents.

Intake and burned use the daily series behind the Trends
"Calories intake vs burned" chart:

  health.nutrition[].calories
  health.calories_burned[].calories

That is the raw daily point, not the 7-day mean. A missing day stays
null. A logged 0 stays 0. Weight is null when that civil day has no
weigh-in.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs

from rt_dashboard.timeutil import local_today_iso, resolve_tz_name

DEFAULT_DAYS = 90
MAX_DAYS = 366
HEALTH_ALERT = "fitdash_trends_health"


def parse_export_days(raw: Optional[str]) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    """Default 90. Anything outside 1..366 is a 400 body, not a silent clamp."""
    if raw is None or str(raw).strip() == "":
        return DEFAULT_DAYS, None
    text = str(raw).strip()
    if not text.isdigit():
        return None, _bad_days(text)
    days = int(text)
    if days < 1 or days > MAX_DAYS:
        return None, _bad_days(text)
    return days, None


def _bad_days(text: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": "bad_days",
        "message": (
            f"days must be an integer from 1 to {MAX_DAYS} "
            f"(default {DEFAULT_DAYS}). Got {text!r}."
        ),
    }


def civil_labels(end_iso: str, days: int) -> List[str]:
    """Inclusive civil window ending on end_iso. Oldest first.

    Date arithmetic stays on the calendar date so a DST boundary
    cannot skip or repeat a label. Same idea as the chart's UTC shift.
    """
    end = datetime.strptime(str(end_iso)[:10], "%Y-%m-%d").date()
    start = end - timedelta(days=days - 1)
    labels: List[str] = []
    cursor = start
    while cursor <= end:
        labels.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return labels


def _finite_kcal(value: Any) -> Optional[float]:
    """Last finite number wins. Blank, bool, and non-numeric stay absent."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _row_get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _series(health: Any, key: str) -> list:
    if health is None:
        return []
    if isinstance(health, dict):
        raw = health.get(key) or []
    else:
        raw = getattr(health, key, None) or []
    return list(raw) if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)) else []


def kcal_by_date(rows: Iterable[Any], *, value_key: str = "calories") -> Dict[str, float]:
    """Match calorie-rolling-avg.js kcalByDate: last finite value wins."""
    by: Dict[str, float] = {}
    for row in rows or []:
        if row is None:
            continue
        day = str(_row_get(row, "date") or "")[:10]
        if len(day) != 10:
            continue
        value = _finite_kcal(_row_get(row, value_key))
        if value is None:
            continue
        by[day] = value
    return by


def _health_error(health: Any) -> str:
    if health is None:
        return ""
    if isinstance(health, dict):
        return str(health.get("error") or "").strip()
    return str(getattr(health, "error", "") or "").strip()


def _query_tz(headers, query: str) -> str:
    raw = ""
    vals = parse_qs(query or "", keep_blank_values=False).get("tz") or []
    if vals:
        raw = str(vals[0] or "").strip()
    if not raw and headers is not None:
        raw = str(
            headers.get("X-Viewer-TZ")
            or headers.get("X-Dashboard-TZ")
            or headers.get("x-viewer-tz")
            or headers.get("x-dashboard-tz")
            or ""
        ).strip()
    return resolve_tz_name(raw)


def export_trends_window(
    health: Any,
    *,
    days: int = DEFAULT_DAYS,
    end: str,
    tz_name: str,
    extra_error: str = "",
) -> Dict[str, Any]:
    """Daily rows for the chart window. Unlogged intake is null, never 0."""
    nutrition = kcal_by_date(_series(health, "nutrition"))
    burned = kcal_by_date(_series(health, "calories_burned"))
    weight = kcal_by_date(_series(health, "weight"), value_key="weight_lbs")
    logged_days = set()
    for row in _series(health, "food_logs"):
        day = str(_row_get(row, "date") or "")[:10]
        if len(day) == 10:
            logged_days.add(day)
    # A nutrition total is itself a food-log day on the chart.
    logged_days.update(nutrition)

    err_parts = [part for part in (_health_error(health), (extra_error or "").strip()) if part]
    err = "; ".join(err_parts)
    series_empty = not nutrition and not burned and not weight and not _series(health, "food_logs")
    if err and series_empty:
        return {
            "ok": False,
            "error": "health_unavailable",
            "message": err,
            "alert": HEALTH_ALERT,
        }

    labels = civil_labels(end, days)
    rows: List[Dict[str, Any]] = []
    for day in labels:
        intake = nutrition.get(day)
        logged = day in logged_days
        rows.append(
            {
                "date": day,
                "intake_kcal": intake,
                "burned_kcal": burned.get(day),
                "weight_lb": weight.get(day),
                "logged": True if logged else False,
            }
        )
    return {
        "ok": True,
        "days": days,
        "start": labels[0],
        "end": labels[-1],
        "timezone": tz_name,
        "burned_source": "health.calories_burned.calories",
        "intake_source": "health.nutrition.calories",
        "rows": rows,
        "health_error": err or None,
    }


def respond_trends_export(headers, query: str, health: Any, extra_error: str = "") -> Tuple[int, Dict[str, Any]]:
    """Parse days and build the JSON body. Auth stays with the caller."""
    raw_days = (parse_qs(query or "").get("days") or [None])[0]
    days, err = parse_export_days(None if raw_days is None else str(raw_days))
    if err:
        return 400, err
    tz_name = _query_tz(headers, query or "")
    end = local_today_iso(tz_name)
    body = export_trends_window(
        health,
        days=int(days or DEFAULT_DAYS),
        end=end,
        tz_name=tz_name,
        extra_error=extra_error,
    )
    if not body.get("ok"):
        return 503, body
    return 200, body
