"""Live catalog-universe rows: Turso custom movements merged before overlay.

catalog.json is the shipped universe. Overlay can only enable/disable ids that
already exist. Gym-floor Add writes a custom row here so Log can list it on
the same load — no git catalog.json deploy. Adding never dumps onto Today.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

CUSTOM_ROW_DEFAULT = "default"

ENSURE_CUSTOM_SQL = """
CREATE TABLE IF NOT EXISTS custom_movements (
  user_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""


def _uid(user_id: str = "") -> str:
    return (user_id or "").strip() or CUSTOM_ROW_DEFAULT


def _as_custom(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {"exercises": []}
    items = raw.get("exercises")
    if not isinstance(items, list):
        items = []
    from .workout_planner import normalize_exercise

    kept: List[dict] = []
    seen = set()
    for row in items:
        if not isinstance(row, dict):
            continue
        try:
            ex = normalize_exercise(row)
        except ValueError:
            continue
        eid = str(ex.get("id") or "").strip()
        if not eid or eid in seen:
            continue
        seen.add(eid)
        ex["universe"] = "custom"
        kept.append(ex)
    out = {k: v for k, v in raw.items() if k != "exercises"}
    out["exercises"] = kept
    return out


def merge_custom_universe(catalog: Optional[dict], custom: Optional[dict]) -> dict:
    """Insert custom rows into the universe. Custom id/name wins. Overlay is next."""
    from .workout_planner import _norm_name

    out = deepcopy(catalog) if isinstance(catalog, dict) else {"exercises": []}
    items = [e for e in (out.get("exercises") or []) if isinstance(e, dict)]
    by_id: Dict[str, int] = {}
    by_name: Dict[str, int] = {}
    for i, ex in enumerate(items):
        eid = str(ex.get("id") or "").strip()
        if eid:
            by_id[eid] = i
        nm = _norm_name(str(ex.get("name") or ""))
        if nm:
            by_name[nm] = i
    for ex in _as_custom(custom).get("exercises") or []:
        idx = by_id.get(str(ex.get("id") or ""))
        if idx is None:
            idx = by_name.get(_norm_name(str(ex.get("name") or "")))
        if idx is None:
            items.append(ex)
            by_id[str(ex["id"])] = len(items) - 1
            by_name[_norm_name(ex["name"])] = len(items) - 1
            continue
        items[idx] = ex
        by_id[str(ex["id"])] = idx
        by_name[_norm_name(ex["name"])] = idx
    out["exercises"] = items
    return out


def upsert_custom_exercise(custom: Optional[dict], raw: dict) -> dict:
    from .workout_planner import add_or_update_exercise, normalize_exercise

    ex = normalize_exercise(raw)
    ex["available"] = True
    ex["universe"] = "custom"
    cat = add_or_update_exercise(
        custom if isinstance(custom, dict) else {"exercises": []}, ex
    )
    return _as_custom(cat)


def missing_inventory_tags(ex: dict, equipment: Optional[dict]) -> List[str]:
    from .equipment_store import owned_equipment_tags
    from .workout_planner import movement_any_tags, movement_required_tags

    owned = owned_equipment_tags(equipment)
    missing = [t for t in movement_required_tags(ex) if t not in owned]
    any_tags = movement_any_tags(ex)
    if any_tags and not any(t in owned for t in any_tags):
        missing.extend(t for t in any_tags if t not in owned)
    seen = set()
    out: List[str] = []
    for tag in missing:
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def require_equipment_access(ex: dict, equipment: Optional[dict]) -> None:
    missing = missing_inventory_tags(ex, equipment)
    if missing:
        raise ValueError(
            "required equipment tag missing from inventory: " + ", ".join(missing)
        )


def _turso_get(user_id: str) -> Optional[dict]:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        return None
    uid = _uid(user_id)
    with connect() as conn:
        conn.execute(ENSURE_CUSTOM_SQL)
        row = conn.execute(
            "SELECT payload FROM custom_movements WHERE user_id = ?",
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


def _turso_put(user_id: str, custom: dict) -> None:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        raise RuntimeError("turso env missing")
    uid = _uid(user_id)
    blob = json.dumps(_as_custom(custom), separators=(",", ":"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect() as conn:
        conn.execute(ENSURE_CUSTOM_SQL)
        conn.execute(
            """
            INSERT INTO custom_movements(user_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (uid, blob, now),
        )


def load_custom_movements(user_id: str = "") -> Tuple[dict, str]:
    from .turso_http import turso_enabled

    empty = {"exercises": []}
    if not turso_enabled():
        return empty, "default"
    try:
        existing = _turso_get(user_id)
    except Exception:
        return empty, "default"
    if not isinstance(existing, dict):
        return empty, "default"
    return _as_custom(existing), "turso"


def save_custom_movements(custom: dict, user_id: str = "") -> dict:
    from .turso_http import turso_enabled

    if not turso_enabled():
        raise RuntimeError("turso env missing")
    payload = _as_custom(custom)
    _turso_put(user_id, payload)
    existing = _turso_get(user_id)
    if not isinstance(existing, dict):
        raise RuntimeError("turso write not visible on readback")
    return _as_custom(existing)


def load_universe_catalog(user_id: str = "") -> Tuple[dict, dict]:
    """File catalog + custom rows. Caller applies overlay after this."""
    from .workout_store import load_workspace_catalog

    catalog, catalog_src = load_workspace_catalog()
    custom, custom_src = load_custom_movements(user_id)
    return merge_custom_universe(catalog, custom), {
        "catalog": catalog_src,
        "custom": custom_src,
    }


def add_library_movement(user_id: str, raw: dict) -> dict:
    """Write a new universe row, enable it, return stamped catalog. No Today write."""
    from .equipment_store import load_preview_equipment
    from .library_store import (
        apply_library_overlay,
        load_library_overlay,
        save_library_overlay,
        set_library_available,
    )
    from .workout_planner import normalize_exercise
    from .workout_store import apply_goals_volume_caps, load_workspace_goals

    ex = normalize_exercise(raw)
    ex["available"] = True
    equipment, equipment_src = load_preview_equipment(user_id)
    require_equipment_access(ex, equipment)
    current, _custom_src = load_custom_movements(user_id)
    updated = upsert_custom_exercise(current, ex)
    saved_custom = save_custom_movements(updated, user_id)
    overlay, _overlay_src = load_library_overlay(user_id)
    saved_overlay = save_library_overlay(
        set_library_available(overlay, ex["id"], True), user_id
    )
    universe, srcs = load_universe_catalog(user_id)
    stamped = apply_library_overlay(universe, saved_overlay)
    goals, goals_src = load_workspace_goals()
    stamped = apply_goals_volume_caps(stamped, goals)
    return {
        "catalog": stamped,
        "custom": saved_custom,
        "library": saved_overlay,
        "exercise": next(
            (
                row
                for row in (saved_custom.get("exercises") or [])
                if isinstance(row, dict) and row.get("id") == ex["id"]
            ),
            ex,
        ),
        "write": {
            "ok": True,
            "source": "turso",
            "verified_on_readback": True,
            "universe": "custom",
        },
        "sources": {
            "catalog": srcs.get("catalog"),
            "custom": srcs.get("custom") or "turso",
            "library": "turso",
            "equipment": equipment_src,
            "goals": goals_src,
        },
    }
