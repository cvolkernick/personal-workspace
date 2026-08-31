# YouTube AI Curated queue (house caps)

Playlist: **AI Curated** — `PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At`  
Writer: Pi hourly youtube-groom timer (one writer).  
Nest SoT: [`scripts/youtube_groom.py`](../scripts/youtube_groom.py)  
Pi copy after merge: `~/.local/lib/youtube-groom/youtube_groom.py` (Grok copies; do not SSH from cloud).

Historical nest path: `scripts/youtube_groom.py`  
Historical MD5 (pre-this change): `25b0bed0ca8f214f9437af3b9a8cfa9d`

The live writer was not in nest git history (app-box runtime; B2 pulls `state.json` / `never_readd` / `groom.log` only). This file + the script are the nest scorecard for house caps.

## Old → new constants

| Cap | Old | New | Role |
|-----|-----|-----|------|
| `TARGET_SIZE` | **25** | **50** | Playlist fill target |
| `MAX_PLAYLIST_SIZE` | **25** | **50** | Hard house max (same as target) |
| `ADD_PER_RUN` | **4** | **8** | Per-tick insert budget |
| `STALE_DAYS` | **7** | **7** | Stale prune; **not** the size cap |
| `MIN_SCORE` | none | none | Sort only — no cutoff that caps size |
| YouTube platform ceiling | 5000 | 5000 | Not the limiter |

Old 25 / 4 came from live ticks that listed ~21–25 items and added 4 per run. Nest git never held those literals; they are the verified house sizes this change doubles.

If a reviewer finds the **Pi** file already at `TARGET_SIZE >= 50`, raise **that** live target to **100** instead. Nest used 25 → 50 because the live list hovered under 25.

## Left alone

- 7-day stale prune (not why the playlist stayed small)
- Dup prune / `never_readd`
- OAuth, Pi systemd units, Mac `~/.config/youtube-mcp`
- A second writer

## Tests

```bash
python3 -m unittest scripts.tests.test_youtube_groom -v
```
