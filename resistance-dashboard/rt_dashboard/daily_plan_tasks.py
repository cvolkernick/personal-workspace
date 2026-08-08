"""Daily plan quests: group + leaf tasks synced to Google Tasks (Fitness list).

Machine notes format (stable sync key):
  fitdash:v1 day=YYYY-MM-DD group=training slug=session

Groups: training, nutrition, shopping, sleep (and other action kinds).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import gtasks_bridge as gtb
from .timeutil import local_today_iso

NOTE_PREFIX = "fitdash:v1"
DEFAULT_LIST_TITLE = "Fitness"

GROUP_META = {
    "training": {"title": "Training", "order": 1},
    "nutrition": {"title": "Nutrition", "order": 2},
    "shopping": {"title": "Shopping", "order": 3},
    "sleep": {"title": "Sleep & recovery", "order": 4},
    "recovery": {"title": "Sleep & recovery", "order": 4},
    "other": {"title": "Other", "order": 9},
}


def _slug(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "item")[:limit]


def make_notes(*, day: str, group: str, slug: str) -> str:
    return f"{NOTE_PREFIX} day={day} group={group} slug={slug}"


def parse_notes(notes: str) -> Optional[Dict[str, str]]:
    if not notes or NOTE_PREFIX not in notes:
        return None
    out: Dict[str, str] = {}
    for part in notes.replace("\n", " ").split():
        if "=" in part:
            k, _, v = part.partition("=")
            if k in ("day", "group", "slug"):
                out[k] = v
    if out.get("day") and out.get("group") and out.get("slug"):
        return out
    return None


@dataclass
class PlannedItem:
    group: str
    slug: str
    title: str
    notes_extra: str = ""


@dataclass
class PlannedGroup:
    group: str
    title: str
    items: List[PlannedItem] = field(default_factory=list)


def plan_from_today_board(today: dict, *, day: Optional[str] = None) -> List[PlannedGroup]:
    """Build desired group → leaf items from coach.today board."""
    day = day or str((today or {}).get("date") or local_today_iso())
    groups: Dict[str, PlannedGroup] = {}

    def _g(kind: str) -> PlannedGroup:
        meta = GROUP_META.get(kind) or GROUP_META["other"]
        # Map recovery into sleep group
        key = "sleep" if kind == "recovery" else kind
        if key not in groups:
            m = GROUP_META.get(key) or GROUP_META["other"]
            groups[key] = PlannedGroup(group=key, title=m["title"], items=[])
        return groups[key]

    # High-level actions
    for i, act in enumerate((today or {}).get("actions") or []):
        if not isinstance(act, dict):
            continue
        kind = str(act.get("kind") or "other").lower()
        text = str(act.get("text") or "").strip()
        if not text:
            continue
        slug = str(act.get("id") or f"action-{kind}-{i}")
        _g(kind).items.append(
            PlannedItem(
                group=("sleep" if kind == "recovery" else kind),
                slug=_slug(slug),
                title=text[:200],
                notes_extra=str(act.get("motivation") or "")[:500],
            )
        )

    # Training exercises as leaf quests
    workout = (today or {}).get("workout") or {}
    if not workout.get("is_rest_day"):
        for ex in workout.get("exercises") or []:
            if not isinstance(ex, dict):
                continue
            name = str(ex.get("name") or "").strip()
            if not name:
                continue
            sets = ex.get("sets")
            reps = ex.get("reps")
            w = ex.get("weight_lbs")
            detail = name
            bits = []
            if w is not None:
                bits.append(f"{w} lb")
            if sets is not None and reps is not None:
                bits.append(f"{sets}×{reps}")
            if bits:
                detail = f"{name} ({' '.join(bits)})"
            _g("training").items.append(
                PlannedItem(
                    group="training",
                    slug=f"ex-{_slug(name)}",
                    title=f"Lift: {detail}"[:200],
                    notes_extra=str(ex.get("rationale") or "")[:500],
                )
            )

    # Meal plan items
    meal = (today or {}).get("meal") or {}
    for j, it in enumerate(meal.get("items") or []):
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        portion = it.get("serving_label") or it.get("portion_g") or ""
        title = f"Eat: {name}"
        if portion:
            title = f"Eat: {name} · {portion}"
        _g("nutrition").items.append(
            PlannedItem(
                group="nutrition",
                slug=f"food-{_slug(name)}-{j}",
                title=title[:200],
                notes_extra="",
            )
        )

    # Shopping rows (beyond top action)
    for k, p in enumerate((today or {}).get("purchases") or []):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        # skip if already covered by first shopping action with same name
        g = _g("shopping")
        if any(name.lower() in it.title.lower() for it in g.items):
            continue
        act = "Restock" if p.get("action") == "restock" else "Get"
        _g("shopping").items.append(
            PlannedItem(
                group="shopping",
                slug=f"buy-{_slug(name)}-{k}",
                title=f"{act}: {name}"[:200],
                notes_extra=str(p.get("reason") or "")[:500],
            )
        )

    # Drop empty groups; sort
    ordered = sorted(
        [g for g in groups.values() if g.items],
        key=lambda g: (GROUP_META.get(g.group) or GROUP_META["other"])["order"],
    )
    return ordered


def _index_existing(tasks: Sequence[dict]) -> Dict[Tuple[str, str, str], dict]:
    """Map (day, group, slug) → task dict."""
    out: Dict[Tuple[str, str, str], dict] = {}
    for t in tasks or []:
        meta = parse_notes(str(t.get("notes") or ""))
        if not meta:
            continue
        key = (meta["day"], meta["group"], meta["slug"])
        out[key] = t
    return out


def ensure_daily_tasks(
    today_board: dict,
    *,
    list_title: str = DEFAULT_LIST_TITLE,
    day: Optional[str] = None,
) -> dict:
    """Ensure Google Tasks exist for today's plan; return UI payload.

    Does not re-create completed items. Creates missing group parents + leaves.
    Auto-completes a group parent when all children are completed.
    """
    day = day or str((today_board or {}).get("date") or local_today_iso())
    planned = plan_from_today_board(today_board or {}, day=day)

    cred = gtb.credentials_status()
    if not cred.get("ok"):
        # Local-only preview (no GT)
        return _local_payload(planned, day=day, error=cred.get("error") or "Google Tasks not configured")

    try:
        list_id = gtb.resolve_list_id(list_title)
        if not list_id:
            return _local_payload(
                planned,
                day=day,
                error=f"Task list '{list_title}' not found",
            )

        listed = gtb.list_tasks(list_id, show_completed=True, show_hidden=True)
        if not listed.get("ok"):
            return _local_payload(
                planned, day=day, error=listed.get("error") or "list_tasks failed"
            )

        existing = _index_existing(listed.get("tasks") or [])
        groups_out: List[dict] = []

        for g in planned:
            parent_slug = "group"
            parent_key = (day, g.group, parent_slug)
            parent_task = existing.get(parent_key)
            if not parent_task:
                notes = make_notes(day=day, group=g.group, slug=parent_slug)
                notes = f"{notes}\nFitDash daily group · {day}"
                created = gtb.create_task(
                    list_id,
                    f"[{day}] {g.title}",
                    notes=notes,
                    due=day,
                )
                if created.get("ok"):
                    parent_task = created.get("task") or {}
                    existing[parent_key] = parent_task
                else:
                    parent_task = {"id": None, "status": "needsAction"}

            parent_id = parent_task.get("id")
            items_out: List[dict] = []
            for it in g.items:
                key = (day, g.group, it.slug)
                task = existing.get(key)
                if not task:
                    notes = make_notes(day=day, group=g.group, slug=it.slug)
                    if it.notes_extra:
                        notes = f"{notes}\n{it.notes_extra}"
                    created = gtb.create_task(
                        list_id,
                        it.title,
                        notes=notes,
                        due=day,
                        parent=str(parent_id) if parent_id else None,
                    )
                    if created.get("ok"):
                        task = created.get("task") or {}
                        existing[key] = task
                    else:
                        task = {
                            "id": None,
                            "title": it.title,
                            "status": "needsAction",
                        }
                completed = str(task.get("status") or "") == "completed"
                items_out.append(
                    {
                        "slug": it.slug,
                        "title": task.get("title") or it.title,
                        "completed": completed,
                        "task_id": task.get("id"),
                        "list_id": list_id,
                        "group": g.group,
                    }
                )

            # Parent complete when all children done
            all_done = bool(items_out) and all(x["completed"] for x in items_out)
            parent_completed = str(parent_task.get("status") or "") == "completed"
            if all_done and not parent_completed and parent_id:
                gtb.complete_task(list_id, str(parent_id), completed=True)
                parent_completed = True
            elif not all_done and parent_completed and parent_id:
                gtb.complete_task(list_id, str(parent_id), completed=False)
                parent_completed = False

            done_n = sum(1 for x in items_out if x["completed"])
            groups_out.append(
                {
                    "group": g.group,
                    "title": g.title,
                    "task_id": parent_id,
                    "list_id": list_id,
                    "completed": parent_completed,
                    "done": done_n,
                    "total": len(items_out),
                    "items": items_out,
                }
            )

        total = sum(g["total"] for g in groups_out)
        done = sum(g["done"] for g in groups_out)
        return {
            "ok": True,
            "source": "google_tasks",
            "list_title": list_title,
            "list_id": list_id,
            "day": day,
            "groups": groups_out,
            "summary": {"done": done, "total": total},
            "error": None,
        }
    except Exception as e:
        return _local_payload(planned, day=day, error=str(e))


def _local_payload(
    planned: List[PlannedGroup], *, day: str, error: Optional[str] = None
) -> dict:
    """Offline preview structure (no task_ids) when Google Tasks unavailable."""
    groups_out = []
    for g in planned:
        items = [
            {
                "slug": it.slug,
                "title": it.title,
                "completed": False,
                "task_id": None,
                "list_id": None,
                "group": g.group,
                "local": True,
            }
            for it in g.items
        ]
        groups_out.append(
            {
                "group": g.group,
                "title": g.title,
                "task_id": None,
                "list_id": None,
                "completed": False,
                "done": 0,
                "total": len(items),
                "items": items,
            }
        )
    total = sum(g["total"] for g in groups_out)
    return {
        "ok": error is None,
        "source": "local_preview",
        "list_title": DEFAULT_LIST_TITLE,
        "list_id": None,
        "day": day,
        "groups": groups_out,
        "summary": {"done": 0, "total": total},
        "error": error,
    }


def complete_leaf(
    list_id: str,
    task_id: str,
    *,
    completed: bool = True,
    parent_id: Optional[str] = None,
    sibling_all_done: Optional[bool] = None,
) -> dict:
    """Complete/uncomplete a leaf; optionally roll up parent."""
    if not list_id or not task_id:
        return {"ok": False, "error": "missing list_id or task_id"}
    try:
        result = gtb.complete_task(list_id, task_id, completed=completed)
        if not result.get("ok"):
            return result
        if parent_id and sibling_all_done is not None:
            gtb.complete_task(list_id, parent_id, completed=bool(sibling_all_done))
        return {"ok": True, "task": result.get("task"), "parent_id": parent_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}
