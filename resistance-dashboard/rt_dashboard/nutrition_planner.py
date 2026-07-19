"""Ingredient inventory + remaining-day meal plan generation."""

from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .models import FoodLogEntry, NutritionDay

INVENTORY_PATH = "fitness/nutrition/inventory.json"
TARGETS_PATH = "fitness/nutrition/targets.json"

DEFAULT_TARGETS = {
    "calories": 2100,
    "protein_g": 210,
    "carbs_g": 180,
    "fat_g": 55,
    "notes": "Default cutting targets",
    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
}


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
        if raw.get("notes"):
            t["notes"] = str(raw["notes"])
        if raw.get("updated_at"):
            t["updated_at"] = str(raw["updated_at"])
    return t


def normalize_ingredient(raw: dict) -> dict:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("ingredient name required")
    iid = str(raw.get("id") or _slug(name)).strip()
    return {
        "id": iid,
        "name": name,
        "category": str(raw.get("category") or "other").strip() or "other",
        "serving_label": str(raw.get("serving_label") or "1 serving").strip(),
        "calories": float(raw.get("calories") or 0),
        "protein_g": float(raw.get("protein_g") or 0),
        "carbs_g": float(raw.get("carbs_g") or 0),
        "fat_g": float(raw.get("fat_g") or 0),
        "in_stock": bool(raw.get("in_stock", True)),
        "notes": str(raw.get("notes") or ""),
    }


def stocked_ingredients(inventory: dict) -> List[dict]:
    return [
        normalize_ingredient(i)
        for i in inventory.get("ingredients") or []
        if i.get("in_stock", True)
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
) -> dict:
    """
    Greedy remaining-day plan from stocked ingredients.

    Adds whole servings that best fill remaining protein/calories without
    massively overshooting calories (allow ~15% soft overshoot on protein only).
    When food_logs_today is provided, the message and scoring bias away from
    foods already eaten heavily today.
    """
    targets = normalize_targets(targets)
    rem = remaining_macros(targets, consumed)
    stocked = stocked_ingredients(inventory)
    plan_items: List[dict] = []
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    logged = list(food_logs_today or [])
    logged_names = {str(x.get("name") or "").strip().lower() for x in logged if x}

    if not stocked:
        return {
            "meals": [],
            "items": [],
            "planned_totals": totals,
            "remaining_after_plan": rem,
            "targets": targets,
            "consumed": consumed,
            "food_logs_today": logged,
            "message": "No in-stock ingredients. Add items to inventory first.",
        }

    # Soft calorie ceiling: don't exceed remaining + 10% or +80 kcal
    cal_ceiling = rem["calories"] + max(80.0, rem["calories"] * 0.1)

    # Cap how many times we pick the same ingredient in one plan
    pick_counts: Dict[str, int] = {}

    for _ in range(max_items):
        # Close enough to targets
        if rem["calories"] < 80 and rem["protein_g"] < 15:
            break
        if rem["protein_g"] < 12 and rem["calories"] < 200:
            break
        candidates = []
        for ing in stocked:
            iid = str(ing["id"])
            if pick_counts.get(iid, 0) >= 3:
                continue
            # Don't add another huge protein hit if protein is nearly done
            if rem["protein_g"] < 20 and float(ing["protein_g"]) > rem["protein_g"] + 25:
                continue
            # skip if adding would blow calorie budget badly
            if totals["calories"] + ing["calories"] > cal_ceiling and rem["protein_g"] < 20:
                continue
            if (
                totals["calories"] + ing["calories"] > cal_ceiling + 100
                and ing["protein_g"] < 25
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
            if sc > 0:
                candidates.append((sc, ing))
        if not candidates:
            break
        candidates.sort(key=lambda x: -x[0])
        best = candidates[0][1]
        # If this single serving overshoots calories a lot and protein is already ok, stop
        if (
            best["calories"] > rem["calories"] + 120
            and rem["protein_g"] < 20
            and totals["calories"] > 0
        ):
            break
        plan_items.append(
            {
                "id": best["id"],
                "name": best["name"],
                "servings": 1,
                "serving_label": best["serving_label"],
                "calories": best["calories"],
                "protein_g": best["protein_g"],
                "carbs_g": best["carbs_g"],
                "fat_g": best["fat_g"],
            }
        )
        pick_counts[str(best["id"])] = pick_counts.get(str(best["id"]), 0) + 1
        for k in ("calories", "protein_g", "carbs_g", "fat_g"):
            totals[k] += float(best[k])
            rem[k] = round(max(0.0, rem[k] - float(best[k])), 1)

    # Group into simple meal buckets
    meals = _bucket_meals(plan_items)
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

    msg = "Plan generated from remaining macros and in-stock ingredients."
    if logged:
        msg = (
            f"Plan uses {len(logged)} Google Health food log"
            f"{'s' if len(logged) != 1 else ''} so far today + remaining macros."
        )
    if remaining_after["protein_g"] > 40:
        msg += " Protein still short — add more high-protein items to inventory if needed."
    if remaining_after["calories"] > 300 and not plan_items:
        msg = "Could not fit more servings without exceeding soft calorie ceiling."

    return {
        "meals": meals,
        "items": plan_items,
        "planned_totals": totals,
        "remaining_before_plan": remaining_macros(targets, consumed),
        "remaining_after_plan": remaining_after,
        "targets": targets,
        "consumed": consumed,
        "food_logs_today": logged,
        "message": msg,
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
    }


def _bucket_meals(items: List[dict]) -> List[dict]:
    if not items:
        return []
    labels = ["Next meal", "Later meal", "Evening", "Optional snack"]
    # split into chunks of ~3 items
    meals = []
    chunk = 3
    for i in range(0, len(items), chunk):
        part = items[i : i + chunk]
        label = labels[min(i // chunk, len(labels) - 1)]
        sub = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
        for it in part:
            for k in sub:
                sub[k] += float(it[k])
        meals.append(
            {
                "label": label,
                "items": part,
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


def update_targets(raw: dict) -> dict:
    t = normalize_targets(raw)
    t["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if raw.get("notes") is not None:
        t["notes"] = str(raw.get("notes") or "")
    return t


# Curated cutting/recomp staples for smart "add to inventory" suggestions.
STAPLE_CATALOG: List[dict] = [
    {
        "id": "chicken-breast",
        "name": "Chicken breast",
        "category": "protein",
        "serving_label": "6 oz cooked",
        "calories": 280,
        "protein_g": 52,
        "carbs_g": 0,
        "fat_g": 6,
    },
    {
        "id": "turkey-breast",
        "name": "Turkey breast",
        "category": "protein",
        "serving_label": "6 oz cooked",
        "calories": 250,
        "protein_g": 50,
        "carbs_g": 0,
        "fat_g": 4,
    },
    {
        "id": "nonfat-greek-yogurt",
        "name": "Greek yogurt (nonfat)",
        "category": "protein",
        "serving_label": "1.5 cups",
        "calories": 200,
        "protein_g": 30,
        "carbs_g": 12,
        "fat_g": 0,
    },
    {
        "id": "cottage-cheese-lowfat",
        "name": "Cottage cheese (low-fat)",
        "category": "protein",
        "serving_label": "1 cup",
        "calories": 180,
        "protein_g": 28,
        "carbs_g": 8,
        "fat_g": 2.5,
    },
    {
        "id": "whey-protein",
        "name": "Whey protein",
        "category": "protein",
        "serving_label": "1 scoop",
        "calories": 120,
        "protein_g": 24,
        "carbs_g": 3,
        "fat_g": 1,
    },
    {
        "id": "egg-whites",
        "name": "Egg whites",
        "category": "protein",
        "serving_label": "1 cup",
        "calories": 125,
        "protein_g": 26,
        "carbs_g": 2,
        "fat_g": 0,
    },
    {
        "id": "canned-tuna",
        "name": "Canned tuna (in water)",
        "category": "protein",
        "serving_label": "1 can drained",
        "calories": 120,
        "protein_g": 26,
        "carbs_g": 0,
        "fat_g": 1,
    },
    {
        "id": "lean-ground-turkey",
        "name": "Lean ground turkey (93%)",
        "category": "protein",
        "serving_label": "6 oz cooked",
        "calories": 260,
        "protein_g": 42,
        "carbs_g": 0,
        "fat_g": 10,
    },
    {
        "id": "oats",
        "name": "Oats",
        "category": "carb",
        "serving_label": "1/2 cup dry",
        "calories": 150,
        "protein_g": 5,
        "carbs_g": 27,
        "fat_g": 3,
    },
    {
        "id": "brown-rice",
        "name": "Brown rice",
        "category": "carb",
        "serving_label": "1 cup cooked",
        "calories": 215,
        "protein_g": 5,
        "carbs_g": 45,
        "fat_g": 2,
    },
    {
        "id": "sweet-potato",
        "name": "Sweet potato",
        "category": "carb",
        "serving_label": "1 medium",
        "calories": 110,
        "protein_g": 2,
        "carbs_g": 26,
        "fat_g": 0,
    },
    {
        "id": "black-beans",
        "name": "Black beans",
        "category": "carb",
        "serving_label": "1 cup cooked",
        "calories": 220,
        "protein_g": 15,
        "carbs_g": 40,
        "fat_g": 1,
    },
    {
        "id": "broccoli",
        "name": "Broccoli",
        "category": "veg",
        "serving_label": "2 cups",
        "calories": 60,
        "protein_g": 5,
        "carbs_g": 12,
        "fat_g": 0.5,
    },
    {
        "id": "spinach",
        "name": "Spinach",
        "category": "veg",
        "serving_label": "3 cups raw",
        "calories": 20,
        "protein_g": 2,
        "carbs_g": 3,
        "fat_g": 0,
    },
    {
        "id": "berries-mixed",
        "name": "Mixed berries",
        "category": "carb",
        "serving_label": "1 cup",
        "calories": 70,
        "protein_g": 1,
        "carbs_g": 17,
        "fat_g": 0.5,
    },
    {
        "id": "olive-oil",
        "name": "Olive oil",
        "category": "fat",
        "serving_label": "1 tbsp",
        "calories": 120,
        "protein_g": 0,
        "carbs_g": 0,
        "fat_g": 14,
    },
    {
        "id": "avocado",
        "name": "Avocado",
        "category": "fat",
        "serving_label": "1/2 medium",
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
