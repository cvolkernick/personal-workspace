"""Chris-approved SMS + voice script (#855, 2026-09-20).

Copy is approved in-repo. Live send stays gated on
PANAMERICA_ROADSIDE_COPY_APPROVED=1 (first-send human gate).

Location slot (#860): outbound SMS/voice use sale_location_phrase;
the CRM keeps the full location string.
"""

from __future__ import annotations

import re

from models import SMS_MAX_CHARS, Lead

# Canonical copy from issue #855. Do not drift without a new Chris approval.
# {location_slot} is "on {road}" or "near {place} in {city}" (#860).
SMS_DRAFT = (
    "Hi, this is Alexandra with Panamerica Auto in Cape Coral. "
    "I saw your {car} for sale {location_slot} — have you considered renting it out "
    "instead of selling? We manage cars for owners and handle everything: "
    "listing, guests, cleaning, maintenance. We also have an owner-exit option "
    "where we take over the payments if you'd rather move on. You can find details "
    "on our available options at https://www.panamericafleet.com/plans. Worth a 10-min chat?"
)

# Inbound parse_inbound still honors STOP. Do not append this line to sends.
OPT_OUT_LINE = "Reply STOP to opt out."


_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
_COORD_RE = re.compile(r"^-?\d{1,3}\.\d+\s*,\s*-?\d{1,3}\.\d+$")
_STATE_ABBR = frozenset(
    {
        "al", "ak", "az", "ar", "ca", "co", "ct", "de", "dc", "fl", "ga",
        "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma",
        "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny",
        "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx",
        "ut", "vt", "va", "wa", "wv", "wi", "wy", "usa", "us",
    }
)
_STATE_NAMES = frozenset(
    {
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "district of columbia", "florida", "georgia",
        "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
        "louisiana", "maine", "maryland", "massachusetts", "michigan",
        "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
        "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
        "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
        "rhode island", "south carolina", "south dakota", "tennessee", "texas",
        "utah", "vermont", "virginia", "washington", "west virginia",
        "wisconsin", "wyoming", "united states",
    }
)


def sale_location_phrase(location: str) -> str:
    """Outbound-only location slot. CRM keeps the original string.

    Proximity ('Near Burnt Store, Cape Coral, FL 33991') →
    'near Burnt Store in Cape Coral'. Road-style ('Burnt Store Rd') →
    'on Burnt Store Rd'. Raw coords and empty fall back to 'on the roadside'.
    """
    text = re_sub_ws(location or "")
    if not text or _COORD_RE.match(text):
        return "on the roadside"
    lowered = text.lower()
    if lowered.startswith("on "):
        text = text[3:].strip()
        lowered = text.lower()
    if lowered in {"the roadside", "roadside"}:
        return "on the roadside"
    if lowered.startswith("near "):
        rest = text[5:].strip()
        parts = _drop_region_parts(rest.split(","))
        if not parts:
            return "on the roadside"
        place = parts[0]
        if place.lower().startswith("near "):
            place = place[5:].strip()
        city = parts[1] if len(parts) > 1 else ""
        if city:
            return f"near {place} in {city}"
        return f"near {place}" if place else "on the roadside"
    parts = _drop_region_parts(text.split(","))
    if not parts:
        return "on the roadside"
    return f"on {parts[0]}"


def render_sms(lead: Lead) -> str:
    car = _clip(lead.car_label() or "car", 40)
    location_slot = sale_location_phrase(lead.location)
    body = SMS_DRAFT.format(car=car, location_slot=location_slot)
    if len(body) <= SMS_MAX_CHARS:
        return body
    for size in (24, 16, 12, 8):
        body = SMS_DRAFT.format(
            car=_clip(car, size),
            location_slot=_clip(location_slot, size),
        )
        if len(body) <= SMS_MAX_CHARS:
            return body
    fallback = (
        "Hi, this is Alexandra with Panamerica Auto in Cape Coral. "
        "Saw your car for sale — rent it out instead of selling? "
        "We handle listing, guests, cleaning, maintenance. "
        "Owner-exit and plans: https://www.panamericafleet.com/plans"
    )
    return fallback[:SMS_MAX_CHARS]


def render_voice_task(lead: Lead) -> str:
    car = lead.car_label()
    location_slot = sale_location_phrase(lead.location)
    return (
        "You are Alexandra calling from Panamerica Auto in Cape Coral. "
        f"You saw their {car} listed for sale {location_slot}. "
        "Pitch: instead of selling, owners host the car in our fleet — "
        "we handle listing, bookings, guests, cleaning, and maintenance; "
        "they earn monthly income off a car they would otherwise sell. "
        "We also have an owner-exit option where we take over the payments, "
        "and details on all our options are at www.panamericafleet.com/plans. "
        "Qualify: are you the owner? Open to a 10-minute chat with Chris? "
        "If interested, book a callback time and log it. "
        "If they decline or ask you to stop, apologize, end the call, and do not push. "
        "Do not mention Turo. Do not re-contact anyone who declined."
    )


def parse_inbound(text: str) -> dict[str, object]:
    raw = (text or "").strip()
    lowered = re_sub_ws(raw).lower()
    tokens = set(lowered.replace(",", " ").replace(".", " ").split())
    opt_out = bool(tokens & {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "unsub"})
    if "stop" in lowered and "opt" in lowered:
        opt_out = True
    declined = any(
        phrase in lowered
        for phrase in (
            "not interested",
            "no thanks",
            "no thank",
            "don't call",
            "do not call",
            "leave me alone",
            "wrong number",
            "sold the car",
            "already sold",
        )
    )
    if tokens == {"no"} or lowered in {"no.", "nope"}:
        declined = True
    interested = any(
        phrase in lowered
        for phrase in (
            "yes",
            "interested",
            "sure",
            "call me",
            "let's talk",
            "lets talk",
            "10 min",
            "10-min",
            "callback",
            "call back",
            "sounds good",
        )
    )
    if opt_out:
        declined = True
        interested = False
    return {
        "text": raw,
        "opt_out": opt_out,
        "declined": declined,
        "interested": interested and not declined,
    }


def re_sub_ws(value: str) -> str:
    return " ".join((value or "").split())


def _is_zip(token: str) -> bool:
    return bool(_ZIP_RE.match(token))


def _is_state_or_zip_only(part: str) -> bool:
    text = re_sub_ws(part).lower().rstrip(".")
    if not text:
        return True
    if text in _STATE_NAMES or text in _STATE_ABBR or _is_zip(text):
        return True
    tokens = [tok.rstrip(".,") for tok in text.split()]
    if not tokens:
        return True
    return all(
        _is_zip(tok) or tok in _STATE_ABBR or tok in _STATE_NAMES for tok in tokens
    )


def _strip_trailing_state_zip(part: str) -> str:
    tokens = re_sub_ws(part).split()
    while tokens:
        last = tokens[-1].rstrip(".,")
        if _is_zip(last) or last.lower() in _STATE_ABBR:
            tokens.pop()
            continue
        peeled = False
        for n in range(len(tokens), 0, -1):
            cand = " ".join(t.lower().rstrip(".,") for t in tokens[-n:])
            if cand in _STATE_NAMES and n < len(tokens):
                tokens = tokens[:-n]
                peeled = True
                break
        if peeled:
            continue
        break
    return " ".join(tokens)


def _drop_region_parts(parts: list[str]) -> list[str]:
    cleaned = [p.strip() for p in parts if p.strip()]
    while cleaned and _is_state_or_zip_only(cleaned[-1]):
        cleaned.pop()
    if cleaned:
        cleaned[-1] = _strip_trailing_state_zip(cleaned[-1])
        cleaned = [p for p in cleaned if p.strip()]
        while cleaned and _is_state_or_zip_only(cleaned[-1]):
            cleaned.pop()
    return cleaned


def _clip(value: str, n: int) -> str:
    text = re_sub_ws(value)
    if len(text) <= n:
        return text
    return text[: max(1, n - 1)].rstrip() + "…"
