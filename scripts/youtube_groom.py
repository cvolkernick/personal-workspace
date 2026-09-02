#!/usr/bin/env python3
"""YouTube AI Curated — nest policy scorecard (not the live writer).

Playlist: AI Curated (`PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At`).
Live writer (prod): prism `~/.local/lib/youtube-groom/youtube_groom.py`
  hourly `youtube-groom.timer`. That file was never in nest git.

DO NOT copy this module over the Pi binary. PR #429 dropped a fake
reconstructed writer for that reason. Grok/Forge patches the live file
in place: remove `MAX_INSERTS_PER_TICK` (last live value 8) and any
equivalent add/hour clamp. Merge policy only; do not clobber search,
score, OAuth, or API code.

This file is the nest SoT for house caps + insert-budget math so tests
and ops stay aligned. No YouTube I/O. No second writer. No OAuth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

# ---------------------------------------------------------------------------
# HOUSE CAPS — live Pi names (hearted 8/31), minus the per-tick insert ceiling
# ---------------------------------------------------------------------------
# Master had none of these files. PR #429 (docs-only draft) verified:
#   MAX_INSERTS_PER_TICK = 8   (was 4)  ← removed this change
#   FRESH_HOURS          = 168 (was 72)
#   CAP                  = 200 (was 100, breaker reason cap_100)
#   STALE_HARD_DAYS      = 7
#   MAX_DELETES_PER_TICK = 80
#   keep_n               = 10  (empty fallback)
# House target ~50 is a fill target, not a YouTube 5000 cap and not CAP.
# ---------------------------------------------------------------------------

PLAYLIST_ID = "PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At"
PLAYLIST_TITLE = "AI Curated"
PI_WRITER_PATH = "~/.local/lib/youtube-groom/youtube_groom.py"
NEST_PATH = "scripts/youtube_groom.py"
HISTORICAL_MD5 = "25b0bed0ca8f214f9437af3b9a8cfa9d"

# Fill-to after prune. Target, not a hard playlist max.
HOUSE_TARGET = 50

# Breaker, not a fill target. Live reason was cap_100; now CAP 200.
CAP = 200

FRESH_HOURS = 168  # was 72; aligns with STALE_HARD_DAYS (7d)
STALE_HARD_DAYS = 7
MAX_DELETES_PER_TICK = 80
KEEP_N = 10  # empty-playlist prune fallback on Pi (`keep_n`)

# YouTube platform ceiling — not our limiter.
YOUTUBE_PLAYLIST_CEILING = 5000

# Last live per-tick insert ceiling (Pi). Removed: a tick may insert as
# many as needed to approach HOUSE_TARGET after prune.
OLD_MAX_INSERTS_PER_TICK = 8
OLD_MAX_INSERTS_PER_TICK_PRE_831 = 4

# Do not invent these. If the Pi file still has MAX_INSERTS_PER_TICK or
# an add/hour clamp, delete that clamp. If a YouTube API quota guard
# exists on Pi, keep it — nest has not seen one.
MAX_ADD_PER_DAY = None
QUOTA_GUARD_IN_NEST = None

HOUSE_CAPS = {
    "HOUSE_TARGET": HOUSE_TARGET,
    "CAP": CAP,
    "FRESH_HOURS": FRESH_HOURS,
    "STALE_HARD_DAYS": STALE_HARD_DAYS,
    "MAX_DELETES_PER_TICK": MAX_DELETES_PER_TICK,
    "KEEP_N": KEEP_N,
    "MAX_INSERTS_PER_TICK": None,
    "MAX_ADD_PER_DAY": MAX_ADD_PER_DAY,
}


@dataclass(frozen=True)
class PlaylistItem:
    video_id: str
    added_at: Optional[datetime] = None
    title: str = ""
    playlist_item_id: str = ""


@dataclass(frozen=True)
class Candidate:
    video_id: str
    score: float = 0.0
    title: str = ""


@dataclass(frozen=True)
class GroomPlan:
    listed: int
    after_prune: int
    slots_to_target: int
    add_budget: int
    remove_stale: tuple[str, ...] = ()
    remove_dup: tuple[str, ...] = ()
    add: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def slots_to_house_target(after_prune: int, *, target: int = HOUSE_TARGET) -> int:
    """How many inserts still approach the house target after prune."""
    return max(0, target - max(0, after_prune))


def insert_budget(
    after_prune: int,
    *,
    house_target: int = HOUSE_TARGET,
    cap: int = CAP,
    playlist_len: Optional[int] = None,
    playlist_ceiling: int = YOUTUBE_PLAYLIST_CEILING,
) -> int:
    """This-tick insert count. No per-tick insert ceiling.

    Stopped by (1) remaining slots to the house target, (2) the CAP 200
    breaker, (3) remaining slots in the playlist (YouTube 5000).
    Does not invent MAX_ADD_PER_DAY. Does not apply OLD_MAX_INSERTS_PER_TICK.
    """
    after = max(0, after_prune)
    to_target = max(0, house_target - after)
    to_cap = max(0, cap - after)
    listed = after if playlist_len is None else max(0, playlist_len)
    to_playlist = max(0, playlist_ceiling - listed)
    return min(to_target, to_cap, to_playlist)


def is_stale(
    added_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    stale_days: int = STALE_HARD_DAYS,
) -> bool:
    if added_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return _aware(now) - _aware(added_at) >= timedelta(days=stale_days)


def plan_groom(
    items: Sequence[PlaylistItem],
    candidates: Sequence[Candidate] = (),
    *,
    now: Optional[datetime] = None,
    never_readd: Iterable[str] = (),
    house_target: int = HOUSE_TARGET,
    cap: int = CAP,
    stale_days: int = STALE_HARD_DAYS,
) -> GroomPlan:
    """Prune-first (dups, stale), then fill toward the house target.

    Policy only — no network. Dead/private/rated/swipe-off stay on the
    Pi writer. This models the nest insert-budget rule.
    """
    now = now or datetime.now(timezone.utc)
    blocked = {v for v in never_readd if v}

    seen: set[str] = set()
    keep: list[PlaylistItem] = []
    stale_ids: list[str] = []
    dup_ids: list[str] = []

    for item in items:
        vid = item.video_id
        if not vid:
            continue
        if vid in seen:
            dup_ids.append(vid)
            continue
        seen.add(vid)
        if is_stale(item.added_at, now=now, stale_days=stale_days):
            stale_ids.append(vid)
            continue
        keep.append(item)

    after = len(keep)
    slots = slots_to_house_target(after, target=house_target)
    budget = insert_budget(after, house_target=house_target, cap=cap)

    keep_ids = {i.video_id for i in keep}
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    add_ids: list[str] = []
    for cand in ranked:
        if len(add_ids) >= budget:
            break
        if cand.video_id in keep_ids or cand.video_id in blocked:
            continue
        if cand.video_id in add_ids:
            continue
        add_ids.append(cand.video_id)

    notes = (
        f"listed={len(items)} after_prune={after} slots_to_target={slots} "
        f"add_budget={budget} house_target={house_target} cap={cap} "
        f"stale_days={stale_days} max_inserts_per_tick=None",
    )
    return GroomPlan(
        listed=len(items),
        after_prune=after,
        slots_to_target=slots,
        add_budget=budget,
        remove_stale=tuple(stale_ids),
        remove_dup=tuple(dup_ids),
        add=tuple(add_ids),
        notes=notes,
    )


def scorecard() -> dict[str, object]:
    """Documented constants for tests / ops / PR review."""
    return {
        "playlist_id": PLAYLIST_ID,
        "playlist_title": PLAYLIST_TITLE,
        "nest_path": NEST_PATH,
        "pi_writer_path": PI_WRITER_PATH,
        "historical_md5": HISTORICAL_MD5,
        "copy_over_pi": False,
        "old": {
            "MAX_INSERTS_PER_TICK": OLD_MAX_INSERTS_PER_TICK,
            "MAX_INSERTS_PER_TICK_PRE_831": OLD_MAX_INSERTS_PER_TICK_PRE_831,
            "FRESH_HOURS": 72,
            "CAP": 100,
            "STALE_HARD_DAYS": 7,
        },
        "new": {
            "MAX_INSERTS_PER_TICK": None,
            "HOUSE_TARGET": HOUSE_TARGET,
            "CAP": CAP,
            "FRESH_HOURS": FRESH_HOURS,
            "STALE_HARD_DAYS": STALE_HARD_DAYS,
            "MAX_DELETES_PER_TICK": MAX_DELETES_PER_TICK,
            "KEEP_N": KEEP_N,
            "MAX_ADD_PER_DAY": MAX_ADD_PER_DAY,
        },
        "youtube_ceiling": YOUTUBE_PLAYLIST_CEILING,
        "cap_is_breaker": True,
        "house_target_is_youtube_5000": False,
        "quota_guard_in_nest": QUOTA_GUARD_IN_NEST,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print house caps. Does not call YouTube (no second writer)."""
    del argv
    card = scorecard()
    print("youtube_groom nest policy (do not copy over Pi writer)")
    print(f"  playlist: {card['playlist_title']} {card['playlist_id']}")
    print(f"  nest:     {card['nest_path']}")
    print(f"  pi file:  {card['pi_writer_path']}")
    print("  insert cap: MAX_INSERTS_PER_TICK 8 → removed")
    print("  otherwise (hearted 8/31):")
    new = card["new"]
    print(f"    HOUSE_TARGET:         {new['HOUSE_TARGET']} (target, not YouTube 5000)")
    print(f"    CAP:                  {new['CAP']} (breaker)")
    print(f"    FRESH_HOURS:          {new['FRESH_HOURS']}")
    print(f"    STALE_HARD_DAYS:      {new['STALE_HARD_DAYS']}")
    print(f"    MAX_DELETES_PER_TICK: {new['MAX_DELETES_PER_TICK']}")
    print(f"    KEEP_N:               {new['KEEP_N']}")
    print("    MAX_ADD_PER_DAY:      not invented")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
