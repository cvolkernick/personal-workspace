"""Operator intent / focus feedback for Orchestrator.

Stores what the user is trying to accomplish and balance so synthesis and
Conductor can prioritize highest-value next actions (not dashboard sprawl).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INTENT_PATH = WORKSPACE_ROOT / "strategy" / "intent.json"

DEFAULT_INTENT: dict[str, Any] = {
    "version": 1,
    "accomplishing": (
        "Build wealth and optionality via Energy, Bitcoin, and AI/Autonomy leverage "
        "while keeping health and systems sustainable."
    ),
    "balancing": [
        "Deep work on AI/Autonomy tooling vs reactive dashboard maintenance",
        "Fitness/recovery as energy enabler vs shipping leverage automations",
        "Treasury/liquidity hygiene vs research time on thematic bets",
    ],
    "constraints": [
        "Prefer one primary next action at a time",
        "Avoid opening every dashboard unless needed for that action",
        "Keep today list to 2–5 real items, not templates",
    ],
    "streamline_goals": [
        "Fewer open loops; clearer single next step",
        "Use domain dashboards only to execute or verify, not to re-decide",
        "Batch hygiene (git, treasury fields) so it does not interrupt deep work",
    ],
    "time_horizon": "next 24–48 hours",
    "energy_notes": "",
    "updated_at": None,
    "notes": "",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def intent_path(workspace: Optional[Path] = None) -> Path:
    root = Path(workspace or WORKSPACE_ROOT).resolve()
    return root / "strategy" / "intent.json"


def load_intent(workspace: Optional[Path] = None) -> dict[str, Any]:
    path = intent_path(workspace)
    if not path.is_file():
        data = dict(DEFAULT_INTENT)
        data["updated_at"] = None
        data["path"] = "strategy/intent.json"
        data["exists"] = False
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = dict(DEFAULT_INTENT)
        data["updated_at"] = None
        data["path"] = "strategy/intent.json"
        data["exists"] = False
        data["error"] = "invalid intent.json"
        return data
    if not isinstance(raw, dict):
        data = dict(DEFAULT_INTENT)
        data["exists"] = False
        return data
    out = dict(DEFAULT_INTENT)
    out.update({k: raw[k] for k in raw if k in DEFAULT_INTENT or k in (
        "updated_at", "notes", "version", "accomplishing", "balancing",
        "constraints", "streamline_goals", "time_horizon", "energy_notes",
    )})
    # normalize lists
    for key in ("balancing", "constraints", "streamline_goals"):
        val = out.get(key)
        if isinstance(val, str):
            out[key] = [ln.strip() for ln in val.splitlines() if ln.strip()]
        elif not isinstance(val, list):
            out[key] = list(DEFAULT_INTENT[key])
        else:
            out[key] = [str(x).strip() for x in val if str(x).strip()]
    out["path"] = "strategy/intent.json"
    out["exists"] = True
    return out


def save_intent(
    updates: dict[str, Any],
    workspace: Optional[Path] = None,
) -> dict[str, Any]:
    """Merge updates into intent.json and return the saved document."""
    path = intent_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = load_intent(workspace)
    # drop meta keys from merge base
    base = {k: v for k, v in current.items() if k not in ("path", "exists", "error")}

    allowed = {
        "accomplishing",
        "balancing",
        "constraints",
        "streamline_goals",
        "time_horizon",
        "energy_notes",
        "notes",
    }
    for key in allowed:
        if key not in updates:
            continue
        val = updates[key]
        if key in ("balancing", "constraints", "streamline_goals"):
            if isinstance(val, str):
                base[key] = [ln.strip() for ln in val.splitlines() if ln.strip()]
            elif isinstance(val, list):
                base[key] = [str(x).strip() for x in val if str(x).strip()]
        elif isinstance(val, str):
            base[key] = val.strip()
        else:
            base[key] = val

    base["version"] = int(base.get("version") or 1)
    base["updated_at"] = _now()
    path.write_text(json.dumps(base, indent=2) + "\n", encoding="utf-8")
    saved = load_intent(workspace)
    return saved


def intent_for_context(intent: dict[str, Any]) -> dict[str, Any]:
    """Slim intent blob for model context / payload."""
    return {
        "accomplishing": intent.get("accomplishing") or "",
        "balancing": list(intent.get("balancing") or [])[:12],
        "constraints": list(intent.get("constraints") or [])[:12],
        "streamline_goals": list(intent.get("streamline_goals") or [])[:12],
        "time_horizon": intent.get("time_horizon") or "",
        "energy_notes": (intent.get("energy_notes") or "")[:500],
        "notes": (intent.get("notes") or "")[:500],
        "updated_at": intent.get("updated_at"),
    }


FOCUS_BRIEF_PROMPT = """Using my operator intent and the orchestration data, give a focused brief.

Structure your reply exactly with these sections:

## Primary next action
One concrete action for the stated time horizon. Why it is highest value *given my intent*.

## Why not the other candidates
2–4 bullets: what you deprioritized and why (avoid re-opening every domain).

## Streamline moves
2–4 ways to reduce context-switching, dashboard thrash, or open loops this period.

## Balance check
How this path respects what I am balancing; any one hygiene item that would block deep work.

## Drill-down only if needed
At most one subordinate dashboard to open (or "none") and the single task to do there.

Be ruthless about focus. Prefer finishing/shipping over exploring more systems.
"""
