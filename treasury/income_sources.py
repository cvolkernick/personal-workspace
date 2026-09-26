"""Canonical Lyft / Grubhub / Turo income matcher.

The Cash Streams rolling chart classifies external inflows with
``classify_income_source``. The Monday forecast income drift check must
import this function too. A second copy will disagree on what counts.

Match is case-insensitive on payee and category. A needle hits only as a
whole token (bounded by anything that is not a letter or digit), so
"Lyft Inc" and "HW*GrubHub Holdings Inc." count and "Grubby" does not.
Needles are lyft, grubhub or grub, and turo. Payee is tried first, then
category. Within one field, first source wins: lyft, then grubhub, then turo.

This function does not apply transfer, starting-balance, or reconcile
exclusions. Callers that share the cash-streams filter apply those first,
then classify the remaining inflows.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

# Colors are the chart contract: Lyft pink, Grubhub orange, Turo gray.
# Gray is light enough to read on the Cash Streams dark panel.
INCOME_SOURCE_LINES: Tuple[Dict[str, object], ...] = (
    {
        "id": "lyft",
        "label": "Lyft",
        "color": "#ff69b4",
        "needles": ("lyft",),
    },
    {
        "id": "grubhub",
        "label": "Grubhub",
        "color": "#ff8c1a",
        "needles": ("grubhub", "grub"),
    },
    {
        "id": "turo",
        "label": "Turo",
        "color": "#b7c0c8",
        "needles": ("turo",),
    },
)


@lru_cache(maxsize=None)
def _token(needle: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])")


def income_source_ids() -> Tuple[str, ...]:
    return tuple(str(row["id"]) for row in INCOME_SOURCE_LINES)


def income_source_public() -> List[Dict[str, str]]:
    """Legend payload. Needles stay server-side."""
    out: List[Dict[str, str]] = []
    for row in INCOME_SOURCE_LINES:
        out.append(
            {
                "id": str(row["id"]),
                "label": str(row["label"]),
                "color": str(row["color"]),
            }
        )
    return out


def _match_field(blob: str) -> Optional[str]:
    folded = blob.casefold()
    if not folded.strip():
        return None
    for row in INCOME_SOURCE_LINES:
        needles = row["needles"]
        if not isinstance(needles, tuple):
            continue
        if any(_token(str(needle)).search(folded) for needle in needles):
            return str(row["id"])
    return None


def classify_income_source(payee: object = "", category: object = "") -> Optional[str]:
    """Return lyft, grubhub, or turo when payee or category matches. Else None."""
    for part in (payee, category):
        hit = _match_field(str(part or ""))
        if hit:
            return hit
    return None
