"""Ingredient inventory + remaining-day meal plan generation."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from .models import FoodLogEntry, NutritionDay
from .nutrition_micros import merge_day_micros, micros_from_nutrients
from .restock_venues import venue_for_item

INVENTORY_PATH = "fitness/nutrition/inventory.json"
TARGETS_PATH = "fitness/nutrition/targets.json"

DEFAULT_TARGETS = {
    "calories": 2100,
    "protein_g": 210,
    "carbs_g": 180,
    "fat_g": 55,
    # Optional scale goal for Trends weight chart guide line (lb). None = unset.
    "weight_goal_lbs": None,
    # Optional nutrition phase: cut | maintain | slow_bulk. None = infer.
    "phase": None,
    "notes": "Default cutting targets",
    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
}

# FitDash Today meal clocks (issue #250). Defaults sit inside the eating window.
MEAL_TZ_NAME = "America/New_York"
DEFAULT_SLOT_HM = ((12, 0), (15, 30), (19, 0))
FOURTH_SLOT_HM = (21, 0)
UPCOMING_MEAL_LABELS = ("Next meal", "Later meal", "Evening", "Optional snack")
PAST_MEAL_LABEL = "Earlier meal"
# Keep a slot you're currently eating; drop anything older (#613).
SLOT_GRACE = timedelta(minutes=20)
# Late-day regen must not stack leftover hinges on top of each other.
MIN_MEAL_GAP = timedelta(minutes=75)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or f"item-{uuid.uuid4().hex[:8]}"


def load_json_file(path: Path, default: dict) -> dict:
    if not path.is_file():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deepcopy(default)


def save_json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def default_inventory() -> dict:
    inv_path = Path(__file__).resolve().parents[2] / "fitness" / "nutrition" / "inventory.json"
    if inv_path.is_file():
        return load_json_file(inv_path, {"ingredients": [], "updated_at": ""})
    return {"ingredients": [], "updated_at": "", "notes": ""}


def normalize_targets(raw: Optional[dict]) -> dict:
    t = deepcopy(DEFAULT_TARGETS)
    if raw:
        for k in ("calories", "protein_g", "carbs_g", "fat_g"):
            if k in raw and raw[k] is not None:
                t[k] = float(raw[k])
        fiber = _coerce_optional_micro(raw.get("fiber_g"), lo=0.0, hi=100.0)
        if "fiber_g" in raw:
            if fiber is None:
                t.pop("fiber_g", None)
            else:
                t["fiber_g"] = fiber
        sugar = _coerce_optional_micro(raw.get("sugar_g"), lo=0.0, hi=300.0)
        if "sugar_g" in raw:
            if sugar is None:
                t.pop("sugar_g", None)
            else:
                t["sugar_g"] = sugar
        sodium = _coerce_optional_micro(raw.get("sodium_mg"), lo=0.0, hi=10000.0)
        if "sodium_mg" in raw:
            if sodium is None:
                t.pop("sodium_mg", None)
            else:
                t["sodium_mg"] = sodium
        if "weight_goal_lbs" in raw:
            t["weight_goal_lbs"] = _coerce_weight_goal_lbs(raw.get("weight_goal_lbs"))
        if "phase" in raw:
            t["phase"] = _coerce_phase(raw.get("phase"))
        if raw.get("notes"):
            t["notes"] = str(raw["notes"])
        if raw.get("updated_at"):
            t["updated_at"] = str(raw["updated_at"])
    # Heal obvious corruption: calorie target looks like a gram value (e.g. fat 45
    # was also written into calories). Recompute from macros when plausible.
    p, c, f = float(t.get("protein_g") or 0), float(t.get("carbs_g") or 0), float(t.get("fat_g") or 0)
    macro_kcal = p * 4 + c * 4 + f * 9
    cal = float(t.get("calories") or 0)
    if cal < 800 and macro_kcal >= 800:
        t["calories"] = round(macro_kcal)
    elif cal < 800:
        t["calories"] = float(DEFAULT_TARGETS["calories"])
    # Clamp absurd ranges rather than displaying nonsense chips
    t["calories"] = max(800.0, min(6000.0, float(t["calories"])))
    t["protein_g"] = max(0.0, min(500.0, float(t["protein_g"])))
    t["carbs_g"] = max(0.0, min(800.0, float(t["carbs_g"])))
    t["fat_g"] = max(0.0, min(300.0, float(t["fat_g"])))
    if t.get("fiber_g") is not None:
        t["fiber_g"] = max(0.0, min(100.0, float(t["fiber_g"])))
    if t.get("sugar_g") is not None:
        t["sugar_g"] = max(0.0, min(300.0, float(t["sugar_g"])))
    if t.get("sodium_mg") is not None:
        t["sodium_mg"] = max(0.0, min(10000.0, float(t["sodium_mg"])))
    if "weight_goal_lbs" not in t:
        t["weight_goal_lbs"] = None
    if "phase" not in t:
        t["phase"] = None
    return t


def _coerce_optional_micro(raw: Any, *, lo: float, hi: float) -> Optional[float]:
    """Optional fiber/sugar/sodium — None if unset/invalid. Never invent."""
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return round(max(lo, min(hi, v)), 1)


def _coerce_phase(raw: Any) -> Optional[str]:
    if raw is None or raw == "":
        return None
    v = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    if v in ("bulk", "gain"):
        v = "slow_bulk"
    if v in ("cut", "maintain", "slow_bulk"):
        return v
    return None


def _coerce_weight_goal_lbs(raw: Any) -> Optional[float]:
    """Body-weight goal in pounds, or None if unset/invalid."""
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    # Athlete scale band — reject nonsense so the chart never draws 5 lb or 900 lb
    return round(max(80.0, min(500.0, v)), 1)


def _coerce_serving_g(raw: dict) -> Optional[float]:
    """Grams of edible food for one inventory serving (macros apply to this mass).

    Prefer explicit ``serving_g``. Fallback: parse ``N g`` or ``N oz`` from
    ``serving_label`` so scale users get a weighable amount even on legacy rows.
    Cups/eggs/medium are *not* guessed (density varies).
    """
    if not isinstance(raw, dict):
        return None
    if raw.get("serving_g") is not None and str(raw.get("serving_g")).strip() != "":
        try:
            v = float(raw["serving_g"])
            return round(v, 1) if v > 0 else None
        except (TypeError, ValueError):
            pass
    label = str(raw.get("serving_label") or "")
    m = re.search(r"([\d.]+)\s*g\b", label, re.I)
    if m:
        try:
            v = float(m.group(1))
            return round(v, 1) if v > 0 else None
        except ValueError:
            return None
    m = re.search(r"([\d.]+)\s*oz\b", label, re.I)
    if m:
        try:
            v = float(m.group(1)) * 28.3495
            return round(v) if v > 0 else None
        except ValueError:
            return None
    return None


_MACRO_KEYS = ("calories", "protein_g", "carbs_g", "fat_g")
SERVING_GRAMS_REQUIRED_MSG = (
    "serving grams required — logged serving has no weighable mass"
)


def _has_macros(raw: dict) -> bool:
    if not isinstance(raw, dict):
        return False
    for k in _MACRO_KEYS:
        try:
            if float(raw.get(k) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def needs_serving_grams(raw: dict) -> bool:
    """True when macros exist but serving mass is unknown / unparseable.

    Does not invent grams. Cups/eggs/medium stay unknown until the user enters mass.
    """
    if not isinstance(raw, dict):
        return False
    if not _has_macros(raw):
        return False
    return _coerce_serving_g(raw) is None


def serving_grams_required(raw: dict) -> bool:
    """Block save only for Health “logged serving” rows with no weighable mass.

    ``portion_g`` / ``serving_g`` is preferred, not required (#503). Whole-food
    serving labels (1 serving, 1 medium, 3 eggs) must save without grams. The
    meal planner uses whole servings when mass is unknown and continuous
    ``portion_g`` when ``serving_g`` is present — it never invents grams.
    """
    if not needs_serving_grams(raw):
        return False
    label = str(raw.get("serving_label") or "").strip()
    return bool(re.search(r"logged\s+serving", label, re.I))


def require_serving_grams_or_raise(raw: dict) -> None:
    if serving_grams_required(raw):
        raise ValueError(SERVING_GRAMS_REQUIRED_MSG)


def serving_grams_nudge_text(items: Optional[Sequence[dict]]) -> str:
    """Honest FitDash note when plan foods have macros but no weighable mass."""
    missing: List[str] = []
    seen = set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if not needs_serving_grams(it):
            continue
        name = str(it.get("name") or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        missing.append(name)
    if not missing:
        return ""
    shown = missing[:3]
    extra = f" (+{len(missing) - 3} more)" if len(missing) > 3 else ""
    return f"Set serving grams on {', '.join(shown)}{extra} to get weighable portions."


# Continuous meal portions (issue #267). Not locked to whole inventory servings.
MIN_PORTION_G = 25.0
PORTION_STEP_G = 5.0
MAX_PORTION_G = 1200.0


def format_portion_label(
    serving_g: Optional[float] = None,
    servings: float = 1.0,
    serving_label: str = "",
) -> str:
    """Human portion for meal plan / inventory: prefer grams for food scale."""
    n = float(servings or 1)
    if serving_g is not None and float(serving_g) > 0:
        total_g = round(float(serving_g) * n)
        if total_g <= 0:
            total_g = 1
        return f"{total_g}g"
    label = (serving_label or "1 serving").strip() or "1 serving"
    if abs(n - 1.0) < 1e-9:
        return label
    if n == int(n):
        return f"{int(n)} × {label}"
    return f"{n:g} × {label}"


def format_plan_portion(it: Optional[dict]) -> str:
    """Primary portion cue for plan / UI / quests: grams when known."""
    if not isinstance(it, dict):
        return "1 serving"
    pg = it.get("portion_g")
    if pg is not None and str(pg).strip() != "":
        try:
            g = float(pg)
            if g > 0:
                return f"{int(round(g))}g"
        except (TypeError, ValueError):
            pass
    sg = it.get("serving_g")
    try:
        n = float(it.get("servings") or 1)
    except (TypeError, ValueError):
        n = 1.0
    if sg is not None and str(sg).strip() != "":
        try:
            base = float(sg)
            if base > 0:
                g = base * n
                if g > 0:
                    return f"{int(round(g))}g"
        except (TypeError, ValueError):
            pass
    return format_portion_label(
        serving_g=None,
        servings=n,
        serving_label=str(it.get("serving_label") or "1 serving"),
    )


def _ingredient_serving_g(ing: dict) -> Optional[float]:
    raw = ing.get("serving_g") if isinstance(ing, dict) else None
    if raw is not None and str(raw).strip() != "":
        try:
            v = float(raw)
            return v if v > 0 else None
        except (TypeError, ValueError):
            pass
    return _coerce_serving_g(ing) if isinstance(ing, dict) else None


def _round_portion_g(grams: float, serving_g: Optional[float] = None) -> float:
    """Nourish AC: prefer ~5g steps, min ~25g.

    Tiny inventory servings (oil, etc. ``serving_g`` < 25) keep a smaller min
    so we do not invent a 25g pour when the default serving is 14g.
    """
    try:
        g = float(grams)
    except (TypeError, ValueError):
        return 0.0
    if g <= 0:
        return 0.0
    step = PORTION_STEP_G
    min_g = MIN_PORTION_G
    if serving_g is not None and 0 < float(serving_g) < MIN_PORTION_G:
        min_g = max(1.0, float(serving_g))
        step = 1.0 if float(serving_g) < 10 else PORTION_STEP_G
    rounded = round(g / step) * step
    if rounded < min_g:
        return float(min_g) if g >= min_g * 0.5 else 0.0
    if abs(rounded - round(rounded)) < 1e-9:
        return float(int(round(rounded)))
    return float(rounded)


def _macros_for_portion(ing: dict, *, servings: float = 1.0, portion_g: Optional[float] = None) -> dict:
    """Scale per-serving macros. ``portion_g / serving_g`` when mass is known."""
    base_g = _ingredient_serving_g(ing)
    if portion_g is not None and base_g is not None and float(base_g) > 0:
        n = float(portion_g) / float(base_g)
    else:
        n = float(servings or 1)
    return {k: round(float(ing.get(k) or 0) * n, 1) for k in _MACRO_KEYS}


def _pick_continuous_portion(
    ing: dict,
    rem: dict,
    cal_ceiling: float,
    totals: dict,
    *,
    max_servings: Optional[float] = None,
) -> Optional[tuple]:
    """Choose a continuous (servings, portion_g|None) that fills remaining macros.

    When ``serving_g`` is known, portion is not locked to 1.0 serving steps.
    When mass is unknown, keep a whole free-text serving (never invent grams).
    ``max_servings`` caps one pick so veg/fruit cannot dump ``MAX_PORTION_G``
    of a single produce item and crowd out pantry alternatives (#513).
    """
    sg = _ingredient_serving_g(ing)
    cal = float(ing.get("calories") or 0)
    prot = float(ing.get("protein_g") or 0)
    cal_room = max(0.0, float(cal_ceiling) - float(totals.get("calories") or 0))

    if sg is not None and float(sg) > 0:
        cal_pg = cal / float(sg)
        prot_pg = prot / float(sg)
        g_from_cal = (cal_room / cal_pg) if cal_pg > 0 else 1e12
        g_from_prot = (float(rem.get("protein_g") or 0) / prot_pg) if prot_pg > 0 else 1e12
        rem_p = float(rem.get("protein_g") or 0)
        # Protein foods fill leftover protein (including partial servings).
        # Once protein is done, fill leftover calories without dumping the
        # rest of the day onto one staple (leave room for other stocked foods).
        if rem_p >= 5 and prot_pg > 0:
            target_g = min(g_from_prot, g_from_cal)
        else:
            target_g = min(g_from_cal, float(sg) * 3.0)
        max_g = min(MAX_PORTION_G, float(sg) * 8.0)
        if max_servings is not None and float(max_servings) > 0:
            max_g = min(max_g, float(sg) * float(max_servings))
        target_g = min(max(0.0, target_g), max_g)
        portion = _round_portion_g(target_g, serving_g=sg)
        if portion <= 0:
            return None
        macros = _macros_for_portion(ing, portion_g=portion)
        # Soft ceiling: a min bite that still blows calories with protein done → skip.
        if (
            float(totals.get("calories") or 0) + macros["calories"] > cal_ceiling + 40
            and rem_p < 12
        ):
            return None
        return (portion / float(sg), portion)

    # No usable mass: one free-text serving if it still fits.
    if cal > 0 and float(totals.get("calories") or 0) + cal > cal_ceiling + 40:
        if float(rem.get("protein_g") or 0) < 20:
            return None
    if cal > rem.get("calories", 0) + 120 and rem.get("protein_g", 0) < 20:
        if float(totals.get("calories") or 0) > 0:
            return None
    return (1.0, None)


STOCK_IN = "in"
STOCK_LOW = "low"
STOCK_OUT = "out"
VALID_STOCK = frozenset({STOCK_IN, STOCK_LOW, STOCK_OUT})


def normalize_stock(raw: Any) -> str:
    """Resolve pantry stock to in|low|out.

    Compat: missing/`true` → in; `false`/`0` → out. Explicit ``stock`` wins
    when it is one of in|low|out. Derived ``in_stock = (stock != out)``.
    """
    if not isinstance(raw, dict):
        if raw is False or raw == 0:
            return STOCK_OUT
        if isinstance(raw, str):
            t = raw.strip().lower()
            if t in VALID_STOCK:
                return t
            if t in ("0", "false", "no", "off"):
                return STOCK_OUT
        return STOCK_IN
    if "stock" in raw:
        s = str(raw.get("stock") or "").strip().lower()
        if s in VALID_STOCK:
            return s
    if "in_stock" not in raw:
        return STOCK_IN
    v = raw.get("in_stock")
    if isinstance(v, str):
        t = v.strip().lower()
        if t in VALID_STOCK:
            return t
        if t in ("1", "true", "yes", "on"):
            return STOCK_IN
        if t in ("0", "false", "no", "off"):
            return STOCK_OUT
        return STOCK_IN
    if v is False or v == 0:
        return STOCK_OUT
    return STOCK_IN


def is_in_stock(raw: dict) -> bool:
    """True when the item can be used in meals (in or low). Out is excluded."""
    if not isinstance(raw, dict):
        return False
    return normalize_stock(raw) != STOCK_OUT


def needs_restock(raw: dict) -> bool:
    """True when Restock/Get shopping should include this item (low or out)."""
    if not isinstance(raw, dict):
        return False
    return normalize_stock(raw) in (STOCK_LOW, STOCK_OUT)


def stock_from_write_payload(payload: Optional[dict]) -> str:
    """POST /api/inventory/stock body → in|low|out.

    ``stock`` wins when present and non-empty. Bool ``in_stock`` maps in/out
    for the agent token path. Missing both → in.
    """
    body = payload if isinstance(payload, dict) else {}
    if "stock" in body and body.get("stock") is not None and str(body.get("stock")).strip() != "":
        s = str(body.get("stock")).strip().lower()
        if s not in VALID_STOCK:
            raise ValueError("stock must be in, low, or out")
        return s
    v = body.get("in_stock", True)
    if isinstance(v, str):
        t = v.strip().lower()
        if t in VALID_STOCK:
            return t
        if t in ("0", "false", "no", "off"):
            return STOCK_OUT
        return STOCK_IN
    return STOCK_IN if bool(v) else STOCK_OUT


def normalize_ingredient(raw: dict) -> dict:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("ingredient name required")
    iid = str(raw.get("id") or _slug(name)).strip()
    serving_g = _coerce_serving_g(raw)
    raw_label = str(raw.get("serving_label") or "").strip()
    # Bias labels toward grams when we know mass; keep free-text only as fallback.
    if serving_g is not None:
        g_txt = f"{int(round(serving_g))}g"
        if not raw_label or raw_label.lower() in ("1 serving", "serving"):
            serving_label = g_txt
        elif re.search(r"\d+\s*g\b", raw_label, re.I):
            # Already gram-first (may include prep note)
            serving_label = raw_label
        elif re.search(r"\d", raw_label):
            # e.g. "6 oz cooked" / "1.5 cups" → "170g cooked" (scale-first)
            note = re.sub(
                r"^[\d./\s½¼¾]+",
                "",
                raw_label,
            ).strip()
            note = re.sub(
                r"^(?:oz|ounce|ounces|cup|cups|tbsp|tsp|scoop|scoops|medium|large|small|can|eggs?)\b\s*",
                "",
                note,
                flags=re.I,
            ).strip(" ·-,")
            serving_label = g_txt + (f" {note}" if note else "")
        else:
            # Prep-only note ("cooked", "dry") — prefix grams
            serving_label = f"{g_txt} {raw_label}".strip()
    else:
        serving_label = raw_label or "1 serving"
    out = {
        "id": iid,
        "name": name,
        "category": str(raw.get("category") or "other").strip() or "other",
        "serving_label": serving_label,
        "calories": float(raw.get("calories") or 0),
        "protein_g": float(raw.get("protein_g") or 0),
        "carbs_g": float(raw.get("carbs_g") or 0),
        "fat_g": float(raw.get("fat_g") or 0),
        "notes": str(raw.get("notes") or ""),
    }
    stock = normalize_stock(raw)
    out["stock"] = stock
    out["in_stock"] = stock != STOCK_OUT
    if serving_g is not None:
        out["serving_g"] = float(serving_g)
    if raw.get("fiber_g") is not None:
        try:
            out["fiber_g"] = max(0.0, float(raw.get("fiber_g") or 0))
        except (TypeError, ValueError):
            pass
    if raw.get("sugar_g") is not None:
        try:
            out["sugar_g"] = max(0.0, float(raw.get("sugar_g") or 0))
        except (TypeError, ValueError):
            pass
    if raw.get("sodium_mg") is not None:
        try:
            out["sodium_mg"] = max(0.0, float(raw.get("sodium_mg") or 0))
        except (TypeError, ValueError):
            pass
    elif raw.get("sodium_g") is not None:
        try:
            out["sodium_mg"] = max(0.0, float(raw.get("sodium_g") or 0) * 1000.0)
        except (TypeError, ValueError):
            pass
    return out


def stocked_ingredients(inventory: dict) -> List[dict]:
    """Return ingredients usable in meals (in or low; not out)."""
    return [
        normalize_ingredient(i)
        for i in inventory.get("ingredients") or []
        if is_in_stock(i)
    ]


def today_consumed_from_nutrition(
    nutrition: Sequence[NutritionDay],
    as_of: Optional[str] = None,
    food_logs: Optional[Sequence[FoodLogEntry]] = None,
) -> dict:
    """Macros for as_of from daily rollups, falling back to summed meal logs."""
    if as_of is None:
        from .timeutil import local_today_iso

        day = local_today_iso()
    else:
        day = as_of
    total = {
        "calories": 0.0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0,
        "date": day,
        "source": "none",
        "food_log_count": 0,
    }
    hit_day = False
    for n in nutrition:
        if n.date != day:
            continue
        hit_day = True
        if n.calories is not None:
            total["calories"] += float(n.calories)
        if n.protein_g is not None:
            total["protein_g"] += float(n.protein_g)
        if n.carbs_g is not None:
            total["carbs_g"] += float(n.carbs_g)
        if n.fat_g is not None:
            total["fat_g"] += float(n.fat_g)
    if hit_day and any(
        total[k] > 0 for k in ("calories", "protein_g", "carbs_g", "fat_g")
    ):
        total["source"] = "daily_rollup"
    elif food_logs:
        n_logs = 0
        for f in food_logs:
            if f.date != day:
                continue
            n_logs += 1
            if f.calories is not None:
                total["calories"] += float(f.calories)
            if f.protein_g is not None:
                total["protein_g"] += float(f.protein_g)
            if f.carbs_g is not None:
                total["carbs_g"] += float(f.carbs_g)
            if f.fat_g is not None:
                total["fat_g"] += float(f.fat_g)
        if n_logs:
            total["source"] = "food_logs"
            total["food_log_count"] = n_logs
    if food_logs and total.get("source") == "daily_rollup":
        total["food_log_count"] = sum(1 for f in food_logs if f.date == day)
    for k in ("calories", "protein_g", "carbs_g", "fat_g"):
        total[k] = round(total[k], 1)
    day_nutrients: Dict[str, float] = {}
    for n in nutrition:
        if n.date != day:
            continue
        day_nutrients = dict(getattr(n, "nutrients", None) or {})
        break
    log_maps = []
    if food_logs:
        for f in food_logs:
            if f.date != day:
                continue
            log_maps.append(getattr(f, "nutrients", None) or {})
    micros = merge_day_micros(day_nutrients, log_maps)
    if day_nutrients:
        total["nutrients"] = dict(day_nutrients)
    if micros:
        total["micros"] = micros
    return total


def food_logs_for_day(
    food_logs: Sequence[FoodLogEntry], as_of: Optional[str] = None
) -> List[dict]:
    """Serialize meal-level entries for a single civil day (UI / plan)."""
    if as_of is None:
        from .timeutil import local_today_iso

        day = local_today_iso()
    else:
        day = as_of
    out: List[dict] = []
    for f in food_logs or []:
        if f.date != day:
            continue
        row = f.to_dict() if hasattr(f, "to_dict") else dict(f)  # type: ignore[arg-type]
        micros = micros_from_nutrients(row.get("nutrients") if isinstance(row, dict) else None)
        if micros:
            row["micros"] = micros
        out.append(row)
    return out


def _food_log_as_fp_row(log: Any) -> Optional[tuple]:
    if log is None:
        return None
    if isinstance(log, dict):
        d = log
    elif hasattr(log, "to_dict"):
        d = log.to_dict()
    else:
        d = None
    if not isinstance(d, dict):
        return None
    name = str(d.get("name") or "").strip().lower()
    if not name and d.get("calories") is None and d.get("protein_g") is None:
        return None

    def _num(value: Any) -> str:
        try:
            return f"{float(value):.1f}"
        except (TypeError, ValueError):
            return ""

    return (
        str(d.get("date") or "")[:10],
        name,
        str(d.get("time") or ""),
        _num(d.get("calories")),
        _num(d.get("protein_g")),
        _num(d.get("carbs_g")),
        _num(d.get("fat_g")),
    )


def food_logs_fingerprint(
    logs: Optional[Sequence[Any]] = None,
    *,
    consumed: Optional[dict] = None,
    day: str = "",
) -> str:
    """Stable hash of civil-day / wake-window food logs that force meal regen.

    Identity is the logged lines (name/time/macros) plus consumed totals when
    present. Empty logs still hash — morning plan vs first log is a real change.
    Does not invent food.
    """
    rows = []
    for log in logs or []:
        row = _food_log_as_fp_row(log)
        if row:
            rows.append(row)
    rows.sort()
    cons = consumed if isinstance(consumed, dict) else {}

    def _cons(key: str) -> str:
        try:
            return f"{float(cons.get(key)):.1f}"
        except (TypeError, ValueError):
            return ""

    payload = {
        "day": str(day or "")[:10],
        "logs": rows,
        "consumed": (
            _cons("calories"),
            _cons("protein_g"),
            _cons("carbs_g"),
            _cons("fat_g"),
            int(cons.get("food_log_count") or len(rows)),
        ),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def remaining_macros(targets: dict, consumed: dict) -> dict:
    rem = {}
    for k in ("calories", "protein_g", "carbs_g", "fat_g"):
        rem[k] = round(max(0.0, float(targets.get(k) or 0) - float(consumed.get(k) or 0)), 1)
    return rem


def _optional_micro_float(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    if n != n:
        return None
    return n


def resolve_micro_target(
    applied: Optional[dict],
    recommended: Optional[dict],
    key: str,
    *,
    fallback: Optional[float] = None,
) -> Optional[float]:
    """Applied first, then coach recommended, then optional fallback. Never invent 0."""
    for src in (applied, recommended):
        if not isinstance(src, dict):
            continue
        n = _optional_micro_float(src.get(key))
        if n is not None:
            return n
    return fallback


# Soft food-quality constraints (#501). Never invent items; pantry only.
SOFT_FIBER_TARGET_G = 25.0
SHAKE_MAX_SERVINGS = 2
SHAKE_MAX_POWDER_PROTEIN_G = 60.0

# #513: diversity is secondary under target closeness.
# Prefer an unused in-stock ingredient when leftover macros after that pick
# are not worse than the best-scoring (possibly repeating) pick by more than
# this ε. Band matches the existing +80 kcal ceiling buffer and an 8g protein
# slack so 210P is not traded for variety. Never invent off-pantry foods.
DIVERSITY_EPS = {
    "calories": 80.0,
    "protein_g": 8.0,
    "carbs_g": 15.0,
    "fat_g": 8.0,
}
DIVERSITY_EPS_KCAL = DIVERSITY_EPS["calories"]
DIVERSITY_EPS_PROTEIN_G = DIVERSITY_EPS["protein_g"]
MAX_DISTINCT_VEG_SLOTS = 2
VEG_SLOT_SERVINGS = 1.0
VEG_PICK_MAX_SERVINGS = 2.0


def _macros_from_pick(ing: dict, pick: tuple) -> dict:
    servings_n, portion_g = pick
    if portion_g is not None:
        return _macros_for_portion(ing, portion_g=portion_g)
    return _macros_for_portion(ing, servings=servings_n)


def remaining_after_macros(rem: dict, macros: dict) -> dict:
    """Leftover targets after adding ``macros`` (floored at 0)."""
    return {
        k: round(max(0.0, float(rem.get(k) or 0) - float(macros.get(k) or 0)), 1)
        for k in _MACRO_KEYS
    }


def within_diversity_eps(best_after: dict, alt_after: dict) -> bool:
    """True when ``alt`` leftover is not worse than ``best`` leftover beyond ε.

    Primary = target closeness: extra leftover calories/protein/carbs/fat
    vs the best pick must stay inside ``DIVERSITY_EPS``. Better closeness
    (less leftover) always qualifies.
    """
    for key, eps in DIVERSITY_EPS.items():
        delta = float(alt_after.get(key) or 0) - float(best_after.get(key) or 0)
        if delta > float(eps):
            return False
    return True


def _one_serving_pick(ing: dict) -> tuple:
    """One inventory serving (grams when known). Never invents mass."""
    sg = _ingredient_serving_g(ing)
    if sg is not None and float(sg) > 0:
        g = _round_portion_g(float(sg), serving_g=float(sg))
        if g <= 0:
            g = float(sg)
        return (g / float(sg), g)
    return (1.0, None)


def select_diverse_candidate(
    candidates: Sequence[tuple],
    rem: dict,
    pick_counts: Dict[str, int],
) -> Optional[tuple]:
    """Choose ``(score, ing, pick)``.

    Primary: highest target-closeness score. Secondary: if that pick repeats
    an already-used ingredient, take the best unused candidate whose leftover
    macros stay within ``DIVERSITY_EPS`` of the repeating pick. Thin pantry
    (no unused in-band) keeps the repeat — never invents food.
    """
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda x: -x[0])
    best = ranked[0]
    _sc, best_ing, best_pick = best
    iid = str(best_ing.get("id") or "")
    if pick_counts.get(iid, 0) == 0:
        return best
    best_after = remaining_after_macros(rem, _macros_from_pick(best_ing, best_pick))
    for row in ranked:
        _s, ing, pick = row
        if pick_counts.get(str(ing.get("id") or ""), 0) != 0:
            continue
        alt_after = remaining_after_macros(rem, _macros_from_pick(ing, pick))
        if within_diversity_eps(best_after, alt_after):
            return row
    return best


def _diversity_notes_fields(*, distinct: int, limited: bool) -> dict:
    return {
        "distinct_ingredients": int(distinct),
        "diversity_limited": bool(limited),
        "diversity_eps_kcal": DIVERSITY_EPS["calories"],
        "diversity_eps_protein_g": DIVERSITY_EPS["protein_g"],
        "diversity_eps_carbs_g": DIVERSITY_EPS["carbs_g"],
        "diversity_eps_fat_g": DIVERSITY_EPS["fat_g"],
        "diversity_primary": "target_closeness",
        "diversity_secondary": "distinct_ingredients",
    }

_SHAKE_NAME_HINTS = (
    "whey",
    "casein",
    "protein powder",
    "protein shake",
    "mass gainer",
    "isolate",
    "protein drink",
)
_SHAKE_EXCLUDE = (
    "yogurt",
    "cottage",
    "chicken",
    "turkey",
    "egg",
    "tuna",
    "beef",
    "fish",
    "tilapia",
    "salmon",
)
_VEG_FRUIT_CATS = frozenset({"veg", "vegetable", "fruit", "produce"})
_VEG_FRUIT_HINTS = (
    "broccoli",
    "spinach",
    "kale",
    "salad",
    "lettuce",
    "greens",
    "asparagus",
    "zucchini",
    "pepper",
    "tomato",
    "cucumber",
    "carrot",
    "berry",
    "berries",
    "apple",
    "banana",
    "orange",
    "fruit",
    "avocado",
    "green bean",
    "brussels",
    "cauliflower",
    "cabbage",
    "mushroom",
    "pea",
    "edamame",
    "vegetable",
)
_FIBER_HINTS = (
    ("chia", 10.0),
    ("flax", 8.0),
    ("black bean", 15.0),
    ("kidney bean", 13.0),
    ("lentil", 15.0),
    ("chickpea", 12.0),
    ("bean", 10.0),
    ("oat", 4.0),
    ("broccoli", 5.0),
    ("spinach", 2.0),
    ("kale", 3.0),
    ("berry", 4.0),
    ("avocado", 5.0),
    ("sweet potato", 4.0),
    ("apple", 4.0),
    ("banana", 3.0),
    ("brown rice", 3.5),
)


def _ing_blob(ing: dict) -> str:
    return (
        f"{ing.get('name') or ''} {ing.get('id') or ''} {ing.get('category') or ''}"
    ).lower()


def is_shake_or_powder(ing: dict) -> bool:
    """Protein powder / RTD shake — not whole-food protein."""
    blob = _ing_blob(ing)
    if any(tok in blob for tok in _SHAKE_EXCLUDE):
        return False
    if any(tok in blob for tok in _SHAKE_NAME_HINTS):
        return True
    if "shake" in blob and "protein" in blob:
        return True
    if "powder" in blob and "protein" in blob:
        return True
    return False


def is_veg_or_fruit(ing: dict) -> bool:
    cat = str(ing.get("category") or "").strip().lower()
    if cat in _VEG_FRUIT_CATS:
        return True
    blob = _ing_blob(ing)
    return any(tok in blob for tok in _VEG_FRUIT_HINTS)


def estimated_fiber_g(ing: dict) -> float:
    """Per-serving fiber for scoring. Explicit fiber_g wins; else name heuristic."""
    if ing.get("fiber_g") is not None:
        try:
            return max(0.0, float(ing.get("fiber_g") or 0))
        except (TypeError, ValueError):
            pass
    blob = _ing_blob(ing)
    for token, grams in _FIBER_HINTS:
        if token in blob:
            return float(grams)
    cat = str(ing.get("category") or "").strip().lower()
    if cat in _VEG_FRUIT_CATS:
        return 3.0
    return 0.0


def estimated_sugar_g(ing: dict) -> Optional[float]:
    """Per-serving sugar. Explicit only — never a name heuristic."""
    n = _optional_micro_float((ing or {}).get("sugar_g"))
    if n is None:
        return None
    return max(0.0, n)


def estimated_sodium_mg(ing: dict) -> Optional[float]:
    """Per-serving sodium mg. Explicit only — never a name heuristic."""
    n = _optional_micro_float((ing or {}).get("sodium_mg"))
    if n is None:
        g = _optional_micro_float((ing or {}).get("sodium_g"))
        if g is not None:
            n = g * 1000.0
    if n is None:
        return None
    return max(0.0, n)


def consumed_fiber_g(food_logs_today: Optional[Sequence[dict]] = None) -> float:
    """Sum logged dietary fiber when GH nutrients{} present; else 0 (unknown)."""
    return _consumed_micro_g(food_logs_today, "fiber_g")


def consumed_sugar_g(food_logs_today: Optional[Sequence[dict]] = None) -> float:
    """Sum logged total sugars (GH SUGAR); else 0 (unknown)."""
    return _consumed_micro_g(food_logs_today, "sugar_g")


def consumed_sodium_mg(food_logs_today: Optional[Sequence[dict]] = None) -> float:
    """Sum logged sodium mg (GH SODIUM, ≥20 treated as mg-scale); else 0."""
    return _consumed_micro_g(food_logs_today, "sodium_mg")


def _consumed_micro_g(
    food_logs_today: Optional[Sequence[dict]], key: str
) -> float:
    from .nutrition_micros import micros_from_nutrients

    total = 0.0
    found = False
    for row in food_logs_today or []:
        if not isinstance(row, dict):
            continue
        m = micros_from_nutrients(row.get("nutrients"))
        n = m.get(key)
        if n is None:
            n = _optional_micro_float(row.get(key))
        if n is None:
            continue
        total += float(n)
        found = True
    return round(total, 1) if found else 0.0


def _score_ingredient(
    ing: dict,
    rem: dict,
    *,
    fiber_need_g: float = 0.0,
    sugar_room_g: Optional[float] = None,
    sodium_room_mg: Optional[float] = None,
) -> float:
    """Higher is better for filling remaining needs (protein-weighted)."""
    if rem["calories"] <= 0 and rem["protein_g"] <= 0:
        return -1.0
    p = float(ing["protein_g"])
    c = float(ing["calories"]) or 1.0
    # Prefer high protein density, still useful for calories
    protein_need = max(rem["protein_g"], 1.0)
    cal_need = max(rem["calories"], 1.0)
    sc = (p / protein_need) * 3.0 + (min(c, rem["calories"]) / cal_need) * 1.0 + (p / c) * 2.0
    if fiber_need_g > 0:
        fg = estimated_fiber_g(ing)
        sc += (fg / max(fiber_need_g, 1.0)) * 1.2
        if is_veg_or_fruit(ing):
            sc += 0.4
    if sugar_room_g is not None:
        sg = estimated_sugar_g(ing)
        if sg:
            if sugar_room_g <= 0:
                sc -= min(sg, 50.0) * 0.05
            else:
                sc -= (sg / max(sugar_room_g, 1.0)) * 0.9
    if sodium_room_mg is not None:
        na = estimated_sodium_mg(ing)
        if na:
            if sodium_room_mg <= 0:
                sc -= min(na / 50.0, 20.0) * 0.05
            else:
                sc -= (na / max(sodium_room_mg, 1.0)) * 0.9
    return sc


# Chris 2026-09-08: whole eggs + egg whites are one grouped component.
EGG_WHOLE_ID = "eggs-whole"
EGG_WHITE_ID = "egg-whites"
EGG_GROUP_ID = "eggs"
EGG_GROUP_LABEL = "Whole eggs + egg whites"
_EGG_WHOLE_IDS = frozenset({"eggs-whole", "egg-whole", "whole-eggs"})
_EGG_WHITE_IDS = frozenset({"egg-whites", "egg-white", "eggs-whites"})


def egg_role(item: Any) -> Optional[str]:
    """Canonical pair id for a plan/inventory row, or None if not an egg half."""
    if not isinstance(item, dict):
        return None
    iid = str(item.get("id") or "").strip().lower()
    if iid in _EGG_WHOLE_IDS:
        return EGG_WHOLE_ID
    if iid in _EGG_WHITE_IDS:
        return EGG_WHITE_ID
    name = str(item.get("name") or "").strip().lower()
    if re.search(r"\beggplant\b", name):
        return None
    if re.search(r"\bwhole eggs?\b", name):
        return EGG_WHOLE_ID
    if re.search(r"\begg whites?\b", name):
        return EGG_WHITE_ID
    return None


def egg_mate_id(role: str) -> str:
    return EGG_WHITE_ID if role == EGG_WHOLE_ID else EGG_WHOLE_ID


def _mark_egg_group(item: dict) -> dict:
    item["group_id"] = EGG_GROUP_ID
    item["group_label"] = EGG_GROUP_LABEL
    return item


def _stocked_egg(stocked: Sequence[dict], role: str) -> Optional[dict]:
    for ing in stocked:
        if egg_role(ing) == role:
            return ing
    return None


def _append_egg_mate(
    plan_items: List[dict],
    ing: dict,
    rem: dict,
    totals: dict,
) -> None:
    """Add the stocked mate. Prefer remaining-macro portion; else one serving."""
    ceiling = float(rem.get("calories") or 0) + max(80.0, float(rem.get("calories") or 0) * 0.1)
    pick = _pick_continuous_portion(ing, rem, ceiling, totals)
    if pick is None:
        pick = _one_serving_pick(ing)
    servings_n, portion_g = pick
    row = _plan_item_from_ingredient(ing, servings=servings_n, portion_g=portion_g)
    n = float(row.get("servings") or 1)
    row["fiber_g"] = round(estimated_fiber_g(ing) * n, 1)
    sg = estimated_sugar_g(ing)
    if sg is not None:
        row["sugar_g"] = round(sg * n, 1)
    na = estimated_sodium_mg(ing)
    if na is not None:
        row["sodium_mg"] = round(na * n, 0)
    row["is_shake"] = is_shake_or_powder(ing)
    row["is_veg_or_fruit"] = is_veg_or_fruit(ing)
    _mark_egg_group(row)
    plan_items.append(row)
    for k in _MACRO_KEYS:
        totals[k] = float(totals.get(k) or 0) + float(row.get(k) or 0)
        rem[k] = round(max(0.0, float(rem.get(k) or 0) - float(row.get(k) or 0)), 1)
    totals["fiber_g"] = round(
        float(totals.get("fiber_g") or 0) + float(row.get("fiber_g") or 0), 1
    )
    if row.get("sugar_g") is not None:
        totals["sugar_g"] = round(
            float(totals.get("sugar_g") or 0) + float(row["sugar_g"]), 1
        )
    if row.get("sodium_mg") is not None:
        totals["sodium_mg"] = round(
            float(totals.get("sodium_mg") or 0) + float(row["sodium_mg"]), 0
        )


def ensure_egg_pair(
    plan_items: List[dict],
    stocked: Sequence[dict],
    rem: dict,
    totals: dict,
) -> tuple[List[dict], Optional[str]]:
    """If either egg half is planned, co-schedule the mate from stock.

    Does not invent the missing half. Does not force eggs onto a plan that
    has neither. Returns honesty rows + notes.egg_pair value.
    """
    honesty: List[dict] = []
    present = {egg_role(it) for it in plan_items}
    present.discard(None)
    if not present:
        return honesty, None
    for it in plan_items:
        if egg_role(it):
            _mark_egg_group(it)
    for role in (EGG_WHOLE_ID, EGG_WHITE_ID):
        if role not in present:
            continue
        mate = egg_mate_id(role)
        if mate in present:
            continue
        stocked_mate = _stocked_egg(stocked, mate)
        if stocked_mate is None:
            have = "whole eggs" if role == EGG_WHOLE_ID else "egg whites"
            missing = "egg whites" if role == EGG_WHOLE_ID else "whole eggs"
            honesty.append(
                {
                    "level": "warn",
                    "kind": "egg_pair",
                    "text": (
                        f"{have.capitalize()} planned; {missing} not in stock — "
                        f"using {have} only (not inventing the pair)."
                    ),
                }
            )
            continue
        _append_egg_mate(plan_items, stocked_mate, rem, totals)
        present.add(mate)
    if EGG_WHOLE_ID in present and EGG_WHITE_ID in present:
        note = "complete"
    elif EGG_WHITE_ID in present:
        note = "whites_only"
    else:
        note = "wholes_only"
    return honesty, note


def _recompute_meal_totals(meal: dict) -> None:
    sub = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    for it in meal.get("items") or []:
        for k in sub:
            sub[k] += float(it.get(k) or 0)
    meal["totals"] = {k: round(v, 1) for k, v in sub.items()}


def colocate_egg_pair(meals: List[dict]) -> List[dict]:
    """Keep whole eggs + egg whites on the same meal, adjacent, grouped."""
    if not meals:
        return meals
    egg_idxs = [
        i
        for i, m in enumerate(meals)
        if any(egg_role(it) for it in (m.get("items") or []))
    ]
    if not egg_idxs:
        return meals
    home = egg_idxs[0]
    for i in egg_idxs[1:]:
        stay = []
        for it in meals[i].get("items") or []:
            if egg_role(it):
                meals[home].setdefault("items", []).append(it)
            else:
                stay.append(it)
        meals[i]["items"] = stay
        _recompute_meal_totals(meals[i])
    meals = [m for m in meals if m.get("items")]
    for meal in meals:
        items = list(meal.get("items") or [])
        eggs = [it for it in items if egg_role(it)]
        rest = [it for it in items if not egg_role(it)]
        if not eggs:
            meal.pop("egg_pair", None)
            continue
        eggs.sort(key=lambda it: 0 if egg_role(it) == EGG_WHOLE_ID else 1)
        for it in eggs:
            _mark_egg_group(it)
        meal["items"] = eggs + rest
        meal["egg_pair"] = {
            "id": EGG_GROUP_ID,
            "label": EGG_GROUP_LABEL,
            "complete": len({egg_role(it) for it in eggs}) >= 2,
            "item_ids": [it.get("id") for it in eggs],
        }
        _recompute_meal_totals(meal)
    return meals


def generate_meal_plan(
    inventory: dict,
    targets: dict,
    consumed: dict,
    max_items: int = 12,
    food_logs_today: Optional[Sequence[dict]] = None,
    *,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
    window_start: Any = None,
    window_end: Any = None,
    eat_slots: Optional[Sequence[Any]] = None,
    sleep_battery: Optional[dict] = None,
    recommended_targets: Optional[dict] = None,
) -> dict:
    """
    Greedy remaining-day plan from stocked ingredients.

    When ``serving_g`` is known, fills remaining protein/calories with
    **continuous ``portion_g``** (partial grams OK — 25g, 250g, 500g). Macros
    are ``(portion_g / serving_g) × per-serving macros``. Not locked to whole
    inventory serving steps. Foods without usable mass keep free-text
    ``serving_label`` honesty — never invent grams. Soft calorie ceiling
    (~+10% / +80 kcal). When food_logs_today is provided, scoring biases away
    from foods already eaten heavily today.

    Meal buckets get America/New_York (or viewer) ``eat_at`` clocks. Times use
    the FitDash eating window (wake→end) when known; optional ``eat_slots``
    only if a caller passes them. Defaults otherwise: ~12:00 / 15:30 / 19:00.
    Regen drops past slots (20 min grace) and re-times remaining meals in the
    leftover window — never lists noon as upcoming at 3 PM. When intake is
    ahead of eating-window calorie pace past the on-pace band, the first
    upcoming meal is pushed to ``now + (ahead / target) * window_duration``,
    clamped to the window with ``MIN_MEAL_GAP``; behind / on-pace is unchanged.
    Slot count is 1–4 from remaining macros + in-stock items, capped by
    remaining-window capacity — never empty timed hinges, never invented food.

    Food quality (#501): ≥1 veg/fruit slot before shake fill when pantry
    allows; soft fiber ~25g biases fill order; shake/powder cap ≤2 servings
    or ~60g powder protein unless pantry cannot otherwise meet the protein
    target (escape + note). Empty plan regenerates with a relaxed ceiling
    when stock can support a plan. Dark pantry is ``pantry_unavailable``,
    never ``no_stock``.

    Diversity (#513): **primary** = target closeness (2100 / 210P / 180C /
    55F remaining). **Secondary** = maximize distinct in-stock ingredients
    within ``DIVERSITY_EPS`` (80 kcal / 8g P / 15g C / 8g F). Up to two
    distinct veg/fruit slots at one serving each when both fit. Repeats
    are allowed when the pantry is too thin; honesty notes that instead
    of inventing food. Whole-food slots first; shakes last-resort under
    the #501 cap.

    Eggs (#532): if the plan includes whole eggs **or** egg whites, the
    stocked mate is co-scheduled on the **same meal** as a grouped unit
    (``group_id=eggs`` / meal ``egg_pair``). Missing half is noted, never
    invented. Neither stocked → eggs are not forced onto the plan.
    """
    targets = normalize_targets(targets)
    remaining_before = remaining_macros(targets, consumed)
    rem = remaining_macros(targets, consumed)
    fiber_target = resolve_micro_target(
        targets, recommended_targets, "fiber_g", fallback=SOFT_FIBER_TARGET_G
    )
    sugar_target = resolve_micro_target(targets, recommended_targets, "sugar_g")
    sodium_target = resolve_micro_target(targets, recommended_targets, "sodium_mg")
    # Meal plan MUST only use actively in-stock inventory (never out-of-stock).
    stocked = stocked_ingredients(inventory)
    stocked_ids = {str(i.get("id") or "") for i in stocked}
    stocked_names = {str(i.get("name") or "").strip().lower() for i in stocked}
    plan_items: List[dict] = []
    totals = {
        "calories": 0.0,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0,
        "fiber_g": 0.0,
        "sugar_g": 0.0,
        "sodium_mg": 0.0,
    }
    logged = list(food_logs_today or [])
    logged_names = {str(x.get("name") or "").strip().lower() for x in logged if x}
    fiber_logged = consumed_fiber_g(logged)
    sugar_logged = consumed_sugar_g(logged)
    sodium_logged = consumed_sodium_mg(logged)
    veg_stocked = [i for i in stocked if is_veg_or_fruit(i)]
    shake_stocked = [i for i in stocked if is_shake_or_powder(i)]

    ings = inventory.get("ingredients") if isinstance(inventory, dict) else None
    pantry_dark = not isinstance(ings, list) or len(ings) == 0
    if not stocked:
        from .meal_plan_store import MSG_NO_IN_STOCK, MSG_PANTRY_UNAVAILABLE

        empty_reason = "pantry_unavailable" if pantry_dark else "no_stock"
        message = MSG_PANTRY_UNAVAILABLE if pantry_dark else MSG_NO_IN_STOCK
        if pantry_dark:
            honesty_text = (
                "Pantry unavailable — not inventing a pantry. "
                "Wait for inventory, then Refresh plan."
            )
        else:
            honesty_text = (
                "No plan — pantry has no in-stock items. "
                "Mark staples in stock, then Refresh plan."
            )
        return {
            "meals": [],
            "items": [],
            "planned_totals": {
                **{k: 0.0 for k in _MACRO_KEYS},
                "fiber_g": 0.0,
                "sugar_g": 0.0,
                "sodium_mg": 0.0,
            },
            "remaining_after_plan": rem,
            "remaining_before_plan": remaining_before,
            "targets": targets,
            "consumed": consumed,
            "food_logs_today": logged,
            "stocked_count": 0,
            "pantry_dark": pantry_dark,
            "in_stock_only": True,
            "message": message,
            "serving_grams_nudge": "",
            "notes": {
                "empty_plan": True,
                "empty_plan_reason": empty_reason,
                "regen_attempted": False,
                "veg_slot_filled": False,
                "veg_slot_missed": False,
                "veg_available": False,
                "shake_servings": 0,
                "shake_powder_protein_g": 0.0,
                "shake_cap_applied": False,
                "shake_cap_escaped": False,
                "fiber_soft_target_g": fiber_target,
                "fiber_planned_g": 0.0,
                "fiber_consumed_g": fiber_logged,
                "fiber_miss": fiber_logged < float(fiber_target or 0),
                "fiber_miss_reason": "no_fiber_foods",
                "sugar_target_g": sugar_target,
                "sugar_planned_g": 0.0,
                "sugar_consumed_g": sugar_logged,
                "sugar_miss": bool(
                    sugar_target is not None and sugar_logged > float(sugar_target)
                ),
                "sodium_target_mg": sodium_target,
                "sodium_planned_mg": 0.0,
                "sodium_consumed_mg": sodium_logged,
                "sodium_miss": bool(
                    sodium_target is not None and sodium_logged > float(sodium_target)
                ),
                **_diversity_notes_fields(distinct=0, limited=False),
            },
            "honesty": [
                {"level": "warn", "kind": "empty_plan", "text": honesty_text}
            ],
            "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        }

    pick_counts: Dict[str, int] = {}
    shake_cap_applied = False
    shake_cap_escaped = False
    regen_attempted = False
    veg_slot_filled = False
    veg_slot_missed = False

    def _shake_totals() -> tuple[float, float]:
        n = 0.0
        p = 0.0
        for it in plan_items:
            if is_shake_or_powder(it) or it.get("is_shake"):
                n += float(it.get("servings") or 1)
                p += float(it.get("protein_g") or 0)
        return n, p

    def _fiber_need() -> float:
        return max(
            0.0,
            float(fiber_target or 0) - fiber_logged - float(totals.get("fiber_g") or 0),
        )

    def _sugar_room() -> Optional[float]:
        if sugar_target is None:
            return None
        return float(sugar_target) - sugar_logged - float(totals.get("sugar_g") or 0)

    def _sodium_room() -> Optional[float]:
        if sodium_target is None:
            return None
        return float(sodium_target) - sodium_logged - float(totals.get("sodium_mg") or 0)

    def _cal_ceiling(relax: bool) -> float:
        base = remaining_before["calories"] + max(80.0, remaining_before["calories"] * 0.1)
        return base + (250.0 if relax else 0.0)

    def _non_shake_protein_available() -> bool:
        for ing in stocked:
            if is_shake_or_powder(ing):
                continue
            if float(ing.get("protein_g") or 0) >= 8:
                return True
        return False

    def _append(ing: dict, pick: tuple) -> None:
        servings_n, portion_g = pick
        row = _plan_item_from_ingredient(ing, servings=servings_n, portion_g=portion_g)
        n = float(row.get("servings") or 1)
        row["fiber_g"] = round(estimated_fiber_g(ing) * n, 1)
        sg = estimated_sugar_g(ing)
        if sg is not None:
            row["sugar_g"] = round(sg * n, 1)
        na = estimated_sodium_mg(ing)
        if na is not None:
            row["sodium_mg"] = round(na * n, 0)
        row["is_shake"] = is_shake_or_powder(ing)
        row["is_veg_or_fruit"] = is_veg_or_fruit(ing)
        plan_items.append(row)
        pick_counts[str(ing["id"])] = pick_counts.get(str(ing["id"]), 0) + 1
        for k in _MACRO_KEYS:
            totals[k] += float(row.get(k) or 0)
            rem[k] = round(max(0.0, rem[k] - float(row.get(k) or 0)), 1)
        totals["fiber_g"] = round(float(totals.get("fiber_g") or 0) + float(row["fiber_g"]), 1)
        if row.get("sugar_g") is not None:
            totals["sugar_g"] = round(
                float(totals.get("sugar_g") or 0) + float(row["sugar_g"]), 1
            )
        if row.get("sodium_mg") is not None:
            totals["sodium_mg"] = round(
                float(totals.get("sodium_mg") or 0) + float(row["sodium_mg"]), 0
            )

    def _collect_candidates(
        *, relax: bool, allow_shake_escape: bool, veg_only: bool = False
    ) -> List[tuple]:
        nonlocal shake_cap_applied
        candidates = []
        ceiling = _cal_ceiling(relax)
        shake_n, shake_p = _shake_totals()
        for ing in stocked:
            if veg_only and not is_veg_or_fruit(ing):
                continue
            iid = str(ing["id"])
            if pick_counts.get(iid, 0) >= 3:
                continue
            veg_cap = VEG_PICK_MAX_SERVINGS if is_veg_or_fruit(ing) else None
            pick = _pick_continuous_portion(
                ing, rem, ceiling, totals, max_servings=veg_cap
            )
            if pick is None:
                continue
            _servings_n, _portion_g = pick
            min_macros = (
                _macros_for_portion(ing, portion_g=_portion_g)
                if _portion_g is not None
                else _macros_for_portion(ing, servings=_servings_n)
            )
            if is_shake_or_powder(ing) and not allow_shake_escape:
                next_n = shake_n + float(_servings_n or 1)
                next_p = shake_p + float(min_macros.get("protein_g") or 0)
                if next_n > SHAKE_MAX_SERVINGS + 0.05 or next_p > SHAKE_MAX_POWDER_PROTEIN_G + 0.05:
                    shake_cap_applied = True
                    room_n = SHAKE_MAX_SERVINGS - shake_n
                    room_p = SHAKE_MAX_POWDER_PROTEIN_G - shake_p
                    sg = _ingredient_serving_g(ing)
                    if room_n <= 0.05 or room_p <= 1.0:
                        continue
                    if _portion_g is not None and sg is not None and float(sg) > 0:
                        prot_pg = float(ing.get("protein_g") or 0) / float(sg)
                        max_g_p = (room_p / prot_pg) if prot_pg > 0 else 0.0
                        max_g_n = room_n * float(sg)
                        new_g = _round_portion_g(
                            min(float(_portion_g), max_g_p, max_g_n), serving_g=sg
                        )
                        if new_g <= 0:
                            continue
                        pick = (new_g / float(sg), new_g)
                        min_macros = _macros_for_portion(ing, portion_g=new_g)
                    elif (shake_n + 1.0) > SHAKE_MAX_SERVINGS + 0.05 or (
                        shake_p + float(ing.get("protein_g") or 0)
                    ) > SHAKE_MAX_POWDER_PROTEIN_G + 0.05:
                        continue
                if not veg_slot_filled and veg_stocked:
                    unused_veg = [
                        v
                        for v in veg_stocked
                        if pick_counts.get(str(v["id"]), 0) == 0
                        and float(v["calories"]) <= rem["calories"] + 80
                    ]
                    if unused_veg:
                        continue
            if rem["protein_g"] < 20 and min_macros["protein_g"] > rem["protein_g"] + 25:
                continue
            if totals["calories"] + min_macros["calories"] > ceiling and rem["protein_g"] < 20:
                continue
            if (
                totals["calories"] + min_macros["calories"] > ceiling + 100
                and min_macros["protein_g"] < 25
            ):
                continue
            if rem["protein_g"] < 15 and float(ing["protein_g"]) > 25 and rem["calories"] > 100:
                if float(ing["carbs_g"]) < 10 and not is_veg_or_fruit(ing):
                    continue
            if relax:
                if (
                    totals["calories"] + float(min_macros["calories"]) > ceiling + 80
                    and float(min_macros["protein_g"]) < 12
                    and not is_veg_or_fruit(ing)
                ):
                    continue
            sc = _score_ingredient(
                ing,
                rem,
                fiber_need_g=_fiber_need(),
                sugar_room_g=_sugar_room(),
                sodium_room_mg=_sodium_room(),
            )
            iname = str(ing.get("name") or "").strip().lower()
            if iname and any(iname in ln or ln in iname for ln in logged_names if ln):
                sc *= 0.85
            already = pick_counts.get(iid, 0)
            if already >= 1:
                sc *= 0.55 ** already
            if sc > 0:
                candidates.append((sc, ing, pick))
        return candidates

    def _fill(*, relax: bool, allow_shake_escape: bool) -> None:
        for _ in range(max(0, max_items - len(plan_items))):
            if rem["calories"] < 25 and rem["protein_g"] < 5:
                break
            candidates = _collect_candidates(
                relax=relax, allow_shake_escape=allow_shake_escape
            )
            if not candidates:
                break
            chosen = select_diverse_candidate(candidates, rem, pick_counts)
            if chosen is None:
                break
            _append(chosen[1], chosen[2])

    def _try_append_veg_serving(ing: dict) -> bool:
        one = _one_serving_pick(ing)
        macros = _macros_from_pick(ing, one)
        if macros["calories"] > rem["calories"] + DIVERSITY_EPS_KCAL:
            pick = _pick_continuous_portion(
                ing,
                rem,
                _cal_ceiling(False),
                totals,
                max_servings=VEG_SLOT_SERVINGS,
            )
            if pick is None:
                return False
            one = pick
            macros = _macros_from_pick(ing, one)
            if macros["calories"] > rem["calories"] + DIVERSITY_EPS_KCAL:
                return False
        if totals["calories"] + macros["calories"] > _cal_ceiling(False) + DIVERSITY_EPS_KCAL:
            return False
        _append(ing, one)
        return True

    # #501: ≥1 veg/fruit slot before shake fill. #513: a second distinct
    # veg/fruit (one serving each) when both close the gap within ε.
    veg_slots_want = (
        MAX_DISTINCT_VEG_SLOTS if len(veg_stocked) >= 2 else (1 if veg_stocked else 0)
    )
    veg_added = 0
    if veg_stocked:
        unused_veg = list(veg_stocked)
        unused_veg.sort(
            key=lambda ing: (
                -estimated_fiber_g(ing),
                -float(ing.get("protein_g") or 0),
                float(ing.get("calories") or 0),
            )
        )
        for ing in unused_veg:
            if veg_added >= veg_slots_want:
                break
            if pick_counts.get(str(ing.get("id") or ""), 0) > 0:
                continue
            if _try_append_veg_serving(ing):
                veg_added += 1
                veg_slot_filled = True
        if veg_added == 0:
            veg_slot_missed = True

    _fill(relax=False, allow_shake_escape=False)

    # AC1: empty plan is not OK when remaining macros exist and pantry has food.
    needs_plan = remaining_before["protein_g"] >= 15 or remaining_before["calories"] >= 80
    if not plan_items and needs_plan:
        regen_attempted = True
        _fill(relax=True, allow_shake_escape=False)

    # AC4: escape hatch — powder past the cap only if 210P cannot be met otherwise.
    if rem["protein_g"] >= 40 and shake_stocked and not _non_shake_protein_available():
        before_n = len(plan_items)
        _fill(relax=False, allow_shake_escape=True)
        if len(plan_items) > before_n:
            shake_cap_escaped = True

    # Safety net: never surface an item that is not currently stocked
    plan_items = [
        it
        for it in plan_items
        if str(it.get("id") or "") in stocked_ids
        or str(it.get("name") or "").strip().lower() in stocked_names
    ]
    # Collapse repeated picks into one line with servings (e.g. 3× chicken)
    plan_items = _collapse_plan_items(plan_items)
    # #532: whole eggs + egg whites are one grouped component (same meal).
    egg_honesty, egg_pair_note = ensure_egg_pair(plan_items, stocked, rem, totals)
    plan_items = _collapse_plan_items(plan_items)
    # Group into timed meal buckets (1–4). No empty hinges; no invented food.
    meals = _bucket_meals(
        plan_items,
        remaining=remaining_macros(targets, consumed),
        now=now,
        tz_name=tz_name,
        window_start=window_start,
        window_end=window_end,
        eat_slots=eat_slots,
        sleep_battery=sleep_battery,
        consumed=consumed,
        targets=targets,
    )
    meals = colocate_egg_pair(meals)
    for k in _MACRO_KEYS:
        totals[k] = round(totals[k], 1)
    totals["fiber_g"] = round(float(totals.get("fiber_g") or 0), 1)

    remaining_after = remaining_macros(
        targets,
        {
            "calories": float(consumed.get("calories") or 0) + totals["calories"],
            "protein_g": float(consumed.get("protein_g") or 0) + totals["protein_g"],
            "carbs_g": float(consumed.get("carbs_g") or 0) + totals["carbs_g"],
            "fat_g": float(consumed.get("fat_g") or 0) + totals["fat_g"],
        },
    )

    shake_n, shake_p = 0.0, 0.0
    veg_in_plan = False
    for it in plan_items:
        if is_shake_or_powder(it) or it.get("is_shake"):
            shake_n += float(it.get("servings") or 1)
            shake_p += float(it.get("protein_g") or 0)
        if is_veg_or_fruit(it) or it.get("is_veg_or_fruit"):
            veg_in_plan = True
    veg_slot_filled = veg_slot_filled or veg_in_plan
    if veg_stocked and not veg_slot_filled:
        veg_slot_missed = True

    fiber_planned = round(sum(float(it.get("fiber_g") or 0) for it in plan_items), 1)
    if fiber_planned <= 0:
        fiber_planned = round(float(totals.get("fiber_g") or 0), 1)
    fiber_total = fiber_logged + fiber_planned
    fiber_miss = fiber_total < float(fiber_target or 0)
    sugar_planned = round(sum(float(it.get("sugar_g") or 0) for it in plan_items), 1)
    if sugar_planned <= 0:
        sugar_planned = round(float(totals.get("sugar_g") or 0), 1)
    sodium_planned = round(sum(float(it.get("sodium_mg") or 0) for it in plan_items), 0)
    if sodium_planned <= 0:
        sodium_planned = round(float(totals.get("sodium_mg") or 0), 0)
    sugar_miss = bool(
        sugar_target is not None
        and (sugar_logged + sugar_planned) > float(sugar_target)
    )
    sodium_miss = bool(
        sodium_target is not None
        and (sodium_logged + sodium_planned) > float(sodium_target)
    )
    fiber_foods = [
        i for i in stocked if estimated_fiber_g(i) >= 2.0 or is_veg_or_fruit(i)
    ]
    if not fiber_miss:
        fiber_miss_reason = None
    elif not fiber_foods:
        fiber_miss_reason = "no_fiber_foods"
    else:
        fiber_miss_reason = "pantry_blocked"

    empty = not plan_items
    if empty:
        if remaining_before["protein_g"] < 15 and remaining_before["calories"] < 80:
            empty_reason = "targets_met"
        else:
            empty_reason = "pantry_blocked"
    else:
        empty_reason = None

    def _item_key(it: dict) -> str:
        return str(it.get("id") or it.get("name") or "").strip().lower()

    distinct_keys = []
    seen_keys = set()
    for it in plan_items:
        k = _item_key(it)
        if k and k not in seen_keys:
            seen_keys.add(k)
            distinct_keys.append(k)
    distinct_n = len(seen_keys)
    repeats = any(float(it.get("servings") or 1) > 1.05 for it in plan_items)
    if not repeats:
        meal_hits: Dict[str, int] = {}
        for meal in meals:
            for it in meal.get("items") or []:
                k = _item_key(it)
                if k:
                    meal_hits[k] = meal_hits.get(k, 0) + 1
        repeats = any(v > 1 for v in meal_hits.values())
    diversity_limited = (not empty) and repeats and (
        distinct_n <= 1 or len(stocked) <= 2
    )

    notes = {
        "empty_plan": empty,
        "empty_plan_reason": empty_reason,
        "regen_attempted": regen_attempted,
        "veg_slot_filled": veg_slot_filled,
        "veg_slot_missed": veg_slot_missed and not veg_slot_filled,
        "veg_available": bool(veg_stocked),
        "shake_servings": round(shake_n, 2),
        "shake_powder_protein_g": round(shake_p, 1),
        "shake_cap_applied": shake_cap_applied and not shake_cap_escaped,
        "shake_cap_escaped": shake_cap_escaped,
        "fiber_soft_target_g": fiber_target,
        "fiber_planned_g": fiber_planned,
        "fiber_consumed_g": fiber_logged,
        "fiber_miss": fiber_miss,
        "fiber_miss_reason": fiber_miss_reason,
        "sugar_target_g": sugar_target,
        "sugar_planned_g": sugar_planned,
        "sugar_consumed_g": sugar_logged,
        "sugar_miss": sugar_miss,
        "sodium_target_mg": sodium_target,
        "sodium_planned_mg": sodium_planned,
        "sodium_consumed_mg": sodium_logged,
        "sodium_miss": sodium_miss,
        **_diversity_notes_fields(distinct=distinct_n, limited=diversity_limited),
        "egg_pair": egg_pair_note,
    }

    honesty: List[dict] = []
    msg = (
        f"Plan from {len(stocked)} in-stock ingredient"
        f"{'s' if len(stocked) != 1 else ''} only (out-of-stock excluded)."
    )
    if logged:
        msg += (
            f" Uses {len(logged)} Google Health food log"
            f"{'s' if len(logged) != 1 else ''} so far today for remaining macros."
        )
    if empty and empty_reason == "targets_met":
        msg = (
            f"Day essentially complete — only ~{remaining_before['calories']:.0f} kcal and "
            f"{remaining_before['protein_g']:.0f}g protein left under target; no extra servings planned."
        )
        honesty.append({"level": "muted", "kind": "empty_plan", "text": msg})
    elif empty and empty_reason == "pantry_blocked":
        msg = (
            "No plan — pantry cannot fill remaining macros without inventing food. "
            "Mark staples in stock, then Refresh plan."
        )
        if regen_attempted:
            msg = (
                "No plan after regen — in-stock items do not fit remaining macros "
                "without inventing food. Restock or Refresh plan after stock lands."
            )
        honesty.append({"level": "warn", "kind": "empty_plan", "text": msg})
    elif remaining_after["protein_g"] > 40:
        msg += " Protein still short — restock high-protein items if needed."
    if remaining_after["calories"] > 300 and not plan_items and remaining_before["protein_g"] >= 20:
        msg = "Could not fit more servings without exceeding soft calorie ceiling (in-stock only)."

    if shake_cap_escaped:
        honesty.append(
            {
                "level": "warn",
                "kind": "shake_cap",
                "text": (
                    f"Shake cap escaped — pantry cannot otherwise meet "
                    f"{int(targets.get('protein_g') or 0)}g protein "
                    f"(using extra powder; {shake_n:g} shakes / {round(shake_p, 0):.0f}g powder P)."
                ),
            }
        )
    elif shake_n > 0 and (shake_cap_applied or shake_n >= SHAKE_MAX_SERVINGS):
        honesty.append(
            {
                "level": "muted",
                "kind": "shake_cap",
                "text": (
                    f"Shake cap applied: {shake_n:g} shake"
                    f"{'s' if shake_n != 1 else ''} / {round(shake_p, 0):.0f}g powder P "
                    f"(≤{SHAKE_MAX_SERVINGS}/day or ≤~{int(SHAKE_MAX_POWDER_PROTEIN_G)}g)."
                ),
            }
        )
    if veg_slot_missed and not veg_slot_filled:
        honesty.append(
            {
                "level": "warn",
                "kind": "veg_slot",
                "text": "Veg/fruit slot missed — pantry had produce but it did not fit remaining calories.",
            }
        )
    elif veg_stocked and not veg_slot_filled:
        honesty.append(
            {
                "level": "warn",
                "kind": "veg_slot",
                "text": "No veg/fruit in this plan (pantry had produce).",
            }
        )
    if fiber_miss and not empty:
        if fiber_miss_reason == "no_fiber_foods":
            honesty.append(
                {
                    "level": "warn",
                    "kind": "fiber",
                    "text": (
                        f"Soft fiber ~{int(fiber_target or 0)}g missed "
                        f"(planned {fiber_planned:.0f}g + logged {fiber_logged:.0f}g). "
                        "No fiber-rich stock — not inventing items."
                    ),
                }
            )
        else:
            honesty.append(
                {
                    "level": "warn",
                    "kind": "fiber",
                    "text": (
                        f"Soft fiber ~{int(fiber_target or 0)}g missed "
                        f"(planned {fiber_planned:.0f}g + logged {fiber_logged:.0f}g; "
                        "pantry-limited, not inventing items)."
                    ),
                }
            )
    if sugar_miss and not empty:
        honesty.append(
            {
                "level": "warn",
                "kind": "sugar",
                "text": (
                    f"Sugar ceiling {int(sugar_target)}g missed "
                    f"(planned {sugar_planned:.0f}g + logged {sugar_logged:.0f}g; "
                    "pantry-limited, not inventing items)."
                ),
            }
        )
    if sodium_miss and not empty:
        honesty.append(
            {
                "level": "warn",
                "kind": "sodium",
                "text": (
                    f"Salt (sodium) ceiling {int(sodium_target)}mg missed "
                    f"(planned {sodium_planned:.0f}mg + logged {sodium_logged:.0f}mg; "
                    "pantry-limited, not inventing items)."
                ),
            }
        )
    if diversity_limited:
        honesty.append(
            {
                "level": "muted",
                "kind": "diversity",
                "text": (
                    "Pantry diversity limited — repeating in-stock items to hit "
                    "targets (not inventing food)."
                ),
            }
        )
    honesty.extend(egg_honesty)

    return {
        "meals": meals,
        "items": plan_items,
        "planned_totals": {
            **{k: totals[k] for k in _MACRO_KEYS},
            "fiber_g": round(float(totals.get("fiber_g") or 0), 1),
            "sugar_g": round(float(totals.get("sugar_g") or 0), 1),
            "sodium_mg": round(float(totals.get("sodium_mg") or 0), 0),
        },
        "remaining_before_plan": remaining_before,
        "remaining_after_plan": remaining_after,
        "targets": targets,
        "consumed": consumed,
        "food_logs_today": logged,
        "stocked_count": len(stocked),
        "pantry_dark": False,
        "in_stock_only": True,
        "message": msg,
        "serving_grams_nudge": serving_grams_nudge_text(plan_items),
        "notes": notes,
        "honesty": honesty,
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
    }

def _plan_item_from_ingredient(
    ing: dict,
    servings: float = 1.0,
    portion_g: Optional[float] = None,
) -> dict:
    """One meal-plan line: macros scale with servings or portion_g / serving_g."""
    base_g = _ingredient_serving_g(ing)
    display_g = None
    if portion_g is not None and base_g is not None and float(base_g) > 0:
        try:
            pg = float(portion_g)
        except (TypeError, ValueError):
            pg = 0.0
        if pg > 0:
            display_g = _round_portion_g(pg, serving_g=float(base_g))
            n = display_g / float(base_g) if display_g else float(servings or 1)
        else:
            n = float(servings or 1)
            display_g = round(float(base_g) * n)
    else:
        n = float(servings or 1)
        if base_g is not None and float(base_g) > 0:
            display_g = round(float(base_g) * n)
    macros = _macros_for_portion(
        ing,
        servings=n,
        portion_g=display_g if display_g is not None and base_g else None,
    )
    label = format_portion_label(
        serving_g=float(base_g) if base_g is not None else None,
        servings=n,
        serving_label=str(ing.get("serving_label") or "1 serving"),
    )
    row = {
        "id": ing.get("id"),
        "name": ing.get("name"),
        "servings": int(n) if abs(n - int(n)) < 1e-9 else round(n, 2),
        "serving_label": label,
        "calories": macros["calories"],
        "protein_g": macros["protein_g"],
        "carbs_g": macros["carbs_g"],
        "fat_g": macros["fat_g"],
        "in_stock": True,
        "fiber_g": round(estimated_fiber_g(ing) * n, 1),
        "is_shake": is_shake_or_powder(ing),
        "is_veg_or_fruit": is_veg_or_fruit(ing),
    }
    sg = estimated_sugar_g(ing)
    if sg is not None:
        row["sugar_g"] = round(sg * n, 1)
    na = estimated_sodium_mg(ing)
    if na is not None:
        row["sodium_mg"] = round(na * n, 0)
    if base_g is not None and float(base_g) > 0:
        row["serving_g"] = float(base_g)
    if display_g is not None:
        row["portion_g"] = float(display_g)
    return row


def scale_plan_item_to_inventory(item: dict, ing: dict) -> dict:
    """Rescale a plan/Grok line from inventory serving macros. No invented grams."""
    if not isinstance(item, dict) or not isinstance(ing, dict):
        return item if isinstance(item, dict) else {}
    sg = _ingredient_serving_g(ing)
    n = None
    pg_in = item.get("portion_g")
    serv_in = item.get("servings")
    if sg is not None and float(sg) > 0:
        if pg_in is not None and str(pg_in).strip() != "":
            try:
                pg = float(pg_in)
                if pg > 0:
                    n = pg / float(sg)
            except (TypeError, ValueError):
                n = None
        if n is None and serv_in is not None and str(serv_in).strip() != "":
            try:
                n = float(serv_in)
            except (TypeError, ValueError):
                n = None
        if n is None:
            try:
                cal = float(item.get("calories") or 0)
                base = float(ing.get("calories") or 0)
                if cal > 0 and base > 0:
                    inferred = cal / base
                    if 0.15 <= inferred <= 8:
                        n = inferred
            except (TypeError, ValueError):
                n = None
        if n is None or n <= 0:
            n = 1.0
        return _plan_item_from_ingredient(ing, servings=n)
    # No usable mass: free-text honesty — never mint portion_g / serving_g.
    if serv_in is not None and str(serv_in).strip() != "":
        try:
            n = float(serv_in) or 1.0
        except (TypeError, ValueError):
            n = 1.0
    else:
        n = 1.0
    row = _plan_item_from_ingredient(ing, servings=n)
    row.pop("portion_g", None)
    row.pop("serving_g", None)
    label = str(item.get("serving_label") or ing.get("serving_label") or "1 serving").strip()
    if label:
        if abs(n - 1.0) >= 1e-9:
            row["serving_label"] = format_portion_label(servings=n, serving_label=label)
        else:
            row["serving_label"] = label
    return row


def _collapse_plan_items(items: List[dict]) -> List[dict]:
    """Merge identical ingredient picks into a single row with servings count."""
    if not items:
        return []
    order: List[str] = []
    by_key: Dict[str, dict] = {}
    for it in items:
        key = str(it.get("id") or it.get("name") or "").lower()
        if not key:
            key = f"anon-{len(by_key)}"
        if key not in by_key:
            n = float(it.get("servings") or 1)
            base_g = it.get("serving_g")
            if base_g is None and it.get("portion_g") is not None and n:
                try:
                    base_g = float(it["portion_g"]) / n
                except (TypeError, ValueError, ZeroDivisionError):
                    base_g = None
            row = {
                "id": it.get("id"),
                "name": it.get("name"),
                "servings": n,
                "serving_label": it.get("serving_label") or "1 serving",
                "calories": float(it.get("calories") or 0),
                "protein_g": float(it.get("protein_g") or 0),
                "carbs_g": float(it.get("carbs_g") or 0),
                "fat_g": float(it.get("fat_g") or 0),
                "fiber_g": float(it.get("fiber_g") or 0),
                "is_shake": bool(it.get("is_shake")),
                "is_veg_or_fruit": bool(it.get("is_veg_or_fruit")),
            }
            if base_g is not None and float(base_g) > 0:
                row["serving_g"] = float(base_g)
            if it.get("portion_g") is not None:
                row["portion_g"] = float(it["portion_g"])
            if it.get("group_id"):
                row["group_id"] = it.get("group_id")
                row["group_label"] = it.get("group_label") or EGG_GROUP_LABEL
            by_key[key] = row
            order.append(key)
        else:
            row = by_key[key]
            add_n = float(it.get("servings") or 1)
            row["servings"] = float(row.get("servings") or 0) + add_n
            for k in ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g"):
                row[k] = round(float(row[k]) + float(it.get(k) or 0), 1)
            row["is_shake"] = bool(row.get("is_shake") or it.get("is_shake"))
            row["is_veg_or_fruit"] = bool(
                row.get("is_veg_or_fruit") or it.get("is_veg_or_fruit")
            )
            if it.get("portion_g") is not None:
                row["portion_g"] = round(
                    float(row.get("portion_g") or 0) + float(it["portion_g"]), 1
                )
            if it.get("group_id"):
                row["group_id"] = it.get("group_id")
                row["group_label"] = it.get("group_label") or row.get("group_label")
    out = []
    for key in order:
        row = by_key[key]
        for k in ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g"):
            row[k] = round(float(row[k]), 1)
        n = float(row.get("servings") or 1)
        base_g = row.get("serving_g")
        if base_g is not None and float(base_g) > 0:
            raw_g = row.get("portion_g")
            try:
                raw_g = float(raw_g) if raw_g is not None else float(base_g) * n
            except (TypeError, ValueError):
                raw_g = float(base_g) * n
            portion = _round_portion_g(raw_g, serving_g=float(base_g))
            if portion <= 0:
                portion = _round_portion_g(float(base_g) * n, serving_g=float(base_g))
            if raw_g > 0 and portion > 0 and abs(portion - raw_g) >= 0.05:
                factor = portion / raw_g
                for k in ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g"):
                    if k in row:
                        row[k] = round(float(row[k]) * factor, 1)
            row["portion_g"] = portion
            n = portion / float(base_g)
            row["servings"] = int(n) if abs(n - int(n)) < 1e-9 else round(n, 2)
            row["serving_label"] = format_portion_label(
                serving_g=float(base_g),
                servings=n,
                serving_label=str(row.get("serving_label") or ""),
            )
        else:
            row["servings"] = int(n) if abs(n - int(n)) < 1e-9 else round(n, 2)
            if float(n) != 1.0:
                # Non-gram multi-servings: "3 × 1 cup"
                base_label = str(row.get("serving_label") or "1 serving")
                # Strip prior multiplier if re-collapsing
                base_label = re.sub(r"^\d+\s*×\s*", "", base_label)
                row["serving_label"] = format_portion_label(
                    servings=n, serving_label=base_label
                )
        out.append(row)
    return out


def _meal_tz(tz_name: Optional[str] = None):
    name = (tz_name or "").strip() or MEAL_TZ_NAME
    try:
        return ZoneInfo(name), name
    except Exception:
        return ZoneInfo(MEAL_TZ_NAME), MEAL_TZ_NAME


def _parse_meal_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clock_label(dt: datetime) -> str:
    h24 = dt.hour
    h = h24 % 12 or 12
    return f"{h}:{dt.minute:02d} {'AM' if h24 < 12 else 'PM'}"


def _dedupe_sorted_times(times: Sequence[datetime]) -> List[datetime]:
    out: List[datetime] = []
    seen = set()
    for t in sorted(times):
        key = t.replace(second=0, microsecond=0).isoformat()
        if key in seen:
            continue
        seen.add(key)
        out.append(t.replace(second=0, microsecond=0))
    return out


def _clamp_into_window(dt: datetime, start: datetime, end: datetime) -> datetime:
    if dt < start:
        return start.replace(second=0, microsecond=0)
    if dt > end:
        return end.replace(second=0, microsecond=0)
    return dt.replace(second=0, microsecond=0)


def _space_in_range(
    n: int,
    lo: datetime,
    hi: datetime,
    avoid: Optional[Sequence[datetime]] = None,
) -> List[datetime]:
    if n <= 0:
        return []
    avoid_keys = {
        t.replace(second=0, microsecond=0).isoformat() for t in (avoid or [])
    }
    if hi <= lo:
        return [lo.replace(second=0, microsecond=0)][:n]
    span = max(60.0, (hi - lo).total_seconds())
    out: List[datetime] = []
    # n+1 so first/last are not glued to the window edges.
    step = span / (n + 1)
    for i in range(1, n + 1):
        t = (lo + timedelta(seconds=step * i)).replace(second=0, microsecond=0)
        key = t.isoformat()
        if key in avoid_keys:
            t = (t + timedelta(minutes=25)).replace(second=0, microsecond=0)
            if t > hi:
                t = hi.replace(second=0, microsecond=0)
            key = t.isoformat()
        if key not in avoid_keys:
            avoid_keys.add(key)
            out.append(t)
    return out


def _parse_eat_slot(raw: Any, tz, day: datetime) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=tz)
        return dt.astimezone(tz)
    if isinstance(raw, dict):
        if raw.get("eat_at") is not None:
            return _parse_eat_slot(raw.get("eat_at"), tz, day)
        if raw.get("time") is not None:
            return _parse_eat_slot(raw.get("time"), tz, day)
        if raw.get("hour") is not None:
            try:
                h = int(raw.get("hour"))
                m = int(raw.get("minute") or 0)
                return day.replace(hour=h, minute=m, second=0, microsecond=0)
            except (TypeError, ValueError):
                return None
        return None
    s = str(raw).strip()
    if not s:
        return None
    if "T" in s or s.endswith("Z"):
        dt = _parse_meal_dt(s)
        return dt.astimezone(tz) if dt else None
    parts = s.replace(".", ":").split(":")
    try:
        if len(parts) >= 2:
            h = int(parts[0])
            m = int(parts[1])
            return day.replace(hour=h, minute=m, second=0, microsecond=0)
        if len(parts) == 1 and parts[0].isdigit():
            return day.replace(hour=int(parts[0]), minute=0, second=0, microsecond=0)
    except (TypeError, ValueError):
        return None
    return None


def _eating_window_bounds(
    now: datetime,
    tz,
    tz_name: str,
    window_start: Any = None,
    window_end: Any = None,
    sleep_battery: Optional[dict] = None,
) -> tuple:
    start = _parse_meal_dt(window_start)
    end = _parse_meal_dt(window_end)
    bat = sleep_battery if isinstance(sleep_battery, dict) else {}
    if (start is None or end is None) and bat:
        try:
            from .calorie_bars import eating_window_fraction

            win = eating_window_fraction(
                now=now,
                tz_name=tz_name,
                last_wake_at=bat.get("last_wake_at"),
                empty_at=bat.get("empty_at"),
                awake_budget_hours=float(bat.get("awake_budget_hours") or 15.0),
            )
            start = start or _parse_meal_dt(win.get("window_start"))
            end = end or _parse_meal_dt(win.get("window_end"))
        except Exception:
            start = start or _parse_meal_dt(bat.get("last_wake_at"))
            end = end or _parse_meal_dt(bat.get("empty_at"))
    if start is not None:
        start = start.astimezone(tz)
    if end is not None:
        end = end.astimezone(tz)
    if start is None:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if end is None:
        end = start + timedelta(hours=24)
    if end <= start:
        end = start + timedelta(hours=12)
    return start, end


def _default_slot_hms(n: int) -> List[tuple]:
    if n <= 1:
        return list(DEFAULT_SLOT_HM)
    if n == 2:
        return [(12, 0), (19, 0)]
    if n == 3:
        return list(DEFAULT_SLOT_HM)
    return list(DEFAULT_SLOT_HM) + [FOURTH_SLOT_HM]


def _slot_horizon(now: datetime) -> datetime:
    return now - SLOT_GRACE


def _remaining_meal_capacity(
    now: datetime,
    start: datetime,
    end: datetime,
    *,
    n_max: int = 4,
) -> int:
    """How many meals still fit in the remaining eating window.

    Late-day regen must not invent extra stacked hinges. While the window
    is open, keep at least the next upcoming meal if food remains.
    """
    if end <= start:
        return 0
    horizon = _slot_horizon(now)
    if now >= end:
        return 1 if horizon < end else 0
    lo = now if now > start else start
    if lo >= end:
        return 0
    span = end - lo
    extra = int(span.total_seconds() // MIN_MEAL_GAP.total_seconds())
    return max(1, min(n_max, 1 + extra))


def _clamp_gap_times(times: Sequence[datetime], n: int, end: datetime) -> List[datetime]:
    """Keep chronological times that are inside the window and not stacked."""
    out: List[datetime] = []
    for t in _dedupe_sorted_times(times):
        if t > end:
            continue
        if out and (t - out[-1]) < MIN_MEAL_GAP:
            continue
        out.append(t)
        if len(out) >= n:
            break
    return out


def _kcal_field(raw: Any, key: str = "calories") -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, dict):
        raw = raw.get(key)
    try:
        return max(0.0, float(raw or 0))
    except (TypeError, ValueError):
        return 0.0


def _catch_up_delay(
    *,
    consumed: Any,
    targets: Any,
    now: datetime,
    start: datetime,
    end: datetime,
) -> timedelta:
    """Delay for the first upcoming meal when ahead of eating-window pace.

    ``catch_up = (ahead / target) * window_duration``. Zero when behind or
    on pace (same band as ``calorie_bars.calorie_pacing``).
    """
    target = _kcal_field(targets)
    consumed_kcal = _kcal_field(consumed)
    window_sec = (end - start).total_seconds()
    if target <= 0 or window_sec <= 0:
        return timedelta(0)
    elapsed = max(0.0, (now - start).total_seconds())
    frac = min(1.0, elapsed / window_sec)
    from .calorie_bars import calorie_pacing

    pacing = calorie_pacing(
        consumed=consumed_kcal, target=target, window_fraction=frac
    )
    if pacing.get("status") != "ahead":
        return timedelta(0)
    ahead = max(0.0, float(pacing.get("delta_vs_pace") or 0))
    if ahead <= 0:
        return timedelta(0)
    return timedelta(seconds=(ahead / target) * window_sec)


def _apply_pace_delay(
    times: Sequence[datetime],
    *,
    now: datetime,
    start: datetime,
    end: datetime,
    catch_up: timedelta,
) -> List[datetime]:
    """Push the first upcoming meal to ``now + catch_up``; keep MIN_MEAL_GAP.

    In-progress grace slots (``t < now``) stay. Later hinges are never pulled
    earlier. Times that cannot fit in the window after the push are dropped.
    """
    if not times or catch_up <= timedelta(0):
        return list(times)
    floor = _clamp_into_window(now + catch_up, start, end)
    out: List[datetime] = []
    pushed = False
    for t in _dedupe_sorted_times(times):
        if t < now:
            out.append(t)
            continue
        if not pushed:
            t = max(t, floor)
            pushed = True
        if out:
            min_next = out[-1] + MIN_MEAL_GAP
            if t < min_next:
                t = min_next
        t = _clamp_into_window(t, start, end)
        if out and t < out[-1] + MIN_MEAL_GAP:
            continue
        if t > end:
            continue
        out.append(t)
    return out


def _resolve_eat_times(
    n: int,
    *,
    now: datetime,
    start: datetime,
    end: datetime,
    tz,
    eat_slots: Optional[Sequence[Any]] = None,
    consumed: Any = None,
    targets: Any = None,
) -> List[datetime]:
    """n clock times inside the remaining eating window. Never invents food.

    Morning plans keep the stable ~12:00 / 15:30 / 19:00 hinges (or caller
    ``eat_slots``). After a slot is past (20 min grace), drop it and schedule
    only remaining-day meals — do not keep noon as upcoming at 3 PM.
    Ahead of calorie pace: first upcoming meal moves to
    ``now + (ahead / target) * window``; behind / on-pace unchanged.
    """
    if n <= 0:
        return []
    catch_up = _catch_up_delay(
        consumed=consumed, targets=targets, now=now, start=start, end=end
    )

    def _finish(times: Sequence[datetime]) -> List[datetime]:
        return _apply_pace_delay(
            times, now=now, start=start, end=end, catch_up=catch_up
        )

    day = now.replace(second=0, microsecond=0)
    horizon = _slot_horizon(now)
    parsed: List[datetime] = []
    for raw in eat_slots or []:
        dt = _parse_eat_slot(raw, tz, day)
        if dt is None:
            continue
        parsed.append(_clamp_into_window(dt, start, end))
    parsed = _dedupe_sorted_times(parsed)

    if n == 1 and not parsed:
        cands = _dedupe_sorted_times(
            [
                _clamp_into_window(
                    day.replace(hour=h, minute=m, second=0, microsecond=0),
                    start,
                    end,
                )
                for h, m in DEFAULT_SLOT_HM
            ]
        )
        upcoming = [t for t in cands if t >= horizon]
        if upcoming:
            return _finish([upcoming[0]])
        if start <= now < end:
            return _finish([_clamp_into_window(now, start, end)])
        return []

    if parsed:
        chosen = parsed[:n]
    else:
        chosen = _dedupe_sorted_times(
            [
                _clamp_into_window(
                    day.replace(hour=h, minute=m, second=0, microsecond=0),
                    start,
                    end,
                )
                for h, m in _default_slot_hms(n)
            ]
        )
    valid = [t for t in chosen if t >= horizon]
    if len(valid) >= n:
        return _finish(valid[:n])

    lo = now if now > start else start
    grace_kept = [t for t in valid if t < now]
    if lo >= end:
        return _finish(valid[:n])

    # Two or more hinges still in the remaining day: keep them, fill gaps
    # only when the extra time is not stacked on a kept hinge.
    if len(valid) >= 2:
        need = n - len(valid)
        spaced = _space_in_range(need, lo, end, avoid=valid) if need else []
        kept = _clamp_gap_times(valid, n, end)
        extra: List[datetime] = []
        for t in spaced:
            if len(kept) + len(extra) >= n:
                break
            if t > end:
                continue
            if any(abs((t - k).total_seconds()) < MIN_MEAL_GAP.total_seconds() for k in kept + extra):
                continue
            extra.append(t)
        return _finish(_dedupe_sorted_times(kept + extra)[:n])

    # Most default slots are past: re-time remaining meals across the rest
    # of the window. Keep an in-progress (grace) slot so it does not vanish.
    need = n - len(grace_kept)
    spaced = _space_in_range(need, lo, end, avoid=grace_kept) if need else []
    return _finish(_clamp_gap_times(list(grace_kept) + spaced, n, end))


def _serving_unit_count(items: Sequence[dict]) -> int:
    total = 0
    for it in items:
        pg = it.get("portion_g")
        sg = it.get("serving_g")
        if pg is not None and sg is not None:
            try:
                portion = float(pg)
                base = float(sg)
            except (TypeError, ValueError):
                portion, base = 0.0, 0.0
            if portion > 0 and base > 0:
                total += max(1, int(portion / base))
                continue
        n = float(it.get("servings") or 1)
        if n <= 0:
            total += 1
            continue
        if abs(n - round(n)) < 1e-9:
            total += max(1, int(round(n)))
        else:
            total += 1
    return total


def _split_grams(total_g: float, n: int) -> List[float]:
    """Split a continuous portion into n gram chunks that sum to total_g.

    Prefer ~5g-aligned chunks when the parent portion is 5g-aligned.
    """
    total = max(1, int(round(float(total_g))))
    n = max(1, int(n))
    if n == 1:
        return [float(total)]
    step = 5 if total >= MIN_PORTION_G and total % 5 == 0 else 1
    base = total // n
    if step > 1:
        base = (base // step) * step
    leftover = total - base * n
    chunks = [float(base) for _ in range(n)]
    i = 0
    while leftover >= step:
        chunks[i % n] += step
        leftover -= step
        i += 1
    if leftover:
        chunks[-1] += leftover
    return [c if c > 0 else 1.0 for c in chunks]


def _desired_slot_count(items: Sequence[dict], remaining: Optional[dict]) -> int:
    units = _serving_unit_count(items)
    if units <= 0:
        return 0
    rem = remaining or {}
    cal = float(rem.get("calories") or 0)
    prot = float(rem.get("protein_g") or 0)
    if cal >= 1400 or prot >= 120:
        want = 4
    elif cal >= 800 or prot >= 70:
        want = 3
    elif cal >= 350 or prot >= 30:
        want = 2
    else:
        want = 1
    return max(1, min(4, units, want))


def _expand_serving_units(items: Sequence[dict]) -> List[dict]:
    """Split large portions so one in-stock food can land in several slots.

    Continuous grams split into ~serving_g chunks (equal grams, not whole-serving
    only). Foods without mass keep the whole-serving split.
    """
    units: List[dict] = []
    for it in items:
        pg = it.get("portion_g")
        sg = it.get("serving_g")
        try:
            portion = float(pg) if pg is not None else 0.0
            base_g = float(sg) if sg is not None else 0.0
        except (TypeError, ValueError):
            portion, base_g = 0.0, 0.0
        if portion > 0 and base_g > 0:
            count = max(1, int(portion / base_g))
            if count <= 1:
                units.append(deepcopy(it))
                continue
            chunks = _split_grams(portion, count)
            total = float(portion) or 1.0
            for cg in chunks:
                unit = deepcopy(it)
                scale = cg / total
                for k in _MACRO_KEYS:
                    unit[k] = round(float(it.get(k) or 0) * scale, 1)
                for mk in ("fiber_g", "sugar_g", "sodium_mg"):
                    if it.get(mk) is not None:
                        unit[mk] = round(float(it.get(mk) or 0) * scale, 1 if mk != "sodium_mg" else 0)
                unit["portion_g"] = float(int(round(cg)))
                unit["servings"] = round(cg / base_g, 2)
                unit["serving_label"] = format_portion_label(
                    serving_g=base_g,
                    servings=cg / base_g,
                    serving_label=str(it.get("serving_label") or ""),
                )
                units.append(unit)
            continue
        n = float(it.get("servings") or 1)
        whole = n >= 1 and abs(n - round(n)) < 1e-9
        count = int(round(n)) if whole else 1
        if count <= 1:
            units.append(deepcopy(it))
            continue
        base = deepcopy(it)
        for k in _MACRO_KEYS:
            base[k] = round(float(it.get(k) or 0) / count, 1)
        for mk in ("fiber_g", "sugar_g", "sodium_mg"):
            if it.get(mk) is not None:
                base[mk] = round(float(it.get(mk) or 0) / count, 1 if mk != "sodium_mg" else 0)
        if it.get("portion_g") is not None:
            try:
                base["portion_g"] = round(float(it["portion_g"]) / count)
            except (TypeError, ValueError):
                pass
        elif it.get("serving_g") is not None:
            try:
                base["portion_g"] = round(float(it["serving_g"]))
            except (TypeError, ValueError):
                pass
        base["servings"] = 1
        if base.get("serving_g") is not None:
            base["serving_label"] = format_portion_label(
                serving_g=float(base["serving_g"]),
                servings=1,
                serving_label=str(it.get("serving_label") or ""),
            )
        units.extend(deepcopy(base) for _ in range(count))
    return units


def _chunk_units(units: Sequence[dict], n_slots: int) -> List[List[dict]]:
    """Deal servings across slots so a 3× protein pick is not one lunch blob."""
    if not units:
        return []
    n = max(1, min(int(n_slots), len(units)))
    slots: List[List[dict]] = [[] for _ in range(n)]
    for i, unit in enumerate(units):
        slots[i % n].append(unit)
    return [part for part in slots if part]


def _bucket_meals(
    items: List[dict],
    remaining: Optional[dict] = None,
    *,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
    window_start: Any = None,
    window_end: Any = None,
    eat_slots: Optional[Sequence[Any]] = None,
    sleep_battery: Optional[dict] = None,
    consumed: Any = None,
    targets: Any = None,
) -> List[dict]:
    """Split in-stock plan items into 1–4 timed buckets. No empty hinges."""
    if not items:
        return []
    tz, resolved_tz = _meal_tz(tz_name)
    if now is None:
        from .timeutil import local_now

        now = local_now(resolved_tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    start, end = _eating_window_bounds(
        now,
        tz,
        resolved_tz,
        window_start=window_start,
        window_end=window_end,
        sleep_battery=sleep_battery,
    )
    cap = _remaining_meal_capacity(now, start, end)
    if cap <= 0:
        return []
    n_slots = _desired_slot_count(items, remaining)
    units = _expand_serving_units(items)
    n_slots = max(1, min(n_slots, cap, len(units)))
    times = _resolve_eat_times(
        n_slots,
        now=now,
        start=start,
        end=end,
        tz=tz,
        eat_slots=eat_slots,
        consumed=consumed,
        targets=targets,
    )
    horizon = _slot_horizon(now)
    times = [t for t in times if t >= horizon]
    if not times:
        return []
    n_slots = min(n_slots, len(times), len(units))
    chunks = _chunk_units(units, n_slots)
    times = times[: len(chunks)]

    meals: List[dict] = []
    upcoming_i = 0
    for i, part in enumerate(chunks):
        collapsed = _collapse_plan_items(list(part))
        sub = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
        for it in collapsed:
            for k in sub:
                sub[k] += float(it.get(k) or 0)
        eat_at = times[i]
        label = UPCOMING_MEAL_LABELS[min(upcoming_i, len(UPCOMING_MEAL_LABELS) - 1)]
        upcoming_i += 1
        meals.append(
            {
                "label": label,
                "eat_at": eat_at.isoformat(timespec="seconds"),
                "eat_at_label": _clock_label(eat_at),
                "timezone": resolved_tz,
                "items": collapsed,
                "totals": {k: round(v, 1) for k, v in sub.items()},
            }
        )
    return meals


def add_ingredient(inventory: dict, raw: dict) -> dict:
    require_serving_grams_or_raise(raw if isinstance(raw, dict) else {})
    inv = deepcopy(inventory) if inventory else {"ingredients": []}
    ing = normalize_ingredient(raw)
    ingredients = inv.setdefault("ingredients", [])
    # replace if same id or same name
    for i, existing in enumerate(ingredients):
        if existing.get("id") == ing["id"] or existing.get("name", "").lower() == ing[
            "name"
        ].lower():
            ingredients[i] = ing
            break
    else:
        ingredients.append(ing)
    inv["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return inv


def remove_ingredient(inventory: dict, ingredient_id: str = "", name: str = "") -> dict:
    inv = deepcopy(inventory) if inventory else {"ingredients": []}
    ingredients = inv.get("ingredients") or []
    nid = (ingredient_id or "").strip().lower()
    nname = (name or "").strip().lower()
    new_list = []
    removed = False
    for existing in ingredients:
        eid = str(existing.get("id") or "").lower()
        ename = str(existing.get("name") or "").lower()
        if (nid and eid == nid) or (nname and ename == nname):
            removed = True
            continue
        new_list.append(existing)
    if not removed:
        raise ValueError("ingredient not found")
    inv["ingredients"] = new_list
    inv["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return inv


def set_stock(inventory: dict, ingredient_id: str, stock: str) -> dict:
    s = str(stock or "").strip().lower()
    if s not in VALID_STOCK:
        raise ValueError("stock must be in, low, or out")
    inv = deepcopy(inventory) if inventory else {"ingredients": []}
    found = False
    want = str(ingredient_id or "").strip().lower()
    for existing in inv.get("ingredients") or []:
        if str(existing.get("id") or "").strip().lower() == want:
            existing["stock"] = s
            existing["in_stock"] = s != STOCK_OUT
            found = True
            break
    if not found:
        raise ValueError("ingredient not found")
    inv["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return inv


def set_in_stock(inventory: dict, ingredient_id: str, in_stock: bool) -> dict:
    """Bool path: True → in, False → out. Agent token and quest complete use this."""
    return set_stock(
        inventory,
        ingredient_id,
        STOCK_IN if in_stock else STOCK_OUT,
    )


def update_ingredient(inventory: dict, raw: dict) -> dict:
    """Edit an existing inventory row. Never invents a new item.

    Requires ``id``. Unknown / missing id raises ValueError (honest error).
    Same add-form fields are overlaid; stable id and in_stock stay unless sent.
    """
    inv = deepcopy(inventory) if inventory else {"ingredients": []}
    iid = str((raw or {}).get("id") or "").strip()
    if not iid:
        raise ValueError("ingredient id required")
    want = iid.lower()
    ingredients = inv.setdefault("ingredients", [])
    idx = None
    for i, existing in enumerate(ingredients):
        if str(existing.get("id") or "").strip().lower() == want:
            idx = i
            break
    if idx is None:
        raise ValueError("ingredient not found")
    existing = dict(ingredients[idx])
    overlay = {
        "id": existing.get("id") or iid,
        "name": raw.get("name", existing.get("name")),
        "category": raw.get("category", existing.get("category")),
        "serving_label": (
            raw["serving_label"] if "serving_label" in raw else existing.get("serving_label")
        ),
        "calories": raw.get("calories", existing.get("calories")),
        "protein_g": raw.get("protein_g", existing.get("protein_g")),
        "carbs_g": raw.get("carbs_g", existing.get("carbs_g")),
        "fat_g": raw.get("fat_g", existing.get("fat_g")),
        "notes": raw.get("notes", existing.get("notes", "")),
    }
    if "stock" in raw and raw.get("stock") is not None and str(raw.get("stock")).strip() != "":
        overlay["stock"] = raw.get("stock")
        if "in_stock" in raw:
            overlay["in_stock"] = raw.get("in_stock")
        else:
            overlay["in_stock"] = existing.get("in_stock", True)
    elif "in_stock" in raw:
        overlay["in_stock"] = raw.get("in_stock")
    else:
        overlay["in_stock"] = existing.get("in_stock", True)
        if existing.get("stock"):
            overlay["stock"] = existing.get("stock")
    if "serving_g" in raw:
        overlay["serving_g"] = raw.get("serving_g")
    elif existing.get("serving_g") is not None:
        overlay["serving_g"] = existing.get("serving_g")
    require_serving_grams_or_raise(overlay)
    ing = normalize_ingredient(overlay)
    # Keep the existing id so a rename does not mint a second row.
    ing["id"] = str(existing.get("id") or iid)
    ingredients[idx] = ing
    inv["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return inv


def update_targets(raw: dict) -> dict:
    t = normalize_targets(raw)
    t["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if raw.get("notes") is not None:
        t["notes"] = str(raw.get("notes") or "")
    return t


# Curated cutting/recomp staples for smart "add to inventory" suggestions.
# Macros are per serving_g (weighable). serving_label is secondary/prep note.
STAPLE_CATALOG: List[dict] = [
    {
        "id": "chicken-breast",
        "name": "Chicken breast",
        "category": "protein",
        "serving_g": 170,
        "serving_label": "170g cooked",
        "calories": 280,
        "protein_g": 52,
        "carbs_g": 0,
        "fat_g": 6,
    },
    {
        "id": "turkey-breast",
        "name": "Turkey breast",
        "category": "protein",
        "serving_g": 170,
        "serving_label": "170g cooked",
        "calories": 250,
        "protein_g": 50,
        "carbs_g": 0,
        "fat_g": 4,
    },
    {
        "id": "nonfat-greek-yogurt",
        "name": "Greek yogurt (nonfat)",
        "category": "protein",
        "serving_g": 360,
        "serving_label": "360g",
        "calories": 200,
        "protein_g": 30,
        "carbs_g": 12,
        "fat_g": 0,
    },
    {
        "id": "cottage-cheese-lowfat",
        "name": "Cottage cheese (low-fat)",
        "category": "protein",
        "serving_g": 226,
        "serving_label": "226g",
        "calories": 180,
        "protein_g": 28,
        "carbs_g": 8,
        "fat_g": 2.5,
    },
    {
        "id": "whey-protein",
        "name": "Whey protein",
        "category": "protein",
        "serving_g": 30,
        "serving_label": "30g dry",
        "calories": 120,
        "protein_g": 24,
        "carbs_g": 3,
        "fat_g": 1,
    },
    {
        "id": "egg-whites",
        "name": "Egg whites",
        "category": "protein",
        "serving_g": 243,
        "serving_label": "243g",
        "calories": 125,
        "protein_g": 26,
        "carbs_g": 2,
        "fat_g": 0,
    },
    {
        "id": "canned-tuna",
        "name": "Canned tuna (in water)",
        "category": "protein",
        "serving_g": 142,
        "serving_label": "142g drained",
        "calories": 120,
        "protein_g": 26,
        "carbs_g": 0,
        "fat_g": 1,
    },
    {
        "id": "lean-ground-turkey",
        "name": "Lean ground turkey (93%)",
        "category": "protein",
        "serving_g": 170,
        "serving_label": "170g cooked",
        "calories": 260,
        "protein_g": 42,
        "carbs_g": 0,
        "fat_g": 10,
    },
    {
        "id": "oats",
        "name": "Oats",
        "category": "carb",
        "serving_g": 40,
        "serving_label": "40g dry",
        "calories": 150,
        "protein_g": 5,
        "carbs_g": 27,
        "fat_g": 3,
    },
    {
        "id": "brown-rice",
        "name": "Brown rice",
        "category": "carb",
        "serving_g": 195,
        "serving_label": "195g cooked",
        "calories": 215,
        "protein_g": 5,
        "carbs_g": 45,
        "fat_g": 2,
    },
    {
        "id": "sweet-potato",
        "name": "Sweet potato",
        "category": "carb",
        "serving_g": 130,
        "serving_label": "130g",
        "calories": 110,
        "protein_g": 2,
        "carbs_g": 26,
        "fat_g": 0,
    },
    {
        "id": "black-beans",
        "name": "Black beans",
        "category": "carb",
        "serving_g": 172,
        "serving_label": "172g cooked",
        "calories": 220,
        "protein_g": 15,
        "carbs_g": 40,
        "fat_g": 1,
    },
    {
        "id": "broccoli",
        "name": "Broccoli",
        "category": "veg",
        "serving_g": 180,
        "serving_label": "180g",
        "calories": 60,
        "protein_g": 5,
        "carbs_g": 12,
        "fat_g": 0.5,
    },
    {
        "id": "spinach",
        "name": "Spinach",
        "category": "veg",
        "serving_g": 90,
        "serving_label": "90g raw",
        "calories": 20,
        "protein_g": 2,
        "carbs_g": 3,
        "fat_g": 0,
    },
    {
        "id": "berries-mixed",
        "name": "Mixed berries",
        "category": "carb",
        "serving_g": 140,
        "serving_label": "140g",
        "calories": 70,
        "protein_g": 1,
        "carbs_g": 17,
        "fat_g": 0.5,
    },
    {
        "id": "olive-oil",
        "name": "Olive oil",
        "category": "fat",
        "serving_g": 14,
        "serving_label": "14g",
        "calories": 120,
        "protein_g": 0,
        "carbs_g": 0,
        "fat_g": 14,
    },
    {
        "id": "avocado",
        "name": "Avocado",
        "category": "fat",
        "serving_g": 68,
        "serving_label": "68g",
        "calories": 120,
        "protein_g": 1.5,
        "carbs_g": 6,
        "fat_g": 11,
    },
    {
        "id": "chia-seeds",
        "name": "Chia seeds",
        "category": "carb",
        "serving_g": 28,
        "serving_label": "28g (2 tbsp)",
        "calories": 138,
        "protein_g": 5,
        "carbs_g": 12,
        "fat_g": 9,
        "fiber_g": 10,
    },
    {
        "id": "ground-flaxseed",
        "name": "Ground flaxseed",
        "category": "carb",
        "serving_g": 24,
        "serving_label": "24g (3 tbsp)",
        "calories": 130,
        "protein_g": 5,
        "carbs_g": 7,
        "fat_g": 9,
        "fiber_g": 8,
    },
    {
        "id": "lentils",
        "name": "Lentils",
        "category": "carb",
        "serving_g": 198,
        "serving_label": "198g cooked",
        "calories": 230,
        "protein_g": 18,
        "carbs_g": 40,
        "fat_g": 1,
        "fiber_g": 16,
    },
    {
        "id": "banana",
        "name": "Banana",
        "category": "carb",
        "serving_label": "1 medium",
        "calories": 105,
        "protein_g": 1.3,
        "carbs_g": 27,
        "fat_g": 0.4,
        "fiber_g": 3.1,
    },
]


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _names_overlap(a: str, b: str) -> bool:
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    ta, tb = set(na.split()), set(nb.split())
    # Ignore tiny tokens
    ta = {t for t in ta if len(t) > 2}
    tb = {t for t in tb if len(t) > 2}
    if not ta or not tb:
        return False
    return len(ta & tb) >= min(2, len(ta), len(tb))


def _protein_density(ing: dict) -> float:
    cal = float(ing.get("calories") or 0) or 1.0
    return float(ing.get("protein_g") or 0) / cal


def inventory_gap_role(ing: dict) -> Optional[str]:
    """``lean_protein`` or ``veg_fiber``. Shakes/supplements are not protected fillers."""
    if not isinstance(ing, dict):
        return None
    if is_shake_or_powder(ing):
        return None
    blob = _ing_blob(ing)
    if any(
        tok in blob
        for tok in (
            "vitamin",
            "multivitamin",
            "supplement",
            "gummy",
            "capsule",
            "tablet",
            "probiotic",
        )
    ):
        return None
    if is_veg_or_fruit(ing) or estimated_fiber_g(ing) >= 3:
        return "veg_fiber"
    if _protein_density(ing) >= 0.08:
        return "lean_protein"
    return None


def suggested_qty_for_item(item: dict) -> dict:
    """Grams when weighable; otherwise whole servings (#503 / #504)."""
    sg = _ingredient_serving_g(item) if isinstance(item, dict) else None
    if sg is not None and float(sg) > 0:
        amt = round(float(sg), 1)
        return {
            "amount": amt,
            "unit": "g",
            "label": f"{int(round(amt))}g",
            "portion_g": amt,
            "servings": None,
        }
    label = str((item or {}).get("serving_label") or "1 serving").strip() or "1 serving"
    return {
        "amount": 1,
        "unit": "servings",
        "label": label,
        "portion_g": None,
        "servings": 1,
    }


def _as_food_dict(row: Any) -> Optional[dict]:
    if hasattr(row, "to_dict"):
        d = row.to_dict()
        return d if isinstance(d, dict) else None
    if isinstance(row, dict):
        return row
    return None


# Purpose roles for #707 pantry-rounding. Missing role → suggest; filled role
# without a diet gap is novelty and must not surface.
PURPOSE_ROLES = (
    "fiber_booster",
    "produce",
    "whole_protein",
    "quality_fat",
    "slow_carb",
)
_PURPOSE_ROLE_WHY = {
    "fiber_booster": (
        "No concentrated fiber staple in pantry; this improves daily fiber density."
    ),
    "produce": (
        "Pantry produce is thin; this improves fiber/volume of the dietary split."
    ),
    "whole_protein": (
        "Pantry lacks a whole-food protein staple; this covers that role."
    ),
    "quality_fat": (
        "No unsaturated-fat staple stocked; this improves the fat-quality split."
    ),
    "slow_carb": (
        "Limited fiber-containing carb staples; this fills remaining carbs without junk."
    ),
}
DIET_GAP_WINDOW_DAYS = 7
DIET_HIT_FRAC = 0.85
DIET_FAT_HIT_FRAC = 0.70
DIET_FIBER_KNOWN_MIN_DAYS = 3
DIET_SHAKE_SHARE = 0.40


def staple_purpose_roles(ing: dict) -> List[str]:
    """Pantry-role tags used to round a stocked kitchen without novelty adds."""
    if not isinstance(ing, dict):
        return []
    if is_shake_or_powder(ing):
        return []
    blob = _ing_blob(ing)
    if any(
        tok in blob
        for tok in (
            "vitamin",
            "multivitamin",
            "supplement",
            "gummy",
            "capsule",
            "tablet",
            "probiotic",
        )
    ):
        return []
    roles: List[str] = []
    fiber = estimated_fiber_g(ing)
    if fiber >= 6.0 or any(
        tok in blob
        for tok in ("chia", "flax", "lentil", "black bean", "kidney bean", "psyllium")
    ):
        roles.append("fiber_booster")
    elif "bean" in blob and "coffee" not in blob:
        roles.append("fiber_booster")
    if is_veg_or_fruit(ing):
        roles.append("produce")
    if _protein_density(ing) >= 0.08:
        roles.append("whole_protein")
    cat = str(ing.get("category") or "").strip().lower()
    if cat == "fat" or any(tok in blob for tok in ("olive oil", "avocado")):
        roles.append("quality_fat")
    if cat == "carb" and fiber >= 2.0 and "fiber_booster" not in roles:
        roles.append("slow_carb")
    return roles


def diet_purpose_gaps(
    targets: Optional[dict] = None,
    food_logs: Optional[Sequence[Any]] = None,
    *,
    window_days: int = DIET_GAP_WINDOW_DAYS,
) -> dict:
    """Recurring intake gaps vs targets. Unknown micros stay unknown (not 0).

    Log frequency of a *food* is never a positive add signal (#502). This only
    asks whether protein / fiber / fat / sugar / shake-share miss the target.
    """
    targets = normalize_targets(targets or {})
    p_tgt = float(targets.get("protein_g") or 0)
    fat_tgt = float(targets.get("fat_g") or 0)
    fiber_tgt = resolve_micro_target(
        targets, None, "fiber_g", fallback=SOFT_FIBER_TARGET_G
    )
    sugar_tgt = resolve_micro_target(targets, None, "sugar_g")
    out: Dict[str, Any] = {
        "days": 0,
        "protein_short": False,
        "fiber_short": False,
        "fat_short": False,
        "sugar_high": False,
        "shake_heavy": False,
        "protein_avg": None,
        "protein_target": p_tgt or None,
        "protein_short_g": None,
        "fiber_avg": None,
        "fiber_target": fiber_tgt,
        "fiber_short_g": None,
        "fiber_known_days": 0,
        "fat_avg": None,
        "fat_target": fat_tgt or None,
        "fat_short_g": None,
        "sugar_avg": None,
        "sugar_target": sugar_tgt,
        "shake_share": None,
    }
    rows = [d for d in (_as_food_dict(f) for f in (food_logs or [])) if d]
    if not rows:
        return out

    from .nutrition_micros import micros_from_nutrients

    by_day: Dict[str, dict] = {}
    for d in rows:
        date = str(d.get("date") or "")[:10]
        if not date:
            continue
        b = by_day.setdefault(
            date,
            {
                "calories": 0.0,
                "protein_g": 0.0,
                "carbs_g": 0.0,
                "fat_g": 0.0,
                "fiber_g": 0.0,
                "sugar_g": 0.0,
                "has_fiber": False,
                "has_sugar": False,
                "shake_protein_g": 0.0,
            },
        )
        for k in ("calories", "protein_g", "carbs_g", "fat_g"):
            try:
                b[k] += float(d.get(k) or 0)
            except (TypeError, ValueError):
                pass
        micros = micros_from_nutrients(d.get("nutrients"))
        fiber = micros.get("fiber_g")
        if fiber is None:
            fiber = _optional_micro_float(d.get("fiber_g"))
        if fiber is not None:
            b["fiber_g"] += float(fiber)
            b["has_fiber"] = True
        sugar = micros.get("sugar_g")
        if sugar is None:
            sugar = _optional_micro_float(d.get("sugar_g"))
        if sugar is not None:
            b["sugar_g"] += float(sugar)
            b["has_sugar"] = True
        try:
            p = float(d.get("protein_g") or 0)
        except (TypeError, ValueError):
            p = 0.0
        if is_shake_or_powder(d):
            b["shake_protein_g"] += p

    if not by_day:
        return out

    dates = sorted(by_day)
    window = dates[-max(1, int(window_days)) :]
    n = len(window)
    out["days"] = n

    def _mean(key: str) -> float:
        return sum(float(by_day[d][key]) for d in window) / n

    p_avg = _mean("protein_g")
    out["protein_avg"] = round(p_avg, 1)
    if p_tgt > 0 and p_avg < p_tgt * DIET_HIT_FRAC:
        out["protein_short"] = True
        out["protein_short_g"] = round(p_tgt - p_avg, 1)

    fiber_days = [d for d in window if by_day[d]["has_fiber"]]
    out["fiber_known_days"] = len(fiber_days)
    if fiber_days and fiber_tgt:
        f_avg = sum(by_day[d]["fiber_g"] for d in fiber_days) / len(fiber_days)
        out["fiber_avg"] = round(f_avg, 1)
        min_known = min(DIET_FIBER_KNOWN_MIN_DAYS, n)
        if len(fiber_days) >= min_known and f_avg < float(fiber_tgt) * DIET_HIT_FRAC:
            out["fiber_short"] = True
            out["fiber_short_g"] = round(float(fiber_tgt) - f_avg, 1)

    fat_avg = _mean("fat_g")
    out["fat_avg"] = round(fat_avg, 1)
    if fat_tgt > 0 and fat_avg < fat_tgt * DIET_FAT_HIT_FRAC:
        out["fat_short"] = True
        out["fat_short_g"] = round(fat_tgt - fat_avg, 1)

    sugar_days = [d for d in window if by_day[d]["has_sugar"]]
    if sugar_days and sugar_tgt:
        s_avg = sum(by_day[d]["sugar_g"] for d in sugar_days) / len(sugar_days)
        out["sugar_avg"] = round(s_avg, 1)
        if s_avg > float(sugar_tgt):
            out["sugar_high"] = True

    prot_sum = sum(by_day[d]["protein_g"] for d in window)
    shake_sum = sum(by_day[d]["shake_protein_g"] for d in window)
    if prot_sum > 0:
        share = shake_sum / prot_sum
        out["shake_share"] = round(share, 3)
        out["shake_heavy"] = share >= DIET_SHAKE_SHARE
    return out


def _find_inventory_match(inventory: dict, name: str, iid: str = "") -> Optional[dict]:
    want_id = (iid or "").strip().lower()
    for raw in inventory.get("ingredients") or []:
        if want_id and str(raw.get("id") or "").lower() == want_id:
            return raw
        if _names_overlap(str(raw.get("name") or ""), name):
            return raw
    return None


def suggest_inventory_staples(
    inventory: dict,
    targets: Optional[dict] = None,
    food_logs: Optional[Sequence[Any]] = None,
    consumed: Optional[dict] = None,
    max_suggestions: int = 8,
    catalog: Optional[Sequence[dict]] = None,
) -> dict:
    """Purpose-based restock / add proposals (#502 + #707).

    Ranking: restocks first, then catalog adds that close a stated purpose —
    remaining macros, diet-window shortfalls, shake-as-filler risk, or a
    missing pantry role (fiber booster / produce / whole protein / quality
    fat / slow carb). Log-frequency is **not** a positive signal. Missing
    catalog SKUs with no purpose are novelty and are skipped. Candidates
    come from ``catalog`` / ``STAPLE_CATALOG``, not “foods Chris logged.”
    Suggestions are proposals — never written as stock-on-hand until accept.
    Recomputed on every dashboard load (inventory / consumed / plan change).
    """
    targets = normalize_targets(targets or {})
    logs = list(food_logs or [])
    consumed = consumed or {}
    catalog_rows = list(catalog) if catalog is not None else list(STAPLE_CATALOG)
    suggestions: List[dict] = []
    seen_keys: set = set()

    def _key(name: str, iid: str = "") -> str:
        return (iid or _slug(name)).lower()

    def _push(item: dict) -> None:
        k = _key(item.get("name") or "", str(item.get("id") or ""))
        if k in seen_keys:
            return
        # Also skip near-duplicate names already queued
        for existing in suggestions:
            if _names_overlap(existing.get("name") or "", item.get("name") or ""):
                return
        seen_keys.add(k)
        suggestions.append(item)

    ingredients = [normalize_ingredient(i) for i in (inventory.get("ingredients") or [])]
    stocked = [i for i in ingredients if is_in_stock(i)]
    restock_needed = [i for i in ingredients if needs_restock(i)]

    # --- 1) Restock low + out items (not `not in_stock` — low is still cookable) ---
    for ing in restock_needed:
        dens = _protein_density(ing)
        low = normalize_stock(ing) == STOCK_LOW
        # Base high so restocks beat net-new catalog noise
        score = 75.0 + dens * 80.0
        if low:
            if dens >= 0.08:
                reason = "Running low and high protein density — restock before empty."
                score += 18
            elif (ing.get("category") or "") == "veg":
                reason = "Running low on veg — restock for volume/fiber."
                score += 10
            else:
                reason = "Running low — restock before empty."
                score += 8
        elif dens >= 0.08:
            reason = "Out of stock and high protein density — restock for meal plans."
            score += 20
        elif (ing.get("category") or "") == "veg":
            reason = "Out of stock veg — restock for volume/fiber."
            score += 12
        else:
            reason = "Marked out of stock — restock if you still use it."
            score += 10
        qty = suggested_qty_for_item(ing)
        restock_row = {
            **ing,
            "action": "restock",
            "reason": reason,
            "need": reason,
            "score": round(score, 1),
            "source": "inventory",
            "proposal": True,
            "suggested_qty": qty,
        }
        restock_row["venue"] = venue_for_item(restock_row)
        _push(restock_row)

    # Log counts are a soft *negative* for low-quality over-representation only.
    # Never a positive add signal (#502 AC1).
    log_counts: Dict[str, int] = {}
    for f in logs:
        d = _as_food_dict(f)
        if not d:
            continue
        name = str(d.get("name") or "").strip()
        if not name or name.lower() in ("logged food", "unknown"):
            continue
        log_counts[name] = log_counts.get(name, 0) + 1

    def _log_hits(name: str) -> int:
        n = 0
        for ln, c in log_counts.items():
            if _names_overlap(name, ln):
                n += c
        return n

    # --- 2) Catalog staples ranked by nutrition/ops need (not log frequency) ---
    tgt_p = float(targets.get("protein_g") or 0)
    rem_p = max(0.0, tgt_p - float(consumed.get("protein_g") or 0))
    stocked_high_p = [i for i in stocked if _protein_density(i) >= 0.08]
    stocked_whole_p = [i for i in stocked_high_p if not is_shake_or_powder(i)]
    veg_n = sum(1 for i in stocked if is_veg_or_fruit(i))
    fiber_stocked = sum(estimated_fiber_g(i) for i in stocked)
    shake_n = sum(1 for i in stocked if is_shake_or_powder(i))
    protein_role_open = len(stocked_whole_p) < 2
    # Remaining macros today are cook-what-you-have, not a shopping hole, once
    # two whole-food proteins are already in the pantry (#709).
    protein_gap = protein_role_open
    fiber_tgt = resolve_micro_target(targets, None, "fiber_g", fallback=SOFT_FIBER_TARGET_G)
    fiber_gap = veg_n < 1 or fiber_stocked < float(fiber_tgt or SOFT_FIBER_TARGET_G)
    shake_heavy = shake_n > 0 and protein_role_open
    diet = diet_purpose_gaps(targets, logs)
    stocked_roles: set = set()
    for i in stocked:
        stocked_roles.update(staple_purpose_roles(i))
    missing_roles = [r for r in PURPOSE_ROLES if r not in stocked_roles]

    honesty: List[dict] = []
    if not catalog_rows:
        if not suggestions:
            summary = "No staple catalog — cannot propose adds (not inventing food)."
            honesty.append(
                {
                    "level": "warn",
                    "kind": "catalog_blocked",
                    "text": "Staple catalog empty — no add suggestions. Not inventing stock-on-hand.",
                }
            )
            return {
                "suggestions": [],
                "summary": summary,
                "count": 0,
                "ranking": "need",
                "log_frequency_positive": False,
                "refresh": "dashboard_load",
                "honesty": honesty,
            }

    for staple in catalog_rows:
        match = _find_inventory_match(
            inventory or {}, staple["name"], str(staple.get("id") or "")
        )
        if match and normalize_stock(match) == STOCK_IN:
            continue
        dens = _protein_density(staple)
        shake = is_shake_or_powder(staple)
        veg = is_veg_or_fruit(staple)
        fiber = estimated_fiber_g(staple)
        roles = staple_purpose_roles(staple)
        score = 8.0 + dens * 20.0
        reasons: List[str] = []
        purposes: List[str] = []
        if match and needs_restock(match):
            action = "restock"
            reasons.append(
                "Pantry hole — restock this staple so the meal plan can use it."
                if normalize_stock(match) == STOCK_OUT
                else "Running low — restock before empty so the plan stays non-shake."
            )
            purposes.append("restock")
            score += 28
            payload = {**normalize_ingredient(match)}
        else:
            action = "add"
            payload = {**staple}
            payload.pop("in_stock", None)
            payload.pop("stock", None)

        if diet.get("protein_short") and dens >= 0.08 and not shake and protein_role_open:
            short = diet.get("protein_short_g")
            avg = diet.get("protein_avg")
            days = diet.get("days") or 0
            reasons.append(
                f"Protein ~{int(avg)}g/day vs {int(tgt_p)}g target over {int(days)}d"
                f" (short ~{int(short)}g); this closes that gap."
            )
            purposes.append("diet_protein")
            score += 36
        elif protein_gap and dens >= 0.08 and not shake:
            reasons.append(
                f"Closes remaining protein (~{int(rem_p)}g of {int(tgt_p)}g) with whole food, not powder."
            )
            purposes.append("protein_gap")
            score += 32
        if diet.get("fiber_short") and (veg or fiber >= 3) and not shake:
            short = diet.get("fiber_short_g")
            avg = diet.get("fiber_avg")
            days = diet.get("fiber_known_days") or diet.get("days") or 0
            tgt_f = diet.get("fiber_target") or fiber_tgt or SOFT_FIBER_TARGET_G
            reasons.append(
                f"Fiber ~{int(avg)}g/day vs {int(tgt_f)}g target over {int(days)}d"
                f" (short ~{int(short)}g); this closes that gap."
            )
            purposes.append("diet_fiber")
            score += 40
        elif fiber_gap and (veg or fiber >= 3):
            reasons.append(
                f"Fills soft fiber / veg gap (target ~{int(fiber_tgt or SOFT_FIBER_TARGET_G)}g; pantry produce thin)."
            )
            purposes.append("fiber_gap")
            score += 36
            if shake:
                # Powder must not win a fiber/veg slot (#504 / #501).
                score -= 50
        diet_shake = bool(diet.get("shake_heavy"))
        if (shake_heavy or diet_shake) and dens >= 0.08 and not shake and protein_role_open:
            if diet_shake and diet.get("shake_share") is not None:
                pct = int(round(float(diet["shake_share"]) * 100))
                reasons.append(
                    f"~{pct}% of protein intake is powder; whole-food staple improves the split."
                )
            else:
                reasons.append("Whole-food protein so the plan is not shake-filled.")
            purposes.append("shake_split")
            score += 22
        if shake:
            if shake_heavy or diet_shake or not protein_gap:
                score -= 40
            elif protein_gap and len(stocked_whole_p) == 0:
                reasons.append("Powder only if whole-food protein is missing — last-resort protein.")
                purposes.append("protein_gap")
                score += 4
            else:
                score -= 12
        if diet.get("fat_short") and "quality_fat" in roles:
            short = diet.get("fat_short_g")
            avg = diet.get("fat_avg")
            days = diet.get("days") or 0
            tgt_fat = diet.get("fat_target") or float(targets.get("fat_g") or 0)
            reasons.append(
                f"Fat ~{int(avg)}g/day vs {int(tgt_fat)}g target over {int(days)}d"
                f" (short ~{int(short)}g); this improves the split."
            )
            purposes.append("diet_fat")
            score += 22
        if diet.get("sugar_high") and (veg or "fiber_booster" in roles) and not shake:
            reasons.append(
                "Sugar running over target most days; this improves the healthfulness of the split."
            )
            purposes.append("diet_sugar")
            score += 16
        carb_n = sum(1 for i in stocked if (i.get("category") or "") == "carb")
        if (staple.get("category") or "") == "carb" and carb_n < 2 and fiber >= 2 and not shake:
            reasons.append("Carb staple with fiber — helps fill remaining carbs without junk.")
            purposes.append("slow_carb")
            score += 10

        # Pantry-role hole (#707): only when this staple fills a *missing* role.
        # Duplicate proteins (turkey while chicken+yogurt are stocked) are novelty.
        for role in PURPOSE_ROLES:
            if role in roles and role in missing_roles:
                why = _PURPOSE_ROLE_WHY.get(role)
                if why and why not in reasons:
                    reasons.append(why)
                purposes.append(role)
                score += 18
                break

        hits = _log_hits(str(staple.get("name") or ""))
        if hits >= 3 and (shake or dens < 0.04):
            score -= 18

        if action == "add" and not reasons:
            continue
        if not reasons:
            reasons.append("Pantry hole for a cutting staple.")
            purposes.append("restock")
        qty = suggested_qty_for_item(payload)
        row = {
            **payload,
            "action": action,
            "reason": " ".join(reasons),
            "need": reasons[0],
            "score": round(score, 1),
            "source": "catalog",
            "proposal": True,
            "suggested_qty": qty,
            "purpose": purposes,
        }
        if qty.get("portion_g") is not None:
            row["portion_g"] = qty["portion_g"]
        row["venue"] = venue_for_item(row)
        _push(row)

    suggestions.sort(key=lambda x: (-float(x.get("score") or 0), x.get("name") or ""))
    limit = max(1, int(max_suggestions))
    restocks = [s for s in suggestions if s.get("action") == "restock"]
    adds = [s for s in suggestions if s.get("action") != "restock"]
    top: List[dict] = []
    top.extend(restocks[:limit])
    if len(top) < limit:
        top.extend(adds[: limit - len(top)])

    restock_n = sum(1 for s in top if s.get("action") == "restock")
    add_n = sum(1 for s in top if s.get("action") == "add")
    bits = []
    if restock_n:
        bits.append(f"{restock_n} restock")
    if add_n:
        bits.append(f"{add_n} add")
    if not top:
        summary = (
            "No purpose-based add/restock — restocks clear, diet hitting targets, "
            "and pantry already covers protein/fiber/produce/fat roles. "
            "Not inventing stock-on-hand."
        )
        honesty.append(
            {
                "level": "muted",
                "kind": "empty_suggestions",
                "text": summary,
            }
        )
    else:
        summary = (
            f"{len(top)} purpose-based suggestions ({', '.join(bits)}) from restocks, "
            f"diet gaps, and pantry-role holes. Every add answers why. "
            f"Log frequency is not a positive rank signal. Proposals only until you accept."
        )
    return {
        "suggestions": top,
        "summary": summary,
        "count": len(top),
        "ranking": "need",
        "log_frequency_positive": False,
        "refresh": "dashboard_load",
        "honesty": honesty,
    }


def suggest_inventory_removals(
    inventory: dict,
    targets: Optional[dict] = None,
    food_logs: Optional[Sequence[Any]] = None,
    max_suggestions: int = 6,
    paired_adds: Optional[Sequence[dict]] = None,
) -> dict:
    """Need-based removal proposals. Rare-log alone is never enough (#502 AC4).

    Never drop the last unique lean-protein or veg-fiber staple without a
    simultaneous add that covers that role (#504 AC1).
    """
    targets = normalize_targets(targets or {})
    ingredients = [normalize_ingredient(i) for i in (inventory.get("ingredients") or [])]
    honesty: List[dict] = []
    if not ingredients:
        summary = "No inventory items to review."
        honesty.append(
            {
                "level": "muted",
                "kind": "empty_removals",
                "text": summary,
            }
        )
        return {
            "suggestions": [],
            "summary": summary,
            "count": 0,
            "honesty": honesty,
        }

    tgt_p = float(targets.get("protein_g") or 0)
    high_protein_goal = tgt_p >= 150
    stocked = [i for i in ingredients if is_in_stock(i)]
    adds = [a for a in (paired_adds or []) if isinstance(a, dict)]

    def _covering_add(role: str, dropping: dict) -> Optional[dict]:
        for a in adds:
            if a.get("action") not in (None, "add", "restock"):
                continue
            if inventory_gap_role(a) != role:
                continue
            if _names_overlap(str(a.get("name") or ""), str(dropping.get("name") or "")):
                continue
            return a
        return None

    def _protect_last_unique(ing: dict) -> Optional[dict]:
        """Return covering add, or None when remove must be skipped."""
        if not is_in_stock(ing):
            return {}  # out-of-stock is not a unique in-pantry filler
        role = inventory_gap_role(ing)
        if not role:
            return {}
        others = [
            i
            for i in stocked
            if str(i.get("id") or "") != str(ing.get("id") or "")
            and inventory_gap_role(i) == role
        ]
        if others:
            return {}
        cover = _covering_add(role, ing)
        if cover is None:
            return None
        return cover

    candidates: List[dict] = []
    skip_keep: set = set()

    # --- Duplicates: keep higher protein-density / in-stock (not log count) ---
    for i, a in enumerate(ingredients):
        for b in ingredients[i + 1 :]:
            if not _names_overlap(a.get("name") or "", b.get("name") or ""):
                continue

            def rank(x: dict) -> tuple:
                return (
                    _protein_density(x),
                    1 if is_in_stock(x) else 0,
                    1 if inventory_gap_role(x) else 0,
                    float(x.get("protein_g") or 0),
                )

            keep, drop = (a, b) if rank(a) >= rank(b) else (b, a)
            did = str(drop.get("id") or "")
            if did in skip_keep:
                continue
            cover = _protect_last_unique(drop)
            if cover is None:
                continue
            skip_keep.add(did)
            row = {
                **drop,
                "action": "remove",
                "reason": (
                    f"Near-duplicate of “{keep.get('name')}” — keep one entry "
                    f"to simplify meal planning."
                ),
                "need": f"Redundant SKU vs “{keep.get('name')}”.",
                "score": 90.0,
                "source": "duplicate",
                "proposal": True,
            }
            if cover:
                row["paired_add"] = {
                    "id": cover.get("id"),
                    "name": cover.get("name"),
                    "reason": cover.get("reason") or cover.get("need") or "",
                }
                row["reason"] = (
                    row["reason"]
                    + f" Paired add: {cover.get('name')} covers the {inventory_gap_role(drop)} role."
                )
            candidates.append(row)

    for ing in ingredients:
        iid = str(ing.get("id") or "")
        if iid in skip_keep:
            continue
        dens = _protein_density(ing)
        cal = float(ing.get("calories") or 0)
        prot = float(ing.get("protein_g") or 0)
        name_l = str(ing.get("name") or "").lower()
        cat = str(ing.get("category") or "other").lower()
        in_stock = is_in_stock(ing)
        reasons: List[str] = []
        score = 0.0

        non_meal_kw = (
            "vitamin",
            "multivitamin",
            "supplement",
            "gummy",
            "capsule",
            "tablet",
            "probiotic",
            "electrolyte packet",
        )
        if any(k in name_l for k in non_meal_kw) or (
            prot < 3 and cal < 40 and cat in ("other", "carb") and not is_veg_or_fruit(ing)
        ):
            reasons.append(
                "Looks like a supplement/micro item — meal planner works better with real food staples."
            )
            score += 55

        # Out of stock + low utility (need-based). Rare-log alone is not enough.
        if not in_stock and dens < 0.06 and cal < 80:
            reasons.append(
                "Out of stock and low meal-plan utility — prune if you will not restock."
            )
            score += 40

        if high_protein_goal and dens < 0.04 and cal >= 100 and cat in ("fat", "other", "carb"):
            reasons.append(
                f"Low protein density ({prot:.0f}g / {cal:.0f} kcal) for a ~{int(tgt_p)}g protein target."
            )
            score += 40
            if dens < 0.025 and cal >= 150:
                reasons.append(
                    "Calorie-dense / low-protein for a cutting-style protein goal — easy to overshoot calories."
                )
                score += 8

        if cal <= 0 and prot <= 0 and float(ing.get("carbs_g") or 0) <= 0:
            reasons.append("No macros on file — not useful for planning until filled in (or remove).")
            score += 45

        if not reasons or score < 25:
            continue

        cover = _protect_last_unique(ing)
        if cover is None:
            continue

        row = {
            **ing,
            "action": "remove",
            "reason": " ".join(reasons[:2]),
            "need": reasons[0],
            "score": round(score, 1),
            "source": "heuristic",
            "proposal": True,
        }
        if cover:
            row["paired_add"] = {
                "id": cover.get("id"),
                "name": cover.get("name"),
                "reason": cover.get("reason") or cover.get("need") or "",
            }
            row["reason"] = (
                row["reason"]
                + f" Paired add: {cover.get('name')} covers the {inventory_gap_role(ing)} role."
            )
        candidates.append(row)

    by_id: Dict[str, dict] = {}
    for c in candidates:
        k = str(c.get("id") or c.get("name") or "").lower()
        if not k:
            continue
        if k not in by_id or float(c.get("score") or 0) > float(by_id[k].get("score") or 0):
            by_id[k] = c
    ranked = sorted(by_id.values(), key=lambda x: (-float(x.get("score") or 0), x.get("name") or ""))
    top = ranked[: max(1, int(max_suggestions))] if ranked else []
    if top:
        summary = (
            f"{len(top)} removal suggestion{'s' if len(top) != 1 else ''} "
            f"(duplicates or weak fit for targets — not rare-log). Proposals until you accept."
        )
    else:
        summary = "No strong removal candidates — inventory looks lean."
        honesty.append(
            {
                "level": "muted",
                "kind": "empty_removals",
                "text": summary,
            }
        )
    return {
        "suggestions": top,
        "summary": summary,
        "count": len(top),
        "honesty": honesty,
    }
