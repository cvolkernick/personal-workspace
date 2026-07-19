"""JSON persistence for the time allocator."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .domain import empty_state, normalize_state

# Default store lives next to the package under holistic/data/
_HOLISTIC_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_PATH = _HOLISTIC_ROOT / "data" / "tasks.json"

ENV_DATA_PATH = "TIME_ALLOCATOR_DATA"


def resolve_data_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    env = os.environ.get(ENV_DATA_PATH)
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_DATA_PATH.resolve()


def load_state(path: str | Path | None = None) -> dict[str, Any]:
    p = resolve_data_path(path)
    if not p.is_file():
        return empty_state()
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"invalid store (expected object): {p}")
    return normalize_state(raw)


def save_state(state: dict[str, Any], path: str | Path | None = None) -> Path:
    p = resolve_data_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    state = normalize_state(state)
    payload = {
        "version": int(state.get("version") or 2),
        "items": list(state.get("items") or []),
        "targets": list(state.get("targets") or []),
        "logs": list(state.get("logs") or []),
        "plan": state.get("plan"),
        "sleep_intervals": list(state.get("sleep_intervals") or []),
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p
