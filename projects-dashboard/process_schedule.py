#!/usr/bin/env python3
"""Schedule / Process tab: live Buzz workflows + day/week process flow (#61).

Source of truth (in order):
  1. Live `buzz workflows list` for known ceremony channels (when buzz + auth work)
  2. Cadence/ops snapshot at ops/process/workflows_snapshot.json
  3. Never invent a second hard-coded HTML ceremony list in the UI

Dashboard is read-only: no create/edit/trigger of workflows from this API.
"""

from __future__ import annotations

import calendar
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from workspace import WORKSPACE_ROOT

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

SCHEMA_VERSION = 1

# Known ceremony channels (UUIDs from GUIDES/CADENCE_SCRUM_CEREMONIES.md)
CHANNEL_WORKFLOW = "db0e8f97-0c81-4976-b299-1c460b87134e"
CHANNEL_STANDUP = "1bafc96c-299a-48ef-aff6-4b6190e643e4"

DEFAULT_CHANNELS: list[dict[str, str]] = [
    {"id": CHANNEL_WORKFLOW, "name": "#workflow", "slug": "workflow"},
    {"id": CHANNEL_STANDUP, "name": "#standup", "slug": "standup"},
]

# Short pubkey → display name for kick targets (prefix match on 16+ hex)
AGENT_PUBKEY_PREFIXES: dict[str, str] = {
    "0092be61e5d369d7": "Cadence",
    "213349578fbf53a2": "Grok",
    "f3414245e8bb0b9b": "Forge",
    "f379e967b0e386b2": "Frankenfit",
    "c54a48a274943cf4": "ChrisV.btc⚡",
}

# @mentions that count as real kick targets (templates use @Primary / @Owner as placeholders)
KNOWN_KICK_NAMES = frozenset(
    {
        "Cadence",
        "Grok",
        "Forge",
        "Frankenfit",
        "Meridian",
        "Nakatoshi",
        "Assay",
        "Honey",
        "Bumble",
        "Fizz",
        "Fizzbuzz",
        "Volt",
        "ChrisV.btc⚡",
        "ChrisV.btc",
    }
)
# Placeholder / role words in kick text that are not agents
KICK_PLACEHOLDERS = frozenset(
    {
        "primary",
        "owner",
        "chris",  # prefer ChrisV.btc⚡ from pubkey
        "assignee",
        "agent",
        "everyone",
        "all",
    }
)

# Known continuous-flow workflows (for process-flow graph labels / order)
CORE_FLOW_NAMES = (
    "cadence-daily-status",
    "eng-gate-sweep",
    "cadence-daily-replenish",
    "cadence-deep-groom",
    "autonomous-backlog-harvest",
)

# Day/week process edges (logical chain — not a second SoT for schedules)
PROCESS_FLOW_EDGES: list[dict[str, str]] = [
    {
        "from": "cadence-daily-status",
        "to": "eng-gate-sweep",
        "label": "Needs Grok feeds eng-gate queue",
    },
    {
        "from": "autonomous-backlog-harvest",
        "to": "cadence-daily-replenish",
        "label": "Harvest → mini-replenish if Ready=0 (Lock D)",
    },
    {
        "from": "cadence-deep-groom",
        "to": "cadence-daily-replenish",
        "label": "Deep groom may light-pull free agents",
    },
    {
        "from": "cadence-daily-replenish",
        "to": "eng-gate-sweep",
        "label": "Assign → implement → PR → eng-gate",
    },
    {
        "from": "eng-gate-sweep",
        "to": "cadence-daily-replenish",
        "label": "MERGE frees agent WIP for next Ready",
    },
]

PROCESS_FLOW_NODES_EXTRA: list[dict[str, Any]] = [
    {
        "id": "assay-system-slice-qa",
        "name": "Assay system-slice QA",
        "kind": "ceremony",
        "schedule_human": "Monday (post-harvest if quiet)",
        "channel_name": "#workflow",
        "kick_targets": ["Cadence", "Assay"],
        "notes": "Not a Buzz cron workflow — Cadence kicks manually. Cap ≤3 Parked ideas.",
        "status": "active",
    },
]

MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9._⚡-]*)")
CRON_FIELD_RE = re.compile(r"^[\d*/,\-]+$")


def snapshot_path(workspace: Path | None = None) -> Path:
    root = workspace or WORKSPACE_ROOT
    return root / "ops" / "process" / "workflows_snapshot.json"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_agent_name(pubkey: str) -> Optional[str]:
    if not pubkey:
        return None
    p = str(pubkey).lower().strip()
    for prefix, name in AGENT_PUBKEY_PREFIXES.items():
        if p.startswith(prefix.lower()):
            return name
    return None


def _normalize_kick_target(raw: str) -> Optional[str]:
    """Keep real agent names; drop instructional placeholders like @Primary."""
    if not raw:
        return None
    name = str(raw).strip().lstrip("@")
    if not name:
        return None
    low = name.lower()
    if low in KICK_PLACEHOLDERS:
        return None
    # Exact known
    for known in KNOWN_KICK_NAMES:
        if name == known or low == known.lower():
            return known
    # Prefix match (e.g. ChrisV)
    for known in KNOWN_KICK_NAMES:
        if low.startswith(known.lower()[:6]) and len(name) >= 4:
            return known
    return None


def _status_for_name(name: str) -> str:
    n = (name or "").strip().lower()
    if n.startswith("zzz-") or "retired" in n or "inert" in n:
        return "inert"
    if n in ("probe",) or n.startswith("probe"):
        return "inert"
    return "active"


def _parse_workflow_yaml(content: str) -> dict[str, Any]:
    """Parse workflow content YAML → name, cron, mentions, channel overrides."""
    out: dict[str, Any] = {
        "name": None,
        "cron": None,
        "trigger_on": None,
        "kick_pubkeys": [],
        "kick_targets": [],
        "step_channel": None,
        "text_excerpt": None,
    }
    if not content or not str(content).strip():
        return out

    data: Any = None
    if yaml is not None:
        try:
            data = yaml.safe_load(content)
        except Exception:
            data = None

    if isinstance(data, dict):
        out["name"] = data.get("name")
        trig = data.get("trigger") or {}
        if isinstance(trig, dict):
            out["trigger_on"] = trig.get("on")
            out["cron"] = trig.get("cron")
        steps = data.get("steps") or []
        pubkeys: list[str] = []
        texts: list[str] = []
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                ch = step.get("channel")
                if ch and not out["step_channel"]:
                    out["step_channel"] = str(ch)
                ments = step.get("mentions") or []
                if isinstance(ments, list):
                    for m in ments:
                        if m is not None:
                            pubkeys.append(str(m))
                text = step.get("text")
                if text:
                    texts.append(str(text))
        out["kick_pubkeys"] = pubkeys
        if texts:
            out["text_excerpt"] = texts[0][:400]
            # @Name from kick text (filtered)
            for t in texts:
                for m in MENTION_RE.findall(t):
                    nm = _normalize_kick_target(m)
                    if nm and nm not in out["kick_targets"]:
                        out["kick_targets"].append(nm)
        for pk in pubkeys:
            nm = _resolve_agent_name(pk)
            if nm and nm not in out["kick_targets"]:
                out["kick_targets"].append(nm)
        return out

    # Fallback: lightweight regex if yaml missing/invalid
    m_name = re.search(r"(?m)^name:\s*[\"']?([^\n\"']+)", content)
    if m_name:
        out["name"] = m_name.group(1).strip()
    m_cron = re.search(r"(?m)^\s*cron:\s*[\"']([^\"']+)[\"']", content)
    if m_cron:
        out["cron"] = m_cron.group(1).strip()
    for m in MENTION_RE.findall(content):
        nm = _normalize_kick_target(m)
        if nm and nm not in out["kick_targets"]:
            out["kick_targets"].append(nm)
    out["text_excerpt"] = content[:400]
    return out


def _expand_cron_field(field: str, min_v: int, max_v: int) -> set[int]:
    """Expand a single 5-field cron token to allowed integers."""
    field = field.strip()
    if field == "*":
        return set(range(min_v, max_v + 1))
    out: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            try:
                step = max(1, int(step_s))
            except ValueError:
                step = 1
            part = base if base else "*"
        if part == "*":
            out.update(range(min_v, max_v + 1, step))
            continue
        if "-" in part:
            a_s, b_s = part.split("-", 1)
            try:
                a, b = int(a_s), int(b_s)
            except ValueError:
                continue
            a = max(min_v, min(a, max_v))
            b = max(min_v, min(b, max_v))
            out.update(range(a, b + 1, step))
            continue
        try:
            v = int(part)
        except ValueError:
            continue
        if min_v <= v <= max_v and (v - min_v) % step == 0:
            out.add(v)
    return out or set(range(min_v, max_v + 1))


def cron_matches(dt: datetime, cron: str) -> bool:
    """True if dt (minute resolution, UTC-aware preferred) matches 5-field cron."""
    parts = (cron or "").strip().split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    mins = _expand_cron_field(minute, 0, 59)
    hours = _expand_cron_field(hour, 0, 23)
    months = _expand_cron_field(month, 1, 12)
    doms = _expand_cron_field(dom, 1, 31)
    # cron DOW: 0=Sun … 6=Sat (also 7=Sun in some dialects)
    dows_raw = _expand_cron_field(dow, 0, 7)
    dows = set()
    for d in dows_raw:
        if d == 7:
            dows.add(0)
        else:
            dows.add(d)

    if dt.minute not in mins or dt.hour not in hours or dt.month not in months:
        return False
    # Day-of-month vs day-of-week: if either is *, the other constrains;
    # if both constrained, standard cron ORs them.
    dom_star = dom.strip() == "*"
    dow_star = dow.strip() == "*"
    # Python weekday: Mon=0 … Sun=6 → cron Sun=0
    cron_dow = (dt.weekday() + 1) % 7
    if dom_star and dow_star:
        return True
    if not dom_star and dow_star:
        return dt.day in doms
    if dom_star and not dow_star:
        return cron_dow in dows
    return dt.day in doms or cron_dow in dows


def next_cron_fire(
    cron: str,
    after: Optional[datetime] = None,
    *,
    max_days: int = 400,
) -> Optional[datetime]:
    """Next UTC minute (inclusive of after+1m) matching cron, or None."""
    if not cron or len(cron.strip().split()) != 5:
        return None
    start = after or _utcnow()
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    else:
        start = start.astimezone(timezone.utc)
    # Start from the next full minute
    cur = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = cur + timedelta(days=max_days)
    # Fast path: if every 15 min pattern, jump smartly later if needed
    while cur <= limit:
        if cron_matches(cur, cron):
            return cur
        cur += timedelta(minutes=1)
    return None


def humanize_cron(cron: str) -> str:
    """Short human summary of common ceremony crons (UTC)."""
    c = (cron or "").strip()
    known = {
        "0 13 * * *": "Daily 13:00 UTC (~9:00 America/New_York EDT)",
        "0 14 * * *": "Daily 14:00 UTC (~9:00 America/New_York EST)",
        "0 16 * * *": "Daily 16:00 UTC",
        "0 16 * * 3": "Wednesdays 16:00 UTC",
        "0 17 * * 1,4": "Mon + Thu 17:00 UTC",
        "*/15 * * * *": "Every 15 minutes",
        "0 0 1 1 *": "Yearly Jan 1 00:00 UTC (inert probe)",
    }
    if c in known:
        return known[c]
    parts = c.split()
    if len(parts) != 5:
        return c or "—"
    return f"cron `{c}` (UTC)"


def format_local(dt: Optional[datetime], tz_name: str = "America/New_York") -> Optional[str]:
    if dt is None:
        return None
    try:
        from zoneinfo import ZoneInfo  # py3.9+

        local = dt.astimezone(ZoneInfo(tz_name))
        return local.strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return _iso(dt)


def load_snapshot(workspace: Path | None = None) -> dict[str, Any]:
    path = snapshot_path(workspace)
    if not path.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "workflows": [],
            "_path": str(path),
            "_exists": False,
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {
            "schema_version": SCHEMA_VERSION,
            "workflows": [],
            "_path": str(path),
            "_exists": True,
            "_error": str(e),
        }
    if not isinstance(raw, dict):
        return {
            "schema_version": SCHEMA_VERSION,
            "workflows": [],
            "_path": str(path),
            "_exists": True,
            "_error": "snapshot is not an object",
        }
    raw["_path"] = str(path)
    raw["_exists"] = True
    if not isinstance(raw.get("workflows"), list):
        raw["workflows"] = []
    return raw


def _normalize_row(
    *,
    workflow_id: str,
    content: str,
    channel_id: str,
    channel_name: str,
    created_at: Any = None,
    pubkey: str | None = None,
) -> dict[str, Any]:
    parsed = _parse_workflow_yaml(content)
    name = (parsed.get("name") or "").strip() or f"workflow-{workflow_id[:8]}"
    status = _status_for_name(name)
    cron = parsed.get("cron")
    # Prefer explicit step channel if set
    ch_id = parsed.get("step_channel") or channel_id
    ch_name = channel_name
    for ch in DEFAULT_CHANNELS:
        if ch["id"] == ch_id:
            ch_name = ch["name"]
            break
    return {
        "id": workflow_id,
        "name": name,
        "cron": cron,
        "schedule_human": humanize_cron(cron) if cron else "—",
        "channel_id": ch_id,
        "channel_name": ch_name,
        "kick_targets": parsed.get("kick_targets") or [],
        "kick_pubkeys": parsed.get("kick_pubkeys") or [],
        "status": status,
        "trigger_on": parsed.get("trigger_on") or "schedule",
        "created_at": created_at,
        "pubkey": pubkey,
        "text_excerpt": parsed.get("text_excerpt"),
    }


def fetch_live_workflows(
    channels: Optional[list[dict[str, str]]] = None,
    *,
    buzz_bin: Optional[str] = None,
    timeout: float = 12.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Call `buzz workflows list --channel` for each ceremony channel.

    Returns (rows, errors). Empty rows + error notes when buzz unavailable.
    """
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    bin_path = buzz_bin or shutil.which("buzz") or os.environ.get("BUZZ_BIN")
    if not bin_path:
        errors.append("buzz CLI not found on PATH — using snapshot only")
        return rows, errors

    for ch in channels or DEFAULT_CHANNELS:
        ch_id = ch["id"]
        ch_name = ch.get("name") or ch_id
        try:
            proc = subprocess.run(
                [bin_path, "workflows", "list", "--channel", ch_id],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            errors.append(f"{ch_name}: buzz list failed: {e}")
            continue
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip()[:300]
            errors.append(f"{ch_name}: buzz exit {proc.returncode}: {msg or 'error'}")
            continue
        try:
            data = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as e:
            errors.append(f"{ch_name}: invalid JSON from buzz: {e}")
            continue
        if not isinstance(data, list):
            errors.append(f"{ch_name}: expected list, got {type(data).__name__}")
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            wid = item.get("workflow_id") or item.get("id")
            if not wid:
                continue
            content = item.get("content") or ""
            if isinstance(content, dict):
                # already structured
                content = yaml.dump(content) if yaml else json.dumps(content)
            rows.append(
                _normalize_row(
                    workflow_id=str(wid),
                    content=str(content),
                    channel_id=ch_id,
                    channel_name=ch_name,
                    created_at=item.get("created_at"),
                    pubkey=item.get("pubkey"),
                )
            )
    return rows, errors


def rows_from_snapshot(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize snapshot workflow entries into UI rows."""
    out: list[dict[str, Any]] = []
    for item in snap.get("workflows") or []:
        if not isinstance(item, dict):
            continue
        # Already normalized rows
        if item.get("name") and item.get("id") and "content" not in item:
            row = dict(item)
            row.setdefault("status", _status_for_name(str(row.get("name") or "")))
            if row.get("cron") and not row.get("schedule_human"):
                row["schedule_human"] = humanize_cron(str(row["cron"]))
            out.append(row)
            continue
        content = item.get("content") or ""
        wid = item.get("workflow_id") or item.get("id") or ""
        ch_id = item.get("channel_id") or CHANNEL_WORKFLOW
        ch_name = item.get("channel_name") or "#workflow"
        if content:
            out.append(
                _normalize_row(
                    workflow_id=str(wid),
                    content=str(content),
                    channel_id=str(ch_id),
                    channel_name=str(ch_name),
                    created_at=item.get("created_at"),
                    pubkey=item.get("pubkey"),
                )
            )
        elif wid:
            name = str(item.get("name") or f"workflow-{str(wid)[:8]}")
            cron = item.get("cron")
            out.append(
                {
                    "id": str(wid),
                    "name": name,
                    "cron": cron,
                    "schedule_human": humanize_cron(str(cron)) if cron else "—",
                    "channel_id": ch_id,
                    "channel_name": ch_name,
                    "kick_targets": item.get("kick_targets") or [],
                    "kick_pubkeys": item.get("kick_pubkeys") or [],
                    "status": item.get("status") or _status_for_name(name),
                    "trigger_on": item.get("trigger_on") or "schedule",
                    "created_at": item.get("created_at"),
                    "pubkey": item.get("pubkey"),
                    "text_excerpt": item.get("text_excerpt"),
                }
            )
    return out


def _clean_kick_targets(targets: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for t in targets or []:
        nm = _normalize_kick_target(str(t))
        if nm and nm not in out:
            out.append(nm)
    return out


def enrich_rows(
    rows: list[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    include_inert: bool = False,
) -> list[dict[str, Any]]:
    """Attach next fire times; optionally filter inert."""
    now = now or _utcnow()
    enriched: list[dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        status = r.get("status") or _status_for_name(str(r.get("name") or ""))
        r["status"] = status
        r["kick_targets"] = _clean_kick_targets(r.get("kick_targets") or [])
        if not include_inert and status == "inert":
            continue
        cron = r.get("cron")
        nxt = next_cron_fire(str(cron), after=now) if cron else None
        r["next_fire_utc"] = _iso(nxt)
        r["next_fire_local"] = format_local(nxt)
        r["is_core"] = str(r.get("name") or "") in CORE_FLOW_NAMES
        enriched.append(r)
    # Sort: core first (by next fire), then other actives, inert last if shown
    def sort_key(x: dict[str, Any]) -> tuple:
        status_rank = 0 if x.get("status") == "active" else 1
        core_rank = 0 if x.get("is_core") else 1
        nxt = x.get("next_fire_utc") or "9999"
        return (status_rank, core_rank, nxt, str(x.get("name") or ""))

    enriched.sort(key=sort_key)
    return enriched


def build_day_timeline(
    rows: list[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    hours: int = 24,
) -> list[dict[str, Any]]:
    """Upcoming fires in the next `hours` for active scheduled workflows."""
    now = now or _utcnow()
    end = now + timedelta(hours=hours)
    events: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "active":
            continue
        cron = row.get("cron")
        if not cron:
            continue
        # Skip high-frequency eng-gate for day strip density — still list first fire
        name = str(row.get("name") or "")
        if name == "eng-gate-sweep":
            nxt = next_cron_fire(str(cron), after=now)
            if nxt and nxt <= end:
                events.append(
                    {
                        "at_utc": _iso(nxt),
                        "at_local": format_local(nxt),
                        "name": name,
                        "id": row.get("id"),
                        "channel_name": row.get("channel_name"),
                        "kick_targets": row.get("kick_targets") or [],
                        "kind": "recurring",
                        "note": "then every 15m",
                    }
                )
            continue
        cursor = now
        # Cap occurrences per workflow in the window
        for _ in range(48):
            nxt = next_cron_fire(str(cron), after=cursor)
            if nxt is None or nxt > end:
                break
            events.append(
                {
                    "at_utc": _iso(nxt),
                    "at_local": format_local(nxt),
                    "name": name,
                    "id": row.get("id"),
                    "channel_name": row.get("channel_name"),
                    "kick_targets": row.get("kick_targets") or [],
                    "kind": "scheduled",
                }
            )
            cursor = nxt
    events.sort(key=lambda e: e.get("at_utc") or "")
    return events


def build_week_grid(
    rows: list[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Week view: which days each active ceremony fires (UTC weekday)."""
    now = now or _utcnow()
    # Monday-start week containing now
    monday = (now.astimezone(timezone.utc) - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    days = []
    for i in range(7):
        day = monday + timedelta(days=i)
        day_end = day + timedelta(days=1) - timedelta(minutes=1)
        day_events: list[dict[str, Any]] = []
        for row in rows:
            if row.get("status") != "active":
                continue
            cron = row.get("cron")
            name = str(row.get("name") or "")
            if not cron:
                continue
            if name == "eng-gate-sweep":
                day_events.append(
                    {
                        "name": name,
                        "id": row.get("id"),
                        "cron": cron,
                        "label": "every 15m",
                        "kick_targets": row.get("kick_targets") or [],
                    }
                )
                continue
            # Any fire on this calendar day?
            cursor = day - timedelta(minutes=1)
            nxt = next_cron_fire(str(cron), after=cursor)
            if nxt and day <= nxt <= day_end:
                day_events.append(
                    {
                        "name": name,
                        "id": row.get("id"),
                        "cron": cron,
                        "at_utc": _iso(nxt),
                        "at_local": format_local(nxt),
                        "kick_targets": row.get("kick_targets") or [],
                    }
                )
        days.append(
            {
                "date": day.strftime("%Y-%m-%d"),
                "weekday": calendar.day_abbr[day.weekday()],
                "is_today": day.date() == now.astimezone(timezone.utc).date(),
                "events": day_events,
            }
        )
    return days


def build_process_flow(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Graph of continuous-flow ceremonies (nodes from live/snapshot + extras)."""
    by_name = {str(r.get("name")): r for r in rows if r.get("name")}
    nodes: list[dict[str, Any]] = []
    for name in CORE_FLOW_NAMES:
        r = by_name.get(name)
        if r:
            nodes.append(
                {
                    "id": name,
                    "name": name,
                    "kind": "workflow",
                    "status": r.get("status"),
                    "cron": r.get("cron"),
                    "schedule_human": r.get("schedule_human"),
                    "channel_name": r.get("channel_name"),
                    "kick_targets": r.get("kick_targets") or [],
                    "workflow_id": r.get("id"),
                    "next_fire_local": r.get("next_fire_local"),
                }
            )
        else:
            nodes.append(
                {
                    "id": name,
                    "name": name,
                    "kind": "workflow",
                    "status": "missing",
                    "notes": "Not present in live list or snapshot",
                }
            )
    for extra in PROCESS_FLOW_NODES_EXTRA:
        nodes.append(dict(extra))

    return {
        "nodes": nodes,
        "edges": list(PROCESS_FLOW_EDGES),
        "narrative": [
            "Daily status (#standup) surfaces Needs Grok + free agents.",
            "Eng-gate sweep (15m) drains open PRs / Pending Review.",
            "Replenish pulls Ready → In Progress for free agents only (WIP=1).",
            "Deep groom (Wed) honesty-pass; may light-pull free agents.",
            "Harvest (Mon/Thu) proposes Parked/Validate only — never auto-Ready.",
            "Lock D: if harvest leaves Ready=0 with free implementers, same-day mini-replenish promotes 1–3 with AC.",
        ],
    }


def process_payload(
    workspace: Path | None = None,
    *,
    live: bool = True,
    include_inert: bool = False,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Full GET /api/process payload."""
    now = now or _utcnow()
    ws = workspace or WORKSPACE_ROOT
    snap = load_snapshot(ws)
    errors: list[str] = []
    source = "snapshot"
    live_rows: list[dict[str, Any]] = []

    if live:
        live_rows, live_errs = fetch_live_workflows()
        errors.extend(live_errs)
        if live_rows:
            source = "relay"
        elif not live_errs:
            errors.append("buzz returned no workflows — falling back to snapshot")

    if live_rows:
        rows = live_rows
    else:
        rows = rows_from_snapshot(snap)
        source = "snapshot" if snap.get("_exists") else "empty"
        if not rows:
            errors.append(
                "No workflows available (relay empty and no ops/process/workflows_snapshot.json)"
            )

    active_rows = enrich_rows(rows, now=now, include_inert=include_inert)
    # Always compute inert count from full set
    all_for_count = enrich_rows(rows, now=now, include_inert=True)
    inert_count = sum(1 for r in all_for_count if r.get("status") == "inert")
    active_count = sum(1 for r in all_for_count if r.get("status") == "active")

    core_present = [
        r["name"] for r in active_rows if r.get("name") in CORE_FLOW_NAMES
    ]

    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "generated_at": _iso(now),
        "timezone_note": "Relay cron is UTC. Local times shown as America/New_York.",
        "channels": DEFAULT_CHANNELS,
        "workflows": active_rows,
        "counts": {
            "shown": len(active_rows),
            "active": active_count,
            "inert": inert_count,
            "core_present": len(core_present),
            "core_expected": len(CORE_FLOW_NAMES),
        },
        "include_inert": include_inert,
        "day_timeline": build_day_timeline(active_rows, now=now, hours=24),
        "week": build_week_grid(active_rows, now=now),
        "process_flow": build_process_flow(all_for_count),
        "snapshot": {
            "path": snap.get("_path"),
            "exists": bool(snap.get("_exists")),
            "updated_at": snap.get("updated_at"),
            "updated_by": snap.get("updated_by"),
            "error": snap.get("_error"),
        },
        "core_workflows": list(CORE_FLOW_NAMES),
        "disclaimer": (
            "Read-only view of Buzz ceremony workflows. "
            "SoT = relay (`buzz workflows list`) or Cadence snapshot at "
            "ops/process/workflows_snapshot.json — not a hard-coded HTML list. "
            "Does not edit workflows, show full run history, or replace Cadence ceremony ownership. "
            "ops/backlog auto-start is a separate system (Plan tab) — not this process flow."
        ),
        "errors": errors,
    }
    if source == "empty" and not active_rows:
        payload["ok"] = False
        payload["error"] = "No process workflows available"
    return payload
