"""Bridge: Workflow backlog ↔ Holistic time allocator.

Macro work (ops/backlog) can be pushed into day-level tasks (holistic/data/tasks.json)
without merging UIs. Orchestra surfaces the linked view.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from backlog import get_item as get_backlog_item  # noqa: E402
from backlog import list_items as list_backlog_items  # noqa: E402
from backlog import load_backlog, save_backlog  # noqa: E402

# Priority label → allocator integer (higher = more important)
_PRI_TO_INT = {"critical": 9, "high": 7, "medium": 5, "low": 3}
_PRI_TO_MINUTES = {"critical": 90, "high": 60, "medium": 45, "low": 30}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def backlog_priority_to_int(priority: str) -> int:
    return _PRI_TO_INT.get((priority or "medium").lower(), 5)


def default_minutes_for_priority(priority: str) -> int:
    return _PRI_TO_MINUTES.get((priority or "medium").lower(), 45)


def _load_allocator_modules():
    from holistic.time_allocator.domain import (  # noqa: WPS433
        add_item,
        build_rolling_plan,
        get_item_by_backlog_id,
        list_items,
    )
    from holistic.time_allocator.store import load_state, save_state  # noqa: WPS433

    return add_item, build_rolling_plan, get_item_by_backlog_id, list_items, load_state, save_state


def list_bridge_status() -> dict[str, Any]:
    """Candidates from backlog + already-linked allocator tasks."""
    (
        _add_item,
        _build_plan,
        get_item_by_backlog_id,
        list_items,
        load_state,
        _save,
    ) = _load_allocator_modules()

    backlog = list_backlog_items(include_done=False, ranked=True)
    state = load_state()
    alloc_items = list_items(state)
    linked = [it for it in alloc_items if it.get("backlog_id")]
    linked_ids = {str(it.get("backlog_id")) for it in linked}

    candidates = []
    for it in backlog:
        st = (it.get("status") or "").lower()
        if st in ("done", "parked"):
            continue
        slot = (it.get("schedule_slot") or "").lower()
        rank = it.get("press_rank") or 99
        # Prefer "now" / this_week / top press ranks
        if slot not in ("now", "this_week") and rank > 3 and st not in ("planning", "active", "ready"):
            continue
        bid = str(it.get("id") or "")
        candidates.append(
            {
                "backlog_id": bid,
                "title": it.get("title"),
                "priority": it.get("priority"),
                "status": it.get("status"),
                "press_rank": it.get("press_rank"),
                "schedule_slot": it.get("schedule_slot"),
                "schedule_label": it.get("schedule_label"),
                "area": it.get("area"),
                "already_linked": bid in linked_ids,
                "allocator_id": next(
                    (x.get("id") for x in linked if str(x.get("backlog_id")) == bid),
                    None,
                ),
            }
        )

    return {
        "ok": True,
        "candidates": candidates[:12],
        "linked": [
            {
                "allocator_id": it.get("id"),
                "backlog_id": it.get("backlog_id"),
                "title": it.get("title"),
                "priority": it.get("priority"),
                "minutes": it.get("minutes"),
                "notes": it.get("notes"),
            }
            for it in linked
        ],
        "allocator_url": "http://127.0.0.1:8770/",
        "workflow_url": "http://127.0.0.1:8765/",
        "note": (
            "Send ranked backlog work into the day planner without merging UIs. "
            "Orchestra shows the same bridge strip."
        ),
    }


def send_backlog_to_allocator(
    backlog_id: str,
    *,
    minutes: Optional[int] = None,
    priority: Optional[int] = None,
    rebuild_plan: bool = True,
) -> dict[str, Any]:
    """Create (or return existing) time-allocator task linked to a backlog item."""
    (
        add_item,
        build_rolling_plan,
        get_item_by_backlog_id,
        list_items,
        load_state,
        save_state,
    ) = _load_allocator_modules()

    bl = get_backlog_item(backlog_id)
    if not bl:
        return {"ok": False, "error": f"backlog item not found: {backlog_id}"}

    state = load_state()
    existing = get_item_by_backlog_id(state, backlog_id)
    if existing:
        return {
            "ok": True,
            "created": False,
            "already_linked": True,
            "item": existing,
            "message": f"Already on today's list: {existing.get('title')}",
            "allocator_url": "http://127.0.0.1:8770/",
        }

    pri_label = bl.get("priority") or "medium"
    pri_int = priority if priority is not None else backlog_priority_to_int(str(pri_label))
    mins = minutes if minutes is not None else default_minutes_for_priority(str(pri_label))
    title = (bl.get("title") or "Backlog work").strip()
    notes_parts = []
    if bl.get("mvp_scope"):
        notes_parts.append(f"MVP: {bl['mvp_scope']}")
    if bl.get("notes"):
        notes_parts.append(str(bl["notes"]))
    notes_parts.append(f"From backlog {backlog_id[:8]} · open Workflow to Initiate goal")
    notes = " · ".join(notes_parts)

    try:
        state = add_item(
            state,
            title,
            kind="goal" if (bl.get("status") or "") in ("planning", "active", "ready") else "task",
            priority=pri_int,
            minutes=int(mins),
            notes=notes,
            source="workflow-backlog",
            backlog_id=str(bl["id"]),
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if rebuild_plan:
        try:
            state["plan"] = build_rolling_plan(state)
        except Exception:
            state["plan"] = None
    else:
        state["plan"] = None

    save_state(state)
    created = get_item_by_backlog_id(state, backlog_id)

    # Stamp backlog item with link (optional metadata)
    data = load_backlog()
    for it in data.get("items") or []:
        if it.get("id") == bl["id"]:
            it["allocator_id"] = created.get("id") if created else None
            it["allocator_linked_at"] = _now()
            it["updated_at"] = _now()
            break
    save_backlog(data)

    return {
        "ok": True,
        "created": True,
        "already_linked": False,
        "item": created,
        "backlog_id": bl["id"],
        "message": f"Sent to time allocator: {title} ({mins}m, priority {pri_int})",
        "allocator_url": "http://127.0.0.1:8770/",
    }


def send_top_to_allocator(*, limit: int = 1) -> dict[str, Any]:
    """Send top press-ranked unlinked backlog item(s) scheduled now/this_week."""
    status = list_bridge_status()
    sent = []
    errors = []
    for c in status.get("candidates") or []:
        if c.get("already_linked"):
            continue
        if len(sent) >= max(1, int(limit)):
            break
        r = send_backlog_to_allocator(str(c["backlog_id"]))
        if r.get("ok") and r.get("created"):
            sent.append(r)
        elif not r.get("ok"):
            errors.append(r)
    return {
        "ok": True,
        "sent": sent,
        "count": len(sent),
        "errors": errors,
        "message": f"Sent {len(sent)} backlog item(s) to time allocator",
    }


if __name__ == "__main__":
    import json

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(list_bridge_status(), indent=2))
    elif cmd == "send" and len(sys.argv) > 2:
        print(json.dumps(send_backlog_to_allocator(sys.argv[2]), indent=2))
    elif cmd == "send-top":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        print(json.dumps(send_top_to_allocator(limit=n), indent=2))
    else:
        print("Usage: bridge.py [status|send <backlog_id>|send-top [n]]", file=sys.stderr)
        raise SystemExit(2)
