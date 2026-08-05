"""Structured multi-domain world-state model: update, query, history helpers."""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from research.horizon import DOMAIN_LABELS, REQUIRED_DOMAINS

IMPACT_WEIGHT = {"low": 1.0, "medium": 2.0, "high": 3.5, "critical": 5.0}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def make_version_id(when: Optional[datetime] = None) -> str:
    dt = when or utc_now()
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:64] or "item"


@dataclass
class WorldNode:
    id: str
    domain: str
    title: str
    facts: list[str] = field(default_factory=list)
    interpretation: str = ""
    confidence: float = 0.5
    impact: str = "medium"
    priority_score: float = 0.0
    tags: list[str] = field(default_factory=list)
    related_domains: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldNode":
        return cls(
            id=str(data["id"]),
            domain=str(data["domain"]),
            title=str(data.get("title") or ""),
            facts=list(data.get("facts") or []),
            interpretation=str(data.get("interpretation") or ""),
            confidence=float(data.get("confidence", 0.5)),
            impact=str(data.get("impact") or "medium"),
            priority_score=float(data.get("priority_score") or 0.0),
            tags=list(data.get("tags") or []),
            related_domains=list(data.get("related_domains") or []),
            sources=list(data.get("sources") or []),
            updated_at=str(data.get("updated_at") or ""),
        )


@dataclass
class WorldEdge:
    from_id: str
    to_id: str
    relation: str = "affects"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldEdge":
        return cls(
            from_id=str(data["from_id"]),
            to_id=str(data["to_id"]),
            relation=str(data.get("relation") or "affects"),
            note=str(data.get("note") or ""),
        )


def compute_priority_score(
    impact: str,
    confidence: float,
    updated_at: str = "",
    now: Optional[datetime] = None,
) -> float:
    """Rank input: impact weight × confidence × mild recency boost."""
    base = IMPACT_WEIGHT.get(impact, 2.0) * max(0.0, min(1.0, confidence))
    recency = 1.0
    if updated_at:
        try:
            ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            age_hours = max(0.0, ((now or utc_now()) - ts).total_seconds() / 3600.0)
            # Full weight within 24h; floors at 0.7 after ~7d
            recency = max(0.7, 1.0 - min(age_hours, 168.0) / 560.0)
        except ValueError:
            pass
    return round(base * recency, 4)


def empty_world_state(version_id: Optional[str] = None) -> dict[str, Any]:
    vid = version_id or make_version_id()
    domains: dict[str, Any] = {}
    for d in REQUIRED_DOMAINS:
        domains[d] = {
            "label": DOMAIN_LABELS.get(d, d),
            "nodes": [],
            "summary": "",
        }
    return {
        "schema_version": 1,
        "version_id": vid,
        "updated_at": iso_now(),
        "domains": domains,
        "edges": [],
        "meta": {"source_modes": [], "event_count": 0, "run_id": vid},
    }


def ensure_domains(state: dict[str, Any]) -> dict[str, Any]:
    """Guarantee all required domains exist; return state (mutates in place)."""
    domains = state.setdefault("domains", {})
    for d in REQUIRED_DOMAINS:
        if d not in domains or not isinstance(domains[d], dict):
            domains[d] = {
                "label": DOMAIN_LABELS.get(d, d),
                "nodes": [],
                "summary": "",
            }
        else:
            domains[d].setdefault("label", DOMAIN_LABELS.get(d, d))
            domains[d].setdefault("nodes", [])
            domains[d].setdefault("summary", "")
    return state


def _node_index(state: dict[str, Any]) -> dict[str, tuple[str, int]]:
    """Map node id -> (domain, index)."""
    idx: dict[str, tuple[str, int]] = {}
    for domain, bucket in (state.get("domains") or {}).items():
        for i, raw in enumerate(bucket.get("nodes") or []):
            nid = raw.get("id") if isinstance(raw, dict) else None
            if nid:
                idx[str(nid)] = (domain, i)
    return idx


def apply_events(
    state: dict[str, Any],
    events: Iterable[dict[str, Any]],
    *,
    version_id: Optional[str] = None,
    source_modes: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Upsert events into world-state. Returns a new state dict."""
    out = copy.deepcopy(state) if state else empty_world_state(version_id)
    ensure_domains(out)
    vid = version_id or make_version_id()
    out["version_id"] = vid
    out["updated_at"] = iso_now()
    modes = list(source_modes or out.get("meta", {}).get("source_modes") or [])
    count = 0
    index = _node_index(out)

    for ev in events:
        if not isinstance(ev, dict):
            continue
        domain = str(ev.get("domain") or "").strip()
        if domain not in REQUIRED_DOMAINS:
            # Allow unknown domains only if explicitly listed; else skip
            continue
        title = str(ev.get("title") or "").strip()
        if not title:
            continue
        nid = str(ev.get("id") or f"{domain}-{_slug(title)}")
        impact = str(ev.get("impact") or "medium").lower()
        if impact not in IMPACT_WEIGHT:
            impact = "medium"
        confidence = float(ev.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        updated_at = str(ev.get("updated_at") or iso_now())
        node = WorldNode(
            id=nid,
            domain=domain,
            title=title,
            facts=[str(f) for f in (ev.get("facts") or []) if str(f).strip()],
            interpretation=str(ev.get("interpretation") or ""),
            confidence=confidence,
            impact=impact,
            priority_score=compute_priority_score(impact, confidence, updated_at),
            tags=[str(t).lower() for t in (ev.get("tags") or [])],
            related_domains=[
                str(d)
                for d in (ev.get("related_domains") or [])
                if str(d) in REQUIRED_DOMAINS
            ],
            sources=list(ev.get("sources") or []),
            updated_at=updated_at,
        )
        node_dict = node.to_dict()
        if nid in index:
            old_domain, i = index[nid]
            # Move domain if changed
            if old_domain != domain:
                out["domains"][old_domain]["nodes"].pop(i)
                index = _node_index(out)
                out["domains"][domain]["nodes"].append(node_dict)
            else:
                out["domains"][domain]["nodes"][i] = node_dict
        else:
            out["domains"][domain]["nodes"].append(node_dict)
        index = _node_index(out)
        count += 1

        # Auto edges from related_domains via tag bridge (light-touch)
        for rd in node.related_domains:
            # find highest-priority node in related domain sharing a tag
            peers = out["domains"][rd]["nodes"]
            for peer in peers:
                shared = set(node.tags) & set(peer.get("tags") or [])
                if shared and peer.get("id") != nid:
                    _upsert_edge(
                        out,
                        nid,
                        peer["id"],
                        "affects",
                        f"shared tags: {', '.join(sorted(shared)[:4])}",
                    )
                    break

    # Domain summaries (fact-forward rollups)
    for domain, bucket in out["domains"].items():
        nodes = sorted(
            bucket.get("nodes") or [],
            key=lambda n: float(n.get("priority_score") or 0),
            reverse=True,
        )
        if not nodes:
            bucket["summary"] = f"No active nodes in {DOMAIN_LABELS.get(domain, domain)}."
        else:
            top = nodes[:3]
            titles = "; ".join(n.get("title", "") for n in top)
            bucket["summary"] = f"{len(nodes)} node(s). Top: {titles}"
        bucket["nodes"] = nodes  # keep sorted

    out["meta"] = {
        "source_modes": modes,
        "event_count": count,
        "run_id": vid,
        "node_total": sum(
            len(b.get("nodes") or []) for b in out["domains"].values()
        ),
    }
    return out


def _upsert_edge(
    state: dict[str, Any],
    from_id: str,
    to_id: str,
    relation: str,
    note: str = "",
) -> None:
    edges = state.setdefault("edges", [])
    for e in edges:
        if e.get("from_id") == from_id and e.get("to_id") == to_id and e.get("relation") == relation:
            if note:
                e["note"] = note
            return
    edges.append(
        WorldEdge(from_id=from_id, to_id=to_id, relation=relation, note=note).to_dict()
    )


def query_nodes(
    state: dict[str, Any],
    *,
    domain: Optional[str] = None,
    tag: Optional[str] = None,
    min_impact: Optional[str] = None,
    min_confidence: float = 0.0,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Query nodes with optional filters; sorted by priority_score desc."""
    ensure_domains(state)
    impact_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    min_rank = impact_rank.get(min_impact or "low", 0)
    results: list[dict[str, Any]] = []
    domains = [domain] if domain else list(REQUIRED_DOMAINS)
    tag_l = tag.lower() if tag else None
    for d in domains:
        bucket = state["domains"].get(d) or {}
        for n in bucket.get("nodes") or []:
            if float(n.get("confidence") or 0) < min_confidence:
                continue
            if impact_rank.get(str(n.get("impact") or "low"), 0) < min_rank:
                continue
            if tag_l and tag_l not in [str(t).lower() for t in (n.get("tags") or [])]:
                continue
            results.append(n)
    results.sort(key=lambda n: float(n.get("priority_score") or 0), reverse=True)
    if limit is not None:
        results = results[:limit]
    return results


def domain_coverage(state: dict[str, Any]) -> dict[str, int]:
    """Return node counts per required domain."""
    ensure_domains(state)
    return {
        d: len((state["domains"].get(d) or {}).get("nodes") or [])
        for d in REQUIRED_DOMAINS
    }
