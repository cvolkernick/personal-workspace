#!/usr/bin/env python3
"""Sprint tab: ceremony state (ops/sprint/current.json) + Buzz Board live.

#56 / schema v2:
  - Board (GitHub Project #1) = column membership SoT
  - Ceremony file = goal, overlays (size/owner/notes), agent roster, ceremonies
  - Free agent = seat implement|gate with zero In Progress cards (Pending Review ≠ busy)
  - Dashboard renders only — never writes board Status; never merges ops/backlog
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workspace import WORKSPACE_ROOT

DEFAULT_AGENT_WIP_CAP = 1
DEFAULT_BOARD_URL = "https://github.com/users/cvolkernick/projects/1"

# Fallback implementer/gate seats when ceremony.agents is empty
DEFAULT_AGENTS: list[dict[str, str]] = [
    {"name": "Forge", "role": "platform eng", "seat": "implement"},
    {"name": "Grok", "role": "SIC + eng gate", "seat": "gate"},
    {"name": "Meridian", "role": "horizon / domain", "seat": "implement"},
    {"name": "Frankenfit", "role": "fitness", "seat": "implement"},
    {"name": "Nakatoshi", "role": "capital", "seat": "domain"},
    {"name": "Assay", "role": "system QA", "seat": "qa"},
    {"name": "Cadence", "role": "scrum / ceremonies", "seat": "process"},
]

# Seats that can hold implement WIP or eng-gate (free/busy applies)
WIP_SEATS = frozenset({"implement", "gate"})

BOARD_COLUMN_KEYS = [
    "Parked",
    "Validate",
    "Ready",
    "In Progress",
    "Pending Review",
    "Done",
]

DEFAULT_CEREMONIES = {
    "replenish_cron": "0 16 * * *",
    "replenish_workflow_id": "85e7e98e-4267-4fe7-8d72-fac49ed3e75b",
    "daily_status_cron": "0 13 * * *",
    "daily_status_workflow_id": "311bb694-4cc5-430b-b663-0ebd113636d4",
    "deep_groom_cron": "0 16 * * 3",
    "deep_groom_workflow_id": "05e7fcc7-11fe-4043-bf6b-e22a5744cc67",
    "eng_gate_sweep_cron": "*/15 * * * *",
    "eng_gate_sweep_workflow_id": "59d56951-77a0-4275-a68c-b1fd794bdba3",
    # legacy keys kept for older UI consumers
    "grooming_cron": "0 16 * * 3",
    "planning_cron": "0 16 * * 1",
    "grooming_workflow_id": "95d911df-509b-4eac-a4f5-ffeaa4c1e3da",
    "planning_workflow_id": "b85c12fa-e7e5-43b3-8292-295a1e9f9783",
}

EMPTY_CEREMONY: dict[str, Any] = {
    "schema_version": 2,
    "sprint_id": None,
    "goal": None,
    "agent_wip_cap": DEFAULT_AGENT_WIP_CAP,
    "board_sot": "github_project_v2",
    "board_url": DEFAULT_BOARD_URL,
    "card_overlays": {},
    "not_this_sprint": [],
    "agents": [],
    "ceremonies": {},
    "notes": [],
    "updated_at": None,
    "updated_by": None,
    # v1 legacy (deprecated — degrade-only when board down)
    "wip_limit": None,
    "capacity_points": None,
    "committed_points": 0,
    "in_progress": [],
    "ready": [],
}


def ceremony_path(workspace: Path | None = None) -> Path:
    root = workspace or WORKSPACE_ROOT
    return root / "ops" / "sprint" / "current.json"


def load_ceremony(workspace: Path | None = None) -> dict[str, Any]:
    """Load ops/sprint/current.json; return empty seed shape if missing/invalid."""
    path = ceremony_path(workspace)
    base = dict(EMPTY_CEREMONY)
    base["card_overlays"] = {}
    base["not_this_sprint"] = []
    base["agents"] = []
    base["ceremonies"] = {}
    base["notes"] = []
    base["in_progress"] = []
    base["ready"] = []
    if not path.is_file():
        base["notes"] = ["No ops/sprint/current.json yet — empty until Cadence seeds."]
        base["_path"] = str(path)
        base["_exists"] = False
        return base
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        base["notes"] = [f"Failed to read ceremony file: {e}"]
        base["_path"] = str(path)
        base["_exists"] = True
        base["_error"] = str(e)
        return base
    if not isinstance(raw, dict):
        base["notes"] = ["Ceremony file is not a JSON object."]
        base["_path"] = str(path)
        base["_exists"] = True
        return base

    # Detect schema: explicit v2, or card_overlays present, else v1
    schema = raw.get("schema_version")
    if schema is None:
        schema = 2 if isinstance(raw.get("card_overlays"), dict) else 1
    try:
        schema = int(schema)
    except (TypeError, ValueError):
        schema = 1
    raw["schema_version"] = schema

    for key, default in EMPTY_CEREMONY.items():
        if key not in raw:
            if isinstance(default, dict):
                raw[key] = {}
            elif isinstance(default, list):
                raw[key] = []
            else:
                raw[key] = default

    if not isinstance(raw.get("card_overlays"), dict):
        raw["card_overlays"] = {}
    for list_key in ("not_this_sprint", "agents", "notes", "in_progress", "ready"):
        if not isinstance(raw.get(list_key), list):
            raw[list_key] = []
    if not isinstance(raw.get("ceremonies"), dict):
        raw["ceremonies"] = {}

    try:
        cap = int(raw.get("agent_wip_cap") or DEFAULT_AGENT_WIP_CAP)
        raw["agent_wip_cap"] = max(1, cap)
    except (TypeError, ValueError):
        raw["agent_wip_cap"] = DEFAULT_AGENT_WIP_CAP

    # v1 soft fields
    if raw.get("wip_limit") is not None:
        try:
            raw["wip_limit"] = max(1, int(raw["wip_limit"]))
        except (TypeError, ValueError):
            raw["wip_limit"] = None

    raw["_path"] = str(path)
    raw["_exists"] = True
    return raw


def _normalize_agent_name(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lstrip("@")
    if not s:
        return None
    return s.split("(")[0].strip() or None


def _parse_agents(ceremony: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize agents list to {name, role, seat}."""
    out: list[dict[str, str]] = []
    for a in ceremony.get("agents") or []:
        if isinstance(a, str):
            n = _normalize_agent_name(a)
            if n:
                out.append({"name": n, "role": "implement", "seat": "implement"})
        elif isinstance(a, dict):
            n = _normalize_agent_name(a.get("name") or a.get("agent"))
            if not n:
                continue
            seat = str(a.get("seat") or "implement").strip().lower() or "implement"
            role = str(a.get("role") or seat).strip()
            out.append({"name": n, "role": role, "seat": seat})
    if out:
        seen: set[str] = set()
        deduped: list[dict[str, str]] = []
        for a in out:
            k = a["name"].lower()
            if k not in seen:
                seen.add(k)
                deduped.append(a)
        return deduped
    return [dict(a) for a in DEFAULT_AGENTS]


def _wip_roster(agents: list[dict[str, str]]) -> list[str]:
    """Names of agents that participate in free/busy (implement + gate)."""
    names: list[str] = []
    for a in agents:
        if a.get("seat") in WIP_SEATS:
            names.append(a["name"])
    return names or [a["name"] for a in agents if a.get("seat") == "implement"]


def _overlay_map(ceremony: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """card_overlays keys are issue number strings → int keys."""
    raw = ceremony.get("card_overlays") or {}
    out: dict[int, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        try:
            n = int(k)
        except (TypeError, ValueError):
            continue
        if isinstance(v, dict):
            out[n] = v
    return out


def _not_this_sprint_set(ceremony: dict[str, Any]) -> set[int]:
    out: set[int] = set()
    for it in ceremony.get("not_this_sprint") or []:
        if isinstance(it, dict) and it.get("number") is not None:
            try:
                out.add(int(it["number"]))
            except (TypeError, ValueError):
                pass
        elif isinstance(it, (int, str)):
            try:
                out.add(int(it))
            except (TypeError, ValueError):
                pass
    return out


def _match_roster(name: str, roster: list[str]) -> str | None:
    low = name.lower()
    for agent in roster:
        al = agent.lower()
        if low == al or low.startswith(al) or al.startswith(low):
            return agent
    return None


def _resolve_card_owner(
    card: dict[str, Any],
    overlay: dict[str, Any] | None,
    roster: list[str],
) -> str | None:
    """Owner resolution: board owner/assignees → overlay.owner → None."""
    candidates: list[str] = []
    n = _normalize_agent_name(card.get("owner"))
    if n:
        candidates.append(n)
    for a in card.get("assignees") or []:
        n2 = _normalize_agent_name(a)
        if n2:
            candidates.append(n2)
    if overlay:
        n3 = _normalize_agent_name(overlay.get("owner"))
        if n3:
            candidates.append(n3)
    for c in candidates:
        matched = _match_roster(c, roster)
        if matched:
            return matched
    # Return first raw candidate even if not in roster (for unmatched reporting)
    return candidates[0] if candidates else None


def apply_overlays(
    cards: list[dict[str, Any]],
    overlays: dict[int, dict[str, Any]],
    roster: list[str],
) -> list[dict[str, Any]]:
    """Merge ceremony overlays onto board cards (size/priority/owner/notes)."""
    out: list[dict[str, Any]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        merged = dict(card)
        num = card.get("number")
        ov: dict[str, Any] | None = None
        if num is not None:
            try:
                ov = overlays.get(int(num))
            except (TypeError, ValueError):
                ov = None
        if ov:
            if ov.get("size") is not None and merged.get("size") is None:
                merged["size"] = ov.get("size")
            if ov.get("priority") and not merged.get("priority"):
                merged["priority"] = ov.get("priority")
            if ov.get("notes"):
                merged["notes"] = ov.get("notes")
        owner = _resolve_card_owner(merged, ov, roster)
        if owner:
            merged["owner"] = owner
        out.append(merged)
    return out


def compute_agent_capacity(
    *,
    agents: list[dict[str, str]],
    in_progress: list[dict[str, Any]],
    pending_review: list[dict[str, Any]] | None = None,
    agent_wip_cap: int = DEFAULT_AGENT_WIP_CAP,
    board_source: str = "unknown",
) -> dict[str, Any]:
    """Free/busy under WIP=1. Only In Progress makes busy; Pending Review listed separately."""
    roster = _wip_roster(agents)
    cap = max(1, int(agent_wip_cap or DEFAULT_AGENT_WIP_CAP))
    busy_map: dict[str, list[dict[str, Any]]] = {n: [] for n in roster}
    gaps: list[str] = []
    unmatched: list[dict[str, Any]] = []

    for card in in_progress:
        if not isinstance(card, dict):
            continue
        owner = card.get("owner")
        num = card.get("number")
        if not owner:
            unmatched.append(
                {
                    "number": num,
                    "title": card.get("title"),
                    "reason": "no_owner",
                }
            )
            label = f"#{num}" if num is not None else "(unknown)"
            gaps.append(
                f"{label} has no owner — cannot compute free agents for that card"
            )
            continue
        matched = _match_roster(str(owner), roster)
        if matched:
            busy_map[matched].append(
                {
                    "number": num,
                    "title": card.get("title"),
                    "url": card.get("url"),
                }
            )
        else:
            unmatched.append(
                {
                    "number": num,
                    "title": card.get("title"),
                    "owners": [owner],
                    "reason": "owner_not_in_roster",
                }
            )
            gaps.append(
                f"#{num} owner {owner!r} not in implement/gate roster"
            )

    free: list[str] = []
    busy: list[dict[str, Any]] = []
    for name in roster:
        cards = busy_map.get(name) or []
        # dedupe by number
        seen: set[Any] = set()
        slim: list[dict[str, Any]] = []
        for c in cards:
            n = c.get("number")
            if n in seen:
                continue
            seen.add(n)
            slim.append(c)
        if slim:
            primary = slim[0]
            busy.append(
                {
                    "name": name,
                    "issue": primary.get("number"),
                    "title": primary.get("title"),
                    "wip": len(slim),
                    "wip_limit": cap,
                    "over": len(slim) > cap,
                    "cards": slim,
                }
            )
        else:
            free.append(name)

    # Pending Review holders (informational — not busy)
    pr_holders: list[dict[str, Any]] = []
    for card in pending_review or []:
        if not isinstance(card, dict):
            continue
        pr_holders.append(
            {
                "number": card.get("number"),
                "title": card.get("title"),
                "owner": card.get("owner"),
            }
        )

    if board_source not in ("github_project_v2",):
        gaps.append(
            f"Board source is {board_source!r} — free/busy may be incomplete without live Board."
        )
    gaps.append(
        "Live session presence is not available — free/busy is In Progress ownership only "
        "(Pending Review does not count as busy)."
    )

    data_gap: str | None = None
    # Prefer concrete missing-owner messages for API contract
    owner_gaps = [g for g in gaps if "has no owner" in g]
    if owner_gaps:
        data_gap = "; ".join(owner_gaps)
    elif any("not in implement" in g for g in gaps):
        data_gap = "; ".join(g for g in gaps if "not in implement" in g)

    return {
        "cap": cap,
        "rule": "WIP=1 per agent; free iff zero In Progress cards (Pending Review ≠ busy)",
        "agent_wip_limit": cap,
        "roster_source": "ceremony" if ceremony_has_custom_agents(agents) else "default",
        "roster": roster,
        "agents_full": agents,
        "free": free,
        "busy": busy,
        "free_count": len(free),
        "busy_count": len(busy),
        "pending_review_holders": pr_holders,
        "unmatched_cards": unmatched,
        "data_gap": data_gap,
        "data_gaps": gaps,
    }


def ceremony_has_custom_agents(agents: list[dict[str, str]]) -> bool:
    default_names = [a["name"].lower() for a in DEFAULT_AGENTS]
    got = [a["name"].lower() for a in agents]
    return got != default_names


def _ceremony_public(ceremony: dict[str, Any]) -> dict[str, Any]:
    """Public ceremony fields for API (schema v2 + soft v1 for UI compat)."""
    overlays_pub: dict[str, Any] = {}
    for k, v in (ceremony.get("card_overlays") or {}).items():
        if isinstance(v, dict):
            overlays_pub[str(k)] = {
                "size": v.get("size"),
                "priority": v.get("priority"),
                "owner": v.get("owner"),
                "notes": v.get("notes"),
            }

    # v1 list shape from overlays for older UI (optional soft view)
    v1_ip: list[dict[str, Any]] = []
    v1_ready: list[dict[str, Any]] = []
    for it in ceremony.get("in_progress") or []:
        if isinstance(it, dict):
            v1_ip.append(
                {
                    "number": it.get("number"),
                    "title": it.get("title") or "(untitled)",
                    "size": it.get("size"),
                    "priority": it.get("priority"),
                    "owner": it.get("owner"),
                    "url": it.get("url"),
                }
            )
    for it in ceremony.get("ready") or []:
        if isinstance(it, dict):
            v1_ready.append(
                {
                    "number": it.get("number"),
                    "title": it.get("title") or "(untitled)",
                    "size": it.get("size"),
                    "priority": it.get("priority"),
                    "owner": it.get("owner"),
                    "url": it.get("url"),
                }
            )

    not_sprint: list[dict[str, Any]] = []
    for it in ceremony.get("not_this_sprint") or []:
        if isinstance(it, dict):
            not_sprint.append(
                {
                    "number": it.get("number"),
                    "title": it.get("title"),
                    "reason": it.get("reason") or it.get("notes"),
                    "size": it.get("size"),
                    "priority": it.get("priority"),
                    "owner": it.get("owner"),
                    "url": it.get("url"),
                }
            )
        elif isinstance(it, (int, str)):
            try:
                not_sprint.append({"number": int(it), "reason": None})
            except (TypeError, ValueError):
                pass

    return {
        "schema_version": ceremony.get("schema_version") or 2,
        "sprint_id": ceremony.get("sprint_id"),
        "goal": ceremony.get("goal"),
        "agent_wip_cap": ceremony.get("agent_wip_cap") or DEFAULT_AGENT_WIP_CAP,
        "board_sot": ceremony.get("board_sot") or "github_project_v2",
        "board_url": ceremony.get("board_url") or DEFAULT_BOARD_URL,
        "card_overlays": overlays_pub,
        "not_this_sprint": not_sprint,
        "agents": _parse_agents(ceremony),
        "notes": [str(n) for n in (ceremony.get("notes") or []) if n is not None],
        "updated_at": ceremony.get("updated_at"),
        "updated_by": ceremony.get("updated_by"),
        "path": ceremony.get("_path"),
        "exists": bool(ceremony.get("_exists")),
        # Soft v1 fields (UI merge helpers / degrade) — not column SoT when board live
        "wip_limit": ceremony.get("wip_limit"),
        "capacity_points": ceremony.get("capacity_points"),
        "committed_points": ceremony.get("committed_points") or 0,
        "in_progress": v1_ip,
        "ready": v1_ready,
    }


def _empty_columns() -> dict[str, list]:
    return {k: [] for k in BOARD_COLUMN_KEYS}


def _columns_from_v1_ceremony(ceremony: dict[str, Any]) -> dict[str, list]:
    """Degrade-only: when Board is down, show v1 lists if present."""
    cols = _empty_columns()

    def slim(items: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            out.append(
                {
                    "number": it.get("number"),
                    "title": it.get("title") or "(untitled)",
                    "size": it.get("size"),
                    "priority": it.get("priority"),
                    "owner": it.get("owner"),
                    "url": it.get("url"),
                    "assignees": list(it.get("assignees") or [])
                    if isinstance(it.get("assignees"), list)
                    else [],
                }
            )
        return out

    cols["Ready"] = slim(ceremony.get("ready") or [])
    cols["In Progress"] = slim(ceremony.get("in_progress") or [])
    return cols


def _slim_card(it: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": it.get("number"),
        "title": it.get("title") or "(untitled)",
        "size": it.get("size_hint") or it.get("size"),
        "priority": it.get("priority_hint") or it.get("priority"),
        "owner": it.get("owner"),
        "assignees": list(it.get("assignees") or [])
        if isinstance(it.get("assignees"), list)
        else [],
        "url": it.get("url"),
        "updated_at": it.get("updated_at"),
        "labels": it.get("labels") or [],
    }


def _board_from_live(live: dict[str, Any]) -> dict[str, Any]:
    """Map sprint_board.sprint_payload → Cadence board shape (six columns)."""
    if not live.get("ok"):
        return {
            "source": "error",
            "error": live.get("error") or "Board fetch failed",
            "columns": _empty_columns(),
            "counts": {k: 0 for k in BOARD_COLUMN_KEYS},
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "url": (live.get("board") or {}).get("url") if live.get("board") else None,
        }
    cols_raw = live.get("columns") or {}
    validate = cols_raw.get("Validate ($0)") or cols_raw.get("Validate") or []
    columns = {
        "Parked": cols_raw.get("Parked") or [],
        "Validate": validate,
        "Ready": cols_raw.get("Ready") or [],
        "In Progress": cols_raw.get("In Progress") or [],
        "Pending Review": cols_raw.get("Pending Review") or [],
        "Done": cols_raw.get("Done") or [],
    }
    slim: dict[str, list] = {}
    for name, items in columns.items():
        slim[name] = []
        for it in items:
            if isinstance(it, dict):
                slim[name].append(_slim_card(it))
    board_meta = live.get("board") or {}
    return {
        "source": "github_project_v2",
        "columns": slim,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "url": board_meta.get("url"),
        "title": board_meta.get("title"),
        "counts": {k: len(v) for k, v in slim.items()},
        "wip": live.get("wip"),
    }


def sprint_payload(
    workspace: Path | None = None,
    *,
    live_board: bool = True,
) -> dict[str, Any]:
    """GET /api/sprint payload — Board columns + ceremony v2 + free agents."""
    ceremony_raw = load_ceremony(workspace)
    ceremony = _ceremony_public(ceremony_raw)
    agents_list = _parse_agents(ceremony_raw)
    roster = _wip_roster(agents_list)
    overlays = _overlay_map(ceremony_raw)
    not_sprint = _not_this_sprint_set(ceremony_raw)
    agent_cap = int(ceremony.get("agent_wip_cap") or DEFAULT_AGENT_WIP_CAP)

    board: dict[str, Any]
    if live_board:
        try:
            from sprint_board import sprint_payload as board_live  # noqa: WPS433

            live = board_live(include_done=True)
            board = _board_from_live(live)
        except Exception as e:  # never blank the tab
            board = {
                "source": "error",
                "error": str(e),
                "columns": _columns_from_v1_ceremony(ceremony_raw),
                "counts": {
                    k: len(v)
                    for k, v in _columns_from_v1_ceremony(ceremony_raw).items()
                },
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "url": ceremony.get("board_url"),
            }
            if board["columns"]["In Progress"] or board["columns"]["Ready"]:
                board["source"] = "ceremony_only"
                board["error"] = str(e)
    else:
        cols = _columns_from_v1_ceremony(ceremony_raw)
        board = {
            "source": "ceremony_only",
            "columns": cols,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "url": ceremony.get("board_url"),
            "counts": {k: len(cols.get(k) or []) for k in BOARD_COLUMN_KEYS},
        }

    # Apply overlays to all active columns used by UI
    cols = board.get("columns") or _empty_columns()
    for key in BOARD_COLUMN_KEYS:
        cols[key] = apply_overlays(cols.get(key) or [], overlays, roster)
    board["columns"] = cols
    board["counts"] = {k: len(cols.get(k) or []) for k in BOARD_COLUMN_KEYS}

    ip = cols.get("In Progress") or []
    pending = cols.get("Pending Review") or []
    ready = cols.get("Ready") or []
    # Filter not-this-sprint from Ready display list (Board still has them)
    ready_display = [
        c
        for c in ready
        if c.get("number") is None or int(c["number"]) not in not_sprint
    ]

    agents = compute_agent_capacity(
        agents=agents_list,
        in_progress=ip,
        pending_review=pending,
        agent_wip_cap=agent_cap,
        board_source=str(board.get("source") or "unknown"),
    )

    # Merge file ceremonies over defaults
    ceremonies = dict(DEFAULT_CEREMONIES)
    file_cer = ceremony_raw.get("ceremonies") or {}
    if isinstance(file_cer, dict):
        ceremonies.update({k: v for k, v in file_cer.items() if v is not None})

    board_ip_count = len(ip)
    return {
        "ok": True,
        "ceremony": ceremony,
        "board": board,
        "ready_display": ready_display,
        "agents": agents,
        "wip": {
            "model": "per_agent",
            "cap": agent_cap,
            "busy_count": agents["busy_count"],
            "free_count": agents["free_count"],
            "board_in_progress_count": board_ip_count,
            "note": "Pending Review does not count as busy",
            # soft legacy keys for older UI bits
            "limit": agent_cap,
            "current": board_ip_count,
            "over": False,
            "remaining": agents["free_count"],
            "source": "board"
            if board.get("source") == "github_project_v2"
            else board.get("source") or "ceremony",
        },
        "ceremonies": ceremonies,
        "disclaimer": (
            "ops/backlog is the autonomous experiment queue — not the sprint board. "
            "Buzz Board remains portfolio SoT; UI never writes Board Status and does not "
            "auto-promote Ready → In Progress."
        ),
        "playbook": "GUIDES/CADENCE_SCRUM_CEREMONIES.md",
    }


if __name__ == "__main__":
    print(json.dumps(sprint_payload(live_board=False), indent=2)[:4000])
