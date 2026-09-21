"""Lead records for the marketplace CRM.

Canonical state lives here. The morning brief and the FCC queue render
from these records. This package does not send Messenger or SMS.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

QUEUE_STATUS = "surface_to_chairman"
SMS_STATUS = "sms_queued"
STATUS_CONTACTED = "contacted"
STATUS_DISQUALIFIED = "disqualified"
STATUS_CONVERTED = "converted"

STATUSES = (
    QUEUE_STATUS,
    SMS_STATUS,
    STATUS_CONTACTED,
    STATUS_DISQUALIFIED,
    STATUS_CONVERTED,
)

# Chairman buttons. sms_eligible is an ops status change, not a send.
CHAIRMAN_ACTIONS = {
    "contacted": STATUS_CONTACTED,
    "disqualified": STATUS_DISQUALIFIED,
    "converted": STATUS_CONVERTED,
}
SMS_ELIGIBLE_ACTION = "sms_eligible"
REFUSED_SEND_ACTIONS = frozenset(
    {"send", "message", "messenger", "sms", "text", "dm"}
)

_LISTING_ID = re.compile(r"^[A-Za-z0-9]{4,64}$")
_ITEM_URL = "https://www.facebook.com/marketplace/item/{listing_id}/"


def normalize_phone(value: object) -> str:
    """Return a 10-digit NANP string, or empty when the listing has no phone."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return digits
    return ""


def clean_listing_id(value: object) -> str:
    listing_id = str(value or "").strip()
    if not _LISTING_ID.fullmatch(listing_id):
        return ""
    return listing_id


def safe_http_url(value: object) -> str:
    url = str(value or "").strip()
    if url.startswith("https://") or url.startswith("http://"):
        return url
    return ""


def canonical_listing_url(listing_id: str, stored: str = "") -> str:
    """Link by item id. Marketplace slugs change; the id does not."""
    listing_id = clean_listing_id(listing_id)
    if listing_id:
        return _ITEM_URL.format(listing_id=listing_id)
    return safe_http_url(stored)


def dedup_key(listing_id: str, phone: str) -> tuple[str, str]:
    return (clean_listing_id(listing_id), normalize_phone(phone))


@dataclass
class Lead:
    id: str
    listing_id: str
    status: str = QUEUE_STATUS
    year: str = ""
    make: str = ""
    model: str = ""
    price: str = ""
    mileage: str = ""
    location: str = ""
    listing_date: str = ""
    listing_url: str = ""
    photo_url: str = ""
    seller_name: str = ""
    seller_rating: str = ""
    reason_flagged: str = ""
    phone: str = ""
    phone_present: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Lead":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def vehicle_label(self) -> str:
        bits = [p for p in (self.year, self.make, self.model) if str(p).strip()]
        return " ".join(bits) if bits else "Vehicle"

    def on_queue(self) -> bool:
        return self.status == QUEUE_STATUS
