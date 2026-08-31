# youtube-groom live caps (Pi)

Writer stays on prism: `~/.local/lib/youtube-groom/youtube_groom.py`  
Nest has **no** copy of that binary. This note is documentation only.

```
MAX_INSERTS_PER_TICK = 8      # was 4
FRESH_HOURS          = 168    # was 72; now matches STALE_HARD_DAYS (7d)
CAP                  = 200    # was 100 (breaker reason cap_100)
STALE_HARD_DAYS      = 7      # unchanged
MAX_DELETES_PER_TICK = 80     # unchanged
keep_n               = 10     # empty fallback; unchanged
```

Playlist id: `PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At`
