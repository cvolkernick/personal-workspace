"""Last good meal_plan persist for the Today slot.

Turso row is keyed by signed-in user_id + viewer civil day (local_today).
Never invent pantry items or meals. Fail honest if the write cannot land.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

ENSURE_MEAL_PLAN_SQL = """
CREATE TABLE IF NOT EXISTS nutrition_meal_plan (
  user_id TEXT NOT NULL,
  local_today TEXT NOT NULL,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, local_today)
)
"""

MSG_PANTRY_UNAVAILABLE = "Pantry unavailable"
MSG_NO_IN_STOCK = "No in-stock items"

_VOLATILE_KEYS = ("persist", "persist_key")


def civil_day(value) -> str:
    return str(value or "")[:10]


def pantry_is_dark(inventory: Optional[dict]) -> bool:
    """No ingredient list at all (load miss / empty pantry). Not the same as all OOS."""
    if not isinstance(inventory, dict):
        return True
    ings = inventory.get("ingredients")
    return not isinstance(ings, list) or len(ings) == 0


def is_good_meal_plan(plan: Optional[dict]) -> bool:
    """A last-good plan has at least one real in-stock line. Empty is not persisted."""
    if not isinstance(plan, dict):
        return False
    items = plan.get("items") or []
    if any(isinstance(it, dict) and str(it.get("name") or "").strip() for it in items):
        return True
    for block in plan.get("meals") or []:
        if not isinstance(block, dict):
            continue
        for it in block.get("items") or []:
            if isinstance(it, dict) and str(it.get("name") or "").strip():
                return True
    return False


def remaining_macros_full(plan: Optional[dict]) -> bool:
    """Same threshold the planner / Today UI use for 'day is essentially full'."""
    if not isinstance(plan, dict):
        return False
    rem = plan.get("remaining_before_plan") or {}
    try:
        cals = float(rem.get("calories"))
        protein = float(rem.get("protein_g"))
    except (TypeError, ValueError):
        return False
    return cals < 150 and protein < 20


def persist_key(user_id: str, local_today: str) -> dict:
    return {
        "user_id": (user_id or "").strip(),
        "local_today": civil_day(local_today),
    }


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload_for_store(plan: dict) -> dict:
    return {k: v for k, v in plan.items() if k not in _VOLATILE_KEYS}


def _turso_get_meal_plan(user_id: str, local_today: str) -> Optional[dict]:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        return None
    uid = (user_id or "").strip()
    day = civil_day(local_today)
    if not uid or not day:
        return None
    with connect() as conn:
        conn.execute(ENSURE_MEAL_PLAN_SQL)
        row = conn.execute(
            """
            SELECT payload FROM nutrition_meal_plan
            WHERE user_id = ? AND local_today = ?
            """,
            (uid, day),
        ).fetchone()
    if not row:
        return None
    payload = row["payload"] if isinstance(row, dict) else row[0]
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _turso_put_meal_plan(user_id: str, local_today: str, plan: dict) -> None:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        raise RuntimeError("turso env missing")
    uid = (user_id or "").strip()
    day = civil_day(local_today)
    if not uid:
        raise ValueError("user_id required")
    if not day:
        raise ValueError("local_today required")
    blob = json.dumps(_payload_for_store(plan), separators=(",", ":"))
    now = _iso_now()
    with connect() as conn:
        conn.execute(ENSURE_MEAL_PLAN_SQL)
        conn.execute(
            """
            INSERT INTO nutrition_meal_plan(user_id, local_today, payload, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, local_today) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (uid, day, blob, now),
        )


def save_last_good_meal_plan(user_id: str, local_today: str, plan: dict) -> dict:
    """Persist a good plan. Never raise out of GET — return honest persist status."""
    key = persist_key(user_id, local_today)
    out = {"ok": False, "store": "turso", "key": key, "error": None}
    if not key["user_id"]:
        out["error"] = "user_id required"
        return out
    if not key["local_today"]:
        out["error"] = "local_today required"
        return out
    if not is_good_meal_plan(plan):
        out["error"] = "not a good meal_plan"
        return out
    from .turso_http import turso_enabled

    if not turso_enabled():
        out["error"] = "turso env missing"
        return out
    try:
        _turso_put_meal_plan(key["user_id"], key["local_today"], plan)
        existing = _turso_get_meal_plan(key["user_id"], key["local_today"])
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:160] or type(exc).__name__
        return out
    if not is_good_meal_plan(existing):
        out["error"] = "turso write not visible on readback"
        return out
    out["ok"] = True
    return out


def load_last_good_meal_plan(user_id: str, local_today: str) -> Optional[dict]:
    """Same user + same civil day only. Miss / Turso down → None (honest)."""
    key = persist_key(user_id, local_today)
    if not key["user_id"] or not key["local_today"]:
        return None
    try:
        data = _turso_get_meal_plan(key["user_id"], key["local_today"])
    except Exception:
        return None
    if not is_good_meal_plan(data):
        return None
    return data


def apply_honest_empty_copy(plan: dict, inventory: Optional[dict]) -> dict:
    """Lock Today empty strings. Never invent food."""
    out = dict(plan or {})
    dark = pantry_is_dark(inventory) or bool(out.get("pantry_dark"))
    out["pantry_dark"] = dark
    items = out.get("items") or []
    meals = out.get("meals") or []
    empty = not items and not any(
        isinstance(m, dict) and (m.get("items") or []) for m in meals
    )
    if not empty:
        return out
    if dark:
        out["message"] = MSG_PANTRY_UNAVAILABLE
        out["stocked_count"] = int(out.get("stocked_count") or 0)
        return out
    if int(out.get("stocked_count") or 0) == 0:
        out["message"] = MSG_NO_IN_STOCK
        return out
    return out


def resolve_dashboard_meal_plan(
    user_id: str,
    local_today: str,
    generated: dict,
    inventory: Optional[dict] = None,
) -> dict:
    """GET/POST meal slot: persist last good; restore same user+day when honest.

    Pantry dark / stocked_count=0 / remaining-macros-full stay empty (no invented meals).
    """
    inventory = inventory if isinstance(inventory, dict) else {"ingredients": []}
    plan = apply_honest_empty_copy(generated or {}, inventory)
    key = persist_key(user_id, local_today)
    plan["persist_key"] = key

    if is_good_meal_plan(plan):
        persist = save_last_good_meal_plan(user_id, local_today, plan)
        plan["persist"] = persist
        plan["source"] = "generate"
        return plan

    plan["source"] = "generate"
    if pantry_is_dark(inventory) or plan.get("pantry_dark"):
        plan["message"] = MSG_PANTRY_UNAVAILABLE
        return plan
    if int(plan.get("stocked_count") or 0) == 0:
        plan["message"] = MSG_NO_IN_STOCK
        return plan
    if remaining_macros_full(plan):
        return plan

    last = load_last_good_meal_plan(user_id, local_today)
    if not last:
        return plan
    from .grok_planner import clamp_meal_to_stock

    filtered = clamp_meal_to_stock(last, inventory)
    filtered = apply_honest_empty_copy(filtered, inventory)
    if not is_good_meal_plan(filtered):
        return plan
    filtered["source"] = "last_good"
    filtered["persist"] = {"ok": True, "store": "turso", "key": key}
    filtered["persist_key"] = key
    filtered["pantry_dark"] = False
    return filtered
