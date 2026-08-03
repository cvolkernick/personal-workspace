"""Operator attention digest and source freshness for Orchestra.

Pure functions — no I/O. Used by payload assembly and unit tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


# Default: treat finance / backlog sources older than this as stale for operators.
DEFAULT_STALE_HOURS = 48.0


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse ISO-ish timestamps to timezone-aware UTC datetime, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    # Tolerate trailing Z
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hours_since(ts: Any, *, now: Optional[datetime] = None) -> Optional[float]:
    """Hours elapsed since ts; None if unparseable."""
    dt = parse_timestamp(ts)
    if dt is None:
        return None
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    delta = ref - dt
    return max(0.0, delta.total_seconds() / 3600.0)


def compute_freshness(
    domains: list[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    stale_hours: float = DEFAULT_STALE_HOURS,
) -> dict[str, Any]:
    """Summarize source ages and stale flags from domain snapshots.

    Reads known timestamps:
    - finance.signals.as_of
    - workflow.signals.backlog.updated_at
    """
    by_id = {d.get("id"): d for d in domains if d.get("id")}
    sources: list[dict[str, Any]] = []
    ref = now or datetime.now(timezone.utc)

    finance = by_id.get("finance") or {}
    fin_sig = finance.get("signals") or {}
    fin_as_of = fin_sig.get("as_of")
    fin_age = hours_since(fin_as_of, now=ref)
    fin_stale = bool(
        finance.get("available")
        and fin_age is not None
        and fin_age > stale_hours
    )
    if fin_as_of or finance.get("available"):
        sources.append(
            {
                "id": "finance_snapshot",
                "domain": "finance",
                "label": "Treasury snapshot",
                "as_of": fin_as_of,
                "age_hours": round(fin_age, 2) if fin_age is not None else None,
                "stale": fin_stale,
                "stale_threshold_hours": stale_hours,
                "path": fin_sig.get("source"),
            }
        )

    workflow = by_id.get("workflow") or {}
    backlog = (workflow.get("signals") or {}).get("backlog") or {}
    bl_updated = backlog.get("updated_at")
    bl_age = hours_since(bl_updated, now=ref)
    bl_stale = bool(
        backlog.get("ok")
        and bl_age is not None
        and bl_age > stale_hours
    )
    if bl_updated is not None or backlog.get("ok"):
        sources.append(
            {
                "id": "backlog",
                "domain": "workflow",
                "label": "Ops backlog",
                "as_of": bl_updated,
                "age_hours": round(bl_age, 2) if bl_age is not None else None,
                "stale": bl_stale,
                "stale_threshold_hours": stale_hours,
                "path": backlog.get("source"),
            }
        )

    stale_sources = [s for s in sources if s.get("stale")]
    ages = [s["age_hours"] for s in sources if s.get("age_hours") is not None]
    return {
        "stale_threshold_hours": stale_hours,
        "sources": sources,
        "stale_count": len(stale_sources),
        "stale_ids": [s["id"] for s in stale_sources],
        "max_age_hours": round(max(ages), 2) if ages else None,
        "has_stale": bool(stale_sources),
        "computed_at": ref.isoformat(),
    }


_SEVERITY_SCORE = {
    "critical": 100,
    "high": 80,
    "medium": 50,
    "low": 25,
    "info": 10,
}


def synthesize_attention(
    domains: list[dict[str, Any]],
    *,
    priorities: list[dict[str, Any]] | None = None,
    bridge: dict[str, Any] | None = None,
    freshness: dict[str, Any] | None = None,
    synergies: list[dict[str, Any]] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Ranked operator attention items: what needs eyes now.

    Pure function. Severity: critical | high | medium | low | info.
    """
    priorities = priorities or []
    bridge = bridge or {}
    freshness = freshness or {}
    synergies = synergies or []
    by_id = {d.get("id"): d for d in domains if d.get("id")}

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(
        *,
        title: str,
        severity: str,
        kind: str,
        domains_involved: list[str],
        detail: str = "",
        score_boost: int = 0,
    ) -> None:
        key = " ".join((title or "").lower().split())[:160]
        if not key or key in seen:
            return
        seen.add(key)
        base = _SEVERITY_SCORE.get((severity or "medium").lower(), 50)
        items.append(
            {
                "id": f"att-{len(items) + 1}",
                "title": title.strip()[:240],
                "severity": severity,
                "kind": kind,
                "domains": domains_involved,
                "detail": (detail or "")[:400],
                "score": base + score_boost,
            }
        )

    # 1) Unavailable / missing domains
    for d in domains:
        did = d.get("id") or "unknown"
        if d.get("available") is False or (d.get("status") or "") == "missing":
            add(
                title=f"Domain unavailable: {d.get('label') or did}",
                severity="high",
                kind="domain_missing",
                domains_involved=[did],
                detail="Collector found no usable sources — restore files or refresh data.",
                score_boost=15,
            )
        elif (d.get("status") or "") == "partial":
            add(
                title=f"Domain partial: {d.get('label') or did}",
                severity="medium",
                kind="domain_partial",
                domains_involved=[did],
                detail=d.get("summary") or "Some sources missing or incomplete.",
                score_boost=5,
            )

    # 2) Stale sources from freshness summary
    for src in freshness.get("sources") or []:
        if not isinstance(src, dict) or not src.get("stale"):
            continue
        age = src.get("age_hours")
        age_s = f"{age:.1f}h old" if isinstance(age, (int, float)) else "age unknown"
        thr = src.get("stale_threshold_hours") or freshness.get("stale_threshold_hours")
        add(
            title=f"Stale data: {src.get('label') or src.get('id')}",
            severity="high",
            kind="stale_source",
            domains_involved=[src.get("domain") or "unknown"],
            detail=(
                f"{age_s} (threshold {thr}h). "
                f"as_of={src.get('as_of') or 'n/a'}. Refresh before acting."
            ),
            score_boost=12,
        )

    # 3) Finance stress
    finance = by_id.get("finance") or {}
    fin_sig = finance.get("signals") or {}
    stress = (fin_sig.get("stress") or "")
    if isinstance(stress, str) and stress.lower() and stress.lower() not in (
        "ok",
        "normal",
        "low",
        "none",
        "green",
    ):
        add(
            title=f"Treasury stress elevated: {stress}",
            severity="critical" if stress.lower() in ("critical", "red", "high") else "high",
            kind="finance_stress",
            domains_involved=["finance"],
            detail="Open financial-command and review stress + open actions.",
            score_boost=20,
        )

    # 4) Empty or thin today plan
    strategy = by_id.get("strategy") or {}
    s_sig = strategy.get("signals") or {}
    today_count = int(s_sig.get("today_count") or 0)
    if strategy.get("available") and today_count == 0:
        add(
            title="Today's micro plan has no open items",
            severity="medium",
            kind="empty_today",
            domains_involved=["strategy"],
            detail="Update strategy/today.md open checklist for a concrete 24–48h focus.",
            score_boost=8,
        )
    elif today_count >= 8:
        add(
            title=f"Today plan is overloaded ({today_count} open items)",
            severity="medium",
            kind="overloaded_today",
            domains_involved=["strategy", "holistic"],
            detail="Trim to 2–5 highest-leverage actions so the plan stays executable.",
            score_boost=6,
        )

    # 5) Day bridge: backlog not on day plan
    candidates = bridge.get("candidates") or []
    if len(candidates) >= 3:
        titles = [
            str(c.get("title") or c.get("backlog_id") or "")
            for c in candidates[:3]
            if isinstance(c, dict)
        ]
        add(
            title=f"{len(candidates)} backlog item(s) waiting for day allocation",
            severity="medium",
            kind="bridge_backlog",
            domains_involved=["workflow", "holistic"],
            detail="Send top items from Workflow → today. Candidates: "
            + "; ".join(t for t in titles if t)[:200],
            score_boost=7,
        )
    elif len(candidates) == 1:
        c0 = candidates[0] if isinstance(candidates[0], dict) else {}
        add(
            title=f"Bridge candidate: {c0.get('title') or c0.get('backlog_id') or 'item'}",
            severity="low",
            kind="bridge_backlog",
            domains_involved=["workflow", "holistic"],
            detail="Unlinked backlog item ready to send to the day plan.",
            score_boost=3,
        )

    # 6) High-strength synergies as coordination nags (cap)
    high_syns = [s for s in synergies if (s.get("strength") or "") == "high"]
    for syn in high_syns[:3]:
        add(
            title=f"Coordinate: {syn.get('title') or 'high synergy'}",
            severity="medium",
            kind="synergy",
            domains_involved=list(syn.get("domains") or []),
            detail=syn.get("detail") or "",
            score_boost=4,
        )

    # 7) Top ranked priority as explicit focus cue
    if priorities:
        top = priorities[0]
        add(
            title=f"Top priority: {top.get('title') or 'action'}",
            severity="info" if (top.get("priority") or "").lower() != "critical" else "high",
            kind="top_priority",
            domains_involved=list(top.get("domains") or ["strategy"]),
            detail=top.get("rationale") or f"source={top.get('source')}",
            score_boost=2,
        )

    # 8) Offline live probes (only when live is explicitly False)
    offline = [
        d
        for d in domains
        if d.get("live") is False and d.get("port") and d.get("launch")
    ]
    if offline:
        labels = [str(d.get("label") or d.get("id")) for d in offline[:4]]
        add(
            title=f"{len(offline)} subordinate server(s) offline",
            severity="low",
            kind="servers_offline",
            domains_involved=[str(d.get("id")) for d in offline if d.get("id")],
            detail="Not listening: " + ", ".join(labels) + ". Launch when you need the UI.",
            score_boost=1,
        )

    items.sort(key=lambda x: (-int(x.get("score") or 0), x.get("id") or ""))
    out: list[dict[str, Any]] = []
    for i, it in enumerate(items[:limit]):
        row = dict(it)
        row["rank"] = i + 1
        out.append(row)
    return out
