"""Per-user equipment/weights inventory: bundled seed + Turso persist.

Catalog stays the movement library. This file is owned gear + available loads,
not a second exercise dump. After seed, Turso is source of truth.
Never invent cable/smith/assisted-pullup.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

EQUIPMENT_PATH = "fitness/exercises/equipment.json"
EQUIPMENT_ROW_DEFAULT = "default"

ENSURE_EQUIPMENT_SQL = """
CREATE TABLE IF NOT EXISTS equipment_inventory (
  user_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""

KNOWN_EQUIPMENT_TAGS = (
    "dumbbells",
    "barbell",
    "bench",
    "incline_bench",
    "cable",
    "lat_pulldown",
    "assisted_pullup",
    "machine",
    "smith_machine",
    "leg_press",
)

_TAG_ALIASES = {
    "db": "dumbbells",
    "dbs": "dumbbells",
    "dumbbell": "dumbbells",
    "bb": "barbell",
    "plates": "barbell",
    "plate_stack": "barbell",
    "inclinebench": "incline_bench",
    "incline": "incline_bench",
    "smith": "smith_machine",
    "assisted_pull_up": "assisted_pullup",
    "assisted_pullups": "assisted_pullup",
    "lat_pulldown_machine": "lat_pulldown",
    "cable_stack": "cable",
}


def _workspace_file_candidates(rel: str) -> list:
    here = Path(__file__).resolve()
    rel_path = Path(rel)
    ordered = []
    if len(here.parents) >= 3:
        ordered.append(here.parents[2] / rel_path)
    if len(here.parents) >= 2:
        ordered.append(here.parents[1] / rel_path)
    cwd = Path.cwd().resolve()
    ordered.append(cwd / rel_path)
    for parent in cwd.parents:
        ordered.append(parent / rel_path)
    seen = set()
    out = []
    for cand in ordered:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


def _equipment_file_candidates() -> list:
    return _workspace_file_candidates(EQUIPMENT_PATH)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s or "gear"


def normalize_equipment_tag(tag: str) -> str:
    key = re.sub(r"[\s\-]+", "_", str(tag or "").strip().lower())
    return _TAG_ALIASES.get(key, key)


def normalize_equipment_item(raw: dict) -> dict:
    name = str((raw or {}).get("name") or "").strip()
    if not name:
        raise ValueError("equipment name required")
    tag = normalize_equipment_tag(
        raw.get("tag") or raw.get("id") or name
    )
    if not tag:
        raise ValueError("equipment tag required")
    eid = str(raw.get("id") or tag).strip()
    max_w = raw.get("max_weight_lbs")
    if max_w is None or max_w == "":
        max_weight = None
    else:
        try:
            max_weight = float(max_w)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_weight_lbs must be a number") from exc
        if max_weight < 0:
            raise ValueError("max_weight_lbs must be >= 0")
    return {
        "id": eid,
        "name": name,
        "tag": tag,
        "max_weight_lbs": max_weight,
        "notes": str(raw.get("notes") or ""),
        "source": str(raw.get("source") or "owned"),
    }


def _as_equipment(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {"items": []}
    items = raw.get("items")
    if not isinstance(items, list):
        items = []
    kept = []
    for i in items:
        if not isinstance(i, dict):
            continue
        try:
            kept.append(normalize_equipment_item(i))
        except ValueError:
            continue
    out = {k: v for k, v in raw.items() if k != "items"}
    out["items"] = kept
    return out


def load_workspace_equipment() -> Tuple[dict, str]:
    """Read fitness/exercises/equipment.json (repo SoT, then Vercel bundle)."""
    from .workout_planner import load_json_file

    for path in _equipment_file_candidates():
        if not path.is_file():
            continue
        raw = load_json_file(path, {})
        inv = _as_equipment(raw)
        if not inv.get("items"):
            continue
        return inv, EQUIPMENT_PATH
    return {"items": []}, "default"


def _equipment_uid(user_id: str = "") -> str:
    return (user_id or "").strip() or EQUIPMENT_ROW_DEFAULT


def _turso_row_empty(raw: Any) -> bool:
    """Missing/invalid row only. A stored items list (even []) is SoT."""
    if raw is None:
        return True
    if not isinstance(raw, dict):
        return True
    if "items" not in raw:
        return True
    if not isinstance(raw.get("items"), list):
        return True
    return False


def _turso_get_equipment(user_id: str) -> Optional[dict]:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        return None
    uid = _equipment_uid(user_id)
    with connect() as conn:
        conn.execute(ENSURE_EQUIPMENT_SQL)
        row = conn.execute(
            "SELECT payload FROM equipment_inventory WHERE user_id = ?",
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


def _turso_put_equipment(user_id: str, equipment: dict) -> None:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        raise RuntimeError("turso env missing")
    uid = _equipment_uid(user_id)
    inv = _as_equipment(equipment)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    blob = json.dumps(inv, separators=(",", ":"))
    with connect() as conn:
        conn.execute(ENSURE_EQUIPMENT_SQL)
        conn.execute(
            """
            INSERT INTO equipment_inventory(user_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (uid, blob, now),
        )


def save_preview_equipment(equipment: dict, user_id: str = "") -> dict:
    """Persist gear edits to Turso. Fail honest if the write cannot land."""
    from .turso_http import turso_enabled

    if not turso_enabled():
        raise RuntimeError("turso env missing")
    inv = _as_equipment(equipment)
    _turso_put_equipment(user_id, inv)
    existing = _turso_get_equipment(user_id)
    if _turso_row_empty(existing):
        raise RuntimeError("turso write not visible on readback")
    return _as_equipment(existing)


def load_preview_equipment(user_id: str = "") -> Tuple[dict, str]:
    """Turso gear if present; seed from bundled file when the row is empty."""
    from .turso_http import turso_enabled

    file_inv, file_src = load_workspace_equipment()
    if not turso_enabled():
        return file_inv, file_src
    try:
        existing = _turso_get_equipment(user_id)
    except Exception:
        return file_inv, file_src
    if not _turso_row_empty(existing):
        return _as_equipment(existing), "turso"
    if not file_inv.get("items"):
        return file_inv, file_src
    try:
        _turso_put_equipment(user_id, file_inv)
    except Exception:
        return file_inv, file_src
    return file_inv, "turso"


def add_equipment_item(equipment: dict, raw: dict) -> dict:
    inv = deepcopy(equipment) if equipment else {"items": []}
    item = normalize_equipment_item(raw)
    items = list(inv.get("items") or [])
    replaced = False
    for i, existing in enumerate(items):
        if not isinstance(existing, dict):
            continue
        same_id = str(existing.get("id") or "").lower() == item["id"].lower()
        same_tag = normalize_equipment_tag(existing.get("tag") or "") == item["tag"]
        same_name = str(existing.get("name") or "").strip().lower() == item["name"].lower()
        if same_id or same_tag or same_name:
            items[i] = item
            replaced = True
            break
    if not replaced:
        items.append(item)
    inv["items"] = items
    inv["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return inv


def update_equipment_item(equipment: dict, raw: dict) -> dict:
    """Edit an existing gear row. Never invents a new item."""
    inv = deepcopy(equipment) if equipment else {"items": []}
    iid = str((raw or {}).get("id") or "").strip()
    if not iid:
        raise ValueError("equipment id required")
    want = iid.lower()
    items = list(inv.get("items") or [])
    idx = None
    for i, existing in enumerate(items):
        if str(existing.get("id") or "").strip().lower() == want:
            idx = i
            break
    if idx is None:
        raise ValueError("equipment not found")
    existing = dict(items[idx])
    overlay = {
        "id": existing.get("id") or iid,
        "name": raw.get("name", existing.get("name")),
        "tag": raw.get("tag", existing.get("tag")),
        "max_weight_lbs": (
            raw["max_weight_lbs"] if "max_weight_lbs" in raw else existing.get("max_weight_lbs")
        ),
        "notes": raw.get("notes", existing.get("notes") or ""),
        "source": raw.get("source", existing.get("source") or "owned"),
    }
    items[idx] = normalize_equipment_item(overlay)
    inv["items"] = items
    inv["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return inv


def remove_equipment_item(
    equipment: dict, equipment_id: str = "", tag: str = "", name: str = ""
) -> dict:
    inv = deepcopy(equipment) if equipment else {"items": []}
    nid = (equipment_id or "").strip().lower()
    ntag = normalize_equipment_tag(tag) if tag else ""
    nname = (name or "").strip().lower()
    new_list = []
    removed = False
    for existing in inv.get("items") or []:
        if not isinstance(existing, dict):
            continue
        eid = str(existing.get("id") or "").lower()
        etag = normalize_equipment_tag(existing.get("tag") or "")
        ename = str(existing.get("name") or "").lower()
        if (nid and eid == nid) or (ntag and etag == ntag) or (nname and ename == nname):
            removed = True
            continue
        new_list.append(existing)
    if not removed:
        raise ValueError("equipment not found")
    inv["items"] = new_list
    inv["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return inv


def owned_equipment_items(equipment: Optional[dict]) -> List[Dict[str, Any]]:
    if not isinstance(equipment, dict):
        return []
    return list(_as_equipment(equipment).get("items") or [])


def owned_equipment_tags(equipment: Optional[dict]) -> set:
    return {str(i.get("tag") or "") for i in owned_equipment_items(equipment) if i.get("tag")}
