"""Assemble the full orchestra orchestration payload."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from .collectors import collect_all_domains
    from .domains import DOMAIN_SPECS
    from .priorities import synthesize_priorities
    from .synergies import detect_synergies
except ImportError:
    from collectors import collect_all_domains
    from domains import DOMAIN_SPECS
    from priorities import synthesize_priorities
    from synergies import detect_synergies

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8790
ORCHESTRA_URL = f"http://127.0.0.1:{DEFAULT_PORT}/"


def build_orchestra_payload(
    workspace: Optional[Path] = None,
    *,
    probe_ports: bool = False,
) -> dict[str, Any]:
    """Build unified orchestration payload from on-disk domain sources.

    Pure aggregation entry point used by the HTTP API and unit tests.
    """
    ws = Path(workspace or WORKSPACE_ROOT).resolve()
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
    return {
        "ok": True,
        "service": "orchestra",
        "name": "Orchestra Command Center",
        "purpose": (
            "Top-level orchestration across strategy, workflow, finance, fitness, "
            "time-allocation, and IoT/home — surfaces overlaps, synergies, and coordinated priorities."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(ws),
        "domains": domains,
        "domain_ids": domain_ids,
        "links": links,
        "synergies": synergies,
        "priorities": priorities,
        "action_plan": priorities,  # alias for UI / verification
        "counts": {
            "domains": len(domains),
            "domains_available": sum(1 for d in domains if d.get("available")),
            "synergies": len(synergies),
            "priorities": len(priorities),
            "initiatives": len(initiatives),
            "today_items": len(today_items),
        },
        "meta": {
            "port": DEFAULT_PORT,
            "url": ORCHESTRA_URL,
            "probe_ports": probe_ports,
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
