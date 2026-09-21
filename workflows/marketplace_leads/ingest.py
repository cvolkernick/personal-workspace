"""Write a discovered listing into the CRM. No outreach side effects."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from workflows.marketplace_leads.models import (
    QUEUE_STATUS,
    SMS_STATUS,
    Lead,
    canonical_listing_url,
    clean_listing_id,
    normalize_phone,
    safe_http_url,
)
from workflows.marketplace_leads.store import FileStore


@dataclass
class IngestResult:
    lead: Lead
    created: bool
    duplicate: bool


def _text(payload: dict, key: str) -> str:
    return str(payload.get(key) or "").strip()


def ingest(store: FileStore, payload: dict) -> IngestResult:
    """Classify a listing.

    A normalized phone is the SMS path (`sms_queued`). No phone is
    `surface_to_chairman`. Either way, an existing listing_id or
    listing_id+phone pair is returned as-is and is not re-queued.
    """
    if not isinstance(payload, dict):
        raise TypeError("lead payload must be an object")
    listing_id = clean_listing_id(payload.get("listing_id"))
    if not listing_id:
        raise ValueError("listing_id is required")
    phone = normalize_phone(payload.get("phone"))
    phone_present = bool(phone)
    status = SMS_STATUS if phone_present else QUEUE_STATUS
    lead = Lead(
        id=str(uuid.uuid4()),
        listing_id=listing_id,
        status=status,
        year=_text(payload, "year"),
        make=_text(payload, "make"),
        model=_text(payload, "model"),
        price=_text(payload, "price"),
        mileage=_text(payload, "mileage"),
        location=_text(payload, "location"),
        listing_date=_text(payload, "listing_date"),
        listing_url=canonical_listing_url(listing_id, _text(payload, "listing_url")),
        photo_url=safe_http_url(payload.get("photo_url") or payload.get("photo")),
        seller_name=_text(payload, "seller_name"),
        seller_rating=_text(payload, "seller_rating"),
        reason_flagged=_text(payload, "reason_flagged") or _text(payload, "reason"),
        phone=phone,
        phone_present=phone_present,
    )
    saved, created = store.insert_if_new(lead)
    return IngestResult(lead=saved, created=created, duplicate=not created)
