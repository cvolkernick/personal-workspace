"""Pure domain operations for the time allocator (no I/O).

Priority: higher integer = more important / more weight when allocating.
"""

from __future__ import annotations

import re
import uuid
from copy import deepcopy
from typing import Any

# Starter list used when seeding an empty store (MVP starting point).
STARTER_ITEMS: list[dict[str, Any]] = [
    {
        "id": "seed-deep-work",
        "title": "Deep work / primary project",
        "kind": "task",
        "priority": 5,
        "minutes": 0,
    },
    {
        "id": "seed-fitness",
        "title": "Fitness / movement",
        "kind": "goal",
        "priority": 4,
        "minutes": 0,
    },
    {
        "id": "seed-admin",
        "title": "Admin / email / chores",
        "kind": "task",
        "priority": 2,
        "minutes": 0,
    },
    {
        "id": "seed-learning",
        "title": "Learning / skill growth",
        "kind": "goal",
        "priority": 3,
        "minutes": 0,
    },
    {
        "id": "seed-rest",
        "title": "Rest / buffer",
        "kind": "task",
        "priority": 1,
        "minutes": 0,
    },
]


def _slug_id(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-") or "item"
    return f"{base[:40]}-{uuid.uuid4().hex[:8]}"


def empty_state() -> dict[str, Any]:
    return {"version": 1, "items": []}


def seed_starter(state: dict[str, Any]) -> dict[str, Any]:
    """Replace items with the starter core list (or fill if empty)."""
    out = deepcopy(state) if state else empty_state()
    out["items"] = deepcopy(STARTER_ITEMS)
    out["version"] = int(out.get("version") or 1)
    return out


def list_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    items = list(state.get("items") or [])
    # Higher priority first, then title for stability.
    return sorted(
        items,
        key=lambda it: (-int(it.get("priority") or 0), str(it.get("title") or "")),
    )


def get_item(state: dict[str, Any], key: str) -> dict[str, Any] | None:
    key_l = key.strip().lower()
    for it in state.get("items") or []:
        if str(it.get("id") or "").lower() == key_l:
            return it
        if str(it.get("title") or "").lower() == key_l:
            return it
    return None


def add_item(
    state: dict[str, Any],
    title: str,
    *,
    kind: str = "task",
    priority: int = 1,
    minutes: int = 0,
    item_id: str | None = None,
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    kind = (kind or "task").strip().lower()
    if kind not in ("task", "goal"):
        raise ValueError("kind must be 'task' or 'goal'")
    priority = int(priority)
    minutes = max(0, int(minutes))
    if get_item(state, title) is not None:
        raise ValueError(f"item already exists with title: {title}")
    new_id = (item_id or _slug_id(title)).strip()
    if get_item(state, new_id) is not None:
        raise ValueError(f"item already exists with id: {new_id}")
    out = deepcopy(state) if state else empty_state()
    items = list(out.get("items") or [])
    items.append(
        {
            "id": new_id,
            "title": title,
            "kind": kind,
            "priority": priority,
            "minutes": minutes,
        }
    )
    out["items"] = items
    out["version"] = int(out.get("version") or 1)
    return out


def remove_item(state: dict[str, Any], key: str) -> dict[str, Any]:
    key = (key or "").strip()
    if not key:
        raise ValueError("key is required")
    found = get_item(state, key)
    if found is None:
        raise KeyError(f"no item matching: {key}")
    rid = found["id"]
    out = deepcopy(state) if state else empty_state()
    out["items"] = [it for it in (out.get("items") or []) if it.get("id") != rid]
    return out


def set_priority(state: dict[str, Any], key: str, priority: int) -> dict[str, Any]:
    found = get_item(state, key)
    if found is None:
        raise KeyError(f"no item matching: {key}")
    out = deepcopy(state)
    for it in out.get("items") or []:
        if it.get("id") == found["id"]:
            it["priority"] = int(priority)
            break
    return out


def set_minutes(state: dict[str, Any], key: str, minutes: int) -> dict[str, Any]:
    found = get_item(state, key)
    if found is None:
        raise KeyError(f"no item matching: {key}")
    out = deepcopy(state)
    for it in out.get("items") or []:
        if it.get("id") == found["id"]:
            it["minutes"] = max(0, int(minutes))
            break
    return out


def allocate_total(state: dict[str, Any], total_minutes: int) -> dict[str, Any]:
    """Distribute total_minutes across items weighted by priority.

    Higher priority gets more minutes. Remainder minutes go to the highest-
    priority item so the sum equals total_minutes exactly.
    """
    total = max(0, int(total_minutes))
    out = deepcopy(state) if state else empty_state()
    items = list(out.get("items") or [])
    if not items:
        return out
    weights = [max(0, int(it.get("priority") or 0)) for it in items]
    weight_sum = sum(weights)
    if weight_sum <= 0:
        # Equal split if all priorities are zero/missing.
        base = total // len(items)
        rem = total - base * len(items)
        for i, it in enumerate(items):
            it["minutes"] = base + (1 if i < rem else 0)
        out["items"] = items
        return out

    allocated = 0
    shares: list[int] = []
    for w in weights:
        share = (total * w) // weight_sum
        shares.append(share)
        allocated += share
    remainder = total - allocated
    # Give leftover minutes to highest-priority item (stable: first in weighted order).
    order = sorted(range(len(items)), key=lambda i: (-weights[i], items[i].get("id") or ""))
    if remainder and order:
        shares[order[0]] += remainder
    for i, it in enumerate(items):
        it["minutes"] = shares[i]
    out["items"] = items
    return out
