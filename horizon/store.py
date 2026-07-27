"""Load/save Horizon season plan (strategy/horizon_season.json + horizon.md)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEASON_PATH = ROOT / "strategy" / "horizon_season.json"
DEFAULT_MD_PATH = ROOT / "strategy" / "horizon.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_season() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "season_label": "2026-Q3",
        "primary_themes": [
            "AI/Autonomy leverage",
            "Systems that compound",
        ],
        "secondary_themes": [
            "Embodied energy",
            "Wealth optionality hygiene",
        ],
        "underweight": [
            "Pure dashboard exploration",
            "Non-bet research rabbit holes",
        ],
        "capacity_notes": "Deep-work blocks protected; batch hygiene.",
        "initiative_slugs": [],
        "notes": "",
    }


def load_season(path: Optional[Path] = None) -> dict[str, Any]:
    p = Path(path or DEFAULT_SEASON_PATH)
    base = default_season()
    if not p.is_file():
        base["exists"] = False
        try:
            base["path"] = str(p.relative_to(ROOT))
        except ValueError:
            base["path"] = str(p)
        return base
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        base["exists"] = False
        base["error"] = "invalid json"
        return base
    if not isinstance(raw, dict):
        base["exists"] = False
        return base
    out = default_season()
    for k in out:
        if k in raw:
            out[k] = raw[k]
    # normalize lists
    for key in (
        "primary_themes",
        "secondary_themes",
        "underweight",
        "initiative_slugs",
    ):
        val = out.get(key)
        if isinstance(val, str):
            out[key] = [ln.strip() for ln in val.splitlines() if ln.strip()]
        elif not isinstance(val, list):
            out[key] = []
        else:
            out[key] = [str(x).strip() for x in val if str(x).strip()]
    out["exists"] = True
    try:
        out["path"] = str(p.relative_to(ROOT))
    except ValueError:
        out["path"] = str(p)
    return out


def save_season(updates: dict[str, Any], path: Optional[Path] = None) -> dict[str, Any]:
    p = Path(path or DEFAULT_SEASON_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = load_season(p)
    base = {k: v for k, v in cur.items() if k not in ("exists", "error", "path")}
    for k, v in updates.items():
        if k in ("exists", "error", "path", "version"):
            continue
        if k in (
            "primary_themes",
            "secondary_themes",
            "underweight",
            "initiative_slugs",
        ):
            if isinstance(v, str):
                base[k] = [ln.strip() for ln in v.splitlines() if ln.strip()]
            elif isinstance(v, list):
                base[k] = [str(x).strip() for x in v if str(x).strip()]
        elif isinstance(v, str):
            base[k] = v.strip()
        else:
            base[k] = v
    base["version"] = 1
    base["updated_at"] = _now()
    p.write_text(json.dumps(base, indent=2) + "\n", encoding="utf-8")
    return load_season(p)


def list_initiatives(workspace: Optional[Path] = None) -> list[dict[str, Any]]:
    root = Path(workspace or ROOT)
    init_dir = root / "initiatives"
    out: list[dict[str, Any]] = []
    if not init_dir.is_dir():
        return out
    for path in sorted(init_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        meta: dict[str, Any] = {"id": path.stem, "path": f"initiatives/{path.name}"}
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].splitlines():
                    if ":" not in line:
                        continue
                    k, _, v = line.partition(":")
                    k, v = k.strip(), v.strip().strip("\"'")
                    if k in (
                        "title",
                        "status",
                        "next_action",
                        "ikigai_intersection",
                        "priority_impact",
                    ):
                        meta[k] = v
                    if k == "ikigai_pillars" and v.startswith("["):
                        try:
                            meta[k] = json.loads(v.replace("'", '"'))
                        except json.JSONDecodeError:
                            meta[k] = v
                    if k == "linked_bets" and v.startswith("["):
                        try:
                            meta[k] = json.loads(v.replace("'", '"'))
                        except json.JSONDecodeError:
                            meta[k] = v
        if "title" not in meta:
            meta["title"] = path.stem.replace("-", " ")
        out.append(meta)
    return out


def load_ikigai_themes(workspace: Optional[Path] = None) -> list[str]:
    root = Path(workspace or ROOT)
    p = root / "strategy" / "ikigai" / "pillars.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    themes = (data.get("center") or {}).get("themes") or []
    return [str(t) for t in themes if str(t).strip()]


def horizon_payload(workspace: Optional[Path] = None) -> dict[str, Any]:
    root = Path(workspace or ROOT)
    season = load_season(root / "strategy" / "horizon_season.json")
    initiatives = list_initiatives(root)
    md_path = root / "strategy" / "horizon.md"
    md = ""
    if md_path.is_file():
        try:
            md = md_path.read_text(encoding="utf-8")[:12000]
        except OSError:
            md = ""
    return {
        "ok": True,
        "service": "horizon",
        "name": "Horizon",
        "purpose": (
            "Seasonal planning between Ikigai identity and Orchestrator next actions."
        ),
        "season": season,
        "initiatives": initiatives,
        "ikigai_themes": load_ikigai_themes(root),
        "horizon_md": md,
        "links": {
            "orchestrator": "http://127.0.0.1:8790/",
            "ikigai": "strategy/ikigai/",
            "horizon_md": "strategy/horizon.md",
        },
    }
