"""Daily synthesis: executive brief, world-state summary, strategy implications, watchlist."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from research.horizon import DOMAIN_LABELS, REQUIRED_DOMAINS
from research.horizon.regime import assess_regime, regime_brief_block
from research.horizon.world_state import query_nodes

# Mild boost so strategy-relevant nodes surface when scores are close (does not
# override a clear higher priority_score).
_STRATEGY_RANK_BOOST = 0.15


def _link_index(
    linkages: list[dict[str, Any]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Map world-state node id → strategy linkage records."""
    by_node: dict[str, list[dict[str, Any]]] = {}
    for link in linkages or []:
        nid = link.get("node_id")
        if not nid:
            continue
        by_node.setdefault(str(nid), []).append(link)
    return by_node


def _strategy_hits_for_node(
    node_id: Any,
    link_index: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for link in link_index.get(str(node_id or ""), []):
        hits.append(
            {
                "priority_id": link.get("priority_id"),
                "priority_label": link.get("priority_label")
                or link.get("priority_id"),
                "affinity": link.get("affinity"),
                "matched_keywords": list(link.get("matched_keywords") or [])[:6],
            }
        )
    # Highest affinity first
    hits.sort(key=lambda h: float(h.get("affinity") or 0), reverse=True)
    return hits


def _rank_items(
    nodes: list[dict[str, Any]],
    *,
    linkages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Rank nodes by priority_score, with mild strategy-link boost for ties."""
    link_index = _link_index(linkages)

    decorated: list[tuple[float, float, dict[str, Any], list[dict[str, Any]]]] = []
    for n in nodes:
        hits = _strategy_hits_for_node(n.get("id"), link_index)
        base = float(n.get("priority_score") or 0)
        best_aff = float(hits[0]["affinity"]) if hits else 0.0
        sort_key = base + (best_aff * _STRATEGY_RANK_BOOST if hits else 0.0)
        decorated.append((sort_key, base, n, hits))

    decorated.sort(key=lambda t: (-t[0], -(t[1]), str(t[2].get("title") or "")))

    ranked: list[dict[str, Any]] = []
    for i, (sort_key, base, n, hits) in enumerate(decorated, start=1):
        labels = [
            str(h.get("priority_label") or h.get("priority_id"))
            for h in hits[:4]
            if h.get("priority_label") or h.get("priority_id")
        ]
        rationale = (
            f"Ranked #{i} by priority_score={base} "
            f"(impact={n.get('impact')}, confidence={n.get('confidence')}"
            f"{', strategy_boost=' + str(round(sort_key - base, 3)) if hits else ''})."
        )
        if labels:
            rationale += f" Linked strategy: {', '.join(labels)}."
        ranked.append(
            {
                "rank": i,
                "id": n.get("id"),
                "title": n.get("title"),
                "domain": n.get("domain"),
                "impact": n.get("impact"),
                "confidence": n.get("confidence"),
                "priority_score": n.get("priority_score"),
                "rank_score": round(sort_key, 4),
                "facts": list(n.get("facts") or []),
                "interpretation": n.get("interpretation") or "",
                "priority_rationale": rationale,
                "strategy_links": hits[:4],
                "strategy_priorities": labels,
                "tags": list(n.get("tags") or []),
            }
        )
    return ranked


def build_executive_brief(
    state: dict[str, Any],
    *,
    top_n: int = 7,
    linkages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Pull a wider pool, then re-rank with strategy so linked items can surface.
    pool = max(top_n * 3, top_n)
    nodes = query_nodes(state, limit=pool)
    items = _rank_items(nodes, linkages=linkages)[:top_n]
    # Re-number ranks 1..n after slice
    for i, it in enumerate(items, start=1):
        it["rank"] = i
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
                "strategy_links": it.get("strategy_links") or [],
                "strategy_priorities": it.get("strategy_priorities") or [],
            }
        )
    return {
        "title": "Executive Brief",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version_id": state.get("version_id"),
        "items": bullets,
        "note": (
            "Facts are listed separately from interpretation; confidence is explicit. "
            "Items with strategy_links match personal priorities (bets/intent keywords)."
        ),
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


def build_watchlist(
    state: dict[str, Any],
    *,
    top_n: int = 12,
    linkages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    pool = max(top_n * 3, top_n)
    nodes = query_nodes(state, limit=pool)
    items = _rank_items(nodes, linkages=linkages)[:top_n]
    for i, it in enumerate(items, start=1):
        it["rank"] = i
    watch = []
    for it in items:
        why = it["priority_rationale"]
        labels = it.get("strategy_priorities") or []
        if labels:
            why = f"{why} Watch because it touches: {', '.join(labels)}."
        watch.append(
            {
                "rank": it["rank"],
                "variable_or_event": it["title"],
                "domain": it["domain"],
                "why_watch": why,
                "facts": it["facts"],
                "interpretation": it["interpretation"],
                "confidence": it["confidence"],
                "impact": it["impact"],
                "priority_score": it["priority_score"],
                "rank_score": it.get("rank_score"),
                "strategy_links": it.get("strategy_links") or [],
                "strategy_priorities": labels,
                "tags": it["tags"],
            }
        )
    return {
        "title": "Watchlist / Radar",
        "items": watch,
        "ranking_field": "priority_score+strategy_affinity",
        "note": (
            "Ordered by priority_score (impact × confidence × recency) with a mild "
            "boost when the node links to personal strategy priorities."
        ),
    }


def synthesize(
    state: dict[str, Any],
    strategy: dict[str, Any],
    linkages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build full synthesis document with required sections."""
    exec_brief = build_executive_brief(state, linkages=linkages)
    world_summary = build_world_state_summary(state)
    implications = build_strategy_implications(linkages, strategy)
    watchlist = build_watchlist(state, linkages=linkages)
    regime = state.get("regime") if isinstance(state.get("regime"), dict) else None
    if not regime:
        regime = assess_regime(state)
    return {
        "schema_version": 2,
        "version_id": state.get("version_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regime": regime,
        "regime_brief": regime_brief_block(regime),
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

    regime = brief.get("regime") or brief.get("regime_assessment") or {}
    if regime:
        lines.append("## 0. Regime Assessment")
        lines.append("")
        primary = regime.get("primary") or {}
        conf = regime.get("confidence_overall")
        if conf is None:
            conf = regime.get("confidence")
        try:
            pp = f"{float(primary.get('probability') or 0):.0%}"
        except (TypeError, ValueError):
            pp = str(primary.get("probability"))
        conf_s = f" · conf {conf}" if conf is not None else ""
        lines.append(f"**{primary.get('label') or 'Regime'}** ({pp}{conf_s})")
        lines.append("")
        if primary.get("summary"):
            lines.append(f"- **Base case:** {primary.get('summary')}")
        axes = regime.get("axes") or []
        if axes:
            lines.append("- **Axes (dominant):**")
            for ax in axes:
                if not isinstance(ax, dict):
                    continue
                if ax.get("states"):
                    dom = ax.get("dominant")
                    dom_label = dom
                    dom_p = None
                    for st in ax.get("states") or []:
                        if st.get("id") == dom:
                            dom_label = st.get("label") or dom
                            dom_p = st.get("probability")
                            break
                    try:
                        p_str = f" {float(dom_p):.0%}" if dom_p is not None else ""
                    except (TypeError, ValueError):
                        p_str = f" {dom_p}" if dom_p is not None else ""
                    lines.append(
                        f"  - {ax.get('label') or ax.get('id')}: "
                        f"**{dom_label}**{p_str} (axis conf {ax.get('confidence')})"
                    )
                else:
                    p = ax.get("probability")
                    try:
                        p_str = f" {float(p):.0%}" if p is not None else ""
                    except (TypeError, ValueError):
                        p_str = f" {p}" if p is not None else ""
                    lines.append(
                        f"  - {ax.get('label') or ax.get('id')}: "
                        f"**{ax.get('dominant_label') or ax.get('dominant')}**{p_str}"
                    )
        forces = regime.get("active_forces") or []
        if forces:
            lines.append("- **Active forces:**")
            for f in forces[:5]:
                lines.append(f"  - {f.get('force')} ({f.get('sign')})")
        scenarios = regime.get("scenarios") or []
        if scenarios:
            lines.append("- **Scenario tree:**")
            for s in scenarios[:5]:
                try:
                    sp = f"{float(s.get('probability') or 0):.0%}"
                except (TypeError, ValueError):
                    sp = str(s.get("probability"))
                lines.append(f"  - {sp} — {s.get('label')}")
        vintage = regime.get("data_vintage") or regime.get("coverage") or {}
        lines.append(
            f"- **Method:** {regime.get('method')} · "
            f"nodes={vintage.get('node_count') or vintage.get('node_total')}"
        )
        notes = regime.get("notes")
        if isinstance(notes, str) and notes:
            lines.append(f"- _Note:_ {notes}")
        elif isinstance(notes, list):
            for note in notes:
                lines.append(f"- _Note:_ {note}")
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
        sp = it.get("strategy_priorities") or []
        if sp:
            lines.append(f"- **Strategy:** {', '.join(str(x) for x in sp)}")
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
        sp = it.get("strategy_priorities") or []
        if sp:
            lines.append(f"   - Strategy: {', '.join(str(x) for x in sp)}")
    lines.append("")
    lines.append("---")
    lines.append(
        "_Horizon separates facts from interpretation and records confidence on judgments._"
    )
    lines.append("")
    return "\n".join(lines)
