"""Synthesize coordinated top-level priorities / action plan."""

from __future__ import annotations

from typing import Any


_PRIORITY_SCORE = {
    "critical": 100,
    "high": 80,
    "medium": 50,
    "low": 25,
}


def _score_priority_label(label: str) -> int:
    return _PRIORITY_SCORE.get((label or "medium").lower(), 50)


def synthesize_priorities(
    *,
    today_items: list[str] | None = None,
    initiatives: list[dict[str, Any]] | None = None,
    backlog_active: list[dict[str, Any]] | None = None,
    finance_actions: list[str] | None = None,
    fitness_summary: str | None = None,
    holistic_targets: list[str] | None = None,
    synergies: list[dict[str, Any]] | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Merge strategy/today, initiatives, backlog, and domain signals into ranked priorities.

    Pure function — no I/O. Returns ordered action-plan items for the orchestra UI.
    """
    today_items = today_items or []
    initiatives = initiatives or []
    backlog_active = backlog_active or []
    finance_actions = finance_actions or []
    holistic_targets = holistic_targets or []
    synergies = synergies or []

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(
        *,
        title: str,
        source: str,
        domains: list[str],
        priority: str = "medium",
        rationale: str = "",
        kind: str = "action",
        score_boost: int = 0,
    ) -> None:
        key = re_normalize(title)
        if not key or key in seen:
            return
        seen.add(key)
        base = _score_priority_label(priority)
        items.append(
            {
                "id": f"pri-{len(items)+1}",
                "title": title.strip()[:240],
                "source": source,
                "domains": domains,
                "priority": priority,
                "rationale": rationale[:400],
                "kind": kind,
                "score": base + score_boost,
            }
        )

    def re_normalize(t: str) -> str:
        return " ".join((t or "").lower().split())[:160]

    # 1) Explicit today.md open checklist — highest coordination signal
    for i, line in enumerate(today_items):
        # strip markdown bold markers for cleaner title
        clean = line.replace("**", "").strip()
        add(
            title=clean,
            source="strategy/today.md",
            domains=["strategy"],
            priority="high" if i < 3 else "medium",
            rationale="Open item on today's micro plan.",
            kind="today",
            score_boost=40 - min(i, 5) * 3,
        )
        # tag domains by keywords
        low = clean.lower()
        if any(k in low for k in ("fitness", "health", "workout", "recovery", "sleep")):
            items[-1]["domains"] = sorted(set(items[-1]["domains"] + ["fitness", "holistic"]))
        if any(k in low for k in ("investment", "dca", "bitcoin", "treasury", "wealth")):
            items[-1]["domains"] = sorted(set(items[-1]["domains"] + ["finance"]))
        if any(k in low for k in ("automation", "command center", "initiative", "ai", "agent")):
            items[-1]["domains"] = sorted(set(items[-1]["domains"] + ["workflow"]))

    # 2) Active initiatives with next_action
    for init in initiatives:
        status = (init.get("status") or "").lower()
        if status in ("done", "cancelled", "archived"):
            continue
        na = (init.get("next_action") or "").strip()
        title = init.get("title") or init.get("id") or "Initiative"
        impact = (init.get("priority_impact") or "medium").lower()
        if impact not in _PRIORITY_SCORE:
            impact = "high" if status == "active" else "medium"
        if na:
            add(
                title=f"{title}: {na}",
                source=f"initiatives/{init.get('id', 'unknown')}",
                domains=["strategy", "workflow"],
                priority=impact if impact in _PRIORITY_SCORE else "high",
                rationale=f"Initiative status={init.get('status')}; bets={init.get('linked_bets') or []}",
                kind="initiative",
                score_boost=25 if status == "active" else 15,
            )
        else:
            add(
                title=f"Define next_action for: {title}",
                source=f"initiatives/{init.get('id', 'unknown')}",
                domains=["strategy"],
                priority="medium",
                rationale="Initiative lacks a concrete next_action.",
                kind="initiative",
                score_boost=5,
            )

    # 3) Backlog active items (top priorities)
    for bi in backlog_active[:8]:
        pri = (bi.get("priority") or "medium").lower()
        title = bi.get("title") or bi.get("notes") or "Backlog item"
        note = (bi.get("notes") or "").strip()
        display = title if not note or note.lower() in title.lower() else f"{title} — {note[:120]}"
        add(
            title=display,
            source="ops/backlog",
            domains=["workflow"] + ([bi["area"]] if bi.get("area") else []),
            priority=pri if pri in _PRIORITY_SCORE else "medium",
            rationale=f"Backlog status={bi.get('status')}",
            kind="backlog",
            score_boost=10,
        )

    # 4) Finance / treasury actions
    for j, fa in enumerate(finance_actions[:5]):
        add(
            title=fa,
            source="treasury evaluation",
            domains=["finance"],
            priority="high" if j < 2 else "medium",
            rationale="Open treasury / financial-command action.",
            kind="finance",
            score_boost=20 - j * 2,
        )

    # 5) Fitness maintenance if summary present
    if fitness_summary:
        add(
            title=f"Maintain fitness enabler — {fitness_summary[:100]}",
            source="fitness",
            domains=["fitness", "holistic"],
            priority="medium",
            rationale="Health/vitality underpins deep work on thematic bets.",
            kind="fitness",
            score_boost=8,
        )

    # 6) Holistic targets as time commitments
    for t in holistic_targets[:4]:
        add(
            title=f"Protect time block: {t}",
            source="holistic",
            domains=["holistic"],
            priority="medium",
            rationale="Time-allocator target / KPI.",
            kind="time",
            score_boost=5,
        )

    # 7) High-strength synergies become coordination priorities
    for syn in synergies:
        if (syn.get("strength") or "") != "high":
            continue
        add(
            title=f"Coordinate: {syn.get('title') or 'cross-domain synergy'}",
            source="orchestra/synergies",
            domains=list(syn.get("domains") or []),
            priority="high",
            rationale=syn.get("detail") or "",
            kind="synergy",
            score_boost=18,
        )

    items.sort(key=lambda x: (-int(x.get("score") or 0), x.get("id") or ""))
    # re-number ranks
    out = []
    for i, it in enumerate(items[:limit]):
        row = dict(it)
        row["rank"] = i + 1
        out.append(row)
    return out
