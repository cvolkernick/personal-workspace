"""Google Tasks write-back and today-window listing for Orchestra NOW/NEXT.

One central task bucket: Google Tasks. No Orchestra task store. No Time
Allocator task list — schedule blocks without a GT id stay uncheckable.
The Turo *list* is a GT sub-list: open tasks in today's window may appear
on NOW/NEXT. Invoice-ready standing line stays on Auto Fleet — no Orchestra
inbox or Fleet embed.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

GT_SOURCE = "google_tasks"
GT_SOURCE_ALIASES = frozenset({"google_tasks", "fitdash"})
GT_ID_KEYS = ("gt_task_id", "google_task_id", "gt_id")
LIST_ID_KEYS = ("list_id", "gt_list_id", "google_list_id")
LIST_TITLE_KEYS = ("list_title", "gt_list_title", "google_list_title")
TURO_LIST_TITLE = "Turo"
CIVIL_TZ = ZoneInfo("America/New_York")
_DUE_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

_ORCH = Path(__file__).resolve().parent
_ROOT = _ORCH.parent


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        raw = item.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return ""


def writable_source(item: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Honest GT handle. None unless a real Google Tasks id is present."""
    if not isinstance(item, dict):
        return None
    source = str(item.get("source") or "").strip()
    source_id = str(item.get("source_id") or "").strip()
    task_id = _first_text(item, GT_ID_KEYS)
    if source in GT_SOURCE_ALIASES and source_id:
        task_id = task_id or source_id
    list_id = _first_text(item, LIST_ID_KEYS)
    list_title = _first_text(item, LIST_TITLE_KEYS)
    if not task_id:
        return None
    if not list_id and not list_title:
        return None
    out: dict[str, Any] = {
        "source": GT_SOURCE,
        "source_id": task_id[:80],
        "checkable": True,
    }
    if list_id:
        out["list_id"] = list_id
    if list_title:
        out["list_title"] = list_title
    return out


def is_checkable(item: Optional[dict[str, Any]]) -> bool:
    ref = writable_source(item)
    return bool(ref and ref.get("checkable"))


def _title_key(value: Any) -> str:
    return str(value or "").strip().lower()


def gt_task_index(*blobs: Any) -> dict[str, dict[str, Any]]:
    """Title → GT handle from collected quests that already have a GT id."""
    index: dict[str, dict[str, Any]] = {}
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        for key in ("quests", "tasks"):
            rows = blob.get(key)
            if not isinstance(rows, list):
                continue
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                probe = dict(raw)
                if not probe.get("source"):
                    probe["source"] = GT_SOURCE
                ref = writable_source(probe)
                title = _title_key(raw.get("title") or raw.get("action"))
                if ref and title:
                    index[title] = ref
    return index


def _civil_today(now: Optional[datetime] = None) -> date:
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return ref.astimezone(CIVIL_TZ).date()


def due_date(task: Optional[dict[str, Any]]) -> Optional[date]:
    """GT due is a calendar date (usually YYYY-MM-DDT00:00:00.000Z)."""
    if not isinstance(task, dict):
        return None
    raw = task.get("due")
    if raw is None or raw is False:
        return None
    match = _DUE_DATE_RE.match(str(raw).strip())
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def task_in_now_window(
    task: Optional[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Open task due today, overdue, or undated. Future-dated stays off."""
    if not isinstance(task, dict):
        return False
    if str(task.get("status") or "needsAction") == "completed":
        return False
    if task.get("deleted") or task.get("hidden"):
        return False
    when = due_date(task)
    if when is None:
        return True
    return when <= _civil_today(now)


def gt_task_to_candidate(task: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """NOW/NEXT row from a Google Task. No GT id → None (uncheckable, do not invent)."""
    if not isinstance(task, dict):
        return None
    title = str(task.get("title") or task.get("action") or "").strip()
    task_id = _first_text(task, GT_ID_KEYS) or str(task.get("id") or "").strip()
    list_id = _first_text(task, LIST_ID_KEYS)
    list_title = _first_text(task, LIST_TITLE_KEYS)
    if not title or not task_id or (not list_id and not list_title):
        return None
    row: dict[str, Any] = {
        "id": task_id[:80],
        "title": title,
        "why": "",
        "severity": "info",
        "kind": "task",
        "source": GT_SOURCE,
        "source_id": task_id[:80],
        "gt_task_id": task_id[:80],
    }
    if list_id:
        row["list_id"] = list_id
    if list_title:
        row["list_title"] = list_title
    raw_due = task.get("due")
    if raw_due:
        row["due"] = raw_due
        row["at"] = raw_due
    return row


def window_gt_candidates(
    tasks: Optional[list[Any]],
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Chris-actionable open GT tasks in today's window. Pure."""
    try:
        from pulse import keep_action_item
    except ImportError:
        from .pulse import keep_action_item

    out: list[dict[str, Any]] = []
    for raw in tasks or []:
        if not task_in_now_window(raw, now=now):
            continue
        cand = gt_task_to_candidate(raw)
        if cand and keep_action_item(cand):
            out.append(cand)
    return out


def list_window_gt_tasks(
    *,
    list_title: str = TURO_LIST_TITLE,
    workspace: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Read one GT list. Missing list / creds → empty (honest). Does not create."""
    ws = Path(workspace) if workspace is not None else _ROOT
    title = (list_title or "").strip()
    if not title:
        return []
    try:
        gt = _load_google_tasks(ws)
        list_id = ""
        lists = gt.list_tasklists()
        if lists.get("ok"):
            want = title.lower()
            for tl in lists.get("lists") or []:
                if str(tl.get("title") or "").strip().lower() == want:
                    list_id = str(tl.get("id") or "")
                    break
        if not list_id:
            return []
        payload = gt.list_tasks(
            list_id,
            show_completed=False,
            list_title=title,
        )
        if not payload.get("ok"):
            return []
        return window_gt_candidates(payload.get("tasks") or [], now=now)
    except Exception:
        return []


def attach_gt_ref(
    item: Optional[dict[str, Any]],
    index: Optional[dict[str, dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    """Copy an existing GT handle onto a NOW/NEXT row. Does not create tasks."""
    if not isinstance(item, dict):
        return item
    if writable_source(item):
        return item
    if not index:
        return item
    hit = index.get(_title_key(item.get("title") or item.get("action")))
    if not hit:
        return item
    out = dict(item)
    out["source"] = hit["source"]
    out["source_id"] = hit["source_id"]
    out["gt_task_id"] = hit["source_id"]
    if hit.get("list_id"):
        out["list_id"] = hit["list_id"]
    if hit.get("list_title"):
        out["list_title"] = hit["list_title"]
    return out


def _load_google_tasks(workspace: Path):
    path = Path(workspace) / "projects-dashboard" / "google_tasks.py"
    if not path.is_file():
        path = _ROOT / "projects-dashboard" / "google_tasks.py"
    if not path.is_file():
        raise FileNotFoundError("Google Tasks client not found")
    name = "orchestra_google_tasks_mod"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _resolve_list_id(list_title: str, *, workspace: Path) -> str:
    gt = _load_google_tasks(workspace)
    payload = gt.list_tasklists()
    if not payload.get("ok"):
        return ""
    want = (list_title or "").strip().lower()
    for tl in payload.get("lists") or []:
        if str(tl.get("title") or "").strip().lower() == want:
            return str(tl.get("id") or "")
    return ""


def _complete_google_task(
    list_id: str,
    task_id: str,
    *,
    workspace: Path,
) -> dict[str, Any]:
    gt = _load_google_tasks(workspace)
    return gt.complete_task(list_id, task_id, completed=True)


def complete_item(
    item: Optional[dict[str, Any]],
    *,
    workspace: Optional[Path] = None,
) -> dict[str, Any]:
    """Complete a NOW/NEXT item in Google Tasks. No GT id → no-op."""
    ws = Path(workspace) if workspace is not None else _ROOT
    if not isinstance(item, dict):
        return {"ok": False, "accepted": False, "error": "no Google Tasks id"}
    ref = writable_source(item)
    if ref is None:
        return {"ok": False, "accepted": False, "error": "no Google Tasks id"}
    source_id = ref["source_id"]
    list_id = str(ref.get("list_id") or "").strip()
    try:
        if not list_id and ref.get("list_title"):
            list_id = _resolve_list_id(str(ref["list_title"]), workspace=ws)
        if not list_id:
            return {
                "ok": False,
                "accepted": False,
                "error": "no Google Tasks list id",
                "source": GT_SOURCE,
                "source_id": source_id,
            }
        result = _complete_google_task(list_id, source_id, workspace=ws)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "accepted": False,
            "error": str(e),
            "source": GT_SOURCE,
            "source_id": source_id,
        }

    accepted = bool(result.get("ok"))
    out = dict(result)
    out["ok"] = accepted
    out["accepted"] = accepted
    out["source"] = GT_SOURCE
    out["source_id"] = source_id
    out["list_id"] = list_id
    return out
