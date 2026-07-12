"""Ingredient inventory + remaining-day meal plan generation."""

from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .models import NutritionDay

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
    nutrition: Sequence[NutritionDay], as_of: Optional[str] = None
) -> dict:
    """Sum macros for as_of (default today UTC) from Google Health nutrition days."""
    day = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0, "date": day}
    for n in nutrition:
        if n.date != day:
            continue
        if n.calories is not None:
            total["calories"] += float(n.calories)
        if n.protein_g is not None:
            total["protein_g"] += float(n.protein_g)
        if n.carbs_g is not None:
            total["carbs_g"] += float(n.carbs_g)
        if n.fat_g is not None:
            total["fat_g"] += float(n.fat_g)
    for k in ("calories", "protein_g", "carbs_g", "fat_g"):
        total[k] = round(total[k], 1)
    return total


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
) -> dict:
    """
    Greedy remaining-day plan from stocked ingredients.

    Adds whole servings that best fill remaining protein/calories without
    massively overshooting calories (allow ~15% soft overshoot on protein only).
    """
    targets = normalize_targets(targets)
    rem = remaining_macros(targets, consumed)
    stocked = stocked_ingredients(inventory)
    plan_items: List[dict] = []
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}

    if not stocked:
        return {
            "meals": [],
            "items": [],
            "planned_totals": totals,
            "remaining_after_plan": rem,
            "targets": targets,
            "consumed": consumed,
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
    for existing in inv.get("ingredients") or []:
        if str(existing.get("id")) == ingredient_id:
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
