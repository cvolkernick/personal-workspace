"""Coach rule load and step-target helpers (Goal 3b).

``cut_band_pct`` is read here. ``cut_coach.BANDS`` is built from this
loader. Missing keys fall back to the 0.25–0.5 cut band.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

RULES_PATH = "fitness/coach/coach_rules.json"

# New defaults. The previous hardcoded 0.5–1.0 / 0.5–0.75 band is not a fallback.
DEFAULT_CUT_BAND = (0.25, 0.5)
DEFAULT_CUT_TARGET = (0.25, 0.5)
DEFAULT_MAINTENANCE_BAND = (-0.25, 0.25)
DEFAULT_KCAL_TOLERANCE = 150
DEFAULT_HYDRATION_ML = 3000.0
DEFAULT_FIBER_G = 35
DEFAULT_STEP_TARGET = 7000
DEFAULT_STEP_UP = 1500
DEFAULT_DELOAD_DROP = 1500
DEFAULT_STEP_FLOOR = 5000
DEFAULT_STEP_SPACING_DAYS = 14
DEFAULT_ADHERENCE_GATE = 0.9
DEFAULT_KCAL_FLOOR_PER_LB = 11.0
DEFAULT_KCAL_OVER = 150
DEFAULT_KCAL_CUT = 150
DEFAULT_KCAL_BUMP = 150
STRENGTH_TRIP_PCT = 0.5

_RULES_CACHE: Optional[dict] = None


def _candidates(rel: str):
    from .equipment_store import _workspace_file_candidates

    return _workspace_file_candidates(rel)


def read_json(rel: str) -> Optional[dict]:
    for path in _candidates(rel):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def load_rules(*, refresh: bool = False) -> dict:
    """Return coach_rules.json, or the built-in defaults when the file is missing."""
    global _RULES_CACHE
    if _RULES_CACHE is not None and not refresh:
        return _RULES_CACHE
    loaded = read_json(RULES_PATH)
    _RULES_CACHE = loaded if isinstance(loaded, dict) else {}
    return _RULES_CACHE


def clear_rules_cache() -> None:
    global _RULES_CACHE
    _RULES_CACHE = None


def _pair(raw: Any, fallback: tuple) -> tuple:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        try:
            return (float(raw[0]), float(raw[1]))
        except (TypeError, ValueError):
            return fallback
    return fallback


def cut_band(rules: Optional[dict] = None) -> tuple:
    rules = load_rules() if rules is None else rules
    return _pair((rules or {}).get("cut_band_pct"), DEFAULT_CUT_BAND)


def cut_target_band(rules: Optional[dict] = None) -> tuple:
    rules = load_rules() if rules is None else rules
    return _pair((rules or {}).get("cut_target_pct"), DEFAULT_CUT_TARGET)


def maintenance_band(rules: Optional[dict] = None) -> tuple:
    rules = load_rules() if rules is None else rules
    return _pair((rules or {}).get("maintenance_band_pct"), DEFAULT_MAINTENANCE_BAND)


def kcal_tolerance(rules: Optional[dict] = None) -> int:
    rules = load_rules() if rules is None else rules
    try:
        n = int((rules or {}).get("kcal_indicator_tolerance") or DEFAULT_KCAL_TOLERANCE)
    except (TypeError, ValueError):
        n = DEFAULT_KCAL_TOLERANCE
    return n if n > 0 else DEFAULT_KCAL_TOLERANCE


def hydration_target_ml(rules: Optional[dict] = None) -> Optional[float]:
    """Flat daily target from coach rules. No lifting-day or hot-day tier."""
    rules = load_rules() if rules is None else rules
    blob = (rules or {}).get("hydration")
    if not isinstance(blob, dict):
        return None
    raw = blob.get("target_ml")
    if raw in (None, ""):
        return None
    try:
        ml = float(raw)
    except (TypeError, ValueError):
        return None
    return ml if ml > 0 else None


def fiber_floor_g(rules: Optional[dict] = None) -> int:
    rules = load_rules() if rules is None else rules
    floors = (rules or {}).get("nutrition_floors")
    raw = floors.get("fiber_g_min") if isinstance(floors, dict) else None
    try:
        n = int(raw) if raw not in (None, "") else DEFAULT_FIBER_G
    except (TypeError, ValueError):
        n = DEFAULT_FIBER_G
    return n if n > 0 else DEFAULT_FIBER_G


def steps_cfg(rules: Optional[dict] = None) -> dict:
    rules = load_rules() if rules is None else rules
    raw = (rules or {}).get("steps")
    raw = raw if isinstance(raw, dict) else {}

    def _num(key: str, default: float) -> float:
        try:
            return float(raw.get(key) if raw.get(key) not in (None, "") else default)
        except (TypeError, ValueError):
            return float(default)

    return {
        "default_target": int(_num("default_target", DEFAULT_STEP_TARGET)),
        "step_up": int(_num("step_up", DEFAULT_STEP_UP)),
        "deload_drop": int(_num("deload_drop", DEFAULT_DELOAD_DROP)),
        "floor": int(_num("floor", DEFAULT_STEP_FLOOR)),
        "min_spacing_days": int(_num("min_spacing_days", DEFAULT_STEP_SPACING_DAYS)),
        "adherence_gate": _num("adherence_gate", DEFAULT_ADHERENCE_GATE),
        "counted_weeks": int(_num("counted_weeks", 2)),
    }


def kcal_floor_per_lb(rules: Optional[dict] = None) -> float:
    rules = load_rules() if rules is None else rules
    try:
        n = float((rules or {}).get("kcal_floor_per_lb") or DEFAULT_KCAL_FLOOR_PER_LB)
    except (TypeError, ValueError):
        n = DEFAULT_KCAL_FLOOR_PER_LB
    return n if n > 0 else DEFAULT_KCAL_FLOOR_PER_LB


def adherence_kcal_over(rules: Optional[dict] = None) -> int:
    rules = load_rules() if rules is None else rules
    try:
        return int((rules or {}).get("adherence_kcal_over") or DEFAULT_KCAL_OVER)
    except (TypeError, ValueError):
        return DEFAULT_KCAL_OVER


def kcal_cut_size(rules: Optional[dict] = None) -> int:
    rules = load_rules() if rules is None else rules
    try:
        return int((rules or {}).get("kcal_cut") or DEFAULT_KCAL_CUT)
    except (TypeError, ValueError):
        return DEFAULT_KCAL_CUT


def kcal_bump_size(rules: Optional[dict] = None) -> int:
    rules = load_rules() if rules is None else rules
    try:
        return int((rules or {}).get("kcal_bump") or DEFAULT_KCAL_BUMP)
    except (TypeError, ValueError):
        return DEFAULT_KCAL_BUMP


def strength_trip_pct(rules: Optional[dict] = None) -> float:
    """Strength-trip edge stays 0.5. It is not derived from the band."""
    rules = load_rules() if rules is None else rules
    try:
        n = float((rules or {}).get("strength_trip_pct") if (rules or {}).get("strength_trip_pct") not in (None, "") else STRENGTH_TRIP_PCT)
    except (TypeError, ValueError):
        n = STRENGTH_TRIP_PCT
    return n


def parse_day(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def shift_days(day: date, n: int) -> date:
    return day + timedelta(days=int(n))


def days_between(start: Any, end: Any) -> Optional[int]:
    a = parse_day(start)
    b = parse_day(end)
    if not a or not b:
        return None
    return (b - a).days


def week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())
