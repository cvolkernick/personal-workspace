# YouTube AI Curated queue

Playlist: **AI Curated** — `PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At`

**One writer:** the Pi hourly `youtube-groom.timer` on prism.  
**Live file:** `~/.local/lib/youtube-groom/youtube_groom.py` (MD5 was `25b0bed0ca8f214f9437af3b9a8cfa9d`).  
That file was **never in nest git**. Nest `scripts/youtube_groom.py` is the policy scorecard only. Do **not** copy any nest `.py` over the Pi file. Do not add a second writer. Do not touch OAuth, Pi systemd, or Mac `~/.config/youtube-mcp`. Do not enable OpenClaw 11:00 cron.

B2 pulls state only (`youtube-groom/state.json`, `never_readd`, `groom.log`).

Constants note: [`YOUTUBE_GROOM_CAPS.md`](YOUTUBE_GROOM_CAPS.md).

`PLANS/YOUTUBE_PLAYLIST_GROOMING.md` does not exist on master or PR #429 — not added.

## Long-form only (#1045)

Never add a YouTube Short to **AI Curated**. Chris, 2026-10-03.

- A Short is `contentDetails.duration` under **60 seconds** (`MIN_LONGFORM_SEC`). That is the definition, not YouTube's 3-minute Shorts cap.
- YouTube can still flag a 61–180 second video as a Short (`SHORTS_MAX_SEC`). Those are skipped when the title, description, or tags contain `#shorts` / `#short`, when the candidate URL contains `/shorts/`, or when `SHORTS_URL_PROBE=1` and `https://www.youtube.com/shorts/<id>` returns 200 with redirects off (303 to `/watch` means not a Short). The probe is off by default. It is an unofficial heuristic. The Data API has no `isShort` field. Errors fail open.
- Missing, malformed, and `P0D` durations are not added.
- Skip reasons: `short<60s`, `short-flagged`, `duration-unknown`. They show up on `skipped_add`, in `skip_reasons`, and in `groom.log`. Chris is not paged for a skip.
- The gate is `filter_longform` in the supply sidecar, after discovery and before ranking. Unknown durations do not get a ranking bonus.
- Shorts already on the playlist stay until a later prune decision. This slice does not remove them.
- Copy the updated sidecar beside the Pi writer and run `--patch-writer`. Do not copy `scripts/youtube_groom.py` over the writer.

## Why the playlist sat ~25, then grew slowly

Not a `TARGET_SIZE=25` (that name does not exist on Pi).  
The old 72h fresh cull plus `MAX_INSERTS_PER_TICK` (4, then 8) kept the list small. YouTube’s 5000 ceiling is not the limiter. CAP 200 is a **breaker**, not a fill target. House target ~100 is a **target** (was ~50; #788).

## Before → after (this change)

| Name | Was (live / hearted 8/31) | Now |
|------|---------------------------|-----|
| `MAX_INSERTS_PER_TICK` | **8** (4 before 8/31) | **removed** — one tick may insert as many as needed to approach house target after prune |
| house target | ~50 (target, not a YouTube cap) | **100** (#788; still a target, not a cap) |
| `FRESH_HOURS` | **168** | **168** |
| `CAP` | **200** (breaker, was `cap_100`) | **200** (breaker) |
| `STALE_HARD_DAYS` | **7** | **7** |
| `MAX_DELETES_PER_TICK` | 80 | 80 |
| `keep_n` empty fallback | 10 | 10 |

## Volume knobs (#731 then #815)

Prefer more fresh content over candidate-starved ticks. Live Pi writer only.
Nest documents the constants; **do not copy nest over Pi**.

| Name | Was (#731) | After #731 | Now (#815) |
|------|------------|------------|------------|
| `MIN_FIT` | **2** (skip `fit < 2`) | **1** (keep `fit ≥ 1`) | **0** (thesis-fit skip disabled; accept `fit ≥ 0`) |
| `SEED_THROTTLE_WEIGHT_FLOOR` | **0.4** | **0.25** | **0.10** |

`SEED_KEEPERS`, `CAP` 200, prune/dup, OAuth unchanged. `HOUSE_TARGET` / `CAP` unchanged in #815.

## House size (#788)

Chairman (via GVG on #718) wants the daily feed closer to **~100 videos/day**.
Live Pi writer only (2026-09-16). Nest documents the constants; **do not copy nest over Pi**.
Did not re-loosen `MIN_FIT` / `SEED_THROTTLE` in #788. CAP 200 stays a breaker.
#815 loosened those floors one more notch; `HOUSE_TARGET` stayed 100.

| Name | Was | Now |
|------|-----|-----|
| `HOUSE_TARGET` | **50** | **100** |
| `SEED_UPLOADS_PER_CHANNEL` | **6** (call site) | **50** (YouTube page max; 7d window) |
| `CAP` | **200** | **200** (unchanged) |

## Volume notch (#815)

Chairman (via GVG on #718, 2026-09-18) wanted one more volume notch.
Playlist still sat ~58 despite `HOUSE_TARGET=100`. Live Pi writer only
(2026-09-18). Nest documents the constants; **do not copy nest over Pi**.
Did not change `HOUSE_TARGET` / `CAP` / `SEED_KEEPERS`.

| Name | Was | Now |
|------|-----|-----|
| `MIN_FIT` | **1** | **0** (no thesis-fit skip) |
| `SEED_THROTTLE_WEIGHT_FLOOR` | **0.25** | **0.10** |
| `HOUSE_TARGET` | **100** | **100** (unchanged) |
| `CAP` | **200** | **200** (unchanged) |

`youtube-groom-impact-check` re-baselined off the Sep 14 `add=8` / `skip={}`
tick so the ~2026-09-19 09:00 ET comparison measures these knobs. See
[`YOUTUBE_GROOM_IMPACT_CHECK.md`](YOUTUBE_GROOM_IMPACT_CHECK.md).

## Policy that stays

- **Prune-first:** dead/private, dups, rated, swipe-off, `STALE_HARD_DAYS=7`
- After prune, insert toward house target ~100
- Stop inserts if `CAP` (200) would be exceeded
- Stop inserts at remaining playlist slots (YouTube 5000)
- If a YouTube API quota guard exists on the Pi writer, **keep it**
- Do not invent `MAX_ADD_PER_DAY`

## Left alone

- `FRESH_HOURS=168`, `CAP=200`, `STALE_HARD_DAYS=7`
- `keep_n=10` empty fallback
- Dup / `never_readd` prune
- OAuth, youtube-mcp, Mac token as prod
- systemd `ExecStart` writer + hourly timer (health adds `ExecStopPost` only)
- A second writer / Bot cron

## Auth/tick alerts (#480)

Silent `invalid_grant` must not freeze the playlist unnoticed. Log reader:
`scripts/youtube_groom_health.py` (copy **alongside** the Pi writer, never over it).
Landing path: [`YOUTUBE_GROOM_HEALTH.md`](YOUTUBE_GROOM_HEALTH.md).
Grok on #workflow + `ops/board/youtube_groom_health.json` (15m export). Not a Chris DM.

## Tick listed/add/skip/quota (#838, not #759)

#759 was a junk malformed create — it never received daily summaries.
Live writer already appends `listed=` / `add=` / `skip=` / `quota=` to
`groom.log`. Durable Grok/GVG path is the log reader
`scripts/youtube_groom_tick_report.py` (copy **alongside** the Pi writer,
never over it): Pi `tick_report.json` + append-only `ticks.jsonl` + 15m
`ops/board/youtube_groom_tick_report.json`. Landing:
[`YOUTUBE_GROOM_TICK_REPORT.md`](YOUTUBE_GROOM_TICK_REPORT.md).
Do not remint OAuth. Do not crank ticks/seeds without the quota block
in that JSON.

## Control loop (#852)

Hold the playlist at **100 ± 10** (band 90–110). Sidecar
`scripts/youtube_groom_control.py` (copy **alongside** the Pi writer,
never over it) notches one knob per tick when outside the band.
Landing: [`YOUTUBE_GROOM_CONTROL.md`](YOUTUBE_GROOM_CONTROL.md).
`HOUSE_TARGET_TOLERANCE = 10` lives next to `HOUSE_TARGET` in this
scorecard. Quota headroom still required before seeds/ticks/caps.
Anti-oscillation cooldown prevents loosen→tighten flip-flops.

## Cap 250 and discovery (#957)

Chairman, 2026-09-30: raise the curator playlist to **250** and loosen fit.
Live Pi writer only. Nest documents the constants. **Do not copy nest over Pi.**
`scripts/youtube_groom_supply.py` is the discovery / share-cap / climb sidecar.
Copy it alongside the writer, then run its `--patch-writer` on the Pi file.
Do not replace the writer with the scorecard.

| Knob | Was (#852) | Now (#957) |
|------|------------|------------|
| `HOUSE_TARGET` | **100** | **250** |
| `CAP` | **200** | **250** (hard max) |
| band | 90–110 | **235–250** (tolerance 15, high side clamped) |
| `STALE_HARD_DAYS` | **7** | **30** |
| `FRESH_HOURS` | **168** | **720** |
| `SEED_THROTTLE_WEIGHT_FLOOR` | **0.10** | **0.00** |
| `WEIGHT_FLOOR` | **0.15** | **0.05** |
| `MIN_FIT` | **0** | **0** |
| `SEED_UPLOADS_PER_CHANNEL` | **50** | **50** (YouTube page max; unchanged) |
| `CLIMB_INSERTS_PER_DAY` | none | **40** |

Discovery search runs every non-hour0 tick, after insert headroom is reserved
(`search.list` = 100 units, max 4 calls). At least 40% of adds over 7 days
should come from channels Chris is not subscribed to. One channel's share of
the playlist is `max(12, ceil(5%))`. Tick log field `supply=` carries the
pool size, the subscribed/discovered split, unique channels, and search units.

30 days is the stale window because the inner fresh prune deletes anything
older than `FRESH_HOURS` whenever one newer video exists. A 7-day window
cannot hold 250. `CLIMB_INSERTS_PER_DAY` is the gradual fill. It is not
`MAX_ADD_PER_DAY` and it does not bring back `MAX_INSERTS_PER_TICK`.

## Tests

```bash
python3 -m unittest scripts.tests.test_youtube_groom -v
python3 -m unittest scripts.tests.test_youtube_groom_health -v
python3 -m unittest scripts.tests.test_youtube_groom_tick_report -v
python3 -m unittest scripts.tests.test_youtube_groom_control -v
```
