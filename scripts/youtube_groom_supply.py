#!/usr/bin/env python3
"""#957 supply lane for the AI Curated writer. Not a second writer.

Discovery search, the non-subscribed mix, and the per-channel share cap
live here so they can be tested without YouTube credentials. The live
writer at ``~/.local/lib/youtube-groom/youtube_groom.py`` calls this
module. Do not copy ``scripts/youtube_groom.py`` over that binary.

Quota: ``search.list`` is 100 units. Inserts are reserved first
(``CLIMB_INSERTS_PER_DAY`` × 50) so discovery cannot starve adds.
A day climbs by at most ``CLIMB_INSERTS_PER_DAY`` videos. The hard
playlist max is CAP 250.

#1045 long-form gate: ``filter_longform`` drops Shorts before ranking.
``search.list`` does not set ``videoDuration``. ``medium`` would drop
interviews longer than 20 minutes, and ``long`` alone would drop the
4–20 minute band. The duration filter is the enforcement.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

# Match the nest scorecard. Do not import youtube_groom — on the Pi that
# name is the live writer.
HOUSE_TARGET = 250
CAP = 250
HOUSE_TARGET_TOLERANCE = 15
BAND_LOW = HOUSE_TARGET - HOUSE_TARGET_TOLERANCE  # 235
BAND_HIGH = min(HOUSE_TARGET + HOUSE_TARGET_TOLERANCE, CAP)  # 250
STALE_HARD_DAYS = 30
FRESH_HOURS = 720
WEIGHT_FLOOR = 0.05
PREV_WEIGHT_FLOOR = 0.15
SEED_THROTTLE_WEIGHT_FLOOR = 0.0
PREV_SEED_THROTTLE_WEIGHT_FLOOR = 0.10
SEED_UPLOADS_PER_CHANNEL = 50  # unchanged; YouTube page max (#788)
CLIMB_INSERTS_PER_DAY = 40
CHANNEL_SHARE_MIN = 12
CHANNEL_SHARE_FRACTION = 0.05
DISCOVERY_MIX_TARGET = 0.40
MAX_SEARCHES_PER_TICK = 4
DISCOVERED_CHANNELS_PER_TICK = 4
DISCOVERY_UPLOADS_PER_CHANNEL = 15
DISCOVERY_RESULTS = 10
POOL_CAP = 200
SUBSCRIPTION_PAGES = 4
SEARCH_UNIT_COST = 100
INSERT_UNIT_COST = 50
LIST_RESERVE_UNITS = 30
ROLLING_DAYS = 7
LEDGER_KEEP_DAYS = 14

# Extra alternations spliced into the writer's HARD_LENSES. Adjacent
# subject matter, not a new catch-all lens.
LENS_EXTRA = {
    "energy": r"solar|geothermal|baseload|battery\s+storage",
    "bitcoin": r"lightning|halving|hashprice|hash\s*rate",
    "ai": r"gpu|inference|transformer|neural|openai|anthropic",
    "autonomy": r"self-driving|waymo",
    "robotics": r"actuator|embodied",
    "capital": r"inflation|deficit|yield\s+curve",
    "fitness": r"strength\s+training|zone\s*5|protein",
}

DISCOVERY_QUERIES = (
    ("bitcoin energy mining", "date"),
    ("AI agents humanoid robots", "relevance"),
    ("monetary inflation liquidity", "date"),
    ("autonomous robotaxi", "relevance"),
)

COPY_OVER_PI = False

# Chris, 2026-10-03: never add a Short to AI Curated. Under 60s is a Short.
# YouTube also lets a Short run to 3 minutes, so duration alone is not enough.
MIN_LONGFORM_SEC = 60
SHORTS_MAX_SEC = 180
# Unofficial. Data API has no isShort. Off unless SHORTS_URL_PROBE=1.
SHORTS_URL_PROBE = os.environ.get("SHORTS_URL_PROBE") == "1"
SHORTS_PROBE_TIMEOUT_SEC = 3.0
_DURATION_RE = re.compile(
    r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$"
)


class PatchError(RuntimeError):
    """The live writer text does not have the anchor this patch expects."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    dt = dt or utcnow()
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
    # ceil(count * fraction) for the 5% default, without binary-float drift.
    numer = int(round(fraction * 100))
    pct = (count * numer + 99) // 100
    return max(minimum, pct)


def search_budget(
    quota_remaining: int,
    inserts_wanted: int,
    *,
    search_cost: int = SEARCH_UNIT_COST,
    insert_cost: int = INSERT_UNIT_COST,
    list_reserve: int = LIST_RESERVE_UNITS,
    max_searches: int = MAX_SEARCHES_PER_TICK,
) -> int:
    """How many search.list calls fit after reserving insert headroom.

    Returns 0 when the reserve would be eaten. Callers log that as
    ``quota_headroom`` and still attempt inserts.
    """
    reserve = max(0, int(inserts_wanted)) * insert_cost + list_reserve
    headroom = int(quota_remaining) - reserve
    if headroom < search_cost:
        return 0
    return max(0, min(int(max_searches), headroom // search_cost))


def climb_inserts_allowed(
    adds_today: int,
    slots_to_cap: int,
    *,
    per_day: int = CLIMB_INSERTS_PER_DAY,
) -> int:
    """Spread the climb across days. Not a per-tick insert ceiling."""
    room = max(0, int(per_day) - max(0, int(adds_today)))
    return max(0, min(room, max(0, int(slots_to_cap))))


def adds_today(state: dict[str, Any], now: datetime) -> int:
    bucket = state.get("adds_by_day") or {}
    if not isinstance(bucket, dict):
        return 0
    return int(bucket.get(now.date().isoformat()) or 0)


def splice_lens(pattern: str, extra: str) -> str:
    """Insert ``extra`` alternatives before the closing ``)\\b``."""
    if not extra or extra in pattern:
        return pattern
    token = r")\b"
    idx = pattern.rfind(token)
    if idx < 0:
        return pattern
    return pattern[:idx] + "|" + extra + pattern[idx:]


def widen_writer(g: dict[str, Any]) -> dict[str, str]:
    """Mutate HARD_LENSES in the live writer globals. No YouTube I/O.

    Idempotent. Does not change house, cap, or quota knobs — those come
    from the source patch and from ``apply_live_knobs``.
    """
    lenses = g.get("HARD_LENSES")
    applied: dict[str, str] = {}
    if not isinstance(lenses, dict):
        return applied
    for key, extra in LENS_EXTRA.items():
        cur = lenses.get(key)
        if not isinstance(cur, str):
            continue
        nxt = splice_lens(cur, extra)
        if nxt != cur:
            lenses[key] = nxt
            applied[key] = extra
    return applied


def _lane(channel_id: str, subscribed: set[str]) -> str:
    if channel_id and channel_id in subscribed:
        return "subscribed"
    return "discovered"


def tag_lanes(candidates: list[dict[str, Any]], subscribed: set[str]) -> None:
    for cand in candidates:
        cid = str(cand.get("channel_id") or "")
        cand["lane"] = _lane(cid, subscribed)


def rolling_mix(
    ledger: Sequence[dict[str, Any]],
    now: datetime,
    *,
    days: int = ROLLING_DAYS,
    target: float = DISCOVERY_MIX_TARGET,
) -> dict[str, Any]:
    cutoff = now - timedelta(days=days)
    subscribed = 0
    discovered = 0
    for row in ledger:
        if not isinstance(row, dict):
            continue
        at = parse_iso(str(row.get("at") or ""))
        if at is None or at < cutoff:
            continue
        if row.get("lane") == "discovered":
            discovered += 1
        else:
            subscribed += 1
    total = subscribed + discovered
    fraction = (discovered / total) if total else None
    return {
        "adds_7d": total,
        "subscribed_adds_7d": subscribed,
        "discovered_adds_7d": discovered,
        "discovered_fraction_7d": fraction,
        "mix_target": target,
        "mix_met": bool(fraction is not None and fraction >= target),
    }


def record_add(state: dict[str, Any], channel_id: str, lane: str, now: datetime) -> None:
    """Append one add and drop ledger rows older than 14 days."""
    ledger = state.get("add_ledger")
    if not isinstance(ledger, list):
        ledger = []
    ledger.append(
        {
            "at": iso(now),
            "channel_id": channel_id,
            "lane": "discovered" if lane == "discovered" else "subscribed",
        }
    )
    cutoff = now - timedelta(days=LEDGER_KEEP_DAYS)
    kept = []
    for row in ledger:
        at = parse_iso(str(row.get("at") or "")) if isinstance(row, dict) else None
        if at is None or at >= cutoff:
            kept.append(row)
    state["add_ledger"] = kept
    bucket = state.get("adds_by_day")
    if not isinstance(bucket, dict):
        bucket = {}
    day = now.date().isoformat()
    bucket[day] = int(bucket.get(day) or 0) + 1
    oldest = (now.date() - timedelta(days=LEDGER_KEEP_DAYS)).isoformat()
    state["adds_by_day"] = {k: v for k, v in bucket.items() if str(k) >= oldest}


def _pool(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = state.get("discovered_channels")
    if not isinstance(raw, dict):
        raw = {}
        state["discovered_channels"] = raw
    return raw


def note_pool(
    state: dict[str, Any],
    channel_id: str,
    name: str,
    fit: int,
    source: str,
    now: datetime,
) -> None:
    if not channel_id:
        return
    pool = _pool(state)
    row = pool.get(channel_id)
    if not isinstance(row, dict):
        row = {"first_seen": iso(now), "fit": 0, "sources": []}
        pool[channel_id] = row
    row["title"] = name or row.get("title") or channel_id
    row["fit"] = max(int(row.get("fit") or 0), int(fit))
    sources = row.get("sources")
    if not isinstance(sources, list):
        sources = []
    if source not in sources:
        sources.append(source)
    row["sources"] = sources
    row["last_seen"] = iso(now)
    if len(pool) > POOL_CAP:
        ranked = sorted(
            pool.items(),
            key=lambda kv: (int((kv[1] or {}).get("fit") or 0), str((kv[1] or {}).get("first_seen") or "")),
        )
        for cid, _row in ranked[: len(pool) - POOL_CAP]:
            pool.pop(cid, None)


def select_pool_channels(
    state: dict[str, Any],
    subscribed: set[str],
    now: datetime,
    *,
    limit: int = DISCOVERED_CHANNELS_PER_TICK,
) -> list[tuple[str, str]]:
    pool = _pool(state)
    rows = []
    for cid, row in pool.items():
        if cid in subscribed or not isinstance(row, dict):
            continue
        sampled = parse_iso(str(row.get("last_sampled") or ""))
        rows.append((int(row.get("fit") or 0), sampled or datetime.min.replace(tzinfo=timezone.utc), cid, str(row.get("title") or cid)))
    rows.sort(key=lambda item: (-item[0], item[1]))
    picked = []
    for _fit, _sampled, cid, title in rows[:limit]:
        pool[cid]["last_sampled"] = iso(now)
        picked.append((cid, title))
    return picked


def _video_id_from_item(item: dict[str, Any]) -> str:
    vid = (item.get("contentDetails") or {}).get("videoId")
    if vid:
        return str(vid)
    rid = (item.get("id") or {})
    if isinstance(rid, dict) and rid.get("videoId"):
        return str(rid["videoId"])
    resource = (item.get("snippet") or {}).get("resourceId") or {}
    return str(resource.get("videoId") or "")


def candidate_from_item(
    item: dict[str, Any],
    *,
    channel_id: str,
    channel_name: str,
    now: datetime,
    lane: str,
) -> Optional[dict[str, Any]]:
    vid = _video_id_from_item(item)
    if not vid:
        return None
    sn = item.get("snippet") or {}
    published = parse_iso(str(sn.get("publishedAt") or ""))
    if published is None:
        age_h = 10**9
        published_at = None
    else:
        age_h = (now - published).total_seconds() / 3600.0
        published_at = iso(published)
    return {
        "video_id": vid,
        "title": sn.get("title") or "",
        "channel": channel_name or sn.get("channelTitle") or channel_id,
        "channel_id": channel_id or sn.get("channelId") or "",
        "published_at": published_at,
        "age_hours": age_h,
        "description": sn.get("description") or "",
        "duration_sec": 0,
        "lane": lane,
    }


def _remember_channel(
    state: dict[str, Any],
    channel_id: str,
    name: str,
    title: str,
    description: str,
    thesis_fit: Optional[Callable[..., int]],
    source: str,
    now: datetime,
) -> None:
    fit = 1
    if thesis_fit is not None:
        try:
            fit = int(thesis_fit(title or "", description or ""))
        except Exception:
            fit = 1
    note_pool(state, channel_id, name, fit, source, now)


def supply_tick(
    *,
    yt: Any,
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
    playlist_items: Sequence[dict[str, Any]],
    subscribed_seed: set[str],
    quota_remaining: int,
    quota_can: Callable[..., bool],
    now: datetime,
    thesis_fit: Optional[Callable[..., int]],
    fresh_hours: int,
    after_prune: int,
    house_target: int = HOUSE_TARGET,
    cap: int = CAP,
    max_searches: Optional[int] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Append discovery candidates and tag every candidate's lane.

    Charges quota only through ``yt`` methods (search, subscriptions,
    uploads). Returns the same list object, extended.
    """
    subscribed, sub_source = _subscribed_ids(
        yt, state, subscribed_seed, quota_remaining, quota_can, now
    )
    tag_lanes(candidates, subscribed)
    seen = {c.get("video_id") for c in candidates if c.get("video_id")}

    slots = min(
        max(0, int(house_target) - max(0, after_prune)),
        max(0, int(cap) - max(0, after_prune)),
    )
    inserts_wanted = climb_inserts_allowed(adds_today(state, now), slots)
    search_cap = MAX_SEARCHES_PER_TICK if max_searches is None else max(0, int(max_searches))
    searches = search_budget(quota_remaining, inserts_wanted, max_searches=search_cap)
    blocker = ""
    if searches == 0 and inserts_wanted > 0:
        blocker = "quota_headroom"
    published_after = iso(now - timedelta(hours=int(fresh_hours)))
    search_calls = 0
    if searches and hasattr(yt, "search_videos"):
        for query, order in DISCOVERY_QUERIES[:searches]:
            if not quota_can("search"):
                blocker = blocker or "quota_headroom"
                break
            try:
                items = yt.search_videos(
                    query,
                    order=order,
                    published_after=published_after,
                    max_results=DISCOVERY_RESULTS,
                )
            except Exception as exc:
                if exc.__class__.__name__ == "QuotaCapReached":
                    raise
                break
            search_calls += 1
            for item in items or []:
                sn = item.get("snippet") or {}
                cid = str(sn.get("channelId") or "")
                _remember_channel(
                    state,
                    cid,
                    str(sn.get("channelTitle") or cid),
                    str(sn.get("title") or ""),
                    str(sn.get("description") or ""),
                    thesis_fit,
                    "search",
                    now,
                )
                cand = candidate_from_item(
                    item,
                    channel_id=cid,
                    channel_name=str(sn.get("channelTitle") or cid),
                    now=now,
                    lane=_lane(cid, subscribed),
                )
                if cand and cand["video_id"] not in seen:
                    candidates.append(cand)
                    seen.add(cand["video_id"])

    for item in playlist_items:
        cid = str(item.get("channel_id") or "")
        if not cid or cid in subscribed:
            continue
        _remember_channel(
            state,
            cid,
            str(item.get("channel") or cid),
            str(item.get("title") or ""),
            str(item.get("description") or ""),
            thesis_fit,
            "playlist",
            now,
        )

    if hasattr(yt, "uploads"):
        for cid, title in select_pool_channels(state, subscribed, now):
            if not quota_can("list"):
                break
            try:
                ups = yt.uploads(cid, max_results=DISCOVERY_UPLOADS_PER_CHANNEL)
            except Exception as exc:
                if exc.__class__.__name__ == "QuotaCapReached":
                    raise
                continue
            for up in ups or []:
                cand = candidate_from_item(
                    up,
                    channel_id=cid,
                    channel_name=title,
                    now=now,
                    lane=_lane(cid, subscribed),
                )
                if cand and cand["video_id"] not in seen:
                    candidates.append(cand)
                    seen.add(cand["video_id"])

    report = {
        "pool": len(_pool(state)),
        "searches": search_calls,
        "search_units": search_calls * SEARCH_UNIT_COST,
        "subscription_source": sub_source,
        "quota_blocker": blocker,
        "inserts_wanted": inserts_wanted,
        "share_cap": channel_share_cap(after_prune),
    }
    state["_supply"] = report
    return candidates, report


def _subscribed_ids(
    yt: Any,
    state: dict[str, Any],
    subscribed_seed: set[str],
    quota_remaining: int,
    quota_can: Callable[..., bool],
    now: datetime,
) -> tuple[set[str], str]:
    cached = state.get("subscribed_channels")
    today = now.date().isoformat()
    ids: set[str] = set()
    source = "seeds_only"
    if isinstance(cached, dict) and cached.get("day") == today:
        ids = {str(x) for x in (cached.get("ids") or [])}
        source = "cache"
    elif (
        hasattr(yt, "subscription_channel_ids")
        and quota_remaining >= LIST_RESERVE_UNITS + SUBSCRIPTION_PAGES
        and quota_can("subscriptions")
    ):
        try:
            ids = {str(x) for x in yt.subscription_channel_ids(max_pages=SUBSCRIPTION_PAGES)}
            state["subscribed_channels"] = {"day": today, "ids": sorted(ids), "source": "api"}
            source = "api"
        except Exception as exc:
            if exc.__class__.__name__ == "QuotaCapReached":
                raise
            ids = set()
            source = "seeds_only"
    return ids | set(subscribed_seed), source


def prepare_adds(
    ranked_add: list[tuple[Any, ...]],
    *,
    playlist_items: Sequence[dict[str, Any]],
    state: dict[str, Any],
    now: datetime,
    after_prune: int,
    house_target: int = HOUSE_TARGET,
    cap: int = CAP,
    quota_remaining: int = 0,
) -> tuple[list[tuple[Any, ...]], list[dict[str, Any]], dict[str, Any]]:
    """Apply share cap, discovery bias, and the daily climb budget."""
    partial = state.pop("_supply", None) or {}
    slots_house = max(0, int(house_target) - max(0, after_prune))
    slots_cap = max(0, int(cap) - max(0, after_prune))
    slots = min(slots_house, slots_cap)
    budget = climb_inserts_allowed(adds_today(state, now), slots)
    share = channel_share_cap(after_prune)
    mix = rolling_mix(state.get("add_ledger") or [], now)
    bias = not mix["mix_met"]
    counts: dict[str, int] = {}
    for item in playlist_items:
        cid = str(item.get("channel_id") or "")
        if cid:
            counts[cid] = counts.get(cid, 0) + 1

    def sort_key(row: tuple[Any, ...]) -> tuple[Any, ...]:
        score = row[0]
        cand = row[1]
        published = cand.get("published_at") or ""
        if bias and cand.get("lane") == "discovered":
            return (1, score, published)
        if bias:
            return (0, score, published)
        return (score, published)

    ordered = sorted(ranked_add, key=sort_key, reverse=True)
    chosen: list[tuple[Any, ...]] = []
    skipped: list[dict[str, Any]] = []
    for row in ordered:
        if len(chosen) >= budget:
            break
        cand = row[1]
        cid = str(cand.get("channel_id") or "")
        if cid and counts.get(cid, 0) >= share:
            skipped.append(
                {
                    "video_id": cand.get("video_id"),
                    "title": cand.get("title") or "",
                    "why": "channel-share",
                }
            )
            continue
        chosen.append(row)
        if cid:
            counts[cid] = counts.get(cid, 0) + 1

    blocker = str(partial.get("quota_blocker") or "")
    if slots > 0 and budget > 0 and int(quota_remaining) < INSERT_UNIT_COST:
        blocker = "quota_exhausted"
    elif slots > 0 and budget == 0 and adds_today(state, now) >= CLIMB_INSERTS_PER_DAY:
        blocker = blocker or "climb_daily_cap"
    report = dict(partial)
    report.update(
        {
            "share_cap": share,
            "inserts_budget": budget,
            "quota_blocker": blocker,
            "mix_met": mix["mix_met"],
            "subscribed_adds_7d": mix["subscribed_adds_7d"],
            "discovered_adds_7d": mix["discovered_adds_7d"],
            "discovered_fraction_7d": mix["discovered_fraction_7d"],
        }
    )
    return chosen, skipped, report


def finish_supply_report(
    report: Optional[dict[str, Any]],
    *,
    added: Sequence[dict[str, Any]],
    playlist_items: Sequence[dict[str, Any]],
    state: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Fill this-tick add split and the flat ``log`` dict the groom line prints."""
    partial = state.pop("_supply", None) or {}
    out = dict(partial)
    out.update(report or {})
    subscribed_adds = 0
    discovered_adds = 0
    for row in added:
        if row.get("lane") == "discovered":
            discovered_adds += 1
        else:
            subscribed_adds += 1
    channels = {
        str(item.get("channel_id"))
        for item in playlist_items
        if item.get("channel_id")
    }
    for row in added:
        if row.get("channel_id"):
            channels.add(str(row["channel_id"]))
    mix = rolling_mix(state.get("add_ledger") or [], now)
    fraction = mix["discovered_fraction_7d"]
    out.update(
        {
            "pool": int(out.get("pool") or len(_pool(state))),
            "subscribed_adds": subscribed_adds,
            "discovered_adds": discovered_adds,
            "unique_channels": len(channels),
            "subscribed_adds_7d": mix["subscribed_adds_7d"],
            "discovered_adds_7d": mix["discovered_adds_7d"],
            "discovered_fraction_7d": fraction,
            "searches": int(out.get("searches") or 0),
            "search_units": int(out.get("search_units") or 0),
            "share_cap": int(out.get("share_cap") or channel_share_cap(len(playlist_items))),
            "inserts_budget": int(out.get("inserts_budget") or 0),
            "quota_blocker": str(out.get("quota_blocker") or ""),
        }
    )
    out["log"] = {
        "pool": out["pool"],
        "subscribed_adds": subscribed_adds,
        "discovered_adds": discovered_adds,
        "unique_channels": out["unique_channels"],
        "searches": out["searches"],
        "search_units": out["search_units"],
        "subscribed_adds_7d": out["subscribed_adds_7d"],
        "discovered_adds_7d": out["discovered_adds_7d"],
        "share_cap": out["share_cap"],
        "inserts_budget": out["inserts_budget"],
        "quota_blocker": out["quota_blocker"],
    }
    return out


def parse_duration_sec(raw: Any) -> Optional[int]:
    """Strict ISO 8601 duration in seconds. Never raises.

    Missing, malformed, and ``P0D`` (live/upcoming) are None. ``PT0S`` is 0.
    """
    if raw is None or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text == "P0D":
        return None
    match = _DURATION_RE.fullmatch(text)
    if not match:
        return None
    days, hours, mins, secs = match.groups()
    if days is None and hours is None and mins is None and secs is None:
        return None
    total = int(days or 0) * 86400 + int(hours or 0) * 3600 + int(mins or 0) * 60
    if secs is not None:
        total += int(float(secs))
    return total


def _has_short_tag(text: str) -> bool:
    return re.search(r"#shorts?\b", text.lower()) is not None


def _probe_shorts_url(video_id: str, *, timeout: float = SHORTS_PROBE_TIMEOUT_SEC) -> bool:
    """200 means Short. 303 to /watch means not. Any error fails open."""

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    url = f"https://www.youtube.com/shorts/{video_id}"
    request = urllib.request.Request(url, method="GET")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 200:
            return True
        if exc.code == 303:
            location = str(exc.headers.get("Location") or "")
            return "/watch" not in location
        return False
    except Exception:
        return False
    try:
        code = response.getcode()
    except Exception:
        return False
    return code == 200


def is_shorts_flagged(video: Any, *, duration_sec: Optional[int] = None) -> bool:
    """Best-effort Shorts mark. The Data API has no isShort field.

    ``#shorts`` / ``#short`` in the title, description, or tags, or a
    ``/shorts/`` URL on the candidate. For 60–180s only, and only when
    ``SHORTS_URL_PROBE=1``, a no-redirect GET of the Shorts URL. Probe
    errors fail open (not flagged).
    """
    if not isinstance(video, dict):
        return False
    snippet = video.get("snippet") if isinstance(video.get("snippet"), dict) else {}
    parts = [
        str(video.get("title") or snippet.get("title") or ""),
        str(video.get("description") or snippet.get("description") or ""),
    ]
    tags = video.get("tags")
    if tags is None:
        tags = snippet.get("tags")
    if isinstance(tags, list):
        parts.extend(str(tag) for tag in tags)
    if _has_short_tag("\n".join(parts)):
        return True
    for key in ("url", "link"):
        if "/shorts/" in str(video.get(key) or "").lower():
            return True
    dur = duration_sec if duration_sec is not None else video.get("duration_sec")
    if not isinstance(dur, int) or isinstance(dur, bool):
        return False
    if dur < MIN_LONGFORM_SEC or dur > SHORTS_MAX_SEC:
        return False
    if os.environ.get("SHORTS_URL_PROBE") != "1":
        return False
    vid = video.get("video_id") or video.get("id") or ""
    if isinstance(vid, dict):
        vid = vid.get("videoId") or ""
    if not vid:
        return False
    return _probe_shorts_url(str(vid))


def _duration_missing(candidate: dict[str, Any]) -> bool:
    dur = candidate.get("duration_sec")
    return not isinstance(dur, int) or isinstance(dur, bool) or dur == 0


def _videos_by_id(yt: Any, video_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    if not video_ids or yt is None or not hasattr(yt, "videos"):
        return found
    for start in range(0, len(video_ids), 50):
        chunk = list(video_ids[start : start + 50])
        got = yt.videos(chunk)
        if isinstance(got, dict):
            for key, item in got.items():
                if isinstance(item, dict):
                    found[str(key)] = item
        elif isinstance(got, list):
            for item in got:
                if not isinstance(item, dict):
                    continue
                vid = item.get("id")
                if isinstance(vid, dict):
                    vid = vid.get("videoId")
                if vid:
                    found[str(vid)] = item
    return found


def _skip_row(candidate: dict[str, Any], why: str) -> dict[str, str]:
    return {
        "video_id": str(candidate.get("video_id") or ""),
        "title": str(candidate.get("title") or ""),
        "why": why,
    }


def filter_longform(
    candidates: Sequence[dict[str, Any]],
    yt: Any,
    log: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Drop Shorts and unknown durations before ranking.

    Candidates with ``duration_sec`` 0 or missing are enriched from
    ``yt.videos`` (50 ids per call). Skip reasons are ``short<60s``,
    ``short-flagged``, and ``duration-unknown``.
    """
    rows = [c for c in candidates if isinstance(c, dict)]
    need = [str(c.get("video_id") or "") for c in rows if c.get("video_id") and _duration_missing(c)]
    fetched = _videos_by_id(yt, [vid for vid in need if vid])
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for candidate in rows:
        vid = str(candidate.get("video_id") or "")
        item = fetched.get(vid) or {}
        if _duration_missing(candidate):
            raw = (item.get("contentDetails") or {}).get("duration") if item else None
            candidate["duration_sec"] = parse_duration_sec(raw)
        duration = candidate.get("duration_sec")
        if not isinstance(duration, int) or isinstance(duration, bool):
            duration = None
            candidate["duration_sec"] = None
        view = dict(candidate)
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        if snippet.get("tags") and not view.get("tags"):
            view["tags"] = snippet.get("tags")
        if snippet.get("description"):
            view["description"] = snippet.get("description") or view.get("description")
        flagged = is_shorts_flagged(view, duration_sec=duration if isinstance(duration, int) else None)
        why = ""
        if isinstance(duration, int) and duration < MIN_LONGFORM_SEC:
            why = "short<60s"
        elif flagged:
            why = "short-flagged"
        elif duration is None:
            why = "duration-unknown"
        if not why:
            kept.append(candidate)
            continue
        skipped.append(_skip_row(candidate, why))
        if log is not None:
            log.info("skip add %s %s", vid, why)
    return kept, skipped


def _patch_duration_bonus(text: str) -> str:
    """Unknown duration must not earn the 15–180 min ranking bonus."""
    old = "    duration = 1 if 15 * 60 <= dur <= 3 * 3600 or dur == 0 else 0\n"
    new = "    duration = 1 if 15 * 60 <= dur <= 3 * 3600 else 0  # unknown is not a bonus (#1045)\n"
    if old in text:
        return text.replace(old, new, 1)
    if "or dur == 0" in text and "def score_candidate" in text:
        raise PatchError("missing anchor: duration-bonus")
    return text


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    # `new` often starts with `old`. Check the rewritten text first so a
    # second run does not insert the same line again.
    if new in text:
        return text
    if old not in text:
        raise PatchError(f"missing anchor: {label}")
    return text.replace(old, new, 1)


def patch_writer_source(text: str, *, parse: bool = True) -> str:
    """Surgical #957 edit. Raises PatchError if the live writer drifted.

    Does not import or replace the module. A second call is a no-op
    once every anchor has already been rewritten.
    """
    text = _replace_once(
        text,
        "HOUSE_TARGET = 100  # fill target after prune; was 50 (#788)\nCAP = 200\n",
        "HOUSE_TARGET = 250  # fill target after prune; was 100 (#957). #788 was 50→100.\n"
        "CAP = 250  # hard max; never exceed (#957). Was 200.\n",
        "house-cap",
    )
    text = _replace_once(
        text,
        "STALE_HARD_DAYS = 7\nFRESH_HOURS = 168\n",
        "STALE_HARD_DAYS = 30  # was 7. 30d can hold ~250 (#957).\n"
        "FRESH_HOURS = 720  # was 168. Matches STALE_HARD_DAYS so the inner prune cannot collapse the list to 7d (#957).\n",
        "stale-fresh",
    )
    text = _replace_once(
        text,
        "SEED_THROTTLE_WEIGHT_FLOOR = 0.10  # skip SEED_THROTTLE only below this (was 0.25, #815)\n",
        "SEED_THROTTLE_WEIGHT_FLOOR = 0.00  # throttle skip off at baseline (#957). Was 0.10 (#815).\n",
        "throttle",
    )
    text = _replace_once(
        text,
        "SEED_UPLOADS_PER_CHANNEL = 50  # was 6; YouTube page max so 7d window can fill house 100\n",
        "SEED_UPLOADS_PER_CHANNEL = 50  # unchanged; YouTube page max (#788). #957 share cap limits playlist share.\n",
        "uploads",
    )
    text = _replace_once(
        text,
        "WEIGHT_FLOOR = 0.15\n",
        "WEIGHT_FLOOR = 0.05  # was 0.15. Decay still ranks a channel down (#957).\n",
        "weight-floor",
    )
    text = _replace_once(
        text,
        "  Hourly search is forbidden. Scout stays off unless --scout (manual).\n",
        "  Unbounded hourly search is forbidden. #957 discovery is budgeted\n"
        "  (max 4 search.list calls/tick, after insert headroom).\n",
        "search-comment",
    )
    for needle, extra in (
        (
            "energy\\s+(crisis|shortage|super|age)|fusion)\\b",
            "energy\\s+(crisis|shortage|super|age)|fusion|solar|geothermal|baseload|battery\\s+storage)\\b",
        ),
        (
            "gold\\s+to|monetary|hyperbitcoin)\\b",
            "gold\\s+to|monetary|hyperbitcoin|lightning|halving|hashprice|hash\\s*rate)\\b",
        ),
        (
            "agentic|foundation\\s+model)\\b",
            "agentic|foundation\\s+model|gpu|inference|transformer|neural|openai|anthropic)\\b",
        ),
        (
            "cybercab|robotaxi|fsd)\\b",
            "cybercab|robotaxi|fsd|self-driving|waymo)\\b",
        ),
        (
            "humanoid|optimus|bots?\\b)\\b",
            "humanoid|optimus|bots?\\b|actuator|embodied)\\b",
        ),
        (
            "rate\\s+cut|balance\\s+sheet|dollar)\\b",
            "rate\\s+cut|balance\\s+sheet|dollar|inflation|deficit|yield\\s+curve)\\b",
        ),
        (
            "hypertrophy|training\\s+load)\\b",
            "hypertrophy|training\\s+load|strength\\s+training|zone\\s*5|protein)\\b",
        ),
    ):
        text = _replace_once(text, needle, extra, needle[:24])
    text = _replace_once(
        text,
        '    "search": 100,\n}',
        '    "search": 100,\n    "subscriptions": 1,\n}',
        "cost",
    )
    methods = '''
    def search_videos(
        self,
        query: str,
        *,
        order: str = "date",
        published_after: str | None = None,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "part": "snippet",
            "type": "video",
            "q": query,
            "order": order,
            "maxResults": max_results,
            "safeSearch": "none",
        }
        if published_after:
            kwargs["publishedAfter"] = published_after
        resp = self._exec("search", self.yt.search().list(**kwargs))
        return resp.get("items") or []

    def subscription_channel_ids(self, max_pages: int = 4) -> set[str]:
        ids: set[str] = set()
        token = None
        for _page in range(max_pages):
            resp = self._exec(
                "subscriptions",
                self.yt.subscriptions().list(
                    part="snippet",
                    mine=True,
                    maxResults=50,
                    pageToken=token,
                ),
            )
            for item in resp.get("items") or []:
                cid = ((item.get("snippet") or {}).get("resourceId") or {}).get("channelId")
                if cid:
                    ids.add(cid)
            token = resp.get("nextPageToken")
            if not token:
                break
        return ids

'''
    if "def search_videos" not in text:
        anchor = "        return resp.get(\"items\") or []\n\n    def maybe_retitle_playlist(self) -> bool:\n"
        if anchor not in text:
            raise PatchError("missing anchor: uploads-tail")
        text = text.replace(anchor, "        return resp.get(\"items\") or []\n" + methods + "    def maybe_retitle_playlist(self) -> bool:\n", 1)
    hook = (
        "    try:\n"
        "        from youtube_groom_supply import widen_writer\n"
        "        widen_writer(globals())\n"
        "    except Exception:\n"
        "        pass\n"
    )
    if "widen_writer" not in text:
        anchor = (
            "        apply_live_knobs(globals())\n"
            "    except Exception:\n"
            "        pass\n"
        )
        text = _replace_once(text, anchor, anchor + hook, "widen-hook")
    supply_call = '''        try:
            from youtube_groom_supply import supply_tick

            candidates, supply_report = supply_tick(
                yt=yt,
                state=state,
                candidates=candidates,
                playlist_items=[
                    i
                    for i in items
                    if i.get("video_id") in remaining_ids and not i.get("dead")
                ],
                subscribed_seed=set(seed_map),
                quota_remaining=quota.remaining(),
                quota_can=quota.can,
                now=utcnow(),
                thesis_fit=thesis_fit,
                fresh_hours=FRESH_HOURS,
                after_prune=len(remaining_ids),
                house_target=HOUSE_TARGET,
                cap=CAP,
            )
        except Exception:
            log.warning("supply lane failed")
'''
    if "supply_tick(" not in text:
        text = _replace_once(text, "        ranked_add = []\n", supply_call + "        ranked_add = []\n", "supply-tick")
    longform_call = '''        except Exception:
            log.warning("supply lane failed")
        try:
            from youtube_groom_supply import filter_longform

            candidates, longform_skipped = filter_longform(candidates, yt, log)
            skipped_add.extend(longform_skipped)
        except Exception:
            log.warning("longform filter failed")
            kept_longform = []
            for c in candidates:
                dur = c.get("duration_sec")
                blob = f"{c.get('title') or ''} {c.get('description') or ''}".lower()
                flagged = "#short" in blob or "/shorts/" in blob
                known_long = (
                    isinstance(dur, int)
                    and not isinstance(dur, bool)
                    and dur >= 60
                    and not flagged
                )
                if known_long:
                    kept_longform.append(c)
                else:
                    why = "short<60s" if isinstance(dur, int) and not isinstance(dur, bool) and 0 < dur < 60 else "duration-unknown"
                    if flagged and not (isinstance(dur, int) and not isinstance(dur, bool) and 0 < dur < 60):
                        why = "short-flagged"
                    skipped_add.append(
                        {
                            "video_id": c.get("video_id") or "",
                            "title": c.get("title") or "",
                            "why": why,
                        }
                    )
            candidates = kept_longform
        ranked_add = []
'''
    text = _replace_once(
        text,
        '        except Exception:\n            log.warning("supply lane failed")\n        ranked_add = []\n',
        longform_call,
        "longform",
    )
    text = _patch_duration_bonus(text)
    text = _replace_once(
        text,
        "    added: list[dict[str, Any]] = []\n    skipped_add: list[dict[str, Any]] = []\n",
        "    added: list[dict[str, Any]] = []\n"
        "    skipped_add: list[dict[str, Any]] = []\n"
        "    supply_report: dict[str, Any] = {}\n",
        "supply-init",
    )
    old_take = (
        "        after_prune = len(remaining_ids)\n"
        "        remaining_playlist_slots = max(0, CAP - after_prune)\n"
        "        take = min(\n"
        "            max(0, HOUSE_TARGET - after_prune),\n"
        "            max(0, CAP - after_prune),\n"
        "            remaining_playlist_slots,\n"
        "            len(ranked_add),\n"
        "        )\n"
        "        for sc, c, detail in ranked_add[:take]:\n"
    )
    new_take = (
        "        after_prune = len(remaining_ids)\n"
        "        try:\n"
        "            from youtube_groom_supply import prepare_adds\n"
        "\n"
        "            chosen, supply_skipped, supply_report = prepare_adds(\n"
        "                ranked_add,\n"
        "                playlist_items=[\n"
        "                    i\n"
        "                    for i in items\n"
        "                    if i.get(\"video_id\") in remaining_ids and not i.get(\"dead\")\n"
        "                ],\n"
        "                state=state,\n"
        "                now=utcnow(),\n"
        "                after_prune=after_prune,\n"
        "                house_target=HOUSE_TARGET,\n"
        "                cap=CAP,\n"
        "                quota_remaining=quota.remaining(),\n"
        "            )\n"
        "            skipped_add.extend(supply_skipped)\n"
        "        except Exception:\n"
        "            log.warning(\"supply prepare failed\")\n"
        "            take = min(\n"
        "                max(0, HOUSE_TARGET - after_prune),\n"
        "                max(0, CAP - after_prune),\n"
        "                len(ranked_add),\n"
        "            )\n"
        "            chosen = ranked_add[:take]\n"
        "            supply_report = {\"log\": {}, \"error\": \"supply_unavailable\"}\n"
        "        for sc, c, detail in chosen:\n"
    )
    if "prepare_adds(" not in text:
        text = _replace_once(text, old_take, new_take, "prepare-adds")
    old_append = (
        "                        \"score\": sc,\n"
        "                        \"detail\": detail,\n"
        "                    }\n"
        "                )\n"
        "                existing.add(c[\"video_id\"])\n"
    )
    new_append = (
        "                        \"score\": sc,\n"
        "                        \"detail\": detail,\n"
        "                        \"channel_id\": c.get(\"channel_id\") or \"\",\n"
        "                        \"lane\": c.get(\"lane\") or \"subscribed\",\n"
        "                    }\n"
        "                )\n"
        "                try:\n"
        "                    from youtube_groom_supply import record_add\n"
        "\n"
        "                    record_add(\n"
        "                        state,\n"
        "                        c.get(\"channel_id\") or \"\",\n"
        "                        c.get(\"lane\") or \"subscribed\",\n"
        "                        utcnow(),\n"
        "                    )\n"
        "                except Exception:\n"
        "                    pass\n"
        "                existing.add(c[\"video_id\"])\n"
    )
    if "record_add(" not in text:
        text = _replace_once(text, old_append, new_append, "record-add")
    finish = '''    try:
        from youtube_groom_supply import finish_supply_report

        supply_report = finish_supply_report(
            supply_report,
            added=added,
            playlist_items=[
                i
                for i in items
                if i.get("video_id") in remaining_ids and not i.get("dead")
            ],
            state=state,
            now=utcnow(),
        )
    except Exception:
        log.warning("supply finish failed")
        if not isinstance(supply_report, dict):
            supply_report = {}
        supply_report.setdefault("log", {})
    state.pop("_supply", None)
'''
    if "finish_supply_report(" not in text:
        text = _replace_once(
            text,
            "    reasons: dict[str, int] = {}\n",
            finish + "    reasons: dict[str, int] = {}\n",
            "finish",
        )
    text = _replace_once(
        text,
        '        "skipped_add": skipped_add[:20],\n',
        '        "skipped_add": skipped_add[:20],\n'
        '        "supply": (supply_report or {}).get("log") or {},\n',
        "result-supply",
    )
    text = _replace_once(
        text,
        '            "quota_used_today",\n',
        '            "quota_used_today",\n'
        '            "supply",\n',
        "state-supply",
    )
    text = _replace_once(
        text,
        '        f"house={HOUSE_TARGET}"\n',
        '        f"house={HOUSE_TARGET} supply={(supply_report or {}).get(\'log\') or {}}"\n',
        "log-line",
    )
    if parse:
        ast.parse(text)
    return text


def patch_writer_file(path: Path, *, backup: bool = True) -> dict[str, Any]:
    """Patch the live writer in place. Backup first. Do not replace the file."""
    original = path.read_text(encoding="utf-8")
    updated = patch_writer_source(original)
    if updated == original:
        return {"changed": False, "path": str(path), "backup": None}
    backup_path = None
    if backup:
        stamp = utcnow().strftime("%Y%m%d")
        backup_path = path.with_name(f"{path.name}.bak-{stamp}-957")
        shutil.copy2(path, backup_path)
    path.write_text(updated, encoding="utf-8")
    return {"changed": True, "path": str(path), "backup": str(backup_path) if backup_path else None}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="YouTube groom #957 supply policy. Does not groom a playlist."
    )
    parser.add_argument("--patch-writer", type=Path, default=None, help="surgical in-place patch")
    parser.add_argument("--check-writer", type=Path, default=None, help="parse the patch, do not write")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.check_writer:
        text = args.check_writer.read_text(encoding="utf-8")
        patch_writer_source(text)
        print(f"patch ok {args.check_writer}")
        return 0
    if args.patch_writer:
        result = patch_writer_file(args.patch_writer)
        print(result)
        return 0
    print("youtube_groom_supply (#957) — do not copy nest youtube_groom.py over the Pi writer")
    print(f"  HOUSE_TARGET={HOUSE_TARGET} CAP={CAP} band={BAND_LOW}–{BAND_HIGH}")
    print(f"  STALE_HARD_DAYS={STALE_HARD_DAYS} FRESH_HOURS={FRESH_HOURS}")
    print(f"  SEED_THROTTLE_WEIGHT_FLOOR={SEED_THROTTLE_WEIGHT_FLOOR} WEIGHT_FLOOR={WEIGHT_FLOOR}")
    print(f"  SEED_UPLOADS_PER_CHANNEL={SEED_UPLOADS_PER_CHANNEL} (unchanged)")
    print(f"  CLIMB_INSERTS_PER_DAY={CLIMB_INSERTS_PER_DAY}")
    print(f"  share=max({CHANNEL_SHARE_MIN}, ceil({CHANNEL_SHARE_FRACTION:.0%}))")
    print(f"  discovery_mix_target={DISCOVERY_MIX_TARGET} searches/tick<={MAX_SEARCHES_PER_TICK}")
    print(f"  MIN_LONGFORM_SEC={MIN_LONGFORM_SEC} SHORTS_MAX_SEC={SHORTS_MAX_SEC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
