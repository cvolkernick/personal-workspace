"""Daily plan quests synced to Google Tasks (Fitness list).

Sync identity lives in a **local cache** (not in GT titles/notes):
  ~/.config/resistance-dashboard/daily_quest_cache.json
  { "day": { "list_id": "...", "ids": { "training|group": "taskId", "training|ex-foo": "..." } } }

Task titles are human-only. Due date = civil day. Notes = optional motivation only.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import gtasks_bridge as gtb
from .timeutil import local_today_iso

DEFAULT_LIST_TITLE = "Fitness"

GROUP_META = {
    "training": {"title": "Training", "order": 1, "emoji": "🏋️"},
    "nutrition": {"title": "Nutrition", "order": 2, "emoji": "🍽"},
    "shopping": {"title": "Shopping", "order": 3, "emoji": "🛒"},
    "sleep": {"title": "Sleep & recovery", "order": 4, "emoji": "😴"},
    "recovery": {"title": "Sleep & recovery", "order": 4, "emoji": "😴"},
    "other": {"title": "Other", "order": 9, "emoji": "✓"},
}


def _slug(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "item")[:limit]


def _cache_path() -> Path:
    override = os.environ.get("RESISTANCE_DASHBOARD_CONFIG_DIR")
    base = Path(override).expanduser() if override else Path.home() / ".config" / "resistance-dashboard"
    return base / "daily_quest_cache.json"


def _load_cache() -> dict:
    path = _cache_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(data: dict) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass


def cache_key(group: str, slug: str) -> str:
    return f"{group}|{slug}"


@dataclass
class PlannedItem:
    group: str
    slug: str
    title: str
    notes_extra: str = ""
    meal_label: str = ""  # e.g. Next meal


@dataclass
class PlannedGroup:
    group: str
    title: str
    emoji: str = "✓"
    items: List[PlannedItem] = field(default_factory=list)


def plan_from_today_board(today: dict, *, day: Optional[str] = None) -> List[PlannedGroup]:
    """Build desired group → leaf items from coach.today board."""
    day = day or str((today or {}).get("date") or local_today_iso())
    groups: Dict[str, PlannedGroup] = {}

    def _g(kind: str) -> PlannedGroup:
        key = "sleep" if kind == "recovery" else kind
        if key not in groups:
            m = GROUP_META.get(key) or GROUP_META["other"]
            groups[key] = PlannedGroup(
                group=key, title=m["title"], emoji=m.get("emoji") or "✓", items=[]
            )
        return groups[key]

    # High-level actions (skip pure nutrition "eat through plan" if we expand meals)
    for i, act in enumerate((today or {}).get("actions") or []):
        if not isinstance(act, dict):
            continue
        kind = str(act.get("kind") or "other").lower()
        text = str(act.get("text") or "").strip()
        if not text:
            continue
        # Prefer meal-bucket leaves over generic "eat through planned meals"
        if kind == "nutrition" and "planned meal" in text.lower():
            continue
        slug = str(act.get("id") or f"action-{kind}-{i}")
        _g(kind).items.append(
            PlannedItem(
                group=("sleep" if kind == "recovery" else kind),
                slug=_slug(slug),
                title=text[:200],
                notes_extra=str(act.get("motivation") or "")[:400],
            )
        )

    # Training exercises
    workout = (today or {}).get("workout") or {}
    if not workout.get("is_rest_day"):
        for ex in workout.get("exercises") or []:
            if not isinstance(ex, dict):
                continue
            name = str(ex.get("name") or "").strip()
            if not name:
                continue
            bits = []
            if ex.get("weight_lbs") is not None:
                bits.append(f"{ex.get('weight_lbs')} lb")
            if ex.get("sets") is not None and ex.get("reps") is not None:
                bits.append(f"{ex.get('sets')}×{ex.get('reps')}")
            detail = f"{name} ({' '.join(bits)})" if bits else name
            _g("training").items.append(
                PlannedItem(
                    group="training",
                    slug=f"ex-{_slug(name)}",
                    title=detail[:200],
                    notes_extra=str(ex.get("rationale") or "")[:400],
                )
            )

    # Nutrition: meal-plan buckets (Next meal / Later / Evening) — not flat day dump
    meal = (today or {}).get("meal") or {}
    meals = list(meal.get("meals") or [])
    if meals:
        for mi, bucket in enumerate(meals):
            if not isinstance(bucket, dict):
                continue
            label = str(bucket.get("label") or f"Meal {mi + 1}").strip()
            items = list(bucket.get("items") or [])
            if not items:
                continue
            for j, it in enumerate(items):
                if not isinstance(it, dict):
                    continue
                name = str(it.get("name") or "").strip()
                if not name:
                    continue
                portion = it.get("serving_label") or (
                    f"{it.get('portion_g')}g" if it.get("portion_g") else ""
                )
                title = f"{label}: {name}"
                if portion:
                    title = f"{label}: {name} · {portion}"
                _g("nutrition").items.append(
                    PlannedItem(
                        group="nutrition",
                        slug=f"meal-{mi}-{_slug(name)}-{j}",
                        title=title[:200],
                        meal_label=label,
                    )
                )
    else:
        # Fallback flat items if meals buckets empty
        for j, it in enumerate(meal.get("items") or []):
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            if not name:
                continue
            portion = it.get("serving_label") or ""
            title = f"Eat: {name}" + (f" · {portion}" if portion else "")
            _g("nutrition").items.append(
                PlannedItem(
                    group="nutrition",
                    slug=f"food-{_slug(name)}-{j}",
                    title=title[:200],
                )
            )

    for k, p in enumerate((today or {}).get("purchases") or []):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        g = _g("shopping")
        if any(name.lower() in it.title.lower() for it in g.items):
            continue
        act = "Restock" if p.get("action") == "restock" else "Get"
        g.items.append(
            PlannedItem(
                group="shopping",
                slug=f"buy-{_slug(name)}-{k}",
                title=f"{act}: {name}"[:200],
                notes_extra=str(p.get("reason") or "")[:400],
            )
        )

    ordered = sorted(
        [g for g in groups.values() if g.items],
        key=lambda g: (GROUP_META.get(g.group) or GROUP_META["other"])["order"],
    )
    return ordered


def _get_task_safe(list_id: str, task_id: str) -> Optional[dict]:
    try:
        gt = gtb.load_google_tasks()
        r = gt.get_task(list_id, task_id)
        if r.get("ok"):
            return r.get("task")
    except Exception:
        return None
    return None


def ensure_daily_tasks(
    today_board: dict,
    *,
    list_title: str = DEFAULT_LIST_TITLE,
    day: Optional[str] = None,
    create_missing: bool = True,
) -> dict:
    """Ensure / refresh quests. Uses local cache; human-only GT titles/notes."""
    day = day or str((today_board or {}).get("date") or local_today_iso())
    planned = plan_from_today_board(today_board or {}, day=day)

    cred = gtb.credentials_status()
    if not cred.get("ok"):
        return _local_payload(
            planned,
            day=day,
            error=cred.get("error") or "Google Tasks not configured",
        )

    try:
        list_id = gtb.resolve_list_id(list_title)
        if not list_id:
            return _local_payload(
                planned, day=day, error=f"Task list '{list_title}' not found"
            )

        cache = _load_cache()
        day_cache = cache.get(day) if isinstance(cache.get(day), dict) else {}
        if day_cache.get("list_id") != list_id:
            day_cache = {"list_id": list_id, "ids": {}}
        ids: Dict[str, str] = dict(day_cache.get("ids") or {})

        groups_out: List[dict] = []

        for g in planned:
            parent_ck = cache_key(g.group, "group")
            parent_id = ids.get(parent_ck)
            parent_task = _get_task_safe(list_id, parent_id) if parent_id else None
            if not parent_task and create_missing:
                created = gtb.create_task(
                    list_id,
                    g.title,  # no date stamp
                    notes="",  # human-empty; mapping is local
                    due=day,
                )
                if created.get("ok") and created.get("task"):
                    parent_task = created["task"]
                    parent_id = str(parent_task.get("id") or "")
                    if parent_id:
                        ids[parent_ck] = parent_id
            elif not parent_task:
                parent_id = None

            items_out: List[dict] = []
            for it in g.items:
                ck = cache_key(g.group, it.slug)
                tid = ids.get(ck)
                task = _get_task_safe(list_id, tid) if tid else None
                if not task and create_missing:
                    notes = (it.notes_extra or "").strip()
                    created = gtb.create_task(
                        list_id,
                        it.title,
                        notes=notes,
                        due=day,
                        parent=str(parent_id) if parent_id else None,
                    )
                    if created.get("ok") and created.get("task"):
                        task = created["task"]
                        tid = str(task.get("id") or "")
                        if tid:
                            ids[ck] = tid
                if not task:
                    items_out.append(
                        {
                            "slug": it.slug,
                            "title": it.title,
                            "completed": False,
                            "task_id": tid,
                            "list_id": list_id if tid else None,
                            "group": g.group,
                            "meal_label": it.meal_label or None,
                        }
                    )
                    continue
                completed = str(task.get("status") or "") == "completed"
                items_out.append(
                    {
                        "slug": it.slug,
                        "title": task.get("title") or it.title,
                        "completed": completed,
                        "task_id": task.get("id"),
                        "list_id": list_id,
                        "group": g.group,
                        "meal_label": it.meal_label or None,
                    }
                )

            all_done = bool(items_out) and all(x["completed"] for x in items_out)
            parent_completed = (
                parent_task is not None
                and str(parent_task.get("status") or "") == "completed"
            )
            if parent_id and create_missing:
                if all_done and not parent_completed:
                    gtb.complete_task(list_id, str(parent_id), completed=True)
                    parent_completed = True
                elif not all_done and parent_completed:
                    gtb.complete_task(list_id, str(parent_id), completed=False)
                    parent_completed = False

            done_n = sum(1 for x in items_out if x["completed"])
            # Nest open items under meal_label for UI
            items_out_open = [x for x in items_out if not x["completed"]]
            groups_out.append(
                {
                    "group": g.group,
                    "title": g.title,
                    "emoji": g.emoji,
                    "task_id": parent_id,
                    "list_id": list_id,
                    "completed": parent_completed or (bool(items_out) and done_n == len(items_out)),
                    "done": done_n,
                    "total": len(items_out),
                    "items": items_out,
                    "open_items": items_out_open,
                }
            )

        day_cache = {"list_id": list_id, "ids": ids}
        cache[day] = day_cache
        # prune old days (keep last 14)
        if len(cache) > 16:
            keys = sorted(k for k in cache.keys() if re.match(r"\d{4}-\d{2}-\d{2}", k))
            for old in keys[:-14]:
                cache.pop(old, None)
        _save_cache(cache)

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


def plan_preview(today_board: dict, *, day: Optional[str] = None) -> dict:
    """Fast local structure for UI skeleton before GT ensure finishes."""
    day = day or str((today_board or {}).get("date") or local_today_iso())
    planned = plan_from_today_board(today_board or {}, day=day)
    return _local_payload(planned, day=day, error=None, source="plan_preview")


def _local_payload(
    planned: List[PlannedGroup],
    *,
    day: str,
    error: Optional[str] = None,
    source: str = "local_preview",
) -> dict:
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
                "meal_label": it.meal_label or None,
                "local": True,
            }
            for it in g.items
        ]
        groups_out.append(
            {
                "group": g.group,
                "title": g.title,
                "emoji": g.emoji,
                "task_id": None,
                "list_id": None,
                "completed": False,
                "done": 0,
                "total": len(items),
                "items": items,
                "open_items": items,
            }
        )
    total = sum(g["total"] for g in groups_out)
    return {
        "ok": error is None,
        "source": source,
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
