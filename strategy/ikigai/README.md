# Ikigai (Layer 0) — usage & quarterly review

## Files

| File | Role |
|------|------|
| [`ikigai.md`](./ikigai.md) | Human narrative (center, pillars, intersections) |
| [`pillars.json`](./pillars.json) | Machine-readable source of truth for Orchestrator / agents |
| [`../bets.md`](../bets.md) | High-conviction bets — **nested under** Ikigai, not co-equal Layer 0 |
| [`../horizon.md`](../horizon.md) | Seasonal (Horizon) planning layer |
| [`../intent.json`](../intent.json) | Near-term operator focus (hours–days) |

## Cadence

| When | Action |
|------|--------|
| **Quarterly** | Run the checklist below; update `pillars.json` + `ikigai.md` center if needed |
| **When bets change** | Update `linked_bets` only; do not rewrite all pillars for market noise |
| **Daily** | Do **not** edit Layer 0 — use Orchestrator + intent |
| **After major wins/losses** | Add one bullet under `good_at` or `out_of_bounds` |

## Quarterly review checklist

- [ ] Re-read **center statement** — still true in one breath?  
- [ ] Scan **four pillars** — any item stale, false, or missing?  
- [ ] Update **intersections** summaries if pillars moved  
- [ ] Confirm **out_of_bounds** still catches real failure modes (dashboard thrash, etc.)  
- [ ] Align **linked_bets** with `../bets.md`  
- [ ] Align **Horizon** seasonal themes with center themes  
- [ ] Spot-check active `initiatives/*.md` for `ikigai_pillars` / `ikigai_intersection` frontmatter  
- [ ] Set `updated_at` in `pillars.json` (ISO UTC) when done  

## Orchestrator

- Payload key: `ikigai` (and `identity`)  
- UI: **Identity / Ikigai** panel (edit form → POST `/api/ikigai`)  
- Focus Coach receives Ikigai in context (soft guidance, not hard filters)

## B2

- Domain note: `brain2/domains/Ikigai & Identity.md`  
- Linked from B2 Hub and Personal Workspace Map  

## Principles

- One source of truth on disk (`pillars.json` + narrative MD).  
- Soft guidance first: Conductor prefers center-aligned actions.  
- No secrets, balances, or session dumps in this folder.
