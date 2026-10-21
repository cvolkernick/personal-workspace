"""Last-good SuperGrok workout plan persist for the Today slot.

Turso row is keyed by user_id + viewer civil day (local_today). Never invent
lifts. Empty lists are not a good plan. Process memory is a fallback so
generate → Today works on Mac preview when Turso is dark.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional, Tuple

_LOG = logging.getLogger("fitdash.plan_write_guard")

# Smoke / parity / dev guard (#1304). When any of these env vars is truthy,
# workout_plan saves are refused for every day except the current training
# day (``training_day.TRAINING_DAY_CUTOFF``, 04:00 local). Refusals are logged.
PLAN_WRITE_GUARD_ENVS = ("FITDASH_PLAN_WRITE_GUARD", "FITDASH_SMOKE", "FITDASH_PARITY")
_TRUTHY = ("1", "true", "yes", "on")

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
        for key in (
            "sets",
            "reps",
            "weight_lbs",
            "load",
            "target_reps",
            "progression_reason",
            "load_source",
            "load_reason",
            "assistance_lbs",
            "effective_load_lbs",
            "effective_load_source",
            "weighin_days_away",
        ):
            if row.get(key) is None and rx.get(key) is not None:
                row[key] = rx[key]
        if row.get("rep_range") is None:
            label = rx.get("rep_range_label")
            if label:
                row["rep_range"] = label
            elif rx.get("rep_range") is not None:
                row["rep_range"] = rx.get("rep_range")
        band = row.get("rep_range")
        if isinstance(band, list) and len(band) >= 2:
            row["rep_range"] = f"{int(band[0])}-{int(band[1])}"
        if row.get("load") is None and row.get("weight_lbs") is not None:
            row["load"] = row.get("weight_lbs")
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
    refusal = plan_write_refusal(local_today)
    if refusal:  # defence in depth for callers that skip the public save
        _LOG.warning("%s user=%s (turso put)", refusal, (user_id or "").strip())
        raise PermissionError(refusal)
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


def plan_write_guard_active() -> bool:
    return any(
        (os.environ.get(name) or "").strip().lower() in _TRUTHY
        for name in PLAN_WRITE_GUARD_ENVS
    )


def current_training_day(now: Optional[datetime] = None) -> str:
    """Today's training day by the 04:00 cutoff (no wake signal used)."""
    from .training_day import training_day_for
    from .timeutil import local_now

    return training_day_for(local_now(now=now)) or ""


def plan_write_refusal(local_today: str, *, now: Optional[datetime] = None) -> Optional[str]:
    """None when the save may proceed. A reason string when the guard refuses."""
    if not plan_write_guard_active():
        return None
    day = civil_day(local_today)
    current = current_training_day(now)
    if day and day == current:
        return None
    return f"plan_write_guard: refused workout_plan write for {day or '?'} (current training day {current})"


@contextmanager
def plan_write_guard() -> Iterator[None]:
    """Turn the guard on for a block (parity / smoke harnesses)."""
    prev = os.environ.get("FITDASH_PLAN_WRITE_GUARD")
    os.environ["FITDASH_PLAN_WRITE_GUARD"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("FITDASH_PLAN_WRITE_GUARD", None)
        else:
            os.environ["FITDASH_PLAN_WRITE_GUARD"] = prev


def save_last_good_workout_plan(user_id: str, local_today: str, plan: dict) -> dict:
    """Persist a good SuperGrok plan. Empty/rest is not a good plan."""
    key = persist_key(user_id, local_today)
    out: Dict[str, Any] = {"ok": False, "store": "turso", "key": key, "error": None}
    refusal = plan_write_refusal(key["local_today"])
    if refusal:
        _LOG.warning("%s user=%s", refusal, key["user_id"])
        out["store"] = "guard_refused"
        out["error"] = refusal
        return out
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


def _plan_letter(plan: dict) -> str:
    return str(plan.get("session_type") or "").strip().lower()


def load_most_recent_good_workout_plan(
    user_id: str,
    on_or_before: str,
    *,
    letter: Optional[str] = None,
) -> Optional[dict]:
    """Newest good plan on or before this civil day.

    When ``letter`` is set, only a plan already on that rotation letter matches.
    Same-day ``load_last_good_workout_plan`` is unchanged.
    """
    uid = (user_id or "").strip()
    day = civil_day(on_or_before)
    want = str(letter or "").strip().lower()
    if not uid or not day:
        return None
    best_day = ""
    best: Optional[dict] = None

    def consider(plan_day: str, plan: Optional[dict]) -> None:
        nonlocal best_day, best
        stamp = civil_day(plan_day)
        if not stamp or stamp > day or not is_good_workout_plan(plan):
            return
        if want and _plan_letter(plan or {}) not in ("", want):
            return
        if stamp >= best_day:
            best_day = stamp
            best = plan

    for (mem_uid, mem_day), plan in list(_MEMORY.items()):
        if mem_uid == uid:
            consider(mem_day, plan)
    try:
        from .turso_http import connect, turso_enabled

        if turso_enabled():
            with connect() as conn:
                conn.execute(ENSURE_WORKOUT_PLAN_SQL)
                rows = conn.execute(
                    """
                    SELECT local_today, payload FROM workout_plan
                    WHERE user_id = ? AND local_today <= ?
                    ORDER BY local_today DESC
                    LIMIT 30
                    """,
                    (uid, day),
                ).fetchall()
            for row in rows or []:
                if isinstance(row, dict):
                    plan_day = row.get("local_today")
                    payload = row.get("payload")
                else:
                    plan_day, payload = row[0], row[1]
                try:
                    data = json.loads(payload) if isinstance(payload, str) else None
                except (TypeError, json.JSONDecodeError):
                    data = None
                consider(str(plan_day or ""), data if isinstance(data, dict) else None)
    except Exception:
        pass
    if not isinstance(best, dict):
        return None
    out = dict(best)
    out["exercises"] = list(out.get("exercises") or [])
    return out
