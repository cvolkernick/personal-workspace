# Coach-owned nutrition targets

**Status:** design lock (2026-08-27). Code not shipped. Do not claim the subroutine exists until it does.

**Origin:** Chris, #fitness — calorie/macro targets should come from coach subroutine(s) given **goals vs current data**, not only a sticky Kitchen form.

**Nest (same lock):** `PLANS/FITDASH_COACH_OWNED_TARGETS.md` · `RESEARCH/FITDASH_COACH_TARGETS_CURRENT_STATE_2026_08_27.md`

## Rule

| Object | Who writes | When |
|--------|------------|------|
| **Recommended** kcal / P / C / F | Deterministic Python `recommend_nutrition_targets()` | Every dashboard load (cheap, pure) |
| **Applied** kcal / P / C / F | Human form, `set targets …`, or **`apply coach targets`** | Explicit write only |

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
  "recommended": {"calories": 2050, "protein_g": 170, "carbs_g": 185, "fat_g": 60},
  "delta": {"calories": -50, "protein_g": -40, "carbs_g": 5, "fat_g": 5},
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

## Module / API (when implemented)

- New: `rt_dashboard/coach.py` (or `nutrition_targets.py` imported by coach) `recommend_nutrition_targets(...)`.
- `build_coach_payload` includes `nutrition_targets: {applied, recommended, …}`.
- `coach_actions`: `apply coach targets` / `apply coach macros` → merge `recommended` into applied via existing `update_targets` + write.
- UI (Daily targets card): show recommended vs applied + reasons; **Apply coach targets** button. Keep the four number fields as override.
- Ask Grok context pack: pass the recommendation object; system prompt: prefer it over inventing macros.
- Preserve `weight_goal_lbs` on every save (already true in `normalize_targets`). Add `phase` to normalize + form (optional select).
- Tests: `tests/test_coach.py` (or `test_nutrition_targets.py`) for cut / maintain / abstain / recovery-floor / missing-burn-not-zero / apply-does-not-run-on-load.
- Full package: `python3 -m unittest discover -s tests` in `resistance-dashboard/`.
- SW cache-bust if the card markup/JS changes.

## Consumers of **applied** targets (do not point them at recommended until apply)

- `calorie_bars.calorie_pacing`
- `remaining_macros` / `generate_meal_plan`
- `compute_adherence_7d`
- Kitchen remaining copy + today board remaining
- Calorie pacing / in-vs-out chips (in-vs-out itself is intake − burned, **not** a target; do not mix)

## Out of scope (this feature)

- Other Trends canvases, AZM spark, 75d calorie chart window
- Hydration / Recovery `.sb-shell` / Hidrate
- Micronutrient targets (backlog #142)
- Auto-apply on a weekly cron (v2 only, after v1 apply-button exists)
- Changing live `targets.json` numbers in the lock PR

## Historical README numbers

`fitness/nutrition/README.md` Apr 23 **1,700 kcal** table is a **historical** cutting note. Live applied targets are `targets.json`. Do not “fix” the dashboard to 1700 because the README still says it.
