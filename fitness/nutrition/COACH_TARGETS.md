# Coach-owned nutrition targets

**Status:** shipped v1 (2026-08-27); **v2 micros** (fiber / sugar / sodium) with #571. `recommend_nutrition_targets()` is live. Dashboard load does **not** write `targets.json`. Coach writes on **Apply coach targets** / chat `apply coach targets` only.

**Origin:** Chris, #fitness — calorie/macro targets should come from coach subroutine(s) given **goals vs current data**, not only a sticky Kitchen form.

**Nest (same lock):** `PLANS/FITDASH_COACH_OWNED_TARGETS.md` · `RESEARCH/FITDASH_COACH_TARGETS_CURRENT_STATE_2026_08_27.md`

## Rule

| Object | Who writes | When |
|--------|------------|------|
| **Recommended** kcal / P / C / F / fiber / sugar / sodium | Deterministic Python `recommend_nutrition_targets()` | Every dashboard load (cheap, pure) |
| **Applied** kcal / P / C / F / fiber / sugar / sodium | Human form, `set targets …`, or **`apply coach targets`** | Explicit write only |

Dashboard load, meal-plan refresh, and background cache **must not** write `targets.json`.

This matches training: `suggest_focus_muscles` computes; `set_focus_muscles` writes. Recovery already works that way (`compute_recovery_status` never persists a score).

## Not the owner

- **Ask Grok / SuperGrok** — explain the subroutine payload. Do not invent daily numbers when the subroutine returned a recommendation.
- **Frankenfit the agent** — implements the subroutine; does not become a daily macro oracle in chat.
- **Existing chat** `"apply those recommendations"` — still means “apply macros from the last **assistant** message” (`coach_actions._targets_from_history`). New action **`apply coach targets`** applies the **subroutine** payload only. Do not overload the chat phrase.

## Inputs (already on the FitDash payload)

| Input | Source | Sparse |
|-------|--------|--------|
| Current weight, 7–14d delta | `health.weight` | Abstain calorie change if no recent weigh-in |
| Goal weight | `targets.weight_goal_lbs` (already normalized; Trends guide line) | Needed for cut vs maintain vs slow-bulk inference |
| Nutrition phase | new optional `targets.phase`: `cut` \| `maintain` \| `slow_bulk` | Else infer: notes matching cut/deficit/loss → cut; bulk/surplus/gain → slow_bulk; else current vs goal (≥3 lb above → cut, ≥3 lb below → slow_bulk, else maintain) |
| TDEE hat | 14d mean of **present** `health.calories_burned` | Need ≥5 present days. Missing days stay missing — **never plot/average as 0** |
| Recent intake | 14d mean of present nutrition calories | Logging gap ≠ low intake |
| Recovery | `compute_recovery_status` | Do **not** deepen a cut when score < 40 |
| Protein adherence 7d | `compute_adherence_7d` | Do **not** cut kcal harder if protein hit rate < 50% (compliance, not energy) |
| Applied targets | `targets.json` | Starting point; recommendation is a delta from here, not a random walk |

Wearable burn is an estimate. Put that in `reasons`.

Training `fitness/exercises/goals.json` (`strength_hypertrophy`, DeanT volume) is **not** the nutrition phase. Do not overload it.

## Output shape

```json
{
  "as_of": "2026-08-27",
  "phase": "cut",
  "tdee_kcal": 2450,
  "tdee_days": 12,
  "current_weight_lbs": 168.4,
  "weight_goal_lbs": 150.0,
  "applied": {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
  "recommended": {"calories": 2050, "protein_g": 170, "carbs_g": 185, "fat_g": 60, "fiber_g": 30, "sugar_g": 50, "sodium_mg": 2300},
  "delta": {"calories": -50, "protein_g": -40, "carbs_g": 5, "fat_g": 5, "fiber_g": 30, "sugar_g": 50, "sodium_mg": 2300},
  "abstain": false,
  "reasons": [
    "14d mean wearable burn 2450 kcal (12 present days)",
    "phase=cut; scale 168.4 vs goal 150",
    "protein 1.0 g/lb current"
  ]
}
```

When data is too thin: `abstain: true`, `recommended` omitted or equal to `applied`, reasons say what was missing. Callers keep applied targets.

Round kcal to nearest **50**, macros to nearest **5 g**. Recompute carbs as remainder after protein + fat so the four numbers add up (`P*4 + C*4 + F*9` ≈ calories, ±50).

## Formula (v1 — change with tests, not vibes)

**Calories**

1. `tdee` = mean present burned, 14d, ≥5 days.
2. Default deficit/surplus from phase:
   - `cut`: `tdee - clamp(gap_lb * 15, 250, 500)` (bigger gap → closer to 500).
   - `maintain`: `tdee`.
   - `slow_bulk`: `tdee + 200`.
3. Rate guard (14d scale change → weekly): if cutting and loss faster than **1.5 lb/week**, raise toward TDEE (do not starve a fast drop). If cutting, gap > 3 lb, and loss slower than **0.2 lb/week**, deepen by 100 kcal once, still inside the 250–500 deficit band.
4. Recovery < 40: recommended calories **≥ applied** (never deepen on a red recovery day).
5. Floor: `max(1800, round(11 * current_lb / 50) * 50)` while cutting. Ceiling: `tdee + 400` on slow_bulk.
6. If `tdee` abstains, calorie recommendation abstains. Protein/fat may still recommend from bodyweight.

**Protein:** `1.0 g/lb` current on cut, `0.9 g/lb` on maintain/slow_bulk, clamp **160–230 g**.

**Fat:** `0.35 g/lb` current, clamp **45–80 g**.

**Carbs:** remainder kcal / 4, clamp **100–350 g**. If remainder would go below 100, cut fat toward 45 before dropping carbs under 100.

Do **not** invent TDEE from intake. Intake is a cross-check in `reasons` (“logged 14d mean 2300 vs burn 2450”), not the calorie target.

## Formula (v2 — micros; #571 / #590)

Derived from the **calorie recommendation** (applied calories when calorie rec abstains). Never invented from missing nutrient logs. Micros do not depend on TDEE.

**Fiber (floor — higher is better):** `max(20, round_g(14 * calories / 1000))` — 14 g per 1000 kcal, nearest 5 g, floor 20 g. (2100 kcal → 30 g.)

**Sugar (ceiling — lower is better):** `round_g(calories * 0.10 / 4)` — WHO ≤10% of energy as **total sugars** (GH SUGAR enum; added sugars are skipped). Never label this “added sugars”. (2100 kcal → 50 g.) Stored as `sugar_g`.

**Salt / sodium (ceiling — lower is better):** `2300` mg/day, rounded to 50 mg. Displayed as “Salt (sodium)”. Stored as `sodium_mg`.

Pace bars reuse `pace_vs_expected` kinds from #573: fiber = `floor`, sugar/sodium = `limit`. Today so far resolves each micro as **applied → coach recommended → omit the bar**. Missing logged keys stay muted — never plotted as 0.

`apply coach targets` still writes micros when calorie rec abstains (recommended calories stay applied). Chat: `set fiber to 30`, `set sugar 40`, `set sodium 2000` (mg).

## Module / API (v1)

- `rt_dashboard/nutrition_targets.py` `recommend_nutrition_targets(...)` (pure; imported by coach).
- `build_coach_payload` includes `nutrition_targets: {applied, recommended, …}`.
- `coach_actions`: `apply coach targets` / `apply coach macros` → merge `recommended` into applied via existing `update_targets` + write.
- UI (Daily targets card): recommended vs applied + reasons; **Apply coach targets** button. Four number fields stay as override. Optional `phase` select.
- Ask Grok context pack: `coach.nutrition_targets`; system prompt prefers that payload over inventing macros.
- `normalize_targets` preserves `weight_goal_lbs` and optional `phase`.
- Tests: `tests/test_nutrition_targets.py` (cut / maintain / abstain / recovery-floor / missing-burn-not-zero / apply-does-not-run-on-load) + `test_coach_actions.py`.
- Full package: `python3 -m unittest discover -s tests` in `resistance-dashboard/`.

## Consumers of **applied** targets (do not point them at recommended until apply)

- `calorie_bars.calorie_pacing`
- `remaining_macros` / `generate_meal_plan`
- `compute_adherence_7d`
- Kitchen remaining copy + today board remaining
- Calorie pacing / in-vs-out chips (in-vs-out itself is intake − burned, **not** a target; do not mix)

## Out of scope (this feature)

- Other Trends canvases, AZM spark, 75d calorie chart window
- Hydration / Recovery `.sb-shell` / Hidrate
- Other micronutrient targets beyond fiber / sugar / sodium (backlog #142 leftovers)
- Auto-apply on a weekly cron (v2 only, after v1 apply-button exists)
- Changing live `targets.json` numbers in the lock PR

## Historical README numbers

`fitness/nutrition/README.md` Apr 23 **1,700 kcal** table is a **historical** cutting note. Live applied targets are `targets.json`. Do not “fix” the dashboard to 1700 because the README still says it.
