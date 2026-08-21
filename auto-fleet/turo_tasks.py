"""Turo invoice-ready items from Google Tasks (list titled Turo).

Read/complete only. Title and notes come from the GT item — do not invent
amounts, VINs, or trips. No Auto Fleet-local task JSON.
"""

from __future__ import annotations

from typing import Any, Optional

try:
    from . import gtasks as gtb
except ImportError:  # script / unittest path
    import gtasks as gtb  # type: ignore

LIST_TITLE = "Turo"


def _item_view(raw: dict[str, Any], list_id: str) -> dict[str, Any]:
    """Pass through GT title/notes only. No invented fleet fields."""
    return {
        "id": raw.get("id"),
        "list_id": raw.get("list_id") or list_id,
        "title": raw.get("title") or "",
        "notes": raw.get("notes") or "",
        "status": raw.get("status") or "needsAction",
        "updated": raw.get("updated"),
    }


def find_or_create_turo_list(gt: Any | None = None) -> dict[str, Any]:
    """Find the Google Tasks list named Turo, or create that one list."""
    client = gt if gt is not None else gtb.load_google_tasks()
    payload = client.list_tasklists()
    if not payload.get("ok"):
        return {
            "ok": False,
            "error": payload.get("error") or "Could not list Google Tasks lists",
        }
    want = LIST_TITLE.lower()
    for tl in payload.get("lists") or []:
        if str(tl.get("title") or "").strip().lower() == want:
            lid = str(tl.get("id") or "") or None
            if not lid:
                continue
            return {
                "ok": True,
                "list_id": lid,
                "list_title": str(tl.get("title") or LIST_TITLE),
                "created": False,
            }
    created = client.create_tasklist(LIST_TITLE)
    if not created.get("ok"):
        return {
            "ok": False,
            "error": created.get("error") or "Could not create Turo list",
        }
    lst = created.get("list") or {}
    lid = str(lst.get("id") or "") or None
    if not lid:
        return {"ok": False, "error": "Turo list create returned no id"}
    return {
        "ok": True,
        "list_id": lid,
        "list_title": str(lst.get("title") or LIST_TITLE),
        "created": True,
    }


def list_open_tasks(*, gt: Any | None = None) -> dict[str, Any]:
    """Open (needsAction) items on the Turo list. Empty is honest, not fake rows."""
    cred = gtb.credentials_status() if gt is None else {"ok": True}
    if not cred.get("ok"):
        err = cred.get("error") or cred.get("hint") or "Google Tasks not configured"
        if "Google Tasks" not in err:
            err = f"Google Tasks not configured — {err}"
        return {
            "ok": False,
            "error": err,
            "source": "google_tasks",
            "list_title": LIST_TITLE,
            "items": [],
        }
    client = gt if gt is not None else gtb.load_google_tasks()
    try:
        found = find_or_create_turo_list(client)
        if not found.get("ok"):
            return {
                "ok": False,
                "error": found.get("error") or "Turo list unavailable",
                "source": "google_tasks",
                "list_title": LIST_TITLE,
                "items": [],
            }
        list_id = str(found["list_id"])
        payload = client.list_tasks(
            list_id,
            show_completed=False,
            show_hidden=False,
            list_title=LIST_TITLE,
        )
        if not payload.get("ok"):
            return {
                "ok": False,
                "error": payload.get("error") or "Could not list Turo tasks",
                "source": "google_tasks",
                "list_title": LIST_TITLE,
                "list_id": list_id,
                "items": [],
            }
        items = [
            _item_view(t, list_id)
            for t in (payload.get("tasks") or [])
            if isinstance(t, dict) and (t.get("status") or "needsAction") == "needsAction"
        ]
        return {
            "ok": True,
            "source": "google_tasks",
            "list_title": LIST_TITLE,
            "list_id": list_id,
            "items": items,
            "count": len(items),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(e) or "Google Tasks error",
            "source": "google_tasks",
            "list_title": LIST_TITLE,
            "items": [],
        }


def complete_task(
    task_id: str,
    list_id: Optional[str] = None,
    *,
    gt: Any | None = None,
) -> dict[str, Any]:
    """Checkbox write-back: mark the GT item completed on the Turo list."""
    tid = (task_id or "").strip()
    if not tid:
        return {"ok": False, "error": "task_id required"}
    cred = gtb.credentials_status() if gt is None else {"ok": True}
    if not cred.get("ok"):
        err = cred.get("error") or cred.get("hint") or "Google Tasks not configured"
        if "Google Tasks" not in err:
            err = f"Google Tasks not configured — {err}"
        return {
            "ok": False,
            "error": err,
        }
    client = gt if gt is not None else gtb.load_google_tasks()
    try:
        found = find_or_create_turo_list(client)
        if not found.get("ok"):
            return {
                "ok": False,
                "error": found.get("error") or "Turo list unavailable",
            }
        turo_id = str(found["list_id"])
        given = (list_id or "").strip()
        if given and given != turo_id:
            return {"ok": False, "error": "task is not on the Turo list"}
        result = client.complete_task(turo_id, tid, completed=True)
        if not isinstance(result, dict):
            return {"ok": False, "error": "complete_failed"}
        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error") or "Could not complete Google Task",
            }
        return {
            "ok": True,
            "source": "google_tasks",
            "list_id": turo_id,
            "task_id": tid,
            "task": result.get("task"),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e) or "Google Tasks error"}
