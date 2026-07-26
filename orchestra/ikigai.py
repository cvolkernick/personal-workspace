"""Ikigai Layer 0 loaders and save/merge for Orchestrator.

Source of truth: strategy/ikigai/pillars.json (+ narrative ikigai.md).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

PILLAR_KEYS = ("love", "good_at", "world_needs", "paid_for")
INTERSECTION_KEYS = ("passion", "mission", "profession", "vocation")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ikigai_dir(workspace: Optional[Path] = None) -> Path:
    return Path(workspace or WORKSPACE_ROOT).resolve() / "strategy" / "ikigai"


def pillars_path(workspace: Optional[Path] = None) -> Path:
    return ikigai_dir(workspace) / "pillars.json"


def narrative_path(workspace: Optional[Path] = None) -> Path:
    return ikigai_dir(workspace) / "ikigai.md"


def _empty_ikigai() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "center": {"statement": "", "themes": []},
        "pillars": {
            k: {"items": [], "notes": ""} for k in PILLAR_KEYS
        },
        "intersections": {
            k: {"summary": "", "items": []} for k in INTERSECTION_KEYS
        },
        "out_of_bounds": [],
        "linked_bets": [],
        "linked_life_domains": [],
        "review_cadence": "quarterly",
    }


def _normalize_pillar(val: Any) -> dict[str, Any]:
    if not isinstance(val, dict):
        return {"items": [], "notes": ""}
    items = val.get("items") or []
    if isinstance(items, str):
        items = [ln.strip() for ln in items.splitlines() if ln.strip()]
    else:
        items = [str(x).strip() for x in items if str(x).strip()]
    return {"items": items, "notes": str(val.get("notes") or "")}


def _normalize_intersection(val: Any) -> dict[str, Any]:
    if not isinstance(val, dict):
        return {"summary": "", "items": []}
    items = val.get("items") or []
    if isinstance(items, str):
        items = [ln.strip() for ln in items.splitlines() if ln.strip()]
    else:
        items = [str(x).strip() for x in items if str(x).strip()]
    return {
        "summary": str(val.get("summary") or "").strip(),
        "items": items,
    }


def normalize_ikigai(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a pillars.json-shaped dict (pure)."""
    base = _empty_ikigai()
    if not isinstance(raw, dict):
        return base

    base["version"] = int(raw.get("version") or 1)
    base["updated_at"] = raw.get("updated_at")
    base["review_cadence"] = str(raw.get("review_cadence") or "quarterly")

    center = raw.get("center") or {}
    if isinstance(center, dict):
        themes = center.get("themes") or []
        if isinstance(themes, str):
            themes = [t.strip() for t in themes.splitlines() if t.strip()]
        else:
            themes = [str(t).strip() for t in themes if str(t).strip()]
        base["center"] = {
            "statement": str(center.get("statement") or "").strip(),
            "themes": themes,
        }

    pillars = raw.get("pillars") or {}
    if isinstance(pillars, dict):
        for k in PILLAR_KEYS:
            base["pillars"][k] = _normalize_pillar(pillars.get(k))

    inter = raw.get("intersections") or {}
    if isinstance(inter, dict):
        for k in INTERSECTION_KEYS:
            base["intersections"][k] = _normalize_intersection(inter.get(k))

    for list_key in ("out_of_bounds", "linked_bets", "linked_life_domains"):
        val = raw.get(list_key) or []
        if isinstance(val, str):
            base[list_key] = [ln.strip() for ln in val.splitlines() if ln.strip()]
        elif isinstance(val, list):
            base[list_key] = [str(x).strip() for x in val if str(x).strip()]
        else:
            base[list_key] = []

    return base


def load_ikigai(workspace: Optional[Path] = None) -> dict[str, Any]:
    """Load and normalize strategy/ikigai/pillars.json."""
    path = pillars_path(workspace)
    narrative = narrative_path(workspace)
    meta = {
        "path": "strategy/ikigai/pillars.json",
        "narrative_path": "strategy/ikigai/ikigai.md",
        "exists": path.is_file(),
        "narrative_exists": narrative.is_file(),
    }
    if not path.is_file():
        data = _empty_ikigai()
        data.update(meta)
        data["ok"] = False
        data["error"] = "pillars.json missing"
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        data = _empty_ikigai()
        data.update(meta)
        data["ok"] = False
        data["error"] = f"invalid pillars.json: {e}"
        return data
    data = normalize_ikigai(raw if isinstance(raw, dict) else {})
    data.update(meta)
    data["ok"] = bool(data["center"].get("statement") or any(
        data["pillars"][k]["items"] for k in PILLAR_KEYS
    ))
    return data


def save_ikigai(
    updates: dict[str, Any],
    workspace: Optional[Path] = None,
) -> dict[str, Any]:
    """Merge updates into pillars.json and return reloaded document.

    Accepts partial updates: center, pillars (dict of pillar key → items/notes),
    intersections, out_of_bounds, linked_bets, linked_life_domains, review_cadence.
    List fields may be list or newline-separated string.
    """
    path = pillars_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = load_ikigai(workspace)
    base = normalize_ikigai(
        {k: v for k, v in current.items() if k not in (
            "path", "narrative_path", "exists", "narrative_exists", "ok", "error"
        )}
    )

    if "center" in updates and isinstance(updates["center"], dict):
        c = updates["center"]
        if "statement" in c:
            base["center"]["statement"] = str(c.get("statement") or "").strip()
        if "themes" in c:
            th = c["themes"]
            if isinstance(th, str):
                base["center"]["themes"] = [
                    ln.strip() for ln in th.splitlines() if ln.strip()
                ]
            elif isinstance(th, list):
                base["center"]["themes"] = [
                    str(x).strip() for x in th if str(x).strip()
                ]

    if "pillars" in updates and isinstance(updates["pillars"], dict):
        for k, v in updates["pillars"].items():
            if k not in PILLAR_KEYS:
                continue
            base["pillars"][k] = _normalize_pillar(
                {**(base["pillars"].get(k) or {}), **(v if isinstance(v, dict) else {"items": v})}
            )

    # Flat pillar convenience: love_items, etc.
    for k in PILLAR_KEYS:
        flat = updates.get(f"{k}_items")
        if flat is not None:
            base["pillars"][k] = _normalize_pillar(
                {"items": flat, "notes": base["pillars"][k].get("notes") or ""}
            )
        notes = updates.get(f"{k}_notes")
        if notes is not None:
            base["pillars"][k]["notes"] = str(notes)

    if "intersections" in updates and isinstance(updates["intersections"], dict):
        for k, v in updates["intersections"].items():
            if k not in INTERSECTION_KEYS:
                continue
            base["intersections"][k] = _normalize_intersection(
                {**(base["intersections"].get(k) or {}), **(v if isinstance(v, dict) else {})}
            )

    for list_key in ("out_of_bounds", "linked_bets", "linked_life_domains"):
        if list_key in updates:
            val = updates[list_key]
            if isinstance(val, str):
                base[list_key] = [ln.strip() for ln in val.splitlines() if ln.strip()]
            elif isinstance(val, list):
                base[list_key] = [str(x).strip() for x in val if str(x).strip()]

    if "review_cadence" in updates:
        base["review_cadence"] = str(updates["review_cadence"] or "quarterly")

    base["version"] = int(base.get("version") or 1)
    base["updated_at"] = _now()

    # Write durable JSON without runtime meta keys
    to_write = {
        "version": base["version"],
        "updated_at": base["updated_at"],
        "center": base["center"],
        "pillars": base["pillars"],
        "intersections": base["intersections"],
        "out_of_bounds": base["out_of_bounds"],
        "linked_bets": base["linked_bets"],
        "linked_life_domains": base["linked_life_domains"],
        "review_cadence": base["review_cadence"],
    }
    path.write_text(json.dumps(to_write, indent=2) + "\n", encoding="utf-8")
    return load_ikigai(workspace)


def ikigai_for_context(data: dict[str, Any]) -> dict[str, Any]:
    """Slim blob for payload / Conductor (non-empty when loaded)."""
    center = data.get("center") or {}
    pillars = data.get("pillars") or {}
    return {
        "ok": bool(data.get("ok")),
        "center": {
            "statement": center.get("statement") or "",
            "themes": list(center.get("themes") or [])[:12],
        },
        "pillars": {
            k: {"items": list((pillars.get(k) or {}).get("items") or [])[:12]}
            for k in PILLAR_KEYS
        },
        "intersections": {
            k: {
                "summary": ((data.get("intersections") or {}).get(k) or {}).get(
                    "summary"
                )
                or "",
            }
            for k in INTERSECTION_KEYS
        },
        "out_of_bounds": list(data.get("out_of_bounds") or [])[:16],
        "linked_bets": list(data.get("linked_bets") or [])[:12],
        "review_cadence": data.get("review_cadence") or "quarterly",
        "path": data.get("path") or "strategy/ikigai/pillars.json",
        "updated_at": data.get("updated_at"),
    }
