"""Assemble the full orchestra orchestration payload."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from .attention import compute_freshness, synthesize_attention
    from .collectors import collect_all_domains
    from .domains import DOMAIN_SPECS
    from .priorities import synthesize_priorities
    from .recommendations import synthesize_recommendations
    from .synergies import detect_synergies
except ImportError:
    from attention import compute_freshness, synthesize_attention
    from collectors import collect_all_domains
    from domains import DOMAIN_SPECS
    from priorities import synthesize_priorities
    from recommendations import synthesize_recommendations
    from synergies import detect_synergies

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8790

try:
    import sys

    if str(WORKSPACE_ROOT) not in sys.path:
        sys.path.insert(0, str(WORKSPACE_ROOT))
    from dashboard_endpoints import service_url as _service_url

    ORCHESTRA_URL = _service_url("orchestra")
except Exception:  # noqa: BLE001
    ORCHESTRA_URL = f"http://192.168.100.98:{DEFAULT_PORT}/"


def _annotate_domains_freshness(
    domains: list[dict[str, Any]],
    freshness: dict[str, Any],
) -> None:
    """Attach stale / age_hours onto domain snapshots from freshness sources (in-place)."""
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for src in freshness.get("sources") or []:
        if not isinstance(src, dict):
            continue
        did = src.get("domain")
        if did:
            by_domain.setdefault(str(did), []).append(src)
    for d in domains:
        did = d.get("id")
        srcs = by_domain.get(str(did) or "", [])
        if not srcs:
            continue
        ages = [s.get("age_hours") for s in srcs if s.get("age_hours") is not None]
        d["stale"] = any(bool(s.get("stale")) for s in srcs)
        d["age_hours"] = max(ages) if ages else None
        sig = d.setdefault("signals", {})
        if isinstance(sig, dict):
            sig["freshness"] = [
                {
                    "id": s.get("id"),
                    "age_hours": s.get("age_hours"),
                    "stale": s.get("stale"),
                    "as_of": s.get("as_of"),
                }
                for s in srcs
            ]


def build_orchestra_payload(
    workspace: Optional[Path] = None,
    *,
    probe_ports: bool = False,
    stale_hours: float = 48.0,
) -> dict[str, Any]:
    """Build unified orchestration payload from on-disk domain sources.

    Pure aggregation entry point used by the HTTP API and unit tests.
    Includes operator attention digest and source freshness (enhancements).
    """
    ws = Path(workspace or WORKSPACE_ROOT).resolve()
    generated_at = datetime.now(timezone.utc)
    domains = collect_all_domains(ws, probe_ports=probe_ports)

    by_id = {d["id"]: d for d in domains}
    strategy = by_id.get("strategy") or {}
    s_sig = strategy.get("signals") or {}
    initiatives = list(s_sig.get("initiatives") or [])
    today_items = list(s_sig.get("today_open") or [])

    workflow = by_id.get("workflow") or {}
    backlog = (workflow.get("signals") or {}).get("backlog") or {}
    backlog_active = list(backlog.get("active") or [])

    finance = by_id.get("finance") or {}
    finance_actions = list((finance.get("signals") or {}).get("action_titles") or [])

    fitness = by_id.get("fitness") or {}
    holistic = by_id.get("holistic") or {}
    holistic_targets = list((holistic.get("signals") or {}).get("targets") or [])
    holistic_sig = holistic.get("signals") or {}
    backlog_linked = list(holistic_sig.get("backlog_linked") or [])
    linked_ids = {
        str(x.get("backlog_id"))
        for x in backlog_linked
        if isinstance(x, dict) and x.get("backlog_id")
    }
    # Bridge candidates: top backlog not yet on day plan
    bridge_candidates = []
    for bi in backlog_active[:10]:
        bid = str(bi.get("id") or "")
        if not bid:
            continue
        slot = (bi.get("schedule_slot") or "").lower()
        try:
            rank = int(bi.get("press_rank") or 99)
        except (TypeError, ValueError):
            rank = 99
        if bid in linked_ids:
            continue
        if slot in ("now", "this_week") or rank <= 3 or (bi.get("status") or "").lower() in (
            "ready",
            "planning",
            "active",
        ):
            bridge_candidates.append(
                {
                    "backlog_id": bid,
                    "title": bi.get("title"),
                    "priority": bi.get("priority"),
                    "status": bi.get("status"),
                    "press_rank": bi.get("press_rank"),
                    "schedule_label": bi.get("schedule_label") or bi.get("schedule_slot"),
                    "area": bi.get("area"),
                }
            )
    iot = by_id.get("iot") or {}
    iot_sig = iot.get("signals") or {}
    iot_routines = [
        str(r.get("name") or r.get("id") or "")
        for r in (iot_sig.get("routines") or [])
        if isinstance(r, dict) and (r.get("name") or r.get("id"))
    ]

    synergies = detect_synergies(
        domains,
        initiatives=initiatives,
        today_items=today_items,
    )
    priorities = synthesize_priorities(
        today_items=today_items,
        initiatives=initiatives,
        backlog_active=backlog_active,
        finance_actions=finance_actions,
        fitness_summary=fitness.get("summary") if fitness.get("available") else None,
        holistic_targets=holistic_targets,
        iot_summary=iot.get("summary") if iot.get("available") else None,
        iot_routines=iot_routines,
        synergies=synergies,
    )

    bridge = {
        "note": (
            "Macro backlog → day plan without merging UIs. "
            "Send from Workflow Management, or deep-link into each tool."
        ),
        "candidates": bridge_candidates[:8],
        "linked": backlog_linked[:12],
        "workflow_url": "http://127.0.0.1:8765/",
        "allocator_url": "http://127.0.0.1:8770/",
        "send_hint": "In Workflow: Send to today / Send top to today",
    }

    freshness = compute_freshness(
        domains, now=generated_at, stale_hours=stale_hours
    )
    _annotate_domains_freshness(domains, freshness)

    attention = synthesize_attention(
        domains,
        priorities=priorities,
        bridge=bridge,
        freshness=freshness,
        synergies=synergies,
    )

    recommendations = synthesize_recommendations(
        domains=domains,
        priorities=priorities,
        attention=attention,
        synergies=synergies,
        bridge=bridge,
        freshness=freshness,
    )
    # Primary operator-facing action list (recommendations.items)
    recommended_actions = list(recommendations.get("items") or [])

    try:
        from fan_in import build_fan_in
    except ImportError:
        from .fan_in import build_fan_in  # type: ignore

    fan_in = build_fan_in(ws)

    links = []
    for spec in DOMAIN_SPECS:
        d = by_id.get(spec["id"]) or {}
        links.append(
            {
                "id": spec["id"],
                "label": spec["label"],
                "url": d.get("url") or spec.get("url"),
                "launch": d.get("launch") or spec.get("launch"),
                "port": d.get("port") if d.get("port") is not None else spec.get("port"),
                "live": d.get("live"),
                "sources": d.get("sources") or spec.get("sources") or [],
            }
        )

    domain_ids = [d["id"] for d in domains]
    high_synergies = sum(
        1 for s in synergies if (s.get("strength") or "") == "high"
    )
    return {
        "ok": True,
        "service": "orchestra",
        "name": "Orchestrator",
        "purpose": (
            "Automates holistic multi-domain analysis and synthesizes recommended next actions "
            "from strategy, workflow, finance, fitness, time-allocation, and IoT — integrating "
            "synergies, attention/hygiene, and priorities without manual triage."
        ),
        "generated_at": generated_at.isoformat(),
        "workspace": str(ws),
        "domains": domains,
        "domain_ids": domain_ids,
        "links": links,
        # Primary synthesized output for operators / agents
        "recommendations": recommendations,
        "recommended_actions": recommended_actions,
        # Intermediate streams (detail / debug; UI demotes vs recommendations)
        "synergies": synergies,
        "priorities": priorities,
        "action_plan": recommended_actions,  # primary plan alias → recommended actions
        "attention": attention,
        "freshness": freshness,
        "bridge": bridge,
        "fan_in": fan_in,
        "counts": {
            "domains": len(domains),
            "domains_available": sum(1 for d in domains if d.get("available")),
            "synergies": len(synergies),
            "synergies_high": high_synergies,
            "priorities": len(priorities),
            "recommendations": len(recommended_actions),
            "initiatives": len(initiatives),
            "today_items": len(today_items),
            "bridge_candidates": len(bridge_candidates),
            "bridge_linked": len(backlog_linked),
            "attention": len(attention),
            "stale_sources": freshness.get("stale_count") or 0,
            "implications_top": len((fan_in.get("implications") or {}).get("top") or []),
        },
        "meta": {
            "port": DEFAULT_PORT,
            "url": ORCHESTRA_URL,
            "probe_ports": probe_ports,
            "stale_hours": stale_hours,
            "primary_output": "recommendations",
            "streams": {
                "recommendations": "Merged automated next actions (primary)",
                "priorities": "Raw priority synthesis (input to recommendations)",
                "attention": "Hygiene/attention digest (input to recommendations)",
                "synergies": "Cross-domain links (input; high preferred, medium fallback)",
                "fan_in": "Host heartbeat + L0 regime/implications strip",
            },
            "subordinate_ports": {
                "financial-command": 8000,
                "projects-dashboard": 8765,
                "holistic": 8770,
                "iot": 8780,
                "resistance-dashboard": 8787,
                "orchestra": DEFAULT_PORT,
            },
        },
    }
