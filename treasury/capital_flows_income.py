"""Per-card YNAB inflow totals for the Capital Flows map (#1133).

An income card in ``investment/capital_flows.json`` opts into a YNAB feed with
``ynab_category`` ({"group": ..., "name": ...}). Matching is case-insensitive
on the resolved category (subtransactions included) and uses the same
countable-row filter as Cash Streams (transfers, starting balances, reconcile
and card-payment rows are dropped).

No double counting: a row is assigned to at most one card. Rows in a mapped
category go to that card first. Only the remaining rows are offered to the
payee/category needle matcher (``classify_income_source``) for needle cards
such as Turo. So a vehicle owner's payout categorized ``Income > Panamerica
Auto`` never also counts on the Turo card, even if the payee says Turo.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from treasury.cash_streams import _iter_countable, category_lookup
from treasury.income_sources import classify_income_source

DEFAULT_WINDOW_DAYS = 30


def category_map(income_sources: Iterable[Mapping[str, Any]]) -> Dict[tuple, str]:
    """(group_lower, name_lower) -> card id for cards with ``ynab_category``."""
    out: Dict[tuple, str] = {}
    for src in income_sources or []:
        if not isinstance(src, Mapping):
            continue
        cat = src.get("ynab_category")
        if not isinstance(cat, Mapping) or not cat.get("name"):
            continue
        key = (
            str(cat.get("group") or "").strip().lower(),
            str(cat["name"]).strip().lower(),
        )
        out[key] = str(src.get("id"))
    return out


def _card_for_category(group: str, name: str, cmap: Mapping[tuple, str]) -> Optional[str]:
    g, n = group.strip().lower(), name.strip().lower()
    if (g, n) in cmap:
        return cmap[(g, n)]
    # A card with no group pinned matches the category name in any group.
    return cmap.get(("", n))


def income_by_card(
    transactions: Sequence[Dict[str, Any]],
    *,
    income_sources: Iterable[Mapping[str, Any]],
    category_groups: Optional[Sequence[Dict[str, Any]]] = None,
    on_budget_ids: Optional[Iterable[str]] = None,
    tracking_outflow_ids: Optional[Iterable[str]] = None,
    today: Optional[date] = None,
    days: int = DEFAULT_WINDOW_DAYS,
) -> Dict[str, Any]:
    """Inflow totals per Capital Flows income card over the last ``days``."""
    sources = [s for s in income_sources or [] if isinstance(s, Mapping)]
    card_ids = {str(s.get("id")) for s in sources}
    cmap = category_map(sources)
    end = today or date.today()
    start = end - timedelta(days=max(int(days), 1) - 1)
    totals: Dict[str, float] = {cid: 0.0 for cid in card_ids}
    counts: Dict[str, int] = {cid: 0 for cid in card_ids}
    for row in _iter_countable(
        transactions or [],
        start=start,
        end=end,
        on_budget_ids=set(on_budget_ids) if on_budget_ids is not None else None,
        lookup=category_lookup(category_groups or []),
        tracking_outflow_ids=set(tracking_outflow_ids or ()),
    ):
        amt = float(row.get("amount") or 0)
        if amt <= 0:
            continue
        card = _card_for_category(row.get("group") or "", row.get("category") or "", cmap)
        if card is None:
            hit = classify_income_source(row.get("payee"), row.get("category"))
            card = hit if hit in card_ids and hit not in cmap.values() else None
        if card is None:
            continue
        totals[card] = round(totals[card] + amt, 2)
        counts[card] += 1
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "totals": totals,
        "counts": counts,
    }
