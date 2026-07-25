"""Cross-dashboard deep links for Orchestra ↔ Workflow ↔ Time allocator.

Convention (query params, all optional):
  Orchestra  : ?rec=<rec_id>&backlog=<backlog_id>&next=1
  Workflow   : ?item=<backlog_id>&from=orchestra&orchestra_rec=<rec_id>
  Holistic   : ?item=<holistic_id>&backlog=<backlog_id>&from=orchestra&orchestra_rec=<rec_id>

Bidirectional: each surface can open the others with the same shared ids.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlencode

ORCHESTRA_BASE = "http://127.0.0.1:8790/"
WORKFLOW_BASE = "http://127.0.0.1:8765/"
HOLISTIC_BASE = "http://127.0.0.1:8770/"
FINANCE_BASE = "http://127.0.0.1:8000/financial-command/"
FITNESS_BASE = "http://127.0.0.1:8787/"
IOT_BASE = "http://127.0.0.1:8780/"


def _url(base: str, params: dict[str, Any]) -> str:
    clean = {k: str(v) for k, v in params.items() if v is not None and str(v) != ""}
    if not clean:
        return base
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}{urlencode(clean)}"

def orchestra_url(
    *,
    rec_id: Optional[str] = None,
    backlog_id: Optional[str] = None,
    next_only: bool = False,
) -> str:
    params: dict[str, Any] = {}
    if rec_id:
        params["rec"] = rec_id
    if backlog_id:
        params["backlog"] = backlog_id
    if next_only:
        params["next"] = "1"
    return _url(ORCHESTRA_BASE, params)


def workflow_url(
    *,
    backlog_id: Optional[str] = None,
    orchestra_rec: Optional[str] = None,
    from_surface: str = "orchestra",
) -> str:
    return _url(
        WORKFLOW_BASE,
        {
            "item": backlog_id,
            "from": from_surface if (backlog_id or orchestra_rec) else None,
            "orchestra_rec": orchestra_rec,
        },
    )


def holistic_url(
    *,
    item_id: Optional[str] = None,
    backlog_id: Optional[str] = None,
    orchestra_rec: Optional[str] = None,
    from_surface: str = "orchestra",
) -> str:
    return _url(
        HOLISTIC_BASE,
        {
            "item": item_id,
            "backlog": backlog_id,
            "from": from_surface if (item_id or backlog_id or orchestra_rec) else None,
            "orchestra_rec": orchestra_rec,
        },
    )


def domain_dashboard_url(domain: str) -> Optional[str]:
    return {
        "workflow": WORKFLOW_BASE,
        "holistic": HOLISTIC_BASE,
        "finance": FINANCE_BASE,
        "fitness": FITNESS_BASE,
        "iot": IOT_BASE,
        "strategy": ORCHESTRA_BASE,  # strategy is files; land on orchestra
    }.get(domain)


def attach_links_to_recommendation(
    item: dict[str, Any],
    *,
    backlog_by_id: dict[str, dict[str, Any]] | None = None,
    holistic_by_backlog: dict[str, dict[str, Any]] | None = None,
    title_to_backlog: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Mutate/return recommendation with trace + bidirectional deep links."""
    backlog_by_id = backlog_by_id or {}
    holistic_by_backlog = holistic_by_backlog or {}
    title_to_backlog = title_to_backlog or {}

    related = dict(item.get("related") or {})
    backlog_id = related.get("backlog_id")
    if not backlog_id and related.get("priority_kind") == "backlog":
        # try title match
        tkey = " ".join((item.get("title") or "").lower().split())
        backlog_id = title_to_backlog.get(tkey)
    if not backlog_id:
        # sources may include backlog via title in action
        tkey = " ".join((item.get("title") or "").lower().split())
        backlog_id = title_to_backlog.get(tkey)

    holistic_item = None
    holistic_id = related.get("holistic_id")
    if backlog_id and backlog_id in holistic_by_backlog:
        holistic_item = holistic_by_backlog[backlog_id]
        holistic_id = holistic_item.get("id") or holistic_id

    rec_id = item.get("id")
    domains = [str(d) for d in (item.get("domains") or [])]

    links: dict[str, Any] = {
        "orchestra": orchestra_url(rec_id=rec_id, backlog_id=backlog_id, next_only=False),
        "orchestra_next": orchestra_url(rec_id=rec_id, backlog_id=backlog_id, next_only=True),
    }

    if backlog_id or "workflow" in domains or item.get("kind") in ("bridge", "focus"):
        if backlog_id or "workflow" in domains or item.get("kind") == "bridge":
            links["workflow"] = workflow_url(
                backlog_id=backlog_id, orchestra_rec=rec_id
            )

    if backlog_id or holistic_id or "holistic" in domains or item.get("kind") == "bridge":
        links["holistic"] = holistic_url(
            item_id=holistic_id, backlog_id=backlog_id, orchestra_rec=rec_id
        )

    # Domain dashboards for non-workflow focus
    for d in domains:
        u = domain_dashboard_url(d)
        if u and d not in ("workflow", "holistic", "strategy"):
            links[d] = u

    if "finance" in domains or item.get("kind") == "hygiene" and "finance" in domains:
        links.setdefault("finance", FINANCE_BASE)

    bl_meta = backlog_by_id.get(str(backlog_id)) if backlog_id else None
    trace = {
        "backlog_id": backlog_id,
        "holistic_id": holistic_id,
        "backlog_title": (bl_meta or {}).get("title") if bl_meta else None,
        "holistic_title": (holistic_item or {}).get("title") if holistic_item else None,
        "press_rank": (bl_meta or {}).get("press_rank") if bl_meta else related.get("press_rank"),
        "schedule_slot": (bl_meta or {}).get("schedule_slot")
        if bl_meta
        else related.get("schedule_slot"),
        "linked_on_day_plan": bool(holistic_id),
    }

    # Enrich related
    if backlog_id:
        related["backlog_id"] = backlog_id
    if holistic_id:
        related["holistic_id"] = holistic_id

    out = dict(item)
    out["related"] = related
    out["trace"] = trace
    out["links"] = links
    return out


def build_link_indexes(
    backlog_active: list[dict[str, Any]] | None = None,
    holistic_linked: list[dict[str, Any]] | None = None,
    bridge_candidates: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]]:
    """Build lookup maps for attach_links_to_recommendation."""
    backlog_by_id: dict[str, dict[str, Any]] = {}
    title_to_backlog: dict[str, str] = {}
    for bi in backlog_active or []:
        if not isinstance(bi, dict):
            continue
        bid = str(bi.get("id") or "")
        if not bid:
            continue
        backlog_by_id[bid] = bi
        tkey = " ".join((bi.get("title") or "").lower().split())
        if tkey:
            title_to_backlog[tkey] = bid
    for c in bridge_candidates or []:
        if not isinstance(c, dict):
            continue
        bid = str(c.get("backlog_id") or c.get("id") or "")
        if bid and bid not in backlog_by_id:
            backlog_by_id[bid] = c
            tkey = " ".join((c.get("title") or "").lower().split())
            if tkey:
                title_to_backlog[tkey] = bid

    holistic_by_backlog: dict[str, dict[str, Any]] = {}
    for h in holistic_linked or []:
        if not isinstance(h, dict):
            continue
        bid = str(h.get("backlog_id") or "")
        if bid:
            holistic_by_backlog[bid] = h
    return backlog_by_id, holistic_by_backlog, title_to_backlog
