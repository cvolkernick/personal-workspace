"""Cross-domain connection / overlap / synergy detection."""

from __future__ import annotations

import re
from typing import Any

try:
    from .domains import THEME_KEYWORDS
except ImportError:
    from domains import THEME_KEYWORDS


def _contains_any(text: str, keywords: list[str]) -> list[str]:
    hits: list[str] = []
    low = text.lower()
    for kw in keywords:
        if kw.lower() in low:
            hits.append(kw)
    return hits


def _theme_hits(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for theme, kws in THEME_KEYWORDS.items():
        hits = _contains_any(text, kws)
        if hits:
            out[theme] = hits
    return out


def _domain_text(domain: dict[str, Any]) -> str:
    import json

    parts = [domain.get("label") or "", domain.get("summary") or ""]
    parts.append(json.dumps(domain.get("signals") or {}, default=str))
    return " ".join(parts).lower()


def detect_synergies(
    domains: list[dict[str, Any]],
    *,
    initiatives: list[dict[str, Any]] | None = None,
    today_items: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return first-class connection/overlap/synergy items across domains.

    Pure function: derives relationships from domain snapshots + strategy inputs.
    """
    by_id = {d["id"]: d for d in domains if d.get("id")}
    texts = {did: _domain_text(d) for did, d in by_id.items()}
    theme_by_domain: dict[str, dict[str, list[str]]] = {
        did: _theme_hits(txt) for did, txt in texts.items()
    }

    synergies: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def add(
        *,
        kind: str,
        title: str,
        domains_involved: list[str],
        detail: str,
        strength: str = "medium",
        evidence: list[str] | None = None,
    ) -> None:
        key = f"{kind}|{title}|{','.join(sorted(domains_involved))}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        synergies.append(
            {
                "id": f"syn-{len(synergies)+1}",
                "kind": kind,  # overlap | connection | relationship | synergy
                "title": title,
                "domains": domains_involved,
                "detail": detail,
                "strength": strength,
                "evidence": evidence or [],
            }
        )

    # 1) Theme co-occurrence across distinct domains
    theme_domains: dict[str, list[str]] = {}
    for did, themes in theme_by_domain.items():
        for theme in themes:
            theme_domains.setdefault(theme, []).append(did)
    for theme, doms in sorted(theme_domains.items()):
        unique = sorted(set(doms))
        if len(unique) < 2:
            continue
        hits_summary = []
        for d in unique:
            kws = theme_by_domain.get(d, {}).get(theme) or []
            if kws:
                hits_summary.append(f"{d}:{','.join(kws[:3])}")
        add(
            kind="overlap",
            title=f"Shared theme: {theme}",
            domains_involved=unique,
            detail=(
                f"Theme '{theme}' appears in {len(unique)} domains "
                f"({', '.join(unique)}). Coordinating these areas multiplies leverage."
            ),
            strength="high" if len(unique) >= 3 else "medium",
            evidence=hits_summary,
        )

    # 2) Initiative linked_bets → domain bridges
    initiatives = initiatives or []
    for init in initiatives:
        bets = init.get("linked_bets") or []
        title = init.get("title") or init.get("id") or "initiative"
        next_a = (init.get("next_action") or "").strip()
        status = (init.get("status") or "").lower()
        if status in ("done", "cancelled", "archived"):
            continue
        # AI/Autonomy initiatives bridge strategy ↔ workflow ↔ tooling
        bet_blob = " ".join(str(b) for b in bets).lower()
        if any(x in bet_blob for x in ("ai", "autonomy", "robotics")):
            add(
                kind="connection",
                title=f"Initiative advances AI/Autonomy: {title[:80]}",
                domains_involved=["strategy", "workflow"],
                detail=(
                    f"Active initiative linked to {bets}. "
                    + (f"Next: {next_a[:200]}" if next_a else "Define next_action.")
                ),
                strength="high" if (init.get("priority_impact") or "").lower() == "high" else "medium",
                evidence=[f"status={init.get('status')}", f"bets={bets}"],
            )
        if any(x in bet_blob for x in ("bitcoin", "energy", "investment")):
            add(
                kind="connection",
                title=f"Initiative ties to wealth bets: {title[:80]}",
                domains_involved=["strategy", "finance"],
                detail=f"Linked bets {bets} connect strategy execution to treasury/investment.",
                strength="medium",
                evidence=[next_a[:120]] if next_a else [],
            )

    # 3) Fitness as energy enabler for deep work (from bets + today language)
    today_items = today_items or []
    today_blob = " ".join(today_items).lower()
    fitness = by_id.get("fitness") or {}
    strategy = by_id.get("strategy") or {}
    if fitness.get("available") and (
        "fitness" in today_blob
        or "health" in today_blob
        or "workout" in today_blob
        or "energy" in today_blob
        or "enabler" in (strategy.get("summary") or "").lower()
    ):
        add(
            kind="synergy",
            title="Fitness enables deep work on high-conviction bets",
            domains_involved=["fitness", "strategy", "holistic"],
            detail=(
                "Today's plan and/or strategy treat fitness as an energy enabler. "
                "Protect workout/sleep targets so AI/Autonomy and wealth work stay sustainable."
            ),
            strength="high",
            evidence=(today_items[:3] if today_items else [])
            + [fitness.get("summary") or ""],
        )
    elif fitness.get("available") and by_id.get("holistic", {}).get("available"):
        # Holistic often has sleep/workout targets
        hol_sig = by_id["holistic"].get("signals") or {}
        targets = " ".join(str(t) for t in (hol_sig.get("targets") or [])).lower()
        if any(k in targets for k in ("sleep", "workout", "walk", "fitness")):
            add(
                kind="relationship",
                title="Time allocator protects health blocks",
                domains_involved=["holistic", "fitness"],
                detail=(
                    "Holistic targets include health-related blocks (sleep/workout/walk). "
                    "These reserve capacity for fitness execution."
                ),
                strength="medium",
                evidence=list(hol_sig.get("targets") or [])[:6],
            )

    # 4) Finance actions + investment open items ↔ Bitcoin/Energy bets
    finance = by_id.get("finance") or {}
    fin_sig = finance.get("signals") or {}
    action_titles = fin_sig.get("action_titles") or []
    if finance.get("available") and action_titles:
        add(
            kind="connection",
            title="Treasury actions support wealth / Bitcoin leg",
            domains_involved=["finance", "strategy"],
            detail=(
                "Treasury evaluation has open actions that protect liquidity and "
                "execution on the Bitcoin/wealth bets."
            ),
            strength="high" if len(action_titles) >= 2 else "medium",
            evidence=[str(a) for a in action_titles[:4]],
        )

    # 5) Workflow backlog items that mention other domains
    workflow = by_id.get("workflow") or {}
    backlog = (workflow.get("signals") or {}).get("backlog") or {}
    for item in backlog.get("active") or []:
        blob = f"{item.get('title','')} {item.get('notes','')} {item.get('area','')}".lower()
        linked = []
        if any(k in blob for k in ("treasury", "finance", "coinbase", "robinhood", "liquidity")):
            linked.append("finance")
        if any(k in blob for k in ("fitness", "workout", "health", "resistance")):
            linked.append("fitness")
        if any(k in blob for k in ("time", "holistic", "allocator", "schedule")):
            linked.append("holistic")
        if any(k in blob for k in ("strategy", "bet", "today", "initiative", "automation", "command center", "orchestra")):
            linked.append("strategy")
        if linked:
            add(
                kind="relationship",
                title=f"Backlog bridges domains: {(item.get('title') or '')[:70]}",
                domains_involved=sorted(set(["workflow"] + linked)),
                detail=f"Backlog item (priority={item.get('priority')}) references {', '.join(linked)}.",
                strength="medium",
                evidence=[item.get("notes") or item.get("title") or ""][:1],
            )

    # 6) Shared next-action language between today.md and initiatives
    if today_items and initiatives:
        for init in initiatives:
            na = (init.get("next_action") or "").lower()
            if len(na) < 12:
                continue
            # token overlap with today items
            init_tokens = set(re.findall(r"[a-z]{4,}", na))
            for t in today_items:
                t_tokens = set(re.findall(r"[a-z]{4,}", t.lower()))
                shared = init_tokens & t_tokens
                if len(shared) >= 3:
                    add(
                        kind="synergy",
                        title="Today plan aligns with initiative next action",
                        domains_involved=["strategy", "workflow"],
                        detail=(
                            f"Today item and initiative '{init.get('title','')[:60]}' "
                            f"share focus tokens: {', '.join(sorted(shared)[:6])}."
                        ),
                        strength="high",
                        evidence=[t[:120], (init.get("next_action") or "")[:120]],
                    )
                    break

    # 7) Always surface multi-domain readiness if workflow dirty + finance actions
    if (workflow.get("signals") or {}).get("dirty_files", 0) and action_titles:
        add(
            kind="overlap",
            title="Protect uncommitted work while executing treasury actions",
            domains_involved=["workflow", "finance"],
            detail=(
                "Repo has dirty files and treasury has open actions. "
                "Commit/push workflow changes before multi-step finance work to avoid context loss."
            ),
            strength="medium",
            evidence=[
                f"dirty_files={(workflow.get('signals') or {}).get('dirty_files')}",
                f"actions={len(action_titles)}",
            ],
        )

    # Prefer higher strength first, then stable id order
    strength_rank = {"high": 0, "medium": 1, "low": 2}
    synergies.sort(
        key=lambda s: (strength_rank.get(s.get("strength") or "medium", 9), s.get("id") or "")
    )
    return synergies
