# Nutrition

> Visual targets + charts in the [Fitness dashboard](../../dashboard/index.html).

## Quick Links

- [Meal Plan](./meal-plan.md) — Daily cutting plan with macros
- [High Protein Per Dollar](./high-protein-value.md) — Budget protein options

## Hydration

![Water Trend](./water-trend.png)

**Target:** 3,000 mL (3L) per day

## Daily Targets (CUTTING - Updated Apr 23)

> ⚠️ **Analysis:** Weight gain +4.4 lbs (Jan-Apr) indicates ~570 cal/day surplus
> **Target adjusted to:** 1,700 cal/day for deficit

| Metric | Target | Notes |
|--------|--------|-------|
| Calories | **1,700** | Down from 2,200 (deficit mode) |
| Protein | 200g+ | Keep high |
| Carbs | ~150g | Lower to hit calorie target |
| Fat | ~45g | Keep moderate |

**Expected:** ~1 lb/week weight loss


## Dashboard inventory & meal planning

| File | Purpose |
|------|---------|
| `inventory.json` | Curated ingredients you currently have (add/remove from dashboard) |
| `targets.json` | Daily calorie + macro targets used by rest-of-day meal planner |

The resistance dashboard reads today's intake from Google Health, compares to `targets.json`, and builds a plan from **in-stock** items in `inventory.json`.
