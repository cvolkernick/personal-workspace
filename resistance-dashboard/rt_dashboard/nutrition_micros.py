"""Surface fiber / sodium / sugar from existing nutrients{} only.

Google Health Nutrient enum (nutrition-log, already fetched) includes
DIETARY_FIBER, SODIUM, SUGAR — quantity is grams. No new GH type fetch.

Never invent values. Absent keys stay absent. Logged zero is a real value.
Sodium display is milligrams; values ≥20 look like mg-scale (Fitbit mix)
and are converted to grams first (same heuristic as coach commentary).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

# Official GH Nutrient enum first; aliases are defensive only.
FIBER_KEYS = ("DIETARY_FIBER", "FIBER", "TOTAL_DIETARY_FIBER", "TOTAL_FIBER")
SODIUM_KEYS = ("SODIUM",)
# TOTAL_SUGARS is an alias for sugar. ADDED_SUGARS is a different quantity — skip.
SUGAR_KEYS = ("SUGAR", "SUGARS", "TOTAL_SUGAR", "TOTAL_SUGARS")
# Google Health nutrient enum plus defensive aliases. Not a target.
SAT_FAT_KEYS = ("SATURATED_FAT", "FAT_SATURATED", "SATURATEDFAT", "SAT_FAT")
SAT_FAT_GUIDE_PCT = 10.0
SAT_FAT_KCAL_PER_G = 9.0
SAT_FAT_DAY_COVERAGE = 0.5

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
    sat = _first_present(upper, SAT_FAT_KEYS)
    if sat is not None:
        out["sat_fat_g"] = round(sat, 2)
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
    if "sat_fat_g" in sums:
        out["sat_fat_g"] = round(sums["sat_fat_g"], 2)
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


def sat_fat_chip_enabled(rules: Optional[dict] = None) -> bool:
    """Display flag. Defaults on. Turning it off hides the chip."""
    if rules is None:
        try:
            from .coach_steps import load_rules

            rules = load_rules()
        except Exception:
            return True
    blob = rules.get("sat_fat_chip") if isinstance(rules, dict) else None
    if not isinstance(blob, dict) or "enabled" not in blob:
        return True
    return blob.get("enabled") is True


def _entry_get(entry: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(entry, Mapping) and key in entry:
            return entry.get(key)
        if hasattr(entry, key):
            return getattr(entry, key)
    return None


def _entry_day(entry: Any) -> str:
    return str(_entry_get(entry, "date") or "")[:10]


def _entry_kcal(entry: Any) -> Optional[float]:
    raw = _entry_get(entry, "calories", "kcal")
    try:
        kcal = float(raw)
    except (TypeError, ValueError):
        return None
    if kcal != kcal or kcal < 0:
        return None
    return kcal


def _entry_sat_fat_g(entry: Any) -> Optional[float]:
    nutrients = _entry_get(entry, "nutrients")
    found = micros_from_nutrients(nutrients).get("sat_fat_g")
    if found is None:
        return None
    return float(found)


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def sat_fat_day(entries: Sequence[Any], day: str) -> Dict[str, Any]:
    """One civil day. Percent uses only entries that carry sat fat."""
    rows = [entry for entry in entries or [] if _entry_day(entry) == str(day)[:10]]
    total_kcal = 0.0
    covered_kcal = 0.0
    sat_g = 0.0
    with_data = 0
    for entry in rows:
        kcal = _entry_kcal(entry)
        if kcal:
            total_kcal += kcal
        sat = _entry_sat_fat_g(entry)
        if sat is None:
            continue
        with_data += 1
        sat_g += sat
        if kcal:
            covered_kcal += kcal
    qualified = total_kcal > 0 and (covered_kcal / total_kcal) >= SAT_FAT_DAY_COVERAGE
    pct = None
    if qualified and covered_kcal > 0:
        pct = sat_g * SAT_FAT_KCAL_PER_G / covered_kcal * 100.0
    return {
        "date": str(day)[:10],
        "entries": len(rows),
        "with_entries": with_data,
        "total_kcal": round(total_kcal, 2),
        "covered_kcal": round(covered_kcal, 2),
        "sat_fat_g": round(sat_g, 2) if with_data else None,
        "pct": None if pct is None else round(pct, 2),
        "qualified": qualified,
        "no_data": not qualified,
        "under_guide": bool(qualified and pct is not None and pct <= SAT_FAT_GUIDE_PCT),
    }


def build_sat_fat_chip(
    entries: Sequence[Any],
    *,
    as_of: Any,
    enabled: Optional[bool] = None,
    rules: Optional[dict] = None,
) -> Optional[dict]:
    """Display-only chip. None hides it. Does not change targets."""
    if enabled is None:
        enabled = sat_fat_chip_enabled(rules)
    if not enabled:
        return None
    end = _as_date(as_of)
    if end is None:
        return None
    window: List[date] = [end - timedelta(days=offset) for offset in range(6, -1, -1)]
    days = [sat_fat_day(entries, day.isoformat()) for day in window]
    if not any(day["with_entries"] for day in days):
        return None
    under = sum(1 for day in days if day["under_guide"])
    today = days[-1]
    coverage = f"from {today['with_entries']}/{today['entries']} entries"
    week = f"{under}/7"
    if not today["qualified"] or today["pct"] is None or today["sat_fat_g"] is None:
        text = f"Sat fat · no data · {coverage} · {week}"
    else:
        grams = int(round(today["sat_fat_g"]))
        pct = int(round(today["pct"]))
        text = (
            f"Sat fat {grams} g ({pct}% of kcal) · guide ≤ 10% · {coverage} · {week}"
        )
    return {
        "text": text,
        "sat_fat_g": today["sat_fat_g"],
        "pct_of_covered_kcal": today["pct"],
        "covered_kcal": today["covered_kcal"],
        "entries_with_data": today["with_entries"],
        "entries": today["entries"],
        "days_under_guide": under,
        "days": 7,
        "guide_pct": SAT_FAT_GUIDE_PCT,
        "no_data": bool(today["no_data"]),
        "enabled": True,
    }
