"""Daily opportunity radar: cap 3, dismiss/promote, material-change fingerprint."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Optional

from domain import RADAR_DAILY_CAP, iso


def candidate_fingerprint(title: str, source: str, why: str) -> str:
    raw = "|".join(
        (
            " ".join((title or "").lower().split()),
            " ".join((source or "").lower().split()),
            " ".join((why or "").lower().split()),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def select_candidates(
    raw: list[dict[str, Any]],
    *,
    dismissed: dict[str, Any],
    now: datetime,
    cap: int = RADAR_DAILY_CAP,
) -> list[dict[str, Any]]:
    """Pick up to `cap` new candidates. Dismissed fingerprints never resurface
    unless title/source/why (the fingerprint) materially changes.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        why = str(row.get("why") or "").strip()
        source = str(row.get("source") or "").strip()
        if not title or not why:
            continue
        fp = candidate_fingerprint(title, source, why)
        if fp in seen:
            continue
        seen.add(fp)
        if fp in (dismissed or {}):
            continue
        cid = str(row.get("id") or f"radar-{fp[:10]}")
        out.append(
            {
                "id": cid,
                "fingerprint": fp,
                "title": title[:240],
                "why": why[:600],
                "source": source[:120],
                "status": "new",
                "time_sensitive": bool(row.get("time_sensitive")),
                "as_of": str(row.get("as_of") or iso(now)),
            }
        )
        if len(out) >= cap:
            break
    return out


def dismiss_candidate(
    state: dict[str, Any],
    candidate_id: str,
    *,
    now: datetime,
) -> dict[str, Any]:
    radar = dict(state.get("radar") or {})
    candidates = list(radar.get("candidates") or [])
    dismissed = dict(radar.get("dismissed") or {})
    kept = []
    for c in candidates:
        if str(c.get("id")) == candidate_id:
            fp = str(c.get("fingerprint") or candidate_id)
            dismissed[fp] = {
                "id": c.get("id"),
                "title": c.get("title"),
                "why": c.get("why"),
                "source": c.get("source"),
                "dismissed_at": iso(now),
            }
            c = dict(c)
            c["status"] = "dismissed"
            radar_dismissed_list = list(radar.get("dismissed_list") or [])
            radar_dismissed_list.append(c)
            radar["dismissed_list"] = radar_dismissed_list[-50:]
        else:
            kept.append(c)
    radar["candidates"] = kept
    radar["dismissed"] = dismissed
    new_state = dict(state)
    new_state["radar"] = radar
    return new_state


def promote_candidate(
    state: dict[str, Any],
    candidate_id: str,
    *,
    now: datetime,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    radar = dict(state.get("radar") or {})
    candidates = list(radar.get("candidates") or [])
    target = None
    kept = []
    for c in candidates:
        if str(c.get("id")) == candidate_id:
            target = dict(c)
            target["status"] = "promoted"
            target["promoted_at"] = iso(now)
        else:
            kept.append(c)
    if target is None:
        return state, None
    radar["candidates"] = kept
    promoted = list(radar.get("promoted") or [])
    promoted.append(target)
    radar["promoted"] = promoted[-50:]
    tracked = list(state.get("tracked_targets") or [])
    tracked_id = f"target-{target.get('fingerprint') or target.get('id')}"
    tracked_row = {
        "id": tracked_id,
        "title": target.get("title"),
        "why": target.get("why"),
        "source": target.get("source"),
        "from_radar": target.get("id"),
        "created_at": iso(now),
        "goal_id": (state.get("goal") or {}).get("id"),
    }
    tracked.append(tracked_row)
    new_state = dict(state)
    new_state["radar"] = radar
    new_state["tracked_targets"] = tracked
    return new_state, tracked_row
