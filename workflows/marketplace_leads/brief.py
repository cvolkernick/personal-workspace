"""Roadside section of the 7:45 morning brief.

Callers include `render_morning_brief` verbatim. The section is built
only from CRM rows still in `surface_to_chairman`.
"""

from __future__ import annotations

from workflows.marketplace_leads.models import (
    Lead,
    canonical_listing_url,
    safe_http_url,
)
from workflows.marketplace_leads.store import FileStore


def _field(label: str, value: str, *, empty: str = "unavailable") -> str:
    text = (value or "").strip()
    return f"- {label}: {text or empty}"


def _seller_line(lead: Lead) -> str:
    name = (lead.seller_name or "").strip()
    rating = (lead.seller_rating or "").strip()
    if name and rating:
        return f"{name} ({rating})"
    if name:
        return name
    if rating:
        return f"rating {rating}"
    return ""


def render_lead_block(lead: Lead) -> str:
    photo = safe_http_url(lead.photo_url)
    link = canonical_listing_url(lead.listing_id, lead.listing_url)
    lines = [
        f"### {lead.vehicle_label()}",
        _field("Photo", photo),
        _field("Price", lead.price),
        _field("Mileage", lead.mileage),
        _field("Location", lead.location),
        _field("Listing date", lead.listing_date),
        _field("Listing", link),
        _field("Flagged", lead.reason_flagged),
        _field("Seller", _seller_line(lead)),
        _field("Phone on listing", "yes" if lead.phone_present else "no", empty="no"),
        "- Follow-up: open the listing and message the seller manually.",
    ]
    return "\n".join(lines)


def render_roadside_section(leads: list[Lead]) -> str:
    open_leads = [lead for lead in leads if lead.on_queue()]
    lines = ["## Roadside", ""]
    if not open_leads:
        lines.append("No Chairman marketplace leads.")
        return "\n".join(lines) + "\n"
    for lead in open_leads:
        lines.append(render_lead_block(lead))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_morning_brief(leads: list[Lead]) -> str:
    """7:45 morning brief Roadside section, rendered from the CRM."""
    return render_roadside_section(leads)


def morning_brief_from_store(store: FileStore) -> str:
    return render_morning_brief(store.all_leads())
