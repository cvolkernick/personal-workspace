"""Load personal strategy from Orchestrator sources and map to world-state nodes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

# Thematic keyword maps: priority id -> keywords that indicate affinity
DEFAULT_PRIORITY_KEYWORDS: dict[str, list[str]] = {
    "energy": [
        "energy", "nuclear", "uranium", "oil", "gas", "lng", "power", "grid",
        "ccj", "leu", "data-centers", "electricity",
    ],
    "bitcoin": [
        "bitcoin", "btc", "mstr", "crypto", "etf", "liquidity", "treasury",
        "digital asset", "risk-assets",
    ],
    "ai": [
        "ai", "artificial intelligence", "gpu", "nvidia", "compute", "model",
        "semiconductor", "chip", "nvda", "amd", "tsm", "asml", "autonomy",
    ],
    "autonomy_robotics": [
        "autonomy", "robotics", "automation", "robot", "tsla", "self-driving",
        "physical-world",
    ],
    "treasury_liquidity": [
        "rates", "fed", "liquidity", "dollar", "treasury", "inflation", "macro",
        "capital flow", "real rates",
    ],
    "geopolitical_risk": [
        "china", "taiwan", "sanction", "war", "military", "export-control",
        "indo-pacific", "geopolitics",
    ],
}


def default_workspace_root() -> Path:
    # research/horizon/strategy_link.py -> workspace root
    return Path(__file__).resolve().parents[2]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _extract_bets(bets_md: str) -> list[str]:
    """Pull thematic bet names from bets.md content."""
    found: list[str] = []
    # Lines like "- **Energy** (...)" or list under Current Thematic Bets
    for m in re.finditer(r"\*\*([^*]+)\*\*", bets_md):
        name = m.group(1).strip()
        # Filter section headers
        if name.lower() in {
            "guiding principle",
            "how to use this file",
            "balanced life principle",
            "current domain weightings (dynamic)",
            "links to execution",
        }:
            continue
        if len(name) < 40 and name not in found:
            # Prefer short thematic labels
            if any(
                k in name.lower()
                for k in (
                    "energy", "bitcoin", "ai", "autonomy", "robotics",
                    "nuclear", "fitness",
                )
            ):
                found.append(name)
    # Fallback parse of parenthetical list
    m = re.search(r"Current Thematic Bets\s*\(([^)]+)\)", bets_md, re.I)
    if m:
        for part in m.group(1).split(","):
            p = part.strip()
            if p and p not in found:
                found.append(p)
    return found[:12]


def _extract_positions(positions_md: str) -> list[str]:
    symbols: list[str] = []
    for line in positions_md.splitlines():
        # Markdown table rows: | BTC | ... or | **BTC** | ...
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells:
            continue
        sym = cells[0]
        # Strip markdown bold/italics around tickers
        sym = re.sub(r"^\*+|\*+$", "", sym).strip()
        if sym.lower() in {"symbol", "sleeve", "----", "---"} or set(sym) <= {"-"}:
            continue
        if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", sym):
            if sym not in symbols:
                symbols.append(sym)
    return symbols


def load_strategy(workspace: Optional[Path] = None) -> dict[str, Any]:
    """Load thesis/priorities from real workspace strategy + investment paths."""
    root = Path(workspace or default_workspace_root()).resolve()
    paths = {
        "bets": root / "strategy" / "bets.md",
        "intent": root / "strategy" / "intent.json",
        "today": root / "strategy" / "today.md",
        "positions": root / "investment" / "positions.md",
    }
    bets_text = _read_text(paths["bets"])
    today_text = _read_text(paths["today"])
    positions_text = _read_text(paths["positions"])
    intent: dict[str, Any] = {}
    intent_exists = paths["intent"].is_file()
    if intent_exists:
        try:
            raw = json.loads(paths["intent"].read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                intent = raw
        except (OSError, json.JSONDecodeError):
            intent = {}

    thematic_bets = _extract_bets(bets_text)
    symbols = _extract_positions(positions_text)
    accomplishing = str(intent.get("accomplishing") or "").strip()

    priorities: list[dict[str, Any]] = []
    # Core thematic priorities from bets + keyword packs
    seed_ids = [
        ("energy", "Energy"),
        ("bitcoin", "Bitcoin"),
        ("ai", "AI / Compute"),
        ("autonomy_robotics", "Autonomy / Robotics"),
        ("treasury_liquidity", "Treasury / Liquidity"),
        ("geopolitical_risk", "Geopolitical Risk Management"),
    ]
    for pid, label in seed_ids:
        kws = list(DEFAULT_PRIORITY_KEYWORDS.get(pid, []))
        # Boost keywords from bet labels and symbols
        for bet in thematic_bets:
            if any(x in bet.lower() for x in pid.split("_")) or pid in bet.lower():
                kws.append(bet.lower())
        if pid == "bitcoin":
            kws.extend(s.lower() for s in symbols if s in {"BTC", "MSTR"})
        if pid == "ai":
            kws.extend(
                s.lower()
                for s in symbols
                if s in {"NVDA", "AMD", "AVGO", "TSM", "ASML", "GOOGL", "SMTC"}
            )
        if pid == "energy":
            kws.extend(s.lower() for s in symbols if s in {"CCJ", "LEU"})
        priorities.append(
            {
                "id": pid,
                "label": label,
                "keywords": sorted(set(k.lower() for k in kws if k)),
                "source": "strategy/bets.md + keyword pack",
            }
        )

    if accomplishing:
        priorities.insert(
            0,
            {
                "id": "intent_north_star",
                "label": "Current intent",
                "keywords": _keywords_from_text(accomplishing),
                "source": "strategy/intent.json",
                "statement": accomplishing,
            },
        )

    return {
        "workspace": str(root),
        "paths": {k: str(v) for k, v in paths.items()},
        "paths_exist": {k: v.is_file() for k, v in paths.items()},
        "thematic_bets": thematic_bets,
        "intent": {
            "accomplishing": accomplishing,
            "balancing": list(intent.get("balancing") or []),
            "constraints": list(intent.get("constraints") or []),
            "time_horizon": intent.get("time_horizon"),
        },
        "positions_symbols": symbols,
        "today_excerpt": today_text[:500],
        "priorities": priorities,
    }


def _keywords_from_text(text: str) -> list[str]:
    stop = {
        "and", "the", "via", "with", "while", "from", "for", "that", "this",
        "are", "was", "onto", "into", "over", "under", "keep", "build",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9+/]{2,}", text.lower())
    out: list[str] = []
    for w in words:
        if w in stop:
            continue
        if w not in out:
            out.append(w)
    # Multi-word themes
    for phrase in ("bitcoin", "energy", "autonomy", "robotics", "treasury", "ai"):
        if phrase in text.lower() and phrase not in out:
            out.append(phrase)
    return out[:40]


def _node_text(node: dict[str, Any]) -> str:
    parts = [
        str(node.get("title") or ""),
        " ".join(str(t) for t in (node.get("tags") or [])),
        " ".join(str(f) for f in (node.get("facts") or [])),
        str(node.get("interpretation") or ""),
        str(node.get("domain") or ""),
    ]
    return " ".join(parts).lower()


def score_affinity(node: dict[str, Any], priority: dict[str, Any]) -> float:
    """Deterministic keyword affinity in [0, 1]."""
    text = _node_text(node)
    kws = priority.get("keywords") or []
    if not kws:
        return 0.0
    hits = 0.0
    for kw in kws:
        k = str(kw).lower().strip()
        if not k:
            continue
        if k in text:
            # Longer keyword matches weigh more
            hits += min(2.0, 0.5 + len(k) / 20.0)
    if hits <= 0:
        return 0.0
    # Saturating score
    raw = hits / (2.0 + 0.35 * len(kws) ** 0.5)
    conf = float(node.get("confidence") or 0.5)
    impact_w = {"low": 0.7, "medium": 1.0, "high": 1.25, "critical": 1.4}
    w = impact_w.get(str(node.get("impact") or "medium"), 1.0)
    return round(min(1.0, raw * conf * w), 4)


def link_world_to_strategy(
    state: dict[str, Any],
    strategy: dict[str, Any],
    *,
    min_affinity: float = 0.12,
) -> list[dict[str, Any]]:
    """Map world-state nodes to personal priorities."""
    linkages: list[dict[str, Any]] = []
    priorities = strategy.get("priorities") or []
    domains = state.get("domains") or {}
    for domain, bucket in domains.items():
        for node in bucket.get("nodes") or []:
            for pr in priorities:
                aff = score_affinity(node, pr)
                if aff < min_affinity:
                    continue
                matched = [
                    kw
                    for kw in (pr.get("keywords") or [])
                    if str(kw).lower() in _node_text(node)
                ][:8]
                linkages.append(
                    {
                        "node_id": node.get("id"),
                        "node_title": node.get("title"),
                        "domain": domain,
                        "priority_id": pr.get("id"),
                        "priority_label": pr.get("label"),
                        "affinity": aff,
                        "matched_keywords": matched,
                        "rationale": (
                            f"Affinity {aff:.2f} with {pr.get('label')} "
                            f"via {', '.join(matched) or 'thematic overlap'}; "
                            f"node impact={node.get('impact')}, "
                            f"confidence={node.get('confidence')}."
                        ),
                        "node_priority_score": float(node.get("priority_score") or 0),
                        "facts": list(node.get("facts") or [])[:3],
                        "interpretation": str(node.get("interpretation") or ""),
                        "confidence": float(node.get("confidence") or 0),
                    }
                )
    linkages.sort(
        key=lambda x: (float(x.get("affinity") or 0), float(x.get("node_priority_score") or 0)),
        reverse=True,
    )
    return linkages
