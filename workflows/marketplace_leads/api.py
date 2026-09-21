"""JSON shapes for the FCC lead queue. Read-only for outreach."""

from __future__ import annotations

from workflows.marketplace_leads.actions import ActionError, apply_action
from workflows.marketplace_leads.models import (
    Lead,
    canonical_listing_url,
    safe_http_url,
)
from workflows.marketplace_leads.store import FileStore


def public_lead(lead: Lead) -> dict:
    photo = safe_http_url(lead.photo_url)
    seller_name = (lead.seller_name or "").strip()
    seller_rating = (lead.seller_rating or "").strip()
    return {
        "id": lead.id,
        "listing_id": lead.listing_id,
        "listing_url": canonical_listing_url(lead.listing_id, lead.listing_url),
        "photo_url": photo,
        "photo_unavailable": not bool(photo),
        "year": lead.year,
        "make": lead.make,
        "model": lead.model,
        "label": lead.vehicle_label(),
        "price": lead.price,
        "mileage": lead.mileage,
        "location": lead.location,
        "listing_date": lead.listing_date,
        "seller_name": seller_name,
        "seller_rating": seller_rating,
        "seller_unavailable": not (seller_name or seller_rating),
        "reason_flagged": lead.reason_flagged,
        "phone_present": bool(lead.phone_present),
        "status": lead.status,
    }


def queue_payload(store: FileStore) -> dict:
    rows = [public_lead(lead) for lead in store.open_queue()]
    return {
        "ok": True,
        "status": "surface_to_chairman",
        "leads": rows,
        "outreach": "manual",
    }


def action_payload(store: FileStore, lead_id: str, action: str) -> dict:
    try:
        lead = apply_action(store, lead_id, action)
    except ActionError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "lead": public_lead(lead), "outreach": "manual"}
