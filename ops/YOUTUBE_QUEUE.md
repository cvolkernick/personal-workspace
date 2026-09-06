# YouTube AI Curated queue

Playlist: **AI Curated** — `PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At`

**One writer:** the Pi hourly `youtube-groom.timer` on prism.  
**Live file:** `~/.local/lib/youtube-groom/youtube_groom.py` (MD5 was `25b0bed0ca8f214f9437af3b9a8cfa9d`).  
That file was **never in nest git**. Nest `scripts/youtube_groom.py` is the policy scorecard only. Do **not** copy any nest `.py` over the Pi file. Do not add a second writer. Do not touch OAuth, Pi systemd, or Mac `~/.config/youtube-mcp`. Do not enable OpenClaw 11:00 cron.

B2 pulls state only (`youtube-groom/state.json`, `never_readd`, `groom.log`).

Constants note: [`YOUTUBE_GROOM_CAPS.md`](YOUTUBE_GROOM_CAPS.md).

`PLANS/YOUTUBE_PLAYLIST_GROOMING.md` does not exist on master or PR #429 — not added.

## Why the playlist sat ~25, then grew slowly

Not a `TARGET_SIZE=25` (that name does not exist on Pi).  
The old 72h fresh cull plus `MAX_INSERTS_PER_TICK` (4, then 8) kept the list small. YouTube’s 5000 ceiling is not the limiter. CAP 200 is a **breaker**, not a fill target. House target ~50 is a **target**.

## Before → after (this change)

| Name | Was (live / hearted 8/31) | Now |
|------|---------------------------|-----|
| `MAX_INSERTS_PER_TICK` | **8** (4 before 8/31) | **removed** — one tick may insert as many as needed to approach house target ~50 after prune |
| house target | ~50 (target, not a YouTube cap) | **50** (same; still a target) |
| `FRESH_HOURS` | **168** | **168** |
| `CAP` | **200** (breaker, was `cap_100`) | **200** (breaker) |
| `STALE_HARD_DAYS` | **7** | **7** |
| `MAX_DELETES_PER_TICK` | 80 | 80 |
| `keep_n` empty fallback | 10 | 10 |

## Policy that stays

- **Prune-first:** dead/private, dups, rated, swipe-off, `STALE_HARD_DAYS=7`
- After prune, insert toward house target ~50
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

## Tests

```bash
python3 -m unittest scripts.tests.test_youtube_groom -v
python3 -m unittest scripts.tests.test_youtube_groom_health -v
```
