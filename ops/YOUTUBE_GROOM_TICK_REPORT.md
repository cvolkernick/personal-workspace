# youtube-groom tick report (#838, not #759)

Log/read only. **Not** a second playlist writer. **Do not** copy
`scripts/youtube_groom.py` (policy scorecard) over the Pi writer.
Do not remint OAuth unless auth is the measured blocker.

#759 was a junk malformed create (`--labels` in the title) and never
received daily listed/add/skip/quota summaries. This path is the
replacement Grok/GVG can read without SSH-grepping `groom.log`.

## Surfaces

Live writer already appends `listed=` / `add=` / `skip=` / `quota=` to
Pi `~/.local/share/youtube-groom/groom.log`. The reader parses those
lines (prefers the ISO append line over the INFO duplicate).

| Path | What |
|------|------|
| **Pi ledger** | `~/.local/share/youtube-groom/tick_report.json` (mode 600) — last tick + 24h rollup + diagnosis + quota headroom |
| **Append-only** | `~/.local/share/youtube-groom/ticks.jsonl` (deduped by `at`) |
| **15m sweep copy** | `scripts/export-day-packets.sh` copies the ledger to `ops/board/youtube_groom_tick_report.json` (gitignored + `workspace_sync.sh` preserve/clean exclude) |
| **After each fire** | `youtube-groom.service` second `ExecStopPost` |

```bash
python3 ~/.local/lib/youtube-groom/youtube_groom_tick_report.py --dry-run --json
```

`--dry-run --json` evaluates and prints; does not persist.

## Diagnosis rule

- listed high + add low + scoring skips (`fit=…`, `throttled-decay`) → **scoring**
- listed itself low vs `HOUSE_TARGET` and skip empty → **supply**
- remain already at house → **filled**

Do **not** crank ticks or seeds without the `quota` block
(`quota_remaining_soft` vs `daily_soft_cap` 8000 / YouTube 10000).
`crank_without_quota` is always false.

## Deploy (Pi, after merge)

1. Copy **only** `scripts/youtube_groom_tick_report.py` →
   `~/.local/lib/youtube-groom/youtube_groom_tick_report.py`
2. Install `scripts/youtube-groom.service` (keep `ExecStart` writer +
   existing health `ExecStopPost`; add tick-report `ExecStopPost`)
3. `systemctl --user daemon-reload` — next hourly fire + next 15m export
4. Do **not** copy nest `scripts/youtube_groom.py` over the writer
5. Do **not** refresh Mac `~/.config/youtube-mcp/` (prod token is Pi)

## #838 snapshot (Pi `groom.log`, 24h ending 2026-09-19T14:00:25Z)

Last tick: `listed=60 add=3 skip={} quota=3690 remain=60 house=100`.

| 24h | Value |
|-----|--------|
| ticks | 25 (hourly) |
| listed | min 58 / max 63 / last 60 |
| remain | min 58 / max 63 / last 60 |
| add | sum 53 / mean ~2.1 / min 1 / max 4 |
| skip | `{}` every tick (`skip_ticks_nonempty=0`) |
| quota | last 3690 / peak 4450 (UTC day roll at 00:00Z) |
| house target | 100 |

**Verdict: supply.** Skip empty since the #815 floors (`MIN_FIT=0`,
`SEED_THROTTLE_WEIGHT_FLOOR=0.10`). Filter is not dropping candidates.
Playlist sits ~60 vs house 100 because seed inventory is ~1–4 new
videos/tick and deletes (dup / dislike / stale_7d) roughly match adds.

Quota headroom at this tick: soft remaining **4310** / 8000; YouTube
remaining **6310** / 10000. Median same-day step ~**226** units/tick.

| Lever | Apply? | Why |
|-------|--------|-----|
| Broaden seed channel set | **yes (next card)** | 6 keepers + 4 throttle. List is cheap; insert 50/video. 4310 soft units left. |
| More ticks/day | **no** | Hourly 24×226 ≈ 5424 fits 8000. 30-min 48×226 ≈ 10848 **exceeds** soft cap. |
| Raise per-tick add cap | **no** | `MAX_INSERTS_PER_TICK` already removed. Add budget is house-after-prune (~40). Observed add 1–4 is inventory, not a cap. |

Auth is not the blocker (ticks succeed hourly; health `healthy` / `ok`).
Do not remint OAuth on this ticket.
