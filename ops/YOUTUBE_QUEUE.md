# YouTube AI Curated queue

Playlist: **AI Curated** — `PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At`

**One writer:** the Pi hourly youtube-groom timer on prism.  
**Live file:** `~/.local/lib/youtube-groom/youtube_groom.py` (MD5 was `25b0bed0ca8f214f9437af3b9a8cfa9d`).  
That file was **never in nest git**. Nest does **not** ship a writer. Do not copy any nest `.py` over the Pi file. Do not add a second writer. Do not touch OAuth, Pi systemd, or Mac `~/.config/youtube-mcp`.

B2 pulls state only (`youtube-groom/state.json`, `never_readd`, `groom.log`).

Constants note: [`YOUTUBE_GROOM_CAPS.md`](YOUTUBE_GROOM_CAPS.md).

## Why the playlist sat ~25

Not a `TARGET_SIZE=25` (that name does not exist on Pi).  
The 72h fresh cull plus `MAX_INSERTS_PER_TICK=4` kept the list small. YouTube’s 5000 ceiling is not the limiter.

## Live names (verified on Pi) — Grok is patching these

| Name | Was | Now (Pi patch) |
|------|-----|----------------|
| `MAX_INSERTS_PER_TICK` | 4 | **8** |
| `FRESH_HOURS` | 72 | **168** (align with 7d hard stale so 72h stops emptying the list) |
| `CAP` | 100 (breaker, `reason cap_100`) | **200** |
| `STALE_HARD_DAYS` | 7 | **7** (unchanged) |
| `MAX_DELETES_PER_TICK` | 80 | 80 (unchanged) |
| `keep_n` empty fallback | 10 | **10** (unchanged) |

## Left alone

- `STALE_HARD_DAYS=7`
- `keep_n=10` empty fallback
- Dup / `never_readd` prune
- OAuth, systemd units, youtube-mcp
- A second writer
