# FitDash: auto-flip cut and bulk from the body-fat median (visible UI change)

Replaces the propose-only 14-day streak from #1389 (`1aafaf05`). When the median rule below is met, FitDash switches phase itself and writes the new targets. This is not a proposal.

Auto-apply **WRITES phase and targets in prod**. Chris approved auto, via Grok 11/9 11:57 PM. The write goes through the existing `switch_phase` writers (`update_targets` then `applied_targets_write` / `persist_targets`), not a new calorie path. No Goal 3 / macro-band scoring change (Goal 3 is retired). The Today tab changes only by the phase-switch banner.

## Shipped rule

1. For each America/New_York civil day D, the daily value is the **median** of deduped body-fat readings on [D-6, D]. Validity and merge-twin dedupe match `body_fat_phase.py` / the chart helper. A day is **countable** only when that window has at least 4 deduped readings. Otherwise it is **thin**.
2. Flip when, over the last **14 counted** days, the daily median is past the bound on **at least 12** of them **and** the median of all deduped readings in the closed span of those 14 days (earliest counted day through latest) is past the same bound. In a cut, flip to bulk at **<= 10.0%**. In a bulk (`bulk` / `slow_bulk` / `gain`), flip to cut at **>= 15.0%**. Otherwise hold.
3. A thin day **pauses** the count: it is skipped and the 14-counted-day window stretches further back. It never resets the count to 0. One outlier day (a miss) does not reset the other days; it is one of the two allowed misses.
4. **Auto-apply** when that condition is met and `bf_auto_flip` is on.
   - Cut to bulk: maintenance calories = the live coach-card TDEE (rounded to the nearest kcal, not to 50) for the first 14 days. Protein 170 g, fat 65 g, carbs = the kcal remainder `round((kcal - 170*4 - 65*9) / 4)`. Phase is stored as `slow_bulk`. At TDEE 2318 that is **2318 / 170 P / 263 C / 65 F**. If the coach card has no TDEE, the flip does not run and no calories are invented.
   - On day 14 after that flip (flip date + 14), calories become that **captured** TDEE + 200, rounded to the nearest 50, with the same macro split. 2318 becomes **2500 / 170 P / 309 C / 65 F**. The step runs once. A later live TDEE does not replace the captured one, so the bump stays +200.
   - Bulk to cut: restore the cut targets stored on the flip record when leaving cut. Nothing is hardcoded (not 2000/170/195/60). If no cut targets were stored, the flip holds.
   - One flip per crossing. The new phase counts only civil days **after** the flip date, so the next day cannot flip back on the days that caused the flip. The +200 step is its own idempotent record.
5. Evaluation runs when the coach card is built (dashboard load via `attach_coach_card`). Concurrent requests share one result: an exclusive file lock on one machine, and a persisted flip key `user|date|leaving-phase`. When Turso is live the claim is `INSERT ... ON CONFLICT DO NOTHING` on `(user_id, flip_key)`; the loser does not write targets.
6. Manual `switch_phase` still wins. It writes through the same handler, logs `trigger=manual`, and the auto count starts over from that date.

The chart overlay in `body-fat-series.js` is still the trailing 7-day average. The phase decision and the coach card use the median.

## Nourish spec (11/9 ~11:25 PM ET), as written

The note below is the spec received that night. Chris's later choice, above, is what shipped. It replaces "flip proposes, never auto-applies" (Chris approved auto via Grok 11/9 11:57 PM). The weekly ±100 kcal nudge and the Goal 3 macro-band question are not part of this change.

> (1) BULK kcal = live FitDash TDEE estimate + 200, rounded to nearest 50 (11/9 coach card TDEE ~2,318 -> ~2,500). Target gain +0.25% BW/wk, band 0.1-0.35%, measured as 2-week change in 7-day avg weight. Weekly: <0.1% propose +100 kcal; >0.35% propose -100.
> (2) BULK macros: protein 170 g floor; fat ~65 g cap; sat fat <=10% kcal (ApoB 84.7 vs 85 hold); carbs = remainder (2,500 -> ~170P/296C/65F). Fiber >=35 g, sugar <=50 g, sodium 2,300 mg. Split ~27/47/23 is outside Goal 3 band (34/39/27 +/-5): Grok+Lodestar decide pause vs bulk band; DO NOT hardcode either.
> (3) Flip back to CUT: return to live Coach-applied cut targets at that time (today 2,000/170P/195C/60F, rate band 0.25-0.5%/wk, plus 11/16 rate branches). Read live values, never hardcode.
> (4) Ramp: CUT->BULK step to maintenance (TDEE) on flip day, add +200 after 14 days. BULK->CUT: cut target at once.
> Guardrails: flip only with >=8 readings in the 14-cal-day window under same conditions, else hold + 'BF data thin' chip. Threshold must hold 7 consecutive days before flip (BIA +/-3-5 pts). Flip PROPOSES, never auto-applies; every kcal change goes through Grok to Chris.
> Conflict: 11/6 Chris locked BF as read-only nutrition commentary that never changes calories. This rule supersedes it -> needs Chris confirmation via Grok at the gate.

Carbs use the remainder already coded in #1389 (4/4/9). At 2,500 kcal that is 170 P / 309 C / 65 F. Fiber, sugar, sodium, and the sat-fat chip are left as they already are on the target row. Goal 3 scoring is untouched.

## Audit log

`bf_phase_flips`, beside `nutrition_targets`, not inside the targets payload (`normalize_targets` would drop an extra key on the next calorie save, and a growing log should not be rewritten by every macro edit).

- Turso live: table `bf_phase_flips` in the same database as `nutrition_targets` (`user_id`, `flip_key`, `payload`, `created_at`, primary key `(user_id, flip_key)`).
- Turso dark (tests, and a Pi without Turso): `bf_phase_flips.json` in `$FITDASH_COACH_STATE_DIR`, or `~/.local/state/fitdash-coach/` when that env is unset. Same directory as `coach_weekly.json`.

Each phase event stores the civil date, an America/New_York timestamp, the counted days and their daily medians, the hit count, the 14-day median, old phase, new phase, old targets, new targets, and `trigger` `auto` or `manual`. Ramp steps are a separate event keyed `ramp|{flip date}|{user}`.

## How to turn it off

Set `"bf_auto_flip": false` in `fitness/coach/coach_rules.json` (the repo-root file and `resistance-dashboard/fitness/coach/coach_rules.json` are the same document). The flag is read on every coach-card build, not from the cached rules object, so it takes effect on the next dashboard load without a code deploy. Missing key means **on**, which is the default Chris asked for. Off still shows the count. It does not write phase or the day-14 ramp.

## Coach card and banner

Coach (`#coach-phase-card`) shows phase (CUT or BULK), today's 7-day median or `thin`, `N/12 of last 14 counted days`, the 14-day median (or an em dash until 14 counted days exist), the threshold (`Bulk at <=10.0%` / `Cut at >=15.0%`), and the last flip when there is one.

Banner text is `Phase switched to BULK on YYYY-MM-DD` or `Phase switched to CUT on YYYY-MM-DD`. It is on Coach (`#bf-phase-banner-coach`) and Today (`#bf-phase-banner`) for the flip date and the next 7 days. Dismiss posts `{ "action": "dismiss_bf_banner" }` to `/api/phase-barometer`. The flip stays in the log.

## Dry run on the #1389 fixture (11/6–11/9)

Six deduped readings on four days (10.4, 10.6, 10.6, 10.2, 9.9, 10.3). Sorted median **10.35%**.

Evaluated as of **2026-11-09** and **2026-11-10** (today), phase cut, live TDEE 2318 passed in, apply enabled:

| | 2026-11-09 | 2026-11-10 |
| --- | --- | --- |
| 7-day median | 10.35% | 10.35% |
| readings / days | 6 / 4 | 6 / 4 |
| thin | no | no |
| counted days | 3 | 4 |
| hits past <=10.0% | 0 | 0 |
| 14-day median | none (under 14 counted days) | none |
| status | **hold, stay CUT** | **hold, stay CUT** |

`applied_targets_write` was not called. `persist_targets` was not called. No targets file was written. No flip log was created.

## Tests

- `resistance-dashboard` pytest: **2610 passed, 2 skipped**, 149 subtests passed.
- `node resistance-dashboard/tests/*.js`: **27/27** scripts passed.
- `tests/test_body_fat_phase.py` (inside the pytest count): 26 tests. Covers the day-13 outlier (13 hits, not a reset, still flips because 13 >= 12), a thin day that pauses and stretches the window, 11 hits holding without a reset, two evaluations and two racing threads producing one write, no flip-back the next day, maintenance then the day-14 +200 through the real target writers, bulk-to-cut restoring stored 1875/160/180/55, manual override, the disable flag, missing TDEE, the 7-day dismissible banner, and the dry run above.

## Pins

- `sw.js` `CACHE`: `fitdash-shell-v164`
- `app.js?v=bf-auto-flip-1`
- `body-fat-series.js?v=bf-phase-14d-1` (unchanged; the chart average did not change)
- `styles.css?v=macro-colors-1` (unchanged)
- `tests/static_asset_pins.json` app.js tag and sha256 updated to match
