#!/usr/bin/env python3
"""YouTube AI Curated playlist — nest source of truth for house caps.

Playlist: AI Curated (`PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At`).
Writer: Pi hourly youtube-groom timer. Copy target after merge:
  ~/.local/lib/youtube-groom/youtube_groom.py

This module is the nest SoT for TARGET/MAX size, per-run add cap, and
stale/dup prune policy. Live YouTube I/O / OAuth stay on the Pi writer;
do not run this as a second writer and do not touch Mac ~/.config/youtube-mcp.

Historical nest path: scripts/youtube_groom.py
Historical MD5 (pre-move / pre-this-change): 25b0bed0ca8f214f9437af3b9a8cfa9d
The file was not in nest git history — runtime lives on the app box.
Grok: merge the HOUSE CAPS block into the Pi copy; do not clobber
search/score/API code if the live file has more than this policy core.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

# ---------------------------------------------------------------------------
# HOUSE CAPS (nest SoT) — old → new
# ---------------------------------------------------------------------------
# Live ticks listed ~21–25 items and added 4/run, so the house TARGET/MAX
# was 25 and the per-run add cap was 4. YouTube's own ceiling (5000) is
# not the limiter. Score is a sort key only — no cutoff that caps size.
#
#   TARGET_SIZE / MAX_PLAYLIST_SIZE : 25 → 50
#   ADD_PER_RUN                     :  4 →  8
#   STALE_DAYS                      :  7 (unchanged; not the size cap)
# ---------------------------------------------------------------------------

PLAYLIST_ID = "PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At"
PLAYLIST_TITLE = "AI Curated"

# Raised: 25 → 50 (double the ~25 live target). If a reviewer finds the
# Pi file already at ≥50, raise that live target to 100 instead.
TARGET_SIZE = 50
MAX_PLAYLIST_SIZE = 50

# Raised: 4 → 8 so the playlist can fill faster without a quota blowup.
ADD_PER_RUN = 8

# 7-day stale prune is NOT the size cap. Leave it.
STALE_DAYS = 7

# Quality sort only. None = no score cutoff (must not silently cap size).
MIN_SCORE: Optional[float] = None

YOUTUBE_PLAYLIST_CEILING = 5000  # platform max; not our limiter

# Documented prior house values (live-tick / hypothesis, confirmed as SoT).
OLD_TARGET_SIZE = 25
OLD_MAX_PLAYLIST_SIZE = 25
OLD_ADD_PER_RUN = 4
OLD_STALE_DAYS = 7

HISTORICAL_NEST_PATH = "scripts/youtube_groom.py"
HISTORICAL_MD5 = "25b0bed0ca8f214f9437af3b9a8cfa9d"
PI_COPY_PATH = "~/.local/lib/youtube-groom/youtube_groom.py"

HOUSE_CAPS = {
    "TARGET_SIZE": TARGET_SIZE,
    "MAX_PLAYLIST_SIZE": MAX_PLAYLIST_SIZE,
    "ADD_PER_RUN": ADD_PER_RUN,
    "STALE_DAYS": STALE_DAYS,
    "MIN_SCORE": MIN_SCORE,
    "PLAYLIST_ID": PLAYLIST_ID,
}

HOUSE_CAPS_OLD = {
    "TARGET_SIZE": OLD_TARGET_SIZE,
    "MAX_PLAYLIST_SIZE": OLD_MAX_PLAYLIST_SIZE,
    "ADD_PER_RUN": OLD_ADD_PER_RUN,
    "STALE_DAYS": OLD_STALE_DAYS,
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
    slots: int
    add_budget: int
    remove_stale: tuple[str, ...] = ()
    remove_dup: tuple[str, ...] = ()
    add: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def slots_to_fill(current_count: int, *, target: int = TARGET_SIZE) -> int:
    """How many videos the playlist still wants under TARGET/MAX."""
    cap = min(target, MAX_PLAYLIST_SIZE, YOUTUBE_PLAYLIST_CEILING)
    return max(0, cap - max(0, current_count))


def add_budget(
    current_count: int,
    *,
    target: int = TARGET_SIZE,
    per_run: int = ADD_PER_RUN,
) -> int:
    """Per-run insert cap, never more than remaining slots."""
    return min(max(0, per_run), slots_to_fill(current_count, target=target))


def is_stale(
    added_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    stale_days: int = STALE_DAYS,
) -> bool:
    if added_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return _aware(now) - _aware(added_at) >= timedelta(days=stale_days)


def passes_score(score: float, *, min_score: Optional[float] = MIN_SCORE) -> bool:
    """Score is a sort key. A None cutoff must not drop candidates."""
    if min_score is None:
        return True
    return score >= min_score


def plan_groom(
    items: Sequence[PlaylistItem],
    candidates: Sequence[Candidate] = (),
    *,
    now: Optional[datetime] = None,
    never_readd: Iterable[str] = (),
    target: int = TARGET_SIZE,
    per_run: int = ADD_PER_RUN,
    stale_days: int = STALE_DAYS,
    min_score: Optional[float] = MIN_SCORE,
) -> GroomPlan:
    """Decide stale/dup removals and this-run inserts. No network."""
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
    slots = slots_to_fill(after, target=target)
    budget = add_budget(after, target=target, per_run=per_run)

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
        if not passes_score(cand.score, min_score=min_score):
            continue
        add_ids.append(cand.video_id)

    notes = (
        f"listed={len(items)} after_prune={after} slots={slots} "
        f"add_budget={budget} target={target} per_run={per_run} "
        f"stale_days={stale_days} min_score={min_score}",
    )
    return GroomPlan(
        listed=len(items),
        after_prune=after,
        slots=slots,
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
        "nest_path": HISTORICAL_NEST_PATH,
        "pi_copy_path": PI_COPY_PATH,
        "historical_md5": HISTORICAL_MD5,
        "old": dict(HOUSE_CAPS_OLD),
        "new": {
            "TARGET_SIZE": TARGET_SIZE,
            "MAX_PLAYLIST_SIZE": MAX_PLAYLIST_SIZE,
            "ADD_PER_RUN": ADD_PER_RUN,
            "STALE_DAYS": STALE_DAYS,
            "MIN_SCORE": MIN_SCORE,
        },
        "youtube_ceiling": YOUTUBE_PLAYLIST_CEILING,
        "score_cutoff_caps_size": False,
        "stale_prune_is_size_cap": False,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print house caps. Does not call YouTube (no second writer)."""
    del argv
    card = scorecard()
    print("youtube_groom house caps (nest SoT)")
    print(f"  playlist: {card['playlist_title']} {card['playlist_id']}")
    print(f"  nest:     {card['nest_path']}")
    print(f"  pi copy:  {card['pi_copy_path']}")
    print("  old → new:")
    old = card["old"]
    new = card["new"]
    for key in ("TARGET_SIZE", "MAX_PLAYLIST_SIZE", "ADD_PER_RUN", "STALE_DAYS"):
        print(f"    {key}: {old[key]} → {new[key]}")
    print(f"    MIN_SCORE: {new['MIN_SCORE']} (sort only; not a size cap)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
