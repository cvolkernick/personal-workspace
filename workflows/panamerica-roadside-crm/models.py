"""Lead records and outreach states for the Panamerica roadside CRM."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# new → sms_sent → call_attempted → responded → interested
# → converted | declined | dead. needs-info is intake-only.
LEAD_STATES = (
    "needs-info",
    "new",
    "sms_sent",
    "call_attempted",
    "responded",
    "interested",
    "converted",
    "declined",
    "dead",
)

TERMINAL_STATES = frozenset({"converted", "declined", "dead"})
NEVER_RECONTACT_STATES = frozenset({"converted", "declined", "dead"})
SMS_ELIGIBLE_STATES = frozenset({"new"})
CALL_ELIGIBLE_STATES = frozenset({"sms_sent"})

COPY_VERSION = "v1"
CANONICAL_STORE = "file"  # JSON file store (not Turso). Path is configurable.
SMS_MAX_CHARS = 300
VOICE_DELAY_MIN_DAYS = 5
VOICE_DELAY_MAX_DAYS = 7

OPT_OUT_TOKENS = frozenset(
    {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "unsub"}
)


@dataclass
class Lead:
    id: str
    folder_id: str
    folder_name: str = ""
    photos: list[dict[str, str]] = field(default_factory=list)
    make: str = ""
    model: str = ""
    year: str = ""
    location: str = ""
    spotted_at: str = ""
    asking_price: str = ""
    phone: str = ""
    contact_name: str = ""
    state: str = "new"
    notes: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    last_contact_at: Optional[str] = None
    sms_sent_at: Optional[str] = None
    call_attempted_at: Optional[str] = None
    responded_at: Optional[str] = None
    callback: Optional[dict[str, Any]] = None
    suppression_reason: str = ""
    copy_version: str = COPY_VERSION
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Lead":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def car_label(self) -> str:
        bits = [p for p in (self.year, self.make, self.model) if p]
        return " ".join(bits) if bits else "car"

    def may_contact(self) -> bool:
        return self.state not in NEVER_RECONTACT_STATES and bool(self.phone)
