"""Discovery email (Touch 1) and inbound reply parsing into template slots."""

from __future__ import annotations

import re
from typing import Any

from models import TEMPLATE_SLOTS, Lead

PHONE_RE = re.compile(
    r"(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}"
)
OPT_OUT_RE = re.compile(
    r"\b(stop|unsubscribe|opt[-\s]?out|do not contact|don't contact|remove me)\b",
    re.I,
)
NOT_INTERESTED_RE = re.compile(
    r"\b(not interested|no thanks|no thank you|please don't|leave me alone)\b",
    re.I,
)
INTEREST_RE = re.compile(
    r"\b(interested|love it|looks great|looks good|how much|pricing|price|"
    r"go live|get it live|let'?s do it|tweak|change the|want it|sign me up)\b",
    re.I,
)

SLOT_HINTS = {
    "services": re.compile(r"\b(service|offer|do you do|we (?:do|offer|provide))\b", re.I),
    "hours": re.compile(r"\b(hours?|open|schedule|mon|tue|wed)\b", re.I),
    "contact": re.compile(r"\b(reach|call|email|contact|phone)\b", re.I),
    "differentiator": re.compile(r"\b(different|unique|why us|what makes)\b", re.I),
    "photos": re.compile(r"\b(photo|logo|image|picture)\b", re.I),
}


def render_discovery_email(lead: Lead, *, physical_address: str, sender_name: str = "Alexandra") -> dict[str, str]:
    neighborhood = lead.neighborhood or lead.address or "your area"
    rating_bit = f" (listed around {lead.rating:g} stars)" if lead.rating else ""
    category_bit = f" {lead.category}" if lead.category else ""
    to = lead.email
    subject = f"Quick question about {lead.name}"
    body = f"""Hi — I'm {sender_name}.

I couldn't find a website for {lead.name} in {neighborhood}{rating_bit}. I'm doing research on{category_bit} businesses that could improve their visibility and outreach with a digital presence.

Could you tell me a little about your business? A few specifics help a lot — four or five is plenty:

1. What services do you offer?
2. Your hours?
3. How should customers reach you?
4. What makes you different?
5. Any photos or a logo you want used?

What's the best number to text the preview link to?

Thanks,
{sender_name}

---
This message was sent by {sender_name} in connection with research on local business visibility.
{physical_address or "[physical mailing address — set DEMO_SITE_OUTREACH_PHYSICAL_ADDRESS]"}
If you don't want further email, reply STOP.
"""
    return {"to": to, "subject": subject, "body": body, "from_name": sender_name}


def render_followup_email(lead: Lead, *, n: int, sender_name: str = "Alexandra") -> dict[str, str]:
    subject = f"Following up — {lead.name}"
    body = (
        f"Hi — {sender_name} again. Just a short follow-up ({n} of 2) on my note about "
        f"{lead.name}. If now's a bad time, reply STOP and I won't email again.\n"
    )
    return {"to": lead.email, "subject": subject, "body": body, "from_name": sender_name}


def render_demo_sms(lead: Lead) -> str:
    url = lead.demo_url or ""
    return (
        f"Hi, it's Alexandra — here's a preview I put together for {lead.name}: {url}\n"
        "What do you think? If you like it, or want tweaks, we can get it live for you."
    )


def render_bow_out_sms(lead: Lead) -> str:
    return (
        f"Glad this was useful for {lead.name}. I'll have him reach out directly — "
        "thanks for your time."
    )


def parse_inbound(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    result: dict[str, Any] = {
        "opt_out": bool(OPT_OUT_RE.search(raw)),
        "not_interested": bool(NOT_INTERESTED_RE.search(raw)),
        "interest": bool(INTEREST_RE.search(raw)),
        "slots": {},
        "sms_number": "",
        "sms_consent": False,
        "asked_for": "",
    }
    if not raw:
        return result

    numbered = _numbered_answers(raw)
    for i, slot in enumerate(TEMPLATE_SLOTS, start=1):
        if str(i) in numbered:
            result["slots"][slot] = numbered[str(i)]

    for slot, hint in SLOT_HINTS.items():
        if slot in result["slots"]:
            continue
        for line in raw.splitlines():
            if hint.search(line) and ":" in line:
                result["slots"][slot] = line.split(":", 1)[1].strip()

    phones = [normalize_phone(m.group(0)) for m in PHONE_RE.finditer(raw)]
    phones = [p for p in phones if p]
    consent_context = bool(
        re.search(r"\b(text|sms|preview|best number|cell|mobile)\b", raw, re.I)
    )
    if phones and (consent_context or "sms_number" in raw.lower() or numbered):
        result["sms_number"] = phones[-1]
        result["sms_consent"] = True
    elif phones and not result["slots"]:
        # A reply that is only a number to the must-ask is consent.
        result["sms_number"] = phones[-1]
        result["sms_consent"] = True

    if result["interest"]:
        result["asked_for"] = raw.strip()[:400]
    return result


def apply_discovery(lead: Lead, parsed: dict[str, Any]) -> Lead:
    slots = parsed.get("slots") or {}
    for slot in TEMPLATE_SLOTS:
        value = str(slots.get(slot) or "").strip()
        if value:
            lead.discovery[slot] = value
    if parsed.get("sms_number"):
        lead.sms_number = parsed["sms_number"]
        lead.sms_consent = True
    elif parsed.get("sms_consent") and lead.phone:
        lead.sms_number = normalize_phone(lead.phone)
        lead.sms_consent = True
    if parsed.get("asked_for"):
        lead.asked_for = parsed["asked_for"]
    return lead


def simulated_reply(lead: Lead) -> str:
    """Deterministic dry-run reply so the harness can run the pipeline without waiting."""
    hours = lead.hours or "Mon–Fri 9am–5pm"
    phone = lead.phone or "555-010-0000"
    category = lead.category or "local services"
    return (
        f"1. {category}\n"
        f"2. {hours}\n"
        f"3. Call or text {phone}\n"
        f"4. Local, reliable, and easy to reach\n"
        f"5. No logo yet\n"
        f"Best number to text the preview: {phone}\n"
    )


def simulated_interest_reply(lead: Lead) -> str:
    return f"Looks great — let's get it live for {lead.name}. How much to go live?"


def normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return digits
    return digits


def _numbered_answers(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in re.finditer(r"(?m)^\s*([1-5])[.):\-]\s*(.+)$", text):
        found[match.group(1)] = match.group(2).strip()
    return found
