"""Daily plan quests synced to Google Tasks (Fitness list).

Sync identity:
  * Durable marker in notes: ``[fitdash-quest:YYYY-MM-DD]`` (titles stay human-only).
  * Local cache when the filesystem persists (Pi). Vercel is ephemeral — do not
    key rollover on cache alone.
  * Known group headers (Training / Nutrition / Shopping / Sleep & recovery)
    and their children, so unmarked user-OAuth leftovers can still be swept.

  ~/.config/resistance-dashboard/daily_quest_cache.json
  { "day": { "list_id": "...", "ids": { "training|group": "taskId", "training|ex-foo": "..." } } }

Due date = civil day. Notes = optional motivation + the FitDash marker.
Chris jots, Turo, and Orchestra NOW/NEXT that are not FitDash quests are
never deleted.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import gtasks_bridge as gtb
from .timeutil import local_today_iso

DEFAULT_LIST_TITLE = "Fitness"
QUEST_MARK_RE = re.compile(r"\[fitdash-quest:(\d{4}-\d{2}-\d{2})\]")

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


def group_header_titles() -> set:
    return {
        str(m.get("title") or "").strip()
        for m in GROUP_META.values()
        if str(m.get("title") or "").strip()
    }


def quest_marker(day: str) -> str:
    return f"[fitdash-quest:{str(day or '')[:10]}]"


def quest_mark_day(notes: str) -> Optional[str]:
    match = QUEST_MARK_RE.search(notes or "")
    if not match:
        return None
    day = match.group(1)
    return day if _is_day_key(day) else None


def quest_notes(motivation: str, day: str) -> str:
    """Human motivation (optional) plus the durable FitDash marker."""
    mark = quest_marker(day)
    extra = QUEST_MARK_RE.sub("", motivation or "").strip()
    if extra:
        return f"{extra}\n\n{mark}"
    return mark


def _is_incomplete(task: dict) -> bool:
    return str((task or {}).get("status") or "") != "completed"


def _quest_civil_day(task: dict) -> str:
    """Civil day this quest belongs to: marker first, then due."""
    marked = quest_mark_day((task or {}).get("notes") or "")
    if marked:
        return marked
    return _task_due_day(task)


def collect_fitdash_quest_ids(tasks: Sequence[dict]) -> set:
    """Ids FitDash wrote: marker, known group header, or child of those.

    Unmarked user-OAuth leftovers are still identifiable when they sit under
    Training / Nutrition / Shopping / Sleep & recovery. Top-level jots and
    other lists are not included.
    """
    headers = group_header_titles()
    quest_ids: set = set()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id") or "")
        if not tid:
            continue
        if quest_mark_day(task.get("notes") or ""):
            quest_ids.add(tid)
        if (task.get("title") or "").strip() in headers:
            quest_ids.add(tid)
    changed = True
    while changed:
        changed = False
        for task in tasks:
            if not isinstance(task, dict):
                continue
            tid = str(task.get("id") or "")
            parent = str(task.get("parent") or "")
            if tid and parent and parent in quest_ids and tid not in quest_ids:
                quest_ids.add(tid)
                changed = True
    return quest_ids


def _belonging_day(task: dict, by_id: Dict[str, dict]) -> str:
    day = _quest_civil_day(task)
    if day:
        return day
    parent = by_id.get(str((task or {}).get("parent") or ""))
    if parent:
        return _quest_civil_day(parent)
    return ""


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
            clock = str(bucket.get("eat_at_label") or "").strip()
            if not clock and bucket.get("eat_at"):
                try:
                    raw_eat = str(bucket.get("eat_at") or "")
                    if raw_eat.endswith("Z"):
                        raw_eat = raw_eat[:-1] + "+00:00"
                    eat_dt = datetime.fromisoformat(raw_eat)
                    h24 = eat_dt.hour
                    h = h24 % 12 or 12
                    clock = f"{h}:{eat_dt.minute:02d} {'AM' if h24 < 12 else 'PM'}"
                except ValueError:
                    clock = ""
            if clock:
                label = f"{label} · {clock}"
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
        r = gtb.get_task(list_id, task_id)
        if r.get("ok"):
            return r.get("task")
    except Exception:
        return None
    return None


def _task_due_day(task: dict) -> str:
    return str((task or {}).get("due") or "")[:10]


def _hydrate_ids_from_listed(
    ids: Dict[str, str],
    planned: List[PlannedGroup],
    listed: dict,
    day: str,
) -> Dict[str, str]:
    """Reuse existing Fitness tasks when the local cache is empty (Vercel)."""
    tasks = [t for t in (listed.get("tasks") or []) if isinstance(t, dict)]
    unused = list(tasks)
    used_ids = {str(v) for v in (ids or {}).values() if v}

    def take(title: str, parent_id: Optional[str] = None) -> Optional[dict]:
        want = (title or "").strip()
        for i, t in enumerate(unused):
            tid = str(t.get("id") or "")
            if tid and tid in used_ids:
                continue
            if (t.get("title") or "").strip() != want:
                continue
            due = _task_due_day(t)
            if due and due != day:
                continue
            if parent_id and str(t.get("parent") or "") not in ("", parent_id):
                continue
            unused.pop(i)
            return t
        return None

    out = dict(ids)
    for g in planned:
        parent_ck = cache_key(g.group, "group")
        parent_id = out.get(parent_ck)
        if not parent_id:
            found = take(g.title)
            if found and found.get("id"):
                parent_id = str(found["id"])
                out[parent_ck] = parent_id
                used_ids.add(parent_id)
        for it in g.items:
            ck = cache_key(g.group, it.slug)
            if out.get(ck):
                continue
            found = take(it.title, parent_id=parent_id)
            if found and found.get("id"):
                out[ck] = str(found["id"])
                used_ids.add(str(found["id"]))
    return out


def _is_day_key(key: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", str(key or "")))


def _delete_order(ids: Dict[str, str]) -> List[Tuple[str, str]]:
    """Children first, group parents last (so leaves are gone before headers)."""
    items = [(str(ck), str(tid)) for ck, tid in (ids or {}).items() if ck and tid]

    def sort_key(item: Tuple[str, str]) -> Tuple[int, str]:
        ck, _tid = item
        is_parent = ck.endswith("|group")
        return (1 if is_parent else 0, ck)

    return sorted(items, key=sort_key)


def purge_stale_quest_tasks(
    *,
    list_id: str,
    today: str,
    cache: Optional[dict] = None,
    save: bool = True,
) -> Dict[str, Any]:
    """Delete yesterday's incomplete FitDash quests; leave everything else.

    User-OAuth writes on Vercel have no durable local cache. Identity is the
    notes marker, known group headers + children, then cache ids when present.
    Completed prior-day quests are left completed (never uncompleted).
    Non-FitDash tasks on this list (jots) and every other list are untouched.

    Returns stats: ``{days_purged, deleted, orphan_deleted, failed, errors}``.
    """
    today = str(today or "")[:10]
    if cache is None:
        cache = _load_cache()
    if not list_id or not today or not _is_day_key(today):
        return {
            "days_purged": [],
            "deleted": 0,
            "orphan_deleted": 0,
            "failed": 0,
            "errors": [],
            "ok": False,
            "error": "missing list_id or today",
        }

    stale_days = sorted(
        k
        for k in list(cache.keys())
        if _is_day_key(k) and k != today and isinstance(cache.get(k), dict)
    )
    deleted = 0
    failed = 0
    errors: List[str] = []
    days_purged: List[str] = []
    already: set = set()
    headers = group_header_titles()

    def _delete(target_list: str, tid: str, label: str) -> bool:
        nonlocal deleted, failed
        try:
            result = gtb.delete_task(target_list, tid)
            already.add(tid)
            if result.get("ok"):
                deleted += 1
                return True
            failed += 1
            errors.append(f"{label}: {result.get('error') or 'delete failed'}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            errors.append(f"{label}: {e}")
        return False

    for stale in stale_days:
        entry = cache.get(stale) or {}
        entry_list = str(entry.get("list_id") or list_id)
        ids = dict(entry.get("ids") or {})
        for ck, tid in _delete_order(ids):
            if tid in already:
                continue
            target_list = entry_list or list_id
            task = _get_task_safe(target_list, tid)
            if task and not _is_incomplete(task):
                already.add(tid)
                continue
            _delete(target_list, tid, f"{stale}/{ck}")
        cache.pop(stale, None)
        days_purged.append(stale)

    # Vercel / empty-cache safety net: identify FitDash quests on this list
    # and drop incomplete ones that belong to a prior civil day.
    orphan_deleted = 0
    try:
        listed = gtb.list_tasks(list_id, show_completed=True, show_hidden=True)
        if listed.get("ok"):
            tasks = [t for t in (listed.get("tasks") or []) if isinstance(t, dict)]
            quest_ids = collect_fitdash_quest_ids(tasks)
            by_id = {str(t.get("id")): t for t in tasks if t.get("id")}
            stale_tasks = []
            for task in tasks:
                tid = str(task.get("id") or "")
                if not tid or tid not in quest_ids or tid in already:
                    continue
                if not _is_incomplete(task):
                    continue
                day = _belonging_day(task, by_id)
                if not day or not _is_day_key(day) or day >= today:
                    continue
                stale_tasks.append(task)
            stale_tasks.sort(
                key=lambda t: (
                    1 if (t.get("title") or "").strip() in headers else 0,
                    str(t.get("id") or ""),
                )
            )
            for task in stale_tasks:
                tid = str(task.get("id") or "")
                if tid in already:
                    continue
                if _delete(list_id, tid, f"orphan/{tid}"):
                    orphan_deleted += 1
    except Exception as e:  # noqa: BLE001
        errors.append(f"orphan_sweep: {e}")

    if save and (days_purged or orphan_deleted):
        _save_cache(cache)

    return {
        "ok": True,
        "days_purged": days_purged,
        "deleted": deleted,
        "orphan_deleted": orphan_deleted,
        "failed": failed,
        "errors": errors[:20],
    }


def ensure_daily_tasks(
    today_board: dict,
    *,
    list_title: str = DEFAULT_LIST_TITLE,
    day: Optional[str] = None,
    create_missing: bool = True,
) -> dict:
    """Ensure / refresh quests. Titles stay human-only; notes carry the marker.

    On each ensure for civil day D: remove incomplete FitDash quests that
    belong to any day other than D, then create/refresh today's groups and
    leaves. Completed prior-day quests are left completed.
    """
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
        # Day rollover cleanup — must run before creating today's tasks
        purge_stats = purge_stale_quest_tasks(
            list_id=list_id, today=day, cache=cache, save=True
        )

        day_cache = cache.get(day) if isinstance(cache.get(day), dict) else {}
        if day_cache.get("list_id") != list_id:
            day_cache = {"list_id": list_id, "ids": {}}
        ids: Dict[str, str] = dict(day_cache.get("ids") or {})
        try:
            listed = gtb.list_tasks(
                list_id, show_completed=True, show_hidden=True
            )
        except Exception:
            listed = None
        if listed and listed.get("ok"):
            ids = _hydrate_ids_from_listed(ids, planned, listed, day)

        groups_out: List[dict] = []

        for g in planned:
            parent_ck = cache_key(g.group, "group")
            parent_id = ids.get(parent_ck)
            parent_task = _get_task_safe(list_id, parent_id) if parent_id else None
            if not parent_task and create_missing:
                created = gtb.create_task(
                    list_id,
                    g.title,  # no date stamp in the title
                    notes=quest_notes("", day),
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
                    notes = quest_notes(it.notes_extra or "", day)
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
            "purge": purge_stats,
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
