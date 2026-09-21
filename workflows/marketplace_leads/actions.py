"""Chairman and ops status changes. Nothing here sends a message."""

from __future__ import annotations

from workflows.marketplace_leads.models import (
    CHAIRMAN_ACTIONS,
    QUEUE_STATUS,
    REFUSED_SEND_ACTIONS,
    SMS_ELIGIBLE_ACTION,
    SMS_STATUS,
    Lead,
)
from workflows.marketplace_leads.store import FileStore, utc_now


class ActionError(ValueError):
    pass


def apply_action(store: FileStore, lead_id: str, action: str) -> Lead:
    name = (action or "").strip().lower()
    if name in REFUSED_SEND_ACTIONS:
        raise ActionError("this surface does not send messages")
    lead = store.get((lead_id or "").strip())
    if lead is None:
        raise ActionError("lead not found")
    if name in CHAIRMAN_ACTIONS:
        return _set_status(store, lead, CHAIRMAN_ACTIONS[name], name)
    if name == SMS_ELIGIBLE_ACTION:
        if lead.status != QUEUE_STATUS:
            raise ActionError("only an open Chairman lead can be marked SMS-eligible")
        return _set_status(store, lead, SMS_STATUS, name)
    raise ActionError("unknown action")


def _set_status(store: FileStore, lead: Lead, status: str, action: str) -> Lead:
    lead.status = status
    lead.events.append({"action": action, "status": status, "at": utc_now()})
    return store.update(lead)
