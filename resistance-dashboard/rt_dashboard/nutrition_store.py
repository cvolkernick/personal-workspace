"""Load/save nutrition inventory + targets.

Inventory file helpers remain for the Turso-dark pantry fallback.
Applied nutrition targets (kcal / macros / fiber / sugar / sodium) persist in
Turso. ``targets.json`` is the empty-start seed only — GitHub-as-database is
dropped. Apply/Save never needs a GitHub token.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .github_client import GitHubError, GitHubLiftClient
from .nutrition_planner import (
    DEFAULT_TARGETS,
    INVENTORY_PATH,
    TARGETS_PATH,
    default_inventory,
    load_json_file,
    normalize_targets,
    save_json_file,
)

SOT_TURSO = "turso"
SOT_FILE = TARGETS_PATH
FALLBACK_TURSO_DARK = "turso_dark"
NAMED_TARGETS_SOTS = (SOT_TURSO, SOT_FILE)
TARGETS_ROW_DEFAULT = "default"

ENSURE_TARGETS_SQL = """
CREATE TABLE IF NOT EXISTS nutrition_targets (
  user_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""


def _targets_file_candidates() -> list:
    """Repo-root seed first, then the Vercel-bundled copy under resistance-dashboard/."""
    here = Path(__file__).resolve()
    rel = Path(TARGETS_PATH)
    ordered = []
    # rt_dashboard/nutrition_store.py → parents[2] = repo root
    if len(here.parents) >= 3:
        ordered.append(here.parents[2] / rel)
    # parents[1] = resistance-dashboard (Vercel project root)
    if len(here.parents) >= 2:
        ordered.append(here.parents[1] / rel)
    cwd = Path.cwd().resolve()
    ordered.append(cwd / rel)
    for parent in cwd.parents:
        ordered.append(parent / rel)
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


def load_workspace_targets() -> Tuple[dict, str]:
    """Read fitness/nutrition/targets.json seed (same file Pi ships).

    Vercel Root Directory is resistance-dashboard/, so a byte-identical copy
    ships at resistance-dashboard/fitness/nutrition/targets.json (includeFiles).
    Source is TARGETS_PATH when the file is found, else "default".
    Live reads go through ``load_preview_targets`` (Turso first).
    """
    for path in _targets_file_candidates():
        if not path.is_file():
            continue
        raw = load_json_file(path, {})
        if not raw:
            continue
        return normalize_targets(raw), TARGETS_PATH
    return normalize_targets(DEFAULT_TARGETS), "default"


def canonicalize_targets_source(source: str) -> str:
    """Named SoT only: turso vs fitness/nutrition/targets.json. Never github."""
    raw = (source or "").strip()
    if raw == SOT_TURSO or raw.startswith("turso"):
        return SOT_TURSO
    return SOT_FILE


def targets_source_fields(source: str) -> dict:
    """API/config targets SoT. File reads are labeled turso_dark, never github."""
    active = canonicalize_targets_source(source)
    return {
        "targets": active,
        "targets_sot": active,
        "targets_fallback": None if active == SOT_TURSO else FALLBACK_TURSO_DARK,
    }


def _targets_uid(user_id: str = "", *, allow_default: bool = True) -> str:
    uid = (user_id or "").strip()
    if uid:
        return uid
    if not allow_default:
        raise ValueError("targets user_id required")
    return TARGETS_ROW_DEFAULT


def _turso_row_empty(raw: Any) -> bool:
    """Missing/invalid row only. A stored dict with calories is SoT."""
    if raw is None or not isinstance(raw, dict):
        return True
    if "calories" not in raw and "protein_g" not in raw:
        return True
    return False


def _turso_get_targets(user_id: str) -> Optional[dict]:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        return None
    uid = _targets_uid(user_id)
    with connect() as conn:
        conn.execute(ENSURE_TARGETS_SQL)
        row = conn.execute(
            "SELECT payload FROM nutrition_targets WHERE user_id = ?",
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


def _turso_put_targets(user_id: str, targets: dict) -> None:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        raise RuntimeError("turso env missing")
    uid = _targets_uid(user_id)
    blob = json.dumps(normalize_targets(targets), separators=(",", ":"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect() as conn:
        conn.execute(ENSURE_TARGETS_SQL)
        conn.execute(
            """
            INSERT INTO nutrition_targets(user_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (uid, blob, now),
        )


def save_preview_targets(targets: dict, user_id: str = "") -> dict:
    """Persist applied targets to Turso. Fail honest if the write cannot land."""
    from .turso_http import turso_enabled

    if not turso_enabled():
        raise RuntimeError("turso env missing")
    t = normalize_targets(targets)
    _turso_put_targets(user_id, t)
    existing = _turso_get_targets(user_id)
    if _turso_row_empty(existing):
        raise RuntimeError("turso write not visible on readback")
    return normalize_targets(existing)


def load_preview_targets(user_id: str = "") -> Tuple[dict, str]:
    """Turso applied targets if present; seed from bundled file when missing.

    After seed, Turso is SoT. File is the empty-start seed (live values on
    first deploy). Source is "turso" or TARGETS_PATH. Turso-dark reads keep
    the file name — targets_source_fields labels the fallback.
    """
    from .turso_http import turso_enabled

    file_t, file_src = load_workspace_targets()
    file_src = canonicalize_targets_source(file_src)
    if not turso_enabled():
        return file_t, file_src
    try:
        existing = _turso_get_targets(user_id)
    except Exception:
        return file_t, file_src
    if not _turso_row_empty(existing):
        return normalize_targets(existing), SOT_TURSO
    try:
        _turso_put_targets(user_id, file_t)
    except Exception:
        return file_t, file_src
    return file_t, SOT_TURSO


def _targets_local_write_path(file_client=None) -> Optional[Path]:
    base = ""
    if file_client is not None:
        base = getattr(file_client, "local_fallback_dir", None) or ""
    if not base:
        base = (os.environ.get("LOCAL_WORKSPACE_DIR") or "").strip()
    if not base:
        return None
    return Path(base) / TARGETS_PATH


def persist_targets(
    targets: dict,
    user_id: str = "",
    *,
    file_client=None,
    message: str = "nutrition: update daily macro targets",
) -> dict:
    """Write the named SoT. Turso when live; local file only when Turso is dark.

    Never writes GitHub. Apply/Save must not need a GitHub token.
    """
    from .turso_http import turso_enabled

    t = normalize_targets(targets)
    if turso_enabled():
        saved = save_preview_targets(t, user_id)
        return {
            "ok": True,
            "source": SOT_TURSO,
            "turso": True,
            "github": False,
            "local": False,
            "verified_on_readback": True,
            "targets": saved,
            "path": None,
            "message": message,
        }
    path = _targets_local_write_path(file_client)
    if path is None:
        raise RuntimeError("turso env missing")
    save_json_file(path, t)
    return {
        "ok": True,
        "source": SOT_FILE,
        "turso": False,
        "github": False,
        "local": True,
        "verified_on_readback": False,
        "targets": t,
        "path": TARGETS_PATH,
        "message": message,
        "note": "Saved locally (Turso dark; GitHub-as-database dropped)",
    }


def persist_targets_result(
    targets: dict,
    user_id: str = "",
    *,
    file_client=None,
    message: str = "nutrition: update daily macro targets",
) -> dict:
    """Never raises; returns a write dict for ``nutrition_write_ok``."""
    try:
        return persist_targets(
            targets, user_id, file_client=file_client, message=message
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "source": SOT_TURSO,
            "turso": False,
            "github": False,
            "local": False,
            "verified_on_readback": False,
            "error": str(exc) or type(exc).__name__,
            "message": message,
        }


def applied_targets_write(
    targets: dict,
    user_id: str = "",
    *,
    file_client=None,
    message: str = "nutrition: update daily macro targets",
) -> Tuple[bool, str, dict, dict]:
    """Persist + honest ok. Returns (stuck, err, write, persisted_targets)."""
    write = persist_targets_result(
        targets, user_id, file_client=file_client, message=message
    )
    stuck, err = nutrition_write_ok(write)
    persisted = (
        write.get("targets")
        if stuck and isinstance(write.get("targets"), dict)
        else normalize_targets(targets)
    )
    return stuck, err, write, persisted


def _local_path(client: GitHubLiftClient, rel: str) -> Path:
    base = client.local_fallback_dir or ""
    if not base:
        raise GitHubError("LOCAL_WORKSPACE_DIR required for local nutrition store")
    return Path(base) / rel


def read_nutrition_file(client: GitHubLiftClient, rel: str, default: dict) -> Tuple[dict, str]:
    """
    Returns (data, source) where source is github|local|default.
    Prefers local if prefer_local, else github then local fallback.
    """
    if client.prefer_local and client.local_fallback_dir:
        p = _local_path(client, rel)
        if p.is_file():
            return load_json_file(p, default), "local"
        return default_inventory() if "inventory" in rel else normalize_targets(DEFAULT_TARGETS), "default"

    # try github
    try:
        fc = client.get_file(rel)
        data = json.loads(fc.content)
        return data, "github"
    except Exception:
        pass

    # local fallback
    if client.local_fallback_dir:
        p = _local_path(client, rel)
        if p.is_file():
            return load_json_file(p, default), "local_fallback"

    if "inventory" in rel:
        return default_inventory(), "default"
    return normalize_targets(DEFAULT_TARGETS), "default"


def write_nutrition_file(
    client: GitHubLiftClient,
    rel: str,
    data: dict,
    message: str,
) -> dict:
    content = json.dumps(data, indent=2) + "\n"
    # Always write local if available (so merge/read works offline)
    local_written = False
    if client.local_fallback_dir:
        p = _local_path(client, rel)
        save_json_file(p, data)
        local_written = True

    if client.prefer_local or not client.token:
        return {
            "path": rel,
            "local": local_written,
            "github": False,
            "message": message,
            "note": "Saved locally"
            + ("" if client.token or client.prefer_local else " (no GITHUB_TOKEN for remote write)"),
        }

    # GitHub write
    try:
        fc = client.get_file(rel)
        sha = fc.sha
    except GitHubError as e:
        if e.status == 404:
            sha = None
        else:
            # still have local
            return {
                "path": rel,
                "local": local_written,
                "github": False,
                "error": str(e),
                "message": message,
            }

    try:
        result = client.put_file(rel, content, message=message, sha=sha)
        return {
            "path": rel,
            "local": local_written,
            "github": True,
            "result": result,
            "message": message,
        }
    except GitHubError as e:
        return {
            "path": rel,
            "local": local_written,
            "github": False,
            "error": str(e),
            "message": message,
        }


def nutrition_write_ok(write: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """Whether a nutrition persist actually stuck.

    Turso success requires verified readback. GitHub success still counts for
    inventory/catalog (not targets). A GitHub *error* (token configured, remote
    write attempted) is a failure even if a local copy was saved. Local-only
    setups (Turso dark / no token) succeed when ``local`` is True.
    """
    write = write or {}
    if write.get("github"):
        return True, ""
    if write.get("turso") or write.get("source") == SOT_TURSO:
        if write.get("error"):
            err = str(write.get("error") or "").strip() or "turso write did not persist"
            return False, err
        if write.get("verified_on_readback"):
            return True, ""
        return False, "turso write did not persist"
    if write.get("error"):
        err = str(write.get("error") or "").strip() or "nutrition write failed"
        return False, err
    if write.get("local"):
        return True, ""
    return False, "nutrition write did not persist"


def load_inventory_and_targets(
    client: GitHubLiftClient, user_id: str = ""
) -> Dict[str, Any]:
    inv, inv_src = read_nutrition_file(
        client, INVENTORY_PATH, default_inventory()
    )
    targets, tgt_src = load_preview_targets(user_id)
    # Ensure local inventory seed exists for first run. Targets live in Turso.
    if client.local_fallback_dir:
        inv_path = _local_path(client, INVENTORY_PATH)
        if not inv_path.is_file() and inv.get("ingredients"):
            save_json_file(inv_path, inv)
    sources = {"inventory": inv_src}
    sources.update(targets_source_fields(tgt_src))
    return {
        "inventory": inv,
        "targets": normalize_targets(targets),
        "sources": sources,
    }
