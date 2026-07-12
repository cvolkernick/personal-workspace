"""Load/save exercise catalog + training goals (local workspace / GitHub)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .github_client import GitHubLiftClient
from .nutrition_store import read_nutrition_file, write_nutrition_file
from .workout_planner import (
    CATALOG_PATH,
    GOALS_PATH,
    default_catalog,
    default_goals,
    normalize_goals,
)


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
