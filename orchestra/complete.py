"""Google Tasks write-back for Orchestra NOW/NEXT checkboxes.

One central task bucket: Google Tasks. Orchestra / FitDash / Turo-Fleet
only surface and complete their slice. No Orchestra task store. No Time
Allocator task list — schedule blocks without a GT id stay uncheckable.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

GT_SOURCE = "google_tasks"
GT_SOURCE_ALIASES = frozenset(
    {"google_tasks", "fitdash", "turo", "turo-fleet", "fleet"}
)
GT_ID_KEYS = ("gt_task_id", "google_task_id", "gt_id")
LIST_ID_KEYS = ("list_id", "gt_list_id", "google_list_id")
LIST_TITLE_KEYS = ("list_title", "gt_list_title", "google_list_title")

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
    """Title → GT handle from FitDash quests / future Turo tasks already collected."""
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
