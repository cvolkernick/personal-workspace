"""Daily synthesis: executive brief, world-state summary, strategy implications, watchlist."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from research.horizon import DOMAIN_LABELS, REQUIRED_DOMAINS
from research.horizon.world_state import query_nodes


def _rank_items(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for i, n in enumerate(nodes, start=1):
        ranked.append(
            {
                "rank": i,
                "id": n.get("id"),
                "title": n.get("title"),
                "domain": n.get("domain"),
                "impact": n.get("impact"),
                "confidence": n.get("confidence"),
                "priority_score": n.get("priority_score"),
                "facts": list(n.get("facts") or []),
                "interpretation": n.get("interpretation") or "",
                "priority_rationale": (
                    f"Ranked #{i} by priority_score={n.get('priority_score')} "
                    f"(impact={n.get('impact')}, confidence={n.get('confidence')})."
                ),
                "tags": list(n.get("tags") or []),
            }
        )
    return ranked


def build_executive_brief(state: dict[str, Any], *, top_n: int = 7) -> dict[str, Any]:
    nodes = query_nodes(state, limit=top_n)
    items = _rank_items(nodes)
    bullets = []
    for it in items:
        fact0 = (it["facts"][0] if it["facts"] else it["title"])
        bullets.append(
            {
                "rank": it["rank"],
                "title": it["title"],
                "domain": it["domain"],
                "fact": fact0,
                "interpretation": it["interpretation"],
                "confidence": it["confidence"],
                "priority_rationale": it["priority_rationale"],
            }
        )
    return {
        "title": "Executive Brief",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version_id": state.get("version_id"),
        "items": bullets,
        "note": "Facts are listed separately from interpretation; confidence is explicit.",
    }


def build_world_state_summary(state: dict[str, Any]) -> dict[str, Any]:
    domains_out: dict[str, Any] = {}
    for d in REQUIRED_DOMAINS:
        bucket = (state.get("domains") or {}).get(d) or {}
        nodes = bucket.get("nodes") or []
        domains_out[d] = {
            "label": bucket.get("label") or DOMAIN_LABELS.get(d, d),
            "summary": bucket.get("summary") or "",
            "node_count": len(nodes),
            "top_nodes": [
                {
                    "id": n.get("id"),
                    "title": n.get("title"),
                    "facts": list(n.get("facts") or [])[:2],
                    "interpretation": n.get("interpretation") or "",
                    "confidence": n.get("confidence"),
                    "impact": n.get("impact"),
                    "priority_score": n.get("priority_score"),
                }
                for n in nodes[:3]
            ],
        }
    return {
        "title": "Current World State",
        "version_id": state.get("version_id"),
        "updated_at": state.get("updated_at"),
        "domains": domains_out,
        "edges_count": len(state.get("edges") or []),
        "meta": state.get("meta") or {},
    }


def build_strategy_implications(
    linkages: list[dict[str, Any]],
    strategy: dict[str, Any],
    *,
    per_priority: int = 4,
) -> dict[str, Any]:
    by_priority: dict[str, list[dict[str, Any]]] = {}
    for link in linkages:
        pid = str(link.get("priority_id") or "unknown")
        by_priority.setdefault(pid, []).append(link)

    sections: list[dict[str, Any]] = []
    # Preserve strategy priority order
    order = [p.get("id") for p in (strategy.get("priorities") or [])]
    for pid in order:
        items = by_priority.get(str(pid), [])[:per_priority]
        if not items:
            continue
        label = items[0].get("priority_label") or pid
        sections.append(
            {
                "priority_id": pid,
                "priority_label": label,
                "items": [
                    {
                        "node_id": it.get("node_id"),
                        "title": it.get("node_title"),
                        "domain": it.get("domain"),
                        "affinity": it.get("affinity"),
                        "facts": it.get("facts") or [],
                        "interpretation": it.get("interpretation") or "",
                        "confidence": it.get("confidence"),
                        "rationale": it.get("rationale"),
                        "matched_keywords": it.get("matched_keywords") or [],
                    }
                    for it in items
                ],
            }
        )

    return {
        "title": "Implications for My Strategy",
        "strategy_paths": strategy.get("paths") or {},
        "paths_exist": strategy.get("paths_exist") or {},
        "thematic_bets": strategy.get("thematic_bets") or [],
        "intent_accomplishing": (strategy.get("intent") or {}).get("accomplishing"),
        "positions_symbols": strategy.get("positions_symbols") or [],
        "sections": sections,
        "linkage_count": len(linkages),
    }


def build_watchlist(state: dict[str, Any], *, top_n: int = 12) -> dict[str, Any]:
    nodes = query_nodes(state, limit=top_n)
    items = _rank_items(nodes)
    watch = []
    for it in items:
        watch.append(
            {
                "rank": it["rank"],
                "variable_or_event": it["title"],
                "domain": it["domain"],
                "why_watch": it["priority_rationale"],
                "facts": it["facts"],
                "interpretation": it["interpretation"],
                "confidence": it["confidence"],
                "impact": it["impact"],
                "priority_score": it["priority_score"],
                "tags": it["tags"],
            }
        )
    return {
        "title": "Watchlist / Radar",
        "items": watch,
        "ranking_field": "priority_score",
        "note": "Ordered by priority_score (impact × confidence × recency).",
    }


def synthesize(
    state: dict[str, Any],
    strategy: dict[str, Any],
    linkages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build full synthesis document with required sections."""
    exec_brief = build_executive_brief(state)
    world_summary = build_world_state_summary(state)
    implications = build_strategy_implications(linkages, strategy)
    watchlist = build_watchlist(state)
    return {
        "schema_version": 1,
        "version_id": state.get("version_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "executive_brief": exec_brief,
        "current_world_state": world_summary,
        "implications_for_my_strategy": implications,
        "watchlist": watchlist,
    }


def render_markdown(brief: dict[str, Any]) -> str:
    """Human-readable daily brief."""
    lines: list[str] = []
    vid = brief.get("version_id") or ""
    lines.append(f"# Horizon Daily Brief — {vid}")
    lines.append("")
    lines.append(f"_Generated: {brief.get('generated_at')}_")
    lines.append("")

    eb = brief.get("executive_brief") or {}
    lines.append("## 1. Executive Brief")
    lines.append("")
    for it in eb.get("items") or []:
        lines.append(
            f"### {it.get('rank')}. [{it.get('domain')}] {it.get('title')}"
        )
        lines.append(f"- **Fact:** {it.get('fact')}")
        if it.get("interpretation"):
            lines.append(f"- **Interpretation:** {it.get('interpretation')}")
        lines.append(f"- **Confidence:** {it.get('confidence')}")
        lines.append(f"- **Why ranked:** {it.get('priority_rationale')}")
        lines.append("")

    ws = brief.get("current_world_state") or {}
    lines.append("## 2. Current World State")
    lines.append("")
    for d, bucket in (ws.get("domains") or {}).items():
        label = bucket.get("label") or d
        lines.append(f"### {label}")
        lines.append(f"- {bucket.get('summary')}")
        for n in bucket.get("top_nodes") or []:
            lines.append(
                f"  - **{n.get('title')}** "
                f"(impact={n.get('impact')}, conf={n.get('confidence')})"
            )
            for f in (n.get("facts") or [])[:1]:
                lines.append(f"    - Fact: {f}")
            if n.get("interpretation"):
                lines.append(f"    - Interpretation: {n.get('interpretation')}")
        lines.append("")

    impl = brief.get("implications_for_my_strategy") or {}
    lines.append("## 3. Implications for My Strategy")
    lines.append("")
    if impl.get("intent_accomplishing"):
        lines.append(f"**Intent:** {impl.get('intent_accomplishing')}")
        lines.append("")
    if impl.get("thematic_bets"):
        lines.append(
            "**Thematic bets:** " + ", ".join(str(b) for b in impl["thematic_bets"])
        )
        lines.append("")
    if impl.get("positions_symbols"):
        lines.append(
            "**Positions (symbols):** "
            + ", ".join(str(s) for s in impl["positions_symbols"][:20])
        )
        lines.append("")
    for sec in impl.get("sections") or []:
        lines.append(f"### {sec.get('priority_label')}")
        for it in sec.get("items") or []:
            lines.append(
                f"- **{it.get('title')}** (affinity={it.get('affinity')}, "
                f"conf={it.get('confidence')})"
            )
            for f in (it.get("facts") or [])[:1]:
                lines.append(f"  - Fact: {f}")
            if it.get("interpretation"):
                lines.append(f"  - Interpretation: {it.get('interpretation')}")
            lines.append(f"  - Rationale: {it.get('rationale')}")
        lines.append("")

    wl = brief.get("watchlist") or {}
    lines.append("## 4. Watchlist / Radar")
    lines.append("")
    lines.append(f"_Ranking: {wl.get('ranking_field')}_")
    lines.append("")
    for it in wl.get("items") or []:
        lines.append(
            f"{it.get('rank')}. **{it.get('variable_or_event')}** "
            f"[{it.get('domain')}] — score={it.get('priority_score')}, "
            f"conf={it.get('confidence')}"
        )
        lines.append(f"   - Why: {it.get('why_watch')}")
    lines.append("")
    lines.append("---")
    lines.append(
        "_Horizon separates facts from interpretation and records confidence on judgments._"
    )
    lines.append("")
    return "\n".join(lines)
