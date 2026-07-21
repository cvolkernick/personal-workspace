"""Canonical ordering for plan/actual allocation blocks (side-by-side pies)."""

from __future__ import annotations

from typing import Any

# Preferred visual order for comparison charts
CANONICAL_IDS: dict[str, int] = {
    "sleep": 0,
    "duchess-walk": 1,
    "workout": 2,
    "lyft": 3,
    "lyft-break": 4,
    "_unaccounted": 900,
}

ROLE_RANK: dict[str, int] = {
    "reserve": 10,
    "fixed": 20,
    "session": 30,
    "fill": 40,
    "break": 50,
    "adhoc": 60,
    "meta": 70,
    "unaccounted": 80,
}


def block_sort_key(block: dict[str, Any]) -> tuple:
    bid = str(block.get("id") or "")
    role = str(block.get("role") or "")
    # Always last so pies/legends line up and free time is easy to spot
    if bid == "_unaccounted" or role == "unaccounted":
        return (2, 0, bid)
    if bid in CANONICAL_IDS:
        return (0, CANONICAL_IDS[bid], bid)
    return (1, ROLE_RANK.get(role, 50), str(block.get("title") or ""), bid)


def sort_allocation_blocks(blocks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Stable preferred order: sleep → Duchess → workout → Lyft → … → unaccounted."""
    return sorted(list(blocks or []), key=block_sort_key)


def id_sort_key(item_id: str) -> tuple:
    bid = str(item_id or "")
    if bid == "_unaccounted":
        return (2, 0, bid)
    if bid in CANONICAL_IDS:
        return (0, CANONICAL_IDS[bid], bid)
    return (1, 50, bid)
