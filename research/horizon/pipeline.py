"""Orchestrate Horizon update + synthesis loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from research.horizon.packets import publish_l0_packet
from research.horizon.regime import attach_regime
from research.horizon.sources.fixture import FixtureSource
from research.horizon.sources.rss import RssSource
from research.horizon.store import (
    DEFAULT_DATA_DIR,
    load_world_state,
    save_brief,
    save_world_state,
)
from research.horizon.strategy_link import link_world_to_strategy, load_strategy
from research.horizon.synthesis import render_markdown, synthesize
from research.horizon.world_state import (
    apply_events,
    empty_world_state,
    ensure_domains,
    make_version_id,
)


def collect_events(
    *,
    offline: bool = True,
    fixture_path: Optional[Path] = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Gather events from adapters. Always includes fixtures for domain coverage."""
    modes: list[str] = []
    events: list[dict[str, Any]] = []

    fixture = FixtureSource(path=fixture_path)
    fix_events = fixture.fetch()
    events.extend(fix_events)
    modes.append("fixture")

    if not offline:
        rss = RssSource()
        live = rss.fetch()
        if live:
            events.extend(live)
            modes.append("rss")
        elif rss.last_errors:
            modes.append("rss_failed")

    return events, modes


def run_pipeline(
    *,
    workspace: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    offline: bool = True,
    link_only: bool = False,
    fixture_path: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Full update+synthesis (or link-only recompute).

    Returns a result dict with paths, version_id, and brief summary stats.
    """
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    version_id = make_version_id()

    previous = load_world_state(data_dir)
    if previous is None:
        previous = empty_world_state(version_id)
    else:
        ensure_domains(previous)

    modes: list[str] = list((previous.get("meta") or {}).get("source_modes") or [])

    if link_only:
        state = previous
        # Keep existing version but stamp a link-only run id in meta
        state = dict(state)
        state["version_id"] = version_id
        state["updated_at"] = previous.get("updated_at") or state.get("updated_at")
        meta = dict(state.get("meta") or {})
        meta["link_only"] = True
        meta["run_id"] = version_id
        state["meta"] = meta
        modes = list(meta.get("source_modes") or ["link_only"])
    else:
        events, modes = collect_events(offline=offline, fixture_path=fixture_path)
        state = apply_events(
            previous,
            events,
            version_id=version_id,
            source_modes=modes,
        )

    # Regime assessment always recomputed from current nodes (product layer)
    state = attach_regime(state)
    regime = state.get("regime") or {}
    save_world_state(state, data_dir)

    strategy = load_strategy(workspace)
    linkages = link_world_to_strategy(state, strategy)
    brief = synthesize(state, strategy, linkages)
    markdown = render_markdown(brief)
    brief_paths = save_brief(brief, markdown, data_dir)

    # L0 implication packet (#49) — producer-owned latest.json + history
    packet_pub = publish_l0_packet(state, brief, data_dir=data_dir)
    packet = packet_pub["packet"]
    packet_paths = packet_pub["paths"]

    return {
        "ok": True,
        "version_id": version_id,
        "offline": offline,
        "link_only": link_only,
        "source_modes": modes,
        "node_total": (state.get("meta") or {}).get("node_total"),
        "linkage_count": len(linkages),
        "regime_primary": (regime.get("primary") or {}).get("label")
        or (regime.get("primary") or {}).get("scenario_id")
        or (regime.get("primary") or {}).get("id"),
        "regime_confidence": regime.get("confidence_overall")
        if regime.get("confidence_overall") is not None
        else regime.get("confidence"),
        "strategy_paths_exist": strategy.get("paths_exist"),
        "paths": {
            "data_dir": str(data_dir),
            "world_state_latest": str(data_dir / "world_state_latest.json"),
            "brief_latest_json": str(brief_paths["latest_json"]),
            "brief_latest_md": str(brief_paths["latest_md"]),
            "brief_version_json": str(brief_paths["version_json"]),
            "packet_latest": str(packet_paths["latest"]),
            "packet_history": str(packet_paths["history"]),
        },
        "sections": {
            "executive_brief_items": len(
                (brief.get("executive_brief") or {}).get("items") or []
            ),
            "world_state_domains": len(
                (brief.get("current_world_state") or {}).get("domains") or {}
            ),
            "strategy_sections": len(
                (brief.get("implications_for_my_strategy") or {}).get("sections") or []
            ),
            "watchlist_items": len((brief.get("watchlist") or {}).get("items") or []),
            "packet_nodes": len(packet.get("nodes") or []),
            "packet_implications": len(packet.get("implications_for_l4") or []),
        },
        "brief": brief,
        "state": state,
        "regime": regime,
        "packet": packet,
        "strategy": strategy,
        "linkages": linkages,
        "markdown": markdown,
    }
