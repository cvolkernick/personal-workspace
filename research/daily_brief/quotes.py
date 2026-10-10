"""Quote of the day for editions that do not bring one of their own.

The list is public-domain or long-attributed lines, kept short. The choice is
a function of the Eastern date and the edition only: the same edition always
gets the same line, and morning and evening differ. The page HTML-escapes
whatever line it shows.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

_PATH = Path(__file__).with_name("quotes.json")
QUOTES: tuple[dict[str, str], ...] = tuple(json.loads(_PATH.read_text(encoding="utf-8")))


def quote_text(q: Any) -> str:
    """Non-empty quote text, or "" when the edition did not provide one."""
    if isinstance(q, str):
        return q.strip()
    if isinstance(q, dict):
        text = q.get("text")
        if isinstance(text, str):
            return text.strip()
    return ""


def quote_attribution(q: Any) -> str:
    """Speaker from ``by`` or the older ``author`` field."""
    if not isinstance(q, dict):
        return ""
    for key in ("by", "author"):
        value = q.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def index_for(day: str, edition: str, n: int | None = None) -> int:
    """Stable slot in the list. Adjacent AM/PM slots, so the two editions differ."""
    count = len(QUOTES) if n is None else n
    if count <= 0:
        return 0
    try:
        ordinal = date.fromisoformat(day).toordinal()
    except (TypeError, ValueError):
        ordinal = 0
    slot = 1 if edition == "pm" else 0
    return (ordinal * 2 + slot) % count


def quote_for(day: str, edition: str) -> dict[str, str]:
    """The curated line for this date and edition. Same inputs, same line."""
    picked = QUOTES[index_for(day, edition)]
    return {"text": picked["text"], "by": picked["by"]}
