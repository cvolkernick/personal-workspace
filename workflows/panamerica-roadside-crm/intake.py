"""Daily Drive intake: one photo set → one CRM lead, phone-deduped."""

from __future__ import annotations

import re
from typing import Any, Optional

from adapters import PhotoSet
from models import Lead
from phones import first_phone, normalize_phone

SET_NAME = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})(?:-(?P<road>.+))?$")
YEAR = re.compile(r"\b(19[9]\d|20[0-2]\d)\b")
PRICE = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
MAKE_MODEL = re.compile(
    r"\b(toyota|honda|ford|chevy|chevrolet|tesla|bmw|audi|nissan|hyundai|kia|"
    r"jeep|ram|gmc|subaru|mazda|volkswagen|vw|mercedes|lexus|rivian|cadillac|"
    r"dodge|chrysler|buick|volvo|porsche|mini|acura|infiniti)\b"
    r"(?:\s+([A-Za-z0-9\-]+))?",
    re.IGNORECASE,
)


def parse_set_name(name: str) -> tuple[str, str]:
    match = SET_NAME.match((name or "").strip())
    if not match:
        return "", (name or "").replace("_", " ").replace("-", " ").strip()
    road = (match.group("road") or "").replace("-", " ").replace("_", " ").strip()
    return match.group("date") or "", road


def extract_vehicle(text: str) -> dict[str, str]:
    blob = text or ""
    year = ""
    years = YEAR.findall(blob)
    if years:
        year = years[0]
    price = ""
    prices = PRICE.findall(blob)
    if prices:
        price = prices[0].replace(",", "")
    make = ""
    model = ""
    mm = MAKE_MODEL.search(blob)
    if mm:
        make = mm.group(1).title()
        if make.lower() == "chevy":
            make = "Chevrolet"
        if make.lower() == "vw":
            make = "Volkswagen"
        model = (mm.group(2) or "").title()
    return {"year": year, "make": make, "model": model, "asking_price": price}


def _ocr_bits(photo_set: PhotoSet, ocr: Any) -> list[str]:
    bits: list[str] = []
    for photo in photo_set.photos:
        mapped = (photo_set.ocr or {}).get(photo.get("id") or "") or (photo_set.ocr or {}).get(
            photo.get("name") or ""
        )
        if mapped:
            bits.append(mapped)
        elif ocr is not None:
            bits.append(ocr.read_text(photo) or "")
    return bits


def harvest_set_text(photo_set: PhotoSet, ocr: Any) -> tuple[str, str]:
    """Return (contact_blob, vehicle_blob). Vehicle SoT is OCR + sidecar only."""
    ocr_bits = _ocr_bits(photo_set, ocr)
    vehicle_text = "\n".join(b for b in [photo_set.sidecar_text, *ocr_bits] if b)
    text = "\n".join(
        b
        for b in [photo_set.sidecar_text, *(p.get("name") or "" for p in photo_set.photos), *ocr_bits]
        if b
    )
    return text, vehicle_text


def harvest_vehicle_text(photo_set: PhotoSet, ocr: Any) -> str:
    """Vehicle SoT is OCR + sidecar. Folder name is sighting date, not year/make."""
    return harvest_set_text(photo_set, ocr)[1]


def harvest_text(photo_set: PhotoSet, ocr: Any) -> str:
    return harvest_set_text(photo_set, ocr)[0]


def lead_from_set(photo_set: PhotoSet, text: str, *, vehicle_text: Optional[str] = None) -> Lead:
    spotted, road = parse_set_name(photo_set.name)
    vehicle = extract_vehicle(
        vehicle_text if vehicle_text is not None else harvest_vehicle_text(photo_set, None)
    )
    phone = first_phone(text)
    state = "new" if phone else "needs-info"
    photos = [
        {
            "id": str(p.get("id") or ""),
            "name": str(p.get("name") or ""),
            "web_view_link": str(p.get("web_view_link") or p.get("webViewLink") or ""),
        }
        for p in photo_set.photos
    ]
    notes: list[str] = []
    if not phone:
        notes.append("unreadable/missing contact info")

    date_source = (photo_set.date_source or "").strip()
    location_source = (photo_set.location_source or "").strip()
    location = (photo_set.location or "").strip()
    spotted_at = (photo_set.spotted_at or "").strip() or spotted
    gps = (photo_set.gps or "").strip()

    if not date_source:
        date_source = "folder" if spotted else "upload"
    if not spotted_at:
        spotted_at = spotted
    if date_source == "upload":
        notes.append("date from upload (no DateTimeOriginal EXIF)")

    if location_source == "exif":
        location = location or road
    elif location:
        location_source = location_source or "folder"
    elif road:
        location = road
        location_source = location_source or "folder"
    else:
        location = ""
        location_source = location_source or "missing"

    if location_source == "missing":
        notes.append("missing GPS — fill location from context")
    elif photo_set.location_flagged:
        notes.append("reverse-geocode empty — fill road from context")

    return Lead(
        id=f"lead-{photo_set.id}",
        folder_id=photo_set.id,
        folder_name=photo_set.name,
        photos=photos,
        make=vehicle["make"],
        model=vehicle["model"],
        year=vehicle["year"],
        location=location,
        spotted_at=spotted_at,
        date_source=date_source,
        location_source=location_source,
        gps=gps,
        asking_price=vehicle["asking_price"],
        phone=normalize_phone(phone),
        state=state,
        notes=[n for n in notes if n],
    )
