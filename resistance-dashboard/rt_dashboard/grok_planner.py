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
    }


def dashboard_plan_slots(user_id: str) -> Tuple[dict, dict]:
    """GET /api/dashboard: never call Grok. Never canned."""
    from .grok_ask import resolve_xai_credentials

    creds = resolve_xai_credentials(user_id=user_id)
    if creds.get("token") and not creds.get("expired"):
        src = str(creds.get("source") or "supergrok_session")
        return (
            honest_empty_meal(READY_MSG, source=src),
            honest_empty_workout(READY_MSG, source=src),
        )
    return honest_empty_meal(), honest_empty_workout()


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
    goals: Optional[dict] = None,
    catalog: Optional[dict] = None,
    next_session_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Call grok_ask with resolved creds. No pantry. No canned fallback."""
    from .grok_ask import GrokAskError, chat_completions, resolve_xai_credentials
    from .workout_store import apply_rest_gate, rest_gate

    creds = resolve_xai_credentials(user_id=user_id)
    if not creds.get("token") or creds.get("expired"):
        return {
            "ok": False,
            "error": creds.get("error") or HONEST_EMPTY_MSG,
            "meal": honest_empty_meal(),
            "workout": apply_rest_gate(honest_empty_workout(), goals or {}, recovery or {}),
        }


    rem = remaining_macros(targets or {}, consumed or {})
    gate = rest_gate(goals or {}, recovery or {})
    # Keep next PPL even when force_rest — it is planner input, not a hide flag.
    catalog = catalog if isinstance(catalog, dict) else {}
    exercises = catalog.get("exercises") if isinstance(catalog.get("exercises"), list) else []
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
            "Inventory is unset (Pi pantry is dark). Do not invent staples "
            "the user owns. Meal ideas may use remaining macros + logged foods only. "
            "Workout must use goals (PPL split / DeanT volume) + catalog names + "
            "recent_sessions + recovery. rest_if_recovery_below is INPUT: you MAY "
            "generate a rest day as today's plan (that still fills the slot). "
            "Do not omit next_session_type. No canned plan. No fake inventory. "
            "Do NOT use catalog default_sets=3. Volume from goals: "
            "default_hard_sets, DeanT 4-8, session_working_set_cap."
        ),
    }
    system = (
        "You generate today's FitDash meal sketch and workout plan.\n"
        "Return ONLY JSON with keys meal and workout.\n"
        "meal: {message, items:[{name, calories, protein_g, carbs_g, fat_g}], meals:[]}\n"
        "workout: {session_type, is_rest_day, message, "
        "exercises:[{name, prescription:{sets, reps, weight_lbs}, primary_muscles, rationale}]}\n"
        "Rules:\n"
        "- Do not assume a pantry or inventory. Do not invent owned staples.\n"
        "- Meal may draft from remaining macros and today's logged foods only.\n"
        "- Workout uses goals.split / rotation, catalog names, recovery, and recent lifts.\n"
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
        )
    except GrokAskError as exc:
        msg = str(exc)
        if getattr(exc, "status", 0) == 403:
            msg = (
                "xAI entitlement gating (SuperGrok or X Premium+) — not a FitDash bug. "
                + msg
            )
        return {
            "ok": False,
            "error": msg,
            "meal": honest_empty_meal(msg, source="error"),
            "workout": apply_rest_gate(honest_empty_workout(msg, source="error"), goals or {}, recovery or {}),
        }

    parsed = _safe_json(str(result.get("answer") or ""))
    if not parsed:
        msg = "SuperGrok replied but the plan was not valid JSON. Try generate again."
        return {
            "ok": False,
            "error": msg,
            "meal": honest_empty_meal(msg, source="error"),
            "workout": apply_rest_gate(honest_empty_workout(msg, source="error"), goals or {}, recovery or {}),
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
        "in_stock_only": False,
        "stocked_count": 0,
        "inventory": None,
        "remaining_before_plan": rem,
    }
    workout_out = {
        "session_type": workout.get("session_type"),
        "is_rest_day": bool(workout.get("is_rest_day")),
        "exercises": exercises,
        "message": str(workout.get("message") or "Generated by SuperGrok."),
        "empty": not exercises and not workout.get("is_rest_day"),
        "source": "grok",
    }
    workout_out = apply_rest_gate(workout_out, goals or {}, recovery or {})
    return {
        "ok": True,
        "meal": meal_out,
        "workout": workout_out,
        "model": result.get("model"),
        "auth_source": result.get("auth_source"),
    }
