# youtube-groom live caps (Pi)

Writer stays on prism: `~/.local/lib/youtube-groom/youtube_groom.py`  
Nest `scripts/youtube_groom.py` is **policy only** (house caps + insert-budget math).  
**Do not copy the nest file over the Pi binary.** Grok patches the live writer in place.

Hearted 8/31 values stand except the per-tick insert ceiling. Master had no
caps file; these match PR #429’s verified Pi names.

```
MAX_INSERTS_PER_TICK = removed   # was 8 (and 4 before 8/31)
HOUSE_TARGET         = 50        # fill target after prune; not YouTube 5000
FRESH_HOURS          = 168       # was 72; matches STALE_HARD_DAYS (7d)
CAP                  = 200       # was 100 (breaker reason cap_100); not a target
STALE_HARD_DAYS      = 7         # unchanged
MAX_DELETES_PER_TICK = 80        # unchanged
keep_n               = 10        # empty fallback; unchanged
```

Insert budget after prune = `min(slots to HOUSE_TARGET, slots to CAP, remaining playlist slots)`.  
No add/hour clamp. Do not invent `MAX_ADD_PER_DAY`. If the Pi file has a YouTube API quota guard, keep it (nest has not seen one).

Playlist id: `PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At`
