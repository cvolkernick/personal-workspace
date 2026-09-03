"""Per-user exercise-library overlay: which catalog rows are programmed.

Catalog.json is the movement universe. available=true in the file is the
default library. Turso overlay records explicit enable/disable after coach
suggests and the athlete applies. Adding gear never writes this overlay.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

LIBRARY_ROW_DEFAULT = "default"

ENSURE_LIBRARY_SQL = """
CREATE TABLE IF NOT EXISTS exercise_library (
  user_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""


def _uid(user_id: str = "") -> str:
    return (user_id or "").strip() or LIBRARY_ROW_DEFAULT


def _as_overlay(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {"enabled": [], "disabled": []}
    enabled = [
        str(x).strip()
        for x in (raw.get("enabled") or [])
        if str(x).strip()
    ]
    disabled = [
        str(x).strip()
        for x in (raw.get("disabled") or [])
        if str(x).strip()
    ]
    # Last write wins if an id is in both.
    enabled_set = []
    seen = set()
    disabled_set = {x for x in disabled}
    for eid in enabled:
        if eid in seen or eid in disabled_set:
            continue
        seen.add(eid)
        enabled_set.append(eid)
    disabled_out = []
    dseen = set()
    for eid in disabled:
        if eid in dseen:
            continue
        dseen.add(eid)
        disabled_out.append(eid)
    return {"enabled": enabled_set, "disabled": disabled_out}


def apply_library_overlay(catalog: Optional[dict], overlay: Optional[dict]) -> dict:
    """Stamp catalog.available from overlay; file flags remain the default."""
    out = deepcopy(catalog) if isinstance(catalog, dict) else {"exercises": []}
    ov = _as_overlay(overlay)
    enabled = set(ov["enabled"])
    disabled = set(ov["disabled"])
    if not enabled and not disabled:
        return out
    for ex in out.get("exercises") or []:
        if not isinstance(ex, dict):
            continue
        eid = str(ex.get("id") or "").strip()
        if not eid:
            continue
        if eid in enabled:
            ex["available"] = True
        elif eid in disabled:
            ex["available"] = False
    return out


def set_library_available(overlay: Optional[dict], exercise_id: str, available: bool) -> dict:
    eid = str(exercise_id or "").strip()
    if not eid:
        raise ValueError("exercise id required")
    ov = _as_overlay(overlay)
    enabled = [x for x in ov["enabled"] if x != eid]
    disabled = [x for x in ov["disabled"] if x != eid]
    if available:
        enabled.append(eid)
    else:
        disabled.append(eid)
    ov["enabled"] = enabled
    ov["disabled"] = disabled
    ov["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return ov


def _turso_get(user_id: str) -> Optional[dict]:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        return None
    uid = _uid(user_id)
    with connect() as conn:
        conn.execute(ENSURE_LIBRARY_SQL)
        row = conn.execute(
            "SELECT payload FROM exercise_library WHERE user_id = ?",
            (uid,),
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


def _turso_put(user_id: str, overlay: dict) -> None:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        raise RuntimeError("turso env missing")
    uid = _uid(user_id)
    ov = _as_overlay(overlay)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    blob = json.dumps(ov, separators=(",", ":"))
    with connect() as conn:
        conn.execute(ENSURE_LIBRARY_SQL)
        conn.execute(
            """
            INSERT INTO exercise_library(user_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (uid, blob, now),
        )


def load_library_overlay(user_id: str = "") -> Tuple[dict, str]:
    from .turso_http import turso_enabled

    empty = {"enabled": [], "disabled": []}
    if not turso_enabled():
        return empty, "default"
    try:
        existing = _turso_get(user_id)
    except Exception:
        return empty, "default"
    if not isinstance(existing, dict):
        return empty, "default"
    return _as_overlay(existing), "turso"


def save_library_overlay(overlay: dict, user_id: str = "") -> dict:
    from .turso_http import turso_enabled

    if not turso_enabled():
        raise RuntimeError("turso env missing")
    ov = _as_overlay(overlay)
    _turso_put(user_id, ov)
    existing = _turso_get(user_id)
    if not isinstance(existing, dict):
        raise RuntimeError("turso write not visible on readback")
    return _as_overlay(existing)


def overlay_ids(overlay: Optional[dict]) -> Tuple[List[str], List[str]]:
    ov = _as_overlay(overlay)
    return list(ov["enabled"]), list(ov["disabled"])
