"""Thin Orchestra pulse: WORLD / NOW / NEXT / BLOCKED + personal dock.

Not #196 chrome. No WEEK / GATES / HELD. Pulse WEEK/GATES stay on Time
Allocator :8770. Horizon is a WORLD deep-link only (:8795), never a dock tile.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

try:
    from .attention import hours_since, parse_timestamp
except ImportError:  # unittest path insert
    from attention import hours_since, parse_timestamp

CIVIL_TZ = ZoneInfo("America/New_York")
HORIZON_URL = "http://127.0.0.1:8795/"
HORIZON_PORT = 8795
ALLOCATOR_URL = "http://127.0.0.1:8770/"
WORKFLOW_URL = "http://127.0.0.1:8765/"

GT_ID_KEYS = ("gt_task_id", "google_task_id", "gt_id")
DOMAIN_FRESH_HOURS = {
    "finance": 6.0,
    "fitness": 24.0,
    "workflow": 4.0,
    "holistic": 24.0,
}


def _utc_now(now: Optional[datetime] = None) -> datetime:
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        return ref.replace(tzinfo=timezone.utc)
    return ref.astimezone(timezone.utc)


def civil_date(value: Any, *, now: Optional[datetime] = None):
    """America/New_York civil date for a timestamp, or None."""
    dt = parse_timestamp(value)
    if dt is None:
        return None
    return dt.astimezone(CIVIL_TZ).date()


def same_civil_day(value: Any, *, now: Optional[datetime] = None) -> bool:
    ref = _utc_now(now).astimezone(CIVIL_TZ).date()
    day = civil_date(value, now=now)
    return day is not None and day == ref


def has_gt_task_id(item: Optional[dict[str, Any]]) -> bool:
    if not isinstance(item, dict):
        return False
    for key in GT_ID_KEYS:
        raw = item.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return True
    return False


def is_quest(item: Optional[dict[str, Any]]) -> bool:
    if not isinstance(item, dict):
        return False
    kind = str(item.get("kind") or "").strip().lower()
    title = str(item.get("title") or item.get("action") or "").lower()
    if kind == "quest":
        return True
    return "quest" in title


_TEAM_BOARD_KINDS = frozenset({"ready", "in_progress", "pipeline", "pending_review"})
_TEAM_BOARD_TITLE_RE = re.compile(
    r"pull candidate\s*#|pull ready:|continue / unblock\s*#|promote parked",
    re.I,
)
_ISSUE_EPIC_TITLE_RE = re.compile(r"#\d+\s*:")


def is_team_board_action(item: Optional[dict[str, Any]]) -> bool:
    """Buzz-board pull/ready/epic rows are team work, not seat NOW/NEXT."""
    if not isinstance(item, dict):
        return False
    kind = str(item.get("kind") or "").strip().lower()
    if kind in _TEAM_BOARD_KINDS:
        return True
    title = str(item.get("title") or item.get("action") or "")
    why = str(item.get("why") or item.get("detail") or "")
    blob = f"{title} {why}".lower()
    if "ready supply" in blob or "free agent" in blob:
        return True
    if _TEAM_BOARD_TITLE_RE.search(title):
        return True
    if _ISSUE_EPIC_TITLE_RE.search(title):
        return True
    return False


def keep_action_item(item: Optional[dict[str, Any]]) -> bool:
    """Quests without a GT id, today.md placeholders, and board jargon are omitted."""
    if not isinstance(item, dict):
        return False
    title = str(item.get("title") or item.get("action") or "").strip()
    if not title:
        return False
    if is_example_today_line(title):
        return False
    if is_team_board_action(item):
        return False
    if is_quest(item) and not has_gt_task_id(item):
        return False
    return True


# Creative-slot template in strategy/today.md — empty until the user writes a real action.
_CREATIVE_SLOT_RE = re.compile(
    r"\bcreative(?:\s+or\s+other(?:\s+domain)?)?\s+next\s+action\b",
    re.I,
)
_PLACEHOLDER_CLAUSES = frozenset(
    {"", "tbd", "...", "…", "todo", "fill", "to fill", "unfilled", "placeholder"}
)


def _is_placeholder_clause(text: str) -> bool:
    t = text.strip(" .()[]*—-_")
    if t in _PLACEHOLDER_CLAUSES:
        return True
    return (
        "user to fill" in t
        or "to be filled" in t
        or t.startswith("unfilled")
    )


def _is_empty_creative_slot(low: str) -> bool:
    if not _CREATIVE_SLOT_RE.search(low):
        return False
    for sep in (":", " — ", " – ", " - "):
        if sep in low:
            after = low.split(sep, 1)[1]
            if after.strip() and not _is_placeholder_clause(after.lower()):
                return False
    return True


def is_example_today_line(text: Any) -> bool:
    """strategy/today.md examples and unfilled slots are not Do-now / kind=today.

    Drops e.g./eg. examples, “user to fill” / unfilled markers, and empty
    creative-slot placeholders. A filled “Creative … next action: <real work>”
    line is an action.
    """
    raw = str(text or "").strip()
    if not raw:
        return True
    low = raw.lower()
    if "e.g." in low or "eg." in low:
        return True
    if "user to fill" in low or "unfilled" in low or "to be filled" in low:
        return True
    return _is_empty_creative_slot(low)


def backlog_feeds_recs(
    backlog: Optional[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    stale_hours: float = 48.0,
) -> bool:
    """ops/backlog may hint a session; it is not Board Status and not recs SoT."""
    if not isinstance(backlog, dict):
        return False
    if backlog.get("not_board_status"):
        return False
    as_of = backlog.get("updated_at") or backlog.get("as_of")
    age = hours_since(as_of, now=_utc_now(now))
    if age is not None and age > stale_hours:
        return False
    return True


def meridian_packet_live(fan_in: Optional[dict[str, Any]]) -> bool:
    """Meridian L0 packet is live when regime or implications are available."""
    fan = fan_in if isinstance(fan_in, dict) else {}
    sources = fan.get("sources") if isinstance(fan.get("sources"), dict) else {}
    regime = fan.get("regime") if isinstance(fan.get("regime"), dict) else {}
    imps = fan.get("implications") if isinstance(fan.get("implications"), dict) else {}
    if sources.get("packet_exists") and (regime.get("available") or imps.get("available")):
        return True
    return bool(regime.get("available") or imps.get("available"))


def build_world(fan_in: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """One Horizon line if Meridian packet is live. Else blank. No tabs/heat/GFS."""
    fan = fan_in if isinstance(fan_in, dict) else {}
    live = meridian_packet_live(fan)
    regime = fan.get("regime") if isinstance(fan.get("regime"), dict) else {}
    imps = fan.get("implications") if isinstance(fan.get("implications"), dict) else {}
    top = imps.get("top") if isinstance(imps.get("top"), list) else []
    first = top[0] if top and isinstance(top[0], dict) else {}
    label = regime.get("primary_label") if regime.get("available") else None
    action = first.get("action") if first else None
    if live and label and action:
        line = f"{label} · {action}"
    elif live and label:
        line = str(label)
    elif live and action:
        line = str(action)
    else:
        line = ""
    return {
        "line": line,
        "live": live,
        "blank": not bool(line),
        "url": HORIZON_URL,
        "port": HORIZON_PORT,
        "label": "WORLD",
        "opens": "horizon",
        "embed": False,
        "tabs": False,
        "heat": False,
        "gfs": False,
    }


def build_dock(domains: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """Personal dock: Time Allocator :8770 and Workflow :8765 only."""
    by_id = {
        str(d.get("id")): d
        for d in (domains or [])
        if isinstance(d, dict) and d.get("id")
    }
    specs = (
        {
            "id": "holistic",
            "label": "Time Allocator",
            "port": 8770,
            "url": ALLOCATOR_URL,
        },
        {
            "id": "workflow",
            "label": "Workflow",
            "port": 8765,
            "url": WORKFLOW_URL,
        },
    )
    out: list[dict[str, Any]] = []
    for spec in specs:
        domain = by_id.get(spec["id"]) or {}
        out.append(
            {
                "id": spec["id"],
                "label": spec["label"],
                "port": spec["port"],
                "url": domain.get("url") or spec["url"],
                "live": domain.get("live"),
                "stale": domain.get("stale"),
                "age_hours": domain.get("age_hours"),
                "status": domain.get("status"),
            }
        )
    return out


def _fresh_window_hours(item: dict[str, Any]) -> float:
    raw = item.get("fresh_for_hours")
    if raw is None:
        raw = item.get("fresh_window_hours")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    domain = str(item.get("domain") or "")
    return float(DOMAIN_FRESH_HOURS.get(domain, 24.0))


def is_falsifiable_fact(
    item: Optional[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Named fact needs a clock or same-civil-day log inside a fresh window."""
    if not isinstance(item, dict):
        return False
    as_of = item.get("as_of") or item.get("logged_at") or item.get("updated_at")
    if parse_timestamp(as_of) is None:
        return False
    ref = _utc_now(now)
    if same_civil_day(as_of, now=ref):
        return True
    age = hours_since(as_of, now=ref)
    if age is None:
        return False
    return age <= _fresh_window_hours(item)


def build_blocked(
    day_plan: Optional[dict[str, Any]] = None,
    *,
    workflow: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Named falsifiable gates ∪ workflow.blocked. Else unknown — not a timeline."""
    ref = _utc_now(now)
    plan = day_plan if isinstance(day_plan, dict) else {}
    gates = [g for g in (plan.get("gates") or []) if isinstance(g, dict)]
    wf = workflow if isinstance(workflow, dict) else {}
    sig = wf.get("signals") if isinstance(wf.get("signals"), dict) else {}
    board = sig.get("board") if isinstance(sig.get("board"), dict) else {}
    blocked_cards = [b for b in (board.get("blocked") or []) if isinstance(b, dict)]
    board_as_of = board.get("as_of")
    board_fresh = float(board.get("fresh_for_hours") or DOMAIN_FRESH_HOURS["workflow"])

    items: list[dict[str, Any]] = []
    unknown = False

    for gate in gates:
        row = {
            "id": gate.get("id"),
            "title": gate.get("title") or gate.get("id"),
            "detail": gate.get("detail") or "",
            "domain": gate.get("domain"),
            "severity": gate.get("severity"),
            "as_of": gate.get("as_of"),
            "source": "gate",
            "deep_link": gate.get("deep_link"),
            "fresh_for_hours": gate.get("fresh_for_hours") or _fresh_window_hours(gate),
        }
        if is_falsifiable_fact(row, now=ref):
            items.append(row)
        else:
            unknown = True

    for card in blocked_cards:
        row = {
            "id": card.get("number") or card.get("id"),
            "title": card.get("title") or card.get("reason") or "blocked",
            "detail": card.get("reason") or card.get("detail") or "",
            "domain": "workflow",
            "severity": "warn",
            "as_of": card.get("as_of") or board_as_of,
            "source": "workflow.blocked",
            "deep_link": board.get("deep_link") or wf.get("url") or WORKFLOW_URL,
            "fresh_for_hours": board_fresh,
        }
        if is_falsifiable_fact(row, now=ref):
            items.append(row)
        else:
            unknown = True

    if not items:
        status = "unknown" if (gates or blocked_cards or unknown or not gates) else "clear"
        if not gates and not blocked_cards:
            status = "unknown"
        return {
            "status": status,
            "items": [],
            "timeline": False,
            "note": (
                "unknown — no clock or same-civil-day log in a fresh window"
                if status == "unknown"
                else ""
            ),
        }

    return {
        "status": "blocked",
        "items": items,
        "timeline": False,
        "note": "",
    }


def _train_line(fitness_src: dict[str, Any]) -> Optional[str]:
    """Seat fact: Train: {session_type|Rest} · cue. Not “Train train” / fake PPL."""
    rec = fitness_src.get("train_recommendation")
    if rec is None or fitness_src.get("stale"):
        return None
    rec_text = str(rec).strip()
    if not rec_text:
        return None
    session = str(fitness_src.get("session_type") or "").strip()
    slot = session if session else "Rest"
    cue = str(fitness_src.get("recovery_label") or "").strip() or rec_text
    return f"Train: {slot} · {cue}"


def _meal_line(fitness_src: dict[str, Any], *, now: datetime) -> Optional[str]:
    band = fitness_src.get("protein_gap_band")
    protein_as_of = fitness_src.get("protein_as_of") or fitness_src.get("as_of")
    if band in (None, "", "unknown") or fitness_src.get("stale"):
        return None
    if not same_civil_day(protein_as_of, now=now):
        return None
    remaining = fitness_src.get("protein_remaining_g")
    if band == "gap":
        extra = f" · remaining≈{remaining}g" if remaining is not None else ""
        return f"Protein gap{extra}"
    if band == "watch":
        extra = f" · remaining≈{remaining}g" if remaining is not None else ""
        return f"Protein watch{extra}"
    pantry = fitness_src.get("pantry") or fitness_src.get("meals")
    if isinstance(pantry, dict) and pantry.get("summary") and is_falsifiable_fact(pantry, now=now):
        return str(pantry.get("summary"))
    if band == "ok" and remaining is not None:
        return f"Protein ok · remaining≈{remaining}g"
    return None


def _cadence_line(workflow_src: dict[str, Any]) -> Optional[str]:
    """Optional Cadence fact. Omit unless Ready and free-agent counts are real."""
    if workflow_src.get("stale") or workflow_src.get("fetch_ok") is False:
        return None
    ready = workflow_src.get("ready_count")
    free = workflow_src.get("free_agent_count")
    if ready is None or free is None:
        return None
    try:
        ready_n = int(ready)
        free_n = int(free)
    except (TypeError, ValueError):
        return None
    return f"Cadence: {ready_n} Ready · {free_n} free"


def _quest_lines(fitness_src: dict[str, Any]) -> list[str]:
    raw = fitness_src.get("quests") or []
    if not isinstance(raw, list):
        return []
    lines: list[str] = []
    for q in raw:
        if not keep_action_item(q if isinstance(q, dict) else None):
            continue
        title = str(q.get("title") or q.get("action") or "").strip()
        if title:
            lines.append(title)
    return lines


def build_one_liners(
    day_plan: Optional[dict[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Optional seat facts only. Omit if unknown. Do not invent."""
    ref = _utc_now(now)
    plan = day_plan if isinstance(day_plan, dict) else {}
    sources = plan.get("sources") if isinstance(plan.get("sources"), dict) else {}
    fit = sources.get("fitness") if isinstance(sources.get("fitness"), dict) else {}
    out: list[dict[str, Any]] = []

    train = _train_line(fit)
    if train:
        out.append({"id": "train", "text": train})

    meal = _meal_line(fit, now=ref)
    if meal:
        out.append({"id": "meals", "text": meal})

    for title in _quest_lines(fit):
        out.append({"id": "quest", "text": title})

    wf = sources.get("workflow") if isinstance(sources.get("workflow"), dict) else {}
    cadence = _cadence_line(wf)
    if cadence:
        out.append({"id": "cadence", "text": cadence})

    return out


def personal_next3(next3: Optional[list[Any]]) -> list[dict[str, Any]]:
    """Chris-facing NOW/NEXT list — no board pull/ready jargon, no placeholders."""
    return [x for x in (next3 or []) if isinstance(x, dict) and keep_action_item(x)]


def now_from_next3(next3: Optional[list[Any]]) -> Optional[dict[str, Any]]:
    rows = personal_next3(next3)
    return rows[0] if rows else None


def build_pulse(
    *,
    day_plan: Optional[dict[str, Any]] = None,
    domains: Optional[list[dict[str, Any]]] = None,
    fan_in: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Assemble the thin personal-window pulse for one :8790 load."""
    ref = _utc_now(now)
    plan = day_plan if isinstance(day_plan, dict) else {}
    next3 = personal_next3(plan.get("next3") or [])
    by_id = {
        str(d.get("id")): d
        for d in (domains or [])
        if isinstance(d, dict) and d.get("id")
    }
    return {
        "schema_version": 1,
        "world": build_world(fan_in),
        "now": now_from_next3(next3),
        "next": next3,
        "blocked": build_blocked(plan, workflow=by_id.get("workflow"), now=ref),
        "one_liners": build_one_liners(plan, now=ref),
        "dock": build_dock(domains),
        "meta": {
            "timezone": "America/New_York",
            "primary": "now_next_blocked",
            "non_goals": [
                "no WEEK/GATES/HELD chrome",
                "no recommendations as NOW/NEXT",
                "no Buzz-board pull/ready jargon as NOW/NEXT",
                "no Horizon embed or dock tile",
                "no FCC/FitDash/Fleet/B2/IoT dock tiles",
            ],
        },
    }


def next_api_payload(orchestra: dict[str, Any]) -> dict[str, Any]:
    """GET /api/next body — personal next3, never recommendations or board jargon."""
    plan = orchestra.get("day_plan") if isinstance(orchestra, dict) else {}
    next3 = personal_next3((plan or {}).get("next3") or [])
    return {
        "ok": True,
        "next": next3,
        "next3": next3,
    }


def now_api_payload(orchestra: dict[str, Any]) -> tuple[int, Optional[dict[str, Any]]]:
    """GET /api/now — next3[0] or 204."""
    plan = orchestra.get("day_plan") if isinstance(orchestra, dict) else {}
    first = now_from_next3((plan or {}).get("next3") or [])
    if first is None:
        return 204, None
    return 200, {"ok": True, "now": first}
