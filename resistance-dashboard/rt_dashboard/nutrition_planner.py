"""Ingredient inventory + remaining-day meal plan generation."""

from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from .models import FoodLogEntry, NutritionDay

INVENTORY_PATH = "fitness/nutrition/inventory.json"
TARGETS_PATH = "fitness/nutrition/targets.json"

DEFAULT_TARGETS = {
    "calories": 2100,
    "protein_g": 210,
    "carbs_g": 180,
    "fat_g": 55,
    # Optional scale goal for Trends weight chart guide line (lb). None = unset.
    "weight_goal_lbs": None,
    "notes": "Default cutting targets",
    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
}

# FitDash Today meal clocks (issue #250). Defaults sit inside the eating window.
MEAL_TZ_NAME = "America/New_York"
DEFAULT_SLOT_HM = ((12, 0), (15, 30), (19, 0))
FOURTH_SLOT_HM = (21, 0)
UPCOMING_MEAL_LABELS = ("Next meal", "Later meal", "Evening", "Optional snack")
PAST_MEAL_LABEL = "Earlier meal"


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
        if "weight_goal_lbs" in raw:
            t["weight_goal_lbs"] = _coerce_weight_goal_lbs(raw.get("weight_goal_lbs"))
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
    if "weight_goal_lbs" not in t:
        t["weight_goal_lbs"] = None
    return t


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


# Continuous meal portions (issue #267). Not locked to whole inventory servings.
MIN_PORTION_G = 25.0
PORTION_STEP_G = 5.0
MAX_PORTION_G = 1200.0
_MACRO_KEYS = ("calories", "protein_g", "carbs_g", "fat_g")


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
    """Round weighable grams (~5g). Min ~25g unless the inventory serving is smaller."""
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
) -> Optional[tuple]:
    """Choose a continuous (servings, portion_g|None) that fills remaining macros.

    When ``serving_g`` is known, portion is not locked to 1.0 serving steps.
    When mass is unknown, keep a whole free-text serving (never invent grams).
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
        "in_stock": bool(raw.get("in_stock", True)),
        "notes": str(raw.get("notes") or ""),
    }
    if serving_g is not None:
        out["serving_g"] = float(serving_g)
    return out


def is_in_stock(raw: dict) -> bool:
    """True only when ingredient is actively marked in stock.

    Missing key defaults to True for legacy rows; explicit false/0/\"false\"
    are always out of stock.
    """
    if not isinstance(raw, dict):
        return False
    if "in_stock" not in raw:
        return True
    v = raw.get("in_stock")
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def stocked_ingredients(inventory: dict) -> List[dict]:
    """Return only ingredients currently marked in stock (for meal plans)."""
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
        out.append(f.to_dict() if hasattr(f, "to_dict") else dict(f))  # type: ignore[arg-type]
    return out


def remaining_macros(targets: dict, consumed: dict) -> dict:
    rem = {}
    for k in ("calories", "protein_g", "carbs_g", "fat_g"):
        rem[k] = round(max(0.0, float(targets.get(k) or 0) - float(consumed.get(k) or 0)), 1)
    return rem


def _score_ingredient(ing: dict, rem: dict) -> float:
    """Higher is better for filling remaining needs (protein-weighted)."""
    if rem["calories"] <= 0 and rem["protein_g"] <= 0:
        return -1.0
    p = float(ing["protein_g"])
    c = float(ing["calories"]) or 1.0
    # Prefer high protein density, still useful for calories
    protein_need = max(rem["protein_g"], 1.0)
    cal_need = max(rem["calories"], 1.0)
    return (p / protein_need) * 3.0 + (min(c, rem["calories"]) / cal_need) * 1.0 + (p / c) * 2.0


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
    Slot count is 1–4 from remaining macros + in-stock items — never empty
    timed hinges, never invented food.
    """
    targets = normalize_targets(targets)
    rem = remaining_macros(targets, consumed)
    # Meal plan MUST only use actively in-stock inventory (never out-of-stock).
    stocked = stocked_ingredients(inventory)
    stocked_ids = {str(i.get("id") or "") for i in stocked}
    stocked_names = {str(i.get("name") or "").strip().lower() for i in stocked}
    plan_items: List[dict] = []
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    logged = list(food_logs_today or [])
    logged_names = {str(x.get("name") or "").strip().lower() for x in logged if x}

    ings = inventory.get("ingredients") if isinstance(inventory, dict) else None
    pantry_dark = not isinstance(ings, list) or len(ings) == 0
    if not stocked:
        from .meal_plan_store import MSG_NO_IN_STOCK, MSG_PANTRY_UNAVAILABLE

        return {
            "meals": [],
            "items": [],
            "planned_totals": totals,
            "remaining_after_plan": rem,
            "remaining_before_plan": rem,
            "targets": targets,
            "consumed": consumed,
            "food_logs_today": logged,
            "stocked_count": 0,
            "pantry_dark": pantry_dark,
            "in_stock_only": True,
            "message": MSG_PANTRY_UNAVAILABLE if pantry_dark else MSG_NO_IN_STOCK,
        }

    # Soft calorie ceiling: don't exceed remaining + 10% or +80 kcal
    cal_ceiling = rem["calories"] + max(80.0, rem["calories"] * 0.1)

    # Cap how many times we pick the same ingredient in one plan
    pick_counts: Dict[str, int] = {}

    for _ in range(max_items):
        # Close enough — leftover protein/cals too small for a useful bite
        if rem["calories"] < 25 and rem["protein_g"] < 5:
            break
        candidates = []
        for ing in stocked:
            iid = str(ing["id"])
            if pick_counts.get(iid, 0) >= 3:
                continue
            pick = _pick_continuous_portion(ing, rem, cal_ceiling, totals)
            if pick is None:
                continue
            _servings_n, _portion_g = pick
            min_macros = (
                _macros_for_portion(ing, portion_g=_portion_g)
                if _portion_g is not None
                else _macros_for_portion(ing, servings=_servings_n)
            )
            # Don't add another huge protein hit if protein is nearly done
            if rem["protein_g"] < 20 and min_macros["protein_g"] > rem["protein_g"] + 25:
                continue
            # skip if this pick would blow calorie budget badly
            if totals["calories"] + min_macros["calories"] > cal_ceiling and rem["protein_g"] < 20:
                continue
            if (
                totals["calories"] + min_macros["calories"] > cal_ceiling + 100
                and min_macros["protein_g"] < 25
            ):
                continue
            # Once protein is filled, prefer carbs/veg to finish calories
            if rem["protein_g"] < 15 and float(ing["protein_g"]) > 25 and rem["calories"] > 100:
                if float(ing["carbs_g"]) < 10:
                    continue
            sc = _score_ingredient(ing, rem)
            # Soft diversify: slight penalty if already logged under a similar name
            iname = str(ing.get("name") or "").strip().lower()
            if iname and any(iname in ln or ln in iname for ln in logged_names if ln):
                sc *= 0.85
            # Stronger diversify vs items already in *this* plan
            already = pick_counts.get(iid, 0)
            if already >= 1:
                sc *= 0.55 ** already
            if sc > 0:
                candidates.append((sc, ing, pick))
        if not candidates:
            break
        candidates.sort(key=lambda x: -x[0])
        best = candidates[0][1]
        servings_n, portion_g = candidates[0][2]
        row = _plan_item_from_ingredient(best, servings=servings_n, portion_g=portion_g)
        plan_items.append(row)
        pick_counts[str(best["id"])] = pick_counts.get(str(best["id"]), 0) + 1
        for k in _MACRO_KEYS:
            totals[k] += float(row.get(k) or 0)
            rem[k] = round(max(0.0, rem[k] - float(row.get(k) or 0)), 1)

    # Safety net: never surface an item that is not currently stocked
    plan_items = [
        it
        for it in plan_items
        if str(it.get("id") or "") in stocked_ids
        or str(it.get("name") or "").strip().lower() in stocked_names
    ]
    # Collapse repeated picks into one line with servings (e.g. 3× chicken)
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
    )
    for k in totals:
        totals[k] = round(totals[k], 1)

    remaining_after = remaining_macros(
        targets,
        {
            "calories": float(consumed.get("calories") or 0) + totals["calories"],
            "protein_g": float(consumed.get("protein_g") or 0) + totals["protein_g"],
            "carbs_g": float(consumed.get("carbs_g") or 0) + totals["carbs_g"],
            "fat_g": float(consumed.get("fat_g") or 0) + totals["fat_g"],
        },
    )

    rem_before = remaining_macros(targets, consumed)
    msg = (
        f"Plan from {len(stocked)} in-stock ingredient"
        f"{'s' if len(stocked) != 1 else ''} only (out-of-stock excluded)."
    )
    if logged:
        msg += (
            f" Uses {len(logged)} Google Health food log"
            f"{'s' if len(logged) != 1 else ''} so far today for remaining macros."
        )
    if not plan_items and rem_before["calories"] < 150 and rem_before["protein_g"] < 20:
        msg = (
            f"Day essentially complete — only ~{rem_before['calories']:.0f} kcal and "
            f"{rem_before['protein_g']:.0f}g protein left under target; no extra servings planned."
        )
    elif remaining_after["protein_g"] > 40:
        msg += " Protein still short — restock high-protein items if needed."
    if remaining_after["calories"] > 300 and not plan_items and rem_before["protein_g"] >= 20:
        msg = "Could not fit more servings without exceeding soft calorie ceiling (in-stock only)."

    return {
        "meals": meals,
        "items": plan_items,
        "planned_totals": totals,
        "remaining_before_plan": remaining_macros(targets, consumed),
        "remaining_after_plan": remaining_after,
        "targets": targets,
        "consumed": consumed,
        "food_logs_today": logged,
        "stocked_count": len(stocked),
        "pantry_dark": False,
        "in_stock_only": True,
        "message": msg,
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
            display_g = float(int(round(pg)))
            n = display_g / float(base_g)
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
    }
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
            }
            if base_g is not None and float(base_g) > 0:
                row["serving_g"] = float(base_g)
            if it.get("portion_g") is not None:
                row["portion_g"] = float(it["portion_g"])
            by_key[key] = row
            order.append(key)
        else:
            row = by_key[key]
            add_n = float(it.get("servings") or 1)
            row["servings"] = float(row.get("servings") or 0) + add_n
            for k in ("calories", "protein_g", "carbs_g", "fat_g"):
                row[k] = round(float(row[k]) + float(it.get(k) or 0), 1)
            if it.get("portion_g") is not None:
                row["portion_g"] = round(
                    float(row.get("portion_g") or 0) + float(it["portion_g"]), 1
                )
    out = []
    for key in order:
        row = by_key[key]
        for k in ("calories", "protein_g", "carbs_g", "fat_g"):
            row[k] = round(float(row[k]), 1)
        n = float(row.get("servings") or 1)
        row["servings"] = int(n) if abs(n - int(n)) < 1e-9 else round(n, 2)
        base_g = row.get("serving_g")
        if base_g is not None and float(base_g) > 0:
            row["portion_g"] = round(float(base_g) * n)
            row["serving_label"] = format_portion_label(
                serving_g=float(base_g),
                servings=n,
                serving_label=str(row.get("serving_label") or ""),
            )
        elif float(n) != 1.0:
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


def _resolve_eat_times(
    n: int,
    *,
    now: datetime,
    start: datetime,
    end: datetime,
    tz,
    eat_slots: Optional[Sequence[Any]] = None,
) -> List[datetime]:
    """n clock times inside the eating window. Never invents food slots.

    Prefer a stable day plan (~12:00 / 15:30 / 19:00, or caller ``eat_slots``).
    Only spread when clamping collapsed two hinges onto the same minute.
    """
    if n <= 0:
        return []
    day = now.replace(second=0, microsecond=0)
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
        upcoming = [t for t in cands if t >= now]
        if upcoming:
            return [upcoming[0]]
        if start <= now < end:
            return [_clamp_into_window(now, start, end)]
        return [cands[-1] if cands else _clamp_into_window(day.replace(hour=12, minute=0), start, end)]

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
    if len(chosen) < n:
        chosen = _dedupe_sorted_times(
            chosen + _space_in_range(n - len(chosen), start, end, avoid=chosen)
        )
    return chosen[:n]


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
    """Split a continuous portion into n integer-gram chunks that sum to total_g."""
    total = max(1, int(round(float(total_g))))
    n = max(1, int(n))
    if n == 1:
        return [float(total)]
    base = total // n
    extra = total - base * n
    chunks = [float(base + (1 if i < extra else 0)) for i in range(n)]
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
    n_slots = _desired_slot_count(items, remaining)
    units = _expand_serving_units(items)
    n_slots = max(1, min(n_slots, len(units)))
    chunks = _chunk_units(units, n_slots)
    times = _resolve_eat_times(
        len(chunks), now=now, start=start, end=end, tz=tz, eat_slots=eat_slots
    )
    while len(times) < len(chunks):
        times.append(times[-1] if times else now)

    meals: List[dict] = []
    next_idx = None
    for i, t in enumerate(times[: len(chunks)]):
        if t >= now:
            next_idx = i
            break
    if next_idx is None:
        next_idx = 0

    upcoming_i = 0
    past_used = False
    for i, part in enumerate(chunks):
        collapsed = _collapse_plan_items(list(part))
        sub = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
        for it in collapsed:
            for k in sub:
                sub[k] += float(it.get(k) or 0)
        eat_at = times[i]
        if i < next_idx:
            label = PAST_MEAL_LABEL if not past_used else "Afternoon"
            past_used = True
        else:
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


def set_in_stock(inventory: dict, ingredient_id: str, in_stock: bool) -> dict:
    inv = deepcopy(inventory) if inventory else {"ingredients": []}
    found = False
    want = str(ingredient_id or "").strip().lower()
    for existing in inv.get("ingredients") or []:
        if str(existing.get("id") or "").strip().lower() == want:
            existing["in_stock"] = bool(in_stock)
            found = True
            break
    if not found:
        raise ValueError("ingredient not found")
    inv["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return inv


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
        "in_stock": raw.get("in_stock", existing.get("in_stock", True)),
        "notes": raw.get("notes", existing.get("notes", "")),
    }
    if "serving_g" in raw:
        overlay["serving_g"] = raw.get("serving_g")
    elif existing.get("serving_g") is not None:
        overlay["serving_g"] = existing.get("serving_g")
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
) -> dict:
    """Suggest restocks / new staples from inventory gaps, logs, and macro needs.

    Returns ``{"suggestions": [...], "summary": str}`` where each suggestion has:
    action (restock|add), reason, score, plus ingredient fields for one-click add.
    """
    targets = normalize_targets(targets or {})
    logs = list(food_logs or [])
    consumed = consumed or {}
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
    stocked = [i for i in ingredients if i.get("in_stock", True)]
    out_of_stock = [i for i in ingredients if not i.get("in_stock", True)]

    # --- 1) Restock out-of-stock items (always high priority — already in your list) ---
    for ing in out_of_stock:
        dens = _protein_density(ing)
        # Base high so restocks beat net-new catalog noise
        score = 75.0 + dens * 80.0
        if dens >= 0.08:
            reason = "Out of stock and high protein density — restock for meal plans."
            score += 20
        elif (ing.get("category") or "") == "veg":
            reason = "Out of stock veg — restock for volume/fiber."
            score += 12
        else:
            reason = "Marked out of stock — restock if you still use it."
            score += 10
        _push(
            {
                **ing,
                "action": "restock",
                "reason": reason,
                "score": round(score, 1),
                "source": "inventory",
            }
        )

    # --- 2) Frequently logged foods missing from inventory ---
    log_stats: Dict[str, Dict[str, Any]] = {}
    for f in logs:
        if hasattr(f, "to_dict"):
            d = f.to_dict()
        elif isinstance(f, dict):
            d = f
        else:
            continue
        name = str(d.get("name") or "").strip()
        if not name or name.lower() in ("logged food", "unknown"):
            continue
        bucket = log_stats.setdefault(
            name,
            {
                "name": name,
                "count": 0,
                "calories": 0.0,
                "protein_g": 0.0,
                "carbs_g": 0.0,
                "fat_g": 0.0,
            },
        )
        bucket["count"] += 1
        for k in ("calories", "protein_g", "carbs_g", "fat_g"):
            try:
                bucket[k] += float(d.get(k) or 0)
            except (TypeError, ValueError):
                pass

    for name, st in sorted(log_stats.items(), key=lambda x: -x[1]["count"]):
        if st["count"] < 2:
            continue
        match = _find_inventory_match(inventory or {}, name)
        if match and match.get("in_stock", True):
            continue
        n = max(1, st["count"])
        avg = {
            "calories": round(st["calories"] / n, 1),
            "protein_g": round(st["protein_g"] / n, 1),
            "carbs_g": round(st["carbs_g"] / n, 1),
            "fat_g": round(st["fat_g"] / n, 1),
        }
        dens = avg["protein_g"] / (avg["calories"] or 1)
        cat = "protein" if dens >= 0.08 else ("fat" if avg["fat_g"] > avg["carbs_g"] and dens < 0.04 else "carb")
        if match and not match.get("in_stock", True):
            _push(
                {
                    **normalize_ingredient(match),
                    "action": "restock",
                    "reason": f"Logged {st['count']}× recently and currently out of stock.",
                    "score": round(50 + st["count"] * 8 + dens * 40, 1),
                    "source": "food_logs",
                }
            )
        else:
            _push(
                {
                    "id": _slug(name),
                    "name": name,
                    "category": cat,
                    "serving_label": "1 logged serving (avg)",
                    **avg,
                    "in_stock": True,
                    "action": "add",
                    "reason": f"Logged {st['count']}× recently but not in inventory.",
                    "score": round(45 + st["count"] * 8 + dens * 50, 1),
                    "source": "food_logs",
                }
            )

    # --- 3) Catalog staples missing from inventory (gap-aware) ---
    tgt_p = float(targets.get("protein_g") or 0)
    rem_p = max(0.0, tgt_p - float(consumed.get("protein_g") or 0))
    stocked_high_p = sum(1 for i in stocked if _protein_density(i) >= 0.08)
    protein_gap = rem_p > 40 or stocked_high_p < 2

    for staple in STAPLE_CATALOG:
        match = _find_inventory_match(
            inventory or {}, staple["name"], str(staple.get("id") or "")
        )
        if match and match.get("in_stock", True):
            continue
        dens = _protein_density(staple)
        score = 20.0 + dens * 60.0
        reasons = []
        if match and not match.get("in_stock", True):
            action = "restock"
            reasons.append("Catalog staple currently out of stock.")
            score += 25
            payload = {**normalize_ingredient(match)}
        else:
            action = "add"
            reasons.append("High-value staple not in inventory.")
            payload = {**staple, "in_stock": True}
        if protein_gap and dens >= 0.08:
            reasons.append("Helps close protein / high-protein stock gap.")
            score += 20
        if staple.get("category") == "veg" and stocked_high_p >= 0:
            # Mild boost for fiber volume when few veg stocked
            veg_n = sum(1 for i in stocked if (i.get("category") or "") == "veg")
            if veg_n < 2:
                reasons.append("Few vegetables stocked.")
                score += 10
        if staple.get("category") == "carb":
            carb_n = sum(1 for i in stocked if (i.get("category") or "") == "carb")
            if carb_n < 2:
                reasons.append("Limited carb staples stocked.")
                score += 8
        _push(
            {
                **payload,
                "action": action,
                "reason": " ".join(reasons),
                "score": round(score, 1),
                "source": "catalog",
            }
        )

    suggestions.sort(key=lambda x: (-float(x.get("score") or 0), x.get("name") or ""))
    limit = max(1, int(max_suggestions))
    # Prefer including restocks first, then fill remaining with highest-score adds
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
    summary = (
        f"{len(top)} suggestions ({', '.join(bits) or 'none'}) from inventory gaps, "
        f"food logs, and staple catalog."
    )
    return {"suggestions": top, "summary": summary, "count": len(top)}


def suggest_inventory_removals(
    inventory: dict,
    targets: Optional[dict] = None,
    food_logs: Optional[Sequence[Any]] = None,
    max_suggestions: int = 6,
) -> dict:
    """Suggest inventory items that may be worth removing (with short reasons).

    Signals: near-duplicates, out-of-stock clutter, low protein density vs
    cutting targets, non-meal items, and stocked items never logged when
    stronger alternatives already exist.
    """
    targets = normalize_targets(targets or {})
    logs = list(food_logs or [])
    ingredients = [normalize_ingredient(i) for i in (inventory.get("ingredients") or [])]
    if not ingredients:
        return {
            "suggestions": [],
            "summary": "No inventory items to review.",
            "count": 0,
        }

    # Food-log name hits for "actually used"
    log_names: List[str] = []
    log_counts: Dict[str, int] = {}
    for f in logs:
        if hasattr(f, "to_dict"):
            d = f.to_dict()
        elif isinstance(f, dict):
            d = f
        else:
            continue
        name = str(d.get("name") or "").strip()
        if not name:
            continue
        log_names.append(name)
        log_counts[name] = log_counts.get(name, 0) + 1

    def _logged(ing: dict) -> int:
        n = 0
        for ln, c in log_counts.items():
            if _names_overlap(ing.get("name") or "", ln):
                n += c
        return n

    tgt_p = float(targets.get("protein_g") or 0)
    high_protein_goal = tgt_p >= 150
    stocked = [i for i in ingredients if is_in_stock(i)]
    stocked_high_p = [i for i in stocked if _protein_density(i) >= 0.08]

    candidates: List[dict] = []
    # Track which id we keep when flagging duplicates
    skip_keep: set = set()

    # --- Duplicates: keep higher protein-density / logged one ---
    for i, a in enumerate(ingredients):
        for b in ingredients[i + 1 :]:
            if not _names_overlap(a.get("name") or "", b.get("name") or ""):
                continue
            # Prefer keep: more log hits, then density, then in-stock
            def rank(x: dict) -> tuple:
                return (
                    _logged(x),
                    _protein_density(x),
                    1 if is_in_stock(x) else 0,
                    float(x.get("protein_g") or 0),
                )

            keep, drop = (a, b) if rank(a) >= rank(b) else (b, a)
            kid = str(keep.get("id") or "")
            did = str(drop.get("id") or "")
            if did in skip_keep:
                continue
            skip_keep.add(did)
            candidates.append(
                {
                    **drop,
                    "action": "remove",
                    "reason": (
                        f"Near-duplicate of “{keep.get('name')}” — keep one entry "
                        f"to simplify meal planning."
                    ),
                    "score": 90.0,
                    "source": "duplicate",
                }
            )

    for ing in ingredients:
        iid = str(ing.get("id") or "")
        if iid in skip_keep:
            continue  # already suggested as duplicate drop
        dens = _protein_density(ing)
        cal = float(ing.get("calories") or 0)
        prot = float(ing.get("protein_g") or 0)
        name = str(ing.get("name") or "")
        name_l = name.lower()
        cat = str(ing.get("category") or "other").lower()
        logged_n = _logged(ing)
        in_stock = is_in_stock(ing)
        reasons: List[str] = []
        score = 0.0

        # Non-meal / supplement-like clutter for the meal planner
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
            prot < 3 and cal < 40 and cat in ("other", "carb")
        ):
            reasons.append(
                "Looks like a supplement/micro item — meal planner works better with real food staples."
            )
            score += 55

        # Out of stock + unused + low utility
        if not in_stock and logged_n == 0 and dens < 0.06:
            reasons.append(
                "Out of stock and not in recent food logs — safe to prune dead catalog rows."
            )
            score += 50
        elif not in_stock and logged_n == 0:
            reasons.append("Out of stock with no recent logs — consider removing if you won’t buy again.")
            score += 35

        # Low protein density while chasing high protein targets
        if high_protein_goal and dens < 0.04 and cal >= 100 and cat in ("fat", "other", "carb"):
            if logged_n <= 1:
                reasons.append(
                    f"Low protein density ({prot:.0f}g / {cal:.0f} kcal) for a ~{int(tgt_p)}g protein target."
                )
                score += 40
            elif dens < 0.025 and cal >= 150:
                reasons.append(
                    "Calorie-dense / low-protein for a cutting-style protein goal — easy to overshoot calories."
                )
                score += 32

        # Stocked but never logged while better proteins exist
        if (
            in_stock
            and logged_n == 0
            and dens < 0.07
            and len(stocked_high_p) >= 2
            and not any(_names_overlap(name, h.get("name") or "") for h in stocked_high_p)
        ):
            reasons.append(
                "In stock but not logged recently; stronger high-protein staples already cover meal plans."
            )
            score += 28

        # Empty / zero-macro junk rows
        if cal <= 0 and prot <= 0 and float(ing.get("carbs_g") or 0) <= 0:
            reasons.append("No macros on file — not useful for planning until filled in (or remove).")
            score += 45

        if not reasons or score < 25:
            continue
        # Don't suggest removing a heavily logged staple
        if logged_n >= 5 and dens >= 0.08:
            continue

        candidates.append(
            {
                **ing,
                "action": "remove",
                "reason": " ".join(reasons[:2]),
                "score": round(score, 1),
                "source": "heuristic",
            }
        )

    # Dedupe by id, keep highest score
    by_id: Dict[str, dict] = {}
    for c in candidates:
        k = str(c.get("id") or c.get("name") or "").lower()
        if not k:
            continue
        if k not in by_id or float(c.get("score") or 0) > float(by_id[k].get("score") or 0):
            by_id[k] = c
    ranked = sorted(by_id.values(), key=lambda x: (-float(x.get("score") or 0), x.get("name") or ""))
    top = ranked[: max(1, int(max_suggestions))] if ranked else []
    summary = (
        f"{len(top)} removal suggestion{'s' if len(top) != 1 else ''} "
        f"(duplicates, unused, or weak fit for targets)."
        if top
        else "No strong removal candidates — inventory looks lean."
    )
    return {"suggestions": top, "summary": summary, "count": len(top)}
