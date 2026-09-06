"""Last-good SuperGrok workout plan persist for the Today slot.

Turso row is keyed by user_id + viewer civil day (local_today). Never invent
lifts. Empty lists are not a good plan. Process memory is a fallback so
generate → Today works on Mac preview when Turso is dark.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

ENSURE_WORKOUT_PLAN_SQL = """
CREATE TABLE IF NOT EXISTS workout_plan (
  user_id TEXT NOT NULL,
  local_today TEXT NOT NULL,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, local_today)
)
"""

_VOLATILE_KEYS = ("persist", "persist_key")
_MEMORY: Dict[Tuple[str, str], dict] = {}


def civil_day(value) -> str:
    return str(value or "")[:10]


def persist_key(user_id: str, local_today: str) -> dict:
    return {
        "user_id": (user_id or "").strip(),
        "local_today": civil_day(local_today),
    }


def flatten_plan_exercises(exercises) -> list:
    """Copy planned lifts. Promote nested SuperGrok prescription fields."""
    out = []
    for ex in exercises or []:
        if not isinstance(ex, dict):
            continue
        name = ex.get("name") or ex.get("exercise")
        if not str(name or "").strip():
            continue
        row = dict(ex)
        row["name"] = name
        rx = row.get("prescription") if isinstance(row.get("prescription"), dict) else {}
        for key in ("sets", "reps", "weight_lbs"):
            if row.get(key) is None and rx.get(key) is not None:
                row[key] = rx[key]
        out.append(row)
    return out


def is_good_workout_plan(plan: Optional[dict]) -> bool:
    """A last-good plan has at least one named lift. Rest/empty is not persisted as good."""
    if not isinstance(plan, dict):
        return False
    if bool(plan.get("is_rest_day")):
        return False
    return bool(flatten_plan_exercises(plan.get("exercises")))


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload_for_store(plan: dict) -> dict:
    out = {k: v for k, v in plan.items() if k not in _VOLATILE_KEYS}
    out["exercises"] = flatten_plan_exercises(out.get("exercises"))
    return out


def _mem_key(user_id: str, local_today: str) -> Tuple[str, str]:
    return ((user_id or "").strip(), civil_day(local_today))


def _turso_get_workout_plan(user_id: str, local_today: str) -> Optional[dict]:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        return None
    uid = (user_id or "").strip()
    day = civil_day(local_today)
    if not uid or not day:
        return None
    with connect() as conn:
        conn.execute(ENSURE_WORKOUT_PLAN_SQL)
        row = conn.execute(
            """
            SELECT payload FROM workout_plan
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


def _turso_put_workout_plan(user_id: str, local_today: str, plan: dict) -> None:
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
        conn.execute(ENSURE_WORKOUT_PLAN_SQL)
        conn.execute(
            """
            INSERT INTO workout_plan(user_id, local_today, payload, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, local_today) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (uid, day, blob, now),
        )


def save_last_good_workout_plan(user_id: str, local_today: str, plan: dict) -> dict:
    """Persist a good SuperGrok plan. Empty/rest is not a good plan."""
    key = persist_key(user_id, local_today)
    out: Dict[str, Any] = {"ok": False, "store": "turso", "key": key, "error": None}
    if not key["user_id"]:
        out["error"] = "user_id required"
        return out
    if not key["local_today"]:
        out["error"] = "local_today required"
        return out
    if not is_good_workout_plan(plan):
        out["error"] = "not a good workout_plan"
        return out
    stored = _payload_for_store(plan)
    _MEMORY[_mem_key(key["user_id"], key["local_today"])] = stored
    from .turso_http import turso_enabled

    if not turso_enabled():
        out["ok"] = True
        out["store"] = "memory"
        return out
    try:
        _turso_put_workout_plan(key["user_id"], key["local_today"], stored)
        existing = _turso_get_workout_plan(key["user_id"], key["local_today"])
    except Exception as exc:  # noqa: BLE001
        out["ok"] = True
        out["store"] = "memory"
        out["error"] = str(exc)[:160] or type(exc).__name__
        return out
    if not is_good_workout_plan(existing):
        out["ok"] = True
        out["store"] = "memory"
        out["error"] = "turso write not visible on readback"
        return out
    out["ok"] = True
    return out


def load_last_good_workout_plan(user_id: str, local_today: str) -> Optional[dict]:
    """Same user + same civil day only. Miss → None (honest)."""
    key = persist_key(user_id, local_today)
    if not key["user_id"] or not key["local_today"]:
        return None
    try:
        data = _turso_get_workout_plan(key["user_id"], key["local_today"])
    except Exception:  # noqa: BLE001
        data = None
    if is_good_workout_plan(data):
        return data
    mem = _MEMORY.get(_mem_key(key["user_id"], key["local_today"]))
    if is_good_workout_plan(mem):
        return mem
    return None


def clear_memory_workout_plans() -> None:
    _MEMORY.clear()
