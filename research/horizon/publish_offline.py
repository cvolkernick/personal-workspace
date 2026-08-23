"""Nest / GitHub offline publish helper for Horizon (#301).

While the live Horizon host is parked, nest is the write path: stamp a new
``version_id`` onto world_state / brief artifacts already in this tree and
open a PR. Grok eng-gates; Orchestra / Horizon consumers read the published
nest files.

This helper does **not** invent facts, rates, or regime prints. Default is a
restamp of the existing latest world-state. ``from_fixtures=True`` is an
explicit opt-in that wraps the shipped offline pipeline (fixtures already in
tree). If there is no latest SoT and fixtures are not requested, the result
is held and nothing is written.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from research.horizon.store import (
    DEFAULT_DATA_DIR,
    brief_latest_paths,
    load_world_state,
    save_brief,
    save_world_state,
    world_state_history_path,
    world_state_latest_path,
)
from research.horizon.strategy_link import link_world_to_strategy, load_strategy
from research.horizon.synthesis import render_markdown, synthesize
from research.horizon.world_state import iso_now, make_version_id

# Existing schema: UTC compact ISO used by make_version_id / ARCHITECTURE.md
VERSION_ID_RE = re.compile(r"^\d{8}T\d{6}Z$")
PUBLISH_PATH = "nest_gh_offline"

# Relative to data/ — the SoT pointers Meridian commits on an offline PR
PUBLISH_SOT_RELATIVE = (
    "world_state_latest.json",
    "briefs/brief_latest.json",
    "briefs/brief_latest.md",
)

# Patterns that must never appear in published nest artifacts
_SECRET_LEAK_RE = re.compile(
    r"("
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|AKIA[0-9A-Z]{16}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r")",
    re.MULTILINE,
)


def is_valid_version_id(version_id: str) -> bool:
    return bool(VERSION_ID_RE.match(version_id))


def stamp_version_id(when: Optional[datetime] = None) -> str:
    """Return a schema-matching version_id (YYYYMMDDTHHMMSSZ)."""
    vid = make_version_id(when)
    if not is_valid_version_id(vid):
        raise ValueError(f"make_version_id produced invalid stamp: {vid!r}")
    return vid


def require_version_id(version_id: Optional[str], *, when: Optional[datetime] = None) -> str:
    vid = version_id if version_id is not None else stamp_version_id(when)
    if not is_valid_version_id(vid):
        raise ValueError(
            f"version_id must match YYYYMMDDTHHMMSSZ (got {version_id!r})"
        )
    return vid


def publish_sot_paths(data_dir: Optional[Path] = None) -> dict[str, Path]:
    """Absolute paths of the nest SoT pointers consumers read."""
    root = Path(data_dir or DEFAULT_DATA_DIR)
    latest_json, latest_md = brief_latest_paths(root)
    return {
        "world_state_latest": world_state_latest_path(root),
        "brief_latest_json": latest_json,
        "brief_latest_md": latest_md,
        "data_dir": root,
    }


def artifact_has_secret_leak(text: str) -> bool:
    return bool(_SECRET_LEAK_RE.search(text or ""))


def _restamp_state(state: dict[str, Any], version_id: str) -> dict[str, Any]:
    """Stamp version_id only. Nodes, facts, and regime scores are unchanged."""
    out = copy.deepcopy(state)
    prior = str(out.get("version_id") or "")
    out["version_id"] = version_id
    out["updated_at"] = iso_now()
    meta = dict(out.get("meta") or {})
    if prior:
        meta["prior_version_id"] = prior
    meta["publish_path"] = PUBLISH_PATH
    meta["run_id"] = version_id
    meta["held"] = False
    out["meta"] = meta
    regime = out.get("regime")
    if isinstance(regime, dict):
        regime = dict(regime)
        regime["version_id"] = version_id
        out["regime"] = regime
    return out


def _write_brief_from_state(
    state: dict[str, Any],
    *,
    workspace: Optional[Path],
    data_dir: Path,
) -> dict[str, Path]:
    strategy = load_strategy(workspace)
    linkages = link_world_to_strategy(state, strategy)
    brief = synthesize(state, strategy, linkages)
    markdown = render_markdown(brief)
    return save_brief(brief, markdown, data_dir)


def _scan_written_paths(paths: list[Path]) -> list[str]:
    leaks: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if artifact_has_secret_leak(text):
            leaks.append(str(path))
    return leaks


def _held_result(
    *,
    reason: str,
    data_dir: Path,
    version_id: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "held": True,
        "reason": reason,
        "version_id": version_id,
        "publish_path": PUBLISH_PATH,
        "from_fixtures": False,
        "paths": {k: str(v) for k, v in publish_sot_paths(data_dir).items()},
        "wrote": [],
    }


def publish_offline(
    *,
    workspace: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    version_id: Optional[str] = None,
    from_fixtures: bool = False,
    fixture_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Stamp nest SoT from existing latest (default) or shipped fixtures.

    Returns a result dict. ``held=True`` means no write (honest empty).
    """
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    vid = require_version_id(version_id)

    if from_fixtures:
        from research.horizon.pipeline import run_pipeline

        result = run_pipeline(
            workspace=workspace,
            data_dir=data_dir,
            offline=True,
            link_only=False,
            fixture_path=fixture_path,
        )
        state = copy.deepcopy(result["state"])
        # Keep the pipeline's own version_id unless the caller pinned one.
        if version_id is not None:
            state = _restamp_state(state, vid)
        else:
            vid = str(state.get("version_id") or vid)
            if not is_valid_version_id(vid):
                state = _restamp_state(state, require_version_id(None))
                vid = state["version_id"]
            meta = dict(state.get("meta") or {})
            meta["publish_path"] = PUBLISH_PATH
            meta["from_fixtures"] = True
            meta["held"] = False
            state["meta"] = meta
        save_world_state(state, data_dir)
        brief_paths = _write_brief_from_state(
            state, workspace=workspace, data_dir=data_dir
        )
        written = [
            world_state_latest_path(data_dir),
            world_state_history_path(vid, data_dir),
            brief_paths["latest_json"],
            brief_paths["latest_md"],
        ]
        leaks = _scan_written_paths(written)
        if leaks:
            raise RuntimeError(f"refusing to publish; secret-like payload in {leaks}")
        return {
            "ok": True,
            "held": False,
            "reason": "published from shipped fixtures (explicit)",
            "version_id": vid,
            "publish_path": PUBLISH_PATH,
            "from_fixtures": True,
            "prior_version_id": (state.get("meta") or {}).get("prior_version_id"),
            "paths": {
                "data_dir": str(data_dir),
                "world_state_latest": str(world_state_latest_path(data_dir)),
                "history": str(world_state_history_path(vid, data_dir)),
                "brief_latest_json": str(brief_paths["latest_json"]),
                "brief_latest_md": str(brief_paths["latest_md"]),
            },
            "wrote": [str(p) for p in written],
        }

    previous = load_world_state(data_dir)
    if previous is None:
        return _held_result(
            reason=(
                "no world_state_latest.json; leave prior / held. "
                "Do not invent a macro print. Pass from_fixtures=True only if "
                "shipped fixtures are the intended real input."
            ),
            data_dir=data_dir,
            version_id=vid,
        )

    state = _restamp_state(previous, vid)
    save_world_state(state, data_dir)
    brief_paths = _write_brief_from_state(
        state, workspace=workspace, data_dir=data_dir
    )
    written = [
        world_state_latest_path(data_dir),
        world_state_history_path(vid, data_dir),
        brief_paths["latest_json"],
        brief_paths["latest_md"],
    ]
    leaks = _scan_written_paths(written)
    if leaks:
        raise RuntimeError(f"refusing to publish; secret-like payload in {leaks}")

    return {
        "ok": True,
        "held": False,
        "reason": "restamped existing latest world-state (no new facts/rates/regime)",
        "version_id": vid,
        "publish_path": PUBLISH_PATH,
        "from_fixtures": False,
        "prior_version_id": (state.get("meta") or {}).get("prior_version_id"),
        "paths": {
            "data_dir": str(data_dir),
            "world_state_latest": str(world_state_latest_path(data_dir)),
            "history": str(world_state_history_path(vid, data_dir)),
            "brief_latest_json": str(brief_paths["latest_json"]),
            "brief_latest_md": str(brief_paths["latest_md"]),
        },
        "wrote": [str(p) for p in written],
    }


def result_json(result: dict[str, Any]) -> str:
    summary = {
        "ok": result.get("ok"),
        "held": result.get("held"),
        "reason": result.get("reason"),
        "version_id": result.get("version_id"),
        "publish_path": result.get("publish_path"),
        "from_fixtures": result.get("from_fixtures"),
        "prior_version_id": result.get("prior_version_id"),
        "paths": result.get("paths"),
    }
    return json.dumps(summary, indent=2) + "\n"
