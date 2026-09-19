"""Turo invoice-ready items from Google Tasks (list titled Turo).

Read/complete only. Title and notes come from the GT item — do not invent
amounts, VINs, or trips. No Auto Fleet-local task JSON.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

try:
    from . import car_cards, gtasks as gtb
except ImportError:  # script / unittest path
    import car_cards  # type: ignore
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


def annotate_unit_ids(
    items: Sequence[Mapping[str, Any]],
    units: Sequence[Mapping[str, Any]] | None,
    bookings_by_unit: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Add unit_id when the GT title/notes (or trip #) maps to one car."""
    out: list[dict[str, Any]] = []
    roster = list(units or [])
    for raw in items:
        rec = dict(raw)
        if roster:
            rec["unit_id"] = car_cards.match_invoice_unit(
                rec, roster, bookings_by_unit
            )
        out.append(rec)
    return out


def list_open_tasks(
    *,
    gt: Any | None = None,
    units: Sequence[Mapping[str, Any]] | None = None,
    bookings_by_unit: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
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
        if units:
            items = annotate_unit_ids(items, units, bookings_by_unit)
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


def _task_blob(item: Mapping[str, Any]) -> str:
    return f"{item.get('title') or ''} {item.get('notes') or ''}".lower()


def _iso_day(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace(" ", "T")[:10]


def task_matches_change(item: Mapping[str, Any], change: Mapping[str, Any]) -> bool:
    blob = _task_blob(item)
    if not blob.strip():
        return False
    trip = str(change.get("trip_id") or "").strip()
    if trip and trip.lower() in blob:
        return True
    guest = str(change.get("guest") or "").strip().lower()
    if not guest or guest not in blob:
        return False
    vehicle = str(change.get("vehicle") or "").lower()
    unit_bits = ("corolla", "tesla", "model 3", "rivian", "r1s", "turo")
    if "turo" in blob or any(bit in blob for bit in unit_bits) or any(
        bit in vehicle for bit in unit_bits if bit in blob
    ):
        return True
    return False


def _describes_old_window(item: Mapping[str, Any], change: Mapping[str, Any]) -> bool:
    blob = _task_blob(item)
    title = str(item.get("title") or "")
    if title.lower().startswith("update") and "turo" in title.lower():
        return True
    for key in ("prior_end", "prior_start"):
        day = _iso_day(change.get(key))
        if day and day in blob:
            return True
    return " end" in blob or blob.rstrip().endswith("end") or "turo end" in blob


def _rewrite_title(change: Mapping[str, Any]) -> str:
    guest = str(change.get("guest") or "Guest").strip()
    vehicle = str(change.get("vehicle") or "Turo").strip()
    end = str(change.get("end") or "").strip()
    return f"{guest} {vehicle} Turo END {end}".strip()


def close_or_rewrite_stale(
    change: Mapping[str, Any],
    *,
    gt: Any | None = None,
) -> dict[str, Any]:
    """Complete Update-window tasks; rewrite standing old-window reminders.

    Call only after calendar apply succeeded so ops tasks do not close stale
    while the calendar is still wrong.
    """
    listed = list_open_tasks(gt=gt)
    if not listed.get("ok"):
        return {
            "ok": False,
            "error": listed.get("error") or "Could not list Turo tasks",
            "items": [],
        }
    items = [i for i in (listed.get("items") or []) if task_matches_change(i, change)]
    if not items:
        return {
            "ok": True,
            "matched": 0,
            "completed": [],
            "rewritten": [],
            "list_id": listed.get("list_id"),
        }
    client = gt
    if client is None:
        cred = gtb.credentials_status()
        if not cred.get("ok"):
            return {"ok": False, "error": cred.get("error") or "Google Tasks not configured"}
        client = gtb.load_google_tasks()
    list_id = str(listed.get("list_id") or "")
    completed: list[str] = []
    rewritten: list[str] = []
    errors: list[str] = []
    for item in items:
        tid = str(item.get("id") or "").strip()
        if not tid:
            continue
        title = str(item.get("title") or "")
        if title.lower().startswith("update") or _describes_old_window(item, change):
            result = complete_task(tid, list_id, gt=client)
            if result.get("ok"):
                completed.append(tid)
            else:
                errors.append(str(result.get("error") or tid))
            continue
        new_title = _rewrite_title(change)
        if hasattr(client, "update_task"):
            result = client.update_task(list_id, tid, title=new_title)
            if isinstance(result, dict) and result.get("ok"):
                rewritten.append(tid)
            elif isinstance(result, dict):
                errors.append(str(result.get("error") or tid))
            else:
                rewritten.append(tid)
        else:
            result = complete_task(tid, list_id, gt=client)
            if result.get("ok"):
                completed.append(tid)
            else:
                errors.append(str(result.get("error") or tid))
    return {
        "ok": not errors,
        "matched": len(items),
        "completed": completed,
        "rewritten": rewritten,
        "errors": errors,
        "list_id": list_id,
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
