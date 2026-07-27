"""Versioned file store for world-state and briefs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = PACKAGE_DIR / "data"


def ensure_data_dirs(data_dir: Optional[Path] = None) -> Path:
    root = Path(data_dir or DEFAULT_DATA_DIR)
    (root / "history").mkdir(parents=True, exist_ok=True)
    (root / "briefs").mkdir(parents=True, exist_ok=True)
    return root


def save_json(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def world_state_latest_path(data_dir: Optional[Path] = None) -> Path:
    return ensure_data_dirs(data_dir) / "world_state_latest.json"


def world_state_history_path(version_id: str, data_dir: Optional[Path] = None) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in version_id)
    return ensure_data_dirs(data_dir) / "history" / f"world_state_{safe}.json"


def brief_latest_paths(data_dir: Optional[Path] = None) -> tuple[Path, Path]:
    root = ensure_data_dirs(data_dir) / "briefs"
    return root / "brief_latest.json", root / "brief_latest.md"


def brief_version_paths(version_id: str, data_dir: Optional[Path] = None) -> tuple[Path, Path]:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in version_id)
    root = ensure_data_dirs(data_dir) / "briefs"
    return root / f"brief_{safe}.json", root / f"brief_{safe}.md"


def save_world_state(state: dict[str, Any], data_dir: Optional[Path] = None) -> dict[str, Path]:
    """Write latest + versioned history copy. Returns paths."""
    data_dir = ensure_data_dirs(data_dir)
    vid = str(state.get("version_id") or "unknown")
    latest = world_state_latest_path(data_dir)
    hist = world_state_history_path(vid, data_dir)
    save_json(latest, state)
    save_json(hist, state)
    return {"latest": latest, "history": hist}


def load_world_state(data_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    path = world_state_latest_path(data_dir)
    if not path.is_file():
        return None
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_history(data_dir: Optional[Path] = None) -> list[Path]:
    hist = ensure_data_dirs(data_dir) / "history"
    return sorted(hist.glob("world_state_*.json"))


def save_brief(
    brief: dict[str, Any],
    markdown: str,
    data_dir: Optional[Path] = None,
) -> dict[str, Path]:
    data_dir = ensure_data_dirs(data_dir)
    vid = str(brief.get("version_id") or "unknown")
    latest_json, latest_md = brief_latest_paths(data_dir)
    ver_json, ver_md = brief_version_paths(vid, data_dir)
    save_json(latest_json, brief)
    save_json(ver_json, brief)
    latest_md.write_text(markdown, encoding="utf-8")
    ver_md.write_text(markdown, encoding="utf-8")
    return {
        "latest_json": latest_json,
        "latest_md": latest_md,
        "version_json": ver_json,
        "version_md": ver_md,
    }
