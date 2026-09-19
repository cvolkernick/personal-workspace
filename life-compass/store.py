"""JSON persistence for Life Compass attention/radar state. Lives with the goal workspace."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from engine import empty_state

PKG = Path(__file__).resolve().parent
DEFAULT_STATE_PATH = PKG / "data" / "state.json"


def resolve_state_path(path: Optional[Path] = None) -> Path:
    env = (os.environ.get("LIFE_COMPASS_STATE") or "").strip()
    if path is not None:
        return Path(path)
    if env:
        return Path(env).expanduser()
    return DEFAULT_STATE_PATH


def load_state(path: Optional[Path] = None) -> dict[str, Any]:
    p = resolve_state_path(path)
    if not p.is_file():
        return empty_state()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    base = empty_state()
    base.update(data)
    return base


def save_state(state: dict[str, Any], path: Optional[Path] = None) -> Path:
    p = resolve_state_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, default=str) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p
