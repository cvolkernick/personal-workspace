# lp118: one-time Leg Press +118 lb sled backfill (2026-11-25)

Chris asked (2026-11-25 ~3:26 AM ET) for the Hammer Strength Linear Leg Press
118 lb starting weight to be added once to every past Leg Press set. There is
no base-weight field and no auto-add: new logs are typed as totals.

Run on the Pi only. Every script is dry by default; `apply` writes Turso.
They read the running service environment and never print it.

| Script | What it does | Status |
|---|---|---|
| `migrate.py` | +118 on every Leg Press set object through 2026-11-08 (26 sessions, 64 working sets, 2026-03-19 to 2026-11-08). Excludes anything with `created_at`/`closed_at` at or after 2026-11-25 00:00 ET plus any session holding the 208 x 10 Leg Press. Recomputes volume, e1RM, best weight; re-runs `apply_auto_prs` in date order (Leg Press flags only). Compare-and-swap updates plus marker `schema_meta.leg_press_sled_backfill_2026-11-25` in one transaction. Replay = 0 rows. | Applied 2026-11-25 4:41 AM ET after backup `bk-20261125` |
| `plan_patch.py` | Rebased the stored 11/24 + 11/25 plan Leg Press slot 205 -> 323 (the 11/08 328 x 4 drop_load). | Applied 2026-11-25. Stale for 11/25, see below |
| `plan_fix_213.py` | Corrects the stored 2026-11-25 Leg Press slot from 323 to the planner's own value from the latest log (2026-11-24 208 x 10 -> 213, add_load). Leg Press slot only. | NOT applied. Waits for Grok's go |

Verified after the backfill: 11/08 reads 328 x 2 x 4, 90-day Leg Press
tonnage 26,145 -> 42,429 (44,509 including the untouched 11/24 208 x 10),
tonight's 11/24 session byte-identical, no other exercise changed.

Why 323 lingered: the 2026-11-25 plan row was generated at 00:08 ET, before
the 11/24 Legs session (closed 3:37 AM ET) existed, and the same-day saved
plan is reused. `plan_patch.py` then rebased that stale slot. The planner
itself already prescribes 213 from the 208 x 10 log
(`tests/test_leg_press_latest_log_lp118.py`), so new plans need no code change.
