"""Weekly grocery list for a recipe plan.

Sums the week's recipe grams, converts them to purchase units, then applies
pantry qty. Generating this list never writes stock. Shopping tasks are a
separate user action.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence

# Same variety window the recipe planner uses. This module does not plan meals.
RECIPE_REPEAT_DAYS = 2
MAX_MEALS_PER_PROTEIN = 2
WEEK_DAYS = 7
MEALS_PER_DAY = 3

# Cooked recipe grams → what you buy.
RICE_COOKED_PER_DRY = 2.5
FISH_COOKED_YIELD = 0.75
EGG_GRAMS = 50.0

PACKS = {
    "egg-whites": ("carton", 453.0),
    "greek-yogurt": ("tub", 907.0),
    "tempeh": ("block", 227.0),
    "pico-de-gallo": ("tub", 454.0),
    "rotel": ("can", 283.0),
    "corn-tortillas": ("pack", 280.0),
    "oats": ("pack", 510.0),
    "brown-rice": ("bag", 907.0),
    "rice": ("bag", 907.0),
    "boneless-skinless-chicken-breast": ("pack", 680.0),
    "chicken": ("pack", 680.0),
    "tilapia": ("pack", 454.0),
    "banana": ("each", 118.0),
    "blueberries": ("pack", 340.0),
    "broccoli": ("each", 300.0),
    "kale": ("bunch", 142.0),
    "brussels-sprouts": ("bag", 340.0),
    "edamame": ("bag", 340.0),
    "chocolate-whey-protein": ("tub", 900.0),
}

# USD per purchase gram, or per egg for whole eggs. Costco is the bulk default.
# These are the candidates `_pick_cheapest` ranks when the client posts none.
UNIT_COST = {
    "walmart": 0.012,
    "costco": 0.009,
}
EGG_UNIT_COST = {
    "walmart": 0.32,
    "costco": 0.22,
}

STAPLE_IDS = {
    "olive-oil",
    "soy-sauce",
    "garlic",
    "garlic-powder",
    "salt",
    "black-pepper",
    "paprika",
    "cumin",
}
_STAPLE_RE = re.compile(
    r"\b(olive oil|soy sauce|garlic|spice|spices|paprika|cumin|seasoning|black pepper|chili powder|salt)\b"
)
_FISH = {"tilapia", "salmon", "cod", "tuna", "fish", "shrimp"}
CHECK_UNKNOWN = "have (qty unknown), check"
CHECK_STAPLES = "check staples"


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _round(value: float) -> float:
    return round(float(value), 1)


def _fmt(value: float) -> str:
    text = f"{_round(value):g}"
    return text


def _blob(ingredient_id: str, name: str) -> str:
    return f"{ingredient_id} {name}".lower().replace("-", " ").replace("_", " ")


def _tokens(ingredient_id: str, name: str) -> set:
    return set(_blob(ingredient_id, name).split())


def _inventory_by_id(inventory: Optional[dict]) -> Dict[str, dict]:
    rows = (inventory or {}).get("ingredients") if isinstance(inventory, dict) else None
    out: Dict[str, dict] = {}
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("id") or "").strip():
            out[str(row["id"]).strip()] = row
    return out


def _recipe_id(recipe: dict) -> str:
    return str((recipe or {}).get("id") or "").strip()


def _is_staple(ingredient_id: str, name: str) -> bool:
    if ingredient_id in STAPLE_IDS:
        return True
    text = _blob(ingredient_id, name)
    if _STAPLE_RE.search(text):
        return True
    if text.strip() in {"oil", "olive oil"} or text.endswith(" oil"):
        return True
    return False


def _is_rice(ingredient_id: str, name: str) -> bool:
    return "rice" in _tokens(ingredient_id, name)


def _is_fish(ingredient_id: str, name: str) -> bool:
    return bool(_tokens(ingredient_id, name) & _FISH)


def _is_whole_egg(ingredient_id: str, name: str) -> bool:
    text = _blob(ingredient_id, name)
    if "white" in text:
        return False
    tokens = set(text.split())
    return bool(tokens & {"egg", "eggs"})


def _is_egg_white(ingredient_id: str, name: str) -> bool:
    text = _blob(ingredient_id, name)
    return "egg" in text and "white" in text


def _in_stock(ing: Optional[dict]) -> bool:
    if not isinstance(ing, dict):
        return False
    flag = ing.get("in_stock")
    if isinstance(flag, bool):
        return flag
    return str(ing.get("stock") or "").strip().lower() == "in"


def _qty(ing: Optional[dict]) -> Optional[float]:
    """On-hand amount. Missing, blank, and non-finite stay unknown."""
    if not isinstance(ing, dict):
        return None
    for key in ("qty", "quantity", "count", "servings", "grams_on_hand", "quantity_g", "qty_g"):
        number = _num(ing.get(key))
        if number is None:
            if ing.get(key) not in (None, ""):
                # Present but not a finite number. Do not treat that as zero.
                raw = ing.get(key)
                if raw is not None and raw != "" and not isinstance(raw, bool):
                    return None
            continue
        if number < 0:
            return None
        return number
    return None


def _meal_recipe(meal: dict, recipes_by_id: Dict[str, dict]) -> Optional[dict]:
    rec = meal.get("recipe") if isinstance(meal.get("recipe"), dict) else None
    if rec is None:
        rec = recipes_by_id.get(str(meal.get("recipe_id") or "").strip())
    return rec if isinstance(rec, dict) else None


def sum_recipe_grams(
    plans: Sequence[dict],
    recipes: Optional[Sequence[dict]] = None,
) -> Dict[str, dict]:
    """Recipe grams across the plans. One row per ingredient id."""
    recipes_by_id = {
        _recipe_id(rec): rec
        for rec in (recipes or [])
        if isinstance(rec, dict) and _recipe_id(rec)
    }
    demand: Dict[str, dict] = {}
    for plan in plans or []:
        if not isinstance(plan, dict):
            continue
        for meal in plan.get("meals") or []:
            if not isinstance(meal, dict):
                continue
            rec = _meal_recipe(meal, recipes_by_id)
            if not rec:
                continue
            servings = _num(meal.get("servings"))
            if servings is None or servings <= 0:
                servings = 1.0
            yld = _num(rec.get("yield_servings"))
            if yld is None or yld <= 0:
                yld = 1.0
            factor = servings / yld
            for line in rec.get("ingredients") or []:
                if not isinstance(line, dict):
                    continue
                iid = str(line.get("ingredient_id") or line.get("id") or "").strip()
                grams = _num(line.get("grams_batch"))
                if grams is None or grams <= 0:
                    grams = _num(line.get("grams"))
                if not iid or grams is None or grams <= 0:
                    continue
                row = demand.setdefault(
                    iid,
                    {
                        "ingredient_id": iid,
                        "name": str(line.get("name") or iid),
                        "recipe_grams": 0.0,
                    },
                )
                if line.get("name"):
                    row["name"] = str(line.get("name"))
                row["recipe_grams"] = row["recipe_grams"] + grams * factor
    for row in demand.values():
        row["recipe_grams"] = _round(row["recipe_grams"])
    return demand


def purchase_from_grams(ingredient_id: str, name: str, recipe_grams: float) -> dict:
    """Convert a recipe remainder into the unit you buy."""
    kind = "as-is"
    amount = max(0.0, float(recipe_grams or 0))
    if _is_rice(ingredient_id, name) and amount > 0:
        amount = amount / RICE_COOKED_PER_DRY
        kind = "dry"
    elif _is_fish(ingredient_id, name) and amount > 0:
        amount = amount / FISH_COOKED_YIELD
        kind = "raw"
    amount = _round(amount)
    if _is_whole_egg(ingredient_id, name):
        count = int(math.ceil(amount / EGG_GRAMS)) if amount > 0 else 0
        label = f"buy {count} egg" if count == 1 else f"buy {count} eggs"
        return {
            "purchase_g": amount,
            "purchase_count": count,
            "purchase_unit": "egg",
            "purchase_kind": "count",
            "purchase_label": label,
        }
    pack = PACKS.get(ingredient_id)
    if pack is None and _is_egg_white(ingredient_id, name):
        pack = ("carton", 453.0)
    if pack and amount > 0:
        unit, size = pack
        count = int(math.ceil(amount / size))
        suffix = {"dry": " dry", "raw": " raw"}.get(kind, "")
        label = f"buy {count} {unit} ({_fmt(amount)} g{suffix})"
        return {
            "purchase_g": amount,
            "purchase_count": count,
            "purchase_unit": unit,
            "purchase_kind": kind,
            "purchase_label": label,
        }
    suffix = {"dry": " dry", "raw": " raw"}.get(kind, "")
    label = f"buy {_fmt(amount)} g{suffix}"
    return {
        "purchase_g": amount,
        "purchase_count": None,
        "purchase_unit": "g",
        "purchase_kind": kind,
        "purchase_label": label,
    }


def _table_offers(ingredient_id: str, name: str) -> List[dict]:
    costs = EGG_UNIT_COST if _is_whole_egg(ingredient_id, name) else UNIT_COST
    offers = []
    for venue in ("walmart", "costco"):
        offers.append(
            {
                "offer_id": f"{venue}:{ingredient_id}",
                "name": f"{venue.title()} {name}",
                "price": costs[venue],
                "venue": venue,
            }
        )
    return offers


def _offer_id(raw: dict) -> str:
    return str(raw.get("offerId") or raw.get("offer_id") or raw.get("usItemId") or raw.get("id") or "")


def price_line(line: dict, client_offers: Optional[dict]) -> dict:
    """Rank Walmart and Costco by unit cost. Client offers replace the table."""
    from .restock_cart import _pick_cheapest

    iid = str(line.get("ingredient_id") or line.get("id") or "")
    posted = []
    if isinstance(client_offers, dict):
        raw = client_offers.get(iid) or client_offers.get(line.get("name")) or []
        if isinstance(raw, list):
            posted = [row for row in raw if isinstance(row, dict)]
    candidates = posted or _table_offers(iid, str(line.get("name") or iid))
    picked = _pick_cheapest(list(candidates))
    if not picked:
        return line
    match = next((row for row in candidates if _offer_id(row) == picked.get("offer_id")), None)
    venue = ""
    if isinstance(match, dict):
        venue = str(match.get("venue") or "")
    if not venue:
        offer = str(picked.get("offer_id") or "")
        venue = offer.split(":", 1)[0] if ":" in offer else ""
    line["offer_id"] = picked.get("offer_id")
    line["unit_price"] = picked.get("price")
    line["offer_name"] = picked.get("name")
    if venue:
        line["venue"] = venue
    return line


def _names_from_inventory(demand: Dict[str, dict], inventory: Optional[dict]) -> None:
    by_id = _inventory_by_id(inventory)
    for iid, row in demand.items():
        ing = by_id.get(iid) or {}
        if row.get("name") in (None, "", iid) and ing.get("name"):
            row["name"] = str(ing.get("name"))


def classify_demand(demand: Dict[str, dict], inventory: Optional[dict]) -> List[dict]:
    """Buy, check, and staples. In-stock rows are not dropped."""
    by_id = _inventory_by_id(inventory)
    _names_from_inventory(demand, inventory)
    lines: List[dict] = []
    for iid, row in demand.items():
        name = str(row.get("name") or iid)
        recipe_grams = float(row.get("recipe_grams") or 0)
        ing = by_id.get(iid)
        base = {
            "id": iid,
            "ingredient_id": iid,
            "name": name,
            "recipe_grams": _round(recipe_grams),
            "source": "recipe_plan",
        }
        if _is_staple(iid, name):
            lines.append(
                {
                    **base,
                    "section": "staples",
                    "action": "check_staples",
                    "grams": _round(recipe_grams),
                    "reason": CHECK_STAPLES,
                    "line": CHECK_STAPLES,
                }
            )
            continue
        in_stock = _in_stock(ing)
        qty = _qty(ing) if in_stock else None
        if ing and in_stock and qty is None:
            lines.append(
                {
                    **base,
                    "section": "check",
                    "action": "check",
                    "grams": _round(recipe_grams),
                    "qty": None,
                    "reason": CHECK_UNKNOWN,
                    "line": CHECK_UNKNOWN,
                }
            )
            continue
        if in_stock and qty is not None:
            remainder = max(0.0, recipe_grams - qty)
        else:
            remainder = recipe_grams
        if remainder <= 0:
            continue
        purchase = purchase_from_grams(iid, name, remainder)
        lines.append(
            {
                **base,
                "section": "buy",
                "action": "buy",
                "grams": purchase["purchase_g"],
                "qty": qty,
                "reason": purchase["purchase_label"],
                "line": purchase["purchase_label"],
                **purchase,
            }
        )
    order = {"buy": 0, "check": 1, "staples": 2}
    lines.sort(key=lambda row: (order.get(row.get("section"), 9), str(row.get("name") or "").lower()))
    return lines


def _usable_recipes(recipes: Sequence[dict]) -> List[dict]:
    out = []
    for rec in recipes or []:
        if not isinstance(rec, dict) or not _recipe_id(rec):
            continue
        if not rec.get("ingredients"):
            continue
        out.append(rec)
    return out


def _pick_recipe(
    recipes: Sequence[dict],
    day: int,
    last_used: Dict[str, int],
    prev_cuisine: str,
    proteins: Dict[str, int],
    already: set,
) -> Optional[dict]:
    def ok(rec: dict, cuisine: bool, protein: bool) -> bool:
        rid = _recipe_id(rec)
        if not rid or rid in already:
            return False
        if rid in last_used and day - last_used[rid] <= RECIPE_REPEAT_DAYS:
            return False
        if protein:
            key = str(rec.get("primary_protein") or "")
            if key and proteins.get(key, 0) >= MAX_MEALS_PER_PROTEIN:
                return False
        if cuisine:
            this = str(rec.get("cuisine") or "")
            if this and prev_cuisine and this == prev_cuisine:
                return False
        return True

    for cuisine, protein in ((True, True), (False, True), (False, False)):
        for rec in recipes:
            if ok(rec, cuisine, protein):
                return rec
    return None


def generate_week_rotation(
    recipes: Sequence[dict],
    *,
    days: int = WEEK_DAYS,
    meals_per_day: int = MEALS_PER_DAY,
    prefix: Optional[Sequence[dict]] = None,
) -> List[dict]:
    """Seven days of library recipes. Posted days stay first and are not rewritten."""
    fixed = [plan for plan in (prefix or []) if isinstance(plan, dict)]
    if len(fixed) >= days:
        return fixed[:days]
    lib = _usable_recipes(recipes)
    last_used: Dict[str, int] = {}
    prev_cuisine = ""
    for day, plan in enumerate(fixed):
        for meal in plan.get("meals") or []:
            if not isinstance(meal, dict):
                continue
            rec = meal.get("recipe") if isinstance(meal.get("recipe"), dict) else {}
            rid = str(meal.get("recipe_id") or rec.get("id") or "").strip()
            if rid:
                last_used[rid] = day
            cuisine = str(meal.get("cuisine") or rec.get("cuisine") or "")
            if cuisine:
                prev_cuisine = cuisine
    out = list(fixed)
    for day in range(len(fixed), days):
        meals: List[dict] = []
        proteins: Dict[str, int] = {}
        already: set = set()
        for _slot in range(meals_per_day):
            pick = _pick_recipe(lib, day, last_used, prev_cuisine, proteins, already)
            if pick is None:
                break
            rid = _recipe_id(pick)
            cuisine = str(pick.get("cuisine") or "")
            protein = str(pick.get("primary_protein") or "")
            meals.append(
                {
                    "recipe_id": rid,
                    "servings": 1,
                    "recipe": pick,
                    "day_index": day,
                    "cuisine": cuisine,
                    "primary_protein": protein,
                    "generated": True,
                }
            )
            last_used[rid] = day
            already.add(rid)
            if cuisine:
                prev_cuisine = cuisine
            if protein:
                proteins[protein] = proteins.get(protein, 0) + 1
        out.append({"day_index": day, "meals": meals, "generated": True})
    return out


def lines_from_plans(
    plans: Sequence[dict],
    inventory: Optional[dict],
    recipes: Optional[Sequence[dict]] = None,
    offers: Optional[dict] = None,
) -> List[dict]:
    """Stock-aware lines for the plans given. Does not invent extra days."""
    demand = sum_recipe_grams(plans, recipes)
    lines = classify_demand(demand, inventory)
    priced = []
    for line in lines:
        if line.get("section") == "staples":
            priced.append(line)
            continue
        priced.append(price_line(dict(line), offers))
    return priced


def assemble_week(
    plans: Optional[Sequence[dict]],
    inventory: Optional[dict],
    recipes: Optional[Sequence[dict]] = None,
    offers: Optional[dict] = None,
    *,
    days: int = WEEK_DAYS,
) -> dict:
    """Fill a posted prefix out to seven days, then classify. No stock write."""
    week = generate_week_rotation(recipes or [], days=days, prefix=plans or [])
    lines = lines_from_plans(week, inventory, recipes, offers)
    buy = [row for row in lines if row.get("section") == "buy"]
    check = [row for row in lines if row.get("section") == "check"]
    staples = [row for row in lines if row.get("section") == "staples"]
    return {
        "days": len(week),
        "plans": week,
        "grocery_week": lines,
        "buy": buy,
        "check": check,
        "staples": staples,
        "writes_stock": False,
        "shopping_tasks": [
            {
                "title": f"Buy {row.get('name')}: {row.get('purchase_label') or row.get('reason')}",
                "id": row.get("id"),
                "kind": f"shopping|buy-{row.get('id') or ''}",
                "proposal": True,
                "creates_stock": False,
            }
            for row in buy
        ],
    }


def shopping_task_hold(buy_lines: Sequence[dict]) -> dict:
    """Park Buy lines on the restock hold. Does not write pantry stock."""
    from .restock_cart import _merge_hold_items, load_hold, save_hold

    incoming = []
    for row in buy_lines or []:
        if not isinstance(row, dict):
            continue
        if row.get("section") not in (None, "buy") and row.get("action") not in (None, "buy", "restock"):
            continue
        incoming.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "action": "buy",
                "venue": row.get("venue") or "walmart",
                "note": row.get("purchase_label") or row.get("reason") or "",
                "status": "task",
            }
        )
    current = load_hold()
    merged = _merge_hold_items(list(current.get("items") or []), incoming)
    return save_hold({"items": merged, "note_title": current.get("note_title")})


def grocery_http(user_id: str, body: Optional[dict] = None) -> dict:
    """POST /api/meal-plan/grocery. Loads inventory. Never persists it."""
    from .inventory_store import load_preview_inventory
    from .recipe_store import list_recipes, load_nourish_recipes

    payload = body if isinstance(body, dict) else {}
    inventory, _source = load_preview_inventory(str(user_id or ""))
    posted_recipes = payload.get("recipes") if isinstance(payload.get("recipes"), list) else None
    if posted_recipes:
        recipes = [rec for rec in posted_recipes if isinstance(rec, dict)]
    else:
        try:
            recipes, _sot = list_recipes(str(user_id or ""), inventory)
        except Exception:
            recipes = []
        if not recipes:
            recipes = load_nourish_recipes(inventory)
    if isinstance(payload.get("plans"), list) and payload.get("plans"):
        prefix = payload.get("plans")
    elif isinstance(payload.get("meals"), list) and payload.get("meals"):
        prefix = [{"meals": payload.get("meals")}]
    else:
        prefix = []
    offers = payload.get("offers") if isinstance(payload.get("offers"), dict) else None
    result = assemble_week(prefix, inventory, recipes, offers)
    result.pop("plans", None)
    result["ok"] = True
    result["tasks_saved"] = False
    if payload.get("create_tasks"):
        try:
            saved = shopping_task_hold(result["buy"])
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": str(exc) or type(exc).__name__,
                "writes_stock": False,
                "grocery_week": result["grocery_week"],
            }
        result["tasks_saved"] = True
        result["hold_count"] = len((saved or {}).get("items") or [])
    return result
