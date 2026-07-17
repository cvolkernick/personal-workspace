"""Autonomous backlog grooming: score, prioritize, schedule, light status hygiene.

Runs against ops/backlog/items.json. Does not invent new work (recommendations
do that); it ranks and schedules existing items and applies safe hygiene rules.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from backlog import PRIORITIES, STATUSES, load_backlog, save_backlog

_PRIORITY_SCORE = {"critical": 100, "high": 75, "medium": 50, "low": 25}
_PRIORITY_COLORS = {
    "critical": "#ff6b6b",
    "high": "#f5a623",
    "medium": "#5b9fd4",
    "low": "#8aa0b5",
}
_STATUS_BOOST = {
    "planning": 30,
    "active": 28,
    "ready": 22,
    "idea": 8,
    "parked": -40,
    "done": -100,
}

_SCHEDULE_LABELS = {
    "now": "Do now",
    "this_week": "This week",
    "next_week": "Next week",
    "later": "Later",
    "parked": "Parked",
    "done": "Done",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def priority_color(priority: str) -> str:
    return _PRIORITY_COLORS.get(priority or "medium", _PRIORITY_COLORS["medium"])


def score_item(item: dict[str, Any], *, wip_count: int = 0) -> tuple[int, list[str]]:
    """Higher = work sooner. Returns (score, reasons)."""
    score = 0
    reasons: list[str] = []
    pri = item.get("priority") or "medium"
    st = item.get("status") or "idea"
    base = _PRIORITY_SCORE.get(pri, 50)
    score += base
    reasons.append(f"{pri} priority (+{base})")

    boost = _STATUS_BOOST.get(st, 0)
    score += boost
    if boost:
        reasons.append(f"status {st} ({boost:+d})")

    if (item.get("mvp_scope") or "").strip():
        score += 12
        reasons.append("has MVP scope (+12)")
    else:
        if st in ("idea", "ready"):
            score -= 6
            reasons.append("missing MVP scope (−6)")

    if (item.get("notes") or "").strip():
        score += 6
        reasons.append("has next-step notes (+6)")

    if item.get("area"):
        score += 4
        reasons.append(f"area={item.get('area')} (+4)")

    tags = {t.lower() for t in (item.get("tags") or [])}
    if "leverage" in tags or "initiative" in tags:
        score += 10
        reasons.append("leverage/initiative tag (+10)")
    if "from-recommendation" in tags:
        score += 5
        reasons.append("from recommendation (+5)")

    # Age: stale ready items need attention; ancient ideas sink slightly
    created = _parse_ts(item.get("created_at"))
    updated = _parse_ts(item.get("updated_at")) or created
    now = datetime.now(timezone.utc)
    if updated:
        age_days = max(0, (now - updated).total_seconds() / 86400)
        if st == "ready" and age_days >= 3:
            score += 14
            reasons.append(f"ready but stale {age_days:.0f}d (+14)")
        elif st == "planning" and age_days >= 2:
            score += 18
            reasons.append(f"planning stall {age_days:.0f}d (+18)")
        elif st == "idea" and age_days >= 14 and not (item.get("mvp_scope") or "").strip():
            score -= 10
            reasons.append(f"stale idea {age_days:.0f}d (−10)")

    # WIP pressure: too many in-flight → deprioritize pure ideas
    if wip_count >= 2 and st == "idea":
        score -= 12
        reasons.append(f"WIP limit ({wip_count} in flight) (−12)")
    if wip_count >= 3 and st == "ready":
        score -= 8
        reasons.append("high WIP — hold new starts (−8)")

    if pri == "critical":
        score += 12
        reasons.append("critical urgency (+12)")

    return score, reasons


def _schedule_for(rank: int, status: str) -> tuple[str, str]:
    if status == "done":
        return "done", _SCHEDULE_LABELS["done"]
    if status == "parked":
        return "parked", _SCHEDULE_LABELS["parked"]
    if status in ("planning", "active"):
        return "now", _SCHEDULE_LABELS["now"]
    if rank <= 2:
        return "now", _SCHEDULE_LABELS["now"]
    if rank <= 4:
        return "this_week", _SCHEDULE_LABELS["this_week"]
    if rank <= 6:
        return "next_week", _SCHEDULE_LABELS["next_week"]
    return "later", _SCHEDULE_LABELS["later"]


def _suggested_priority(item: dict[str, Any], score: int) -> Optional[str]:
    """Optional auto priority adjustment (safe, never jumps more than one step)."""
    cur = item.get("priority") or "medium"
    st = item.get("status") or "idea"
    if st in ("planning", "active") and cur == "low":
        return "medium"
    if st == "ready" and score >= 110 and cur == "medium":
        return "high"
    if st == "idea" and score < 30 and cur == "high":
        return "medium"
    if st == "idea" and score < 15 and cur == "medium":
        return "low"
    return None


def _suggested_status(item: dict[str, Any]) -> Optional[str]:
    """Optional hygiene: idea → ready when well-specified."""
    st = item.get("status") or "idea"
    if st != "idea":
        return None
    has_mvp = bool((item.get("mvp_scope") or "").strip())
    has_notes = bool((item.get("notes") or "").strip())
    has_area = bool((item.get("area") or "").strip())
    if has_mvp and (has_notes or has_area):
        return "ready"
    return None


def rank_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach score/press_rank/schedule to copies of items (active only for ranking)."""
    wip = sum(1 for i in items if i.get("status") in ("planning", "active"))
    active = [i for i in items if i.get("status") not in ("done",)]
    done = [i for i in items if i.get("status") == "done"]

    scored: list[dict[str, Any]] = []
    for it in active:
        s = dict(it)
        sc, reasons = score_item(s, wip_count=wip)
        s["score"] = sc
        s["rank_reasons"] = reasons
        s["priority_color"] = priority_color(s.get("priority") or "medium")
        scored.append(s)

    scored.sort(
        key=lambda x: (
            -(x.get("score") or 0),
            -_PRIORITY_SCORE.get(x.get("priority") or "medium", 0),
            x.get("updated_at") or "",
        ),
        reverse=False,
    )
    # sort already by -score first key... wait I used reverse=False with negative scores so higher score first. Good.

    labels = {1: "Do first", 2: "Do next", 3: "Then"}
    for i, s in enumerate(scored, start=1):
        s["press_rank"] = i
        s["rank_label"] = labels.get(i, "Queued")
        slot, slot_label = _schedule_for(i, s.get("status") or "idea")
        s["schedule_slot"] = slot
        s["schedule_label"] = slot_label

    for d in done:
        s = dict(d)
        s["score"] = 0
        s["press_rank"] = None
        s["rank_label"] = "Done"
        s["schedule_slot"] = "done"
        s["schedule_label"] = "Done"
        s["rank_reasons"] = []
        s["priority_color"] = priority_color(s.get("priority") or "medium")
        scored.append(s)

    return scored


def group_by_schedule(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = ("now", "this_week", "next_week", "later", "parked", "done")
    groups = []
    for slot in order:
        items = [i for i in ranked if i.get("schedule_slot") == slot]
        if items:
            groups.append(
                {
                    "slot": slot,
                    "label": _SCHEDULE_LABELS.get(slot, slot),
                    "count": len(items),
                    "items": items,
                }
            )
    return groups


def group_by_priority(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = []
    for pri in ("critical", "high", "medium", "low"):
        items = [
            i
            for i in ranked
            if (i.get("priority") or "medium") == pri and i.get("status") != "done"
        ]
        if items:
            groups.append(
                {
                    "priority": pri,
                    "color": priority_color(pri),
                    "label": pri.upper(),
                    "count": len(items),
                    "items": items,
                }
            )
    return groups


def groom_backlog(*, apply: bool = True, include_done: bool = False) -> dict[str, Any]:
    """Rank, schedule, and optionally apply hygiene (priority/status tweaks).

    apply=True writes score/rank/schedule onto items and safe status/priority bumps.
    """
    data = load_backlog()
    raw = list(data.get("items") or [])
    wip = sum(1 for i in raw if i.get("status") in ("planning", "active"))
    changes: list[dict[str, Any]] = []

    # Score full set for ranking active items
    ranked = rank_items(raw)
    rank_by_id = {i["id"]: i for i in ranked if i.get("id")}

    if apply:
        for it in raw:
            rid = it.get("id")
            ranked_it = rank_by_id.get(rid)
            if not ranked_it:
                continue
            before = {
                "priority": it.get("priority"),
                "status": it.get("status"),
                "press_rank": it.get("press_rank"),
                "schedule_slot": it.get("schedule_slot"),
            }
            # Persist ranking fields
            it["score"] = ranked_it.get("score")
            it["press_rank"] = ranked_it.get("press_rank")
            it["rank_label"] = ranked_it.get("rank_label")
            it["rank_reasons"] = ranked_it.get("rank_reasons")
            it["priority_color"] = ranked_it.get("priority_color")
            it["schedule_slot"] = ranked_it.get("schedule_slot")
            it["schedule_label"] = ranked_it.get("schedule_label")
            it["last_groomed_at"] = _now()

            if it.get("status") not in ("done", "parked"):
                sp = _suggested_priority(it, ranked_it.get("score") or 0)
                if sp and sp in PRIORITIES and sp != it.get("priority"):
                    it["priority"] = sp
                    it["priority_color"] = priority_color(sp)
                    changes.append(
                        {
                            "id": rid,
                            "title": it.get("title"),
                            "field": "priority",
                            "from": before["priority"],
                            "to": sp,
                            "reason": "auto-groom score band",
                        }
                    )
                ss = _suggested_status(it)
                if ss and ss in STATUSES and ss != it.get("status"):
                    it["status"] = ss
                    # re-schedule after status change for display consistency
                    slot, slab = _schedule_for(it.get("press_rank") or 99, ss)
                    it["schedule_slot"] = slot
                    it["schedule_label"] = slab
                    changes.append(
                        {
                            "id": rid,
                            "title": it.get("title"),
                            "field": "status",
                            "from": before["status"],
                            "to": ss,
                            "reason": "idea well-specified → ready",
                        }
                    )

            after_rank = {
                "priority": it.get("priority"),
                "status": it.get("status"),
                "press_rank": it.get("press_rank"),
                "schedule_slot": it.get("schedule_slot"),
            }
            if after_rank != before and not any(c["id"] == rid for c in changes):
                changes.append(
                    {
                        "id": rid,
                        "title": it.get("title"),
                        "field": "rank/schedule",
                        "from": before,
                        "to": after_rank,
                        "reason": "re-ranked",
                    }
                )

        data["items"] = raw
        data["last_groomed_at"] = _now()
        data["groom_meta"] = {
            "wip_count": wip,
            "changes_count": len(changes),
            "groomed_at": data["last_groomed_at"],
        }
        save_backlog(data)
        ranked = rank_items(raw)

    active = [i for i in ranked if include_done or i.get("status") != "done"]
    return {
        "ok": True,
        "applied": apply,
        "groomed_at": _now(),
        "wip_count": wip,
        "count": len([i for i in active if i.get("status") != "done"]),
        "changes": changes,
        "items": active,
        "ranked": [i for i in active if i.get("status") != "done"],
        "by_schedule": group_by_schedule([i for i in active if i.get("status") != "done"]),
        "by_priority": group_by_priority(active),
        "top": [i for i in active if i.get("status") not in ("done", "parked")][:3],
        "message": (
            f"Groomed {len(active)} items"
            + (f", {len(changes)} hygiene changes" if changes else "")
        ),
    }


def enrich_backlog_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Rank without writing (used on read). Prefer stored ranks if fresh."""
    ranked = rank_items(items)
    active = [i for i in ranked if i.get("status") != "done"]
    return {
        "ranked": active,
        "by_schedule": group_by_schedule(active),
        "by_priority": group_by_priority(active),
        "top": [i for i in active if i.get("status") != "parked"][:3],
    }
