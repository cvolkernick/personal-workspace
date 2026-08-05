# Horizon macro model — assessment (#20)

**Date:** 2026-08-05  
**Scope:** Revisit current model; land smallest useful enhance (not full rewrite).

## What works

| Piece | Assessment |
|-------|------------|
| Offline pipeline | Solid: fixtures → world-state → strategy link → brief; CI-safe |
| Domain set | Broad enough for v1 (10 domains) |
| Fact vs interpretation | Explicit separation + confidence on nodes |
| Strategy load | Reads bets / intent / today / positions |
| Dashboard | Tabs for Overview, Brief, World, Strategy, Watch, Graph on :8795 |

## Gaps (priority order)

1. **Brief/watchlist not strategy-aware in ranking** — implications section had linkages, but executive brief ranked only by raw `priority_score`, so personal bets did not re-rank what you *see first*.  
2. **Keyword-only strategy linking** — brittle if bets rename; no positions-aware market mapping beyond tickers list.  
3. **Thin historical continuity** — versioned JSON exists; little decay visualization or “what changed since yesterday” in brief.  
4. **Sources** — fixtures + optional RSS; few primary adapters (Fed/EIA-class).  
5. **Sparse per-domain depth** — many domains have 1 fixture node; good for smoke, weak for real macro coverage.  
6. **Explainability** — “why ranked” restated score only (until this PR).

## Enhancement plan (phased)

| Phase | Work | Status |
|-------|------|--------|
| **P0 (this PR)** | Strategy-aware rank/annotate on brief + watchlist; assessment doc | **Landed** |
| P1 | “Delta since last brief” section from history store | Not started |
| P1 | Expand fixture + 1–2 high-cred RSS domains (macro, energy) | Not started |
| P2 | Richer strategy match (positions symbols → domain tags) | Not started |
| P2 | Dashboard surface for `strategy_priorities` chips | Not started |

## Acceptance mapping (#20)

- [x] Written assessment (this file)  
- [x] Concrete enhancement improving daily usefulness (strategy-linked rank + rationale)  
- [x] Offline/CLI path still runnable with fixtures + tests  

## Related

- Issue: https://github.com/cvolkernick/personal-workspace/issues/20  
- Architecture: `ARCHITECTURE.md`  
- Code: `synthesis.py`, `strategy_link.py`, `world_state.py`
