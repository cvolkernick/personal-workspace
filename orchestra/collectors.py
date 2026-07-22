"""Pure collectors: domain snapshots from on-disk sources (no live servers required)."""

from __future__ import annotations

import json
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from .domains import DOMAIN_SPECS
except ImportError:  # script / unittest path insert
    from domains import DOMAIN_SPECS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_text(path: Path, limit: int = 12000) -> str:
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except OSError:
        return ""


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _checklist_open(md: str) -> list[str]:
    out: list[str] = []
    for line in md.splitlines():
        m = re.match(r"^\s*[-*]\s*\[\s*\]\s*(.+)$", line)
        if m:
            out.append(m.group(1).strip())
    return out


def _strip_md_emphasis(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\*(.+?)\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    return s.strip()


def _split_title_detail(raw: str) -> tuple[str, str]:
    """Split a checklist line into short title + why/detail (bet/initiative link)."""
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    # Prefer explicit *Why…* / *Link…* italic tail
    m = re.search(
        r"\s+\*((?:Why|Link|Link to bets?|Why this moves)[^*]+)\*\s*$",
        raw,
        re.I,
    )
    if m:
        detail = m.group(1).strip()
        title = raw[: m.start()].strip().rstrip(".").rstrip()
        return _strip_md_emphasis(title), detail
    # Parenthetical tail as secondary detail
    m2 = re.match(r"^(.+?)\s+\(([^)]{8,})\)\s*\.?\s*$", raw)
    if m2 and not m2.group(1).count("("):
        title = _strip_md_emphasis(m2.group(1).strip())
        detail = m2.group(2).strip()
        return title, detail
    # Bold lead as title, remainder as detail
    m3 = re.match(r"^\*\*(.+?)\*\*\s*(.*)$", raw)
    if m3:
        title = m3.group(1).strip()
        rest = m3.group(2).strip().strip("—–-").strip()
        rest = _strip_md_emphasis(rest)
        return title, rest
    return _strip_md_emphasis(raw), ""


def _parse_checklist_items(md: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse open and done checklist items into structured cards."""
    open_items: list[dict[str, Any]] = []
    done_items: list[dict[str, Any]] = []
    for line in md.splitlines():
        m_open = re.match(r"^\s*[-*]\s*\[\s*\]\s*(.+)$", line)
        m_done = re.match(r"^\s*[-*]\s*\[[xX]\]\s*(.+)$", line)
        if m_open:
            raw = m_open.group(1).strip()
            title, detail = _split_title_detail(raw)
            open_items.append(
                {
                    "raw": raw,
                    "title": title or raw,
                    "detail": detail,
                    "done": False,
                }
            )
        elif m_done:
            raw = m_done.group(1).strip()
            title, detail = _split_title_detail(raw)
            done_items.append(
                {
                    "raw": raw,
                    "title": title or raw,
                    "detail": detail,
                    "done": True,
                }
            )
    for i, item in enumerate(open_items, start=1):
        item["rank"] = i
    for i, item in enumerate(done_items, start=1):
        item["rank"] = i
    return open_items, done_items


def _extract_today_meta(md: str) -> dict[str, str]:
    """Pull Date / Context lines from strategy/today.md body."""
    date = ""
    context = ""
    for line in md.splitlines():
        s = line.strip()
        m_date = re.match(r"^\*?\*?Date:?\*?\*?\s*(.+)$", s, re.I)
        if m_date and not date:
            date = _strip_md_emphasis(m_date.group(1))
            continue
        m_ctx = re.match(r"^\*?\*?Context:?\*?\*?\s*(.+)$", s, re.I)
        if m_ctx and not context:
            context = _strip_md_emphasis(m_ctx.group(1))
            continue
    return {"date": date, "context": context}


def _bets_blurb(bets_text: str) -> str:
    """First useful blurb from strategy/bets.md for the Today panel."""
    lines = [ln.strip() for ln in bets_text.splitlines() if ln.strip()]
    for ln in lines[1:10]:
        if ln.startswith("#"):
            break
        if ln.startswith("**") or ln.startswith("-"):
            return _strip_md_emphasis(ln.strip("*").strip())
        if not ln.startswith("["):
            return ln
    return ""


def _match_item_links(
    item: dict[str, Any],
    *,
    bets: list[str],
    initiatives: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach matching thematic bets + initiative ids for UI deep-links."""
    blob = f"{item.get('title', '')} {item.get('detail', '')} {item.get('raw', '')}".lower()
    matched_bets = [b for b in bets if b.lower() in blob]
    # Soft keyword → bet aliases
    aliases = {
        "bitcoin": "Bitcoin",
        "btc": "Bitcoin",
        "energy": "Energy",
        "nuclear": "Energy",
        "ai": "AI",
        "agent": "AI",
        "autonomy": "Autonomy",
        "automation": "Autonomy",
        "robot": "Robotics",
        "command center": "AI",
        "orchestra": "AI",
    }
    for key, bet in aliases.items():
        if key in blob and bet in bets and bet not in matched_bets:
            matched_bets.append(bet)
    matched_inits: list[dict[str, str]] = []
    for init in initiatives:
        title = str(init.get("title") or "")
        iid = str(init.get("id") or "")
        path = str(init.get("path") or f"initiatives/{iid}.md")
        tokens = [
            t
            for t in re.split(r"[^a-z0-9]+", f"{title} {iid}".lower())
            if len(t) >= 4
        ]
        if iid and iid.replace("-", " ") in blob:
            matched_inits.append({"id": iid, "title": title, "path": path})
            continue
        hits = sum(1 for t in tokens if t in blob)
        if hits >= 2 or (iid and any(t in blob for t in iid.split("-") if len(t) >= 5)):
            matched_inits.append({"id": iid, "title": title, "path": path})
    # Dedupe initiatives
    seen: set[str] = set()
    uniq_inits: list[dict[str, str]] = []
    for mi in matched_inits:
        if mi["id"] in seen:
            continue
        seen.add(mi["id"])
        uniq_inits.append(mi)
    out = dict(item)
    out["linked_bets"] = matched_bets[:5]
    out["linked_initiatives"] = uniq_inits[:3]
    return out


def build_today_focus(workspace: Path) -> dict[str, Any]:
    """Structured Today's Focus for the command-center UI (from strategy MD)."""
    ws = Path(workspace)
    bets_path = ws / "strategy" / "bets.md"
    today_path = ws / "strategy" / "today.md"
    bets_text = _read_text(bets_path)
    today_text = _read_text(today_path)
    thematic: list[str] = []
    for name in ("Energy", "Bitcoin", "AI", "Autonomy", "Robotics"):
        if re.search(rf"\b{name}\b", bets_text, re.I):
            thematic.append(name)
    meta = _extract_today_meta(today_text)
    open_raw, done_raw = _parse_checklist_items(today_text)
    initiatives = collect_initiatives(ws)
    open_items = [
        _match_item_links(it, bets=thematic, initiatives=initiatives) for it in open_raw
    ]
    done_items = [
        _match_item_links(it, bets=thematic, initiatives=initiatives) for it in done_raw
    ]
    active_inits = [
        i
        for i in initiatives
        if (i.get("status") or "").lower()
        in ("active", "todo", "in_progress", "planning", "ready")
    ]
    return {
        "ok": bool(today_text or bets_text or initiatives),
        "path": "strategy/today.md" if today_path.is_file() else None,
        "bets_path": "strategy/bets.md" if bets_path.is_file() else None,
        "initiatives_dir": "initiatives/",
        "date": meta.get("date") or "",
        "context": meta.get("context") or "",
        "bets_blurb": _bets_blurb(bets_text),
        "thematic_bets": thematic,
        "open_items": open_items,
        "done_items": done_items,
        "open_count": len(open_items),
        "done_count": len(done_items),
        "initiatives": initiatives,
        "active_initiatives": active_inits,
        "edit_hint": "Edit strategy/today.md in any editor (or ask Grok to refresh it from initiatives).",
        "new_initiative_guide": {
            "summary": (
                "Add a new initiative as a structured Markdown file under initiatives/ "
                "(YAML frontmatter + Description / Current Next Action / Progress)."
            ),
            "template_path": "initiatives/_template.md",
            "example_path": "initiatives/improve-command-center-daily-planner.md",
            "fields": [
                "title",
                "status",
                "linked_bets",
                "priority_impact",
                "next_action",
                "energy",
                "domain_weighting_context",
            ],
        },
    }


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Minimal YAML-ish frontmatter: key: value or key: ["a","b"]."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, Any] = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            items = []
            for piece in re.split(r",\s*", inner):
                piece = piece.strip().strip("\"'")
                if piece:
                    items.append(piece)
            meta[key] = items
        else:
            meta[key] = raw.strip("\"'")
    return meta, parts[2]


def probe_port(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    """True if something accepts TCP connections on host:port."""
    if not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def collect_initiatives(workspace: Path) -> list[dict[str, Any]]:
    """Parse initiatives/*.md frontmatter + next_action."""
    root = Path(workspace) / "initiatives"
    items: list[dict[str, Any]] = []
    if not root.is_dir():
        return items
    for path in sorted(root.glob("*.md")):
        text = _read_text(path)
        meta, body = _parse_frontmatter(text)
        title = meta.get("title") or path.stem.replace("-", " ")
        next_action = meta.get("next_action") or ""
        if not next_action:
            m = re.search(
                r"(?im)^##\s*Current Next Action[^\n]*\n+(.+?)(?:\n##|\Z)",
                body,
            )
            if m:
                next_action = m.group(1).strip().splitlines()[0].strip()
        try:
            rel_path = str(path.relative_to(workspace))
        except ValueError:
            rel_path = str(path)
        items.append(
            {
                "id": path.stem,
                "path": rel_path,
                "title": title,
                "status": meta.get("status") or "unknown",
                "linked_bets": meta.get("linked_bets") or [],
                "priority_impact": meta.get("priority_impact") or "",
                "next_action": next_action,
                "domain_weighting_context": meta.get("domain_weighting_context") or "",
                "energy": meta.get("energy") or "",
            }
        )
    return items


def collect_backlog_summary(workspace: Path) -> dict[str, Any]:
    path = Path(workspace) / "ops" / "backlog" / "items.json"
    data = _read_json(path)
    if not data:
        return {"ok": False, "count": 0, "active": [], "source": str(path)}
    items = data.get("items") or []
    active = []
    for it in items:
        if not isinstance(it, dict):
            continue
        st = (it.get("status") or "").lower()
        if st in ("done", "cancelled", "rejected", "archived"):
            continue
        active.append(
            {
                "id": it.get("id"),
                "title": it.get("title") or "",
                "priority": it.get("priority") or "medium",
                "status": it.get("status") or "",
                "area": it.get("area") or "",
                "notes": (it.get("notes") or "")[:300],
                "tags": it.get("tags") or [],
            }
        )
    return {
        "ok": True,
        "count": len(active),
        "total": len(items),
        "active": active[:20],
        "source": "ops/backlog/items.json",
        "updated_at": data.get("updated_at"),
    }


def collect_strategy(workspace: Path) -> dict[str, Any]:
    ws = Path(workspace)
    focus = build_today_focus(ws)
    thematic = list(focus.get("thematic_bets") or [])
    open_items = [it.get("raw") or it.get("title") or "" for it in (focus.get("open_items") or [])]
    # Keep plain strings for priorities/synergies (backward compatible)
    open_items = [s for s in open_items if s]
    if not open_items:
        open_items = _checklist_open(_read_text(ws / "strategy" / "today.md"))
    initiatives = list(focus.get("initiatives") or [])
    bets_ok = bool(focus.get("bets_path"))
    today_ok = bool(focus.get("path"))
    status = "ok" if bets_ok or today_ok or initiatives else "missing"
    summary_bits = []
    if thematic:
        summary_bits.append("bets: " + ", ".join(thematic[:5]))
    if open_items:
        summary_bits.append(f"{len(open_items)} open today item(s)")
    if initiatives:
        active_n = len(focus.get("active_initiatives") or [])
        summary_bits.append(f"{active_n} initiative(s)")
    return {
        "id": "strategy",
        "label": "Strategy",
        "status": status,
        "summary": "; ".join(summary_bits) or "No strategy files found",
        "signals": {
            "thematic_bets": thematic,
            "today_open": open_items,
            "today_count": len(open_items),
            "today_focus": focus,
            "initiatives": initiatives,
            "bets_path": focus.get("bets_path"),
            "today_path": focus.get("path"),
        },
        "available": bool(bets_ok or today_ok or initiatives),
        "live": None,
        "url": None,
        "launch": None,
        "sources": ["strategy/bets.md", "strategy/today.md", "initiatives/"],
    }


def collect_workflow(workspace: Path) -> dict[str, Any]:
    ws = Path(workspace)
    backlog = collect_backlog_summary(ws)
    idx_path = ws / "ops" / "session-index" / "latest.json"
    idx = _read_json(idx_path) or {}
    sessions = idx.get("sessions") or []
    session_n = len(sessions) if isinstance(sessions, list) else 0
    # light git dirty count without full workspace collector
    dirty_n = 0
    try:
        import subprocess

        proc = subprocess.run(
            ["git", "-C", str(ws), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout:
            dirty_n = len([ln for ln in proc.stdout.splitlines() if ln.strip()])
    except (OSError, subprocess.TimeoutExpired):
        dirty_n = 0

    bits = [
        f"{backlog.get('count', 0)} active backlog",
        f"{session_n} session(s) indexed",
    ]
    if dirty_n:
        bits.append(f"{dirty_n} dirty file(s)")
    return {
        "id": "workflow",
        "label": "Workflow / Projects",
        "status": "ok" if backlog.get("ok") or session_n else "partial",
        "summary": "; ".join(bits),
        "signals": {
            "backlog": backlog,
            "session_count": session_n,
            "dirty_files": dirty_n,
            "session_index": "ops/session-index/latest.json"
            if idx_path.is_file()
            else None,
        },
        "available": True,
        "live": None,
        "url": "http://127.0.0.1:8765/",
        "launch": "python3 projects-dashboard/server.py",
        "port": 8765,
        "sources": ["ops/backlog/", "ops/session-index/", "projects-dashboard/"],
    }


def collect_finance(workspace: Path) -> dict[str, Any]:
    ws = Path(workspace)
    candidates = [
        ws / "treasury" / "snapshots" / "treasury_latest.json",
        ws / "financial-command" / "treasury_latest.json",
    ]
    data = None
    source = None
    for p in candidates:
        data = _read_json(p)
        if data:
            try:
                source = str(p.relative_to(ws))
            except ValueError:
                source = str(p)
            break
    if not data:
        sources_missing = []
        for c in candidates:
            try:
                sources_missing.append(str(c.relative_to(ws)))
            except ValueError:
                sources_missing.append(str(c))
        return {
            "id": "finance",
            "label": "Finance / Treasury",
            "status": "missing",
            "summary": "No treasury snapshot found",
            "signals": {},
            "available": False,
            "live": None,
            "url": "http://127.0.0.1:8000/financial-command/",
            "launch": "python3 financial-command/server.py",
            "port": 8000,
            "sources": sources_missing,
        }

    snap = data.get("snapshot") or {}
    evaluation = data.get("evaluation") or {}
    cb = snap.get("coinbase") or {}
    rh = snap.get("robinhood") or {}
    actions = evaluation.get("actions") or []
    next_steps = evaluation.get("next_steps") or []
    stress = evaluation.get("stress") or {}
    action_titles = []
    for a in actions[:6]:
        if isinstance(a, dict) and a.get("title"):
            action_titles.append(a["title"])
        elif isinstance(a, str):
            action_titles.append(a)
    if isinstance(next_steps, list):
        for s in next_steps[:4]:
            if isinstance(s, str):
                action_titles.append(s)
            elif isinstance(s, dict) and s.get("title"):
                action_titles.append(s["title"])

    btc_price = cb.get("btc_usd_price")
    liquid_btc_usd = cb.get("liquid_btc_usd")
    rh_value = rh.get("total_value") or rh.get("equity_value")
    bits = []
    if btc_price is not None:
        bits.append(f"BTC ${btc_price:,.0f}" if isinstance(btc_price, (int, float)) else f"BTC {btc_price}")
    if liquid_btc_usd is not None and isinstance(liquid_btc_usd, (int, float)):
        bits.append(f"CB liquid BTC ${liquid_btc_usd:,.2f}")
    if rh_value is not None and isinstance(rh_value, (int, float)):
        bits.append(f"RH ${rh_value:,.2f}")
    if action_titles:
        bits.append(f"{len(action_titles)} treasury action(s)")
    stress_label = stress.get("level") or stress.get("label") if isinstance(stress, dict) else None

    inv_path = ws / "investment" / "treasury-action-items.md"
    inv_text = _read_text(inv_path, 2000)
    inv_open = _checklist_open(inv_text) if inv_text else []

    return {
        "id": "finance",
        "label": "Finance / Treasury",
        "status": "ok" if data else "missing",
        "summary": "; ".join(bits) or "Treasury snapshot loaded",
        "signals": {
            "source": source,
            "as_of": snap.get("as_of"),
            "btc_usd_price": btc_price,
            "liquid_btc_usd": liquid_btc_usd,
            "liquid_usdc": cb.get("liquid_usdc"),
            "robinhood_total": rh_value,
            "buying_power": rh.get("buying_power"),
            "action_titles": action_titles,
            "stress": stress_label,
            "investment_open": inv_open[:8],
        },
        "available": True,
        "live": None,
        "url": "http://127.0.0.1:8000/financial-command/",
        "launch": "python3 financial-command/server.py",
        "port": 8000,
        "sources": [source] if source else [],
    }


def collect_fitness(workspace: Path) -> dict[str, Any]:
    ws = Path(workspace)
    metrics_path = ws / "fitness" / "data" / "health-metrics.json"
    metrics = _read_json(metrics_path) or {}
    weight_series = metrics.get("weight") or []
    latest_weight = None
    if isinstance(weight_series, list) and weight_series:
        # last by date if present
        sorted_w = sorted(
            [w for w in weight_series if isinstance(w, dict)],
            key=lambda w: w.get("date") or "",
        )
        if sorted_w:
            latest_weight = sorted_w[-1]

    workouts_dir = ws / "fitness" / "workouts"
    workout_files = []
    if workouts_dir.is_dir():
        workout_files = [
            p.name for p in sorted(workouts_dir.glob("*.md")) if not p.name.startswith("_")
        ]

    nutrition_targets = _read_json(ws / "fitness" / "nutrition" / "targets.json")
    goals = _read_json(ws / "fitness" / "exercises" / "goals.json")

    bits = []
    if latest_weight:
        w = latest_weight.get("weight_lbs")
        d = latest_weight.get("date")
        if w is not None:
            bits.append(f"weight {w} lbs" + (f" ({d})" if d else ""))
    if workout_files:
        bits.append(f"workouts: {', '.join(workout_files[:4])}")
    if nutrition_targets:
        bits.append("nutrition targets present")

    available = bool(metrics or workout_files or nutrition_targets or goals)
    return {
        "id": "fitness",
        "label": "Fitness / Health",
        "status": "ok" if available else "missing",
        "summary": "; ".join(bits) or "No fitness data found",
        "signals": {
            "latest_weight": latest_weight,
            "workout_files": workout_files,
            "has_nutrition_targets": bool(nutrition_targets),
            "has_goals": bool(goals),
            "metrics_path": "fitness/data/health-metrics.json"
            if metrics_path.is_file()
            else None,
        },
        "available": available,
        "live": None,
        "url": "http://127.0.0.1:8787/",
        "launch": "python3 resistance-dashboard/server.py",
        "port": 8787,
        "sources": ["fitness/data/", "fitness/workouts/", "resistance-dashboard/"],
    }


def collect_holistic(workspace: Path) -> dict[str, Any]:
    ws = Path(workspace)
    data_path = ws / "holistic" / "data" / "tasks.json"
    state = _read_json(data_path) or {}
    targets = state.get("targets") or []
    items = state.get("items") or []
    plan = state.get("plan") or {}
    blocks = plan.get("blocks") if isinstance(plan, dict) else []
    if not isinstance(blocks, list):
        blocks = []
    target_titles = []
    for t in targets:
        if isinstance(t, dict) and t.get("title"):
            target_titles.append(t["title"])
        elif isinstance(t, dict) and t.get("id"):
            target_titles.append(str(t["id"]))
    bits = [
        f"{len(targets)} target(s)",
        f"{len(items)} item(s)",
    ]
    if blocks:
        bits.append(f"{len(blocks)} plan block(s)")
    return {
        "id": "holistic",
        "label": "Time Allocation",
        "status": "ok" if state else "missing",
        "summary": "; ".join(bits) if state else "No holistic state found",
        "signals": {
            "targets": target_titles[:12],
            "target_count": len(targets),
            "item_count": len(items),
            "plan_blocks": [
                b.get("id") or b.get("title")
                for b in blocks[:10]
                if isinstance(b, dict)
            ],
            "source": "holistic/data/tasks.json" if data_path.is_file() else None,
        },
        "available": bool(state),
        "live": None,
        "url": "http://127.0.0.1:8770/",
        "launch": "python3 holistic/server.py",
        "port": 8770,
        "sources": ["holistic/data/", "holistic/time_allocator/"],
    }


def collect_iot(workspace: Path) -> dict[str, Any]:
    """Wiz lights registry, room groups, and sun schedule from on-disk IoT config."""
    ws = Path(workspace)
    bulbs_path = ws / "iot" / "wiz-lights" / "bulbs.json"
    groups_path = ws / "iot" / "groups.json"
    schedule_path = ws / "iot" / "schedule.json"

    bulbs = _read_json(bulbs_path) or {}
    groups_raw = _read_json(groups_path) or {}
    schedule = _read_json(schedule_path) or {}

    # bulbs.json is name -> {ip, mac}; skip non-dict entries
    device_names = [
        name
        for name, meta in bulbs.items()
        if isinstance(meta, dict) and (meta.get("ip") or meta.get("mac"))
    ]
    group_labels: list[str] = []
    for gid, gmeta in groups_raw.items():
        if isinstance(gmeta, dict):
            group_labels.append(str(gmeta.get("label") or gid))
        else:
            group_labels.append(str(gid))

    routines = schedule.get("routines") if isinstance(schedule, dict) else []
    if not isinstance(routines, list):
        routines = []
    enabled_routines = []
    for r in routines:
        if not isinstance(r, dict):
            continue
        if r.get("enabled") is False:
            continue
        enabled_routines.append(
            {
                "id": r.get("id"),
                "name": r.get("name") or r.get("id"),
                "trigger": r.get("trigger"),
                "target": r.get("target"),
                "color": r.get("color"),
            }
        )
    location = schedule.get("location") if isinstance(schedule, dict) else None
    loc_ok = bool(
        isinstance(location, dict)
        and location.get("latitude") is not None
        and location.get("longitude") is not None
    )

    bits: list[str] = []
    if device_names:
        bits.append(f"{len(device_names)} bulb(s)")
    if group_labels:
        bits.append(f"groups: {', '.join(group_labels[:4])}")
    if enabled_routines:
        bits.append(f"{len(enabled_routines)} sun routine(s)")
    if loc_ok:
        bits.append("location set")
    elif schedule:
        bits.append("location missing")

    available = bool(device_names or group_labels or schedule)
    return {
        "id": "iot",
        "label": "IoT / Home",
        "status": "ok" if available else "missing",
        "summary": "; ".join(bits) or "No IoT config found",
        "signals": {
            "device_count": len(device_names),
            "devices": device_names[:20],
            "groups": group_labels,
            "group_count": len(group_labels),
            "routines": enabled_routines,
            "routine_count": len(enabled_routines),
            "location_configured": loc_ok,
            "location_label": (location or {}).get("label")
            if isinstance(location, dict)
            else None,
            "bulbs_path": "iot/wiz-lights/bulbs.json" if bulbs_path.is_file() else None,
            "schedule_path": "iot/schedule.json" if schedule_path.is_file() else None,
        },
        "available": available,
        "live": None,
        "url": "http://127.0.0.1:8780/",
        "launch": "python3 iot/server.py",
        "port": 8780,
        "sources": [
            "iot/wiz-lights/bulbs.json",
            "iot/groups.json",
            "iot/schedule.json",
            "iot/",
        ],
    }


_COLLECTORS = {
    "strategy": collect_strategy,
    "workflow": collect_workflow,
    "finance": collect_finance,
    "fitness": collect_fitness,
    "holistic": collect_holistic,
    "iot": collect_iot,
}


def collect_all_domains(
    workspace: Path,
    *,
    probe_ports: bool = False,
) -> list[dict[str, Any]]:
    """Collect status for all registered domains. Optionally mark live server badges."""
    ws = Path(workspace)
    domains: list[dict[str, Any]] = []
    for spec in DOMAIN_SPECS:
        fn = _COLLECTORS.get(spec["id"])
        if fn:
            snap = fn(ws)
        else:
            snap = {
                "id": spec["id"],
                "label": spec["label"],
                "status": "unknown",
                "summary": "",
                "signals": {},
                "available": False,
                "live": None,
                "url": spec.get("url"),
                "launch": spec.get("launch"),
                "port": spec.get("port"),
                "sources": spec.get("sources") or [],
            }
        # ensure launch/url/port from registry when missing
        if snap.get("url") is None and spec.get("url"):
            snap["url"] = spec["url"]
        if snap.get("launch") is None and spec.get("launch"):
            snap["launch"] = spec["launch"]
        if snap.get("port") is None and spec.get("port"):
            snap["port"] = spec["port"]
        if probe_ports and snap.get("port"):
            snap["live"] = probe_port(int(snap["port"]))
        domains.append(snap)
    return domains


def text_corpus_from_domains(domains: list[dict[str, Any]]) -> dict[str, str]:
    """Flatten domain signals into lowercase text bags for keyword overlap."""
    bags: dict[str, str] = {}
    for d in domains:
        parts: list[str] = [d.get("label") or "", d.get("summary") or ""]
        sig = d.get("signals") or {}
        parts.append(json.dumps(sig, default=str))
        bags[d["id"]] = " ".join(parts).lower()
    return bags
