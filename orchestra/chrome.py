"""Orchestra v1 chrome: WORLD / WEEK / GATES / HELD around existing day_plan + dock.

Mode source of truth lives in ``orchestra/mode.json`` (America/New_York).
Pure builders — no Google Calendar writes, no clock-hour bands, no 4th app.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

try:
    from .fan_in import build_fan_in
except ImportError:  # unittest path insert
    from fan_in import build_fan_in

ORCHESTRA_DIR = Path(__file__).resolve().parent
MODE_CONFIG_PATH = ORCHESTRA_DIR / "mode.json"

WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
HINGE_MODES = frozenset({"hinge_a", "hinge_b"})
CLOSED_HINT = "not this window"

DEFAULT_HORIZON_PORT = 8795
DEFAULT_ALLOCATOR_URL = "http://127.0.0.1:8770/"
DEFAULT_WORKFLOW_URL = "http://127.0.0.1:8765/"


def load_mode_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Load Mode SoT. Missing/invalid file falls back to the shipped weekday map."""
    p = Path(path) if path is not None else MODE_CONFIG_PATH
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("weekday_modes"), dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "timezone": "America/New_York",
        "weekday_modes": {
            "mon": "slow",
            "tue": "slow",
            "wed": "hinge_a",
            "thu": "busy",
            "fri": "busy",
            "sat": "busy",
            "sun": "hinge_b",
        },
        "mode_labels": {
            "slow": "Slow",
            "busy": "Busy",
            "hinge_a": "Hinge A",
            "hinge_b": "Hinge B",
        },
        "transitions": [
            {
                "id": "sunday_into_monday",
                "mode": "hinge_b",
                "from_weekday": "sun",
                "into_weekday": "mon",
                "note": "Sunday-into-Monday is always Hinge B.",
            }
        ],
        "personal_chips": [{"date": "2026-08-25", "label": "Service center"}],
        "gates": [
            {"id": "drive", "label": "Drive"},
            {"id": "sleep", "label": "Sleep"},
            {"id": "desk", "label": "Desk"},
            {
                "id": "hinge_buffer",
                "label": "Hinge buffer",
                "style": "dashed",
                "never_red": True,
            },
        ],
        "gate_open_by_mode": {
            "slow": ["drive", "sleep", "desk"],
            "busy": ["drive", "sleep", "desk"],
            "hinge_a": ["hinge_buffer"],
            "hinge_b": ["hinge_buffer"],
        },
        "horizon": {"port": DEFAULT_HORIZON_PORT, "path": "/", "label": "Horizon"},
        "dock": [
            {"id": "holistic", "label": "Time Allocator", "port": 8770, "path": "/"},
            {"id": "workflow", "label": "Workflow", "port": 8765, "path": "/"},
        ],
    }


def timezone_name(config: Optional[dict[str, Any]] = None) -> str:
    cfg = config if isinstance(config, dict) else {}
    tz = str(cfg.get("timezone") or "America/New_York").strip()
    return tz or "America/New_York"


def local_now(
    now: Optional[datetime] = None,
    *,
    config: Optional[dict[str, Any]] = None,
) -> datetime:
    tz = ZoneInfo(timezone_name(config))
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc).astimezone(tz)
    return now.astimezone(tz)


def weekday_key(day: date | datetime) -> str:
    return WEEKDAY_KEYS[day.weekday()]


def mode_label(mode_id: str, config: Optional[dict[str, Any]] = None) -> str:
    cfg = config if isinstance(config, dict) else {}
    labels = cfg.get("mode_labels") if isinstance(cfg.get("mode_labels"), dict) else {}
    fallback = {
        "slow": "Slow",
        "busy": "Busy",
        "hinge_a": "Hinge A",
        "hinge_b": "Hinge B",
    }
    return str(labels.get(mode_id) or fallback.get(mode_id) or mode_id)


def weekday_mode(key: str, config: Optional[dict[str, Any]] = None) -> str:
    cfg = config if isinstance(config, dict) else load_mode_config()
    modes = cfg.get("weekday_modes") if isinstance(cfg.get("weekday_modes"), dict) else {}
    return str(modes.get(key) or "slow")


def sunday_into_monday_note(config: Optional[dict[str, Any]] = None) -> str:
    cfg = config if isinstance(config, dict) else {}
    for item in cfg.get("transitions") or []:
        if isinstance(item, dict) and item.get("id") == "sunday_into_monday":
            return str(item.get("note") or "Sunday-into-Monday is always Hinge B.")
    return "Sunday-into-Monday is always Hinge B. Hinges are offset on purpose."


def resolve_mode(
    now: Optional[datetime] = None,
    *,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Civil-day mode in America/New_York. No clock-hour bands."""
    cfg = config if isinstance(config, dict) else load_mode_config()
    local = local_now(now, config=cfg)
    key = weekday_key(local)
    mode_id = weekday_mode(key, cfg)
    is_hinge = mode_id in HINGE_MODES
    return {
        "id": mode_id,
        "label": mode_label(mode_id, cfg),
        "weekday": key,
        "date": local.date().isoformat(),
        "timezone": timezone_name(cfg),
        "is_hinge": is_hinge,
        "hinge_kind": "A" if mode_id == "hinge_a" else ("B" if mode_id == "hinge_b" else None),
        "sunday_into_monday": "hinge_b",
        "sunday_into_monday_note": sunday_into_monday_note(cfg),
        "hinges_offset": True,
        "success_empty": is_hinge,
        "success_note": (
            "Hinge day — short or empty is success, not a failed day."
            if is_hinge
            else ""
        ),
    }


def _personal_chips_for_date(
    day: date,
    config: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    cfg = config if isinstance(config, dict) else {}
    out: list[dict[str, Any]] = []
    for chip in cfg.get("personal_chips") or []:
        if not isinstance(chip, dict):
            continue
        raw = str(chip.get("date") or "").strip()
        if raw != day.isoformat():
            continue
        label = str(chip.get("label") or "").strip()
        if not label:
            continue
        out.append({"date": raw, "label": label})
    return out


def build_week(
    now: Optional[datetime] = None,
    *,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Mon–Sun personal week. No hour grid. Personal chips only when dated and real."""
    cfg = config if isinstance(config, dict) else load_mode_config()
    local = local_now(now, config=cfg)
    today = local.date()
    monday = today - timedelta(days=today.weekday())
    days: list[dict[str, Any]] = []
    for i, key in enumerate(WEEKDAY_KEYS):
        day = monday + timedelta(days=i)
        mode_id = weekday_mode(key, cfg)
        days.append(
            {
                "weekday": key,
                "weekday_label": key[:3].capitalize() if key != "thu" else "Thu",
                "date": day.isoformat(),
                "mode": mode_id,
                "label": mode_label(mode_id, cfg),
                "today": day == today,
                "is_hinge": mode_id in HINGE_MODES,
                "chips": _personal_chips_for_date(day, cfg),
            }
        )
    return {
        "timezone": timezone_name(cfg),
        "week_start": monday.isoformat(),
        "today": today.isoformat(),
        "hinges_offset": True,
        "sunday_into_monday": "hinge_b",
        "note": "Mon–Tue Slow · Wed Hinge A · Thu–Sat Busy · Sun Hinge B (Sunday-into-Monday).",
        "days": days,
    }


def build_gates(
    mode: Optional[dict[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Drive / Sleep / Desk / Hinge buffer. Open = filled. Closed = outline + hint.

    Hinge buffer is dashed and never red. Hinge days open only the buffer.
    """
    cfg = config if isinstance(config, dict) else load_mode_config()
    resolved = mode if isinstance(mode, dict) else resolve_mode(now, config=cfg)
    mode_id = str(resolved.get("id") or "slow")
    open_map = cfg.get("gate_open_by_mode") if isinstance(cfg.get("gate_open_by_mode"), dict) else {}
    open_ids = {
        str(x)
        for x in (open_map.get(mode_id) or [])
        if x
    }
    specs = cfg.get("gates") if isinstance(cfg.get("gates"), list) else []
    items: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, dict) or not spec.get("id"):
            continue
        gid = str(spec["id"])
        opened = gid in open_ids
        style = str(spec.get("style") or ("dashed" if gid == "hinge_buffer" else "solid"))
        never_red = bool(spec.get("never_red")) or gid == "hinge_buffer"
        items.append(
            {
                "id": gid,
                "label": str(spec.get("label") or gid),
                "open": opened,
                "hint": "" if opened else CLOSED_HINT,
                "style": style,
                "never_red": never_red,
            }
        )
    return {
        "mode": mode_id,
        "mode_label": resolved.get("label") or mode_label(mode_id, cfg),
        "is_hinge": bool(resolved.get("is_hinge")),
        "success_empty": bool(resolved.get("success_empty")),
        "success_note": resolved.get("success_note") or "",
        "items": items,
    }


def horizon_url(config: Optional[dict[str, Any]] = None) -> str:
    cfg = config if isinstance(config, dict) else {}
    hz = cfg.get("horizon") if isinstance(cfg.get("horizon"), dict) else {}
    port = int(hz.get("port") or DEFAULT_HORIZON_PORT)
    path = str(hz.get("path") or "/")
    if not path.startswith("/"):
        path = "/" + path
    return f"http://127.0.0.1:{port}{path}"


def build_world(
    fan_in: Optional[dict[str, Any]] = None,
    *,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """One-line read-only Horizon deep-link. Placeholder if no regime feed yet."""
    cfg = config if isinstance(config, dict) else load_mode_config()
    fan = fan_in if isinstance(fan_in, dict) else {}
    regime = fan.get("regime") if isinstance(fan.get("regime"), dict) else {}
    imps = fan.get("implications") if isinstance(fan.get("implications"), dict) else {}
    top = imps.get("top") if isinstance(imps.get("top"), list) else []
    first = top[0] if top and isinstance(top[0], dict) else {}
    label = regime.get("primary_label") if regime.get("available") else None
    action = first.get("action") if first else None
    if label and action:
        line = f"{label} · {action}"
        placeholder = False
    elif label:
        line = str(label)
        placeholder = False
    else:
        line = "Horizon — open for world regime"
        placeholder = True
    return {
        "line": line,
        "placeholder": placeholder,
        "url": horizon_url(cfg),
        "port": int((cfg.get("horizon") or {}).get("port") or DEFAULT_HORIZON_PORT)
        if isinstance(cfg.get("horizon"), dict)
        else DEFAULT_HORIZON_PORT,
        "label": "WORLD",
        "opens": "horizon",
        "embed": False,
        "note": "Read-only nest: Horizon is the world superset. Click opens :8795.",
    }


def build_held(bridge: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Thin Workflow → Holistic today strip. Not a second sprint board."""
    br = bridge if isinstance(bridge, dict) else {}
    items: list[dict[str, Any]] = []
    for raw, kind in (
        (br.get("candidates") or [], "candidate"),
        (br.get("linked") or [], "linked"),
    ):
        if not isinstance(raw, list):
            continue
        for row in raw:
            if not isinstance(row, dict):
                continue
            title = row.get("title") or row.get("id") or row.get("backlog_id")
            if not title:
                continue
            items.append(
                {
                    "id": str(row.get("backlog_id") or row.get("id") or title)[:80],
                    "title": str(title)[:160],
                    "kind": kind,
                    "status": row.get("status"),
                    "schedule_label": row.get("schedule_label"),
                }
            )
            if len(items) >= 6:
                break
        if len(items) >= 6:
            break
    return {
        "note": br.get("note")
        or "Workflow → Holistic today. Thin hold strip — not a sprint board.",
        "items": items,
        "workflow_url": br.get("workflow_url") or DEFAULT_WORKFLOW_URL,
        "allocator_url": br.get("allocator_url") or DEFAULT_ALLOCATOR_URL,
        "send_hint": br.get("send_hint") or "In Workflow: Send to today",
        "kind": "workflow_to_holistic_today",
    }


def build_dock(
    domains: Optional[list[dict[str, Any]]] = None,
    *,
    config: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Personal-window dock: Time Allocator :8770 and Workflow :8765 only."""
    cfg = config if isinstance(config, dict) else load_mode_config()
    specs = cfg.get("dock") if isinstance(cfg.get("dock"), list) else []
    by_id = {
        str(d.get("id")): d
        for d in (domains or [])
        if isinstance(d, dict) and d.get("id")
    }
    out: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, dict) or not spec.get("id"):
            continue
        did = str(spec["id"])
        port = int(spec.get("port") or 0)
        path = str(spec.get("path") or "/")
        if not path.startswith("/"):
            path = "/" + path
        domain = by_id.get(did) or {}
        url = domain.get("url") or f"http://127.0.0.1:{port}{path}"
        out.append(
            {
                "id": did,
                "label": str(spec.get("label") or domain.get("label") or did),
                "port": port,
                "url": url,
                "live": domain.get("live"),
                "stale": domain.get("stale"),
                "age_hours": domain.get("age_hours"),
                "status": domain.get("status"),
            }
        )
    return out


def build_chrome(
    *,
    domains: Optional[list[dict[str, Any]]] = None,
    bridge: Optional[dict[str, Any]] = None,
    fan_in: Optional[dict[str, Any]] = None,
    workspace: Optional[Path] = None,
    now: Optional[datetime] = None,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble v1 chrome for one :8790 load."""
    cfg = config if isinstance(config, dict) else load_mode_config()
    fan = fan_in if isinstance(fan_in, dict) else build_fan_in(workspace)
    mode = resolve_mode(now, config=cfg)
    return {
        "schema_version": 1,
        "mode": mode,
        "world": build_world(fan, config=cfg),
        "week": build_week(now, config=cfg),
        "gates": build_gates(mode, now=now, config=cfg),
        "held": build_held(bridge),
        "dock": build_dock(domains, config=cfg),
        "meta": {
            "timezone": timezone_name(cfg),
            "nest": {
                "horizon": "world superset (WORLD strip only)",
                "orchestra": "personal window",
                "children": "Time Allocator + Workflow dock; further subsets stay in their apps",
            },
            "non_goals": [
                "no clock-hour bands",
                "no Google Calendar write",
                "no Income/Self Care recreation",
                "no Horizon embed or dock tile",
                "no FCC/FitDash/IoT/B2/Seasonal/auto-fleet fold",
            ],
        },
    }
