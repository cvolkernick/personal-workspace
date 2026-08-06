"""Orchestra fan-in strip: host heartbeat + L0 regime/implications (#51).

Reads **producer-owned** paths only — no dual SoT copy:

- Heartbeat (ops): ``orchestra/data/heartbeat/latest.json`` (#50)
- Implication packet (L0): ``research/horizon/data/packets/latest.json`` (#49)

When a producer file is missing, fields soft-degrade with ``available: false``
(stubs) so offline/fixture Orchestra still works.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Relative to monorepo / workspace root (producer paths)
HEARTBEAT_REL = Path("orchestra") / "data" / "heartbeat" / "latest.json"
PACKET_REL = Path("research") / "horizon" / "data" / "packets" / "latest.json"

DEFAULT_TOP_N = 5
# Default monorepo root when workspace not passed (avoid importing payload — circular)
_ORCHESTRA_DIR = Path(__file__).resolve().parent
_DEFAULT_WORKSPACE = _ORCHESTRA_DIR.parent


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _age_seconds(as_of: Optional[str], *, now: Optional[datetime] = None) -> Optional[float]:
    dt = _parse_iso(as_of)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds())


def _load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def heartbeat_path(workspace: Path) -> Path:
    return Path(workspace) / HEARTBEAT_REL


def packet_path(workspace: Path) -> Path:
    return Path(workspace) / PACKET_REL


def build_host_slice(
    doc: Optional[dict[str, Any]],
    *,
    path: Path,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Host ok/as_of from heartbeat document. Trust producer ``ok`` (yellow ≠ global red)."""
    if doc is None:
        return {
            "available": False,
            "ok": None,
            "as_of": None,
            "age_seconds": None,
            "host": None,
            "host_role": None,
            "degraded": [],
            "path": str(path),
            "note": "heartbeat_missing — run pi-heartbeat / await #50 deploy",
        }
    as_of = doc.get("as_of") if isinstance(doc.get("as_of"), str) else None
    degraded = doc.get("degraded") if isinstance(doc.get("degraded"), list) else []
    # Contract: yellow-only failures leave ok:true — surface as-is, do not recompute.
    ok = doc.get("ok")
    return {
        "available": True,
        "ok": bool(ok) if ok is not None else None,
        "as_of": as_of,
        "age_seconds": _age_seconds(as_of, now=now),
        "host": doc.get("host"),
        "host_role": doc.get("host_role"),
        "degraded": degraded,
        "path": str(path),
        "note": "",
    }


def build_regime_slice(packet: Optional[dict[str, Any]]) -> dict[str, Any]:
    if packet is None:
        return {
            "available": False,
            "primary_label": None,
            "primary_probability": None,
            "confidence": None,
            "note": "stub — awaiting L0 implication packet (#49)",
        }
    rs = packet.get("regime_summary")
    if not isinstance(rs, dict):
        return {
            "available": False,
            "primary_label": None,
            "primary_probability": None,
            "confidence": None,
            "note": "packet present but regime_summary missing",
        }
    label = rs.get("primary_label")
    return {
        "available": bool(label),
        "primary_label": label,
        "primary_probability": rs.get("primary_probability"),
        "confidence": rs.get("confidence"),
        "note": rs.get("note") or "",
    }


def build_implications_slice(
    packet: Optional[dict[str, Any]],
    *,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    if packet is None:
        return {
            "available": False,
            "as_of": None,
            "stale": None,
            "confidence_overall": None,
            "top": [],
            "count": 0,
            "note": "stub — awaiting L0 implication packet (#49)",
        }
    raw = packet.get("implications_for_l4")
    items = [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []
    # Prefer higher urgency first (stable secondary key: confidence desc)
    urgency_rank = {"immediate": 0, "this_week": 1, "watch": 2, "structural": 3}

    def sort_key(it: dict[str, Any]) -> tuple:
        u = str(it.get("urgency") or "watch").lower()
        conf = it.get("confidence")
        try:
            c = float(conf) if conf is not None else 0.0
        except (TypeError, ValueError):
            c = 0.0
        return (urgency_rank.get(u, 9), -c)

    items_sorted = sorted(items, key=sort_key)
    top = []
    for it in items_sorted[: max(0, top_n)]:
        top.append(
            {
                "id": it.get("id"),
                "action": it.get("action"),
                "owner_domain": it.get("owner_domain"),
                "urgency": it.get("urgency"),
                "rationale": it.get("rationale"),
                "confidence": it.get("confidence"),
                "horizon_days": it.get("horizon_days"),
            }
        )
    fresh = packet.get("freshness") if isinstance(packet.get("freshness"), dict) else {}
    as_of = packet.get("as_of") or fresh.get("as_of")
    return {
        "available": True,
        "as_of": as_of if isinstance(as_of, str) else None,
        "stale": bool(fresh.get("stale")) if fresh else None,
        "confidence_overall": fresh.get("confidence_overall"),
        "top": top,
        "count": len(items),
        "note": "" if items else "packet present but implications_for_l4 empty",
    }


def build_fan_in(
    workspace: Optional[Path] = None,
    *,
    top_n: int = DEFAULT_TOP_N,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Assemble fan-in strip for Orchestra payload / GET /api/fan-in."""
    ws = Path(workspace or _DEFAULT_WORKSPACE).resolve()
    now = now or datetime.now(timezone.utc)
    hb_path = heartbeat_path(ws)
    pk_path = packet_path(ws)
    hb = _load_json(hb_path)
    packet = _load_json(pk_path)

    host = build_host_slice(hb, path=hb_path, now=now)
    regime = build_regime_slice(packet)
    implications = build_implications_slice(packet, top_n=top_n)

    return {
        "ok": True,
        "generated_at": now.replace(microsecond=0).isoformat(),
        "host": host,
        "regime": regime,
        "implications": implications,
        "sources": {
            "heartbeat_path": str(hb_path),
            "packet_path": str(pk_path),
            "heartbeat_exists": hb is not None,
            "packet_exists": packet is not None,
        },
        "meta": {
            "top_n": top_n,
            "plane_separation": {
                "host": "runtime health (ops)",
                "regime_implications": "decision / L0 weave",
            },
            "note": (
                "Producer-owned latest paths only; Orchestra does not rewrite domain SoTs. "
                "Yellow heartbeat degraded entries do not alone flip host.ok."
            ),
        },
    }
