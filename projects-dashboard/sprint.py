#!/usr/bin/env python3
"""Sprint tab: ceremony state (ops/sprint/current.json) + optional Buzz Board live.

Cadence owns ceremony file updates after planning. Dashboard renders only —
never auto-promotes Ready → In Progress.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workspace import WORKSPACE_ROOT

DEFAULT_WIP_LIMIT = 3
DEFAULT_CAPACITY = 13
DEFAULT_BOARD_URL = "https://github.com/users/cvolkernick/projects/1"

DEFAULT_CEREMONIES = {
    "grooming_cron": "0 16 * * 3",
    "planning_cron": "0 16 * * 1",
    "grooming_workflow_id": "95d911df-509b-4eac-a4f5-ffeaa4c1e3da",
    "planning_workflow_id": "b85c12fa-e7e5-43b3-8292-295a1e9f9783",
}

EMPTY_CEREMONY: dict[str, Any] = {
    "sprint_id": None,
    "goal": None,
    "wip_limit": DEFAULT_WIP_LIMIT,
    "capacity_points": DEFAULT_CAPACITY,
    "committed_points": 0,
    "in_progress": [],
    "ready": [],
    "not_this_sprint": [],
    "notes": [],
    "updated_at": None,
    "updated_by": None,
    "board_url": DEFAULT_BOARD_URL,
}


def ceremony_path(workspace: Path | None = None) -> Path:
    root = workspace or WORKSPACE_ROOT
    return root / "ops" / "sprint" / "current.json"


def load_ceremony(workspace: Path | None = None) -> dict[str, Any]:
    """Load ops/sprint/current.json; return empty seed shape if missing/invalid."""
    path = ceremony_path(workspace)
    base = dict(EMPTY_CEREMONY)
    if not path.is_file():
        base["notes"] = ["No ops/sprint/current.json yet — empty until first planning."]
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
    for key, default in EMPTY_CEREMONY.items():
        if key not in raw:
            raw[key] = default if not isinstance(default, list) else []
    # Normalize list fields
    for list_key in ("in_progress", "ready", "not_this_sprint", "notes"):
        if not isinstance(raw.get(list_key), list):
            raw[list_key] = []
    try:
        raw["wip_limit"] = max(1, int(raw.get("wip_limit") or DEFAULT_WIP_LIMIT))
    except (TypeError, ValueError):
        raw["wip_limit"] = DEFAULT_WIP_LIMIT
    try:
        raw["capacity_points"] = int(raw.get("capacity_points") or DEFAULT_CAPACITY)
    except (TypeError, ValueError):
        raw["capacity_points"] = DEFAULT_CAPACITY
    try:
        raw["committed_points"] = int(raw.get("committed_points") or 0)
    except (TypeError, ValueError):
        raw["committed_points"] = 0
    raw["_path"] = str(path)
    raw["_exists"] = True
    return raw


def _card_list(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
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
            }
        )
    return out


def _ceremony_public(ceremony: dict[str, Any]) -> dict[str, Any]:
    """Strip internal keys for API clients."""
    return {
        "sprint_id": ceremony.get("sprint_id"),
        "goal": ceremony.get("goal"),
        "wip_limit": ceremony.get("wip_limit") or DEFAULT_WIP_LIMIT,
        "capacity_points": ceremony.get("capacity_points") or DEFAULT_CAPACITY,
        "committed_points": ceremony.get("committed_points") or 0,
        "in_progress": _card_list(ceremony.get("in_progress") or []),
        "ready": _card_list(ceremony.get("ready") or []),
        "not_this_sprint": _card_list(ceremony.get("not_this_sprint") or []),
        "notes": [
            str(n) for n in (ceremony.get("notes") or []) if n is not None
        ],
        "updated_at": ceremony.get("updated_at"),
        "updated_by": ceremony.get("updated_by"),
        "board_url": ceremony.get("board_url") or DEFAULT_BOARD_URL,
        "path": ceremony.get("_path"),
        "exists": bool(ceremony.get("_exists")),
    }


def _columns_from_ceremony(ceremony: dict[str, Any]) -> dict[str, list]:
    return {
        "Parked": [],
        "Validate": [],
        "Ready": _card_list(ceremony.get("ready") or []),
        "In Progress": _card_list(ceremony.get("in_progress") or []),
        "Done": [],
    }


def _board_from_live(live: dict[str, Any]) -> dict[str, Any]:
    """Map sprint_board.sprint_payload → Cadence board shape."""
    if not live.get("ok"):
        return {
            "source": "error",
            "error": live.get("error") or "Board fetch failed",
            "columns": {
                "Parked": [],
                "Validate": [],
                "Ready": [],
                "In Progress": [],
                "Done": [],
            },
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "url": (live.get("board") or {}).get("url") if live.get("board") else None,
        }
    cols_raw = live.get("columns") or {}
    # Normalize Validate ($0) → Validate for UI
    validate = cols_raw.get("Validate ($0)") or cols_raw.get("Validate") or []
    columns = {
        "Parked": cols_raw.get("Parked") or [],
        "Validate": validate,
        "Ready": cols_raw.get("Ready") or [],
        "In Progress": cols_raw.get("In Progress") or [],
        "Done": cols_raw.get("Done") or [],
    }
    # Slim cards for tab (number/title/url/size/priority)
    slim: dict[str, list] = {}
    for name, items in columns.items():
        slim[name] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            slim[name].append(
                {
                    "number": it.get("number"),
                    "title": it.get("title") or "(untitled)",
                    "size": it.get("size_hint") or it.get("size"),
                    "priority": it.get("priority_hint") or it.get("priority"),
                    "owner": it.get("owner"),
                    "url": it.get("url"),
                    "updated_at": it.get("updated_at"),
                    "labels": it.get("labels") or [],
                }
            )
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
    """GET /api/sprint payload — ceremony required; board optional/degraded."""
    ceremony_raw = load_ceremony(workspace)
    ceremony = _ceremony_public(ceremony_raw)
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
                "columns": _columns_from_ceremony(ceremony_raw),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "url": ceremony.get("board_url"),
            }
            # Prefer ceremony lists when live blows up
            if board["columns"]["In Progress"] or board["columns"]["Ready"]:
                board["source"] = "ceremony_only"
                board["error"] = str(e)
    else:
        board = {
            "source": "ceremony_only",
            "columns": _columns_from_ceremony(ceremony_raw),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "url": ceremony.get("board_url"),
            "counts": {
                "In Progress": len(ceremony["in_progress"]),
                "Ready": len(ceremony["ready"]),
            },
        }

    # Prefer ceremony WIP list for meter when ceremony has cards; else board
    ip_ceremony = ceremony["in_progress"]
    ip_board = (board.get("columns") or {}).get("In Progress") or []
    wip_current = len(ip_ceremony) if ip_ceremony else len(ip_board)
    wip_limit = int(ceremony.get("wip_limit") or DEFAULT_WIP_LIMIT)

    return {
        "ok": True,
        "ceremony": ceremony,
        "board": board,
        "wip": {
            "limit": wip_limit,
            "current": wip_current,
            "over": wip_current > wip_limit,
            "remaining": max(0, wip_limit - wip_current),
            "source": "ceremony" if ip_ceremony else (
                "board" if board.get("source") == "github_project_v2" else "ceremony"
            ),
        },
        "ceremonies": dict(DEFAULT_CEREMONIES),
        "disclaimer": (
            "ops/backlog is the autonomous experiment queue — not the sprint board. "
            "Buzz Board remains portfolio SoT; this tab does not auto-promote Ready → In Progress."
        ),
        "playbook": "GUIDES/CADENCE_SCRUM_CEREMONIES.md",
    }


if __name__ == "__main__":
    print(json.dumps(sprint_payload(live_board=False), indent=2)[:3000])
