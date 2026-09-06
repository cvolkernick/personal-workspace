# youtube-groom health / alert landing (#480)

Log/alert only. **Not** a second playlist writer. **Do not** copy
`scripts/youtube_groom.py` (policy scorecard) over the Pi writer.

## Detect

Pi oneshot `youtube-groom.timer` → `~/.local/lib/youtube-groom/youtube_groom.py`.
Tick SoT: `~/.local/share/youtube-groom/groom.log`.

Unhealthy when:

- no successful `listed=` / INFO completion in that log within **2h**, or
- last tick contains `RefreshError` / `invalid_grant` / uncaught (`groom failed` / traceback)

Last tick is **scan order** (later line in `groom.log`), not a wall-clock compare.
Success is raw ISO UTC (`listed=` append); failures are logging `asctime` (host
local, no TZ). On prism (`America/New_York`) those clocks must not hide a later
`invalid_grant`.

Checker: `scripts/youtube_groom_health.py` (copy to Pi
`~/.local/lib/youtube-groom/youtube_groom_health.py` — **alongside** the writer,
never replacing it).

```bash
python3 ~/.local/lib/youtube-groom/youtube_groom_health.py --dry-run --json
```

`--dry-run --json` evaluates and prints; does not persist or post. Assay can
score with groom.log excerpts + this JSON (failed tick → alert → successful tick).

## Alert landing

| Path | What |
|------|------|
| **Thin #workflow message** | On **broken** transition, **recovery**, and optional **daily reminder** if still broken >24h. Mentions **Grok only**. Clock identity (`ceremony_clock.send_channel_message`). **Not** a Chris DM from the Pi. |
| **Durable ledger (Pi)** | `~/.local/share/youtube-groom/health.json` (mode 600) |
| **15m sweep copy** | `scripts/export-day-packets.sh` (board-day-export, 15m) runs the checker and copies the ledger to `ops/board/youtube_groom_health.json` — same tree Grok’s 15m eng-gate already reads (`ops/board/`). Gitignored + `workspace_sync.sh` `git clean` / `preserve_durable` exclude (same as `day_constraints.json`) so the 15m copy is not deleted on the ~5m sync. |
| **After each fire** | `youtube-groom.service` `ExecStopPost` (success or fail) |

Dedup: one alert on broken, one on recovery; no hourly spam. Healthy ticks produce no message.

Channel: `#workflow` `db0e8f97-0c81-4976-b299-1c460b87134e`.

## Deploy (Pi, after merge — not this PR)

1. Copy **only** `scripts/youtube_groom_health.py` → `~/.local/lib/youtube-groom/youtube_groom_health.py`
2. Install `scripts/youtube-groom.service` `ExecStopPost` (keep existing `ExecStart` writer)
3. `systemctl --user daemon-reload` — next hourly fire + next 15m export pick it up
4. Do **not** dual-run a second writer against `PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At`
5. Do **not** refresh Mac `~/.config/youtube-mcp/` (prod token is Pi)
6. Interim YouTube groom health watch stays until this ships — do not double-alert Chris

Caps / prune / OAuth: unchanged. See `YOUTUBE_GROOM_CAPS.md`.
