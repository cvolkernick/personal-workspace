"""Identity normalization. Phone is E.164, email lowercased, VIN uppercased."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_STOP = frozenset({"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "unsub"})


def digits_only(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def nanp_digits(value: object) -> str:
    """10-digit NANP, or empty. Leading country code 1 is stripped."""
    digits = digits_only(value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in {"0", "1"}:
        return ""
    return digits


def phone_e164(value: object) -> str:
    digits = nanp_digits(value)
    return f"+1{digits}" if digits else ""


def phone_key(value: object) -> str:
    """Suppression / FileStore key. Empty when the value is not a phone."""
    digits = nanp_digits(value)
    if digits:
        return "phone:" + digits
    raw = str(value or "").strip()
    if raw.lower().startswith("phone:"):
        return phone_key(raw.split(":", 1)[1])
    return ""


def email_norm(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text or not _EMAIL.fullmatch(text):
        return ""
    return text


def vin_norm(value: object) -> str:
    text = re.sub(r"[\s-]+", "", str(value or "")).upper()
    if len(text) < 6 or not re.fullmatch(r"[A-Z0-9]+", text):
        return ""
    return text


def listing_id_norm(value: object) -> str:
    return str(value or "").strip()


def folder_id_norm(value: object) -> str:
    return str(value or "").strip()


def is_stop_text(value: object) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    token = re.split(r"[\s,.:;!]+", text, maxsplit=1)[0]
    return token in _STOP
