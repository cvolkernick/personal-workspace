# Post-build review: resistance training dashboard

What shipped works as a functional PPL log viewer/logger with volume & strength charts and a recovery score driven by sleep/weight (when Google Fit is connected) plus recent training load. Below are the highest-leverage additions that would make it *significantly* more useful for tracking real resistance progress.

## High impact (do next)

1. **Per-exercise progression targets / next-session prescriptions**  
   Surface “last time you did X at Y×Z; suggested next: +2.5 lb or +1 rep” using simple progressive-overload rules. Right now charts show history but don’t tell you what to lift *today*.

2. **RPE / RIR and set quality, not just load**  
   Logs are weight × sets × reps only. Adding optional RPE (or “left in tank”) lets volume load and recovery status reflect *effort*, which is what actually drives fatigue.

3. **Bodyweight-relative strength & e1RM normalized by weight**  
   With live scale data, show e1RM / bodyweight for main lifts. Strength trends in absolute pounds can look flat while relative strength rises during a cut.

4. **Deload detection and scheduled rest compliance**  
   Your notes already mention deloads and “BE FULLY RESTED.” Auto-flag when 7-day volume spikes, sleep drops, or PPLR rest-day rule is broken (rest only after 3 straight days).

5. **PR ledger with context**  
   Automatic PR table (best e1RM / best volume set) with date and prior PR. Celebrating PRs is fine; *comparing conditions* (sleep, bodyweight that week) is what makes them decision-useful.

## Medium impact

6. **Exercise name normalization / aliases**  
   Historical logs mix `Tricep Pushdown` vs `Tricep Pushdowns`, `RDL` vs `RDLs`, `DB DB Shoulder Press`. A small alias map would clean strength trends that currently fragment.

7. **Warm-up vs working-set filtering**  
   Multi-weight lines include ramp sets. Tagging working sets (or auto-detecting top sets) would make “best working weight” and volume more honest.

8. **Injury / pain flags and exercise substitutions**  
   Simple per-session tags (“shoulder tweak”) that grey out or swap recommended accessories.

9. **Offline / PWA install + one-tap log templates**  
   Mobile-friendly CSS is there; a home-screen PWA with last-session templates would make gym logging faster than typing each exercise.

10. **Export + weekly email/Telegram summary**  
    One screenshot-quality weekly report: volume vs last week, top slopes, recovery label, missed sessions.

## Data / integrations

11. **Complete Google Fit OAuth bootstrap UI**  
    Live client exists; this environment lacked tokens. An in-app “Connect Google” redirect + token storage would close the last mile for weight/sleep.

12. **GitHub write token setup + PR-based logging option**  
    Remote pull works on public `master`; write needs a PAT. Optional: open a PR instead of committing straight to `master` for safer history.

13. **Heart-rate variability / resting HR if available**  
    Fitbit stubs already exist in `fitness/data`. Folding RHR/HRV (Fitbit export or Fit) would sharpen the recovery model beyond sleep hours alone.

14. **Nutrition coupling (protein / calories)**  
    Out of original scope, but recovery suggestions get much better when under-eating is visible alongside high volume.

## Analytics depth

15. **Muscle-group volume balance (push/pull/legs + horizontal/vertical)**  
    Catch imbalances (e.g. pressing volume >> pulling) that absolute totals hide.

16. **Estimated fatigue / stimulus models (e.g. set-volume landmines)**  
    Even a crude “hard sets near failure this week” counter beats raw tonnage for hypertrophy tracking.

17. **Plateau detector**  
    Flag exercises with slope ≈ 0 for N sessions so you change stimulus, not just “try harder.”

## UX polish

18. **Edit / delete last session**  
    Logging mistakes happen; append-only is safe but painful without a fix-up path.

19. **Rest timer + plate calculator on log form**  
    Gym-floor convenience keeps the app open during the workout (higher log compliance).

20. **Compare any two date ranges**  
    “This mesocycle vs last” for volume and e1RM—critical once history spans multiple blocks.

---

**Bottom line:** the foundation (real GitHub log I/O shape, pure volume/e1RM math, recovery labeling, mobile UI) is solid. The jump from “nice dashboard” to “training co-pilot” is mostly **next-session prescriptions**, **effort (RPE)**, **bodyweight-normalized strength**, and **closing Google OAuth + write-token setup** so every morning the recovery card and charts refresh without manual files.
