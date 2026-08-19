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
) -> Dict[str, Any]:
    """Call grok_ask with resolved creds. No pantry. No canned fallback."""
    from .grok_ask import GrokAskError, chat_completions, resolve_xai_credentials

    creds = resolve_xai_credentials(user_id=user_id)
    if not creds.get("token") or creds.get("expired"):
        return {
            "ok": False,
            "error": creds.get("error") or HONEST_EMPTY_MSG,
            "meal": honest_empty_meal(),
            "workout": honest_empty_workout(),
        }

    rem = remaining_macros(targets or {}, consumed or {})
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
        "inventory": None,
        "notes": (
            "Inventory is unset (Pi pantry is dark). Do not invent staples "
            "the user owns. Meal ideas may use remaining macros + logged foods only."
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
        "- Workout may use recovery + recent sessions. ~4-8 hard sets/muscle/week.\n"
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
            "workout": honest_empty_workout(msg, source="error"),
        }

    parsed = _safe_json(str(result.get("answer") or ""))
    if not parsed:
        msg = "SuperGrok replied but the plan was not valid JSON. Try generate again."
        return {
            "ok": False,
            "error": msg,
            "meal": honest_empty_meal(msg, source="error"),
            "workout": honest_empty_workout(msg, source="error"),
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
    return {
        "ok": True,
        "meal": meal_out,
        "workout": workout_out,
        "model": result.get("model"),
        "auth_source": result.get("auth_source"),
    }
