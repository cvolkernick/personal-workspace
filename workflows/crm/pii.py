"""Scrub sensitive patterns from one-line summaries before they leave the CRM (#1097)."""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[number]"),  # card numbers
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[number]"),  # SSN
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (
        re.compile(
            r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][a-z]+\s+){1,3}"
            r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Ct|Court|Way|Pl|Place|Pkwy|Hwy|Cir|Ter|Trl)\b\.?",
        ),
        "[address]",
    ),
    (
        re.compile(
            r"\b(?:code|pin|passcode|lockbox|gate code|access code|password)\b[\s:#-]*(?:is\s+)?\w*\d\w*",
            re.IGNORECASE,
        ),
        "[code]",
    ),
    (re.compile(r"\b(?:plate|tag|license|licence|DL)\b[\s:#-]*(?:no\.?|number|is)?\s*[A-Z0-9-]{4,9}\b", re.IGNORECASE), "[id]"),
    (re.compile(r"\b(?=[A-Z0-9]{5,8}\b)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]+\b"), "[id]"),  # plates
    (re.compile(r"\+?1?[ .(-]*\d{3}[ .)-]*\d{3}[ .-]*\d{4}\b"), "[phone]"),
)

MAX_SUMMARY = 200


def scrub(text: object, limit: int = MAX_SUMMARY) -> str:
    out = " ".join(str(text or "").split())
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    if len(out) > limit:
        out = out[: limit - 1].rstrip() + "…"
    return out
