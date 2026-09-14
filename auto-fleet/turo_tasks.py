"""Turo invoice-ready items from Turso (Panamerica inbox scan).

Read/complete only. Title and notes come from the email row — do not invent
amounts, VINs, or trips. No Auto Fleet-local task JSON. Google Tasks is not
the sink.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

try:
    from . import car_cards, invoice_ready
except ImportError:  # script / unittest path
    import car_cards  # type: ignore
    import invoice_ready  # type: ignore

LIST_TITLE = invoice_ready.LIST_TITLE
LIST_ID = invoice_ready.LIST_ID


def _item_view(raw: Mapping[str, Any]) -> dict[str, Any]:
    status = raw.get("status") or invoice_ready.STATUS_OPEN
    if status in (invoice_ready.STATUS_OPEN, "needsAction"):
        ui_status = "needsAction"
    else:
        ui_status = "completed"
    return {
        "id": raw.get("id"),
        "list_id": raw.get("list_id") or LIST_ID,
        "title": raw.get("title") or raw.get("subject") or "",
        "notes": raw.get("notes") or "",
        "status": ui_status,
        "updated": raw.get("updated") or raw.get("updated_at"),
        "received_at": raw.get("received_at"),
        "source_email_ref": raw.get("source_email_ref") or raw.get("id"),
    }


def annotate_unit_ids(
    items: Sequence[Mapping[str, Any]],
    units: Sequence[Mapping[str, Any]] | None,
    bookings_by_unit: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Add unit_id when the title/notes (or trip #) maps to one car."""
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


def _from_injected(store: Any) -> dict[str, Any] | None:
    if store is None:
        return None
    if isinstance(store, invoice_ready.MemoryStore):
        return None
    if isinstance(store, dict) and "items" in store:
        items = [_item_view(t) for t in (store.get("items") or []) if isinstance(t, dict)]
        return {
            "ok": store.get("ok", True),
            "source": store.get("source") or "turso",
            "configured": store.get("configured", True),
            "list_title": store.get("list_title") or LIST_TITLE,
            "list_id": store.get("list_id") or LIST_ID,
            "error": store.get("error"),
            "items": items,
            "count": store.get("count", len(items)),
        }
    return None


def list_open_tasks(
    *,
    store: Any | None = None,
    gt: Any | None = None,
    units: Sequence[Mapping[str, Any]] | None = None,
    bookings_by_unit: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Open invoice-ready items. Empty is honest, not fake rows."""
    injected = store if store is not None else gt
    payload = _from_injected(injected)
    if payload is None:
        listed = invoice_ready.list_open(store=injected)
        items = [_item_view(t) for t in (listed.get("items") or [])]
        payload = {
            "ok": listed.get("ok", True),
            "source": listed.get("source") or "turso",
            "configured": listed.get("configured", True),
            "list_title": listed.get("list_title") or LIST_TITLE,
            "list_id": listed.get("list_id") or LIST_ID,
            "error": listed.get("error"),
            "items": items,
            "count": listed.get("count", len(items)),
        }
    else:
        items = list(payload.get("items") or [])
    open_items = [
        it
        for it in items
        if (it.get("status") or "needsAction") == "needsAction"
    ]
    if units:
        open_items = annotate_unit_ids(open_items, units, bookings_by_unit)
    payload["items"] = open_items
    payload["count"] = len(open_items)
    return payload


def complete_task(
    task_id: str,
    list_id: Optional[str] = None,
    *,
    store: Any | None = None,
    gt: Any | None = None,
) -> dict[str, Any]:
    """Checkbox write-back: mark the Turso row completed."""
    tid = (task_id or "").strip()
    if not tid:
        return {"ok": False, "error": "task_id required"}
    injected = store if store is not None else gt
    if isinstance(injected, dict) and "items" in injected:
        return {"ok": False, "error": "store does not support complete"}
    result = invoice_ready.complete(tid, store=injected)
    if not result.get("ok"):
        return result
    return {
        "ok": True,
        "source": result.get("source") or "turso",
        "list_id": result.get("list_id") or LIST_ID,
        "task_id": tid,
        "task": result.get("item"),
    }
