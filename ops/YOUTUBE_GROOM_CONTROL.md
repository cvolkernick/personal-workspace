# youtube-groom control loop (#852)

Hold **AI Curated** at `HOUSE_TARGET ± HOUSE_TARGET_TOLERANCE` = **100 ± 10**
(band **90–110**). Sidecar, not a second playlist writer. Do not copy
`scripts/youtube_groom.py` (policy scorecard) over the Pi writer.
Do not remint OAuth. Do not run `deploy/install_remote.sh`.

#838 restored per-tick listed/add/skip/quota and diagnosed **supply**
(playlist ~59 vs house 100, skip empty). This loop closes the criteria
when the count sits outside the band.

## Surfaces

| Path | What |
|------|------|
| **Pi state** | `~/.local/share/youtube-groom/control_state.json` (mode 600) |
| **Next-tick knobs** | `~/.local/share/youtube-groom/knobs.json` (mode 600) — live writer loads via `apply_live_knobs` |
| **Tick report** | merged `control` block on `tick_report.json` (playlist_count, net_new, distance_from_band, adjustment + why) |
| **Append-only** | `~/.local/share/youtube-groom/control.jsonl` |
| **After each fire** | `youtube-groom.service` third `ExecStopPost` (`youtube_groom_control.py --apply-timer`) |

```bash
python3 ~/.local/lib/youtube-groom/youtube_groom_control.py --dry-run --json
```

`--dry-run --json` evaluates and prints; does not persist; does not touch the timer.

## Band + notches

- Inside 90–110 → rest.
- Below 90 → **one** loosen notch per tick (bias: more skippable fresh content).
  Order: ease `MIN_FIT` → lower `SEED_THROTTLE_WEIGHT_FLOOR` (0.10→0.05→0.00)
  → broaden `SEED_EXTRA` (one inspect-list channel) → raise add cap (already
  removed on live) → `ticks_per_day` 24→48.
- Above 110 → **one** tighten notch per tick. First: prune-to-band (`CAP` → 110).
- Quota headroom check runs **before** any seeds / ticks / caps increase.
  `crank_without_quota` is always false. Quota exhaustion is the only
  acceptable stall (logged as `blocker=quota_exhausted`).
- Anti-oscillation: `COOLDOWN_TICKS=2` so loosen→tighten cannot flip on
  consecutive ticks.
- Same `last_tick.at` is idempotent (15m export must not notch twice).

Live 2026-09-20: `MIN_FIT` already 0, skip `{}`, net_new ≈ 0. First effective
notches are throttle-floor then extra seeds (What Bitcoin Did, Diamandis,
Pompliano, Bitcoin Magazine, Natalie Brunell, Milkshakes Pod — inspect
2026-08-14 channels not in the live 6+4 set).

## Live writer hook (surgical, not nest-copy)

After overlaying `youtube_groom_control.py` alongside the writer, add **only**
this at the top of `groom()` (or after the constants) on the Pi file:

```python
try:
    from youtube_groom_control import apply_live_knobs
    apply_live_knobs(globals())
except Exception:
    pass
```

`apply_live_knobs` reads `knobs.json` and overlays `MIN_FIT`,
`SEED_THROTTLE_WEIGHT_FLOOR`, `HOUSE_TARGET`, `CAP`, and merges `SEED_EXTRA`
into `SEED_KEEPERS`. It does not call YouTube.

`--apply-timer` writes
`~/.config/systemd/user/youtube-groom.timer.d/control.conf`
(`OnCalendar=hourly` or `*:0/30`) only when `ticks_per_day` actually changes.
Never points `ExecStart` at the nest scorecard.

## Deploy (Pi, after merge)

1. Copy **only** `scripts/youtube_groom_control.py` →
   `~/.local/lib/youtube-groom/youtube_groom_control.py`
2. Copy updated `scripts/youtube_groom_tick_report.py` (merges the control
   block into `tick_report.json`). Do **not** copy nest `youtube_groom.py`.
3. Install `scripts/youtube-groom.service` (keep `ExecStart` writer + health +
   tick-report StopPosts; add control `--apply-timer` StopPost).
4. Surgical `apply_live_knobs` hook on the live writer. Backup first
   (`youtube_groom.py.bak-YYYYMMDD-852`).
5. `systemctl --user daemon-reload` — next hourly fire + next 15m export
6. Do **not** refresh Mac `~/.config/youtube-mcp/` (prod token is Pi)
7. Do **not** run `deploy/install_remote.sh`

## Tests

```bash
python3 -m unittest scripts.tests.test_youtube_groom_control -v
```
