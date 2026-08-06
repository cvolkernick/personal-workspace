# Horizon macro model — assessment (#20)

**Date:** 2026-08-05  
**Owner:** Meridian (product) · eng on request: Grok  
**Scope:** Revisit current model; land regime layer + source density (not full rewrite).

## What works

| Piece | Assessment |
|-------|------------|
| Offline pipeline | Solid: fixtures → world-state → regime → strategy link → brief; CI-safe |
| Domain set | Broad enough for v1 (10 domains) |
| Fact vs interpretation | Explicit separation + confidence on nodes |
| Strategy load | Reads bets / intent / today / positions |
| Dashboard | Tabs for Overview, Brief, World, Strategy, Watch, Graph on :8795 |
| Strategy-aware ranking | Brief/watchlist boost linked priorities (`synthesis.py`) |
| Regime layer | Multi-axis + composite scenarios (`regime.py`) |
| Source density | Official RSS list far beyond Fed/EIA (16 feeds) |

## Gaps (remaining)

1. **Historical delta** — versioned JSON exists; little “what changed since yesterday” in brief.  
2. **Keyword-only strategy linking** — brittle if bets rename; thin positions mapping.  
3. **Per-domain depth** — better after fixture expand, still not multi-node living graph.  
4. **Live source reliability** — expanded RSS list needs Pi prod smoke (feeds can 403/404).  
5. **L0→L4 implication packets** — weave contract design only (separate sequence).  
6. **Numeric macro prints** — rates/FCI/FX still not hard features; regime is node-weighted prior.

## Enhancement plan (phased)

| Phase | Work | Status |
|-------|------|--------|
| P0 | Strategy-aware rank/annotate on brief + watchlist | **Landed** (prior) |
| **P0b** | **Regime assessment layer** + brief §0 + Overview + pipeline stamp | **Landed** |
| **P0b** | **Source expansion** — multi-CB + stats + EIA + IMF/WB + State/USTR/DoD/WH/SEC | **Landed** |
| **P0b** | Schema docs (`docs/REGIME_*`, `docs/SOURCE_*`) | **Landed** |
| P1 | “Delta since last brief” section from history store | Not started |
| P1 | Prod live RSS smoke + drop dead feeds | Not started |
| P2 | Richer strategy match (positions → domain tags) | Not started |
| P2 | Scheduled Pi refresh + stale flags | Not started |

## Regime layer (product contract)

- **Module:** `research/horizon/regime.py`  
- **Docs:** `docs/REGIME_ASSESSMENT_SCHEMA.md`  
- **Axes:** monetary · growth · liquidity · risk_appetite · geopolitics · energy_tech  
- **Scenarios:** restrictive_soft_landing · higher_for_longer_slowdown · easing_reacceleration · stagflation_or_supply_shock · geopolitical_risk_premium  
- **Method:** keyword/domain evidence over existing nodes; **no invented prints**  
- **Honesty:** `confidence_overall` capped; fixture-scaffold flagged in `data_vintage`  
- **Surfaces:** `state.regime`, `brief.regime`, markdown §0, dashboard Overview  

### First-pass (fixture seed, offline 2026-08-05)

| Axis / scenario | Dominant | p (approx) |
|-----------------|----------|------------|
| **Primary scenario** | Geopolitical risk-premium | **38%** |
| Monetary | higher_for_longer | 65% |
| Geopolitics | elevated_competition | 51% |
| Energy/tech | power_constrained_ai | 84% |
| Growth | stagflation_risk | 33% |
| Risk appetite | mixed | 47% |
| Overall conf | — | **0.50** (scaffold) |

## Source density (P0b)

- See `docs/SOURCE_CATALOG.md`.  
- Baseline was **Fed + EIA only**; adapter now **16** official feeds.  
- Live items enter at conf 0.45 as leads.

## Acceptance mapping (#20)

- [x] Written assessment (this file)  
- [x] Concrete enhancement (strategy rank + **regime layer** + **source list**)  
- [x] Offline/CLI path runnable with fixtures + tests (**20/20** unittest green)  
- [ ] Pi deploy of this slice (Grok eng **on Meridian request**)

## Related

- Issue: https://github.com/cvolkernick/personal-workspace/issues/20  
- Architecture: `ARCHITECTURE.md`  
- Code: `regime.py`, `synthesis.py`, `pipeline.py`, `sources/rss.py`, `fixtures/sample_events.json`, `index.html`, `docs/*`
