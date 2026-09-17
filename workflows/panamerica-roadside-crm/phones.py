"""US phone normalize + extract. Phone is the CRM dedupe key."""

from __future__ import annotations

import re

_DIGIT_RUN = re.compile(
    r"""
    (?:
        \+?1[\s.\-()] *
    )?
    \(?
    (\d{3})
    \)?
    [\s.\-]*
    (\d{3})
    [\s.\-]*
    (\d{4})
    """,
    re.VERBOSE,
)


def normalize_phone(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    if digits[0] in {"0", "1"}:
        return ""
    return digits


def extract_phones(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _DIGIT_RUN.finditer(text or ""):
        phone = normalize_phone("".join(match.groups()))
        if phone and phone not in seen:
            seen.add(phone)
            found.append(phone)
    return found


def first_phone(text: str) -> str:
    phones = extract_phones(text)
    return phones[0] if phones else ""


def e164(phone: str) -> str:
    digits = normalize_phone(phone)
    return f"+1{digits}" if digits else ""
