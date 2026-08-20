"""Automated recommended-next-actions synthesis for Orchestra.

Merges attention (hygiene/blockers), priorities (work), and synergies
(cross-domain leverage) into one ranked list so operators do not need to
manually weigh those streams. Pure functions — no I/O.
"""

from __future__ import annotations

from typing import Any

try:
    from .pulse import backlog_feeds_recs, is_example_today_line
except ImportError:  # unittest path insert
    from pulse import backlog_feeds_recs, is_example_today_line


_URGENCY_SCORE = {
    "critical": 100,
    "high": 80,
    "medium": 50,
    "low": 25,
    "info": 10,
}

# Attention kinds that block or degrade trust before "do work" items
_HYGIENE_KINDS = frozenset(
    {
        "domain_missing",
        "domain_partial",
        "stale_source",
        "finance_stress",
        "empty_today",
        "overloaded_today",
        "servers_offline",
    }
)


def _norm_key(title: str) -> str:
    return " ".join((title or "").lower().split())[:160]


def _urgency_from_priority_label(label: str) -> str:
    lab = (label or "medium").lower()
    if lab in _URGENCY_SCORE:
        return lab
    return "medium"


def _synergy_action(syn: dict[str, Any]) -> str:
    """Turn a detected synergy into a concrete recommended action."""
    title = (syn.get("title") or "cross-domain link").strip()
    domains = [str(d) for d in (syn.get("domains") or []) if d]
    doms = ", ".join(domains[:4]) if domains else "related domains"
    kind = (syn.get("kind") or "connection").lower()
    if kind == "synergy":
        return f"Protect and execute across {doms}: treat “{title[:100]}” as one coordinated move today."
    if kind == "overlap":
        return f"Align work on shared theme across {doms} — pick one next step that advances all of them."
    if kind == "relationship":
        return f"Follow the link across {doms}: schedule or complete the concrete task that closes this relationship."
    return f"Coordinate {doms}: act on “{title[:100]}” so the domains reinforce each other."


def _priority_action(pri: dict[str, Any]) -> str:
    title = (pri.get("title") or "priority item").strip()
    kind = (pri.get("kind") or "action").lower()
    if kind == "today":
        return f"Do now (from today’s plan): {title[:180]}"
    if kind == "initiative":
        return f"Advance initiative: {title[:180]}"
    if kind == "finance":
        return f"Clear treasury action: {title[:180]}"
    if kind == "backlog":
        return f"Ship or unblock backlog item: {title[:180]}"
    if kind == "fitness":
        return f"Protect energy: {title[:180]}"
    if kind == "time":
        return f"Honor time block: {title[:180]}"
    if kind == "iot":
        return f"Home systems: {title[:180]}"
    if kind == "synergy":
        return f"Coordinate: {title[:180]}"
    return f"Next action: {title[:180]}"


def _attention_action(att: dict[str, Any]) -> str:
    title = (att.get("title") or "attention item").strip()
    kind = (att.get("kind") or "").lower()
    if kind == "stale_source":
        return f"Refresh data before deciding — {title[:140]}"
    if kind == "domain_missing":
        return f"Restore domain sources — {title[:140]}"
    if kind == "finance_stress":
        return f"Open financial-command and resolve stress — {title[:140]}"
    if kind == "empty_today":
        return "Update strategy/today.md with 2–5 open checklist items for the next 24–48h."
    if kind == "overloaded_today":
        return "Trim strategy/today.md to the highest-leverage 2–5 items."
    if kind == "bridge_backlog":
        return f"Allocate to day plan from Workflow — {title[:140]}"
    if kind == "servers_offline":
        return f"Launch subordinate UI only if needed — {title[:140]}"
    if kind == "top_priority":
        return f"Focus: {title[:160]}"
    if kind == "synergy":
        return f"Coordinate: {title[:160]}"
    return f"Address: {title[:160]}"


def synthesize_recommendations(
    *,
    domains: list[dict[str, Any]] | None = None,
    priorities: list[dict[str, Any]] | None = None,
    attention: list[dict[str, Any]] | None = None,
    synergies: list[dict[str, Any]] | None = None,
    bridge: dict[str, Any] | None = None,
    freshness: dict[str, Any] | None = None,
    limit: int = 8,
    focus_limit: int = 3,
) -> dict[str, Any]:
    """Build automated recommended actions from all orchestration streams.

    Processing order (scores decide final rank):
    1. Hygiene/blockers from attention (stale, missing, stress) — act first
    2. High-strength synergies → concrete coordinate actions
    3. Top priorities, boosted when they share domains with high synergies
    4. If no high synergies: fallback medium synergies + note
    5. Bridge candidates if still thin
    6. Thin-data fallback if almost nothing usable
    """
    domains = domains or []
    priorities = priorities or []
    attention = attention or []
    synergies = synergies or []
    bridge = bridge or {}
    freshness = freshness or {}

    high_syns = [s for s in synergies if (s.get("strength") or "") == "high"]
    medium_syns = [s for s in synergies if (s.get("strength") or "") == "medium"]
    used_high = bool(high_syns)

    workflow = next((d for d in domains if d.get("id") == "workflow"), None) or {}
    backlog = ((workflow.get("signals") or {}).get("backlog") or {}) if isinstance(workflow, dict) else {}
    freshness_sources = freshness.get("sources") if isinstance(freshness, dict) else None
    if isinstance(freshness_sources, list):
        bl_fresh = next(
            (s for s in freshness_sources if isinstance(s, dict) and s.get("id") == "backlog"),
            None,
        )
        if isinstance(bl_fresh, dict) and bl_fresh.get("as_of") and not backlog.get("updated_at"):
            backlog = {**backlog, "updated_at": bl_fresh.get("as_of")}
        if isinstance(bl_fresh, dict) and bl_fresh.get("stale"):
            backlog = {**backlog, "updated_at": backlog.get("updated_at") or bl_fresh.get("as_of")}
    allow_backlog_recs = backlog_feeds_recs(backlog)

    high_domain_set: set[str] = set()
    for s in high_syns:
        for d in s.get("domains") or []:
            high_domain_set.add(str(d))

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(
        *,
        title: str,
        action: str,
        why: str,
        urgency: str,
        kind: str,
        domains_involved: list[str],
        sources: list[str],
        score_boost: int = 0,
        related: dict[str, Any] | None = None,
    ) -> None:
        key = _norm_key(title) or _norm_key(action)
        if not key or key in seen:
            return
        # Soft de-dupe: skip if action text already covered
        act_key = _norm_key(action)
        if act_key and act_key in seen:
            return
        seen.add(key)
        if act_key:
            seen.add(act_key)
        base = _URGENCY_SCORE.get((urgency or "medium").lower(), 50)
        row: dict[str, Any] = {
            "id": f"rec-{len(items) + 1}",
            "title": (title or action)[:200].strip(),
            "action": (action or title)[:280].strip(),
            "why": (why or "")[:400],
            "urgency": urgency,
            "kind": kind,
            "domains": list(domains_involved or []),
            "sources": list(sources or []),
            "score": base + score_boost,
            "automated": True,
        }
        if related:
            row["related"] = related
        items.append(row)

    # --- 1) Hygiene / blockers from attention ---
    for att in attention:
        kind = (att.get("kind") or "").lower()
        sev = (att.get("severity") or "medium").lower()
        if kind not in _HYGIENE_KINDS and kind != "bridge_backlog":
            continue
        # Skip low-noise offline servers unless only signal
        if kind == "servers_offline" and sev in ("low", "info"):
            continue
        if kind == "bridge_backlog" and sev == "low":
            score_boost = 4
        elif kind in ("stale_source", "domain_missing", "finance_stress"):
            score_boost = 25
        elif kind in ("empty_today", "overloaded_today"):
            score_boost = 12
        else:
            score_boost = 8
        add(
            title=att.get("title") or "Attention item",
            action=_attention_action(att),
            why=att.get("detail") or "Automated hygiene signal from domain analysis.",
            urgency=sev if sev in _URGENCY_SCORE else "medium",
            kind="hygiene" if kind in _HYGIENE_KINDS else "bridge",
            domains_involved=list(att.get("domains") or []),
            sources=["attention", kind],
            score_boost=score_boost,
            related={"attention_id": att.get("id"), "attention_kind": kind},
        )

    # --- 2) High synergies → coordinate actions ---
    for i, syn in enumerate(high_syns[:6]):
        add(
            title=syn.get("title") or "High-strength synergy",
            action=_synergy_action(syn),
            why=(
                (syn.get("detail") or "")
                + " High-strength cross-domain leverage — prioritized automatically."
            ).strip(),
            urgency="high",
            kind="synergy",
            domains_involved=list(syn.get("domains") or []),
            sources=["synergy", "high"],
            score_boost=22 - min(i, 5) * 2,
            related={
                "synergy_id": syn.get("id"),
                "synergy_kind": syn.get("kind"),
                "strength": "high",
            },
        )

    # --- 3) Priorities (boost if domains overlap high synergies) ---
    for i, pri in enumerate(priorities[:10]):
        if is_example_today_line(pri.get("title")):
            continue
        if (pri.get("kind") or "").lower() == "backlog" and not allow_backlog_recs:
            continue
        if (pri.get("source") or "").startswith("ops/backlog") and not allow_backlog_recs:
            continue
        pdoms = [str(d) for d in (pri.get("domains") or [])]
        overlap = high_domain_set.intersection(pdoms)
        boost = 18 - min(i, 6) * 2
        if overlap:
            boost += 12
        urg = _urgency_from_priority_label(str(pri.get("priority") or "medium"))
        why_bits = [
            pri.get("rationale") or "",
            f"Source: {pri.get('source') or 'priorities'}.",
        ]
        if overlap:
            why_bits.append(
                f"Boosted: domains {', '.join(sorted(overlap))} also appear in high-strength synergies."
            )
        add(
            title=pri.get("title") or "Priority",
            action=_priority_action(pri),
            why=" ".join(w for w in why_bits if w)[:400],
            urgency=urg,
            kind="focus",
            domains_involved=pdoms,
            sources=["priority", str(pri.get("kind") or "action")],
            score_boost=boost,
            related={
                "priority_id": pri.get("id"),
                "priority_rank": pri.get("rank"),
                "priority_kind": pri.get("kind"),
            },
        )

    # --- 4) Fallback when no high synergies ---
    mode = "high_focus"
    deferred_note = ""
    if not used_high:
        mode = "fallback_medium"
        deferred_note = (
            "No high-strength synergies detected. Promoting medium synergies and "
            "top domain priorities so you still get automated next steps."
        )
        for i, syn in enumerate(medium_syns[:4]):
            add(
                title=syn.get("title") or "Medium synergy",
                action=_synergy_action(syn),
                why=(
                    (syn.get("detail") or "")
                    + " Fallback: no high-strength synergies available."
                ).strip(),
                urgency="medium",
                kind="fallback",
                domains_involved=list(syn.get("domains") or []),
                sources=["synergy", "medium", "fallback"],
                score_boost=10 - i,
                related={
                    "synergy_id": syn.get("id"),
                    "strength": "medium",
                    "fallback": True,
                },
            )

    # --- 5) Bridge fill if still sparse ---
    candidates = bridge.get("candidates") or []
    if not allow_backlog_recs:
        candidates = []
    if len(items) < max(3, focus_limit) and candidates:
        for c in candidates[:3]:
            if not isinstance(c, dict):
                continue
            title = c.get("title") or c.get("backlog_id") or "Backlog item"
            add(
                title=f"Allocate to day plan: {title}",
                action=(
                    f"From Workflow, send “{str(title)[:120]}” to today’s day plan "
                    f"(bridge candidate; status={c.get('status') or 'n/a'})."
                ),
                why="Automated day-bridge: active backlog not yet on the time allocator plan.",
                urgency="medium",
                kind="bridge",
                domains_involved=["workflow", "holistic"],
                sources=["bridge"],
                score_boost=6,
                related={"backlog_id": c.get("backlog_id")},
            )

    # --- 6) Thin data / empty workspace ---
    available_n = sum(1 for d in domains if d.get("available"))
    if len(items) < 2:
        mode = "thin_data"
        deferred_note = (
            "Limited signals on disk. Fill strategy/today.md, initiatives, backlog, "
            "or refresh treasury so Orchestra can synthesize stronger recommendations."
        )
        add(
            title="Seed orchestration inputs",
            action=(
                "Add open items to strategy/today.md, define initiative next_action fields, "
                "and ensure ops/backlog or a treasury snapshot exists."
            ),
            why=f"Only {available_n} domain(s) available; automated analysis needs more source signal.",
            urgency="medium",
            kind="hygiene",
            domains_involved=["strategy", "workflow"],
            sources=["recommendations", "thin_data"],
            score_boost=5,
        )

    # Hygiene-first mode if top item is hygiene and high urgency
    items.sort(key=lambda x: (-int(x.get("score") or 0), x.get("id") or ""))
    out_items: list[dict[str, Any]] = []
    for i, it in enumerate(items[:limit]):
        row = dict(it)
        row["rank"] = i + 1
        out_items.append(row)

    if out_items and out_items[0].get("kind") == "hygiene" and (
        out_items[0].get("urgency") or ""
    ) in ("critical", "high"):
        mode = "hygiene_first"

    focus = out_items[:focus_limit]
    summary = _build_summary(
        mode=mode,
        focus=focus,
        high_n=len(high_syns),
        med_n=len(medium_syns),
        hygiene_n=sum(1 for x in out_items if x.get("kind") == "hygiene"),
        stale_n=int(freshness.get("stale_count") or 0),
        deferred_note=deferred_note,
    )

    return {
        "summary": summary,
        "mode": mode,
        "deferred_note": deferred_note or None,
        "high_synergy_count": len(high_syns),
        "medium_synergy_count": len(medium_syns),
        "items": out_items,
        "focus": focus,
        "counts": {
            "items": len(out_items),
            "focus": len(focus),
            "hygiene": sum(1 for x in out_items if x.get("kind") == "hygiene"),
            "synergy": sum(1 for x in out_items if x.get("kind") == "synergy"),
            "focus_work": sum(1 for x in out_items if x.get("kind") == "focus"),
            "fallback": sum(1 for x in out_items if x.get("kind") == "fallback"),
        },
    }


def _build_summary(
    *,
    mode: str,
    focus: list[dict[str, Any]],
    high_n: int,
    med_n: int,
    hygiene_n: int,
    stale_n: int,
    deferred_note: str,
) -> str:
    """One-paragraph automated briefing for the operator."""
    parts: list[str] = []
    if mode == "hygiene_first":
        parts.append(
            "Automated analysis flags hygiene first (stale or missing data, stress, or plan issues) "
            "before deeper work."
        )
    elif mode == "fallback_medium":
        parts.append(
            f"No high-strength synergies ({high_n} high / {med_n} medium). "
            "Recommendations fall back to medium synergies plus top priorities."
        )
    elif mode == "thin_data":
        parts.append(
            "Sparse domain signals — recommendations are seeding steps until more on-disk data exists."
        )
    else:
        parts.append(
            f"Automated holistic pass: {high_n} high-strength synergies integrated with "
            "today/initiatives/backlog/treasury priorities."
        )
    if stale_n:
        parts.append(f"{stale_n} stale source(s) need refresh before finance/workflow decisions.")
    if hygiene_n and mode != "hygiene_first":
        parts.append(f"{hygiene_n} hygiene item(s) mixed into the list.")
    if focus:
        top = focus[0]
        parts.append(f"Top recommended action: {top.get('action') or top.get('title')}.")
    if deferred_note and mode == "fallback_medium":
        parts.append(deferred_note)
    return " ".join(parts)[:700]
