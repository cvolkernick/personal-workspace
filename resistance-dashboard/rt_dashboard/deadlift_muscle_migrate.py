"""Retag custom deadlift rows once. Logged sets are not read or written.

A custom movement with the same id or normalized name wins over catalog.json
at merge time. The live "Barbell Deadlift" row was stored as glutes-only, so
a catalog row alone does not change that session's credits. The first load
rewrites matching rows that are not already at the target tags, stamps the
payload, and writes once. Later loads see the stamp and do not write, so a
legal edit of those muscles stays stored.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# Forearms stay on secondary_muscles and are omitted from volume_secondary.
# They are not a VOLUME_GROUPS member, so credit stays 1.0 / 0.5 on the
# tracked groups only (#1299).
CONVENTIONAL_TAGS: Dict[str, List[str]] = {
    "primary_muscles": ["glutes", "hamstrings", "lower_back"],
    "secondary_muscles": ["quads", "lats_upper_back", "traps", "forearms"],
    "volume_primary": ["glutes", "hamstrings", "lower_back"],
    "volume_secondary": ["quads", "lats_upper_back", "traps"],
}
TRAP_TAGS: Dict[str, List[str]] = {
    "primary_muscles": ["glutes", "hamstrings", "lower_back", "quads"],
    "secondary_muscles": ["lats_upper_back", "traps", "forearms"],
    "volume_primary": ["glutes", "hamstrings", "lower_back", "quads"],
    "volume_secondary": ["lats_upper_back", "traps"],
}
_TAG_KEYS = (
    "primary_muscles",
    "secondary_muscles",
    "volume_primary",
    "volume_secondary",
)
_CONVENTIONAL_NAMES = {
    "barbell deadlift",
    "conventional deadlift",
    "conventional barbell deadlift",
    "deadlift",
}
_TRAP_NAMES = {
    "trap bar deadlift",
    "hex bar deadlift",
}
# Payload key, not an exercise field. normalize_exercise drops unknown
# exercise keys. _as_custom copies every other key on the custom payload.
STAMP_KEY = "deadlift_muscles_1299"
STAMP_VALUE = "1"


def _norm_move(name: str) -> str:
    text = str(name or "").strip().lower().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text)


def _tokens(raw) -> List[str]:
    if isinstance(raw, str):
        raw = [raw]
    out: List[str] = []
    for item in raw or []:
        token = str(item or "").strip().lower().replace("-", "_").replace(" ", "_")
        if token and token not in out:
            out.append(token)
    return out


def deadlift_kind(name: str) -> Optional[str]:
    """``conventional`` or ``trap`` for a custom-movement name. RDL stays out.

    Match is the normalized name, not a substring. Romanian, stiff-leg, and
    RDL rows are left alone. The stored name is not rewritten.
    """
    normalized = _norm_move(name)
    if not normalized:
        return None
    tokens = normalized.split()
    if "rdl" in tokens or "romanian" in normalized or "stiff" in normalized:
        return None
    if normalized in _TRAP_NAMES or (
        "deadlift" in tokens
        and (
            "trap bar" in normalized
            or "hex bar" in normalized
            or "trapbar" in normalized
            or "hexbar" in normalized
        )
    ):
        return "trap"
    if normalized in _CONVENTIONAL_NAMES:
        return "conventional"
    return None


def _tags_match(row: dict, spec: Dict[str, List[str]]) -> bool:
    return all(_tokens(row.get(key)) == list(spec[key]) for key in _TAG_KEYS)


def ensure_deadlift_stamp(payload: dict) -> dict:
    """Keep the one-shot marker on a user write. Do not retag here."""
    if STAMP_KEY not in payload:
        payload[STAMP_KEY] = STAMP_VALUE
    return payload


def retag_custom_movements(custom: Optional[dict]) -> Tuple[dict, bool]:
    """Return ``(payload, changed)``. Sessions and set rows are not touched.

    ``changed`` is true only on the unstamped pass. That pass retags matching
    rows that are not already at the target tags, sets ``STAMP_KEY``, and
    asks the caller to write once. A later load, including one whose deadlift
    tags no longer match, returns the same payload and does not write.
    """
    if not isinstance(custom, dict):
        return {"exercises": []}, False
    if STAMP_KEY in custom:
        return custom, False
    items = custom.get("exercises")
    if not isinstance(items, list):
        return custom, False
    new_items = []
    for row in items:
        if not isinstance(row, dict):
            new_items.append(row)
            continue
        kind = deadlift_kind(str(row.get("name") or ""))
        if kind is None:
            new_items.append(row)
            continue
        spec = TRAP_TAGS if kind == "trap" else CONVENTIONAL_TAGS
        if _tags_match(row, spec):
            new_items.append(row)
            continue
        nxt = dict(row)
        for key in _TAG_KEYS:
            nxt[key] = list(spec[key])
        new_items.append(nxt)
    out = dict(custom)
    out["exercises"] = new_items
    out[STAMP_KEY] = STAMP_VALUE
    return out, True
