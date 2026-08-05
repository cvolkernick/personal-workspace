"""Offline fixture source — high-signal multi-domain seed events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DEFAULT_FIXTURE = FIXTURES_DIR / "sample_events.json"


class FixtureSource:
    name = "fixture"

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or DEFAULT_FIXTURE)

    def fetch(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            events = raw.get("events") or []
        elif isinstance(raw, list):
            events = raw
        else:
            return []
        out: list[dict[str, Any]] = []
        for ev in events:
            if isinstance(ev, dict) and ev.get("title") and ev.get("domain"):
                out.append(dict(ev))
        return out
