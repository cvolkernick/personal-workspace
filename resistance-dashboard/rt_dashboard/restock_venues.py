"""Venue tags for FitDash restock — Walmart vs Costco vs other.

Restock SoT is FitDash (inventory suggestions / today.purchases), not
Google Tasks. Venue is tagged here so the cart job can auto-add.

Rules (#554):
  * Explicit ``venue`` on the row wins.
  * Named-exception Costco ids/names only (bulk cheapest-unit staples).
  * Prepared / restaurant / generic staple placeholders → ``other`` (park).
  * Remaining grocery staples → ``walmart``.
  * Do not invent brand picks. Search query is the ingredient name.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

VENUE_WALMART = "walmart"
VENUE_COSTCO = "costco"
VENUE_OTHER = "other"
VENUES = (VENUE_WALMART, VENUE_COSTCO, VENUE_OTHER)

# Costco bulk cheapest-unit named exceptions. Ids only — no brand SKUs.
COSTCO_IDS = frozenset(
    {
        "chicken-breast",
        "boneless-skinless-chicken-breast",
        "greek-yogurt",
        "nonfat-greek-yogurt",
        "brown-rice",
        "whey-protein",
        "chocolate-whey-protein",
        "oats",
        "eggs-whole",
    }
)
COSTCO_NAME_NEEDLES = (
    "chicken breast",
    "greek yogurt",
    "brown rice",
    "whey protein",
    "whole egg",
)

# Not a retailer grocery line — park honestly.
OTHER_IDS = frozenset(
    {
        "chicken-burrito-bowl",
        "double-cheeseburger",
        "sugar-free-vanilla-iced-coffee-large",
    }
)
OTHER_NAME_NEEDLES = (
    "burrito bowl",
    "cheeseburger",
    "iced coffee",
    "staples that fit",
    "high-protein staples",
    "pantry staples",
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(value: str) -> str:
    return _SLUG_RE.sub("-", (value or "").lower()).strip("-")


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def normalize_venue(value: Any) -> Optional[str]:
    raw = _norm(value)
    if raw in VENUES:
        return raw
    if raw in ("wm", "wally", "walmart.com"):
        return VENUE_WALMART
    if raw in ("costco.com", "costco wholesale"):
        return VENUE_COSTCO
    return None


def venue_for_item(item: Optional[dict]) -> str:
    """Walmart | Costco | other. Never invents a brand or a fourth retailer."""
    row = item if isinstance(item, dict) else {}
    explicit = normalize_venue(row.get("venue") or row.get("store") or row.get("retailer"))
    if explicit:
        return explicit
    iid = slug(str(row.get("id") or ""))
    name = _norm(row.get("name") or row.get("title") or "")
    if iid in OTHER_IDS or any(n in name for n in OTHER_NAME_NEEDLES):
        return VENUE_OTHER
    if iid in COSTCO_IDS or any(n in name for n in COSTCO_NAME_NEEDLES):
        return VENUE_COSTCO
    if not iid and not name:
        return VENUE_OTHER
    if name in ("staples", "groceries", "shopping"):
        return VENUE_OTHER
    return VENUE_WALMART


def tag_item(item: Optional[dict]) -> dict:
    """Copy of item with ``venue`` set. Leaves other fields alone."""
    row = dict(item) if isinstance(item, dict) else {}
    row["venue"] = venue_for_item(row)
    return row


def tag_items(items: Optional[Iterable[dict]]) -> List[dict]:
    out: List[dict] = []
    for raw in items or []:
        if isinstance(raw, dict):
            out.append(tag_item(raw))
    return out


def restock_line(item: dict) -> dict:
    """Stable restock line for agent/ops (venue-tagged, no GT)."""
    row = tag_item(item if isinstance(item, dict) else {})
    name = str(row.get("name") or "").strip()
    iid = str(row.get("id") or "").strip()
    action = str(row.get("action") or "restock").strip() or "restock"
    qty = row.get("suggested_qty") if isinstance(row.get("suggested_qty"), dict) else {}
    return {
        "id": iid or None,
        "name": name,
        "action": action,
        "venue": row["venue"],
        "reason": str(row.get("reason") or row.get("need") or ""),
        "suggested_qty": qty or None,
        "query": name,
    }


def group_by_venue(items: Optional[Iterable[dict]]) -> Dict[str, List[dict]]:
    grouped = {v: [] for v in VENUES}
    for line in items or []:
        if not isinstance(line, dict):
            continue
        tagged = restock_line(line) if "venue" not in line else tag_item(line)
        venue = tagged.get("venue") or VENUE_OTHER
        if venue not in grouped:
            venue = VENUE_OTHER
            tagged["venue"] = VENUE_OTHER
        grouped[venue].append(tagged)
    return grouped
