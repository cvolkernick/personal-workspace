"""Assisted-lift effective load (#1156).

Logged weight stays the machine pin. Effective load is bodyweight minus that
pin. The closest weigh-in in either direction is used, with no window. The
stored log is not rewritten.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import ExerciseEntry, Session, SetEntry

Readout = Tuple[float, int, str]  # bodyweight lb, absolute days away, weigh-in date


def _tag(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def catalog_is_assisted(ex: Optional[dict]) -> bool:
    """True for ``assisted: true`` or any ``assisted_pullup`` equipment tag."""
    if not isinstance(ex, dict):
        return False
    if ex.get("assisted") is True:
        return True
    tags: List[str] = []
    for key in ("equipment", "equipment_any"):
        raw = ex.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, list):
            tags.extend(_tag(item) for item in raw)
    return "assisted_pullup" in tags


def _parse_day(value: Any):
    text = str(value or "")[:10]
    if len(text) != 10:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _sample_day_lbs(sample: Any) -> Optional[Tuple[Any, float]]:
    if isinstance(sample, dict):
        day = _parse_day(sample.get("date"))
        raw = sample.get("weight_lbs")
        if raw is None:
            raw = sample.get("lbs")
    else:
        day = _parse_day(getattr(sample, "date", None))
        raw = getattr(sample, "weight_lbs", None)
    if day is None:
        return None
    try:
        lbs = float(raw)
    except (TypeError, ValueError):
        return None
    if lbs <= 0:
        return None
    return day, lbs


def nearest_weighin(day: Any, samples: Optional[Sequence[Any]]) -> Optional[Readout]:
    """Closest weigh-in in either direction. Ties prefer the earlier date.

    Returns ``(bodyweight_lbs, days_away, weighin_date)``. No samples → None.
    There is no maximum gap.
    """
    session_day = _parse_day(day)
    if session_day is None:
        return None
    by_day: Dict[Any, float] = {}
    for sample in samples or []:
        parsed = _sample_day_lbs(sample)
        if parsed is None:
            continue
        by_day[parsed[0]] = parsed[1]
    if not by_day:
        return None
    chosen = None
    for weigh_day, lbs in by_day.items():
        delta = abs((session_day - weigh_day).days)
        past_rank = 0 if weigh_day <= session_day else 1
        key = (delta, past_rank, weigh_day.toordinal())
        if chosen is None or key < chosen[0]:
            chosen = (key, lbs, delta, weigh_day)
    _key, lbs, delta, weigh_day = chosen
    return float(lbs), int(delta), weigh_day.isoformat()


def effective_load_lbs(bodyweight: float, assistance: float) -> float:
    return round(float(bodyweight) - float(assistance), 1)


def _reduce_assistance(weight: float, step: float) -> float:
    """One pin less. Zero assistance is a bodyweight rep, not a floor at the step."""
    dropped = round(float(weight) - float(step), 1)
    if dropped < 0:
        return 0.0
    return dropped


def assisted_plan_fields(
    assistance: float,
    day: Any,
    samples: Optional[Sequence[Any]],
) -> Dict[str, Any]:
    """Additive plan/log fields. The assistance number is unchanged."""
    pin = round(float(assistance), 1)
    out: Dict[str, Any] = {
        "assistance_lbs": pin,
    }
    match = nearest_weighin(day, samples)
    if match is None:
        out["effective_load_source"] = "none"
        return out
    bodyweight, days_away, _when = match
    out["effective_load_source"] = "nearest_weighin"
    out["effective_load_lbs"] = effective_load_lbs(bodyweight, pin)
    out["weighin_days_away"] = int(days_away)
    return out


def _default_catalog() -> dict:
    from .workout_store import load_workspace_catalog

    catalog, _src = load_workspace_catalog()
    return catalog if isinstance(catalog, dict) else {"exercises": []}


def _is_assisted_name(name: str, catalog: dict) -> bool:
    from .workout_planner import _catalog_row_for_name

    return catalog_is_assisted(_catalog_row_for_name(name, catalog))


def _mark_set(st: SetEntry, *, source: str, bodyweight: Optional[float], days_away: Optional[int]) -> None:
    st.effective_load_source = source
    if source == "nearest_weighin" and bodyweight is not None:
        st.exclude_from_load_math = False
        st.tonnage_weight_lbs = effective_load_lbs(bodyweight, st.weight_lbs)
        st.weighin_days_away = int(days_away or 0)
    else:
        st.exclude_from_load_math = True
        st.tonnage_weight_lbs = None
        st.weighin_days_away = None


def _mark_exercise(ex: ExerciseEntry, *, source: str, bodyweight: Optional[float], days_away: Optional[int]) -> None:
    ex.effective_load_source = source
    first = ex.sets[0] if ex.sets else None
    ex.assistance_lbs = float(first.weight_lbs) if first is not None else None
    if source == "nearest_weighin" and bodyweight is not None and first is not None:
        ex.effective_load_lbs = effective_load_lbs(bodyweight, first.weight_lbs)
        ex.weighin_days_away = int(days_away or 0)
    else:
        ex.effective_load_lbs = None
        ex.weighin_days_away = None
    for st in ex.sets:
        _mark_set(st, source=source, bodyweight=bodyweight, days_away=days_away)


def _session_exercises(session: Any) -> List[Any]:
    if isinstance(session, dict):
        raw = session.get("exercises") or []
        return list(raw) if isinstance(raw, list) else []
    return list(getattr(session, "exercises", None) or [])


def _session_day(session: Any) -> str:
    if isinstance(session, dict):
        return str(session.get("date") or "")[:10]
    return str(getattr(session, "date", "") or "")[:10]


def _exercise_name(ex: Any) -> str:
    if isinstance(ex, dict):
        return str(ex.get("name") or ex.get("exercise") or "")
    return str(getattr(ex, "name", "") or "")


def _apply_dict_exercise(ex: dict, *, source: str, bodyweight: Optional[float], days_away: Optional[int]) -> None:
    sets = ex.get("sets") if isinstance(ex.get("sets"), list) else []
    first_pin = None
    total = 0.0
    for st in sets:
        if not isinstance(st, dict):
            continue
        try:
            pin = float(st.get("weight_lbs") or 0)
            count = int(st.get("sets") or 0)
            reps = int(st.get("reps") or 0)
        except (TypeError, ValueError):
            continue
        if first_pin is None:
            first_pin = pin
        st["assistance_lbs"] = pin
        st["effective_load_source"] = source
        if source == "nearest_weighin" and bodyweight is not None:
            eff = effective_load_lbs(bodyweight, pin)
            st["effective_load_lbs"] = eff
            st["weighin_days_away"] = int(days_away or 0)
            total += eff * count * reps
        else:
            st.pop("effective_load_lbs", None)
            st.pop("weighin_days_away", None)
    ex["effective_load_source"] = source
    if first_pin is not None:
        ex["assistance_lbs"] = first_pin
    if source == "nearest_weighin" and bodyweight is not None and first_pin is not None:
        ex["effective_load_lbs"] = effective_load_lbs(bodyweight, first_pin)
        ex["weighin_days_away"] = int(days_away or 0)
        ex["volume"] = total
    else:
        ex.pop("effective_load_lbs", None)
        ex.pop("weighin_days_away", None)
        ex["volume"] = 0.0


def _refresh_dict_volume(session: dict) -> None:
    total = 0.0
    for ex in session.get("exercises") or []:
        if not isinstance(ex, dict):
            continue
        if ex.get("volume") is not None:
            try:
                total += float(ex["volume"])
                continue
            except (TypeError, ValueError):
                pass
        for st in ex.get("sets") or []:
            if not isinstance(st, dict):
                continue
            try:
                total += (
                    float(st.get("weight_lbs") or 0)
                    * int(st.get("sets") or 0)
                    * int(st.get("reps") or 0)
                )
            except (TypeError, ValueError):
                continue
    session["volume"] = total


def apply_assisted_effective_loads(
    sessions: Optional[Sequence[Any]],
    weighins: Optional[Sequence[Any]],
    catalog: Optional[dict] = None,
) -> None:
    """Overlay effective load on assisted logs. ``weight_lbs`` stays the pin.

    Idempotent. Dict rows already carrying ``effective_load_source`` are left
    as they are. Object rows are re-marked from the pin, so a second call
    does not subtract twice.
    """
    book = catalog if isinstance(catalog, dict) else _default_catalog()
    for session in sessions or []:
        day = _session_day(session)
        match = nearest_weighin(day, weighins)
        if isinstance(session, dict):
            for ex in _session_exercises(session):
                if not isinstance(ex, dict):
                    continue
                if not _is_assisted_name(_exercise_name(ex), book):
                    continue
                if ex.get("effective_load_source"):
                    continue
                if match is None:
                    _apply_dict_exercise(ex, source="none", bodyweight=None, days_away=None)
                else:
                    bodyweight, days_away, _when = match
                    _apply_dict_exercise(
                        ex,
                        source="nearest_weighin",
                        bodyweight=bodyweight,
                        days_away=days_away,
                    )
            if any(
                isinstance(ex, dict) and ex.get("effective_load_source")
                for ex in _session_exercises(session)
            ):
                _refresh_dict_volume(session)
            continue
        for ex in _session_exercises(session):
            if not isinstance(ex, ExerciseEntry):
                continue
            if not _is_assisted_name(ex.name, book):
                continue
            if match is None:
                _mark_exercise(ex, source="none", bodyweight=None, days_away=None)
            else:
                bodyweight, days_away, _when = match
                _mark_exercise(
                    ex,
                    source="nearest_weighin",
                    bodyweight=bodyweight,
                    days_away=days_away,
                )
