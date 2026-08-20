"""Thin ADVICE seat — Grok COS proposed courses only.

Honest empty when there is no real COS call. Does not read
/api/recommendations, strategy/today.md, Ready, or Cadence.
A later pass can drop a COS packet at orchestra/data/advice.json
(or orchestra/data/advice/latest.json). payload.advice is the
typed slot either way ({items, blank, source}).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

ADVICE_REL_PATHS = (
    Path("orchestra") / "data" / "advice.json",
    Path("orchestra") / "data" / "advice" / "latest.json",
)
COS_SOURCES = frozenset({"grok_cos", "cos", "grok"})
MAX_ADVICE = 3
# Old hygiene / board streams must never be treated as a COS packet.
_REJECT_SOURCES = frozenset(
    {
        "recommendations",
        "recommended_actions",
        "today",
        "today.md",
        "ready",
        "cadence",
        "attention",
        "priorities",
    }
)
_TEAM_STATUS_RE = re.compile(
    r"pull candidate\s*#|^cadence:|\b\d+\s+ready\b.*\bfree\b|ready supply|free agent",
    re.I,
)
_PLACEHOLDER_RE = re.compile(
    r"\be\.g\.|\beg\.|user to fill|unfilled|to be filled",
    re.I,
)


def _blank(source: Optional[str] = None, as_of: Any = None) -> dict[str, Any]:
    return {
        "items": [],
        "blank": True,
        "source": source,
        "as_of": as_of,
    }


def _item_title(item: dict[str, Any]) -> str:
    return str(item.get("title") or item.get("action") or item.get("text") or "").strip()


def keep_advice_item(item: Optional[dict[str, Any]]) -> bool:
    """Few, plain COS courses. No board/today/Ready/Cadence theater."""
    if not isinstance(item, dict):
        return False
    title = _item_title(item)
    if not title:
        return False
    blob = f"{title} {item.get('why') or item.get('detail') or ''}"
    if _PLACEHOLDER_RE.search(blob) or _TEAM_STATUS_RE.search(blob):
        return False
    return True


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    title = _item_title(item)
    row: dict[str, Any] = {"title": title}
    why = str(item.get("why") or item.get("detail") or "").strip()
    check = str(item.get("check") or item.get("falsify") or "").strip()
    if why:
        row["why"] = why
    if check:
        row["check"] = check
    return row


def build_advice(packet: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Typed ADVICE seat. Empty/blank if there is no real COS call. Do not invent."""
    if isinstance(packet, list):
        # payload.advice[] without a COS source is a slot, not a call.
        return _blank()
    if not isinstance(packet, dict):
        return _blank()
    source = str(packet.get("source") or packet.get("from") or "").strip().lower()
    as_of = packet.get("as_of")
    if source in _REJECT_SOURCES:
        return _blank()
    if source and source not in COS_SOURCES:
        return _blank()
    raw_items = packet.get("items") or packet.get("advice") or []
    if not isinstance(raw_items, list):
        return _blank(source or None, as_of)
    # No source and no items — honest empty slot, not a call.
    if not source:
        return _blank()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not keep_advice_item(raw if isinstance(raw, dict) else None):
            continue
        row = _normalize_item(raw)
        key = row["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= MAX_ADVICE:
            break
    if not out:
        return _blank(source, as_of)
    return {
        "items": out,
        "blank": False,
        "source": source,
        "as_of": as_of,
    }


def load_advice_packet(workspace: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Read a COS packet if one exists. Never opens today.md or recommendations."""
    if workspace is None:
        return None
    ws = Path(workspace)
    for rel in ADVICE_REL_PATHS:
        path = ws / rel
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None
    return None
