"""Vercel pantry: bundled inventory.json + Turso seed/persist.

File is the empty-start seed. After seed, Turso is source of truth.
Never invent ingredients or macros.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

from .nutrition_planner import INVENTORY_PATH, load_json_file

ENSURE_INVENTORY_SQL = """
CREATE TABLE IF NOT EXISTS nutrition_inventory (
  user_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""

INVENTORY_ROW_DEFAULT = "default"


def _workspace_file_candidates(rel: str) -> list:
    """Repo-root SoT first, then the Vercel-bundled copy under resistance-dashboard/."""
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


def _inventory_file_candidates() -> list:
    return _workspace_file_candidates(INVENTORY_PATH)


def _as_inventory(raw: Any) -> dict:
    """Keep file/Turso ingredients as stored. Do not invent items or macros."""
    if not isinstance(raw, dict):
        return {"ingredients": []}
    ings = raw.get("ingredients")
    if not isinstance(ings, list):
        ings = []
    kept = [i for i in ings if isinstance(i, dict) and str(i.get("name") or "").strip()]
    out = {k: v for k, v in raw.items() if k != "ingredients"}
    out["ingredients"] = kept
    return out


def load_workspace_inventory() -> Tuple[dict, str]:
    """Read fitness/nutrition/inventory.json (same file Pi uses).

    Vercel Root Directory is resistance-dashboard/, so a byte-identical copy
    ships at resistance-dashboard/fitness/nutrition/inventory.json (includeFiles).
    Source is INVENTORY_PATH when the file is found, else "default".
    Never "unset".
    """
    for path in _inventory_file_candidates():
        if not path.is_file():
            continue
        raw = load_json_file(path, {})
        inv = _as_inventory(raw)
        if not inv.get("ingredients"):
            continue
        return inv, INVENTORY_PATH
    return {"ingredients": []}, "default"


def _inventory_uid(user_id: str = "") -> str:
    return (user_id or "").strip() or INVENTORY_ROW_DEFAULT


def _turso_row_empty(raw: Any) -> bool:
    """Missing/invalid row only. A stored ingredients list (even []) is SoT."""
    if raw is None:
        return True
    if not isinstance(raw, dict):
        return True
    if "ingredients" not in raw:
        return True
    if not isinstance(raw.get("ingredients"), list):
        return True
    return False


def _turso_get_inventory(user_id: str) -> Optional[dict]:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        return None
    uid = _inventory_uid(user_id)
    with connect() as conn:
        conn.execute(ENSURE_INVENTORY_SQL)
        row = conn.execute(
            "SELECT payload FROM nutrition_inventory WHERE user_id = ?",
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


def _turso_put_inventory(user_id: str, inventory: dict) -> None:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        raise RuntimeError("turso env missing")
    uid = _inventory_uid(user_id)
    inv = _as_inventory(inventory)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    blob = json.dumps(inv, separators=(",", ":"))
    with connect() as conn:
        conn.execute(ENSURE_INVENTORY_SQL)
        conn.execute(
            """
            INSERT INTO nutrition_inventory(user_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (uid, blob, now),
        )


def save_preview_inventory(inventory: dict, user_id: str = "") -> dict:
    """Persist Kitchen edits to Turso. Fail honest if the write cannot land."""
    from .turso_http import turso_enabled

    if not turso_enabled():
        raise RuntimeError("turso env missing")
    inv = _as_inventory(inventory)
    _turso_put_inventory(user_id, inv)
    existing = _turso_get_inventory(user_id)
    if _turso_row_empty(existing):
        raise RuntimeError("turso write not visible on readback")
    return _as_inventory(existing)


def load_preview_inventory(user_id: str = "") -> Tuple[dict, str]:
    """Turso pantry if present; seed from bundled file when the row is empty.

    After seed, Turso is SoT. File is the empty-start seed only.
    Source is "turso" or INVENTORY_PATH (never "unset").
    """
    from .turso_http import turso_enabled

    file_inv, file_src = load_workspace_inventory()
    if not turso_enabled():
        return file_inv, file_src
    try:
        existing = _turso_get_inventory(user_id)
    except Exception:
        return file_inv, file_src
    if not _turso_row_empty(existing):
        return _as_inventory(existing), "turso"
    if not file_inv.get("ingredients"):
        return file_inv, file_src
    try:
        _turso_put_inventory(user_id, file_inv)
    except Exception:
        return file_inv, file_src
    return file_inv, "turso"
