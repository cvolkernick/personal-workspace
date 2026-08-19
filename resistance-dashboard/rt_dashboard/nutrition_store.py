"""Load/save nutrition inventory + targets via GitHub or local workspace."""

from __future__ import annotations

import json
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


def _targets_file_candidates() -> list:
    """Repo-root SoT first, then the Vercel-bundled copy under resistance-dashboard/."""
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
    """Read fitness/nutrition/targets.json (same file Pi uses). No inventory.

    Vercel Root Directory is resistance-dashboard/, so a byte-identical copy
    ships at resistance-dashboard/fitness/nutrition/targets.json (includeFiles).
    Source is TARGETS_PATH when the file is found, else "default".
    """
    for path in _targets_file_candidates():
        if not path.is_file():
            continue
        raw = load_json_file(path, {})
        if not raw:
            continue
        return normalize_targets(raw), TARGETS_PATH
    return normalize_targets(DEFAULT_TARGETS), "default"


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


def load_inventory_and_targets(client: GitHubLiftClient) -> Dict[str, Any]:
    inv, inv_src = read_nutrition_file(
        client, INVENTORY_PATH, default_inventory()
    )
    targets, tgt_src = read_nutrition_file(
        client, TARGETS_PATH, normalize_targets(DEFAULT_TARGETS)
    )
    # Ensure local seed files exist for first run
    if client.local_fallback_dir:
        inv_path = _local_path(client, INVENTORY_PATH)
        tgt_path = _local_path(client, TARGETS_PATH)
        if not inv_path.is_file() and inv.get("ingredients"):
            save_json_file(inv_path, inv)
        if not tgt_path.is_file():
            save_json_file(tgt_path, normalize_targets(targets))
    return {
        "inventory": inv,
        "targets": normalize_targets(targets),
        "sources": {"inventory": inv_src, "targets": tgt_src},
    }
