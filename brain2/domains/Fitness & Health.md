# Fitness & Health

**Execution:** `fitness/`, `resistance-dashboard/`  
**Hub:** [[00 Home - B2 Hub]] · **Enables:** [[Strategy & Bets]]

## Why it lives in B2

Fitness is the energy enabler for high-leverage work on AI, autonomy, creative, and wealth bets. Track execution in tools; keep principles and map here.

## Structure (workspace)

| Area | Location |
|------|----------|
| PPL workouts (push / pull / legs) | `fitness/workouts/` |
| Nutrition targets & inventory | `fitness/nutrition/` |
| Health metrics / Fitbit | `fitness/data/`, Fitbit sync |
| Charts | `fitness/charts/` |
| Coach UI + Ask Grok (fitness data) | `resistance-dashboard/` (port **8787**) |

## Training model

- Push / Pull / Legs rotation with logged sets, reps, and volume
- Recovery inputs: sleep, weight, hydration, nutrition adherence
- Dashboard coach surfaces today board, adherence, and weekly review

## Nutrition principles

- High-protein targets tracked in nutrition store
- Meal plan + inventory (in-stock ingredients) feed the resistance dashboard
- Prefer durable habits over one-off crash diets

## Related

- [[Strategy & Bets]] — vitality as equal-weight domain
- [[Agents & Tooling]] — resistance-dashboard Ask Grok pattern (fitness-scoped)
- [[HOWTO - Using B2]] — when to put health *decisions* into B2 vs live metrics
