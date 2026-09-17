"""Weekly Drive intake: one photo set → one CRM lead, phone-deduped."""

from __future__ import annotations

import re
from typing import Any

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


def harvest_text(photo_set: PhotoSet, ocr: Any) -> str:
    bits = [photo_set.name, photo_set.sidecar_text]
    for photo in photo_set.photos:
        bits.append(photo.get("name") or "")
        mapped = (photo_set.ocr or {}).get(photo.get("id") or "") or (photo_set.ocr or {}).get(
            photo.get("name") or ""
        )
        if mapped:
            bits.append(mapped)
        elif ocr is not None:
            bits.append(ocr.read_text(photo) or "")
    return "\n".join(b for b in bits if b)


def lead_from_set(photo_set: PhotoSet, text: str) -> Lead:
    spotted, road = parse_set_name(photo_set.name)
    vehicle = extract_vehicle(text)
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
    note = "unreadable/missing contact info" if not phone else ""
    return Lead(
        id=f"lead-{photo_set.id}",
        folder_id=photo_set.id,
        folder_name=photo_set.name,
        photos=photos,
        make=vehicle["make"],
        model=vehicle["model"],
        year=vehicle["year"],
        location=road,
        spotted_at=spotted,
        asking_price=vehicle["asking_price"],
        phone=normalize_phone(phone),
        state=state,
        notes=[note] if note else [],
    )
