"""Versioned file store for world-state, briefs, and implication packets."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = PACKAGE_DIR / "data"


def ensure_data_dirs(data_dir: Optional[Path] = None) -> Path:
    root = Path(data_dir or DEFAULT_DATA_DIR)
    (root / "history").mkdir(parents=True, exist_ok=True)
    (root / "briefs").mkdir(parents=True, exist_ok=True)
    (root / "packets").mkdir(parents=True, exist_ok=True)
    (root / "packets" / "history").mkdir(parents=True, exist_ok=True)
    return root


def save_json(path: Path, obj: Any) -> Path:
    """Atomic write (temp + os.replace) so readers never see partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
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


# --- Implication packets (L0 producer SoT; #49) ---


def packets_dir(data_dir: Optional[Path] = None) -> Path:
    return ensure_data_dirs(data_dir) / "packets"


def packet_latest_path(data_dir: Optional[Path] = None) -> Path:
    return packets_dir(data_dir) / "latest.json"


def packet_history_path(packet_id: str, data_dir: Optional[Path] = None) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in packet_id)
    return packets_dir(data_dir) / "history" / f"{safe}.json"


def save_packet(packet: dict[str, Any], data_dir: Optional[Path] = None) -> dict[str, Path]:
    """Write latest.json + id'd archive. Caller must validate fail-closed first."""
    from research.horizon.packets import assert_valid_packet

    assert_valid_packet(packet)
    data_dir = ensure_data_dirs(data_dir)
    pid = str(packet.get("packet_id") or "unknown")
    latest = packet_latest_path(data_dir)
    hist = packet_history_path(pid, data_dir)
    save_json(latest, packet)
    save_json(hist, packet)
    return {"latest": latest, "history": hist}


def load_packet(data_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    path = packet_latest_path(data_dir)
    if not path.is_file():
        return None
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
