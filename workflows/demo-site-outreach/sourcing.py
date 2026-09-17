"""Stage 1 — Google Places lead sourcing, website-absence filter, suppression dedupe."""

from __future__ import annotations

from typing import Any

from models import Lead
from store import FileStore, utc_now


def listings_to_leads(
    listings: list[dict[str, Any]],
    store: FileStore,
    *,
    geo: str,
    limit: int,
) -> list[Lead]:
    """Keep businesses with no website, not already stored, not suppressed."""
    out: list[Lead] = []
    for row in listings:
        if len(out) >= limit:
            break
        website = str(row.get("website") or "").strip()
        if website:
            continue
        place_id = str(row.get("place_id") or "").strip()
        name = str(row.get("name") or "").strip()
        if not place_id or not name:
            continue
        if store.find_by_place(place_id):
            continue
        phone = str(row.get("phone") or "").strip()
        email = str(row.get("email") or "").strip()
        if store.is_suppressed(f"place:{place_id}", phone, email):
            continue
        lead = Lead(
            id=place_id,
            place_id=place_id,
            name=name,
            address=str(row.get("address") or ""),
            phone=phone,
            email=email,
            hours=str(row.get("hours") or ""),
            category=str(row.get("category") or ""),
            rating=row.get("rating") if isinstance(row.get("rating"), (int, float)) else None,
            reviews=list(row.get("reviews") or []),
            photos=list(row.get("photos") or []),
            neighborhood=str(row.get("neighborhood") or ""),
            website="",
            geo=str(row.get("geo") or geo),
            state="sourced",
        )
        lead.events.append({"at": utc_now(), "op": "sourced"})
        store.put(lead)
        out.append(lead)
    return out


def source_leads(store: FileStore, places: Any, *, geo: str, category: str, limit: int) -> list[Lead]:
    listings = places.search(geo=geo, category=category, limit=max(limit * 3, limit))
    return listings_to_leads(listings, store, geo=geo, limit=limit)
