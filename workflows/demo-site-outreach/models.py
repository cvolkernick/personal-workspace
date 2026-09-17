"""Lead records and state-machine constants for demo-site outreach."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# sourced → contacted → discovering → building → demo_sent → qualifying
# → handed_off | dead | suppressed
LEAD_STATES = (
    "sourced",
    "contacted",
    "discovering",
    "building",
    "demo_sent",
    "qualifying",
    "handed_off",
    "dead",
    "suppressed",
)

TERMINAL_STATES = frozenset({"handed_off", "dead", "suppressed"})
SMS_ELIGIBLE_STATES = frozenset({"building", "demo_sent", "qualifying"})

TEMPLATE_SLOTS = ("services", "hours", "contact", "differentiator", "photos")
TEMPLATE_VERSION = "v1"
MAX_FOLLOW_UPS = 2

CANONICAL_STORE = "file"  # JSON file store (not Turso). Path is configurable.


@dataclass
class Lead:
    id: str
    place_id: str
    name: str
    address: str = ""
    phone: str = ""
    email: str = ""
    hours: str = ""
    category: str = ""
    rating: Optional[float] = None
    reviews: list[str] = field(default_factory=list)
    photos: list[str] = field(default_factory=list)
    neighborhood: str = ""
    website: str = ""
    geo: str = "US"
    state: str = "sourced"
    template_version: str = TEMPLATE_VERSION
    discovery: dict[str, str] = field(default_factory=dict)
    sms_number: str = ""
    sms_consent: bool = False
    follow_ups: int = 0
    demo_url: str = ""
    vercel_deployment_id: str = ""
    last_contact_at: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    handoff: Optional[dict[str, Any]] = None
    suppression_reason: str = ""
    asked_for: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Lead":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def discovery_complete(self) -> bool:
        return all(str(self.discovery.get(slot) or "").strip() for slot in TEMPLATE_SLOTS)

    def two_line_summary(self) -> str:
        services = (self.discovery.get("services") or self.category or "services unknown").strip()
        differentiator = (self.discovery.get("differentiator") or "").strip()
        line1 = f"{self.name} — {services}"
        line2 = differentiator or (self.neighborhood or self.address or "location unknown")
        return f"{line1}\n{line2}"
