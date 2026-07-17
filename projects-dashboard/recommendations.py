"""Dynamic backlog recommendations: next actions + new item suggestions.

Regenerates from live personal-workspace context (backlog, initiatives,
strategy/today.md, monorepo areas, git readiness, Grok session index).

Suggestions are persisted in ops/backlog/suggestions.json so approve/reject
survives refresh. Approved "new_item" suggestions become backlog items;
"action" suggestions can be applied as notes/status bumps on existing items.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backlog import (
    BACKLOG_DIR,
    PRIORITIES,
    WORKSPACE_ROOT,
    add_item,
    get_item,
    list_items,
    load_backlog,
    update_item,
)

SUGGESTIONS_PATH = BACKLOG_DIR / "suggestions.json"

# Known monorepo areas worth suggesting work for if idle
_FOCUS_AREAS = (
    "resistance-dashboard",
    "financial-command",
    "treasury",
    "projects-dashboard",
    "fitness",
    "investment",
    "strategy",
    "initiatives",
    "research",
    "iot",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure() -> None:
    BACKLOG_DIR.mkdir(parents=True, exist_ok=True)
    if not SUGGESTIONS_PATH.is_file():
        SUGGESTIONS_PATH.write_text(
            json.dumps(
                {"version": 1, "updated_at": _now(), "generated_at": None, "suggestions": []},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def load_suggestions() -> dict[str, Any]:
    _ensure()
    try:
        data = json.loads(SUGGESTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"version": 1, "updated_at": _now(), "generated_at": None, "suggestions": []}
    if not isinstance(data.get("suggestions"), list):
        data["suggestions"] = []
    return data


def save_suggestions(data: dict[str, Any]) -> None:
    _ensure()
    data["updated_at"] = _now()
    data["version"] = data.get("version") or 1
    SUGGESTIONS_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_text(path: Path, limit: int = 8000) -> str:
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except OSError:
        return ""


def _checklist_lines(md: str) -> list[str]:
    out = []
    for line in md.splitlines():
        m = re.match(r"^\s*[-*]\s*\[\s*\]\s*(.+)$", line)
        if m:
            out.append(m.group(1).strip())
    return out


def _session_titles(limit: int = 8) -> list[str]:
    idx = WORKSPACE_ROOT / "ops" / "session-index" / "latest.json"
    if not idx.is_file():
        return []
    try:
        data = json.loads(idx.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    titles = []
    for s in data.get("sessions") or []:
        t = (s.get("title") or "").strip()
        if t:
            titles.append(t)
        if len(titles) >= limit:
            break
    return titles


def _area_edit_hints() -> dict[str, int]:
    """Best-effort: session-index doesn't have areas; use git dirty + backlog areas."""
    from workspace import collect_repo_status, known_project_dirs  # noqa: WPS433

    dirty_counts: dict[str, int] = {p.name: 0 for p in known_project_dirs()}
    try:
        st = collect_repo_status(WORKSPACE_ROOT)
        for path in st.get("dirty_paths") or []:
            top = path.split("/", 1)[0]
            if top in dirty_counts:
                dirty_counts[top] += 1
    except Exception:
        pass
    return dirty_counts


def _new_suggestion(
    *,
    kind: str,
    title: str,
    rationale: str,
    priority: str = "medium",
    area: str = "",
    mvp_scope: str = "",
    description: str = "",
    backlog_item_id: Optional[str] = None,
    action: Optional[str] = None,
    source: str = "heuristic",
    tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    if priority not in PRIORITIES:
        priority = "medium"
    return {
        "id": str(uuid.uuid4()),
        "kind": kind,  # action | new_item
        "status": "pending",  # pending | approved | rejected
        "title": title,
        "rationale": rationale,
        "priority": priority,
        "area": area,
        "mvp_scope": mvp_scope,
        "description": description or rationale,
        "backlog_item_id": backlog_item_id,
        "action": action,  # for kind=action: freeform next step
        "source": source,
        "tags": tags or ["recommended"],
        "created_at": _now(),
        "resolved_at": None,
    }


def generate_recommendations(*, replace_pending: bool = True) -> dict[str, Any]:
    """Build fresh pending suggestions; keep historical approved/rejected."""
    data = load_suggestions()
    kept = [
        s
        for s in data.get("suggestions") or []
        if s.get("status") in ("approved", "rejected")
    ]
    pending_old = [
        s for s in data.get("suggestions") or [] if s.get("status") == "pending"
    ]
    if not replace_pending:
        # only fill if empty
        if pending_old:
            return {
                "ok": True,
                "generated_at": data.get("generated_at"),
                "suggestions": [s for s in data["suggestions"] if s.get("status") == "pending"],
                "all": data["suggestions"],
                "message": "kept existing pending",
            }

    # Rejected titles (avoid re-suggesting same new_item title soon)
    rejected_titles = {
        (s.get("title") or "").strip().lower()
        for s in kept
        if s.get("status") == "rejected" and s.get("kind") == "new_item"
    }
    approved_titles = {
        (s.get("title") or "").strip().lower()
        for s in kept
        if s.get("status") == "approved" and s.get("kind") == "new_item"
    }
    backlog = list_items(include_done=True)
    active_backlog = [i for i in backlog if i.get("status") not in ("done", "parked")]
    backlog_titles = {(i.get("title") or "").strip().lower() for i in backlog}

    fresh: list[dict[str, Any]] = []

    # --- Actions from existing backlog ---
    pri_rank = {p: i for i, p in enumerate(PRIORITIES)}
    ranked = sorted(
        active_backlog,
        key=lambda x: pri_rank.get(x.get("priority") or "medium", 1),
        reverse=True,
    )
    for item in ranked[:8]:
        st = item.get("status") or "idea"
        title = item.get("title") or "item"
        notes = (item.get("notes") or "").strip()
        mvp = (item.get("mvp_scope") or "").strip()
        if st == "idea":
            action = (
                notes
                or f"Promote to ready: clarify MVP for “{title}”, then Initiate goal planning."
            )
            fresh.append(
                _new_suggestion(
                    kind="action",
                    title=f"Next: clarify & ready “{title[:60]}”",
                    rationale=f"Backlog item is still idea-level (priority={item.get('priority')}).",
                    priority=item.get("priority") or "medium",
                    area=item.get("area") or "",
                    backlog_item_id=item.get("id"),
                    action=action,
                    description=action,
                    tags=["recommended", "from-backlog", "idea"],
                )
            )
        elif st == "ready":
            action = (
                notes
                or f"Initiate goal session for “{title}” to write seed spec and build MVP"
                + (f": {mvp}" if mvp else ".")
            )
            fresh.append(
                _new_suggestion(
                    kind="action",
                    title=f"Next: initiate “{title[:60]}”",
                    rationale="Item is ready — highest leverage is starting the Grok /goal planning + MVP path.",
                    priority=item.get("priority") or "high",
                    area=item.get("area") or "",
                    backlog_item_id=item.get("id"),
                    action=action,
                    description=action,
                    tags=["recommended", "from-backlog", "ready"],
                )
            )
        elif st == "planning":
            action = (
                notes
                or f"Continue planning/build for “{title}”: refine seed, ship MVP, then protect & push."
            )
            fresh.append(
                _new_suggestion(
                    kind="action",
                    title=f"Next: finish planning/MVP “{title[:55]}”",
                    rationale="Item already in planning — complete the open goal rather than starting something new.",
                    priority=item.get("priority") or "high",
                    area=item.get("area") or "",
                    backlog_item_id=item.get("id"),
                    action=action,
                    description=action,
                    tags=["recommended", "from-backlog", "planning"],
                )
            )
        elif st == "active":
            action = notes or f"Iterate on “{title}”: next MVP slice, tests, then git_workflow sync."
            fresh.append(
                _new_suggestion(
                    kind="action",
                    title=f"Next: iterate “{title[:60]}”",
                    rationale="Active project — protect progress and define the next small slice.",
                    priority=item.get("priority") or "medium",
                    area=item.get("area") or "",
                    backlog_item_id=item.get("id"),
                    action=action,
                    description=action,
                    tags=["recommended", "from-backlog", "active"],
                )
            )

    # --- From strategy/today.md unchecked items ---
    today = _read_text(WORKSPACE_ROOT / "strategy" / "today.md")
    for line in _checklist_lines(today)[:5]:
        # strip markdown bold
        clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        clean = re.sub(r"\*[^*]+\*", "", clean).strip()
        short = clean[:120]
        key = short.lower()
        if key in backlog_titles or key in rejected_titles or key in approved_titles:
            continue
        # Prefer as action if it references existing backlog keywords
        matched = None
        for it in active_backlog:
            t = (it.get("title") or "").lower()
            if any(w in clean.lower() for w in t.split()[:4] if len(w) > 4):
                matched = it
                break
        if matched:
            fresh.append(
                _new_suggestion(
                    kind="action",
                    title=f"Today’s focus → {matched['title'][:50]}",
                    rationale=f"From strategy/today.md: {short}",
                    priority=matched.get("priority") or "high",
                    area=matched.get("area") or "strategy",
                    backlog_item_id=matched.get("id"),
                    action=short,
                    description=short,
                    tags=["recommended", "from-today"],
                )
            )
        else:
            fresh.append(
                _new_suggestion(
                    kind="new_item",
                    title=short[:100],
                    rationale="Unchecked item from strategy/today.md — not yet on backlog.",
                    priority="high",
                    area="strategy",
                    mvp_scope="One concrete deliverable you can finish in a single Grok goal session.",
                    description=f"Sourced from strategy/today.md:\n\n{clean}",
                    tags=["recommended", "from-today"],
                )
            )

    # --- Idle monorepo areas without backlog coverage ---
    dirty = _area_edit_hints()
    covered_areas = {(i.get("area") or "").lower() for i in active_backlog if i.get("area")}
    # also match title mentions
    for area in _FOCUS_AREAS:
        if area.lower() in covered_areas:
            continue
        if any(area.replace("-", " ") in (i.get("title") or "").lower() for i in active_backlog):
            continue
        title = f"Improve {area}"
        if title.lower() in rejected_titles or title.lower() in backlog_titles:
            continue
        dcount = dirty.get(area, 0)
        if dcount:
            fresh.append(
                _new_suggestion(
                    kind="action",
                    title=f"Protect dirty work in {area}",
                    rationale=f"{dcount} uncommitted file(s) under {area}/ — commit via protect/sync before context switch.",
                    priority="high",
                    area=area,
                    action=f"Run: python3 projects-dashboard/git_workflow.py start {area} && python3 projects-dashboard/git_workflow.py sync",
                    description=f"Dirty tree under {area}/",
                    tags=["recommended", "git", "dirty"],
                )
            )
        else:
            # only suggest new if area looks "product-like"
            if area in ("resistance-dashboard", "financial-command", "treasury", "fitness", "iot"):
                fresh.append(
                    _new_suggestion(
                        kind="new_item",
                        title=f"Next iteration for {area}",
                        rationale=f"No open backlog item targets area “{area}”. Worth a small improvement goal.",
                        priority="low",
                        area=area,
                        mvp_scope=f"One user-visible improvement in personal-workspace/{area}/ with a test or verification step.",
                        description=(
                            f"Propose and ship a small iteration in `{area}`. "
                            "Start with a goal planning session to pick the highest-leverage slice."
                        ),
                        tags=["recommended", "area-gap"],
                    )
                )

    # --- Session-derived themes ---
    for title in _session_titles(5):
        low = title.lower()
        if low in backlog_titles or low in rejected_titles:
            continue
        # skip if already clearly matched
        if any(low[:20] in (i.get("title") or "").lower() for i in active_backlog):
            continue
        if "dashboard" in low or "coinbase" in low or "treasury" in low:
            fresh.append(
                _new_suggestion(
                    kind="new_item",
                    title=f"Follow-up: {title[:80]}",
                    rationale="Recent Grok session title without a matching open backlog item — capture follow-up work.",
                    priority="medium",
                    area=(
                        "treasury"
                        if "coinbase" in low or "treasury" in low
                        else "projects-dashboard"
                        if "project" in low or "dashboard" in low
                        else ""
                    ),
                    mvp_scope="Document open threads from the session and ship one remaining task.",
                    description=f"Derived from Grok session: {title}",
                    tags=["recommended", "from-session"],
                )
            )

    # --- Standing leverage bet if automation item is still idea ---
    if not any("automation" in (i.get("title") or "").lower() and i.get("status") == "ready" for i in active_backlog):
        auto = next(
            (i for i in active_backlog if "automation" in (i.get("title") or "").lower()),
            None,
        )
        if auto and auto.get("status") == "idea":
            fresh.append(
                _new_suggestion(
                    kind="action",
                    title="Pick one painful manual step to automate this week",
                    rationale="Leverage bet (AI/Autonomy): convert the automation initiative from idea → ready with a concrete MVP.",
                    priority="high",
                    area="initiatives",
                    backlog_item_id=auto.get("id"),
                    action=auto.get("notes")
                    or "Name one repeatable manual step and define a 1-session MVP automation.",
                    tags=["recommended", "leverage"],
                )
            )

    # Deduplicate by title
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for s in fresh:
        k = (s.get("kind"), (s.get("title") or "").lower())
        if k in seen:
            continue
        seen.add(k)
        deduped.append(s)

    # Cap pending volume
    actions = [s for s in deduped if s["kind"] == "action"][:6]
    news = [s for s in deduped if s["kind"] == "new_item"][:5]
    pending = actions + news

    data["suggestions"] = kept + pending
    data["generated_at"] = _now()
    save_suggestions(data)
    return {
        "ok": True,
        "generated_at": data["generated_at"],
        "count_pending": len(pending),
        "suggestions": pending,
        "actions": actions,
        "new_items": news,
    }


def recommendations_payload(*, refresh: bool = False) -> dict[str, Any]:
    data = load_suggestions()
    pending = [s for s in data.get("suggestions") or [] if s.get("status") == "pending"]
    if refresh or not pending:
        gen = generate_recommendations(replace_pending=True)
        pending = gen.get("suggestions") or []
        data = load_suggestions()
    return {
        "ok": True,
        "generated_at": data.get("generated_at"),
        "updated_at": data.get("updated_at"),
        "pending": pending,
        "actions": [s for s in pending if s.get("kind") == "action"],
        "new_items": [s for s in pending if s.get("kind") == "new_item"],
        "history": [
            s
            for s in data.get("suggestions") or []
            if s.get("status") in ("approved", "rejected")
        ][-20:],
        "path": str(SUGGESTIONS_PATH.relative_to(WORKSPACE_ROOT)),
    }


def approve_suggestion(suggestion_id: str) -> dict[str, Any]:
    data = load_suggestions()
    found = None
    for s in data.get("suggestions") or []:
        if s.get("id") == suggestion_id:
            found = s
            break
    if not found:
        return {"ok": False, "error": "not found"}
    if found.get("status") != "pending":
        return {"ok": False, "error": f"already {found.get('status')}"}

    result: dict[str, Any] = {"ok": True, "suggestion": found}

    if found.get("kind") == "new_item":
        added = add_item(
            found.get("title") or "Untitled",
            description=found.get("description") or found.get("rationale") or "",
            priority=found.get("priority") or "medium",
            status="idea",
            tags=list(set((found.get("tags") or []) + ["from-recommendation"])),
            mvp_scope=found.get("mvp_scope") or "",
            notes=found.get("rationale") or "",
            area=found.get("area") or "",
        )
        if not added.get("ok"):
            return added
        found["status"] = "approved"
        found["resolved_at"] = _now()
        found["created_backlog_id"] = added["item"]["id"]
        result["backlog_item"] = added["item"]
        result["message"] = f"Added to backlog: {added['item']['title']}"
    else:
        # action: attach to backlog item notes / bump idea → ready
        bid = found.get("backlog_item_id")
        action_text = found.get("action") or found.get("title") or ""
        if bid and get_item(bid):
            item = get_item(bid)
            assert item is not None
            notes = (item.get("notes") or "").strip()
            new_notes = action_text if not notes else f"{notes}\n— Next: {action_text}"
            patch: dict[str, Any] = {"notes": new_notes}
            if item.get("status") == "idea" and "ready" in (found.get("title") or "").lower():
                patch["status"] = "ready"
            elif item.get("status") == "idea" and "initiate" in (action_text or "").lower():
                patch["status"] = "ready"
            upd = update_item(bid, patch)
            result["backlog_item"] = upd.get("item")
            result["message"] = f"Applied action to backlog item {bid[:8]}"
        else:
            # free-floating action → create idea with the action as notes
            added = add_item(
                found.get("title") or action_text[:80],
                description=found.get("description") or found.get("rationale") or "",
                priority=found.get("priority") or "medium",
                status="ready",
                tags=list(set((found.get("tags") or []) + ["from-recommendation", "action"])),
                mvp_scope=found.get("mvp_scope") or "",
                notes=action_text,
                area=found.get("area") or "",
            )
            result["backlog_item"] = added.get("item")
            result["message"] = "Action promoted to backlog item"
            if added.get("item"):
                found["created_backlog_id"] = added["item"]["id"]
        found["status"] = "approved"
        found["resolved_at"] = _now()

    save_suggestions(data)
    result["suggestion"] = found
    return result


def reject_suggestion(suggestion_id: str) -> dict[str, Any]:
    data = load_suggestions()
    found = None
    for s in data.get("suggestions") or []:
        if s.get("id") == suggestion_id:
            found = s
            break
    if not found:
        return {"ok": False, "error": "not found"}
    if found.get("status") != "pending":
        return {"ok": False, "error": f"already {found.get('status')}"}
    found["status"] = "rejected"
    found["resolved_at"] = _now()
    save_suggestions(data)
    return {"ok": True, "suggestion": found, "message": "Rejected"}


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "refresh":
        print(json.dumps(generate_recommendations(), indent=2))
    elif cmd == "list":
        print(json.dumps(recommendations_payload(), indent=2))
    elif cmd == "approve" and len(sys.argv) > 2:
        print(json.dumps(approve_suggestion(sys.argv[2]), indent=2))
    elif cmd == "reject" and len(sys.argv) > 2:
        print(json.dumps(reject_suggestion(sys.argv[2]), indent=2))
    else:
        print(
            "Usage: recommendations.py [list|refresh|approve <id>|reject <id>]",
            file=sys.stderr,
        )
        raise SystemExit(2)
