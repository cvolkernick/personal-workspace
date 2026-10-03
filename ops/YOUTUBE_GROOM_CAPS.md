# youtube-groom live caps (Pi)

Writer stays on prism: `~/.local/lib/youtube-groom/youtube_groom.py`  
Nest `scripts/youtube_groom.py` is **policy only** (house caps + insert-budget math).  
**Do not copy the nest file over the Pi binary.** Grok patches the live writer in place.

Hearted 8/31 values stand except the per-tick insert ceiling. Master had no
caps file; these match PR #429’s verified Pi names.

Superseded by #957 (kept so the old names stay readable):

```
MAX_INSERTS_PER_TICK = removed   # was 8 (and 4 before 8/31)
HOUSE_TARGET         = 100       # was 50; fill target after prune; not YouTube 5000 (#788)
HOUSE_TARGET_TOLERANCE = 10      # #852 band 90–110; sidecar youtube_groom_control.py
FRESH_HOURS          = 168       # was 72; matches STALE_HARD_DAYS (7d)
CAP                  = 200       # was 100 (breaker reason cap_100); not a target
STALE_HARD_DAYS      = 7         # unchanged
MAX_DELETES_PER_TICK = 80        # unchanged
keep_n               = 10        # empty fallback; unchanged
MIN_FIT              = 0         # was 1 (#731); was 2. thesis-fit skip disabled (#815)
SEED_THROTTLE_WEIGHT_FLOOR = 0.10  # was 0.25 (#731); was 0.4. skip SEED_THROTTLE only below this (#815)
SEED_UPLOADS_PER_CHANNEL = 50    # was 6; live writer only — YouTube page max, 7d window (#788)
```

#957 live values. High side of the band is clamped to CAP. Never exceed 250.

```
HOUSE_TARGET         = 250       # was 100
HOUSE_TARGET_TOLERANCE = 15      # band 235–250
FRESH_HOURS          = 720       # was 168; matches STALE_HARD_DAYS (30d)
CAP                  = 250       # was 200; hard max
STALE_HARD_DAYS      = 30        # was 7
MIN_FIT              = 0         # unchanged; thesis-fit skip stays off
SEED_THROTTLE_WEIGHT_FLOOR = 0.00  # was 0.10; off at baseline, not only as a notch
WEIGHT_FLOOR         = 0.05      # was 0.15
SEED_UPLOADS_PER_CHANNEL = 50    # unchanged; YouTube page max
CLIMB_INSERTS_PER_DAY = 40       # gradual fill; not MAX_ADD_PER_DAY
CHANNEL_SHARE        = max(12, ceil(5% of playlist))
DISCOVERY_MIX_TARGET = 0.40      # rolling 7d adds from channels Chris is not subscribed to
```

Insert budget after prune = `min(slots to HOUSE_TARGET, slots to CAP, remaining playlist slots)`.  
No add/hour clamp. Do not invent `MAX_ADD_PER_DAY`. If the Pi file has a YouTube API quota guard, keep it (nest has not seen one).

#1045 long-form only. Do not add a YouTube Short to AI Curated.

```
MIN_LONGFORM_SEC     = 60        # Chris: under 60s is a Short
SHORTS_MAX_SEC       = 180       # YouTube may mark a Short up to 3 minutes
```

Skip reasons, silent to Chris, written to `skipped_add`, `skip_reasons`, and `groom.log`:

- `short<60s` — duration under 60 seconds
- `short-flagged` — `#shorts` / `#short` in the title, description, or tags, a `/shorts/` URL on the candidate, or (only when `SHORTS_URL_PROBE=1`) a 60–180s video whose `https://www.youtube.com/shorts/<id>` URL returns 200. The probe is off by default. It is unofficial. The Data API has no `isShort` field. Probe errors fail open.
- `duration-unknown` — missing, malformed, or `P0D` (live/upcoming). Unknown duration is not added, and it no longer gets the old `dur == 0` ranking bonus.

`filter_longform` in `scripts/youtube_groom_supply.py` runs after `supply_tick` and before ranking. `search.list` does not set `videoDuration`: `medium` drops interviews over 20 minutes, and `long` alone drops the 4–20 minute band. Shorts already on the playlist are not pruned.

Playlist id: `PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At`

Auth/tick failure alerts (#480) are a **separate** log reader:
`scripts/youtube_groom_health.py` → Pi `health.json` + #workflow (Grok).
Do not copy this policy module over the writer. Landing path:
[`YOUTUBE_GROOM_HEALTH.md`](YOUTUBE_GROOM_HEALTH.md).

Control loop (#852, rebased by #957): hold playlist at **235–250**
(was 90–110). Sidecar
[`YOUTUBE_GROOM_CONTROL.md`](YOUTUBE_GROOM_CONTROL.md) +
`scripts/youtube_groom_control.py`. Copy alongside the writer, never over it.

Impact comparison (`youtube-groom-impact-check`, ~2026-09-19 09:00 ET):
[`YOUTUBE_GROOM_IMPACT_CHECK.md`](YOUTUBE_GROOM_IMPACT_CHECK.md) +
`ops/youtube_groom_impact_check_baseline.json`. Pi copy
`~/.local/share/youtube-groom/impact-check-baseline.json`. Do not use the
Sep 14 `add=8` / `skip={}` tick as t0.

Per-tick listed/add/skip/quota + 24h rollup (#838, **not #759**):
[`YOUTUBE_GROOM_TICK_REPORT.md`](YOUTUBE_GROOM_TICK_REPORT.md).
`scripts/youtube_groom_tick_report.py` → Pi `tick_report.json` + `ticks.jsonl`
+ 15m `ops/board/youtube_groom_tick_report.json`. Copy **alongside** the
writer, never over it. Do not remint OAuth for this path.
