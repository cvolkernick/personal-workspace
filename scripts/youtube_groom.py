#!/usr/bin/env python3
"""YouTube AI Curated — nest policy scorecard (not the live writer).

Playlist: AI Curated (`PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At`).
Live writer (prod): prism `~/.local/lib/youtube-groom/youtube_groom.py`
  hourly `youtube-groom.timer`. That file was never in nest git.

DO NOT copy this module over the Pi binary. PR #429 dropped a fake
reconstructed writer for that reason. Grok/Forge patches the live file
in place. Merge policy only; do not clobber search, score, OAuth, or
API code.

This file is the nest SoT for house caps, insert-budget math, the
#731/#815 add-path floors (`MIN_FIT`, `SEED_THROTTLE_WEIGHT_FLOOR`), the
#788 house fill (`HOUSE_TARGET` 50 → 100), the #852 control-loop band,
and the #957 rebaseline (target/cap 250, band 235–250, stale 30d).
No YouTube I/O. No second writer. No OAuth.
The loop itself lives in `scripts/youtube_groom_control.py`. Discovery,
share cap, and the daily climb live in `scripts/youtube_groom_supply.py`.
Copy those sidecars alongside the Pi writer, never over it.
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
# House target is a fill target, not a YouTube 5000 cap and not CAP.
# #731 (Pi writer 2026-09-14): looser add-path floors for volume.
#   MIN_FIT was 2 (skip fit < 2); then 1 (keep fit ≥ 1).
#   SEED_THROTTLE_WEIGHT_FLOOR was 0.4; then 0.25.
# #788 (Pi writer 2026-09-16): HOUSE_TARGET 50 → 100. CAP 200 unchanged.
#   SEED_UPLOADS_PER_CHANNEL 6 → 50 on the live writer only (YouTube page max).
# #815 (Pi writer 2026-09-18): one more volume notch.
#   MIN_FIT 1 → 0 (disable thesis-fit skip; accept fit≥0).
#   SEED_THROTTLE_WEIGHT_FLOOR 0.25 → 0.10. HOUSE_TARGET/CAP unchanged.
# #852 (control loop): HOUSE_TARGET_TOLERANCE = 10 → band 90–110.
#   Sidecar youtube_groom_control.py notches knobs when outside the band.
# #957: HOUSE_TARGET 100 → 250, CAP 200 → 250, tolerance 15.
#   Band is 235–250 (high side clamped to CAP; never exceed 250).
#   STALE_HARD_DAYS 7 → 30 and FRESH_HOURS 168 → 720 so the inner
#   fresh prune cannot immediately delete the list back to 7 days.
#   SEED_THROTTLE_WEIGHT_FLOOR 0.10 → 0.00 at baseline (not only as a
#   below-band notch). WEIGHT_FLOOR 0.15 → 0.05.
#   SEED_UPLOADS_PER_CHANNEL stays 50 (YouTube page max).
#   CLIMB_INSERTS_PER_DAY 40 spreads the fill. That is not MAX_ADD_PER_DAY.
# ---------------------------------------------------------------------------

PLAYLIST_ID = "PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At"
PLAYLIST_TITLE = "AI Curated"
PI_WRITER_PATH = "~/.local/lib/youtube-groom/youtube_groom.py"
NEST_PATH = "scripts/youtube_groom.py"
HISTORICAL_MD5 = "25b0bed0ca8f214f9437af3b9a8cfa9d"

# Fill-to after prune. #957 target. Not a YouTube 5000 cap.
HOUSE_TARGET = 250
PREV_HOUSE_TARGET = 100  # #788/#852
OLD_HOUSE_TARGET = 50
# High side is clamped to CAP so the band cannot sit above 250.
HOUSE_TARGET_TOLERANCE = 15
PREV_HOUSE_TARGET_TOLERANCE = 10
# Breaker and hard max. #957 never exceeds 250. Was 200.
CAP = 250
PREV_CAP = 200
BAND_LOW = HOUSE_TARGET - HOUSE_TARGET_TOLERANCE  # 235
BAND_HIGH = min(HOUSE_TARGET + HOUSE_TARGET_TOLERANCE, CAP)  # 250

FRESH_HOURS = 720  # was 168; matches STALE_HARD_DAYS (30d)
PREV_FRESH_HOURS = 168
STALE_HARD_DAYS = 30  # was 7
PREV_STALE_HARD_DAYS = 7
MAX_DELETES_PER_TICK = 80
KEEP_N = 10  # empty-playlist prune fallback on Pi (`keep_n`)

# Add-path floors on the Pi writer (#731, #815, then #957 baseline).
MIN_FIT = 0  # thesis-fit skip disabled; accept fit≥0 (was 1)
OLD_MIN_FIT = 1
ORIG_MIN_FIT = 2  # pre-#731 skip fit < 2
SEED_THROTTLE_WEIGHT_FLOOR = 0.0  # #957 baseline; throttle skip off
PREV_SEED_THROTTLE_WEIGHT_FLOOR = 0.10  # #815
OLD_SEED_THROTTLE_WEIGHT_FLOOR = 0.25
ORIG_SEED_THROTTLE_WEIGHT_FLOOR = 0.4  # pre-#731
WEIGHT_FLOOR = 0.05  # was 0.15 on the live writer
PREV_WEIGHT_FLOOR = 0.15
SEED_UPLOADS_PER_CHANNEL = 50  # unchanged; YouTube page max
# Gradual fill. Distinct from the removed per-tick ceiling and from
# MAX_ADD_PER_DAY, which stays unset.
CLIMB_INSERTS_PER_DAY = 40
CHANNEL_SHARE_MIN = 12
CHANNEL_SHARE_FRACTION = 0.05
DISCOVERY_MIX_TARGET = 0.40
MAX_SEARCHES_PER_TICK = 4

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

# Extra seed ladder for the #852 control loop (inspect 2026-08-14 channels
# that were already on AI Curated and are not in the live 6+4 seed set).
# Channel lists still stay on the Pi writer; the sidecar writes knobs.json.
EXTRA_SEED_LADDER = (
    ("UCtvg5cXLY_tHDJeBoRySBtg", "What Bitcoin Did"),
    ("UCCpNQKYvrnWQNjZprabMJlw", "Peter H. Diamandis"),
    ("UCYXLs8tkNQrENrT1s60rxCw", "Anthony Pompliano"),
    ("UCk6EGp5yqsB-YtBE3AF8dWw", "Bitcoin Magazine"),
    ("UCfs-Vb0DOIZNN0xKyfz-svg", "Natalie Brunell"),
    ("UCPcO_WZXKQa1lFwCGltWc8A", "Brent Johnson Milkshakes Pod"),
)

HOUSE_CAPS = {
    "HOUSE_TARGET": HOUSE_TARGET,
    "HOUSE_TARGET_TOLERANCE": HOUSE_TARGET_TOLERANCE,
    "BAND_LOW": BAND_LOW,
    "BAND_HIGH": BAND_HIGH,
    "CAP": CAP,
    "FRESH_HOURS": FRESH_HOURS,
    "STALE_HARD_DAYS": STALE_HARD_DAYS,
    "MAX_DELETES_PER_TICK": MAX_DELETES_PER_TICK,
    "KEEP_N": KEEP_N,
    "MAX_INSERTS_PER_TICK": None,
    "MAX_ADD_PER_DAY": MAX_ADD_PER_DAY,
    "MIN_FIT": MIN_FIT,
    "SEED_THROTTLE_WEIGHT_FLOOR": SEED_THROTTLE_WEIGHT_FLOOR,
    "WEIGHT_FLOOR": WEIGHT_FLOOR,
    "CLIMB_INSERTS_PER_DAY": CLIMB_INSERTS_PER_DAY,
    "CHANNEL_SHARE_MIN": CHANNEL_SHARE_MIN,
    "DISCOVERY_MIX_TARGET": DISCOVERY_MIX_TARGET,
    "SEED_UPLOADS_PER_CHANNEL": SEED_UPLOADS_PER_CHANNEL,
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


def skip_add_reason(
    *,
    fit: int,
    channel_weight: float,
    is_seed_throttle: bool,
    min_fit: int = MIN_FIT,
    throttle_floor: float = SEED_THROTTLE_WEIGHT_FLOOR,
) -> Optional[str]:
    """Pi add-path skip reason. Policy only — seed channel ids stay on the writer."""
    if fit < min_fit:
        return f"fit={fit}"
    if is_seed_throttle and channel_weight < throttle_floor:
        return "throttled-decay"
    return None


def band_edges(
    *,
    house: int = HOUSE_TARGET,
    tolerance: int = HOUSE_TARGET_TOLERANCE,
    cap: int = CAP,
) -> tuple[int, int]:
    """Low is house − tolerance. High never exceeds CAP."""
    low = house - tolerance
    high = min(house + tolerance, cap)
    return low, high


def channel_share_cap(
    playlist_count: int,
    *,
    minimum: int = CHANNEL_SHARE_MIN,
    fraction: float = CHANNEL_SHARE_FRACTION,
) -> int:
    """max(12, ceil(5% of the playlist)). At 250 the cap is 13."""
    count = max(0, int(playlist_count))
    if count == 0:
        return minimum
    numer = int(round(fraction * 100))
    pct = (count * numer + 99) // 100
    return max(minimum, pct)


def band_side(
    count: int,
    *,
    house: int = HOUSE_TARGET,
    tolerance: int = HOUSE_TARGET_TOLERANCE,
    cap: int = CAP,
) -> str:
    """below / inside / above the band. High side is clamped to CAP."""
    low, high = band_edges(house=house, tolerance=tolerance, cap=cap)
    if count < low:
        return "below"
    if count > high:
        return "above"
    return "inside"


def distance_from_band(
    count: int,
    *,
    house: int = HOUSE_TARGET,
    tolerance: int = HOUSE_TARGET_TOLERANCE,
    cap: int = CAP,
) -> int:
    """How far the playlist count sits outside the band. 0 if inside."""
    low, high = band_edges(house=house, tolerance=tolerance, cap=cap)
    if count < low:
        return low - count
    if count > high:
        return count - high
    return 0


def net_new(added: int, removed: int) -> int:
    """added − removed/expired for one tick."""
    return int(added) - int(removed)


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

    Stopped by (1) remaining slots to the house target, (2) the CAP 250
    breaker, (3) remaining slots in the playlist (YouTube 5000).
    The live writer also applies CLIMB_INSERTS_PER_DAY on top of this.
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
            "MIN_FIT": OLD_MIN_FIT,
            "SEED_THROTTLE_WEIGHT_FLOOR": OLD_SEED_THROTTLE_WEIGHT_FLOOR,
            "HOUSE_TARGET": OLD_HOUSE_TARGET,
        },
        "previous": {
            "HOUSE_TARGET": PREV_HOUSE_TARGET,
            "CAP": PREV_CAP,
            "FRESH_HOURS": PREV_FRESH_HOURS,
            "STALE_HARD_DAYS": PREV_STALE_HARD_DAYS,
            "MIN_FIT": MIN_FIT,
            "SEED_THROTTLE_WEIGHT_FLOOR": PREV_SEED_THROTTLE_WEIGHT_FLOOR,
            "WEIGHT_FLOOR": PREV_WEIGHT_FLOOR,
            "HOUSE_TARGET_TOLERANCE": PREV_HOUSE_TARGET_TOLERANCE,
            "BAND_LOW": PREV_HOUSE_TARGET - PREV_HOUSE_TARGET_TOLERANCE,
            "BAND_HIGH": PREV_HOUSE_TARGET + PREV_HOUSE_TARGET_TOLERANCE,
            "SEED_UPLOADS_PER_CHANNEL": SEED_UPLOADS_PER_CHANNEL,
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
            "MIN_FIT": MIN_FIT,
            "SEED_THROTTLE_WEIGHT_FLOOR": SEED_THROTTLE_WEIGHT_FLOOR,
            "WEIGHT_FLOOR": WEIGHT_FLOOR,
            "HOUSE_TARGET_TOLERANCE": HOUSE_TARGET_TOLERANCE,
            "BAND_LOW": BAND_LOW,
            "BAND_HIGH": BAND_HIGH,
            "CLIMB_INSERTS_PER_DAY": CLIMB_INSERTS_PER_DAY,
            "SEED_UPLOADS_PER_CHANNEL": SEED_UPLOADS_PER_CHANNEL,
        },
        "youtube_ceiling": YOUTUBE_PLAYLIST_CEILING,
        "cap_is_breaker": True,
        "house_target_is_youtube_5000": False,
        "quota_guard_in_nest": QUOTA_GUARD_IN_NEST,
        "control_loop": {
            "issue": 852,
            "rebased_by": 957,
            "sidecar": "scripts/youtube_groom_control.py",
            "supply": "scripts/youtube_groom_supply.py",
            "band": f"{BAND_LOW}–{BAND_HIGH}",
            "extra_seed_count": len(EXTRA_SEED_LADDER),
            "copy_over_pi": False,
        },
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
    prev = card["previous"]
    print(
        f"    HOUSE_TARGET:         {new['HOUSE_TARGET']} "
        f"(was {prev['HOUSE_TARGET']}; target, not YouTube 5000)"
    )
    print(f"    CAP:                  {new['CAP']} (hard max; was {prev['CAP']})")
    print(f"    FRESH_HOURS:          {new['FRESH_HOURS']} (was {prev['FRESH_HOURS']})")
    print(f"    STALE_HARD_DAYS:      {new['STALE_HARD_DAYS']} (was {prev['STALE_HARD_DAYS']})")
    print(f"    MAX_DELETES_PER_TICK: {new['MAX_DELETES_PER_TICK']}")
    print(f"    KEEP_N:               {new['KEEP_N']}")
    print("    MAX_ADD_PER_DAY:      not invented")
    print(f"    CLIMB_INSERTS_PER_DAY: {new['CLIMB_INSERTS_PER_DAY']}")
    print(f"    MIN_FIT:              {new['MIN_FIT']} (was {card['old']['MIN_FIT']})")
    print(
        f"    SEED_THROTTLE_WEIGHT_FLOOR: {new['SEED_THROTTLE_WEIGHT_FLOOR']} "
        f"(was {prev['SEED_THROTTLE_WEIGHT_FLOOR']})"
    )
    print(
        f"    WEIGHT_FLOOR:         {new['WEIGHT_FLOOR']} "
        f"(was {prev['WEIGHT_FLOOR']})"
    )
    print(
        f"    HOUSE_TARGET_TOLERANCE: {new['HOUSE_TARGET_TOLERANCE']} "
        f"(band {new['BAND_LOW']}–{new['BAND_HIGH']}; #957, was 90–110)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
