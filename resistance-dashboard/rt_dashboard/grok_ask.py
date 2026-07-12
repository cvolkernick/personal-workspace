"""Ask Grok about dashboard fitness data using SuperGrok / Grok Build session."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

XAI_API_BASE = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("XAI_MODEL", "grok-4.20-non-reasoning")
AUTH_PATH = Path.home() / ".grok" / "auth.json"
# Lighter default pack for Ask (override with GROK_ASK_MAX_CONTEXT_CHARS).
MAX_CONTEXT_CHARS = int(os.environ.get("GROK_ASK_MAX_CONTEXT_CHARS", "28000"))
REQUEST_TIMEOUT = int(os.environ.get("GROK_ASK_TIMEOUT_SEC", "90"))
ASK_SESSION_LIMIT = int(os.environ.get("GROK_ASK_SESSION_LIMIT", "20"))
ASK_HEALTH_DAYS = int(os.environ.get("GROK_ASK_HEALTH_DAYS", "14"))

SYSTEM_PROMPT = """You are a fitness coach assistant for the user's personal resistance-training dashboard.

You only answer using the FITNESS DATA JSON provided in the user message (plus general exercise/nutrition knowledge that does not invent facts about THIS user).

Rules:
- Ground answers in the provided data: workouts, recovery, weight, sleep, nutrition intake, hydration, inventory, targets, meal plan.
- If something is missing from the data, say so clearly. Do not invent sessions, weights, macros, or dates.
- Prefer concise, practical answers. Use bullet lists when helpful.
- When discussing progress, cite specific numbers and dates from the data.
- Do not claim access to Google Health settings/goals unless they appear in the data.
- Do not discuss secrets, tokens, or how to hack systems.
"""


class GrokAskError(Exception):
    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _parse_expires_at(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        # 2026-07-12T10:49:50.259841Z
        s = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _read_grok_auth_entry() -> Optional[dict]:
    if not AUTH_PATH.is_file():
        return None
    try:
        data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    # Prefer non-expired entry with a key
    best = None
    best_exp = None
    now = datetime.now(timezone.utc)
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        token = entry.get("key") or entry.get("access_token")
        if not token:
            continue
        exp = _parse_expires_at(entry.get("expires_at"))
        if exp and exp <= now:
            # keep as fallback but prefer non-expired
            if best is None:
                best = entry
            continue
        if best is None or (exp and (best_exp is None or exp > best_exp)):
            best = entry
            best_exp = exp
    return best


def resolve_xai_credentials() -> Dict[str, Any]:
    """
    Resolve auth for xAI chat completions.

    Priority:
      1. XAI_API_KEY env (console.x.ai API key)
      2. SuperGrok / Grok Build session in ~/.grok/auth.json
    """
    api_key = (os.environ.get("XAI_API_KEY") or "").strip()
    if api_key:
        return {
            "token": api_key,
            "source": "xai_api_key",
            "email": None,
            "expires_at": None,
            "expired": False,
        }

    entry = _read_grok_auth_entry()
    if not entry:
        return {
            "token": None,
            "source": "none",
            "email": None,
            "expires_at": None,
            "expired": False,
            "error": (
                "No SuperGrok session found. Sign in with `grok login` (uses your "
                "SuperGrok subscription), or set XAI_API_KEY from console.x.ai."
            ),
        }

    token = (entry.get("key") or entry.get("access_token") or "").strip()
    exp = _parse_expires_at(entry.get("expires_at"))
    now = datetime.now(timezone.utc)
    expired = bool(exp and exp <= now)
    return {
        "token": token if token else None,
        "source": "supergrok_session",
        "email": entry.get("email"),
        "expires_at": entry.get("expires_at"),
        "expired": expired,
        "error": (
            "SuperGrok session expired. Run `grok login` in a terminal, then try again."
            if expired
            else (None if token else "Empty session token in ~/.grok/auth.json")
        ),
    }


def auth_status() -> Dict[str, Any]:
    creds = resolve_xai_credentials()
    return {
        "ok": bool(creds.get("token")) and not creds.get("expired"),
        "source": creds.get("source"),
        "email": creds.get("email"),
        "expires_at": creds.get("expires_at"),
        "expired": bool(creds.get("expired")),
        "model": DEFAULT_MODEL,
        "error": creds.get("error"),
        "auth_path": str(AUTH_PATH),
    }


def _trim_sessions(sessions: Any, limit: int = 40) -> List[dict]:
    if not isinstance(sessions, list):
        return []
    out: List[dict] = []
    for s in sessions[:limit]:
        if not isinstance(s, dict):
            continue
        exercises = []
        for ex in (s.get("exercises") or [])[:30]:
            if not isinstance(ex, dict):
                continue
            exercises.append(
                {
                    "name": ex.get("name"),
                    "weight_lbs": ex.get("weight_lbs"),
                    "sets": ex.get("sets"),
                    "reps": ex.get("reps"),
                    "volume": ex.get("volume"),
                }
            )
        out.append(
            {
                "date": s.get("date"),
                "session_type": s.get("session_type") or s.get("type"),
                "notes": s.get("notes") or "",
                "exercises": exercises,
                "total_volume": s.get("total_volume") or s.get("volume"),
            }
        )
    return out


def _series_tail(points: Any, n: int = 90) -> List[Any]:
    if not isinstance(points, list):
        return []
    return points[-n:]


def build_fitness_context(dashboard: dict, *, compact: bool = True) -> dict:
    """Snapshot of dashboard data for the model.

    compact=True (default for Ask): last ~20 sessions, ~14d health, stocked
    inventory only, weekly volume truncated — keeps latency/tokens down.
    """
    health = dashboard.get("health") or {}
    nut = dashboard.get("nutrition_store") or {}
    inventory = nut.get("inventory") or {}
    ingredients = inventory.get("ingredients") if isinstance(inventory, dict) else []
    stocked = [
        {
            "id": i.get("id"),
            "name": i.get("name"),
            "category": i.get("category"),
            "serving_label": i.get("serving_label"),
            "calories": i.get("calories"),
            "protein_g": i.get("protein_g"),
            "carbs_g": i.get("carbs_g"),
            "fat_g": i.get("fat_g"),
            "in_stock": i.get("in_stock", True),
        }
        for i in (ingredients or [])
        if isinstance(i, dict) and i.get("in_stock", True)
    ]

    meal_plan = nut.get("meal_plan") or {}
    if isinstance(meal_plan, dict):
        meal_plan = {
            "message": meal_plan.get("message"),
            "items": (meal_plan.get("items") or meal_plan.get("meals") or [])[:12],
            "totals": meal_plan.get("totals") or meal_plan.get("planned_totals"),
            "remaining_before_plan": meal_plan.get("remaining_before_plan"),
            "targets": meal_plan.get("targets"),
        }

    sess_limit = ASK_SESSION_LIMIT if compact else 40
    h_days = ASK_HEALTH_DAYS if compact else 90
    meta_keys = (
        "generated_at",
        "source",
        "local_today",
        "timezone",
        "load_ms",
        "health_weight_points",
        "health_sleep_points",
        "health_nutrition_days",
        "cache_ttl_sec",
    )
    full_meta = dashboard.get("meta") or {}
    meta = {k: full_meta.get(k) for k in meta_keys if full_meta.get(k) is not None}

    # Strength: only top few exercises, last points
    trends_in = dashboard.get("strength_trends") or {}
    trends_out = {}
    if isinstance(trends_in, dict):
        for name in list(trends_in.keys())[:8]:
            series = trends_in.get(name) or []
            if isinstance(series, list):
                trends_out[name] = series[-8:]

    weekly = dashboard.get("weekly_volume") or dashboard.get("volume_by_week") or []
    if isinstance(weekly, list):
        weekly = weekly[-12:]

    wo = dashboard.get("workout_store") or {}
    plan = wo.get("plan") or {}
    if isinstance(plan, dict):
        plan = {
            "date": plan.get("date"),
            "session_type": plan.get("session_type"),
            "is_rest_day": plan.get("is_rest_day"),
            "message": plan.get("message"),
            "exercises": [
                {
                    "name": e.get("name"),
                    "prescription": e.get("prescription"),
                    "primary_muscles": e.get("primary_muscles"),
                }
                for e in (plan.get("exercises") or [])[:8]
                if isinstance(e, dict)
            ],
        }

    context = {
        "generated_at": full_meta.get("generated_at"),
        "local_today": full_meta.get("local_today"),
        "timezone": full_meta.get("timezone"),
        "meta": meta,
        "recovery": dashboard.get("recovery"),
        "weekly_volume": weekly,
        "strength_trends": trends_out,
        "sessions": _trim_sessions(dashboard.get("sessions"), limit=sess_limit),
        "health": {
            "weight": _series_tail(health.get("weight"), h_days),
            "sleep": _series_tail(health.get("sleep"), h_days),
            "nutrition": _series_tail(health.get("nutrition"), h_days),
            "hydration": _series_tail(health.get("hydration"), h_days),
            "calories_burned": _series_tail(health.get("calories_burned"), min(h_days, 14)),
            "notes": health.get("error"),
        },
        "nutrition_store": {
            "targets": nut.get("targets"),
            "today_consumed": nut.get("today_consumed"),
            "inventory": stocked[:40],
            "meal_plan": meal_plan,
        },
        "workout_store": {
            "goals": wo.get("goals"),
            "plan": plan,
            "catalog_count": len(((wo.get("catalog") or {}).get("exercises") or [])),
        },
    }
    return context


def _shrink_context(context: dict, max_chars: int = MAX_CONTEXT_CHARS) -> Tuple[dict, bool]:
    """If JSON is too large, drop older series / sessions until it fits."""
    ctx = json.loads(json.dumps(context))  # deep copy via JSON
    trimmed = False
    for limit_sessions, limit_health, limit_ex in (
        (20, 14, 12),
        (15, 14, 10),
        (10, 10, 8),
        (5, 7, 6),
        (3, 5, 4),
    ):
        sessions = (ctx.get("sessions") or [])[:limit_sessions]
        for s in sessions:
            if isinstance(s, dict) and isinstance(s.get("exercises"), list):
                s["exercises"] = s["exercises"][:limit_ex]
        ctx["sessions"] = sessions
        h = ctx.get("health") or {}
        for key in ("weight", "sleep", "nutrition", "hydration", "calories_burned"):
            if isinstance(h.get(key), list):
                h[key] = h[key][-limit_health:]
        ctx["health"] = h
        raw = json.dumps(ctx, separators=(",", ":"))
        if len(raw) <= max_chars:
            return ctx, trimmed
        trimmed = True
    # last resort: drop bulky optional sections
    ctx.pop("strength_trends", None)
    ctx.pop("weekly_volume", None)
    ctx.pop("summary", None)
    inv = (ctx.get("nutrition_store") or {}).get("inventory")
    if isinstance(inv, list) and len(inv) > 15:
        ctx.setdefault("nutrition_store", {})["inventory"] = inv[:15]
    raw = json.dumps(ctx, separators=(",", ":"))
    if len(raw) <= max_chars:
        return ctx, True
    # hard cut sessions to one-line summaries
    slim_sessions = []
    for s in (ctx.get("sessions") or [])[:8]:
        if not isinstance(s, dict):
            continue
        names = [
            (ex.get("name") or "?")
            for ex in (s.get("exercises") or [])[:6]
            if isinstance(ex, dict)
        ]
        slim_sessions.append(
            {
                "date": s.get("date"),
                "session_type": s.get("session_type"),
                "exercises": names,
                "total_volume": s.get("total_volume"),
            }
        )
    ctx["sessions"] = slim_sessions
    return ctx, True


def chat_completions(
    messages: List[dict],
    *,
    model: Optional[str] = None,
    max_tokens: int = 1200,
    temperature: float = 0.3,
) -> Dict[str, Any]:
    creds = resolve_xai_credentials()
    token = creds.get("token")
    if not token:
        raise GrokAskError(creds.get("error") or "No xAI credentials", status=401)
    if creds.get("expired"):
        raise GrokAskError(creds.get("error") or "Session expired", status=401)

    body = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        f"{XAI_API_BASE}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "resistance-dashboard/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:2000]
        raise GrokAskError(
            f"xAI API error HTTP {e.code}: {err_body[:400]}",
            status=e.code,
            body=err_body,
        ) from e
    except urllib.error.URLError as e:
        raise GrokAskError(f"xAI network error: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise GrokAskError(f"Invalid JSON from xAI: {e}") from e

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise GrokAskError(f"Unexpected xAI response shape: {str(data)[:400]}") from e

    return {
        "answer": content,
        "model": data.get("model") or (model or DEFAULT_MODEL),
        "usage": data.get("usage"),
        "auth_source": creds.get("source"),
    }


def ask_about_dashboard(
    question: str,
    dashboard: dict,
    *,
    history: Optional[List[dict]] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    q = (question or "").strip()
    if not q:
        raise GrokAskError("question is required", status=400)
    if len(q) > 4000:
        raise GrokAskError("question too long (max 4000 chars)", status=400)

    context, trimmed = _shrink_context(build_fitness_context(dashboard, compact=True))
    context_json = json.dumps(context, indent=2)

    user_block = (
        "FITNESS DATA JSON (authoritative for this user):\n"
        f"```json\n{context_json}\n```\n\n"
        f"QUESTION:\n{q}"
    )

    messages: List[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Optional short chat history (user/assistant only)
    if history:
        for turn in history[-8:]:
            if not isinstance(turn, dict):
                continue
            role = turn.get("role")
            content = (turn.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content[:4000]})
    messages.append({"role": "user", "content": user_block})

    result = chat_completions(messages, model=model)
    result["context_trimmed"] = trimmed
    result["context_chars"] = len(context_json)
    result["session_count"] = len(context.get("sessions") or [])
    return result
