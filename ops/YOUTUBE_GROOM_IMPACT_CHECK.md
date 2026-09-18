# youtube-groom-impact-check baseline

Comparison GVG named `youtube-groom-impact-check` lands ~**2026-09-19 09:00 ET**.
It must measure the **live** knobs, not the Sep 14 `#731` tick.

No systemd/OpenClaw job with that name exists on prism-gateway (checked
2026-09-18: `youtube-groom.timer` hourly, OpenClaw cron is `x-weekly-analytics`
only). This file + `youtube_groom_impact_check_baseline.json` are the nest SoT
the comparison should read. Pi copy:
`~/.local/share/youtube-groom/impact-check-baseline.json`.

## Old baseline (do not use)

| Field | Value |
|-------|--------|
| Source | #731 live tick 2026-09-14T14:50:50Z |
| `MIN_FIT` | `1` |
| `SEED_THROTTLE_WEIGHT_FLOOR` | `0.25` |
| `HOUSE_TARGET` | `50` (later #788 → 100) |
| Tick | `listed=38 del=1 remain=45 add=8 skip={} quota=2390` |

## New baseline (#815)

| Field | Value |
|-------|--------|
| Source | #815 live tick 2026-09-18T14:47:06Z |
| `MIN_FIT` | `0` |
| `SEED_THROTTLE_WEIGHT_FLOOR` | `0.10` |
| `HOUSE_TARGET` | `100` |
| `CAP` | `200` |
| Tick | `listed=58 del=1 {'dup': 1} remain=58 add=1 skip={} quota=2416 house=100` |

Skip was already empty at `MIN_FIT=1` / floor `0.25`. Candidate supply is still
the limiter (house 58 / target 100). The 09:00 ET comparison should treat the
#815 tick as t0, not `add=8`.
