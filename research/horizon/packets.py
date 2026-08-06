"""Implication packet v0 — L0 producer + structural validation.

Product schema: nest RESEARCH/IMPLICATION_PACKET_V0_SCHEMA.md
JSON Schema file: research/horizon/schemas/implication_packet_v0.json

Write path: fail-closed (raises PacketValidationError).
Read path: soft-degrade helpers for consumers (Orchestra later).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from research.horizon.world_state import IMPACT_WEIGHT, query_nodes

SCHEMA_VERSION = 1
MAX_NODES = 12
MAX_EDGES = 20
MAX_IMPLICATIONS = 8
DEFAULT_MAX_AGE_HOURS = 168

LEVELS = frozenset({"L0", "L1", "L2", "L3", "L4"})
DIRECTIONS = frozenset({"down", "up"})
IMPACTS = frozenset({"low", "medium", "high", "critical"})
RELATIONS = frozenset(
    {
        "affects",
        "constrains",
        "funds",
        "exposes",
        "opportunities",
        "requires_action",
        "amplifies",
        "diverges",
    }
)
OWNER_DOMAINS = frozenset(
    {"capital", "body", "work", "time", "home", "knowledge", "weave"}
)
URGENCIES = frozenset({"watch", "this_week", "immediate", "structural"})
CONSTRAINT_DOMAINS = frozenset({"capital", "body", "work", "time", "home"})
SEVERITIES = frozenset({"info", "yellow", "red"})

# Horizon domain id → L4 owner for so-what routing
DOMAIN_TO_OWNER: dict[str, str] = {
    "capital_flows": "capital",
    "macroeconomics": "capital",
    "monetary_fiscal": "capital",
    "financial_conditions": "capital",
    "fx_dollar": "capital",
    "geopolitics": "capital",
    "technology_ai": "capital",
    "supply_chains": "capital",
    "energy": "capital",
    "commodities": "capital",
    "demographics": "work",
    "climate": "home",
    "labor": "work",
}

DEFAULT_CONSUMERS = ["orchestra", "nakatoshi", "cadence", "chris"]


class PacketValidationError(ValueError):
    """Raised when a packet fails fail-closed write validation."""


def schema_path() -> "Path":  # noqa: F821 — resolved at import bottom
    from pathlib import Path

    return Path(__file__).resolve().parent / "schemas" / "implication_packet_v0.json"


def _clamp01(x: Any, default: float = 0.5) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


def _impact_score(impact: str, confidence: float) -> float:
    return IMPACT_WEIGHT.get(str(impact or "medium"), 2.0) * _clamp01(confidence)


def _parse_as_of(as_of: str) -> Optional[datetime]:
    if not as_of:
        return None
    s = str(as_of).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def compute_stale(as_of: str, max_age_hours: float, *, now: Optional[datetime] = None) -> bool:
    dt = _parse_as_of(as_of)
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    age_h = (now - dt).total_seconds() / 3600.0
    return age_h > float(max_age_hours)


def owner_domain_for_horizon_domain(domain: str) -> str:
    d = str(domain or "").lower()
    if d in DOMAIN_TO_OWNER:
        return DOMAIN_TO_OWNER[d]
    if "capital" in d or "macro" in d or "rate" in d or "fx" in d:
        return "capital"
    if "labor" in d or "work" in d:
        return "work"
    if "health" in d or "body" in d:
        return "body"
    return "capital"


def urgency_for_node(impact: str, confidence: float, horizon_days: Optional[int]) -> str:
    imp = str(impact or "medium")
    conf = _clamp01(confidence)
    days = int(horizon_days) if horizon_days is not None else 90
    if imp == "critical" and conf >= 0.6:
        return "immediate"
    if days >= 180 and imp in ("high", "critical"):
        return "structural"
    if imp in ("high", "critical") and days <= 60:
        return "this_week"
    if imp == "high" and conf >= 0.65:
        return "this_week"
    if conf < 0.45:
        return "watch"
    return "watch"


def validate_packet(packet: dict[str, Any]) -> list[str]:
    """Return list of validation errors (empty = valid). Soft-degrade friendly."""
    errors: list[str] = []
    if not isinstance(packet, dict):
        return ["packet must be an object"]

    if packet.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    for key in (
        "packet_id",
        "level",
        "direction",
        "as_of",
        "producer",
        "freshness",
        "nodes",
        "implications_for_l4",
        "constraints_from_l4",
    ):
        if key not in packet:
            errors.append(f"missing required field: {key}")

    level = packet.get("level")
    if level is not None and level not in LEVELS:
        errors.append(f"level invalid: {level}")

    direction = packet.get("direction")
    if direction is not None and direction not in DIRECTIONS:
        errors.append(f"direction invalid: {direction}")

    producer = packet.get("producer")
    if producer is not None:
        if not isinstance(producer, dict):
            errors.append("producer must be object")
        else:
            if not producer.get("domain"):
                errors.append("producer.domain required")
            if not producer.get("surface"):
                errors.append("producer.surface required")

    freshness = packet.get("freshness")
    if freshness is not None:
        if not isinstance(freshness, dict):
            errors.append("freshness must be object")
        else:
            for fk in ("as_of", "max_age_hours", "stale", "confidence_overall"):
                if fk not in freshness:
                    errors.append(f"freshness.{fk} required")
            if "stale" in freshness and not isinstance(freshness.get("stale"), bool):
                errors.append("freshness.stale must be boolean")
            # Dual as_of lock-in: root and freshness should match on write
            if packet.get("as_of") and freshness.get("as_of"):
                if str(packet["as_of"]) != str(freshness["as_of"]):
                    errors.append("as_of must equal freshness.as_of")

    nodes = packet.get("nodes")
    if nodes is not None:
        if not isinstance(nodes, list):
            errors.append("nodes must be array")
        else:
            if len(nodes) > MAX_NODES:
                errors.append(f"nodes maxItems {MAX_NODES}, got {len(nodes)}")
            for i, n in enumerate(nodes):
                if not isinstance(n, dict):
                    errors.append(f"nodes[{i}] must be object")
                    continue
                for nk in ("id", "title", "domain", "impact", "confidence"):
                    if nk not in n:
                        errors.append(f"nodes[{i}].{nk} required")
                if n.get("impact") not in IMPACTS and "impact" in n:
                    errors.append(f"nodes[{i}].impact invalid")

    edges = packet.get("edges")
    if edges is not None:
        if not isinstance(edges, list):
            errors.append("edges must be array")
        elif len(edges) > MAX_EDGES:
            errors.append(f"edges maxItems {MAX_EDGES}, got {len(edges)}")
        else:
            for i, e in enumerate(edges):
                if not isinstance(e, dict):
                    errors.append(f"edges[{i}] must be object")
                    continue
                if "from" not in e or "to" not in e:
                    errors.append(f"edges[{i}] requires from/to")
                rel = e.get("relation")
                if rel is not None and rel not in RELATIONS:
                    errors.append(f"edges[{i}].relation invalid: {rel}")

    impls = packet.get("implications_for_l4")
    if impls is not None:
        if not isinstance(impls, list):
            errors.append("implications_for_l4 must be array")
        else:
            if len(impls) > MAX_IMPLICATIONS:
                errors.append(
                    f"implications_for_l4 maxItems {MAX_IMPLICATIONS}, got {len(impls)}"
                )
            if direction == "down" and len(impls) < 1:
                errors.append("direction=down requires ≥1 implications_for_l4")
            for i, it in enumerate(impls):
                if not isinstance(it, dict):
                    errors.append(f"implications_for_l4[{i}] must be object")
                    continue
                for ik in (
                    "id",
                    "action",
                    "owner_domain",
                    "urgency",
                    "rationale",
                    "confidence",
                ):
                    if ik not in it:
                        errors.append(f"implications_for_l4[{i}].{ik} required")
                if it.get("owner_domain") not in OWNER_DOMAINS and "owner_domain" in it:
                    errors.append(f"implications_for_l4[{i}].owner_domain invalid")
                if it.get("urgency") not in URGENCIES and "urgency" in it:
                    errors.append(f"implications_for_l4[{i}].urgency invalid")
                if not str(it.get("action") or "").strip() and "action" in it:
                    errors.append(f"implications_for_l4[{i}].action empty")

    constraints = packet.get("constraints_from_l4")
    if constraints is not None:
        if not isinstance(constraints, list):
            errors.append("constraints_from_l4 must be array")
        else:
            for i, c in enumerate(constraints):
                if not isinstance(c, dict):
                    errors.append(f"constraints_from_l4[{i}] must be object")
                    continue
                for ck in ("id", "domain", "constraint", "severity", "as_of"):
                    if ck not in c:
                        errors.append(f"constraints_from_l4[{i}].{ck} required")
                if c.get("domain") not in CONSTRAINT_DOMAINS and "domain" in c:
                    errors.append(f"constraints_from_l4[{i}].domain invalid")
                if c.get("severity") not in SEVERITIES and "severity" in c:
                    errors.append(f"constraints_from_l4[{i}].severity invalid")

    if direction == "up" and constraints is not None and not isinstance(constraints, list):
        errors.append("direction=up requires constraints_from_l4 array (may be empty)")

    return errors


def assert_valid_packet(packet: dict[str, Any]) -> dict[str, Any]:
    errors = validate_packet(packet)
    if errors:
        raise PacketValidationError("; ".join(errors))
    return packet


def _packet_nodes_from_state(state: dict[str, Any], *, limit: int = MAX_NODES) -> list[dict[str, Any]]:
    raw = query_nodes(state, limit=None)
    ranked = sorted(
        raw,
        key=lambda n: _impact_score(str(n.get("impact") or "medium"), float(n.get("confidence") or 0)),
        reverse=True,
    )[:limit]
    out: list[dict[str, Any]] = []
    for n in ranked:
        facts = list(n.get("facts") or [])
        fact = str(facts[0]) if facts else ""
        # horizon_days heuristic from tags / impact
        impact = str(n.get("impact") or "medium")
        horizon_days = 90
        if impact == "critical":
            horizon_days = 30
        elif impact == "high":
            horizon_days = 180
        elif impact == "low":
            horizon_days = 365
        tags = [str(t) for t in (n.get("tags") or [])][:8]
        out.append(
            {
                "id": str(n.get("id") or ""),
                "title": str(n.get("title") or ""),
                "domain": str(n.get("domain") or ""),
                "impact": impact if impact in IMPACTS else "medium",
                "confidence": round(_clamp01(n.get("confidence")), 4),
                "horizon_days": horizon_days,
                "fact": fact,
                "interpretation": str(n.get("interpretation") or ""),
                "tags": tags,
            }
        )
    return out


def _packet_edges_from_state(
    state: dict[str, Any],
    node_ids: set[str],
    *,
    limit: int = MAX_EDGES,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in state.get("edges") or []:
        if not isinstance(e, dict):
            continue
        frm = str(e.get("from_id") or e.get("from") or "")
        to = str(e.get("to_id") or e.get("to") or "")
        if not frm or not to:
            continue
        if frm not in node_ids and to not in node_ids:
            continue
        rel = str(e.get("relation") or "affects")
        if rel not in RELATIONS:
            rel = "affects"
        out.append(
            {
                "from": frm,
                "to": to,
                "relation": rel,
                "note": str(e.get("note") or ""),
            }
        )
        if len(out) >= limit:
            break
    return out


def _slug_action(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:48] or fallback)


def _implications_from_brief_and_nodes(
    *,
    brief: Optional[dict[str, Any]],
    nodes: list[dict[str, Any]],
    regime_summary: Optional[dict[str, Any]],
    limit: int = MAX_IMPLICATIONS,
) -> list[dict[str, Any]]:
    """Build actionable L4 so-whats from strategy linkages + top nodes."""
    impls: list[dict[str, Any]] = []
    seen_actions: set[str] = set()

    strategy = (brief or {}).get("implications_for_my_strategy") or {}
    for section in strategy.get("sections") or []:
        if not isinstance(section, dict):
            continue
        plabel = str(section.get("priority_label") or section.get("priority_id") or "priority")
        for item in section.get("items") or []:
            if len(impls) >= limit:
                break
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain") or "")
            owner = owner_domain_for_horizon_domain(domain)
            title = str(item.get("title") or item.get("node_id") or "node")
            interp = str(item.get("interpretation") or item.get("rationale") or "").strip()
            conf = _clamp01(item.get("confidence"), 0.5)
            nid = str(item.get("node_id") or "")
            # Imperative so-what — not a restated headline
            action = (
                f"Weight {plabel} decisions against: {title.rstrip('.')}. "
                f"{' ' + interp if interp else ''}"
            ).strip()
            if len(action) > 320:
                action = action[:317] + "..."
            key = action[:80].lower()
            if key in seen_actions:
                continue
            seen_actions.add(key)
            impact = "high"  # strategy-linked items are material
            # try find node impact
            for n in nodes:
                if n.get("id") == nid:
                    impact = str(n.get("impact") or impact)
                    break
            urg = urgency_for_node(impact, conf, 90)
            impls.append(
                {
                    "id": f"impl-{owner}-{_slug_action(nid or title, 'item')}",
                    "action": action,
                    "owner_domain": owner,
                    "urgency": urg,
                    "rationale": str(item.get("rationale") or interp or f"Linked to strategy priority {plabel}"),
                    "confidence": round(conf, 4),
                    "horizon_days": 90,
                    "related_node_ids": [nid] if nid else [],
                }
            )
        if len(impls) >= limit:
            break

    # Fill from top nodes if strategy thin
    for n in nodes:
        if len(impls) >= limit:
            break
        owner = owner_domain_for_horizon_domain(str(n.get("domain") or ""))
        conf = _clamp01(n.get("confidence"))
        impact = str(n.get("impact") or "medium")
        title = str(n.get("title") or n.get("id") or "node")
        interp = str(n.get("interpretation") or "").strip()
        action = f"Monitor and size exposure for: {title.rstrip('.')}."
        if interp:
            action = f"{action} {interp}"
        if len(action) > 320:
            action = action[:317] + "..."
        key = action[:80].lower()
        if key in seen_actions:
            continue
        seen_actions.add(key)
        impls.append(
            {
                "id": f"impl-{owner}-{_slug_action(str(n.get('id') or title), 'node')}",
                "action": action,
                "owner_domain": owner,
                "urgency": urgency_for_node(impact, conf, n.get("horizon_days")),
                "rationale": f"Top impact×confidence node in domain {n.get('domain')}",
                "confidence": round(conf, 4),
                "horizon_days": int(n.get("horizon_days") or 90),
                "related_node_ids": [str(n.get("id"))] if n.get("id") else [],
            }
        )

    # Always include a weave consumer note for Orchestra
    if len(impls) < limit:
        label = (regime_summary or {}).get("primary_label") or "current regime"
        weave_action = (
            f"Orchestra should surface L0 regime '{label}' + top implications "
            f"with as_of/confidence — do not re-render full Horizon UI."
        )
        key = weave_action[:80].lower()
        if key not in seen_actions:
            impls.append(
                {
                    "id": "impl-weave-display",
                    "action": weave_action,
                    "owner_domain": "weave",
                    "urgency": "structural",
                    "rationale": "Anti-pattern: god-dashboard. Weave fans in packets only.",
                    "confidence": 0.75,
                    "horizon_days": 30,
                    "related_node_ids": [],
                }
            )

    # direction=down requires ≥1
    if not impls:
        impls.append(
            {
                "id": "impl-work-horizon-refresh",
                "action": "Refresh Horizon sources and re-publish L0 packet before treating regime as decision-grade.",
                "owner_domain": "work",
                "urgency": "this_week",
                "rationale": "Producer had no ranked nodes/strategy linkages.",
                "confidence": 0.4,
                "horizon_days": 7,
                "related_node_ids": [],
            }
        )

    return impls[:limit]


def build_regime_summary(state: dict[str, Any], brief: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    regime = None
    if isinstance(brief, dict) and isinstance(brief.get("regime"), dict):
        regime = brief["regime"]
    elif isinstance(state.get("regime"), dict):
        regime = state["regime"]
    if not regime:
        return {
            "primary_label": "unknown",
            "primary_probability": 0.0,
            "confidence": 0.0,
            "note": "No regime layer on this world-state",
        }
    primary = regime.get("primary") or {}
    conf = regime.get("confidence_overall")
    if conf is None:
        conf = regime.get("confidence")
    note = ""
    notes = regime.get("notes")
    if isinstance(notes, list) and notes:
        note = str(notes[0])
    elif primary.get("summary"):
        note = str(primary["summary"])
    return {
        "primary_label": str(primary.get("label") or primary.get("id") or "unknown"),
        "primary_probability": round(float(primary.get("probability") or 0.0), 4),
        "confidence": round(_clamp01(conf, 0.0), 4),
        "note": note,
    }


def build_l0_down_packet(
    state: dict[str, Any],
    brief: Optional[dict[str, Any]] = None,
    *,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    consumers: Optional[list[str]] = None,
    refresh_cadence: str = "weekly",
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build a validated L0 direction=down implication packet from Horizon state."""
    now = now or datetime.now(timezone.utc)
    version_id = str(
        (brief or {}).get("version_id")
        or state.get("version_id")
        or now.strftime("%Y%m%dT%H%M%SZ")
    )
    as_of = str(
        (brief or {}).get("generated_at")
        or state.get("updated_at")
        or now.isoformat()
    )
    # Normalize dual as_of to same value
    nodes = _packet_nodes_from_state(state, limit=MAX_NODES)
    node_ids = {n["id"] for n in nodes if n.get("id")}
    edges = _packet_edges_from_state(state, node_ids, limit=MAX_EDGES)
    regime_summary = build_regime_summary(state, brief)
    conf_overall = float(regime_summary.get("confidence") or 0.0)
    if not conf_overall and nodes:
        conf_overall = sum(float(n.get("confidence") or 0) for n in nodes) / len(nodes)

    impls = _implications_from_brief_and_nodes(
        brief=brief,
        nodes=nodes,
        regime_summary=regime_summary,
        limit=MAX_IMPLICATIONS,
    )

    stale = compute_stale(as_of, max_age_hours, now=now)
    modes = list((state.get("meta") or {}).get("source_modes") or [])
    notes = [
        f"Produced from Horizon world-state version_id={version_id}",
        f"source_modes={','.join(modes) if modes else 'unknown'}",
    ]
    if conf_overall <= 0.55:
        notes.append(
            "confidence_overall ≤0.55 — treat as structural hypothesis, not high-confidence market call"
        )

    packet: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "packet_id": f"l0-{version_id}",
        "level": "L0",
        "direction": "down",
        "as_of": as_of,
        "producer": {
            "domain": "horizon",
            "surface": "horizon-macro",
            "version_id": version_id,
            "refresh_cadence": refresh_cadence
            if refresh_cadence in ("manual", "daily", "weekly", "on_event")
            else "weekly",
        },
        "freshness": {
            "as_of": as_of,
            "max_age_hours": float(max_age_hours),
            "stale": stale,
            "confidence_overall": round(_clamp01(conf_overall), 4),
        },
        "regime_summary": regime_summary,
        "nodes": nodes,
        "edges": edges,
        "implications_for_l4": impls,
        "constraints_from_l4": [],
        "consumers": list(consumers or DEFAULT_CONSUMERS),
        "notes": notes,
    }
    return assert_valid_packet(packet)


def publish_l0_packet(
    state: dict[str, Any],
    brief: Optional[dict[str, Any]] = None,
    *,
    data_dir: Optional[Any] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build, validate (fail-closed), and atomically write L0 packet. Returns paths + packet."""
    from research.horizon.store import save_packet

    packet = build_l0_down_packet(state, brief, **kwargs)
    paths = save_packet(packet, data_dir=data_dir)
    return {"packet": packet, "paths": paths}
