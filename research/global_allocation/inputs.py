"""Load append-only anchor and quantity files. Later entries win."""

from __future__ import annotations

import json
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent


def load_entries(path: Path, key: str) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for entry in data["entries"]:
        out[entry[key]] = entry
    return out


def resolve(root: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    local = Path(root) / "inputs"
    anchors_path = local / "anchors.json"
    quantities_path = local / "quantities.json"
    if not anchors_path.is_file():
        anchors_path = PACKAGE_ROOT / "inputs" / "anchors.json"
    if not quantities_path.is_file():
        quantities_path = PACKAGE_ROOT / "inputs" / "quantities.json"
    return load_entries(anchors_path, "class_id"), load_entries(quantities_path, "id")
