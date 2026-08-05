# Horizon macro model — assessment (#20)

**Date:** 2026-08-05  
**Owner:** Meridian (product) · eng on request: Grok  
**Scope:** Revisit current model; land regime layer + source density (not full rewrite).

## What works

| Piece | Assessment |
|-------|------------|
| Offline pipeline | Solid: fixtures → world-state → strategy link → brief; CI-safe |
| Domain set | Broad enough for v1 (10 domains) |
| Fact vs interpretation | Explicit separation + confidence on nodes |
| Strategy load | Reads bets / intent / today / positions |
| Dashboard | Tabs for Overview, Brief, World, Strategy, Watch, Graph on :8795 |
| Strategy-aware ranking | Brief/watchlist boost linked priorities (`synthesis.py`) |

## Gaps (remaining)

1. **Historical delta** — versioned JSON exists; little “what changed since yesterday” in brief.  
2. **Keyword-only strategy linking** — brittle if bets rename; thin positions mapping.  
3. **Per-domain depth** — better than 1-node theater after fixture expand, still not multi-node living graph.  
4. **Live source reliability** — expanded RSS list needs prod smoke (feeds can 404/change).  
5. **L0→L4 implication packets** — weave contract design only (separate board card).

## Enhancement plan (phased)

| Phase | Work | Status |
|-------|------|--------|
| P0 | Strategy-aware rank/annotate on brief + watchlist | **Landed** (prior) |
| **P0b (this slice)** | **Regime assessment layer** (`regime.py`) + brief §0 + Overview card + pipeline stamp | **Landed** |
| **P0b** | **Source expansion** — Fed/ECB/BoE/BIS/BLS/BEA/Treasury/EIA/IMF/WB/State/USTR RSS list | **Landed** |
| **P0b** | Fixture density — fiscal, credit, labor/services, oil, AI controls | **Landed** |
| P1 | “Delta since last brief” section from history store | Not started |
| P1 | Prod live RSS smoke + drop dead feeds | Not started |
| P2 | Richer strategy match (positions → domain tags) | Not started |
| P2 | Dashboard chips for `strategy_priorities` | Not started |

## Regime layer (product contract)

- **Module:** `research/horizon/regime.py`
- **Method:** keyword + domain-prior scoring over nodes (impact × confidence); **no invented prints**
- **Output:** `primary` / `secondary` / `probabilities` / `dimensions` / `drivers` / `confidence` / `notes`
- **Composite IDs:** `restrictive_soft_landing`, `higher_for_longer_slowdown`, `easing_reacceleration`, `stagflation_or_supply_shock`, `geopolitical_risk_premium`
- **Honesty:** confidence hard-capped when node density is scaffold-level; fixture-only runs get an explicit note
- **Surfaces:** brief JSON + markdown §0, `state.regime`, dashboard Overview card, pipeline meta

## Acceptance mapping (#20)

- [x] Written assessment (this file)  
- [x] Concrete enhancement improving daily usefulness (strategy rank + **regime layer** + **source list**)  
- [x] Offline/CLI path still runnable with fixtures + tests (`test_regime`, `test_synthesis`, `test_pipeline`)  
- [ ] Pi deploy of this slice (Grok eng **on Meridian request** after Mac offline green)

## Related

- Issue: https://github.com/cvolkernick/personal-workspace/issues/20  
- Architecture: `ARCHITECTURE.md`  
- Code: `regime.py`, `synthesis.py`, `pipeline.py`, `sources/rss.py`, `fixtures/sample_events.json`, `index.html`
