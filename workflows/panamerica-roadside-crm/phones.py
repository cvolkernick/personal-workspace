"""US phone normalize + extract. Phone is the CRM dedupe key.

GPS/decimal degree runs (e.g. 26.639011, -82.039046) are not phones.
Prefer dashed/parenthesized NANP over bare digit runs (#862).
"""

from __future__ import annotations

import re

PHONE_UNKNOWN = "UNKNOWN"

# Explicit NANP. Dotted form is exactly NNN.NNN.NNNN (not decimal degrees).
_FORMATTED = re.compile(
    r"""
    (?:(?:\+?1)[\s.\-]*)?
    (?:
        \(\d{3}\)[\s.\-]*\d{3}[\s.\-]*\d{4}
      | \d{3}-\d{3}-\d{4}
      | \d{3}\.\d{3}\.\d{4}
      | \d{3}\s+\d{3}\s+\d{4}
    )
    """,
    re.VERBOSE,
)

# Bare 10 or 11 (leading 1) digits, not touching more digits or a decimal point.
_BARE = re.compile(r"(?<![\d.])(1?\d{10})(?![\d.])")

# Decimal degrees / lat-long / DMS / plus-codes — blanked before bare scan.
_COORD_PAIR = re.compile(
    r"-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}"
)
_DECIMAL_DEG = re.compile(
    r"-?\d{1,3}\.\d{4,}(?:\s*°?\s*[NSEW])?",
    re.IGNORECASE,
)
_DMS = re.compile(
    r"""\d{1,3}\s*°\s*\d{1,2}\s*['′]\s*\d{1,2}(?:\.\d+)?\s*[\"″]?\s*[NSEW]?""",
    re.IGNORECASE | re.VERBOSE,
)
_PLUSCODE = re.compile(
    r"\b[2-9CFGHJMPQRVWX]{4,8}\+[2-9CFGHJMPQRVWX]{2,3}\b",
    re.IGNORECASE,
)


def normalize_phone(value: str) -> str:
    raw = (value or "").strip()
    if not raw or raw.upper() == PHONE_UNKNOWN:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    if digits[0] in {"0", "1"}:
        return ""
    return digits


def _strip_coordinates(text: str) -> str:
    text = _COORD_PAIR.sub(" ", text or "")
    text = _DECIMAL_DEG.sub(" ", text)
    text = _DMS.sub(" ", text)
    text = _PLUSCODE.sub(" ", text)
    return text


def extract_phones(text: str) -> list[str]:
    raw = text or ""
    found: list[str] = []
    seen: set[str] = set()

    def add(span: str) -> None:
        phone = normalize_phone(span)
        if phone and phone not in seen:
            seen.add(phone)
            found.append(phone)

    for match in _FORMATTED.finditer(raw):
        add(match.group(0))
    if found:
        return found

    cleaned = _strip_coordinates(raw)
    for match in _FORMATTED.finditer(cleaned):
        add(match.group(0))
    if found:
        return found

    for match in _BARE.finditer(cleaned):
        add(match.group(1))
    return found


def first_phone(text: str) -> str:
    """Dialable 10-digit NANP, or empty if none."""
    phones = extract_phones(text)
    return phones[0] if phones else ""


def phone_or_unknown(text: str) -> str:
    """UNKNOWN when no valid phone — never a GPS/decimal garbage number."""
    return first_phone(text) or PHONE_UNKNOWN


def e164(phone: str) -> str:
    digits = normalize_phone(phone)
    return f"+1{digits}" if digits else ""
