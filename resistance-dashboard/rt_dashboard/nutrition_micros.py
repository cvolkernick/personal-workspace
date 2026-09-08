"""Surface fiber / sodium / sugar from existing nutrients{} only.

Google Health Nutrient enum (nutrition-log, already fetched) includes
DIETARY_FIBER, SODIUM, SUGAR — quantity is grams. No new GH type fetch.

Never invent values. Absent keys stay absent. Logged zero is a real value.
Sodium display is milligrams; values ≥20 look like mg-scale (Fitbit mix)
and are converted to grams first (same heuristic as coach commentary).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Union

# Official GH Nutrient enum first; aliases are defensive only.
FIBER_KEYS = ("DIETARY_FIBER", "FIBER", "TOTAL_DIETARY_FIBER", "TOTAL_FIBER")
SODIUM_KEYS = ("SODIUM",)
# TOTAL_SUGARS is an alias for sugar. ADDED_SUGARS is a different quantity — skip.
SUGAR_KEYS = ("SUGAR", "SUGARS", "TOTAL_SUGAR", "TOTAL_SUGARS")

NutrientsIn = Union[Mapping[Any, Any], Iterable[Any], None]


def coerce_nutrients_map(raw: NutrientsIn) -> Dict[str, float]:
    """GH list or dict → uppercase enum → grams. Empty if absent/unparseable."""
    out: Dict[str, float] = {}
    if raw is None:
        return out
    if isinstance(raw, Mapping):
        for k, v in raw.items():
            grams = _quantity_grams(v)
            if grams is None:
                continue
            key = str(k).upper().strip()
            if key:
                out[key] = grams
        return out
    if isinstance(raw, (str, bytes)):
        return out
    try:
        items = list(raw)
    except TypeError:
        return out
    for item in items:
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("nutrient") or "").upper().strip()
        if not key or key == "NUTRIENT_UNSPECIFIED":
            continue
        grams = _quantity_grams(item.get("quantity"))
        if grams is None:
            continue
        out[key] = grams
    return out


def _quantity_grams(obj: Any) -> Optional[float]:
    if obj is None or obj == "":
        return None
    if isinstance(obj, bool):
        return None
    if isinstance(obj, (int, float)):
        return float(obj)
    if isinstance(obj, Mapping):
        for k in ("grams", "gramsSum", "value", "sum"):
            if obj.get(k) is None:
                continue
            try:
                return float(obj[k])
            except (TypeError, ValueError):
                continue
    return None


def _first_present(upper: Mapping[str, float], keys: Iterable[str]) -> Optional[float]:
    for k in keys:
        if k in upper:
            return float(upper[k])
    return None


def _sodium_grams(raw: float) -> float:
    """Canonical grams. Values ≥20 look like milligrams (Fitbit/GH mix)."""
    return raw / 1000.0 if raw >= 20 else raw


def micros_from_nutrients(nutrients: NutrientsIn) -> Dict[str, float]:
    """Return fiber_g / sodium_g / sodium_mg / sugar_g only for present keys."""
    upper = coerce_nutrients_map(nutrients)
    out: Dict[str, float] = {}
    fiber = _first_present(upper, FIBER_KEYS)
    sodium = _first_present(upper, SODIUM_KEYS)
    sugar = _first_present(upper, SUGAR_KEYS)
    if fiber is not None:
        out["fiber_g"] = round(fiber, 2)
    if sodium is not None:
        grams = _sodium_grams(float(sodium))
        out["sodium_g"] = round(grams, 4)
        out["sodium_mg"] = round(grams * 1000.0, 0)
    if sugar is not None:
        out["sugar_g"] = round(sugar, 2)
    return out


def sum_micros(nutrient_maps: Iterable[NutrientsIn]) -> Dict[str, float]:
    """Sum present micros across meals. A missing key on one meal is not 0."""
    sums: Dict[str, float] = {}
    for raw in nutrient_maps:
        m = micros_from_nutrients(raw)
        for k, v in m.items():
            if k == "sodium_mg":
                continue
            sums[k] = sums.get(k, 0.0) + float(v)
    out: Dict[str, float] = {}
    if "fiber_g" in sums:
        out["fiber_g"] = round(sums["fiber_g"], 2)
    if "sodium_g" in sums:
        out["sodium_g"] = round(sums["sodium_g"], 4)
        out["sodium_mg"] = round(sums["sodium_g"] * 1000.0, 0)
    if "sugar_g" in sums:
        out["sugar_g"] = round(sums["sugar_g"], 2)
    return out


def merge_day_micros(
    day_nutrients: NutrientsIn,
    log_nutrient_maps: Iterable[NutrientsIn],
) -> Dict[str, float]:
    """Day/rollup nutrients win per key; meal logs fill keys the day payload lacks."""
    from_logs = sum_micros(log_nutrient_maps)
    from_day = micros_from_nutrients(day_nutrients)
    if not from_logs and not from_day:
        return {}
    merged = dict(from_logs)
    merged.update(from_day)
    if "sodium_g" in merged:
        merged["sodium_mg"] = round(float(merged["sodium_g"]) * 1000.0, 0)
    return merged
