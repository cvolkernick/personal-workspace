"""Buzz Board day constraint packet for Orchestra (P3-W).

Pure export from Project #1 board items (+ optional ceremony overlays).
Writes ``ops/board/day_constraints.json`` so ``orchestra.collectors`` can read
without live GraphQL. Domains remain write SoT — Orchestra never writes Board.

Product freeze: PLANS/ORCHESTRATOR_UNITARY_DAILY_PLANNER.md §Workflow packet.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

SCHEMA_VERSION = 1
FRESH_FOR_HOURS = 4.0
DEFAULT_DEEP_LINK = "http://127.0.0.1:8765/"
DEFAULT_BOARD_URL = "https://github.com/users/cvolkernick/projects/1"

# Seats that hold implement WIP or eng-gate (Pending Review does not busy)
WIP_SEATS = frozenset({"implement", "gate"})

DEFAULT_AGENTS: list[dict[str, str]] = [
    {"name": "Forge", "role": "platform eng", "seat": "implement"},
    {"name": "Grok", "role": "SIC + eng gate", "seat": "gate"},
    {"name": "Meridian", "role": "horizon / domain", "seat": "implement"},
    {"name": "Frankenfit", "role": "fitness", "seat": "implement"},
    {"name": "Nakatoshi", "role": "capital", "seat": "domain"},
    {"name": "Assay", "role": "system QA", "seat": "qa"},
    {"name": "Cadence", "role": "scrum / ceremonies", "seat": "process"},
]

# Status column names (canonical Project #1)
STATUS_READY = "Ready"
STATUS_IP = "In Progress"
STATUS_PR = "Pending Review"
STATUS_BLOCKED_ALIASES = frozenset(
    {
        "blocked",
        "block",
    }
)

_OWNER_LINE = re.compile(
    r"^\s*(?:\*\*)?(?:owner|primary|primary_owner|implementer|assigned to)"
    r"(?:\*\*)?\s*:\s*(.+)$",
    re.IGNORECASE,
)
_AT_NAME = re.compile(r"@([A-Za-z][A-Za-z0-9_.-]*)")


def _utc_now(now: Optional[datetime] = None) -> datetime:
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        return ref.replace(tzinfo=timezone.utc)
    return ref.astimezone(timezone.utc)


def _normalize_name(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip().lstrip("@")
    if not s:
        return None
    # Strip trailing process notes: "Cadence process · Forge eng" → first token family
    return s


def _match_roster(name: str, roster: Sequence[str]) -> Optional[str]:
    """Match a single name or multi-agent line to roster.

    For compound lines (``Frankenfit (+ Forge if …)`` / ``Cadence · Forge``),
    prefer the **leftmost** roster name so co-owners listed later do not win.
    """
    low = name.lower().strip()
    if not low:
        return None
    # Exact / prefix on the whole string first
    for agent in roster:
        al = agent.lower()
        if low == al or low.startswith(al + " ") or low.startswith(al + "("):
            return agent
    # Leftmost substring match (word-ish: agent name as token)
    best: Optional[str] = None
    best_pos = len(low) + 1
    for agent in roster:
        al = agent.lower()
        pos = low.find(al)
        if pos < 0:
            continue
        # Avoid matching inside unrelated words when possible
        before_ok = pos == 0 or not low[pos - 1].isalnum()
        after = pos + len(al)
        after_ok = after >= len(low) or not low[after].isalnum()
        if not (before_ok and after_ok):
            continue
        if pos < best_pos:
            best_pos = pos
            best = agent
    return best


def _parse_agents(agents: Optional[Sequence[Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for a in agents or []:
        if isinstance(a, str):
            n = a.strip()
            if n:
                out.append({"name": n, "role": "implement", "seat": "implement"})
            continue
        if not isinstance(a, Mapping):
            continue
        n = str(a.get("name") or "").strip()
        if not n:
            continue
        seat = str(a.get("seat") or "implement").strip().lower() or "implement"
        role = str(a.get("role") or seat).strip()
        out.append({"name": n, "role": role, "seat": seat})
    return out or list(DEFAULT_AGENTS)


def _wip_roster(agents: Sequence[Mapping[str, str]]) -> list[str]:
    names = [a["name"] for a in agents if a.get("seat") in WIP_SEATS]
    return names or [a["name"] for a in agents if a.get("seat") == "implement"]


def _owner_from_body(body: Optional[str], roster: Sequence[str]) -> Optional[str]:
    if not body:
        return None
    for line in str(body).splitlines()[:50]:
        m = _OWNER_LINE.match(line)
        if not m:
            continue
        raw = m.group(1).strip().strip("*").strip()
        # Prefer first roster match in the line
        matched = _match_roster(raw, roster)
        if matched:
            return matched
        # Try @mentions
        for at in _AT_NAME.findall(raw):
            matched = _match_roster(at, roster)
            if matched:
                return matched
        # Return first segment before · or /
        first = re.split(r"[·|/]", raw)[0].strip()
        if first:
            matched = _match_roster(first, roster)
            if matched:
                return matched
            return first
    return None


def resolve_primary_owner(
    item: Mapping[str, Any],
    *,
    overlay: Optional[Mapping[str, Any]] = None,
    roster: Sequence[str] = (),
) -> Optional[str]:
    """Resolve primary_owner: owner field → assignees → overlay → body Owner line."""
    candidates: list[str] = []
    for key in ("primary_owner", "owner", "owner_hint"):
        n = _normalize_name(item.get(key))
        if n:
            candidates.append(n)
    for a in item.get("assignees") or []:
        n = _normalize_name(a)
        if n:
            candidates.append(n)
    if overlay:
        n = _normalize_name(overlay.get("owner"))
        if n:
            candidates.append(n)
    body_owner = _owner_from_body(item.get("body"), roster)
    if body_owner:
        candidates.append(body_owner)

    for c in candidates:
        matched = _match_roster(c, roster) if roster else None
        if matched:
            return matched
    return candidates[0] if candidates else None


def _status_of(item: Mapping[str, Any]) -> str:
    return str(item.get("status") or item.get("board_status") or "").strip()


def _label_names(item: Mapping[str, Any]) -> set[str]:
    """Normalize issue labels from list[str], list[{name}], or a single string."""
    raw = item.get("labels")
    if raw is None:
        return set()
    if isinstance(raw, str):
        n = raw.strip().lower()
        return {n} if n else set()
    names: set[str] = set()
    for lab in raw:
        if isinstance(lab, Mapping):
            n = str(lab.get("name") or "").strip().lower()
        else:
            n = str(lab or "").strip().lower()
        if n:
            names.add(n)
    return names


def _is_open_issue_like(item: Mapping[str, Any]) -> bool:
    kind = str(item.get("kind") or item.get("type") or "Issue")
    if kind not in ("Issue", "PullRequest") and not item.get("is_issue") and not item.get(
        "is_pr"
    ):
        # Normalized items may use is_issue
        if item.get("number") is None:
            return False
    state = str(item.get("state") or "OPEN").upper()
    if state and state not in ("OPEN", ""):
        return False
    return True


def _size_of(
    item: Mapping[str, Any],
    overlay: Optional[Mapping[str, Any]],
) -> Any:
    if overlay and overlay.get("size") is not None:
        return overlay.get("size")
    for key in ("size", "size_hint"):
        if item.get(key) is not None:
            return item.get(key)
    return None


def pipeline_pressure(
    *,
    ready_count: int,
    free_agent_count: int,
    pending_review_count: int,
    in_progress_count: int,
) -> str:
    """dry = Ready0 + free≥1; stuck = Ready>0 + free0 + PR0 + IP busy; else ok."""
    if ready_count == 0 and free_agent_count >= 1:
        return "dry"
    if (
        ready_count > 0
        and free_agent_count == 0
        and pending_review_count == 0
        and in_progress_count > 0
    ):
        return "stuck"
    return "ok"


def compute_wip_overload(in_progress: Sequence[Mapping[str, Any]]) -> bool:
    """True when any primary_owner appears on >1 In Progress card."""
    counts: Counter[str] = Counter()
    for card in in_progress:
        owner = card.get("primary_owner")
        if owner:
            counts[str(owner)] += 1
    return any(n > 1 for n in counts.values())


def build_day_constraints_packet(
    items: Sequence[Mapping[str, Any]],
    *,
    agents: Optional[Sequence[Any]] = None,
    overlays: Optional[Mapping[Any, Mapping[str, Any]]] = None,
    now: Optional[datetime] = None,
    deep_link: Optional[str] = None,
    board_url: Optional[str] = None,
    fetch_ok: bool = True,
) -> dict[str, Any]:
    """Build frozen workflow day_constraints packet from board items.

    ``items`` are open Project #1 cards (status + number + title + optional
    assignees/owner/body). Never invents Ready/IP zeros when ``fetch_ok`` is False —
    use :func:`build_fetch_failed_packet` for fail path.
    """
    if not fetch_ok:
        return build_fetch_failed_packet(
            now=now,
            deep_link=deep_link,
            board_url=board_url,
            detail="fetch_ok=false",
        )

    ref = _utc_now(now)
    as_of = ref.isoformat()
    agents_list = _parse_agents(agents)
    roster = _wip_roster(agents_list)
    ov_map: dict[int, Mapping[str, Any]] = {}
    if overlays:
        for k, v in overlays.items():
            try:
                ov_map[int(k)] = v
            except (TypeError, ValueError):
                continue

    ready: list[dict[str, Any]] = []
    process_ready_count = 0
    in_progress: list[dict[str, Any]] = []
    pending_review: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for raw in items:
        if not isinstance(raw, Mapping):
            continue
        if not _is_open_issue_like(raw):
            continue
        status = _status_of(raw)
        num = raw.get("number")
        try:
            number = int(num) if num is not None else None
        except (TypeError, ValueError):
            number = None
        title = str(raw.get("title") or "(untitled)")
        ov = ov_map.get(number) if number is not None else None
        owner = resolve_primary_owner(raw, overlay=ov, roster=roster)
        size = _size_of(raw, ov)
        label_names = _label_names(raw)

        if status == STATUS_READY:
            # human-only is excluded from both eng Ready and process Ready.
            # process is counted only in process_ready_count (pipe stays dry).
            if "human-only" in label_names:
                pass
            elif "process" in label_names:
                process_ready_count += 1
            else:
                ready.append(
                    {
                        "number": number,
                        "title": title,
                        **({"size": size} if size is not None else {}),
                    }
                )
        elif status == STATUS_IP:
            card = {
                "number": number,
                "title": title,
                "primary_owner": owner,
            }
            if size is not None:
                card["size"] = size
            age = raw.get("age_hours")
            if age is not None:
                card["age_hours"] = age
            in_progress.append(card)
            if not owner:
                blocked.append(
                    {
                        "number": number,
                        "title": title,
                        "reason": "missing primary_owner on In Progress",
                    }
                )
        elif status == STATUS_PR:
            pending_review.append(
                {
                    "number": number,
                    "title": title,
                    "primary_owner": owner,
                }
            )
        # Explicit blocked markers in title/labels (optional process)
        title_l = title.lower()
        if "blocked" in label_names or title_l.startswith("[blocked]"):
            if not any(b.get("number") == number for b in blocked):
                blocked.append(
                    {
                        "number": number,
                        "title": title,
                        "reason": "blocked label/title",
                    }
                )

    ready_count = len(ready)
    # Prefer lower number as "top" (stable); Ready queue order not guaranteed by GraphQL
    ready_sorted = sorted(
        ready,
        key=lambda c: (c.get("number") is None, c.get("number") or 0),
    )
    ready_top = ready_sorted[:3]
    pr_count = len(pending_review)

    busy_owners = {
        str(c["primary_owner"])
        for c in in_progress
        if c.get("primary_owner")
    }
    free_agents = [n for n in roster if n not in busy_owners]
    free_agent_count = len(free_agents)

    wip_overload = compute_wip_overload(in_progress)
    pressure = pipeline_pressure(
        ready_count=ready_count,
        free_agent_count=free_agent_count,
        pending_review_count=pr_count,
        in_progress_count=len(in_progress),
    )

    summary = (
        f"Ready {ready_count} · process {process_ready_count} · "
        f"IP {len(in_progress)} · PR {pr_count} "
        f"· free agents {free_agent_count}"
    )
    if wip_overload:
        summary += " · WIP overload"
    if blocked:
        summary += f" · blocked {len(blocked)}"

    deep = deep_link or DEFAULT_DEEP_LINK
    board = board_url or DEFAULT_BOARD_URL

    return {
        "schema_version": SCHEMA_VERSION,
        "domain": "workflow",
        "as_of": as_of,
        "fresh_for_hours": FRESH_FOR_HOURS,
        "stale": False,
        "fetch_ok": True,
        "confidence": 0.9,
        "ready_count": ready_count,
        "process_ready_count": process_ready_count,
        "ready_top": ready_top,
        "in_progress": in_progress,
        "pending_review_count": pr_count,
        "blocked": blocked,
        "wip_overload": wip_overload,
        "free_agent_count": free_agent_count,
        "pipeline_pressure": pressure,
        "summary": summary,
        "deep_link": deep,
        "board_url": board,
        "source": "buzz-board Project #1",
        "free_agents": free_agents,
    }


def build_fetch_failed_packet(
    *,
    now: Optional[datetime] = None,
    deep_link: Optional[str] = None,
    board_url: Optional[str] = None,
    detail: str = "Board fetch failed",
    as_of: Optional[str] = None,
) -> dict[str, Any]:
    """Honest fail packet — no invented Ready 0 / free agents."""
    ref = _utc_now(now)
    return {
        "schema_version": SCHEMA_VERSION,
        "domain": "workflow",
        "as_of": as_of or ref.isoformat(),
        "fresh_for_hours": FRESH_FOR_HOURS,
        "stale": True,
        "fetch_ok": False,
        "confidence": 0.0,
        # Intentionally omit ready_count / free_agent_count / zeros —
        # collector must treat as unknown, not pretty zeros.
        "ready_top": [],
        "in_progress": [],
        "blocked": [],
        "wip_overload": None,
        "pipeline_pressure": None,
        "pending_review_count": None,
        "summary": f"Board unknown — {detail}",
        "deep_link": deep_link or DEFAULT_DEEP_LINK,
        "board_url": board_url or DEFAULT_BOARD_URL,
        "source": "buzz-board Project #1",
        "error": detail,
    }


def day_constraints_path(workspace: Union[str, Path]) -> Path:
    return Path(workspace) / "ops" / "board" / "day_constraints.json"


def write_day_constraints(
    workspace: Union[str, Path],
    packet: Mapping[str, Any],
) -> Path:
    """Atomic write of packet to ops/board/day_constraints.json."""
    path = day_constraints_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Drop internal-only keys from disk
    clean = {k: v for k, v in packet.items() if not str(k).startswith("_")}
    payload = json.dumps(clean, indent=2, sort_keys=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".day_constraints.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def load_ceremony_helpers(
    workspace: Union[str, Path],
) -> tuple[list[dict[str, str]], dict[int, dict[str, Any]]]:
    """Load agents + overlays from ops/sprint/current.json when present."""
    path = Path(workspace) / "ops" / "sprint" / "current.json"
    if not path.is_file():
        return list(DEFAULT_AGENTS), {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list(DEFAULT_AGENTS), {}
    if not isinstance(raw, dict):
        return list(DEFAULT_AGENTS), {}
    agents = _parse_agents(raw.get("agents"))
    overlays: dict[int, dict[str, Any]] = {}
    for k, v in (raw.get("card_overlays") or {}).items():
        try:
            n = int(k)
        except (TypeError, ValueError):
            continue
        if isinstance(v, dict):
            overlays[n] = v
    return agents, overlays
