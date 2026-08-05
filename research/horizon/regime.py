"""Global multi-axis regime assessment from world-state nodes.

Pure transform: no network, no invented market prints. Scores only from nodes already
present in world-state (facts/tags/titles/domains/impact/confidence). Confidence is
capped when density is scaffold-level or fixture-only.

Surfaces: pipeline stamps state.regime; synthesis brief.regime; dashboard Overview.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from research.horizon.world_state import query_nodes

# Stable axis IDs (dashboard / Orchestra consumers)
REGIME_AXES: tuple[str, ...] = (
    "monetary",
    "growth",
    "liquidity",
    "risk_appetite",
    "geopolitics",
    "energy_tech",
)

AXIS_LABELS: dict[str, str] = {
    "monetary": "Monetary policy",
    "growth": "Growth / activity",
    "liquidity": "Liquidity / credit",
    "risk_appetite": "Risk appetite",
    "geopolitics": "Geopolitics",
    "energy_tech": "Energy / tech constraint",
}

# Axis state vocabularies
AXIS_STATES: dict[str, tuple[str, ...]] = {
    "monetary": ("higher_for_longer", "easing", "neutral", "unknown"),
    "growth": ("soft_landing", "slowdown", "reacceleration", "stagflation_risk", "unknown"),
    "liquidity": ("tight", "neutral", "loose", "unknown"),
    "risk_appetite": ("risk_on", "mixed", "risk_off", "unknown"),
    "geopolitics": (
        "elevated_competition",
        "multipolar_fragmentation",
        "deescalation",
        "unknown",
    ),
    "energy_tech": (
        "power_constrained_ai",
        "commodity_shock",
        "transition_smooth",
        "unknown",
    ),
}

AXIS_STATE_LABELS: dict[str, str] = {
    "higher_for_longer": "Higher for longer",
    "easing": "Easing bias",
    "neutral": "Neutral",
    "soft_landing": "Soft landing",
    "slowdown": "Slowdown",
    "reacceleration": "Reacceleration",
    "stagflation_risk": "Stagflation risk",
    "tight": "Tight",
    "loose": "Loose",
    "risk_on": "Risk-on",
    "mixed": "Mixed",
    "risk_off": "Risk-off",
    "elevated_competition": "Elevated competition",
    "multipolar_fragmentation": "Multipolar fragmentation",
    "deescalation": "De-escalation",
    "power_constrained_ai": "Power-constrained AI",
    "commodity_shock": "Commodity shock risk",
    "transition_smooth": "Smooth energy transition",
    "unknown": "Unknown",
}

# Composite scenario catalog (primary regime label for overview)
SCENARIOS: dict[str, dict[str, str]] = {
    "restrictive_soft_landing": {
        "label": "Restrictive / soft landing",
        "description": (
            "Policy stays restrictive vs 2010s baseline while growth avoids deep contraction."
        ),
    },
    "higher_for_longer_slowdown": {
        "label": "Higher-for-longer + slowdown",
        "description": "Elevated real rates and/or tight credit slow activity; easing delayed.",
    },
    "easing_reacceleration": {
        "label": "Easing + reacceleration",
        "description": "Policy accommodates and growth reaccelerates; risk assets reprice up.",
    },
    "stagflation_or_supply_shock": {
        "label": "Stagflation / supply shock",
        "description": "Energy/supply/wage shocks keep inflation sticky while growth softens.",
    },
    "geopolitical_risk_premium": {
        "label": "Geopolitical risk-premium",
        "description": (
            "Great-power competition and export controls dominate risk premia and industrial policy."
        ),
    },
}

# Keywords: axis -> state -> substrings
_KW: dict[str, dict[str, list[str]]] = {
    "monetary": {
        "higher_for_longer": [
            "elevated",
            "higher-for-longer",
            "higher for longer",
            "restrictive",
            "policy rates remain elevated",
            "qt",
            "quantitative tightening",
            "real rates",
            "rate hike",
        ],
        "easing": [
            "cut",
            "easing",
            "pivot",
            "accommodative",
            "qe",
            "rate cut",
        ],
        "neutral": ["data dependent", "hold", "steady policy", "forward guidance"],
    },
    "growth": {
        "soft_landing": [
            "soft landing",
            "soft-landing",
            "disinflation",
            "cooled from peaks",
            "cooling without a collapse",
            "resilient demand",
        ],
        "slowdown": [
            "slowdown",
            "recession",
            "weak demand",
            "contraction",
            "growth scare",
            "growth constraint",
            "growth potential",
        ],
        "reacceleration": [
            "reacceleration",
            "boom",
            "capex surge",
            "buildout",
            "above-trend",
        ],
        "stagflation_risk": [
            "stagflation",
            "services inflation",
            "wage",
            "supply shock",
            "sticky",
        ],
    },
    "liquidity": {
        "tight": [
            "funding stress",
            "credit crunch",
            "lending standards",
            "qt",
            "liquidity tight",
            "tight credit",
        ],
        "loose": [
            "etf inflow",
            "global liquidity",
            "abundant",
            "stimulus",
            "qe",
            "risk appetite",
        ],
        "neutral": [
            "absence of credit stress",
            "steady liquidity",
            "balanced funding",
        ],
    },
    "risk_appetite": {
        "risk_on": ["risk-on", "risk on", "rally", "euphoria", "etf inflow"],
        "risk_off": [
            "risk-off",
            "risk off",
            "drawdown",
            "flight to quality",
            "disillusionment",
        ],
        "mixed": [
            "high-beta",
            "correlated",
            "oscillate",
            "uncertain",
            "high-beta expression",
        ],
    },
    "geopolitics": {
        "elevated_competition": [
            "strategic competition",
            "export control",
            "export-controls",
            "sanction",
            "indo-pacific",
            "great power",
            "military posture",
        ],
        "multipolar_fragmentation": [
            "friend-shoring",
            "fragment",
            "industrial policy",
            "tariff",
            "decoupl",
            "multipolar",
            "local-content",
        ],
        "deescalation": ["de-escalat", "ceasefire", "detente", "thaw"],
    },
    "energy_tech": {
        "power_constrained_ai": [
            "data-center",
            "data center",
            "grid constraint",
            "nuclear",
            "power",
            "compute",
            "gpu",
            "ai",
            "energy-constrained",
        ],
        "commodity_shock": [
            "oil",
            "opec",
            "energy shock",
            "uranium",
            "commodity",
            "lng",
        ],
        "transition_smooth": [
            "transition smooth",
            "renewable surplus",
            "abundant power",
        ],
    },
}

_DOMAIN_AXIS_PRIOR: dict[str, dict[str, dict[str, float]]] = {
    "macroeconomics": {
        "monetary": {"higher_for_longer": 0.4, "easing": 0.15},
        "growth": {"soft_landing": 0.25, "slowdown": 0.15},
    },
    "capital_flows": {
        "liquidity": {"loose": 0.2, "tight": 0.15, "neutral": 0.1},
        "risk_appetite": {"mixed": 0.3, "risk_on": 0.1},
    },
    "geopolitics": {
        "geopolitics": {"elevated_competition": 0.45, "multipolar_fragmentation": 0.2},
    },
    "military": {
        "geopolitics": {"elevated_competition": 0.3},
        "risk_appetite": {"risk_off": 0.1, "mixed": 0.1},
    },
    "energy": {
        "energy_tech": {"commodity_shock": 0.25, "power_constrained_ai": 0.2},
        "growth": {"stagflation_risk": 0.1},
    },
    "technology_ai": {
        "energy_tech": {"power_constrained_ai": 0.4},
        "growth": {"reacceleration": 0.15},
    },
    "supply_chains": {
        "geopolitics": {"multipolar_fragmentation": 0.2, "elevated_competition": 0.15},
        "energy_tech": {"power_constrained_ai": 0.1},
    },
    "narrative_information": {
        "risk_appetite": {"mixed": 0.25},
    },
    "demographics": {
        "growth": {"slowdown": 0.1, "stagflation_risk": 0.08},
    },
}


def _node_text(node: dict[str, Any]) -> str:
    parts = [
        str(node.get("title") or ""),
        str(node.get("interpretation") or ""),
        " ".join(str(t) for t in (node.get("tags") or [])),
        " ".join(str(f) for f in (node.get("facts") or [])),
    ]
    return " ".join(parts).lower()


def _impact_w(impact: str) -> float:
    return {"low": 0.6, "medium": 1.0, "high": 1.5, "critical": 2.0}.get(
        str(impact or "medium").lower(), 1.0
    )


def _normalize(scores: dict[str, float], labels: Iterable[str]) -> dict[str, float]:
    labels_list = list(labels)
    known = [l for l in labels_list if l != "unknown"]
    total = sum(max(0.0, scores.get(l, 0.0)) for l in known)
    out: dict[str, float] = {}
    if total <= 0:
        for l in labels_list:
            out[l] = 1.0 if l == "unknown" else 0.0
        return out
    unknown_mass = min(0.4, 0.45 / max(total, 0.25)) if total < 1.2 else 0.05
    known_mass = 1.0 - unknown_mass
    for l in labels_list:
        if l == "unknown":
            out[l] = round(unknown_mass, 4)
        else:
            out[l] = round(known_mass * max(0.0, scores.get(l, 0.0)) / total, 4)
    s = sum(out.values())
    if known and abs(s - 1.0) > 1e-6:
        top = max(known, key=lambda x: out.get(x, 0.0))
        out[top] = round(out[top] + (1.0 - s), 4)
    return out


def _collect_nodes(state: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = query_nodes(state, limit=200)
    if nodes:
        return nodes
    out: list[dict[str, Any]] = []
    for bucket in (state.get("domains") or {}).values():
        out.extend(bucket.get("nodes") or [])
    return out


def regime_headline(regime: dict[str, Any] | None) -> str:
    if not regime:
        return "Regime: unknown"
    primary = regime.get("primary") or {}
    label = primary.get("label") or primary.get("id") or "unknown"
    conf = regime.get("confidence_overall", regime.get("confidence"))
    prob = primary.get("probability")
    parts = [f"Regime: {label}"]
    if prob is not None:
        try:
            parts.append(f"{float(prob):.0%}")
        except (TypeError, ValueError):
            pass
    if conf is not None:
        parts.append(f"conf={conf}")
    return " · ".join(parts)


def assess_regime(state: dict[str, Any]) -> dict[str, Any]:
    """Multi-axis regime assessment from world-state nodes."""
    nodes = _collect_nodes(state)
    modes = list((state.get("meta") or {}).get("source_modes") or [])
    axis_scores: dict[str, dict[str, float]] = {
        ax: {} for ax in REGIME_AXES
    }
    forces: list[dict[str, Any]] = []
    evidence = 0.0

    for n in nodes:
        text = _node_text(n)
        conf = max(0.0, min(1.0, float(n.get("confidence") or 0.5)))
        w = _impact_w(str(n.get("impact") or "medium")) * conf
        evidence += w
        domain = str(n.get("domain") or "")

        for axis, state_kws in _KW.items():
            for st, kws in state_kws.items():
                hits = sum(1 for k in kws if k in text)
                if hits:
                    axis_scores[axis][st] = axis_scores[axis].get(st, 0.0) + w * (
                        1.0 + 0.2 * (hits - 1)
                    )

        for axis, priors in (_DOMAIN_AXIS_PRIOR.get(domain) or {}).items():
            if axis not in axis_scores:
                continue
            for st, pw in priors.items():
                axis_scores[axis][st] = axis_scores[axis].get(st, 0.0) + w * pw * 0.35

        if w >= 0.75 or str(n.get("impact") or "").lower() in ("high", "critical"):
            forces.append(
                {
                    "node_id": n.get("id"),
                    "title": n.get("title"),
                    "domain": domain,
                    "impact": n.get("impact"),
                    "confidence": conf,
                    "weight": round(w, 3),
                }
            )

    axes_out: list[dict[str, Any]] = []
    axis_dom: dict[str, str] = {}
    axis_probs: dict[str, dict[str, float]] = {}

    for ax in REGIME_AXES:
        probs = _normalize(axis_scores.get(ax, {}), AXIS_STATES[ax])
        axis_probs[ax] = probs
        dominant = max(probs, key=lambda k: probs[k])
        axis_dom[ax] = dominant
        conf_ax = round(min(0.85, 0.35 + 0.08 * sum(axis_scores.get(ax, {}).values())), 3)
        if modes == ["fixture"] or (len(modes) == 1 and "fixture" in modes):
            conf_ax = min(conf_ax, 0.55)
        axes_out.append(
            {
                "id": ax,
                "label": AXIS_LABELS.get(ax, ax),
                "dominant": dominant,
                "dominant_label": AXIS_STATE_LABELS.get(dominant, dominant),
                "probability": probs.get(dominant, 0.0),
                "confidence": conf_ax,
                "states": [
                    {
                        "id": st,
                        "label": AXIS_STATE_LABELS.get(st, st),
                        "probability": probs.get(st, 0.0),
                    }
                    for st in AXIS_STATES[ax]
                ],
            }
        )

    # Composite scenarios from axes
    raw: dict[str, float] = {k: 0.05 for k in SCENARIOS}
    m = axis_probs["monetary"]
    g = axis_probs["growth"]
    liq = axis_probs["liquidity"]
    risk = axis_probs["risk_appetite"]
    geo = axis_probs["geopolitics"]
    et = axis_probs["energy_tech"]

    raw["restrictive_soft_landing"] += m.get("higher_for_longer", 0) * (
        g.get("soft_landing", 0) + 0.25
    )
    raw["higher_for_longer_slowdown"] += m.get("higher_for_longer", 0) * g.get(
        "slowdown", 0
    )
    raw["higher_for_longer_slowdown"] += liq.get("tight", 0) * 0.2
    raw["easing_reacceleration"] += (m.get("easing", 0) + 0.3 * m.get("neutral", 0)) * (
        g.get("reacceleration", 0) + 0.2 * g.get("soft_landing", 0)
    )
    raw["stagflation_or_supply_shock"] += g.get("stagflation_risk", 0) * 1.2 + et.get(
        "commodity_shock", 0
    ) * 0.5
    raw["geopolitical_risk_premium"] += geo.get("elevated_competition", 0) * 0.9 + geo.get(
        "multipolar_fragmentation", 0
    ) * 0.5
    if risk.get("risk_off", 0) > 0.3:
        raw["higher_for_longer_slowdown"] += 0.1
        raw["geopolitical_risk_premium"] += 0.08
    if et.get("power_constrained_ai", 0) > 0.35:
        raw["restrictive_soft_landing"] += 0.05  # AI capex can support soft landing narrative
        raw["geopolitical_risk_premium"] += 0.05

    total = sum(raw.values()) or 1.0
    scen_probs = {k: round(v / total, 4) for k, v in raw.items()}
    drift = 1.0 - sum(scen_probs.values())
    if abs(drift) > 1e-6:
        top = max(scen_probs, key=lambda x: scen_probs[x])
        scen_probs[top] = round(scen_probs[top] + drift, 4)

    ordered = sorted(scen_probs.items(), key=lambda kv: -kv[1])
    primary_id, primary_p = ordered[0]
    secondary_id, secondary_p = ordered[1]

    scenarios = [
        {
            "id": sid,
            "label": SCENARIOS[sid]["label"],
            "description": SCENARIOS[sid]["description"],
            "probability": scen_probs[sid],
        }
        for sid, _ in ordered
    ]

    node_count = len(nodes)
    conf = min(0.9, 0.25 + 0.06 * evidence) * min(1.0, 0.45 + node_count / 40.0)
    if node_count < 25:
        conf = min(conf, 0.55)
    if modes == ["fixture"] or (len(modes) == 1 and "fixture" in modes):
        conf = min(conf, 0.5)
    conf = round(max(0.15, min(0.75, conf)), 3)

    forces.sort(key=lambda d: float(d.get("weight") or 0), reverse=True)
    forces = forces[:8]

    inflection: list[str] = []
    if axis_dom["monetary"] == "higher_for_longer" and g.get("soft_landing", 0) > 0.25:
        inflection.append(
            "Labor/services inflation path — branch between soft landing and slowdown."
        )
    if geo.get("elevated_competition", 0) > 0.3:
        inflection.append(
            "Export-control / Indo-Pacific escalation could reprice risk premia abruptly."
        )
    if et.get("power_constrained_ai", 0) > 0.3:
        inflection.append(
            "Grid/power delivery vs AI capex plans — binding constraint on reacceleration."
        )
    if liq.get("tight", 0) > 0.25:
        inflection.append("Credit/funding stress can force growth scare without rate cuts.")

    notes: list[str] = []
    if node_count <= 20:
        notes.append(
            "Scaffold-level node density — regime is a structural hypothesis, not a live high-confidence call."
        )
    if modes == ["fixture"] or (len(modes) == 1 and "fixture" in modes):
        notes.append("Fixture-only source mode on this run.")

    primary = {
        "id": primary_id,
        "label": SCENARIOS[primary_id]["label"],
        "probability": primary_p,
        "summary": SCENARIOS[primary_id]["description"],
    }
    secondary = {
        "id": secondary_id,
        "label": SCENARIOS[secondary_id]["label"],
        "probability": secondary_p,
        "summary": SCENARIOS[secondary_id]["description"],
    }

    # Backward-compatible flat fields for earlier dashboard wiring
    probabilities = {
        sid: {"label": SCENARIOS[sid]["label"], "probability": scen_probs[sid]}
        for sid in SCENARIOS
    }

    return {
        "schema_version": 1,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "version_id": state.get("version_id"),
        "method": "multi-axis keyword+domain-prior (impact×confidence)",
        "primary": primary,
        "secondary": secondary,
        "probabilities": probabilities,
        "axes": axes_out,
        "scenarios": scenarios,
        "active_forces": forces,
        "drivers": forces,  # alias for dashboard that expects drivers
        "inflection_watch": inflection,
        "confidence": conf,
        "confidence_overall": conf,
        "coverage": {
            "node_total": node_count,
            "evidence_weight": round(evidence, 3),
            "source_modes": modes,
        },
        "data_vintage": {
            "node_count": node_count,
            "source_modes": modes,
            "fixture_scaffold_dominant": (
                modes == ["fixture"]
                or (len(modes) == 1 and "fixture" in modes)
                or node_count < 25
            ),
            "as_of": state.get("updated_at"),
        },
        "notes": notes,
        "dimensions": {
            ax: {
                "label": axis_dom[ax],
                "probabilities": axis_probs[ax],
            }
            for ax in REGIME_AXES
        },
    }


def attach_regime(state: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copied state with regime assessment attached."""
    out = dict(state)
    regime = assess_regime(out)
    out["regime"] = regime
    meta = dict(out.get("meta") or {})
    meta["regime_primary"] = (regime.get("primary") or {}).get("id")
    meta["regime_confidence"] = regime.get("confidence_overall")
    out["meta"] = meta
    return out


def regime_brief_block(regime: dict[str, Any] | None) -> dict[str, Any]:
    """Compact block for brief JSON / dashboard."""
    if not regime:
        return {
            "title": "Regime Assessment",
            "primary": None,
            "axes": [],
            "confidence_overall": None,
        }
    return {
        "title": "Regime Assessment",
        "primary": regime.get("primary"),
        "secondary": regime.get("secondary"),
        "axes": regime.get("axes") or [],
        "scenarios": (regime.get("scenarios") or [])[:5],
        "inflection_watch": regime.get("inflection_watch") or [],
        "confidence_overall": regime.get("confidence_overall"),
        "data_vintage": regime.get("data_vintage"),
        "notes": regime.get("notes") or [],
    }
