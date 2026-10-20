"""Whole-recipe day plans. Ingredient filler stays in nutrition_planner.

A plan is library dishes scaled onto the existing eat_at engine. Mixed
ingredient plates are only produced by generate_meal_plan when this
search returns None (mode ingredient_fallback).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .nutrition_planner import (
    UPCOMING_MEAL_LABELS,
    _clamp_gap_times,
    _clamp_into_window,
    _clock_label,
    _consumed_for_planner,
    _dedupe_sorted_times,
    _eating_window_bounds,
    _kitchen_is_closed,
    _meal_tz,
    _nutrition_day_payload,
    _parse_meal_dt,
    _remaining_meal_capacity,
    _resolve_eat_times,
    _sleep_windows_for_plan,
    clamp_resolved_times,
    egg_role,
    is_in_stock,
    normalize_targets,
    remaining_macros,
)

STALE_PANTRY_DAYS = 21
RECIPE_REPEAT_DAYS = 2
MAX_MEALS_PER_PROTEIN = 2
ROLE_LABELS = {
    "pre-workout": "Pre-workout",
    "post-workout": "Post-workout",
    "first": "First meal",
    "lunch": "Lunch",
    "dinner": "Dinner",
    "snack": "Snack",
}
_FILL_AFTER_POST = ("dinner", "snack", "lunch")
_FILL_REST = ("first", "lunch", "dinner", "snack")
_PLANNER_KEYS = (
    "slots",
    "cuisine",
    "prep_min",
    "batch_friendly",
    "fridge_days",
    "primary_protein",
    "scalable",
)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _day(value: Any, tz) -> Optional[datetime]:
    """Civil YYYY-MM-DD stays on that calendar day in ``tz`` (not UTC midnight)."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=tz)
        return dt.astimezone(tz)
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            civil = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            civil = None
        if civil is not None and len(text) == 10:
            return civil.replace(tzinfo=tz)
    dt = _parse_meal_dt(value)
    if dt is None:
        return None
    return dt.astimezone(tz)


def coerce_workout_window(raw: Any, tz) -> Optional[Tuple[datetime, datetime]]:
    """Accept {start, end} or a (start, end) pair. None when it is not a window."""
    start = end = None
    if isinstance(raw, dict):
        start = _day(raw.get("start") or raw.get("start_at"), tz)
        end = _day(raw.get("end") or raw.get("end_at"), tz)
    elif isinstance(raw, (list, tuple)) and len(raw) == 2:
        start = _day(raw[0], tz)
        end = _day(raw[1], tz)
    if start is None or end is None or end <= start:
        return None
    return start, end


def _touch_times(ing: dict, logs: Sequence[dict], confirms: Sequence[dict], tz) -> List[datetime]:
    found: List[datetime] = []
    iid = str(ing.get("id") or "").strip().lower()
    name = str(ing.get("name") or "").strip().lower()
    for key in ("updated_at", "last_used_at", "restocked_at", "received_at"):
        dt = _parse_meal_dt(ing.get(key))
        if dt is not None:
            found.append(dt.astimezone(tz))
    for log in logs or []:
        if not isinstance(log, dict):
            continue
        lid = str(log.get("ingredient_id") or log.get("id") or "").strip().lower()
        lname = str(log.get("name") or "").strip().lower()
        if lid != iid and (not name or lname != name):
            continue
        dt = _parse_meal_dt(log.get("time") or log.get("logged_at") or log.get("date"))
        if dt is not None:
            found.append(dt.astimezone(tz))
    for row in confirms or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("ingredient_id") or row.get("id") or "").strip().lower()
        cname = str(row.get("name") or "").strip().lower()
        if cid != iid and (not name or cname != name):
            continue
        dt = _parse_meal_dt(row.get("at") or row.get("confirmed_at") or row.get("received_at"))
        if dt is not None:
            found.append(dt.astimezone(tz))
    return found


def pantry_is_fresh(
    ing: dict,
    *,
    now: datetime,
    logs: Optional[Sequence[dict]] = None,
    confirms: Optional[Sequence[dict]] = None,
    stale_days: int = STALE_PANTRY_DAYS,
) -> bool:
    """In-stock food with a log, restock, or stamp inside N days.

    A null stamp and no log is unconfirmed — not fresh. Out of stock is
    never fresh.
    """
    if not isinstance(ing, dict) or not is_in_stock(ing):
        return False
    touches = _touch_times(ing, logs or [], confirms or [], now.tzinfo)
    if not touches:
        return False
    latest = max(touches)
    return (now - latest) <= timedelta(days=stale_days)


def _inventory_by_id(inventory: Optional[dict]) -> Dict[str, dict]:
    rows = (inventory or {}).get("ingredients") if isinstance(inventory, dict) else None
    out: Dict[str, dict] = {}
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("id") or "").strip():
            out[str(row["id"]).strip()] = row
    return out


def _grams_cap(ing: dict) -> Optional[float]:
    for key in ("grams_on_hand", "quantity_g", "qty_g"):
        raw = ing.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def collect_recipe_gaps(
    recipes: Sequence[dict],
    inventory: Optional[dict],
    *,
    now: Optional[datetime] = None,
    food_logs: Optional[Sequence[dict]] = None,
    restock_confirms: Optional[Sequence[dict]] = None,
    stale_days: int = STALE_PANTRY_DAYS,
) -> dict:
    """Restock lines for blocked dishes, and 'Still have this?' for stale stock."""
    tz = now.tzinfo if now is not None and now.tzinfo else timezone.utc
    clock = now.astimezone(tz) if now is not None else datetime.now(tz)
    by_id = _inventory_by_id(inventory)
    prompts: List[dict] = []
    seen_prompt = set()
    for ing in by_id.values():
        if not is_in_stock(ing):
            continue
        if pantry_is_fresh(ing, now=clock, logs=food_logs, confirms=restock_confirms, stale_days=stale_days):
            continue
        iid = str(ing.get("id") or "")
        if iid in seen_prompt:
            continue
        seen_prompt.add(iid)
        prompts.append(
            {
                "id": iid,
                "name": str(ing.get("name") or iid),
                "text": "Still have this?",
            }
        )
    unlocks: List[dict] = []
    seen_unlock = set()
    for rec in recipes or []:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("name") or rec.get("id") or "").strip()
        rid = str(rec.get("id") or "").strip()
        for line in rec.get("ingredients") or []:
            if not isinstance(line, dict):
                continue
            iid = str(line.get("ingredient_id") or line.get("id") or "").strip()
            if not iid:
                continue
            ing = by_id.get(iid)
            fresh = bool(
                ing
                and pantry_is_fresh(
                    ing, now=clock, logs=food_logs, confirms=restock_confirms, stale_days=stale_days
                )
            )
            if fresh:
                continue
            key = (iid, rid)
            if key in seen_unlock:
                continue
            seen_unlock.add(key)
            label = str((ing or {}).get("name") or iid.replace("-", " ").title())
            unlocks.append(
                {
                    "action": "restock",
                    "id": iid,
                    "name": label,
                    "recipe_id": rid,
                    "recipe_name": name,
                    "source": "recipe_unlock",
                    "reason": f"{label} → unlocks {name}",
                    "score": 100000,
                }
            )
            if len(unlocks) >= 12:
                break
        if len(unlocks) >= 12:
            break
    return {"prompts": prompts, "unlocks": unlocks}


def _shift_targets(targets: dict, training: bool) -> dict:
    if "carb_shift_g" not in targets or targets.get("carb_shift_g") is None:
        return targets
    try:
        shift = float(targets["carb_shift_g"])
    except (TypeError, ValueError):
        return targets
    delta = shift if training else -shift
    out = dict(targets)
    out["carbs_g"] = round(max(0.0, float(out.get("carbs_g") or 0) + delta), 1)
    out["fat_g"] = round(max(0.0, float(out.get("fat_g") or 0) - (delta * 4.0) / 9.0), 1)
    return out


def _anchor(moment: datetime, hours: float = 0, minutes: float = 0) -> datetime:
    return (moment + timedelta(hours=hours, minutes=minutes)).replace(second=0, microsecond=0)


def resolve_recipe_times(
    n: int,
    *,
    now: datetime,
    start: datetime,
    end: datetime,
    tz,
    eat_slots: Optional[Sequence[Any]] = None,
    consumed: Any = None,
    targets: Any = None,
    sleep_windows: Optional[Sequence[tuple]] = None,
    workout: Optional[Tuple[datetime, datetime]] = None,
) -> List[datetime]:
    """Engine times. A workout window overlays pre (start−2h) and post (end+45m)."""
    engine = _resolve_eat_times(
        n,
        now=now,
        start=start,
        end=end,
        tz=tz,
        eat_slots=eat_slots,
        consumed=consumed,
        targets=targets,
        sleep_windows=sleep_windows,
    )
    if not workout or n <= 0:
        return list(engine)
    wstart, wend = workout
    horizon = now - timedelta(minutes=20)
    pre = _anchor(wstart, hours=-2)
    pre_lo = _anchor(wstart, hours=-3)
    pre_hi = _anchor(wstart, minutes=-90)
    if pre < pre_lo:
        pre = pre_lo
    if pre > pre_hi:
        pre = pre_hi
    pre = _clamp_into_window(pre, start, end)
    if pre < horizon:
        pre = None
    post = _anchor(wend, minutes=45)
    post_lo = _anchor(wend, minutes=30)
    post_hi = _anchor(wend, minutes=60)
    if post < post_lo:
        post = post_lo
    if post > post_hi:
        post = post_hi
    post = _clamp_into_window(post, start, end)
    if post < horizon:
        post = None
    times = list(engine)

    def _replace(target: Optional[datetime], pred) -> None:
        if target is None:
            return
        idxs = [i for i, item in enumerate(times) if pred(item)]
        if not idxs:
            times.append(target)
            return
        best = min(idxs, key=lambda i: abs((times[i] - target).total_seconds()))
        times[best] = target

    _replace(pre, lambda item: item < wstart)
    _replace(post, lambda item: item > wend)
    times = [item for item in times if not (wstart <= item < wend)]
    anchors = [item for item in (pre, post) if item is not None]
    for anchor in anchors:
        if all(item != anchor for item in times):
            times.append(anchor)
    times = _dedupe_sorted_times(times)
    while len(times) > n:
        extras = [item for item in times if item not in anchors]
        if not extras:
            break
        times.remove(extras[-1])
    times = _clamp_gap_times(times, max(n, len(anchors)), end)
    for anchor in anchors:
        if anchor not in times:
            times.append(anchor)
    times = _dedupe_sorted_times(times)
    if len(times) > n:
        kept = [item for item in times if item in anchors]
        for item in times:
            if item in kept:
                continue
            if len(kept) >= n:
                break
            kept.append(item)
        times = _dedupe_sorted_times(kept)[:n]
    return clamp_resolved_times(times, sleep_windows or [], now=now)


def _assign_roles(
    times: Sequence[datetime], workout: Optional[Tuple[datetime, datetime]]
) -> List[str]:
    roles: List[Optional[str]] = [None] * len(times)
    if workout:
        wstart, wend = workout
        for i, moment in enumerate(times):
            hours_before = (wstart - moment).total_seconds() / 3600.0
            if 1.5 - 1e-6 <= hours_before <= 3.0 + 1e-6:
                roles[i] = "pre-workout"
                break
        for i, moment in enumerate(times):
            if roles[i]:
                continue
            hours_after = (moment - wend).total_seconds() / 3600.0
            if -1e-6 <= hours_after <= 2.0 + 1e-6:
                roles[i] = "post-workout"
                break
    fill = list(_FILL_AFTER_POST if "pre-workout" in roles or "post-workout" in roles else _FILL_REST)
    cursor = 0
    for i, moment in enumerate(times):
        if roles[i]:
            continue
        if workout and moment < workout[0] and "first" not in roles:
            roles[i] = "first"
            continue
        while cursor < len(fill) and fill[cursor] in roles:
            cursor += 1
        roles[i] = fill[cursor] if cursor < len(fill) else "snack"
        cursor += 1
    return [role or "snack" for role in roles]


def _scales(recipe: dict) -> List[float]:
    raw = recipe.get("scalable") if isinstance(recipe.get("scalable"), dict) else {}
    try:
        lo = float(raw.get("min", 0.75))
        hi = float(raw.get("max", 1.5))
        step = float(raw.get("step", 0.25))
    except (TypeError, ValueError):
        return [1.0]
    if step <= 0 or hi < lo:
        return [round(lo, 2)]
    if abs(hi - lo) < 1e-9:
        return [round(lo, 2)]
    out: List[float] = []
    cursor = lo
    while cursor <= hi + 1e-9 and len(out) < 8:
        out.append(round(cursor, 2))
        cursor += step
    return out or [1.0]


def _recipe_slots(recipe: dict) -> List[str]:
    raw = recipe.get("slots") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def _protein_source(recipe: dict) -> str:
    named = str(recipe.get("primary_protein") or "").strip().lower()
    if named:
        return named
    best = ""
    best_p = -1.0
    for line in recipe.get("ingredients") or []:
        if not isinstance(line, dict):
            continue
        macros = line.get("macros_per_serving") if isinstance(line.get("macros_per_serving"), dict) else {}
        protein = float(macros.get("protein_g") or 0)
        if protein > best_p:
            best_p = protein
            best = str(line.get("ingredient_id") or "").strip().lower()
    return best


def _line_grams(line: dict, scale: float, yield_servings: float) -> float:
    yld = yield_servings if yield_servings > 0 else 1.0
    grams = 0.0
    try:
        grams = float(line.get("grams_batch") or line.get("grams") or 0)
    except (TypeError, ValueError):
        grams = 0.0
    if grams > 0:
        return round(grams * (scale / yld), 1)
    return 0.0


def _candidate(
    recipe: dict,
    scale: float,
    inventory: dict,
    *,
    role: str,
    earliest: bool,
) -> Optional[dict]:
    from .recipe_store import scale_servings

    if recipe.get("stale"):
        return None
    slots = _recipe_slots(recipe)
    if slots and role not in slots:
        return None
    scaled = scale_servings(recipe, scale, inventory)
    if scaled.get("stale"):
        return None
    macros = scaled.get("macros") or {}
    protein = float(macros.get("protein_g") or 0)
    carbs = float(macros.get("carbs_g") or 0)
    fat = float(macros.get("fat_g") or 0)
    fiber = float(macros.get("fiber_g") or 0)
    kcal = float(macros.get("calories") or 0)
    if earliest and not (49.95 <= protein <= 60.05):
        return None
    if role == "pre-workout":
        if not (39.95 <= carbs <= 60.05):
            return None
        if fat > 10.05 or fiber > 6.05:
            return None
    by_id = _inventory_by_id(inventory)
    yld = float(recipe.get("yield_servings") or 1) or 1.0
    grams: Dict[str, float] = {}
    items: List[dict] = []
    for line in scaled.get("ingredients") or []:
        if not isinstance(line, dict):
            continue
        iid = str(line.get("ingredient_id") or "").strip()
        if not iid:
            continue
        portion = line.get("grams")
        if portion is None:
            portion = _line_grams(line, scale, yld)
        try:
            portion_g = round(float(portion or 0), 1)
        except (TypeError, ValueError):
            portion_g = 0.0
        grams[iid] = grams.get(iid, 0.0) + portion_g
        per = line.get("macros_per_serving") if isinstance(line.get("macros_per_serving"), dict) else {}
        factor = scale / yld
        item = {
            "id": iid,
            "name": str(line.get("name") or iid),
            "portion_g": portion_g,
            "calories": round(float(per.get("calories") or 0) * factor, 1),
            "protein_g": round(float(per.get("protein_g") or 0) * factor, 1),
            "carbs_g": round(float(per.get("carbs_g") or 0) * factor, 1),
            "fat_g": round(float(per.get("fat_g") or 0) * factor, 1),
            "fiber_g": round(float(per.get("fiber_g") or 0) * factor, 1),
            "serving_label": f"{portion_g:g}g" if portion_g else str(line.get("name") or ""),
        }
        egg = egg_role({"id": iid, "name": item["name"]})
        if egg:
            item["group_id"] = "eggs"
            item["group_label"] = "Whole eggs + egg whites"
        items.append(item)
        ing = by_id.get(iid)
        cap = _grams_cap(ing) if ing else None
        if cap is not None and grams[iid] > cap + 0.2:
            return None
    steps = recipe.get("steps") or recipe.get("instructions") or []
    steps = [str(step).strip() for step in steps if str(step).strip()]
    local = abs(protein - 52.0) + (abs(carbs - 50.0) * 0.15 if role == "pre-workout" else 0)
    if role == "dinner":
        local += carbs * 0.05
    return {
        "recipe_id": str(recipe.get("id") or ""),
        "recipe_name": str(recipe.get("name") or ""),
        "servings": scale,
        "role": role,
        "cuisine": str(recipe.get("cuisine") or "").strip(),
        "primary_protein": _protein_source(recipe),
        "batch_friendly": bool(recipe.get("batch_friendly")),
        "prep_min": recipe.get("prep_min"),
        "fridge_days": recipe.get("fridge_days"),
        "steps": steps,
        "items": items,
        "grams": grams,
        "calories": round(kcal, 1),
        "protein_g": round(protein, 1),
        "carbs_g": round(carbs, 1),
        "fat_g": round(fat, 1),
        "fiber_g": round(fiber, 1),
        "local": local,
    }


def _blocked_ids(history: Sequence[dict], day, tz) -> Dict[str, datetime]:
    last: Dict[str, datetime] = {}
    for row in history or []:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("recipe_id") or "").strip()
        if not rid:
            continue
        when = _day(row.get("date") or row.get("day"), tz)
        if when is None:
            continue
        when = when.replace(hour=0, minute=0, second=0, microsecond=0)
        prev = last.get(rid)
        if prev is None or when > prev:
            last[rid] = when
    blocked = set()
    today = day.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    for rid, when in last.items():
        delta = (today - when).days
        if 0 < delta <= RECIPE_REPEAT_DAYS:
            blocked.add(rid)
    return {rid: last[rid] for rid in last}, blocked


def _shortlist(candidates: List[dict], limit: int = 8) -> List[dict]:
    grouped: Dict[str, List[dict]] = {}
    for cand in candidates:
        grouped.setdefault(cand["recipe_id"], []).append(cand)
    picked: List[dict] = []
    for group in grouped.values():
        group.sort(key=lambda item: (item["local"], -item["protein_g"], item["recipe_id"]))
        picked.extend(group[:2])
    picked.sort(key=lambda item: (item["local"], -item["protein_g"], item["recipe_id"]))
    return picked[:limit]


def _legal_day(chosen: Sequence[dict], *, kcal_lo: float, kcal_hi: float, protein_floor: float) -> bool:
    if not chosen:
        return False
    kcal = sum(item["calories"] for item in chosen)
    protein = sum(item["protein_g"] for item in chosen)
    if kcal < kcal_lo - 0.05 or kcal > kcal_hi + 0.05:
        return False
    if protein + 0.05 < protein_floor:
        return False
    cuisines = [item["cuisine"].casefold() for item in chosen if item["cuisine"]]
    if len(chosen) > 1 and len(set(cuisines)) < 2:
        return False
    post_indexes = [i for i, item in enumerate(chosen) if item["role"] == "post-workout"]
    if post_indexes:
        post_c = chosen[post_indexes[0]]["carbs_g"]
        others = [item["carbs_g"] for i, item in enumerate(chosen) if i not in post_indexes]
        higher = [value for value in others if value > post_c + 0.05]
        if len(higher) != 1:
            return False
    return True


def _score_day(chosen: Sequence[dict], targets: dict, history_last: Dict[str, datetime], day) -> Tuple:
    protein = sum(item["protein_g"] for item in chosen)
    kcal = sum(item["calories"] for item in chosen)
    carbs = sum(item["carbs_g"] for item in chosen)
    fat = sum(item["fat_g"] for item in chosen)
    target_p = float(targets.get("protein_g") or 0)
    target_k = float(targets.get("calories") or 0)
    target_c = float(targets.get("carbs_g") or 0)
    target_f = float(targets.get("fat_g") or 0)
    variety = 0.0
    today = day.replace(hour=0, minute=0, second=0, microsecond=0)
    for item in chosen:
        last = history_last.get(item["recipe_id"])
        if last is None:
            continue
        delta = (today - last.replace(hour=0, minute=0, second=0, microsecond=0)).days
        if delta > 0:
            variety += 1.0 / delta
    score = (
        4.0 * max(0.0, target_p - protein) ** 2
        + (kcal - target_k) ** 2
        + 0.5 * (carbs - target_c) ** 2
        + 0.5 * (fat - target_f) ** 2
        + 40.0 * variety
    )
    return (score, -protein, tuple(item["recipe_id"] for item in chosen))


def _search(
    pools: Sequence[List[dict]],
    *,
    targets: dict,
    kcal_lo: float,
    kcal_hi: float,
    protein_floor: float,
    history_last: Dict[str, datetime],
    day: datetime,
    gram_caps: Dict[str, float],
) -> Optional[List[dict]]:
    best: Optional[Tuple] = None
    best_choice: Optional[List[dict]] = None

    def walk(index: int, chosen: List[dict], used: set, proteins: Dict[str, int], last_p: str, last_c: str, grams: Dict[str, float]) -> None:
        nonlocal best, best_choice
        if index == len(pools):
            if not _legal_day(chosen, kcal_lo=kcal_lo, kcal_hi=kcal_hi, protein_floor=protein_floor):
                return
            rank = _score_day(chosen, targets, history_last, day)
            if best is None or rank < best:
                best = rank
                best_choice = list(chosen)
            return
        for cand in pools[index]:
            rid = cand["recipe_id"]
            if rid in used:
                continue
            protein = cand["primary_protein"]
            if protein and protein == last_p:
                continue
            if protein and proteins.get(protein, 0) >= MAX_MEALS_PER_PROTEIN:
                continue
            cuisine = cand["cuisine"].casefold()
            if cuisine and cuisine == last_c:
                continue
            nxt = dict(grams)
            blocked = False
            for iid, need in cand["grams"].items():
                nxt[iid] = nxt.get(iid, 0.0) + need
                cap = gram_caps.get(iid)
                if cap is not None and nxt[iid] > cap + 0.2:
                    blocked = True
                    break
            if blocked:
                continue
            used.add(rid)
            if protein:
                proteins[protein] = proteins.get(protein, 0) + 1
            chosen.append(cand)
            walk(index + 1, chosen, used, proteins, protein or last_p, cuisine or last_c, nxt)
            chosen.pop()
            used.remove(rid)
            if protein:
                proteins[protein] -= 1

    walk(0, [], set(), {}, "", "", {})
    return best_choice


def _prepare_recipes(recipes: Sequence[dict], inventory: Optional[dict]) -> List[dict]:
    from .recipe_store import compute_recipe_macros, normalize_recipe

    out: List[dict] = []
    for raw in recipes or []:
        if not isinstance(raw, dict):
            continue
        try:
            if raw.get("macros_per_serving"):
                rec = dict(raw)
            else:
                rec = compute_recipe_macros(normalize_recipe(raw), inventory)
        except ValueError:
            continue
        if not rec.get("id"):
            continue
        out.append(rec)
    return out


def _usable_ids(inventory: Optional[dict], now: datetime, logs, confirms, stale_days: int) -> set:
    usable = set()
    for ing in _inventory_by_id(inventory).values():
        if pantry_is_fresh(ing, now=now, logs=logs, confirms=confirms, stale_days=stale_days):
            usable.add(str(ing.get("id") or ""))
    return usable


def _recipe_covered(recipe: dict, usable: set) -> bool:
    ids = []
    for line in recipe.get("ingredients") or []:
        if isinstance(line, dict):
            iid = str(line.get("ingredient_id") or line.get("id") or "").strip()
            if iid:
                ids.append(iid)
    return bool(ids) and all(iid in usable for iid in ids)


def _meal_from_choice(choice: dict, moment: datetime, tz_name: str, *, role_label: bool) -> dict:
    label = ROLE_LABELS.get(choice["role"], choice["role"]) if role_label else None
    totals = {
        "calories": choice["calories"],
        "protein_g": choice["protein_g"],
        "carbs_g": choice["carbs_g"],
        "fat_g": choice["fat_g"],
        "fiber_g": choice["fiber_g"],
    }
    meal = {
        "label": label or "",
        "slot_role": choice["role"],
        "eat_at": _iso(moment),
        "eat_at_label": _clock_label(moment),
        "timezone": tz_name,
        "recipe_id": choice["recipe_id"],
        "recipe_name": choice["recipe_name"],
        "servings": choice["servings"],
        "steps": list(choice["steps"]),
        "cuisine": choice["cuisine"],
        "primary_protein": choice["primary_protein"],
        "batch_friendly": choice["batch_friendly"],
        "prep_min": choice["prep_min"],
        "items": choice["items"],
        "totals": totals,
    }
    if choice["batch_friendly"]:
        meal["batch_prep"] = True
    return meal


def _apply_rest_labels(meals: List[dict]) -> None:
    for i, meal in enumerate(meals):
        meal["label"] = UPCOMING_MEAL_LABELS[min(i, len(UPCOMING_MEAL_LABELS) - 1)]


def _apply_role_labels(meals: List[dict]) -> None:
    for meal in meals:
        meal["label"] = ROLE_LABELS.get(meal.get("slot_role") or "", meal.get("label") or "Meal")


def rollup_ingredient_grams(meals: Sequence[dict]) -> List[dict]:
    """Sum scaled ingredient grams. One row per id, name kept from the first line."""
    totals: Dict[str, dict] = {}
    for meal in meals or []:
        if not isinstance(meal, dict):
            continue
        for item in meal.get("items") or []:
            if not isinstance(item, dict):
                continue
            iid = str(item.get("id") or item.get("ingredient_id") or "").strip()
            if not iid:
                continue
            try:
                grams = float(item.get("portion_g") or 0)
            except (TypeError, ValueError):
                grams = 0.0
            row = totals.setdefault(iid, {"ingredient_id": iid, "name": str(item.get("name") or iid), "grams": 0.0})
            row["grams"] = round(row["grams"] + grams, 1)
    return [totals[key] for key in sorted(totals)]


def price_grocery_lines(lines: Sequence[dict], offers_by_id: Optional[dict] = None) -> List[dict]:
    """Attach the cheapest Walmart/Costco offer when the caller passes candidates."""
    from .restock_cart import _pick_cheapest

    priced: List[dict] = []
    offers = offers_by_id or {}
    for line in lines or []:
        row = dict(line)
        candidates = offers.get(row.get("ingredient_id")) or offers.get(row.get("id")) or []
        picked = _pick_cheapest(list(candidates)) if candidates else None
        if picked:
            row["offer_id"] = picked.get("offer_id")
            row["unit_price"] = picked.get("price")
            row["offer_name"] = picked.get("name")
        priced.append(row)
    return priced


def cooked_batch(inventory: Optional[dict], recipe: dict, servings: float = 1.0) -> dict:
    """Mark a prepared dish in stock. Greedy planning skips recipe_batch rows."""
    from copy import deepcopy

    from .recipe_store import scale_servings

    inv = deepcopy(inventory) if isinstance(inventory, dict) else {"ingredients": []}
    ingredients = inv.setdefault("ingredients", [])
    rid = str((recipe or {}).get("id") or "").strip()
    if not rid:
        raise ValueError("recipe id required")
    try:
        n = float(servings or 1)
    except (TypeError, ValueError):
        n = 1.0
    if n <= 0:
        n = 1.0
    scaled = scale_servings(recipe, 1.0, inv)
    macros = scaled.get("macros") or {}
    pid = f"prepared-{rid}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = None
    for existing in ingredients:
        if isinstance(existing, dict) and str(existing.get("id") or "") == pid:
            row = existing
            break
    if row is None:
        row = {
            "id": pid,
            "name": str(recipe.get("name") or rid),
            "category": "prepared",
            "serving_label": "1 prepared serving",
            "serving_g": None,
            "calories": float(macros.get("calories") or 0),
            "protein_g": float(macros.get("protein_g") or 0),
            "carbs_g": float(macros.get("carbs_g") or 0),
            "fat_g": float(macros.get("fat_g") or 0),
            "fiber_g": float(macros.get("fiber_g") or 0),
            "recipe_id": rid,
            "recipe_batch": True,
            "prepared_servings": 0,
        }
        ingredients.append(row)
    row["recipe_batch"] = True
    row["in_stock"] = True
    row["updated_at"] = now
    try:
        have = float(row.get("prepared_servings") or 0)
    except (TypeError, ValueError):
        have = 0.0
    row["prepared_servings"] = round(have + n, 2)
    row["calories"] = float(macros.get("calories") or row.get("calories") or 0)
    row["protein_g"] = float(macros.get("protein_g") or row.get("protein_g") or 0)
    row["carbs_g"] = float(macros.get("carbs_g") or row.get("carbs_g") or 0)
    row["fat_g"] = float(macros.get("fat_g") or row.get("fat_g") or 0)
    return inv


def _locks_from_previous(
    roles: Sequence[str],
    times: Sequence[datetime],
    previous: Optional[Sequence[dict]],
    *,
    now: datetime,
    usable_ids: set,
    recipes_by_id: Dict[str, dict],
) -> Dict[int, str]:
    locks: Dict[int, str] = {}
    if not previous:
        return locks
    horizon = now - timedelta(minutes=20)
    for index, role in enumerate(roles):
        for meal in previous:
            if not isinstance(meal, dict):
                continue
            if str(meal.get("slot_role") or "") != role:
                continue
            rid = str(meal.get("recipe_id") or "").strip()
            if not rid or rid not in recipes_by_id:
                continue
            eat = _parse_meal_dt(meal.get("eat_at"))
            if eat is not None:
                eat = eat.astimezone(now.tzinfo)
                if eat < horizon:
                    continue
            if not _recipe_covered(recipes_by_id[rid], usable_ids):
                continue
            if _recipe_slots(recipes_by_id[rid]) and role not in _recipe_slots(recipes_by_id[rid]):
                continue
            locks[index] = rid
            break
    return locks


def generate_recipe_plan(
    inventory: dict,
    targets: dict,
    consumed: dict,
    recipes: Sequence[dict],
    *,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
    window_start: Any = None,
    window_end: Any = None,
    eat_slots: Optional[Sequence[Any]] = None,
    sleep_battery: Optional[dict] = None,
    food_logs_today: Optional[Sequence[dict]] = None,
    food_logs: Optional[Sequence[Any]] = None,
    sleep_intervals: Optional[Sequence[Any]] = None,
    sleep_blocks: Optional[Sequence[Any]] = None,
    recipe_history: Optional[Sequence[dict]] = None,
    workout_window: Any = None,
    previous_meals: Optional[Sequence[dict]] = None,
    locked_slots: Optional[dict] = None,
    banned_slots: Optional[dict] = None,
    training_day: Optional[bool] = None,
    strict_locks: bool = False,
    restock_confirms: Optional[Sequence[dict]] = None,
    stale_days: int = STALE_PANTRY_DAYS,
) -> Optional[dict]:
    """A day of library recipes, or None when none fit. Never a loose ingredient."""
    if not recipes:
        return None
    raw_targets = targets if isinstance(targets, dict) else {}
    tz, resolved_tz = _meal_tz(tz_name)
    if now is None:
        from .timeutil import local_now

        now = local_now(resolved_tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    workout = coerce_workout_window(workout_window, tz)
    training = bool(workout) if training_day is None else bool(training_day)
    normalized = _shift_targets(normalize_targets(raw_targets), training)
    sleep_windows = _sleep_windows_for_plan(sleep_blocks, now)
    start, end = _eating_window_bounds(
        now,
        tz,
        resolved_tz,
        window_start=window_start,
        window_end=window_end,
        sleep_battery=sleep_battery,
        sleep_windows=sleep_windows,
    )
    consumed_now, consumed_clock, nd_span = _consumed_for_planner(
        consumed,
        food_logs,
        now,
        start,
        end,
        sleep_battery=sleep_battery,
        sleep_intervals=sleep_intervals,
        tz_name=resolved_tz,
    )
    remaining_before = remaining_macros(normalized, consumed_now)
    if _kitchen_is_closed(
        now,
        start,
        end,
        remaining_before,
        normalized,
        sleep_battery=sleep_battery,
        sleep_windows=sleep_windows,
    ):
        return None
    prepared = _prepare_recipes(recipes, inventory)
    logs = list(food_logs or food_logs_today or [])
    usable = _usable_ids(inventory, now, logs, restock_confirms, stale_days)
    covered = [rec for rec in prepared if _recipe_covered(rec, usable)]
    if not covered:
        return None
    by_id = {str(rec.get("id") or ""): rec for rec in covered}
    cap = _remaining_meal_capacity(now, start, end)
    if cap <= 0:
        return None
    history_last, blocked = _blocked_ids(recipe_history or [], now, tz)
    # _blocked_ids returns (last, blocked) — fix unpack if I returned a tuple named wrong
    bans = {int(k): str(v) for k, v in (banned_slots or {}).items()}
    explicit_locks = {int(k): str(v) for k, v in (locked_slots or {}).items()}
    target_k = float(remaining_before.get("calories") or 0)
    target_p = float(normalized.get("protein_g") or 0)
    consumed_p = float((consumed_now or {}).get("protein_g") or 0)
    protein_floor = 0.95 * float(remaining_before.get("protein_g") or 0)
    if target_p >= 200:
        protein_floor = max(protein_floor, max(0.0, 200.0 - consumed_p))
    kcal_lo = 0.95 * target_k
    kcal_hi = 1.05 * target_k
    gram_caps = {}
    for ing in _inventory_by_id(inventory).values():
        cap_g = _grams_cap(ing)
        if cap_g is not None:
            gram_caps[str(ing.get("id") or "")] = cap_g
    role_labels = bool(workout) or training
    chosen_pack = None
    chosen_times: List[datetime] = []
    chosen_roles: List[str] = []
    for n in range(min(4, cap), 0, -1):
        times = resolve_recipe_times(
            n,
            now=now,
            start=start,
            end=end,
            tz=tz,
            eat_slots=eat_slots,
            consumed=consumed_now,
            targets=normalized,
            sleep_windows=sleep_windows,
            workout=workout,
        )
        horizon = now - timedelta(minutes=20)
        times = [item for item in times if item >= horizon]
        if len(times) < 1:
            continue
        roles = _assign_roles(times, workout)
        locks = dict(explicit_locks)
        if not locks and previous_meals:
            locks = _locks_from_previous(
                roles, times, previous_meals, now=now, usable_ids=usable, recipes_by_id=by_id
            )
        pools: List[List[dict]] = []
        empty_pool = False
        for index, role in enumerate(roles):
            pool = []
            allowed = locks.get(index)
            banned = bans.get(index)
            for rec in covered:
                rid = str(rec.get("id") or "")
                if not rid or rid in blocked:
                    continue
                if allowed and rid != allowed:
                    continue
                if banned and rid == banned:
                    continue
                earliest = index == 0
                for scale in _scales(rec):
                    cand = _candidate(
                        rec, scale, inventory, role=role, earliest=earliest
                    )
                    if cand is not None:
                        pool.append(cand)
            if allowed and not pool and not strict_locks:
                for rec in covered:
                    rid = str(rec.get("id") or "")
                    if not rid or rid in blocked or rid == banned:
                        continue
                    for scale in _scales(rec):
                        cand = _candidate(
                            rec, scale, inventory, role=role, earliest=(index == 0)
                        )
                        if cand is not None:
                            pool.append(cand)
            pool = _shortlist(pool, 8 if not allowed else 6)
            if not pool:
                empty_pool = True
                break
            pools.append(pool)
        if empty_pool or len(pools) != len(roles):
            continue
        found = _search(
            pools,
            targets=normalized,
            kcal_lo=kcal_lo,
            kcal_hi=kcal_hi,
            protein_floor=protein_floor,
            history_last=history_last,
            day=now,
            gram_caps=gram_caps,
        )
        if found:
            chosen_pack = found
            chosen_times = times[: len(found)]
            chosen_roles = roles[: len(found)]
            break
    if not chosen_pack:
        return None
    meals = []
    for choice, moment in zip(chosen_pack, chosen_times):
        meals.append(_meal_from_choice(choice, moment, resolved_tz, role_label=role_labels))
    if role_labels:
        _apply_role_labels(meals)
    else:
        _apply_rest_labels(meals)
    items: List[dict] = []
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0, "fiber_g": 0.0}
    for meal in meals:
        items.extend(meal["items"])
        for key in totals:
            totals[key] = round(totals[key] + float((meal.get("totals") or {}).get(key) or 0), 1)
    remaining_after = {
        "calories": round(max(0.0, float(normalized.get("calories") or 0) - float((consumed_now or {}).get("calories") or 0) - totals["calories"]), 1),
        "protein_g": round(max(0.0, float(normalized.get("protein_g") or 0) - float((consumed_now or {}).get("protein_g") or 0) - totals["protein_g"]), 1),
        "carbs_g": round(max(0.0, float(normalized.get("carbs_g") or 0) - float((consumed_now or {}).get("carbs_g") or 0) - totals["carbs_g"]), 1),
        "fat_g": round(max(0.0, float(normalized.get("fat_g") or 0) - float((consumed_now or {}).get("fat_g") or 0) - totals["fat_g"]), 1),
    }
    gaps = collect_recipe_gaps(
        prepared,
        inventory,
        now=now,
        food_logs=logs,
        restock_confirms=restock_confirms,
        stale_days=stale_days,
    )
    grocery = rollup_ingredient_grams(meals)
    nutrition_day = _nutrition_day_payload(
        now=now,
        start=start,
        end=end,
        kitchen_closed=False,
        consumed_clock=consumed_clock,
        sleep_battery=sleep_battery,
        nutrition_day=nd_span,
    )
    return {
        "mode": "recipe",
        "meals": meals,
        "items": items,
        "planned_totals": totals,
        "remaining_before_plan": remaining_before,
        "remaining_after_plan": remaining_after,
        "targets": normalized,
        "consumed": consumed_now,
        "food_logs_today": list(food_logs_today or []),
        "stocked_count": len([i for i in _inventory_by_id(inventory).values() if is_in_stock(i)]),
        "pantry_dark": False,
        "in_stock_only": True,
        "message": f"Recipe plan: {len(meals)} library meals.",
        "serving_grams_nudge": "",
        "notes": {
            "mode": "recipe",
            "empty_plan": False,
            "kitchen_closed": False,
            "slot_count": len(meals),
        },
        "honesty": [],
        "nutrition_day": nutrition_day,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "restock_suggestions": gaps["unlocks"],
        "pantry_prompts": gaps["prompts"],
        "grocery_week": grocery,
    }


def swap_recipe_slot(
    inventory: dict,
    targets: dict,
    consumed: dict,
    recipes: Sequence[dict],
    meals: Sequence[dict],
    slot_index: int,
    **kwargs,
) -> Optional[dict]:
    """Next-best recipe for one slot. Other slots stay on their recipe ids."""
    rows = [meal for meal in meals or [] if isinstance(meal, dict)]
    if slot_index < 0 or slot_index >= len(rows):
        return None
    current = str(rows[slot_index].get("recipe_id") or "")
    locked = {
        index: str(meal.get("recipe_id") or "")
        for index, meal in enumerate(rows)
        if index != slot_index and meal.get("recipe_id")
    }
    banned = {slot_index: current} if current else {}
    return generate_recipe_plan(
        inventory,
        targets,
        consumed,
        recipes,
        locked_slots=locked,
        banned_slots=banned,
        strict_locks=True,
        previous_meals=None,
        **kwargs,
    )
