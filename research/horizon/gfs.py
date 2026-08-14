"""GFS pressure / valve packet — Horizon Macro layer (not a third dashboard).

Fixture-backed MVP. Nakatoshi can replace node ids in
``fixtures/gfs_graph.json`` without a schema break.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURE = PACKAGE_DIR / "fixtures" / "gfs_graph.json"
DEFAULT_PACKET = PACKAGE_DIR / "data" / "gfs_latest.json"

SCHEMA_VERSION = 1
PRESSURE_STATES = ("loose", "neutral", "tight", "seize")
VALVE_STATES = ("shut", "partial", "open")
KINDS = ("pressure", "valve")
STRENGTHS = ("weak", "mixed", "strong")
RELATIONS = ("tightens", "vents", "funds", "exposes")
BOOK_PREFIXES = ("btc_morpho_ltv", "usdc_cash", "rh_sleeve", "strc_jr")

LEVEL_BY_STATE = {
    "loose": 1,
    "neutral": 2,
    "tight": 4,
    "seize": 5,
    "shut": 1,
    "partial": 3,
    "open": 4,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def load_fixture(path: Path | None = None) -> dict[str, Any]:
    p = Path(path or DEFAULT_FIXTURE)
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("gfs fixture must be an object")
    return raw


def validate_graph(raw: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    nodes = raw.get("nodes")
    edges = raw.get("edges")
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty list")
        return errors
    if not isinstance(edges, list):
        errors.append("edges must be a list")
        edges = []

    ids: set[str] = set()
    book_channels = raw.get("book_channels") or {}
    if book_channels and not isinstance(book_channels, Mapping):
        errors.append("book_channels must be an object")
        book_channels = {}

    for i, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            errors.append(f"nodes[{i}] not an object")
            continue
        nid = str(node.get("id") or "").strip()
        if not nid:
            errors.append(f"nodes[{i}] missing id")
            continue
        if nid in ids:
            errors.append(f"duplicate node id {nid}")
        ids.add(nid)
        kind = str(node.get("kind") or "")
        if kind not in KINDS:
            errors.append(f"{nid}: kind must be pressure|valve")
        state = str(node.get("state") or "")
        allowed = PRESSURE_STATES if kind == "pressure" else VALVE_STATES
        if kind in KINDS and state not in allowed:
            errors.append(f"{nid}: invalid state {state!r} for {kind}")
        strength = str(node.get("strength") or "mixed")
        if strength not in STRENGTHS:
            errors.append(f"{nid}: strength must be weak|mixed|strong")
        try:
            level = int(node.get("level") or 0)
        except (TypeError, ValueError):
            errors.append(f"{nid}: level must be int 1-5")
            level = 0
        if level < 1 or level > 5:
            errors.append(f"{nid}: level must be 1-5")
        conf = node.get("confidence")
        try:
            c = float(conf)
            if c < 0 or c > 1:
                errors.append(f"{nid}: confidence must be 0-1")
        except (TypeError, ValueError):
            errors.append(f"{nid}: confidence must be a number")
        src = node.get("source") or {}
        if not isinstance(src, Mapping) or not src.get("kind"):
            errors.append(f"{nid}: source.kind required")

    known = set(ids) | set(book_channels.keys())
    for i, edge in enumerate(edges):
        if not isinstance(edge, Mapping):
            errors.append(f"edges[{i}] not an object")
            continue
        a = str(edge.get("from_id") or "")
        b = str(edge.get("to_id") or "")
        rel = str(edge.get("relation") or "")
        if a not in known:
            errors.append(f"edges[{i}] from_id {a!r} unknown")
        if b not in known:
            errors.append(f"edges[{i}] to_id {b!r} unknown")
        if rel not in RELATIONS:
            errors.append(f"edges[{i}] relation must be one of {RELATIONS}")
    return errors


def _normalize_node(raw: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(raw.get("kind") or "pressure")
    state = str(raw.get("state") or ("neutral" if kind == "pressure" else "shut"))
    try:
        level = int(raw.get("level") or LEVEL_BY_STATE.get(state, 2))
    except (TypeError, ValueError):
        level = LEVEL_BY_STATE.get(state, 2)
    try:
        confidence = float(raw.get("confidence") or 0.4)
    except (TypeError, ValueError):
        confidence = 0.4
    src = raw.get("source") if isinstance(raw.get("source"), Mapping) else {}
    return {
        "id": str(raw.get("id") or ""),
        "kind": kind,
        "title": str(raw.get("title") or raw.get("id") or ""),
        "state": state,
        "level": max(1, min(5, level)),
        "strength": str(raw.get("strength") or "mixed"),
        "horizon_domains": [str(x) for x in _as_list(raw.get("horizon_domains"))],
        "facts": [str(x) for x in _as_list(raw.get("facts")) if str(x).strip()],
        "interpretation": str(raw.get("interpretation") or ""),
        "confidence": max(0.0, min(1.0, confidence)),
        "our_book": [str(x) for x in _as_list(raw.get("our_book"))],
        "watch_if": str(raw.get("watch_if") or ""),
        "source": {
            "name": str(src.get("name") or ""),
            "url": str(src.get("url") or ""),
            "kind": str(src.get("kind") or "fixture"),
        },
    }


def _normalize_edge(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "from_id": str(raw.get("from_id") or ""),
        "to_id": str(raw.get("to_id") or ""),
        "relation": str(raw.get("relation") or "tightens"),
        "note": str(raw.get("note") or ""),
    }


def _pressure_index(nodes: Iterable[Mapping[str, Any]]) -> float:
    pressures = [n for n in nodes if n.get("kind") == "pressure"]
    if not pressures:
        return 0.0
    weighted = 0.0
    weight = 0.0
    for n in pressures:
        w = {"weak": 0.6, "mixed": 1.0, "strong": 1.4}.get(str(n.get("strength")), 1.0)
        # Index is 1-5 tightness, not confidence-discounted (conf is per-node).
        weighted += int(n.get("level") or 0) * w
        weight += w
    if weight <= 0:
        return 0.0
    return round(weighted / weight, 2)


def _hop_paths(
    edges: list[Mapping[str, Any]],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """One- and two-hop transmissions that land on a book channel or another node."""
    out: list[dict[str, Any]] = []
    by_from: dict[str, list[Mapping[str, Any]]] = {}
    for e in edges:
        by_from.setdefault(str(e["from_id"]), []).append(e)
    for e in edges:
        a, b, rel = e["from_id"], e["to_id"], e["relation"]
        out.append(
            {
                "path": [a, b],
                "relations": [rel],
                "hops": 1,
                "note": e.get("note") or "",
            }
        )
        for e2 in by_from.get(b, []):
            if e2["to_id"] == a:
                continue
            out.append(
                {
                    "path": [a, b, e2["to_id"]],
                    "relations": [rel, e2["relation"]],
                    "hops": 2,
                    "note": (e.get("note") or "") + " → " + (e2.get("note") or ""),
                }
            )
        if len(out) >= limit * 3:
            break
    # Prefer paths that expose the book
    def _score(p: dict[str, Any]) -> tuple:
        tail = p["path"][-1]
        bookish = any(tail == ch or tail.startswith(ch) for ch in BOOK_PREFIXES)
        return (0 if bookish else 1, p["hops"], tail)

    out.sort(key=_score)
    return out[:limit]


def build_gfs_packet(
    raw: Mapping[str, Any] | None = None,
    *,
    now: Optional[datetime] = None,
    fixture_path: Path | None = None,
) -> dict[str, Any]:
    graph = dict(raw or load_fixture(fixture_path))
    errors = validate_graph(graph)
    nodes = [_normalize_node(n) for n in (graph.get("nodes") or []) if isinstance(n, Mapping)]
    edges = [_normalize_edge(e) for e in (graph.get("edges") or []) if isinstance(e, Mapping)]
    book = graph.get("book_channels") if isinstance(graph.get("book_channels"), Mapping) else {}
    when = now or utc_now()
    pressures = [n for n in nodes if n["kind"] == "pressure"]
    valves = [n for n in nodes if n["kind"] == "valve"]
    stressed = sorted(
        pressures,
        key=lambda n: (int(n["level"]), str(n["strength"]) == "strong"),
        reverse=True,
    )
    open_valves = [n for n in valves if n["state"] in ("partial", "open")]
    live_n = sum(1 for n in nodes if (n.get("source") or {}).get("kind") == "live")
    honesty = str(graph.get("honesty") or "")
    if live_n == 0 and "fixture" not in honesty.lower():
        honesty = (honesty + " · fixture-backed").strip(" ·")
    packet = {
        "ok": not errors,
        "schema_version": int(graph.get("schema_version") or SCHEMA_VERSION),
        "as_of": when.isoformat(),
        "honesty": honesty or "fixture-backed",
        "source_kind": "live" if live_n == len(nodes) and nodes else "fixture",
        "live_node_count": live_n,
        "node_count": len(nodes),
        "pressure_count": len(pressures),
        "valve_count": len(valves),
        "pressure_index": _pressure_index(nodes),
        "most_stressed": [
            {"id": n["id"], "title": n["title"], "state": n["state"], "level": n["level"]}
            for n in stressed[:3]
        ],
        "open_valves": [
            {"id": n["id"], "title": n["title"], "state": n["state"]}
            for n in open_valves
        ],
        "nodes": nodes,
        "edges": edges,
        "book_channels": dict(book),
        "transmissions": _hop_paths(edges),
        "errors": errors,
    }
    return packet


def write_gfs_packet(packet: Mapping[str, Any], path: Path | None = None) -> Path:
    dest = Path(path or DEFAULT_PACKET)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(packet, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(dest)
    return dest


def load_gfs_packet(path: Path | None = None) -> Optional[dict[str, Any]]:
    p = Path(path or DEFAULT_PACKET)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def ensure_gfs_packet(
    *,
    data_dir: Path | None = None,
    fixture_path: Path | None = None,
    rebuild: bool = False,
) -> dict[str, Any]:
    dest = Path(data_dir or PACKAGE_DIR / "data") / "gfs_latest.json"
    existing = None if rebuild else load_gfs_packet(dest)
    if existing and existing.get("nodes"):
        return existing
    packet = build_gfs_packet(fixture_path=fixture_path)
    write_gfs_packet(packet, dest)
    return packet
