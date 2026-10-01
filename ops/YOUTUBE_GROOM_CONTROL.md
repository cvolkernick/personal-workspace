# youtube-groom control loop (#852)

Hold **AI Curated** in the **235–250** band (#957). That rebases the #852
loop, which held **100 ± 10** (band **90–110**). High side is CAP 250.
The loop never sets a cap above 250. `HOUSE_TARGET_TOLERANCE` is 15.
Sidecar, not a second playlist writer. Do not copy
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

- Inside 235–250 → rest.
- Below 235 → **one** loosen notch per tick (bias: more skippable fresh content).
  #957 already sets the throttle floor to 0.00, so the first notch is an
  extra seed unless a saved knob still has a higher floor.
  Order: ease `MIN_FIT` → lower `SEED_THROTTLE_WEIGHT_FLOOR` (0.10→0.05→0.00)
  → broaden `SEED_EXTRA` (one inspect-list channel) → raise add cap (already
  removed on live) → `ticks_per_day` 24→48.
- Above 250 → **one** tighten notch per tick. First: prune-to-band (`CAP` → 250).
- Quota headroom check runs **before** any seeds / ticks / caps increase.
  `crank_without_quota` is always false. Quota exhaustion is the only
  acceptable stall (logged as `blocker=quota_exhausted`).
- Anti-oscillation: `COOLDOWN_TICKS=2` so loosen→tighten cannot flip on
  consecutive ticks.
- Same `last_tick.at` is idempotent (15m export must not notch twice).

#852 (2026-09-20) started with `MIN_FIT` already 0 and skip `{}`. Its first
effective notches were throttle-floor then extra seeds (What Bitcoin Did,
Diamandis, Pompliano, Bitcoin Magazine, Natalie Brunell, Milkshakes Pod —
inspect 2026-08-14 channels not in the live 6+4 set). #957 sets the floor
to 0.00, so a fresh schema-2 state notches `SEED_EXTRA` first.

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
(`OnCalendar=hourly` or `*:0/30`) when the desired `ticks_per_day`
does not match the drop-in. Tick-report StopPost runs the loop first
(`apply_timer_changes=False`) and is idempotent on `last_tick.at`; the
third StopPost `--apply-timer` still writes the drop-in for that same
tick. Never points `ExecStart` at the nest scorecard.

## Deploy (Pi, after merge)

1. Copy `scripts/youtube_groom_control.py`, `scripts/youtube_groom_supply.py`,
   and `scripts/youtube_groom_tick_report.py` alongside the writer under
   `~/.local/lib/youtube-groom/`. Do **not** copy nest `youtube_groom.py`.
2. Run
   `python3 ~/.local/lib/youtube-groom/youtube_groom_supply.py --patch-writer ~/.local/lib/youtube-groom/youtube_groom.py`.
   That backs up `youtube_groom.py.bak-YYYYMMDD-957` and edits the writer in
   place (`apply_live_knobs`, widened lenses, budgeted `supply_tick`).
   Schema-1 `knobs.json` does not overlay the new house/cap. The next control
   tick writes schema 2.
3. Keep the existing `youtube-groom.service` (`ExecStart` stays the Pi writer;
   health, tick-report, and control `--apply-timer` StopPosts stay).
4. `systemctl --user daemon-reload` — next hourly fire + next 15m export.
5. Do **not** refresh Mac `~/.config/youtube-mcp/` (prod token is Pi).
6. Do **not** run `deploy/install_remote.sh`.

## Tests

```bash
python3 -m unittest scripts.tests.test_youtube_groom_control -v
```
