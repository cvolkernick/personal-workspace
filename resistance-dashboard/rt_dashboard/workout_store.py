"""Load/save exercise catalog + training goals (local workspace / GitHub)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from .github_client import GitHubLiftClient
from .nutrition_store import read_nutrition_file, write_nutrition_file
from .workout_planner import (
    CATALOG_PATH,
    DEFAULT_CATALOG,
    GOALS_PATH,
    default_catalog,
    default_goals,
    load_json_file,
    normalize_goals,
)


def _workspace_file_candidates(rel: str) -> list:
    """Repo-root SoT first, then the Vercel-bundled copy under resistance-dashboard/."""
    here = Path(__file__).resolve()
    rel_path = Path(rel)
    ordered = []
    # rt_dashboard/workout_store.py → parents[2] = repo root
    if len(here.parents) >= 3:
        ordered.append(here.parents[2] / rel_path)
    # parents[1] = resistance-dashboard (Vercel project root)
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


def load_workspace_goals() -> Tuple[dict, str]:
    """Read fitness/exercises/goals.json (same file Pi uses).

    Vercel Root Directory is resistance-dashboard/, so a byte-identical copy
    ships at resistance-dashboard/fitness/exercises/goals.json (includeFiles).
    Source is GOALS_PATH when the file is found, else "default".
    """
    for path in _workspace_file_candidates(GOALS_PATH):
        if not path.is_file():
            continue
        raw = load_json_file(path, {})
        if not raw:
            continue
        return normalize_goals(raw), GOALS_PATH
    return normalize_goals(default_goals()), "default"


def load_workspace_catalog() -> Tuple[dict, str]:
    """Read fitness/exercises/catalog.json (full file; ~7KB / 19 exercises).

    Same SoT-then-bundle walk as targets.json / goals.json.
    Source is CATALOG_PATH when the file is found, else "default".
    """
    for path in _workspace_file_candidates(CATALOG_PATH):
        if not path.is_file():
            continue
        raw = load_json_file(path, {})
        if not isinstance(raw, dict) or not raw:
            continue
        if not isinstance(raw.get("exercises"), list):
            raw = {**raw, "exercises": []}
        return raw, CATALOG_PATH
    return dict(DEFAULT_CATALOG), "default"


def load_catalog_and_goals(client: GitHubLiftClient) -> Dict[str, Any]:
    catalog, cat_src = read_nutrition_file(client, CATALOG_PATH, default_catalog())
    goals, goals_src = read_nutrition_file(
        client, GOALS_PATH, normalize_goals(default_goals())
    )
    # Seed local files on first run
    if client.local_fallback_dir:
        base = Path(client.local_fallback_dir)
        cat_path = base / CATALOG_PATH
        goals_path = base / GOALS_PATH
        if not cat_path.is_file() and (catalog.get("exercises") or []):
            from .workout_planner import save_json_file

            save_json_file(cat_path, catalog)
        if not goals_path.is_file():
            from .workout_planner import save_json_file

            save_json_file(goals_path, normalize_goals(goals))
    return {
        "catalog": catalog if isinstance(catalog, dict) else default_catalog(),
        "goals": normalize_goals(goals if isinstance(goals, dict) else None),
        "sources": {"catalog": cat_src, "goals": goals_src},
    }


def write_catalog(client: GitHubLiftClient, catalog: dict, message: str) -> dict:
    return write_nutrition_file(client, CATALOG_PATH, catalog, message=message)


def write_goals(client: GitHubLiftClient, goals: dict, message: str) -> dict:
    return write_nutrition_file(
        client, GOALS_PATH, normalize_goals(goals), message=message
    )
