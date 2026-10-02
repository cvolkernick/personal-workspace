"""Daily hydration quest: civil-day water versus the Today water target.

Progress is the civil-day total already on the health series (Hidrate
``Day.totalAmount`` when that date has a Hidrate Day; Google Health only
when it does not). Rows are not summed. The wake-window sip bar stays on
the Today pacing card and does not complete this quest.

Target is the same number the pacing card uses (``hydration_bars``):
35 ml/kg from the latest weight on or before the day, else 2500 ml.
The 7-day adherence counter's flat 2500 is a historical hit-rate, not
this target.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .hydration_bars import (
    DEFAULT_HYDRATION_GOAL_ML,
    hydration_target_ml_from_lbs,
    latest_weight_lbs,
    water_ml_for_day,
)

KIND_KEY = "hydration|water"
SLUG = "water"
GROUP = "hydration"

MOTIVATION = (
    "Hydration supports performance and appetite control. "
    "Sip through the day, not only at meals."
)


def _civil_day(raw: Any) -> str:
    return str(raw or "")[:10]


def _row_map(row: Any) -> Optional[dict]:
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    if hasattr(row, "to_dict"):
        try:
            mapped = row.to_dict()
            if isinstance(mapped, dict):
                return mapped
        except Exception:  # noqa: BLE001
            return None
    out: Dict[str, Any] = {}
    for key in ("date", "water_ml", "source", "weight_lbs", "lbs"):
        if hasattr(row, key):
            out[key] = getattr(row, key)
    return out or None


def compact_hydration_days(rows: Optional[Sequence[Any]]) -> List[dict]:
    """One row per date. The first row wins — do not add GH on top of Hidrate."""
    out: List[dict] = []
    seen = set()
    for row in rows or []:
        mapped = _row_map(row)
        if not mapped:
            continue
        day = _civil_day(mapped.get("date"))
        if len(day) != 10 or day in seen:
            continue
        try:
            ml = float(mapped.get("water_ml") or 0)
        except (TypeError, ValueError):
            ml = 0.0
        seen.add(day)
        out.append(
            {
                "date": day,
                "water_ml": round(max(0.0, ml), 1),
                "source": str(mapped.get("source") or "unknown"),
            }
        )
    return out


def compact_body_weight(rows: Optional[Sequence[Any]]) -> List[dict]:
    out: List[dict] = []
    seen = set()
    for row in rows or []:
        mapped = _row_map(row)
        if not mapped:
            continue
        day = _civil_day(mapped.get("date"))
        if len(day) != 10 or day in seen:
            continue
        raw = mapped.get("weight_lbs")
        if raw is None:
            raw = mapped.get("lbs")
        try:
            lbs = float(raw)
        except (TypeError, ValueError):
            continue
        if lbs <= 0:
            continue
        seen.add(day)
        out.append({"date": day, "weight_lbs": round(lbs, 2)})
    return out


def hydration_title(*, current_ml: float, target_ml: float) -> str:
    current = max(0, int(round(float(current_ml or 0))))
    target = max(1, int(round(float(target_ml or 0))))
    return f"Hydration — {current} / {target} ml"


def _series_from_board(board: dict) -> List[dict]:
    raw = board.get("hydration_days")
    if isinstance(raw, (list, tuple)):
        return compact_hydration_days(raw)
    health = board.get("health") if isinstance(board.get("health"), dict) else {}
    nested = health.get("hydration")
    if isinstance(nested, (list, tuple)):
        return compact_hydration_days(nested)
    return []


def _weight_from_board(board: dict) -> List[dict]:
    raw = board.get("body_weight")
    if isinstance(raw, (list, tuple)):
        return compact_body_weight(raw)
    health = board.get("health") if isinstance(board.get("health"), dict) else {}
    nested = health.get("weight")
    if isinstance(nested, (list, tuple)):
        return compact_body_weight(nested)
    return []


def _snapshot(board: dict, day: str) -> dict:
    snap = board.get("hydration")
    if not isinstance(snap, dict):
        return {}
    snap_day = _civil_day(snap.get("date"))
    if snap_day and day and snap_day != day:
        return {}
    return snap


def hydration_spec(
    today: Optional[dict] = None,
    *,
    hydration: Optional[Sequence[Any]] = None,
    weight: Optional[Sequence[Any]] = None,
    as_of: Optional[str] = None,
    target_ml: Optional[float] = None,
) -> Dict[str, Any]:
    """Stable hydration|water prescription for this civil day.

    Missing today → 0 ml logged, not a hit. Target still renders so the
    quest is visible before the first sip.
    """
    board = today if isinstance(today, dict) else {}
    day = _civil_day(as_of or board.get("date"))
    days = (
        compact_hydration_days(hydration)
        if hydration is not None
        else _series_from_board(board)
    )
    weights = (
        compact_body_weight(weight)
        if weight is not None
        else _weight_from_board(board)
    )
    snap = _snapshot(board, day)
    day_row = water_ml_for_day(days, as_of=day) if day else {
        "date": day,
        "water_ml": 0.0,
        "source": "none",
    }
    if days or hydration is not None:
        current = float(day_row.get("water_ml") or 0)
        source = str(day_row.get("source") or "none")
    elif snap.get("water_ml") is not None:
        try:
            current = max(0.0, float(snap.get("water_ml") or 0))
        except (TypeError, ValueError):
            current = 0.0
        source = str(snap.get("source") or "none")
    else:
        current = 0.0
        source = "none"

    target_source = "default"
    weight_lbs = None
    weight_date = None
    if target_ml is not None:
        try:
            override = float(target_ml)
        except (TypeError, ValueError):
            override = 0.0
        if override > 0:
            target = override
            target_source = "override"
        else:
            target = DEFAULT_HYDRATION_GOAL_ML
    else:
        wt = latest_weight_lbs(weights, as_of=day) if day else None
        derived = (
            hydration_target_ml_from_lbs(wt["weight_lbs"]) if wt is not None else None
        )
        if derived is not None and derived > 0:
            target = float(derived)
            target_source = "weight_35ml_kg"
            weight_lbs = wt["weight_lbs"]
            weight_date = wt["date"]
        elif snap.get("target_ml") not in (None, "") and not weights:
            try:
                target = float(snap.get("target_ml"))
            except (TypeError, ValueError):
                target = DEFAULT_HYDRATION_GOAL_ML
            else:
                if target <= 0:
                    target = DEFAULT_HYDRATION_GOAL_ML
                    target_source = "default"
                else:
                    target_source = str(snap.get("target_source") or "snapshot")
        else:
            target = DEFAULT_HYDRATION_GOAL_ML

    current_i = max(0, int(round(current)))
    target_i = max(1, int(round(target)))
    hit = current_i >= target_i
    return {
        "kind": KIND_KEY,
        "slug": SLUG,
        "group": GROUP,
        "date": day or None,
        "water_ml": round(current, 1),
        "target_ml": float(target_i),
        "target_source": target_source,
        "source": source,
        "weight_lbs": weight_lbs,
        "weight_date": weight_date,
        "hit": hit,
        "title": hydration_title(current_ml=current_i, target_ml=target_i),
        "motivation": MOTIVATION,
        "days": days,
        "weights": weights,
    }
