"""Chris-approved SMS + voice script (#855, 2026-09-20).

Copy is approved in-repo. Live send stays gated on
PANAMERICA_ROADSIDE_COPY_APPROVED=1 (first-send human gate).
"""

from __future__ import annotations

from models import SMS_MAX_CHARS, Lead

# Canonical copy from issue #855. Do not drift without a new Chris approval.
SMS_DRAFT = (
    "Hi, this is Alexandra with Panamerica Auto in Cape Coral. "
    "I saw your {car} for sale on {road} — have you considered renting it out "
    "instead of selling? We manage cars for owners and handle everything: "
    "listing, guests, cleaning, maintenance. We also have an owner-exit option "
    "where we take over the payments if you'd rather move on. You can find details "
    "on our available options at https://www.panamericafleet.com/plans. Worth a 10-min chat?"
)

# Inbound parse_inbound still honors STOP. Do not append this line to sends.
OPT_OUT_LINE = "Reply STOP to opt out."


def render_sms(lead: Lead) -> str:
    car = _clip(lead.car_label() or "car", 40)
    road = _clip(lead.location or "the roadside", 40)
    body = SMS_DRAFT.format(car=car, road=road)
    if len(body) <= SMS_MAX_CHARS:
        return body
    for size in (24, 16, 12, 8):
        body = SMS_DRAFT.format(car=_clip(car, size), road=_clip(road, size))
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
    road = lead.location or "the roadside"
    return (
        "You are Alexandra calling from Panamerica Auto in Cape Coral. "
        f"You saw their {car} listed for sale on {road}. "
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


def _clip(value: str, n: int) -> str:
    text = re_sub_ws(value)
    if len(text) <= n:
        return text
    return text[: max(1, n - 1)].rstrip() + "…"
