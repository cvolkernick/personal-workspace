"""Optional bi-annual / quarterly lab panels (manual JSON, not Google Health).

Path: fitness/data/labs.json

Schema::
  {
    "source_note": "...",
    "panels": [
      {
        "date": "YYYY-MM-DD",
        "lab": "Quest / Labcorp / …",
        "markers": { "vitamin_d_ng_ml": 32, "ldl_mg_dl": 100, … },
        "notes": "optional free text"
      }
    ]
  }
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_REL_PATH = "fitness/data/labs.json"

# Rough adult reference ranges for coach flags only — not medical advice.
# Values are (low, high) in the unit implied by the key suffix.
REFERENCE_HINTS: Dict[str, tuple] = {
    "vitamin_d_ng_ml": (30, 80),
    "ldl_mg_dl": (0, 100),
    "hdl_mg_dl": (40, 100),
    "triglycerides_mg_dl": (0, 150),
    "total_cholesterol_mg_dl": (0, 200),
    "glucose_mg_dl": (70, 99),
    "hba1c_pct": (4.0, 5.6),
    "ferritin_ng_ml": (30, 300),
    "tsh_miu_l": (0.4, 4.0),
    "testosterone_ng_dl": (300, 1000),
    "hemoglobin_g_dl": (13.0, 17.5),
    "alt_u_l": (7, 56),
    "ast_u_l": (10, 40),
    "creatinine_mg_dl": (0.7, 1.3),
    "b12_pg_ml": (200, 900),
    "folate_ng_ml": (3, 20),
    "magnesium_mg_dl": (1.7, 2.2),
    "potassium_mmol_l": (3.5, 5.1),
    "sodium_mmol_l": (135, 145),
}


def default_labs() -> dict:
    return {
        "source_note": (
            "Optional lab panels (bi-annual/quarterly). Add markers with "
            "snake_case keys and units in the name (e.g. vitamin_d_ng_ml)."
        ),
        "panels": [],
        "updated_at": "",
    }


def load_labs(workspace_dir: str = "", rel_path: str = DEFAULT_REL_PATH) -> dict:
    if not workspace_dir:
        return default_labs()
    p = Path(workspace_dir) / rel_path
    if not p.is_file():
        return default_labs()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_labs()
    if not isinstance(data, dict):
        return default_labs()
    out = default_labs()
    out["source_note"] = str(data.get("source_note") or out["source_note"])
    out["updated_at"] = str(data.get("updated_at") or "")
    panels: List[dict] = []
    for raw in data.get("panels") or []:
        if not isinstance(raw, dict) or not raw.get("date"):
            continue
        markers = raw.get("markers") or {}
        if not isinstance(markers, dict):
            markers = {}
        clean_markers = {}
        for k, v in markers.items():
            try:
                clean_markers[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        panels.append(
            {
                "date": str(raw["date"])[:10],
                "lab": str(raw.get("lab") or ""),
                "markers": clean_markers,
                "notes": str(raw.get("notes") or ""),
            }
        )
    panels.sort(key=lambda x: x["date"])
    out["panels"] = panels
    return out


def latest_panel(labs: Optional[dict]) -> Optional[dict]:
    if not labs:
        return None
    panels = labs.get("panels") or []
    if not panels:
        return None
    return panels[-1]


def flag_markers(panel: Optional[dict]) -> List[dict]:
    """Return out-of-range marker flags using REFERENCE_HINTS."""
    if not panel:
        return []
    flags: List[dict] = []
    for key, val in (panel.get("markers") or {}).items():
        rng = REFERENCE_HINTS.get(key)
        if not rng:
            continue
        lo, hi = rng
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        status = "ok"
        if v < lo:
            status = "low"
        elif v > hi:
            status = "high"
        if status != "ok":
            flags.append(
                {
                    "marker": key,
                    "value": v,
                    "status": status,
                    "ref_low": lo,
                    "ref_high": hi,
                }
            )
    return flags


def labs_summary_for_coach(labs: Optional[dict]) -> dict:
    """Compact payload for coach commentary + Ask context."""
    panel = latest_panel(labs)
    if not panel:
        return {
            "has_labs": False,
            "message": "No lab panels on file (optional: fitness/data/labs.json).",
        }
    flags = flag_markers(panel)
    return {
        "has_labs": True,
        "date": panel.get("date"),
        "lab": panel.get("lab") or "",
        "marker_count": len(panel.get("markers") or {}),
        "flags": flags,
        "notes": panel.get("notes") or "",
        "markers": deepcopy(panel.get("markers") or {}),
    }
