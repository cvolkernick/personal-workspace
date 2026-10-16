"""Grok-backed meal/workout plans. Honest-empty when SuperGrok is not connected.

Never invent a canned meal/workout. Never assume a pantry / inventory.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from .grok_oauth import CONNECT_ERROR
from .nutrition_planner import remaining_macros

HONEST_EMPTY_MSG = CONNECT_ERROR
READY_MSG = "SuperGrok connected. Generate today's meal/workout plan."


def clamp_meal_to_stock(meal: dict, inventory: dict) -> dict:
    """Drop invented / out-of-stock items. Meal plan is in-stock pantry only."""
    from .nutrition_planner import scale_plan_item_to_inventory, stocked_ingredients

    meal = dict(meal or {})
    stocked = stocked_ingredients(inventory or {})
    ids = {str(i.get("id") or "").strip().lower() for i in stocked}
    names = {str(i.get("name") or "").strip().lower() for i in stocked}

    def ok(item) -> bool:
        if not isinstance(item, dict):
            return False
        iid = str(item.get("id") or "").strip().lower()
        name = str(item.get("name") or "").strip().lower()
        if iid and iid in ids:
            return True
        if name and name in names:
            return True
        return False

    def _scale(item: dict) -> dict:
        iid = str(item.get("id") or "").strip().lower()
        name = str(item.get("name") or "").strip().lower()
        ing = None
        if iid:
            for s in stocked:
                if str(s.get("id") or "").strip().lower() == iid:
                    ing = s
                    break
        if ing is None and name:
            for s in stocked:
                if str(s.get("name") or "").strip().lower() == name:
                    ing = s
                    break
        if ing is None:
            return item
        return scale_plan_item_to_inventory(item, ing)

    items = [_scale(i) for i in (meal.get("items") or []) if ok(i)]
    meals = []
    for block in meal.get("meals") or []:
        if not isinstance(block, dict):
            continue
        keep = [_scale(i) for i in (block.get("items") or []) if ok(i)]
        meals.append({**block, "items": keep})
    meal["items"] = items
    meal["meals"] = meals
    meal["in_stock_only"] = True
    meal["stocked_count"] = len(stocked)
    meal["inventory"] = [{"id": i.get("id"), "name": i.get("name")} for i in stocked]
    meal["empty"] = not items and not any((m.get("items") or []) for m in meals)
    return meal


def honest_empty_meal(message: str = HONEST_EMPTY_MSG, *, source: str = "none") -> dict:
    return {
        "meals": [],
        "items": [],
        "planned_totals": {},
        "message": message,
        "empty": True,
        "source": source,
        "in_stock_only": False,
        "stocked_count": 0,
        "inventory": None,
    }


def honest_empty_workout(message: str = HONEST_EMPTY_MSG, *, source: str = "none") -> dict:
    return {
        "session_type": None,
        "is_rest_day": False,
        "exercises": [],
        "message": message,
        "empty": True,
        "source": source,
        "next_session_type": None,
        "training_continuity": None,
    }


def dashboard_plan_slots(
    user_id: str,
    *,
    sessions=None,
    goals=None,
    recovery=None,
    as_of: Optional[str] = None,
    train_parent_completed: bool = False,
) -> Tuple[dict, dict]:
    """GET /api/dashboard Today slots: never call Grok. Never canned.

    Hybrid fill: stamp session_type + training_continuity from Turso+goals
    even when exercises stay []. SuperGrok still owns the exercise list
    on Generate.
    """
    from .grok_ask import resolve_xai_credentials
    from .workout_store import load_workspace_goals, stamp_today_session

    creds = resolve_xai_credentials(user_id=user_id)
    if creds.get("token") and not creds.get("expired"):
        src = str(creds.get("source") or "supergrok_session")
        meal = honest_empty_meal(READY_MSG, source=src)
        workout = honest_empty_workout(READY_MSG, source=src)
    else:
        meal = honest_empty_meal()
        workout = honest_empty_workout()

    if goals is None:
        goals, _ = load_workspace_goals()
    from .daily_plan_tasks import cached_training_objective

    objective = cached_training_objective(as_of)
    if sessions is None:
        sessions = []
        try:
            from .turso_repo import list_sessions_detailed

            sessions, _notes = list_sessions_detailed(user_id)
        except Exception:  # noqa: BLE001
            sessions = []
    workout = stamp_today_session(
        workout,
        sessions,
        goals,
        recovery,
        as_of=as_of,
        fill_rest=True,
        train_parent_completed=bool(train_parent_completed),
        training_objective=objective,
    )
    from .ai_planner import active_block

    block = active_block(user_id)
    if block and not workout.get("is_rest_day"):
        from .agent_plan import fallback_workout

        letter = str(workout.get("session_type") or "").strip().lower() or None
        catalog: dict = {}
        equipment = None
        try:
            from .workout_store import load_workspace_catalog

            loaded, _src = load_workspace_catalog()
            if isinstance(loaded, dict):
                catalog = loaded
        except Exception:
            catalog = {}
        try:
            from .equipment_store import load_workspace_equipment

            loaded_eq, _src = load_workspace_equipment()
            if isinstance(loaded_eq, dict):
                equipment = loaded_eq
        except Exception:
            equipment = None
        fallback = fallback_workout(
            {
                "day": str(as_of or "")[:10],
                "sessions": sessions or [],
                "goals": goals or {},
                "recovery": recovery or {},
                "catalog": catalog,
                "equipment": equipment,
            },
            letter,
            str(block.get("message") or ""),
            user_id,
            str(as_of or "")[:10],
        )
        if fallback:
            fallback["next_session_type"] = workout.get("next_session_type")
            fallback["training_continuity"] = workout.get("training_continuity")
            workout = fallback
    return meal, workout


def _safe_json(text: str) -> Optional[dict]:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def generate_grok_plans(
    user_id: str,
    *,
    targets: Optional[dict] = None,
    consumed: Optional[dict] = None,
    food_logs_today: Optional[list] = None,
    recovery: Optional[dict] = None,
    sessions_brief: Optional[list] = None,
    sessions: Optional[list] = None,
    goals: Optional[dict] = None,
    catalog: Optional[dict] = None,
    next_session_type: Optional[str] = None,
    inventory: Optional[dict] = None,
    equipment: Optional[dict] = None,
    as_of: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Call grok_ask with resolved creds. In-stock pantry only. No canned fallback."""
    from .grok_ask import GrokAskError, chat_completions, resolve_xai_credentials
    from .workout_store import load_workspace_goals, rest_gate, stamp_today_session

    if not isinstance(goals, dict) or not (goals.get("rotation") or []):
        file_goals, _ = load_workspace_goals()
        goals = {**file_goals, **(goals if isinstance(goals, dict) else {})}

    from .daily_plan_tasks import cached_training_objective

    objective = cached_training_objective(as_of)
    creds = resolve_xai_credentials(user_id=user_id)
    if not creds.get("token") or creds.get("expired"):
        empty = honest_empty_workout()
        return {
            "ok": False,
            "error": creds.get("error") or HONEST_EMPTY_MSG,
            "meal": honest_empty_meal(),
            "workout": stamp_today_session(
                empty,
                sessions_brief or [],
                goals or {},
                recovery or {},
                fill_rest=True,
                next_st_override=next_session_type,
                as_of=as_of,
                training_objective=objective,
            ),
        }


    rem = remaining_macros(targets or {}, consumed or {})
    gate = rest_gate(goals or {}, recovery or {})
    # Keep next PPL even when force_rest — it is planner input, not a hide flag.
    catalog = catalog if isinstance(catalog, dict) else {}
    from .workout_planner import filter_catalog_by_equipment

    feasible = (
        filter_catalog_by_equipment(catalog, equipment)
        if isinstance(equipment, dict)
        else catalog
    )
    exercises = feasible.get("exercises") if isinstance(feasible.get("exercises"), list) else []
    catalog_brief = {
        "count": len(exercises),
        "names": [
            e.get("name")
            for e in exercises[:40]
            if isinstance(e, dict) and e.get("available", True) and e.get("name")
        ],
    }
    user_block = {
        "remaining_macros": rem,
        "targets": {
            k: (targets or {}).get(k)
            for k in ("calories", "protein_g", "carbs_g", "fat_g")
        },
        "today_consumed": consumed or {},
        "food_logs_today": (food_logs_today or [])[:20],
        "recovery": recovery or {},
        "recent_sessions": (sessions_brief or [])[:8],
        "goals": goals or {},
        "catalog": catalog_brief,
        "inventory": None,
        "equipment": None,
        "next_session_type": next_session_type,
        "rest_if_recovery_below": (goals or {}).get("rest_if_recovery_below") or 40,
        "rest_gate": gate,
        "volume_caps": {
            "default_hard_sets": (goals or {}).get("default_hard_sets"),
            "session_working_set_cap": (goals or {}).get("session_working_set_cap"),
            "sets_per_muscle_week": "4-8",
            "ignore_catalog_default_sets": True,
        },
        "notes": (
            "PANTRY IS EMPTY OR MISSING. Return meal items=[] meals=[]. "
            "Do not invent food, meals, or ingredients. "
            "Workout must use goals (PPL split / DeanT volume) + catalog names + "
            "recent_sessions + recovery. rest_if_recovery_below is INPUT: you MAY "
            "generate a rest day as today's plan (that still fills the slot). "
            "Do not omit next_session_type. No canned plan. No fake inventory. "
            "Do NOT use catalog default_sets=3. Volume from goals: "
            "default_hard_sets, DeanT 4-8, session_working_set_cap. "
            "Workout names are the programmed library, already checked against "
            "accessible equipment. Do not invent lifts or enable available=false "
            "rows. New gear does not auto-expand the library. "
            "Do not prescribe loads above max_weight_lbs."
        ),
    }
    if isinstance(equipment, dict):
        from .equipment_store import owned_equipment_items

        user_block["equipment"] = [
            {
                "id": i.get("id"),
                "name": i.get("name"),
                "tag": i.get("tag"),
                "max_weight_lbs": i.get("max_weight_lbs"),
                "source": i.get("source") or "owned",
            }
            for i in owned_equipment_items(equipment)
        ]
    from .meal_plan_store import pantry_is_dark

    inv_for_meal = inventory if isinstance(inventory, dict) else None
    if pantry_is_dark(inv_for_meal):
        user_block["inventory"] = []
        user_block["notes"] = (
            "PANTRY IS EMPTY OR MISSING. Return meal items=[] meals=[]. "
            "Do not invent food, meals, or ingredients. "
            "Workout must use goals (PPL split / DeanT volume) + catalog names + "
            "recent_sessions + recovery. rest_if_recovery_below is INPUT: you MAY "
            "generate a rest day as today's plan (that still fills the slot). "
            "Do not omit next_session_type. No canned plan. No fake inventory. "
            "Do NOT use catalog default_sets=3. Volume from goals: "
            "default_hard_sets, DeanT 4-8, session_working_set_cap. "
            "Workout names are the programmed library, already checked against "
            "accessible equipment. Do not invent lifts or enable available=false "
            "rows. New gear does not auto-expand the library. "
            "Do not prescribe loads above max_weight_lbs."
        )
    elif inventory is not None:
        from .nutrition_planner import stocked_ingredients

        stocked = stocked_ingredients(inventory)
        user_block["inventory"] = [
            {
                "id": i.get("id"),
                "name": i.get("name"),
                "category": i.get("category"),
                "serving_g": i.get("serving_g"),
                "serving_label": i.get("serving_label"),
                "calories": i.get("calories"),
                "protein_g": i.get("protein_g"),
                "carbs_g": i.get("carbs_g"),
                "fat_g": i.get("fat_g"),
                "stock": i.get("stock") or "in",
                "in_stock": True,
            }
            for i in stocked
        ]
        user_block["notes"] = (
            "Meal plan is IN-STOCK ONLY. Use only inventory items listed "
            "(stock in or low; in_stock=true). Do not invent pantry items "
            "or out-of-stock staples. Low items are still cookable. "
            "When serving_g is known, pick continuous portion_g (25g, 250g, 500g) "
            "— do not lock to whole inventory servings. Macros = "
            "(portion_g / serving_g) × per-serving macros. Round grams ~5g, "
            "min ~25g. If serving_g is missing, do not invent grams; use "
            "serving_label only. "
            "Workout must use goals (PPL split / DeanT volume) + catalog names + "
            "recent_sessions + recovery. rest_if_recovery_below is INPUT: you MAY "
            "generate a rest day as today's plan (that still fills the slot). "
            "Do not omit next_session_type. No canned plan. No fake inventory. "
            "Do NOT use catalog default_sets=3. Volume from goals: "
            "default_hard_sets, DeanT 4-8, session_working_set_cap. "
            "Workout names are the programmed library, already checked against "
            "accessible equipment. Do not invent lifts or enable available=false "
            "rows. New gear does not auto-expand the library. "
            "Do not prescribe loads above max_weight_lbs."
        )
    system = (
        "You generate today's FitDash meal sketch and workout plan.\n"
        "Return ONLY JSON with keys meal and workout.\n"
        "meal: {message, items:[{name, portion_g, servings, serving_label, "
        "calories, protein_g, carbs_g, fat_g}], meals:[]}\n"
        "workout: {session_type, is_rest_day, message, "
        "exercises:[{name, prescription:{sets, reps, weight_lbs}, primary_muscles, rationale}]}\n"
        "Rules:\n"
        "- Meal items must come from provided in-stock inventory only.\n"
        "- Do not invent pantry items or use out-of-stock ingredients.\n"
        "- When inventory serving_g is known, choose continuous portion_g "
        "(partial grams OK: 25g, 250g, 500g). Do not lock to whole servings.\n"
        "- Macros MUST be (portion_g / serving_g) × per-serving macros. "
        "Round grams to ~5g, minimum ~25g.\n"
        "- If serving_g is missing, do not invent grams; use serving_label only.\n"
        "- If inventory is null or empty, return meal items=[] meals=[]. "
        "Do not invent food.\n"
        "- Never use logged foods or remaining macros to invent a pantry.\n"
        "- Workout uses goals.split / rotation, catalog names in the programmed "
        "library (available=true and feasible with current access), recovery, "
        "and recent lifts.\n"
        "- Every catalog.equipment tag must be on the equipment inventory "
        "(home owned or gym access). weight_lbs must be "
        "<= that implement's max_weight_lbs (DB max/hand, plate stack).\n"
        "- Never invent a lift that is not in the library. New gear does not "
        "auto-add catalog rows.\n"
        "- If no accessible gear can load this PPL slot from the library, "
        "return exercises=[] and say why. Still set session_type.\n"
        "- Volume caps come from goals (default_hard_sets, DeanT 4-8, "
        "session_working_set_cap). NEVER use catalog default_sets=3.\n"
        "- rest_if_recovery_below + recovery.score + sparse are INPUT to you. "
        "You MAY return is_rest_day as today's plan (a rest day is a plan, "
        "not an omitted slot). Still set session_type (rest or next PPL). "
        "Never omit the workout object. Keep next_session_type for rotation. "
        "Sparse sleep must not rest.\n"
        "- No canned plan. No fake inventory.\n"
        "- If you cannot generate, return empty arrays and say why in message.\n"
        "- Never include secrets or tokens."
    )
    def _fail(msg: str, *, kind: str) -> Dict[str, Any]:
        meal_source = "xai_quota" if kind == "xai_quota" else "error"
        return {
            "ok": False,
            "error": msg,
            "error_kind": kind,
            "meal": honest_empty_meal(msg, source=meal_source),
            "workout": stamp_today_session(
                honest_empty_workout(msg, source="error"),
                sessions_brief or [],
                goals or {},
                recovery or {},
                fill_rest=True,
                next_st_override=next_session_type,
                as_of=as_of,
                training_objective=objective,
            ),
        }

    from .ai_planner import active_block, format_quota_message, note_quota, parse_reset

    if not force:
        block = active_block(user_id)
        if block:
            return _fail(str(block.get("message") or ""), kind="xai_quota")
    try:
        result = chat_completions(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": "CONTEXT JSON:\n"
                    + json.dumps(user_block, indent=2)
                    + "\n\nGenerate today's meal and workout JSON.",
                },
            ],
            user_id=user_id,
            max_tokens=1600,
            temperature=0.3,
            force=force,
        )
    except GrokAskError as exc:
        kind = str(getattr(exc, "kind", "") or "")
        msg = str(exc)
        if kind == "xai_quota" or "xai_quota" in msg:
            source = str(getattr(exc, "source", "") or creds.get("source") or "none")
            if not msg.startswith("AI plan unavailable"):
                msg = format_quota_message(
                    source=source,
                    code=str(getattr(exc, "xai_code", "") or ""),
                    reset_label=str(getattr(exc, "reset_label", "") or ""),
                )
            if active_block(user_id) is None:
                _label, reset_at = parse_reset(str(getattr(exc, "reset_label", "") or ""))
                note_quota(
                    user_id,
                    source=source if source in ("supergrok_session", "xai_api_key", "none") else "none",
                    code=str(getattr(exc, "xai_code", "") or ""),
                    message=msg,
                    reset_at=reset_at,
                )
            return _fail(msg, kind="xai_quota")
        if getattr(exc, "status", 0) == 403 and kind != "xai_quota":
            msg = (
                "xAI entitlement gating (SuperGrok or X Premium+) — not a FitDash bug. "
                + msg
            )
        return _fail(msg, kind=kind or "error")

    parsed = _safe_json(str(result.get("answer") or ""))
    if not parsed:
        msg = "SuperGrok replied but the plan was not valid JSON. Try generate again."
        return {
            "ok": False,
            "error": msg,
            "meal": honest_empty_meal(msg, source="error"),
            "workout": stamp_today_session(
                honest_empty_workout(msg, source="error"),
                sessions_brief or [],
                goals or {},
                recovery or {},
                fill_rest=True,
                next_st_override=next_session_type,
                as_of=as_of,
                training_objective=objective,
            ),
        }

    meal = parsed.get("meal") if isinstance(parsed.get("meal"), dict) else {}
    workout = parsed.get("workout") if isinstance(parsed.get("workout"), dict) else {}
    meal_items = [i for i in (meal.get("items") or []) if isinstance(i, dict)]
    meal_meals = [m for m in (meal.get("meals") or []) if isinstance(m, dict)]
    exercises = [e for e in (workout.get("exercises") or []) if isinstance(e, dict)]
    meal_out = {
        "meals": meal_meals,
        "items": meal_items,
        "planned_totals": meal.get("planned_totals") or {},
        "message": str(meal.get("message") or "Generated by SuperGrok."),
        "empty": not meal_items and not meal_meals,
        "source": "grok",
        "in_stock_only": inventory is not None,
        "stocked_count": 0,
        "inventory": None,
        "remaining_before_plan": rem,
    }
    from .meal_plan_store import (
        MSG_NO_IN_STOCK,
        MSG_PANTRY_UNAVAILABLE,
        pantry_is_dark,
    )

    if pantry_is_dark(inventory if isinstance(inventory, dict) else None):
        meal_out = honest_empty_meal(MSG_PANTRY_UNAVAILABLE, source="pantry")
        meal_out["pantry_dark"] = True
        meal_out["in_stock_only"] = True
        meal_out["stocked_count"] = 0
        meal_out["remaining_before_plan"] = rem
    elif inventory is not None:
        meal_out = clamp_meal_to_stock(meal_out, inventory)
        if meal_out.get("empty") or (
            not meal_out.get("items")
            and not any((m.get("items") or []) for m in meal_out.get("meals") or [])
        ):
            if (meal_out.get("stocked_count") or 0) == 0:
                meal_out["items"] = []
                meal_out["meals"] = []
                meal_out["message"] = MSG_NO_IN_STOCK
                meal_out["pantry_dark"] = False
                meal_out["empty"] = True
    hard = max(1, int((goals or {}).get("default_hard_sets") or 2))
    capped = []
    for ex in exercises:
        item = dict(ex)
        if item.get("default_sets") == 3:
            item["default_sets"] = hard
            item["volume_from"] = "goals"
        capped.append(item)
    workout_out = {
        "session_type": workout.get("session_type") or next_session_type,
        "is_rest_day": bool(workout.get("is_rest_day")),
        "exercises": capped,
        "message": str(workout.get("message") or "Generated by SuperGrok."),
        "empty": not capped and not workout.get("is_rest_day"),
        "source": "grok",
    }
    from .workout_planner import clamp_llm_plan_loads, clamp_workout_to_equipment

    if isinstance(equipment, dict):
        workout_out = clamp_workout_to_equipment(workout_out, catalog, equipment)
    history = sessions if sessions is not None else (sessions_brief or [])
    workout_out = clamp_llm_plan_loads(
        workout_out,
        history,
        catalog,
        goals=goals,
        recovery=recovery,
        equipment=equipment if isinstance(equipment, dict) else None,
        as_of=as_of,
    )
    workout_out = stamp_today_session(
        workout_out,
        sessions_brief or [],
        goals or {},
        recovery or {},
        fill_rest=not capped,
        next_st_override=next_session_type,
        as_of=as_of,
        training_objective=objective,
    )
    return {
        "ok": True,
        "meal": meal_out,
        "workout": workout_out,
        "model": result.get("model"),
        "auth_source": result.get("auth_source"),
    }
