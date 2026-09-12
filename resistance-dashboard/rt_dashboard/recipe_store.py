"""Named recipes in Turso. Macros computed from inventory — never stored.

Turso is SoT. There is no seed library; recipes appear as the coach or
user saves them. File JSON must never overwrite a Turso row.

Servings: yield_servings is the batch size. Ingredient grams_batch are
totals for that yield. Per-serving = grams_batch / yield. Logging
multiplies by N servings (fractional OK).

Drift:
- Ingredient **edit** (macros/name): recipes recompute on read. No stored macros.
- Ingredient **delete**: block (409) while any recipe references it.
  ``force=True`` marks those recipes stale and drops the id from inventory;
  recipe lines stay (stale) so nothing silently vanishes.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .timeutil import local_today_iso

SOT_TURSO = "turso"
FALLBACK_TURSO_DARK = "turso_dark"
SOURCES = ("coach", "user")
MACRO_KEYS = ("calories", "protein_g", "carbs_g", "fat_g")
MICRO_KEYS = ("fiber_g", "sugar_g", "sodium_mg")

ENSURE_RECIPES_SQL = """
CREATE TABLE IF NOT EXISTS nutrition_recipes (
  user_id TEXT NOT NULL,
  id TEXT NOT NULL,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, id)
)
"""

ENSURE_RECIPE_LOGS_SQL = """
CREATE TABLE IF NOT EXISTS nutrition_recipe_logs (
  user_id TEXT NOT NULL,
  local_today TEXT NOT NULL,
  id TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, local_today, id)
)
"""


class IngredientInUseError(ValueError):
    """Delete blocked because recipes still point at this ingredient."""

    def __init__(self, ingredient_id: str, recipes: Sequence[dict]):
        names = [
            str(r.get("name") or r.get("id") or "").strip()
            for r in recipes
            if isinstance(r, dict)
        ]
        names = [n for n in names if n]
        detail = ", ".join(names[:6]) if names else str(ingredient_id)
        super().__init__(f"ingredient in use by recipes: {detail}")
        self.ingredient_id = str(ingredient_id or "").strip()
        self.recipes = [r for r in recipes if isinstance(r, dict)]
        self.error_code = "ingredient_in_use"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n:  # NaN
        return default
    return n


def _inventory_by_id(inventory: Optional[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    ings = (inventory or {}).get("ingredients") if isinstance(inventory, dict) else None
    if not isinstance(ings, list):
        return out
    for ing in ings:
        if not isinstance(ing, dict):
            continue
        iid = str(ing.get("id") or "").strip()
        if iid:
            out[iid] = ing
    return out


def _ingredient_serving_g(ing: dict) -> Optional[float]:
    raw = ing.get("serving_g")
    if raw is None or raw == "":
        return None
    n = _as_float(raw, 0.0)
    return n if n > 0 else None


def fingerprint_ingredients(
    ingredients: Sequence[dict], yield_servings: float
) -> str:
    rows = []
    for line in ingredients or []:
        if not isinstance(line, dict):
            continue
        iid = str(line.get("ingredient_id") or "").strip()
        if not iid:
            continue
        grams = _as_float(line.get("grams_batch"), 0.0)
        servings = _as_float(line.get("servings_batch"), 0.0)
        grams_key = round(grams / 5.0) * 5.0 if grams > 0 else 0.0
        rows.append((iid, grams_key, round(servings, 2)))
    rows.sort()
    return json.dumps(
        {"y": round(_as_float(yield_servings, 1.0), 2), "i": rows},
        separators=(",", ":"),
    )


def name_from_items(items: Sequence[dict]) -> str:
    names = [
        str(it.get("name") or "").strip()
        for it in items or []
        if isinstance(it, dict) and str(it.get("name") or "").strip()
    ]
    if not names:
        return "Untitled recipe"
    if len(names) == 1:
        return names[0][:80]
    if len(names) == 2:
        return f"{names[0]} + {names[1]}"[:80]
    extra = len(names) - 2
    return f"{names[0]}, {names[1]} + {extra} more"[:80]


def compose_from_meal_items(
    items: Sequence[dict],
    *,
    yield_servings: float = 1.0,
    name: str = "",
    source: str = "coach",
    instructions: Optional[Sequence[str]] = None,
) -> dict:
    """Build a recipe dict from planner meal lines. Does not invent grams."""
    lines: List[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id") or it.get("ingredient_id") or "").strip()
        if not iid:
            continue
        grams = _as_float(it.get("portion_g") or it.get("grams_batch"), 0.0)
        servings = _as_float(it.get("servings") or it.get("servings_batch"), 0.0)
        row: Dict[str, Any] = {"ingredient_id": iid}
        if grams > 0:
            row["grams_batch"] = grams
        elif servings > 0:
            row["servings_batch"] = servings
        else:
            continue
        lines.append(row)
    yld = _as_float(yield_servings, 1.0)
    if yld <= 0:
        yld = 1.0
    steps = [
        str(s).strip()
        for s in (instructions or [])
        if str(s).strip()
    ]
    src = str(source or "coach").strip().lower()
    if src not in SOURCES:
        src = "coach"
    title = str(name or "").strip() or name_from_items(items)
    now = _iso_now()
    return {
        "id": "",
        "name": title[:80],
        "yield_servings": yld,
        "ingredients": lines,
        "instructions": steps,
        "source": src,
        "fingerprint": fingerprint_ingredients(lines, yld),
        "created_at": now,
        "updated_at": now,
        "stale": False,
        "stale_ingredient_ids": [],
    }


def normalize_recipe(raw: dict, *, existing: Optional[dict] = None) -> dict:
    base = dict(existing) if isinstance(existing, dict) else {}
    data = dict(raw) if isinstance(raw, dict) else {}
    rid = str(data.get("id") or base.get("id") or "").strip() or _new_id()
    name = str(data.get("name") or base.get("name") or "").strip() or "Untitled recipe"
    yld = _as_float(data.get("yield_servings"), _as_float(base.get("yield_servings"), 1.0))
    if yld <= 0:
        yld = 1.0
    src = str(data.get("source") or base.get("source") or "user").strip().lower()
    if src not in SOURCES:
        src = "user"
    raw_ings = data.get("ingredients")
    if not isinstance(raw_ings, list):
        raw_ings = base.get("ingredients") if isinstance(base.get("ingredients"), list) else []
    lines: List[dict] = []
    for line in raw_ings:
        if not isinstance(line, dict):
            continue
        iid = str(line.get("ingredient_id") or line.get("id") or "").strip()
        if not iid:
            continue
        grams = _as_float(line.get("grams_batch"), 0.0)
        servings = _as_float(line.get("servings_batch"), 0.0)
        row: Dict[str, Any] = {"ingredient_id": iid}
        if grams > 0:
            row["grams_batch"] = grams
        if servings > 0:
            row["servings_batch"] = servings
        if "grams_batch" not in row and "servings_batch" not in row:
            continue
        lines.append(row)
    raw_steps = data.get("instructions")
    if not isinstance(raw_steps, list):
        raw_steps = base.get("instructions") if isinstance(base.get("instructions"), list) else []
    steps = [str(s).strip() for s in raw_steps if str(s).strip()]
    now = _iso_now()
    created = str(base.get("created_at") or data.get("created_at") or now)
    stale_ids = [
        str(x).strip()
        for x in (data.get("stale_ingredient_ids") or base.get("stale_ingredient_ids") or [])
        if str(x).strip()
    ]
    return {
        "id": rid,
        "name": name[:80],
        "yield_servings": yld,
        "ingredients": lines,
        "instructions": steps,
        "source": src,
        "fingerprint": fingerprint_ingredients(lines, yld),
        "created_at": created,
        "updated_at": now,
        "stale": bool(data.get("stale") if "stale" in data else base.get("stale")),
        "stale_ingredient_ids": stale_ids,
    }


def _line_macros(line: dict, ing: Optional[dict], yield_servings: float) -> dict:
    """Per-serving macros for one recipe line. Missing ingredient → zeros + stale."""
    yld = yield_servings if yield_servings > 0 else 1.0
    zeros = {k: 0.0 for k in MACRO_KEYS}
    zeros.update({k: 0.0 for k in MICRO_KEYS})
    if not isinstance(ing, dict) or not ing:
        return {**zeros, "stale": True}
    grams = _as_float(line.get("grams_batch"), 0.0)
    servings_batch = _as_float(line.get("servings_batch"), 0.0)
    sg = _ingredient_serving_g(ing)
    if grams > 0 and sg and sg > 0:
        n = (grams / sg) / yld
    elif servings_batch > 0:
        n = servings_batch / yld
    else:
        return {**zeros, "stale": False}
    out = {k: round(float(ing.get(k) or 0) * n, 1) for k in MACRO_KEYS}
    for mk in MICRO_KEYS:
        if ing.get(mk) is None or ing.get(mk) == "":
            continue
        out[mk] = round(_as_float(ing.get(mk), 0.0) * n, 1 if mk != "sodium_mg" else 0)
    out["stale"] = False
    return out


def compute_recipe_macros(recipe: dict, inventory: Optional[dict]) -> dict:
    """Attach per-serving and batch macros. Marks stale lines; never invents food."""
    rec = dict(recipe or {})
    yld = _as_float(rec.get("yield_servings"), 1.0)
    if yld <= 0:
        yld = 1.0
        rec["yield_servings"] = yld
    by_id = _inventory_by_id(inventory)
    stale_ids: List[str] = []
    per = {k: 0.0 for k in MACRO_KEYS}
    per.update({k: 0.0 for k in MICRO_KEYS})
    resolved: List[dict] = []
    for line in rec.get("ingredients") or []:
        if not isinstance(line, dict):
            continue
        iid = str(line.get("ingredient_id") or "").strip()
        ing = by_id.get(iid)
        macros = _line_macros(line, ing, yld)
        grams = _as_float(line.get("grams_batch"), 0.0)
        per_g = round(grams / yld, 1) if grams > 0 else None
        row = {
            **line,
            "name": str((ing or {}).get("name") or "") or iid,
            "per_serving_g": per_g,
            "macros_per_serving": {k: macros.get(k) for k in MACRO_KEYS},
            "missing": ing is None,
        }
        if macros.get("stale") or ing is None:
            stale_ids.append(iid)
            row["missing"] = True
        resolved.append(row)
        if ing is None:
            continue
        for k in MACRO_KEYS:
            per[k] += float(macros.get(k) or 0)
        for k in MICRO_KEYS:
            if k in macros:
                per[k] += float(macros.get(k) or 0)
    rec["ingredients"] = resolved
    rec["macros_per_serving"] = {k: round(per[k], 1 if k != "sodium_mg" else 0) for k in per}
    rec["macros_batch"] = {
        k: round(v * yld, 1 if k != "sodium_mg" else 0) for k, v in rec["macros_per_serving"].items()
    }
    rec["stale"] = bool(stale_ids)
    rec["stale_ingredient_ids"] = stale_ids
    return rec


def scale_servings(recipe: dict, servings: float, inventory: Optional[dict] = None) -> dict:
    """Macros for N servings via batch arithmetic. Fractional servings OK."""
    n = _as_float(servings, 0.0)
    if n < 0:
        n = 0.0
    computed = compute_recipe_macros(recipe, inventory)
    yld = _as_float(computed.get("yield_servings"), 1.0) or 1.0
    batch = computed.get("macros_batch") or {}
    factor = n / yld
    macros = {
        k: round(float(batch.get(k) or 0) * factor, 1 if k != "sodium_mg" else 0)
        for k in list(MACRO_KEYS) + list(MICRO_KEYS)
        if k in batch or k in MACRO_KEYS
    }
    lines = []
    for line in computed.get("ingredients") or []:
        grams = _as_float(line.get("grams_batch"), 0.0)
        scaled = dict(line)
        if grams > 0:
            scaled["grams"] = round(grams * factor, 1)
        lines.append(scaled)
    return {
        "recipe_id": computed.get("id"),
        "name": computed.get("name"),
        "servings": n,
        "yield_servings": yld,
        "macros": macros,
        "ingredients": lines,
        "stale": bool(computed.get("stale")),
    }


def recipes_using_ingredient(recipes: Sequence[dict], ingredient_id: str) -> List[dict]:
    want = str(ingredient_id or "").strip()
    if not want:
        return []
    hit = []
    for rec in recipes or []:
        if not isinstance(rec, dict):
            continue
        for line in rec.get("ingredients") or []:
            if not isinstance(line, dict):
                continue
            if str(line.get("ingredient_id") or "").strip() == want:
                hit.append(rec)
                break
    return hit


def mark_stale_for_ingredient(recipe: dict, ingredient_id: str) -> dict:
    rec = dict(recipe or {})
    want = str(ingredient_id or "").strip()
    ids = [
        str(x).strip()
        for x in (rec.get("stale_ingredient_ids") or [])
        if str(x).strip()
    ]
    if want and want not in ids:
        ids.append(want)
    rec["stale_ingredient_ids"] = ids
    rec["stale"] = True
    rec["updated_at"] = _iso_now()
    return rec


def shopping_from_plans(
    plans: Sequence[dict],
    inventory: Optional[dict],
    recipes: Optional[Sequence[dict]] = None,
) -> List[dict]:
    """Aggregate recipe demand vs pantry stock flags. No invented pantry grams."""
    by_id = _inventory_by_id(inventory)
    by_recipe = {
        str(r.get("id") or ""): r
        for r in (recipes or [])
        if isinstance(r, dict) and str(r.get("id") or "").strip()
    }
    demand: Dict[str, float] = {}
    names: Dict[str, str] = {}
    for plan in plans or []:
        if not isinstance(plan, dict):
            continue
        for meal in plan.get("meals") or []:
            if not isinstance(meal, dict):
                continue
            rec = meal.get("recipe") if isinstance(meal.get("recipe"), dict) else None
            if rec is None:
                rec = by_recipe.get(str(meal.get("recipe_id") or "").strip())
            servings = _as_float(meal.get("servings"), 1.0)
            yld = 1.0
            lines: Sequence[dict] = []
            if rec:
                yld = _as_float(rec.get("yield_servings"), 1.0) or 1.0
                lines = rec.get("ingredients") or []
            else:
                continue
            factor = servings / yld
            for line in lines:
                if not isinstance(line, dict):
                    continue
                iid = str(line.get("ingredient_id") or "").strip()
                grams = _as_float(line.get("grams_batch"), 0.0)
                if not iid or grams <= 0:
                    continue
                demand[iid] = demand.get(iid, 0.0) + grams * factor
                names[iid] = str(line.get("name") or (by_id.get(iid) or {}).get("name") or iid)
    out: List[dict] = []
    for iid, grams in sorted(demand.items(), key=lambda kv: names.get(kv[0], kv[0]).lower()):
        ing = by_id.get(iid) or {}
        stock = str(ing.get("stock") or ("in" if ing.get("in_stock") else "out")).strip().lower()
        if stock not in ("in", "low", "out"):
            stock = "out" if not ing else "in"
        if stock == "in":
            continue
        out.append(
            {
                "action": "restock",
                "id": iid,
                "name": names.get(iid) or iid,
                "grams": round(grams, 1),
                "stock": stock,
                "reason": f"{round(grams):g}g needed for planned recipes",
                "source": "recipe_plan",
            }
        )
    return out


def _turso():
    from .turso_http import connect, turso_enabled

    return connect, turso_enabled


def _parse_payload(raw: Any) -> Optional[dict]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_recipes(user_id: str, inventory: Optional[dict] = None) -> Tuple[List[dict], str]:
    """Turso recipes for the user. Empty list + turso_dark when Turso is down."""
    connect, turso_enabled = _turso()
    uid = str(user_id or "").strip()
    if not uid or not turso_enabled():
        return [], FALLBACK_TURSO_DARK
    try:
        with connect() as conn:
            conn.execute(ENSURE_RECIPES_SQL)
            rows = conn.execute(
                """
                SELECT id, payload FROM nutrition_recipes
                WHERE user_id = ?
                ORDER BY updated_at DESC
                """,
                (uid,),
            ).fetchall()
    except Exception:
        return [], FALLBACK_TURSO_DARK
    out: List[dict] = []
    for row in rows or []:
        payload = row["payload"] if isinstance(row, dict) else row[1]
        data = _parse_payload(payload)
        if not data:
            continue
        if not data.get("id") and isinstance(row, dict):
            data["id"] = row.get("id")
        out.append(compute_recipe_macros(normalize_recipe(data), inventory))
    return out, SOT_TURSO


def get_recipe(user_id: str, recipe_id: str, inventory: Optional[dict] = None) -> Optional[dict]:
    connect, turso_enabled = _turso()
    uid = str(user_id or "").strip()
    rid = str(recipe_id or "").strip()
    if not uid or not rid or not turso_enabled():
        return None
    try:
        with connect() as conn:
            conn.execute(ENSURE_RECIPES_SQL)
            row = conn.execute(
                """
                SELECT payload FROM nutrition_recipes
                WHERE user_id = ? AND id = ?
                """,
                (uid, rid),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    payload = row["payload"] if isinstance(row, dict) else row[0]
    data = _parse_payload(payload)
    if not data:
        return None
    data.setdefault("id", rid)
    return compute_recipe_macros(normalize_recipe(data), inventory)


def upsert_recipe(user_id: str, raw: dict, inventory: Optional[dict] = None) -> dict:
    connect, turso_enabled = _turso()
    uid = str(user_id or "").strip()
    if not uid:
        raise ValueError("user_id required")
    if not turso_enabled():
        raise RuntimeError("turso env missing")
    existing = None
    rid = str((raw or {}).get("id") or "").strip()
    if rid:
        existing = get_recipe(uid, rid, inventory=None)
    rec = normalize_recipe(raw or {}, existing=existing)
    blob = json.dumps(
        {k: rec[k] for k in rec if k not in ("macros_per_serving", "macros_batch")},
        separators=(",", ":"),
    )
    with connect() as conn:
        conn.execute(ENSURE_RECIPES_SQL)
        conn.execute(
            """
            INSERT INTO nutrition_recipes(user_id, id, payload, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (uid, rec["id"], blob, rec["updated_at"]),
        )
    saved = get_recipe(uid, rec["id"], inventory=inventory)
    if not saved:
        raise RuntimeError("turso write not visible on readback")
    return saved


def find_recipe_by_fingerprint(
    user_id: str, fingerprint: str, inventory: Optional[dict] = None
) -> Optional[dict]:
    fp = str(fingerprint or "").strip()
    if not fp:
        return None
    recipes, _src = list_recipes(user_id, inventory=inventory)
    for rec in recipes:
        if str(rec.get("fingerprint") or "") == fp:
            return rec
    return None


def delete_recipe(user_id: str, recipe_id: str) -> bool:
    connect, turso_enabled = _turso()
    uid = str(user_id or "").strip()
    rid = str(recipe_id or "").strip()
    if not uid or not rid:
        raise ValueError("id required")
    if not turso_enabled():
        raise RuntimeError("turso env missing")
    with connect() as conn:
        conn.execute(ENSURE_RECIPES_SQL)
        conn.execute(
            "DELETE FROM nutrition_recipes WHERE user_id = ? AND id = ?",
            (uid, rid),
        )
    return True


def recipes_blocking_ingredient(user_id: str, ingredient_id: str) -> List[dict]:
    recipes, _src = list_recipes(user_id, inventory=None)
    return recipes_using_ingredient(recipes, ingredient_id)


def assert_ingredient_not_in_use(user_id: str, ingredient_id: str) -> None:
    hit = recipes_blocking_ingredient(user_id, ingredient_id)
    if hit:
        raise IngredientInUseError(ingredient_id, hit)


def mark_recipes_stale_for_ingredient(user_id: str, ingredient_id: str) -> int:
    hit = recipes_blocking_ingredient(user_id, ingredient_id)
    n = 0
    for rec in hit:
        upsert_recipe(user_id, mark_stale_for_ingredient(rec, ingredient_id))
        n += 1
    return n


def attach_recipes_to_plan(
    plan: dict,
    inventory: Optional[dict],
    user_id: str,
) -> dict:
    """Stamp recipe_id + servings on meal buckets. Persist coach recipes (deduped)."""
    out = dict(plan or {})
    meals = list(out.get("meals") or [])
    uid = str(user_id or "").strip()
    stamped: List[dict] = []
    for meal in meals:
        if not isinstance(meal, dict):
            continue
        row = dict(meal)
        existing_id = str(row.get("recipe_id") or "").strip()
        if existing_id:
            rec = get_recipe(uid, existing_id, inventory=inventory) if uid else None
            if rec:
                row["recipe_id"] = rec["id"]
                row["recipe_name"] = rec.get("name")
                row["servings"] = _as_float(row.get("servings"), 1.0) or 1.0
                row["recipe"] = rec
                stamped.append(row)
                continue
        items = [it for it in (row.get("items") or []) if isinstance(it, dict)]
        drafted = compose_from_meal_items(items, yield_servings=1.0, source="coach")
        if not drafted.get("ingredients"):
            stamped.append(row)
            continue
        if not uid:
            row["recipe_name"] = drafted.get("name")
            row["servings"] = 1.0
            stamped.append(row)
            continue
        try:
            found = find_recipe_by_fingerprint(
                uid, drafted["fingerprint"], inventory=inventory
            )
            if found:
                saved = found
            else:
                saved = upsert_recipe(uid, drafted, inventory=inventory)
        except Exception:
            row["recipe_name"] = drafted.get("name")
            row["servings"] = 1.0
            stamped.append(row)
            continue
        row["recipe_id"] = saved["id"]
        row["recipe_name"] = saved.get("name")
        row["servings"] = _as_float(row.get("servings"), 1.0) or 1.0
        row["recipe"] = saved
        stamped.append(row)
    out["meals"] = stamped
    return out


def log_recipe_servings(
    user_id: str,
    recipe_id: str,
    servings: float,
    *,
    day: Optional[str] = None,
    inventory: Optional[dict] = None,
) -> dict:
    connect, turso_enabled = _turso()
    uid = str(user_id or "").strip()
    rid = str(recipe_id or "").strip()
    n = _as_float(servings, 0.0)
    if not uid:
        raise ValueError("user_id required")
    if not rid:
        raise ValueError("recipe_id required")
    if n <= 0:
        raise ValueError("servings must be > 0")
    if not turso_enabled():
        raise RuntimeError("turso env missing")
    rec = get_recipe(uid, rid, inventory=inventory)
    if not rec:
        raise ValueError("recipe not found")
    scaled = scale_servings(rec, n, inventory=inventory)
    civil = str(day or local_today_iso())[:10]
    log_id = _new_id()
    now = _iso_now()
    payload = {
        "id": log_id,
        "recipe_id": rid,
        "name": rec.get("name"),
        "servings": n,
        "date": civil,
        "macros": scaled.get("macros") or {},
        "source": "fitdash_recipe",
        "created_at": now,
    }
    blob = json.dumps(payload, separators=(",", ":"))
    with connect() as conn:
        conn.execute(ENSURE_RECIPE_LOGS_SQL)
        conn.execute(
            """
            INSERT INTO nutrition_recipe_logs(
              user_id, local_today, id, payload, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (uid, civil, log_id, blob, now),
        )
    return payload


def list_recipe_logs(user_id: str, day: str) -> List[dict]:
    connect, turso_enabled = _turso()
    uid = str(user_id or "").strip()
    civil = str(day or "")[:10]
    if not uid or not civil or not turso_enabled():
        return []
    try:
        with connect() as conn:
            conn.execute(ENSURE_RECIPE_LOGS_SQL)
            rows = conn.execute(
                """
                SELECT payload FROM nutrition_recipe_logs
                WHERE user_id = ? AND local_today = ?
                ORDER BY created_at ASC
                """,
                (uid, civil),
            ).fetchall()
    except Exception:
        return []
    out: List[dict] = []
    for row in rows or []:
        payload = row["payload"] if isinstance(row, dict) else row[0]
        data = _parse_payload(payload)
        if data:
            out.append(data)
    return out


def recipe_logs_as_food_entries(logs: Sequence[dict]) -> List[dict]:
    """Shape recipe logs like Health food_logs_today rows for the Logged today UI."""
    out: List[dict] = []
    for log in logs or []:
        if not isinstance(log, dict):
            continue
        macros = log.get("macros") if isinstance(log.get("macros"), dict) else {}
        servings = _as_float(log.get("servings"), 0.0)
        serve = f"{servings:g} serving" + ("" if servings == 1 else "s")
        out.append(
            {
                "date": str(log.get("date") or "")[:10],
                "name": str(log.get("name") or "Recipe"),
                "calories": macros.get("calories"),
                "protein_g": macros.get("protein_g"),
                "carbs_g": macros.get("carbs_g"),
                "fat_g": macros.get("fat_g"),
                "fiber_g": macros.get("fiber_g"),
                "sugar_g": macros.get("sugar_g"),
                "sodium_mg": macros.get("sodium_mg"),
                "meal_type": "recipe",
                "serving_label": serve,
                "source": "fitdash_recipe",
                "recipe_id": log.get("recipe_id"),
                "servings": servings,
            }
        )
    return out


def add_recipe_logs_to_consumed(consumed: Optional[dict], logs: Sequence[dict]) -> dict:
    """Add FitDash recipe-log macros onto today's consumed (not Health double-count)."""
    out = dict(consumed or {})
    extra = {k: 0.0 for k in MACRO_KEYS}
    n = 0
    for log in logs or []:
        if not isinstance(log, dict):
            continue
        macros = log.get("macros") if isinstance(log.get("macros"), dict) else {}
        n += 1
        for k in MACRO_KEYS:
            extra[k] += _as_float(macros.get(k), 0.0)
    if not n:
        return out
    for k in MACRO_KEYS:
        out[k] = round(_as_float(out.get(k), 0.0) + extra[k], 1)
    out["recipe_log_count"] = n
    return out


def overlay_recipes_on_nutrition(
    *,
    user_id: str,
    day: str,
    inventory: Optional[dict],
    meal_plan: Optional[dict],
    consumed: Optional[dict],
    food_logs_today: Optional[list],
) -> dict:
    """Attach recipes, logs, shopping onto the nutrition_store slice."""
    uid = str(user_id or "").strip()
    civil = str(day or "")[:10]
    plan = dict(meal_plan or {})
    recipes: List[dict] = []
    source = FALLBACK_TURSO_DARK
    if uid:
        plan = attach_recipes_to_plan(plan, inventory, uid)
        recipes, source = list_recipes(uid, inventory=inventory)
    logs = list_recipe_logs(uid, civil) if uid and civil else []
    food_rows = list(food_logs_today or [])
    food_rows.extend(recipe_logs_as_food_entries(logs))
    consumed_out = add_recipe_logs_to_consumed(consumed, logs)
    week_plans = [plan]
    if uid and civil:
        week_plans = _week_plans(uid, civil, plan)
    shopping = shopping_from_plans(week_plans, inventory, recipes)
    return {
        "meal_plan": plan,
        "recipes": recipes,
        "recipes_sot": source,
        "recipe_logs_today": logs,
        "food_logs_today": food_rows,
        "today_consumed": consumed_out,
        "recipe_shopping": shopping,
    }


def _week_plans(user_id: str, day: str, today_plan: dict) -> List[dict]:
    plans = [today_plan]
    try:
        from .meal_plan_store import load_last_good_meal_plan

        start = datetime.strptime(str(day)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return plans
    for i in range(1, 7):
        other = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        loaded = load_last_good_meal_plan(user_id, other)
        if loaded:
            plans.append(loaded)
    return plans
