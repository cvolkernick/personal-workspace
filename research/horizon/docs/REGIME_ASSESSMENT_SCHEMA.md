# Regime assessment schema (v1)

**Owner:** Meridian · **Issue:** #20 · **Module:** `research/horizon/regime.py`  
**Status:** Landed on `feature/horizon-macro-enhance-20` (commit family `7db025d`+)

## Purpose

Attach a **probability-weighted, multi-axis regime view** to every world-state / brief so
decision agents get a top-line macro frame without scanning every domain node.

## Axes (`REGIME_AXES`)

| Axis id | Label | Dominant states (vocab) |
|---------|-------|-------------------------|
| `monetary` | Monetary policy | higher_for_longer · easing · neutral · unknown |
| `growth` | Growth / activity | soft_landing · slowdown · reacceleration · stagflation_risk · unknown |
| `liquidity` | Liquidity / credit | tight · neutral · loose · unknown |
| `risk_appetite` | Risk appetite | risk_on · mixed · risk_off · unknown |
| `geopolitics` | Geopolitics | elevated_competition · multipolar_fragmentation · deescalation · unknown |
| `energy_tech` | Energy / tech constraint | power_constrained_ai · commodity_shock · transition_smooth · unknown |

Per-axis **state probabilities sum to 1.0**. `dominant` = argmax state.

## Composite scenarios (`SCENARIOS`)

| id | Label |
|----|--------|
| `restrictive_soft_landing` | Restrictive / soft landing |
| `higher_for_longer_slowdown` | Higher-for-longer + slowdown |
| `easing_reacceleration` | Easing + reacceleration |
| `stagflation_or_supply_shock` | Stagflation / supply shock |
| `geopolitical_risk_premium` | Geopolitical risk-premium |

Scenario probabilities **sum to 1.0**. `primary` points at the top scenario.

## Output document (abridged)

```json
{
  "schema_version": 1,
  "as_of": "ISO-8601",
  "method": "...",
  "primary": { "id", "label", "probability", "summary" },
  "axes": [
    {
      "id": "monetary",
      "label": "...",
      "dominant": "higher_for_longer",
      "states": [{ "id", "label", "probability" }],
      "confidence": 0.55,
      "evidence_nodes": ["..."]
    }
  ],
  "scenarios": [{ "id", "label", "probability", "description" }],
  "active_forces": [],
  "inflection_watch": [],
  "confidence_overall": 0.5,
  "data_vintage": {
    "node_count": 17,
    "fixture_scaffold_dominant": true,
    "source_modes": ["fixture"]
  },
  "notes": []
}
```

### Confidence honesty

- Hard ceiling **0.75** (not market-calibrated).  
- Fixture-scaffold dominant → tighter caps (often ~0.50–0.55).  
- Method is keyword/domain evidence over **existing nodes only** — no invented prints.

## Pipeline attachment

1. After events (or link-only) → `attach_regime(state)` / pipeline stamp.  
2. Persist `world_state.regime`.  
3. `synthesize` → `brief.regime` (+ markdown §0).  
4. Dashboard Overview card reads `regime`.

## First-pass from fixture seed (2026-08-05 offline)

| Field | Value |
|-------|--------|
| **Primary scenario** | `geopolitical_risk_premium` ≈ **38%** |
| Secondary mass | stagflation/supply ≈24% · restrictive soft landing ≈23% |
| Monetary dominant | `higher_for_longer` ≈65% |
| Geopolitics dominant | `elevated_competition` ≈51% |
| Energy/tech dominant | `power_constrained_ai` ≈84% |
| Growth dominant | `stagflation_risk` ≈33% (close race — sparse growth prints) |
| Risk appetite | `mixed` ≈47% |
| Overall conf | **0.50** (fixture scaffold) |
| Nodes | 17 |

Treat as **structural prior**, not a live market SoT.

## Next

- Numeric adapters (policy rate, real rates, FCI, FX) as hard features.  
- Delta vs prior `version_id`.  
- L0→L4 implication packet fields fed by `primary` + `axes`.
