"""personal-workspace workflow-management / pre-reset readiness dashboard.

Purpose: before system updates, reboots, or long interruptions, confirm that
Grok Build work in personal-workspace can stop without losing session context
or breaking uncommitted/unpushed builds.

Strict scope: the personal-workspace monorepo. "Projects" are top-level areas
(e.g. resistance-dashboard, financial-command, treasury) matched from Grok
session edit hunks.

Session context lives on disk under ~/.grok/sessions — reboot kills live PIDs
but does not erase history; resume with `grok --resume <session-id>`.
"""

from __future__ import annotations

import json
import os
import subprocess
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Repo root = parent of projects-dashboard/
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GROK_HOME = Path(os.environ.get("GROK_HOME", Path.home() / ".grok"))

# Top-level names that are not "projects"
_SKIP_TOP = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".DS_Store",
}

# Planning / content only — not runnable project areas (no status cards)
# strategy + initiatives feed Today/recs/backlog; ops is git metadata
META_CONTENT_DIRS = frozenset(
    {
        "strategy",
        "initiatives",
        "ops",
    }
)

# Local servers often left running across project work
KNOWN_PORTS = {
    8765: "projects-dashboard",
    8787: "resistance-dashboard",
    8000: "financial-command",
    8770: "holistic",
    8790: "orchestra",
}



def _run_git(repo: Path, *args: str, timeout: float = 10.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return 1, "", str(e)


def collect_repo_status(repo: Path = WORKSPACE_ROOT) -> dict[str, Any]:
    """Full-repo git status for personal-workspace."""
    repo = Path(repo).resolve()
    result: dict[str, Any] = {
        "name": repo.name,
        "path": str(repo),
        "is_git": False,
        "branch": None,
        "remotes": [],
        "dirty": None,
        "ahead": None,
        "behind": None,
        "upstream": None,
        "status_label": "not a git repo",
        "dirty_paths": [],
        "error": None,
    }
    if not repo.is_dir():
        result["error"] = "missing"
        result["status_label"] = "missing"
        return result

    code, out, err = _run_git(repo, "rev-parse", "--is-inside-work-tree")
    if code != 0 or out.lower() != "true":
        result["error"] = err or "not a git repository"
        return result
    result["is_git"] = True

    code, branch, _ = _run_git(repo, "branch", "--show-current")
    if branch:
        result["branch"] = branch
    else:
        code2, sha, _ = _run_git(repo, "rev-parse", "--short", "HEAD")
        result["branch"] = f"detached@{sha}" if code2 == 0 and sha else "detached HEAD"

    code, remote_out, _ = _run_git(repo, "remote", "-v")
    seen: set[tuple[str, str]] = set()
    remotes: list[dict[str, str]] = []
    if code == 0 and remote_out:
        for line in remote_out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                key = (parts[0], parts[1])
                if key not in seen:
                    seen.add(key)
                    remotes.append({"name": parts[0], "url": parts[1]})
    result["remotes"] = remotes

    code, porcelain, _ = _run_git(repo, "status", "--porcelain")
    dirty_paths: list[str] = []
    if code == 0 and porcelain:
        try:
            from git_workflow import parse_porcelain_path  # noqa: WPS433
        except Exception:
            parse_porcelain_path = None  # type: ignore
        for line in porcelain.splitlines():
            if parse_porcelain_path:
                path = parse_porcelain_path(line) or ""
            else:
                # fallback: XY + space
                path = line[3:].strip() if len(line) > 3 and line[2:3] == " " else (
                    line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""
                )
                if " -> " in path:
                    path = path.split(" -> ", 1)[1]
            if path:
                dirty_paths.append(path)
    result["dirty"] = bool(dirty_paths) if code == 0 else None
    result["dirty_paths"] = dirty_paths

    code, upstream, _ = _run_git(repo, "rev-parse", "--abbrev-ref", "@{upstream}")
    if code == 0 and upstream:
        result["upstream"] = upstream
        code_ab, ab_out, _ = _run_git(
            repo, "rev-list", "--left-right", "--count", f"{upstream}...HEAD"
        )
        if code_ab == 0 and ab_out:
            parts = ab_out.split()
            if len(parts) >= 2:
                try:
                    result["behind"] = int(parts[0])
                    result["ahead"] = int(parts[1])
                except ValueError:
                    pass

    parts: list[str] = []
    if result["dirty"]:
        parts.append(f"dirty ({len(dirty_paths)} files)")
    else:
        parts.append("clean")
    ahead, behind = result.get("ahead"), result.get("behind")
    if ahead is not None and behind is not None:
        if ahead == 0 and behind == 0:
            parts.append("synced")
        else:
            if ahead:
                parts.append(f"ahead {ahead}")
            if behind:
                parts.append(f"behind {behind}")
    elif not remotes:
        parts.append("no remote")
    elif not result.get("upstream"):
        parts.append("no upstream")
    result["status_label"] = ", ".join(parts)
    return result


def path_is_dirty(rel_prefix: str, dirty_paths: list[str]) -> bool:
    """True if any dirty path is under rel_prefix (or equals it)."""
    if not rel_prefix or rel_prefix in (".", ""):
        return bool(dirty_paths)
    prefix = rel_prefix.rstrip("/") + "/"
    for p in dirty_paths:
        if p == rel_prefix or p.startswith(prefix):
            return True
    return False


def known_project_dirs(workspace: Path = WORKSPACE_ROOT) -> list[Path]:
    """Top-level directories that are execution projects (not strategy/initiatives/ops)."""
    out: list[Path] = []
    if not workspace.is_dir():
        return out
    for child in sorted(workspace.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name in _SKIP_TOP or child.name.startswith("."):
            continue
        if child.name in META_CONTENT_DIRS:
            continue
        out.append(child)
    return out


def load_strategy_focus(workspace: Path = WORKSPACE_ROOT) -> dict[str, Any]:
    """Read strategy/today.md + light bets summary for the Today panel (not a project)."""
    workspace = Path(workspace)
    today_path = workspace / "strategy" / "today.md"
    bets_path = workspace / "strategy" / "bets.md"
    open_items: list[str] = []
    done_items: list[str] = []
    today_text = ""
    if today_path.is_file():
        try:
            today_text = today_path.read_text(encoding="utf-8")
        except OSError:
            today_text = ""
        for line in today_text.splitlines():
            m_open = re.match(r"^\s*[-*]\s*\[\s*\]\s*(.+)$", line)
            m_done = re.match(r"^\s*[-*]\s*\[[xX]\]\s*(.+)$", line)
            if m_open:
                open_items.append(m_open.group(1).strip())
            elif m_done:
                done_items.append(m_done.group(1).strip())

    bets_blurb = ""
    if bets_path.is_file():
        try:
            bets = bets_path.read_text(encoding="utf-8")
            # First non-empty paragraph after title
            lines = [ln.strip() for ln in bets.splitlines() if ln.strip()]
            for ln in lines[1:8]:
                if ln.startswith("#"):
                    break
                if ln.startswith("**") or ln.startswith("-"):
                    bets_blurb = ln.strip("*").strip()
                    break
                if not ln.startswith("["):
                    bets_blurb = ln
                    break
        except OSError:
            pass

    return {
        "ok": True,
        "kind": "meta-content",
        "path": "strategy/",
        "today_path": "strategy/today.md",
        "bets_path": "strategy/bets.md",
        "open_items": open_items,
        "done_items": done_items,
        "open_count": len(open_items),
        "done_count": len(done_items),
        "bets_blurb": bets_blurb,
        "note": (
            "Strategy is planning content (bets + daily focus), not a runnable project. "
            "Promote open items into backlog or execute in domain folders."
        ),
    }


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError, ValueError, TypeError):
        return False


def load_active_sessions(grok_home: Path) -> dict[str, dict[str, Any]]:
    path = grok_home / "active_sessions.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("session_id"):
                out[item["session_id"]] = item
    return out


def list_listening_ports(ports: dict[int, str] = KNOWN_PORTS) -> list[dict[str, Any]]:
    """Which known project servers still hold a port (ok to kill before reboot)."""
    found: list[dict[str, Any]] = []
    for port, label in ports.items():
        try:
            proc = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            continue
        pids: list[int] = []
        for line in proc.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                pids.append(int(parts[1]))
        found.append(
            {
                "port": port,
                "label": label,
                "pids": sorted(set(pids)),
                "url": f"http://127.0.0.1:{port}/",
            }
        )
    return found


def collect_stashes(repo: Path) -> list[dict[str, str]]:
    code, out, _ = _run_git(repo, "stash", "list")
    if code != 0 or not out:
        return []
    items = []
    for line in out.splitlines():
        # stash@{0}: WIP on master: abc message
        if ":" in line:
            ref, _, msg = line.partition(":")
            items.append({"ref": ref.strip(), "message": msg.strip()})
        else:
            items.append({"ref": line, "message": ""})
    return items


def session_disk_path(grok_home: Path, session_id: str, cwd: Optional[str]) -> Optional[str]:
    """Best-effort path to on-disk session dir (context survives reboot)."""
    sessions_root = grok_home / "sessions"
    if not sessions_root.is_dir():
        return None
    # Prefer cwd-encoded group if present
    candidates: list[Path] = []
    if cwd:
        from urllib.parse import quote

        enc = quote(cwd, safe="")
        candidates.append(sessions_root / enc / session_id)
    for group in sessions_root.iterdir():
        if group.is_dir():
            candidates.append(group / session_id)
    for p in candidates:
        if p.is_dir() and (p / "summary.json").is_file():
            return str(p)
    return None


def _session_meta(
    summary: dict[str, Any],
    session_dir: Path,
    active: dict,
    grok_home: Path,
) -> dict[str, Any]:
    info = summary.get("info") or {}
    sid = info.get("id") or session_dir.name
    cwd = info.get("cwd")
    pid = active[sid].get("pid") if sid in active else None
    alive = pid_alive(pid) if pid else False
    disk = str(session_dir) if session_dir.is_dir() else session_disk_path(
        grok_home, sid, cwd
    )
    meta = {
        "id": sid,
        "title": summary.get("generated_title")
        or summary.get("session_summary")
        or sid,
        "last_active_at": summary.get("last_active_at") or summary.get("updated_at"),
        "created_at": summary.get("created_at"),
        "cwd": cwd,
        "agent_name": summary.get("agent_name"),
        "model": summary.get("current_model_id"),
        "num_chat_messages": summary.get("num_chat_messages"),
        "session_kind": summary.get("session_kind") or "primary",
        "active": sid in active,
        "pid_alive": alive,
        "disk_path": disk,
        "persisted": bool(disk and Path(disk).is_dir()),
        "resume_cmd": f"grok --resume {sid}",
        "resume_cwd": cwd or str(WORKSPACE_ROOT),
    }
    if sid in active:
        meta["active_pid"] = active[sid].get("pid")
        meta["opened_at"] = active[sid].get("opened_at")
    return meta


def build_readiness(
    repo: dict[str, Any],
    projects: list[dict[str, Any]],
    workspace_sessions: list[dict[str, Any]],
    stashes: list[dict[str, str]],
    listeners: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute pre-reboot / workflow-management checklist and verdict.

    Verdict levels:
      ready  — no data-loss risk from reboot (sessions on disk; git clean+synced)
      caution — reboot ok for session context, but uncommitted/unpushed work or live agents
      blocked — something critical missing (e.g. not a git repo)
    """
    checks: list[dict[str, Any]] = []
    dirty_n = len(repo.get("dirty_paths") or [])
    ahead = repo.get("ahead")
    behind = repo.get("behind")
    live_sessions = [s for s in workspace_sessions if s.get("active") and s.get("pid_alive")]
    persisted = [s for s in workspace_sessions if s.get("persisted")]
    unpersisted = [s for s in workspace_sessions if s.get("active") and not s.get("persisted")]

    # 1. Uncommitted work
    if dirty_n == 0:
        checks.append(
            {
                "id": "uncommitted",
                "level": "ok",
                "title": "Working tree clean",
                "detail": "No uncommitted files in personal-workspace.",
                "action": None,
            }
        )
    else:
        checks.append(
            {
                "id": "uncommitted",
                "level": "warn",
                "title": f"{dirty_n} uncommitted file(s)",
                "detail": "Local edits are not in git yet — reboot keeps the disk files, but a bad crash or "
                "cleanup can still lose them. Commit (and push) or stash before a reset.",
                "action": "git add -A && git commit && git push   # or: git stash push -u -m 'pre-reboot'",
                "paths": (repo.get("dirty_paths") or [])[:30],
            }
        )

    # 2. Unpushed commits
    if ahead is None:
        checks.append(
            {
                "id": "unpushed",
                "level": "warn",
                "title": "No upstream tracking",
                "detail": "Cannot tell if commits are pushed. Set upstream or push explicitly.",
                "action": "git push -u origin HEAD",
            }
        )
    elif ahead > 0:
        checks.append(
            {
                "id": "unpushed",
                "level": "warn",
                "title": f"{ahead} unpushed commit(s)",
                "detail": "Commits exist only on this machine until pushed. A disk wipe loses them.",
                "action": "git push",
            }
        )
    else:
        checks.append(
            {
                "id": "unpushed",
                "level": "ok",
                "title": "No unpushed commits",
                "detail": "HEAD matches upstream (or is not ahead).",
                "action": None,
            }
        )

    if behind is not None and behind > 0:
        checks.append(
            {
                "id": "behind",
                "level": "info",
                "title": f"{behind} commit(s) behind upstream",
                "detail": "Optional: pull after reboot so builds match remote.",
                "action": "git pull --rebase",
            }
        )

    # 3. Session persistence (the key "don't lose context" guarantee)
    if not workspace_sessions:
        checks.append(
            {
                "id": "sessions_disk",
                "level": "info",
                "title": "No workspace-linked Grok sessions",
                "detail": "Nothing to resume for personal-workspace after reboot.",
                "action": None,
            }
        )
    elif unpersisted:
        checks.append(
            {
                "id": "sessions_disk",
                "level": "warn",
                "title": f"{len(unpersisted)} live session(s) without on-disk folder",
                "detail": "Unexpected — Grok usually writes ~/.grok/sessions continuously. "
                "Note session IDs before reboot.",
                "action": "Copy resume commands from the Resume kit below.",
            }
        )
    else:
        checks.append(
            {
                "id": "sessions_disk",
                "level": "ok",
                "title": f"{len(persisted)} session(s) persisted on disk",
                "detail": "Grok session history survives reboot. Resume with the commands below "
                "(live PIDs will die; that is expected).",
                "action": None,
            }
        )

    # 4. Live agent processes
    if live_sessions:
        checks.append(
            {
                "id": "live_agents",
                "level": "info",
                "title": f"{len(live_sessions)} live Grok process(es)",
                "detail": "Reboot kills these PIDs. Context remains on disk — resume after reboot. "
                "Prefer finishing or pausing in-flight tool work first if an agent is mid-edit.",
                "action": "Finish active prompts, then reboot. After: grok --resume <id>",
            }
        )
    else:
        checks.append(
            {
                "id": "live_agents",
                "level": "ok",
                "title": "No live Grok PIDs for workspace sessions",
                "detail": "No running agent processes to interrupt.",
                "action": None,
            }
        )

    # 5. Stashes
    if stashes:
        checks.append(
            {
                "id": "stashes",
                "level": "info",
                "title": f"{len(stashes)} git stash(es)",
                "detail": "Stashes stay on disk with the repo; remember to pop/apply after reboot if needed.",
                "action": "git stash list",
            }
        )

    # 6. Local servers
    if listeners:
        labels = ", ".join(f"{x['label']}:{x['port']}" for x in listeners)
        checks.append(
            {
                "id": "servers",
                "level": "info",
                "title": f"{len(listeners)} local server(s) still listening",
                "detail": f"{labels}. Optional to stop; reboot kills them. Not a data-loss risk.",
                "action": "lsof -ti :<port> | xargs kill   # optional",
            }
        )
    else:
        checks.append(
            {
                "id": "servers",
                "level": "ok",
                "title": "No known project servers listening",
                "detail": "Ports 8765/8787/8000 free.",
                "action": None,
            }
        )

    # Dirty projects summary
    dirty_projects = [p["name"] for p in projects if p.get("dirty")]
    if dirty_projects:
        checks.append(
            {
                "id": "dirty_projects",
                "level": "warn",
                "title": f"Dirty project areas: {', '.join(dirty_projects)}",
                "detail": "Uncommitted work is concentrated in these top-level areas.",
                "action": "Review project cards → commit/push or stash per area.",
            }
        )

    levels = {c["level"] for c in checks}
    if "blocked" in levels or not repo.get("is_git"):
        verdict = "blocked"
        verdict_label = "Not ready — fix blockers first"
    elif "warn" in levels:
        verdict = "caution"
        verdict_label = "Caution — reboot keeps session history, but protect uncommitted/unpushed work first"
    else:
        verdict = "ready"
        verdict_label = "Ready — safe to reboot/update; resume sessions afterward"

    exit_steps: list[str] = []
    if dirty_n:
        exit_steps.append(
            "Commit or stash uncommitted files in personal-workspace (see dirty list)."
        )
    if ahead and ahead > 0:
        exit_steps.append("git push  # save commits to remote")
    if live_sessions:
        exit_steps.append(
            "Let any mid-flight agent finish the current tool turn (avoid killing mid-write)."
        )
    exit_steps.append(
        "Copy/save Resume kit session IDs (or rely on `grok --resume` / TUI /resume)."
    )
    if listeners:
        exit_steps.append("Optional: stop local servers (reboot will kill them anyway).")
    exit_steps.append("Install updates / reboot.")
    exit_steps.append(
        f"After reboot: cd {repo.get('path') or WORKSPACE_ROOT} && grok --resume <session-id>"
    )

    return {
        "verdict": verdict,
        "verdict_label": verdict_label,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "exit_steps": exit_steps,
        "counts": {
            "dirty_files": dirty_n,
            "ahead": ahead,
            "behind": behind,
            "live_sessions": len(live_sessions),
            "persisted_sessions": len(persisted),
            "stashes": len(stashes),
            "listeners": len(listeners),
            "dirty_projects": len(dirty_projects),
        },
    }


def build_resume_kit(workspace_sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Commands and IDs to restore context after reboot."""
    items = []
    for s in workspace_sessions:
        items.append(
            {
                "id": s["id"],
                "title": s.get("title"),
                "resume_cmd": s.get("resume_cmd") or f"grok --resume {s['id']}",
                "cwd": s.get("resume_cwd") or s.get("cwd"),
                "disk_path": s.get("disk_path"),
                "persisted": s.get("persisted"),
                "active": s.get("active"),
                "pid_alive": s.get("pid_alive"),
                "areas": s.get("areas") or [],
                "last_active_at": s.get("last_active_at"),
            }
        )
    return {
        "note": (
            "Grok saves every session under ~/.grok/sessions automatically. "
            "Reboot ends live processes; history and tool logs remain. "
            "Resume from the same cwd with the command below, or use the TUI /resume picker."
        ),
        "sessions": items,
    }


def area_for_workspace_file(file_path: str, workspace: Path) -> Optional[str]:
    """Map a file path to a top-level project area name under workspace, or None.

    Meta content dirs (strategy, initiatives, ops) map to ``_meta`` so edits still
    count as workspace activity without creating project-area cards.
    """
    try:
        rel = Path(file_path).resolve().relative_to(workspace.resolve())
    except (ValueError, OSError):
        # try non-resolved
        try:
            rel = Path(file_path).relative_to(workspace)
        except ValueError:
            return None
    if not rel.parts:
        return None
    top = rel.parts[0]
    if top in _SKIP_TOP or top.startswith("."):
        return None
    if top in META_CONTENT_DIRS:
        return "_meta"
    # Only treat as a project area if it's a known execution project dir
    known_names = {p.name for p in known_project_dirs(workspace)}
    if top in known_names:
        return top
    candidate = workspace / top
    if candidate.is_dir() and top not in META_CONTENT_DIRS:
        # Unknown top-level dir: still allow if it looks like a project
        return top
    # Root-level files (README.md, launch.py) → special bucket
    return "_root"


def collect_workspace_dashboard(
    workspace: Path = WORKSPACE_ROOT,
    grok_home: Optional[Path] = None,
    *,
    include_subagents: bool = False,
    only_touched: bool = False,
) -> dict[str, Any]:
    """Status payload for personal-workspace + Grok-matched sub-projects.

    only_touched: if True, omit project dirs with zero Grok edits.
    """
    workspace = Path(workspace).resolve()
    grok_home = Path(grok_home or DEFAULT_GROK_HOME)
    sessions_root = grok_home / "sessions"
    active = load_active_sessions(grok_home)

    repo = collect_repo_status(workspace)
    dirty_paths = repo.get("dirty_paths") or []

    # area_name -> edit count / files / sessions
    area_edits: dict[str, int] = defaultdict(int)
    area_files: dict[str, Counter] = defaultdict(Counter)
    area_sessions: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    all_sessions: list[dict[str, Any]] = []
    workspace_session_ids: set[str] = set()

    if sessions_root.is_dir():
        for workspace_dir in sessions_root.iterdir():
            if not workspace_dir.is_dir() or workspace_dir.name.startswith("."):
                continue
            for sdir in workspace_dir.iterdir():
                if not sdir.is_dir() or not (sdir / "summary.json").is_file():
                    continue
                try:
                    summary = json.loads(
                        (sdir / "summary.json").read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    continue
                kind = summary.get("session_kind") or "primary"
                if not include_subagents and kind in ("subagent", "subagent_fork"):
                    continue
                meta = _session_meta(summary, sdir, active, grok_home)
                sid = meta["id"]
                areas_touched: set[str] = set()
                hunks = sdir / "hunk_records.jsonl"
                if hunks.is_file():
                    try:
                        with hunks.open(encoding="utf-8") as fh:
                            for line in fh:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    rec = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                fp = rec.get("filePath") or ""
                                area = area_for_workspace_file(fp, workspace)
                                if not area:
                                    continue
                                area_edits[area] += 1
                                area_files[area][fp] += 1
                                area_sessions[area][sid] = meta
                                areas_touched.add(area)
                                workspace_session_ids.add(sid)
                    except OSError:
                        pass
                meta["areas"] = sorted(a for a in areas_touched if a != "_root")
                meta["touches_workspace"] = bool(areas_touched)
                all_sessions.append(meta)

    # Build project list from known execution dirs + hunk areas (never meta)
    known = {p.name: p for p in known_project_dirs(workspace)}
    area_names = set(known) | {
        a
        for a in area_edits
        if a not in ("_root", "_meta") and a not in META_CONTENT_DIRS
    }

    projects: list[dict[str, Any]] = []
    for name in sorted(area_names, key=lambda n: (-area_edits.get(n, 0), n.lower())):
        path = known.get(name) or (workspace / name)
        if not path.exists() and name not in area_edits:
            continue
        edits = area_edits.get(name, 0)
        if only_touched and edits == 0:
            continue
        sessions = list(area_sessions.get(name, {}).values())
        sessions.sort(key=lambda s: s.get("last_active_at") or "", reverse=True)
        rel = name
        dirty = path_is_dirty(rel, dirty_paths)
        dirty_in_area = [
            p for p in dirty_paths if p == rel or p.startswith(rel + "/")
        ]
        exit_ok = not dirty  # area-level: no uncommitted files in this tree
        projects.append(
            {
                "name": name,
                "path": str(path.resolve()) if path.exists() else str(workspace / name),
                "relative": rel,
                "exists": path.exists(),
                "edit_count": edits,
                "dirty": dirty,
                "dirty_files": dirty_in_area[:40],
                "dirty_file_count": len(dirty_in_area),
                "sessions": sessions,
                "session_count": len(sessions),
                "active_session_count": sum(
                    1 for s in sessions if s.get("active") and s.get("pid_alive")
                ),
                "last_session_at": sessions[0].get("last_active_at") if sessions else None,
                "top_files": [
                    {"path": fp, "edits": n}
                    for fp, n in area_files.get(name, Counter()).most_common(6)
                ],
                "status_label": (
                    f"dirty ({len(dirty_in_area)})" if dirty else "clean"
                )
                + (f", {edits} grok edits" if edits else ", no grok edits"),
                "exit_ready": exit_ok,
                "exit_note": (
                    "Clean — no uncommitted work in this area."
                    if exit_ok
                    else f"Commit/stash {len(dirty_in_area)} file(s) before reboot to protect this area."
                ),
                "resume_cmds": [
                    s.get("resume_cmd") for s in sessions if s.get("resume_cmd")
                ][:5],
            }
        )

    # Sort: active sessions, then edits, then name
    projects.sort(
        key=lambda p: (
            p.get("active_session_count") or 0,
            p.get("edit_count") or 0,
            p.get("last_session_at") or "",
        ),
        reverse=True,
    )

    ws_sessions = [s for s in all_sessions if s.get("touches_workspace")]
    ws_sessions.sort(key=lambda s: s.get("last_active_at") or "", reverse=True)
    orphan = [s for s in all_sessions if not s.get("touches_workspace")]
    orphan.sort(key=lambda s: s.get("last_active_at") or "", reverse=True)

    root_edits = area_edits.get("_root", 0)
    root_sessions = list(area_sessions.get("_root", {}).values())
    root_sessions.sort(key=lambda s: s.get("last_active_at") or "", reverse=True)

    stashes = collect_stashes(workspace)
    listeners = list_listening_ports()
    readiness = build_readiness(repo, projects, ws_sessions, stashes, listeners)
    resume_kit = build_resume_kit(ws_sessions)

    try:
        from git_workflow import collect_branch_status  # noqa: WPS433

        branches = collect_branch_status(workspace)
    except Exception as e:
        branches = {"error": str(e), "current": repo.get("branch"), "branches": []}

    # Point exit steps at automation
    if readiness.get("counts", {}).get("dirty_files"):
        readiness.setdefault("exit_steps", [])
        if not any("protect" in s.lower() or "sync" in s.lower() for s in readiness["exit_steps"]):
            readiness["exit_steps"].insert(
                0,
                "Auto-protect: python3 projects-dashboard/git_workflow.py sync",
            )

    strategy_focus = load_strategy_focus(workspace)

    return {
        "ok": True,
        "mode": "workflow-management",
        "purpose": (
            "Pre-reset readiness for personal-workspace: protect uncommitted/unpushed "
            "work and preserve Grok session context across reboots/system updates."
        ),
        "workspace": repo,
        "projects": projects,
        "count": len(projects),
        "meta_content_dirs": sorted(META_CONTENT_DIRS),
        "strategy_focus": strategy_focus,
        "readiness": readiness,
        "resume_kit": resume_kit,
        "branches": branches,
        "stashes": stashes,
        "listeners": listeners,
        "automation": {
            "sync": "python3 projects-dashboard/git_workflow.py sync",
            "protect": "python3 projects-dashboard/git_workflow.py protect",
            "start_work": "python3 projects-dashboard/git_workflow.py start <area>",
            "session_index": "python3 projects-dashboard/session_backup.py index",
            "session_archive": "python3 projects-dashboard/session_backup.py archive",
            "note": (
                "After completing a unit of work, run sync (or dashboard Protect & push). "
                "It refreshes ops/session-index, commits on work/<area> if needed, and pushes. "
                "Full Grok transcripts stay in ~/.grok/sessions (not git); index is resume metadata only."
            ),
        },
        "root_edits": {
            "edit_count": root_edits,
            "sessions": root_sessions,
            "dirty": path_is_dirty("", dirty_paths)
            and any("/" not in p for p in dirty_paths),
        },
        "workspace_sessions": ws_sessions,
        "orphan_sessions": orphan,
        "active_session_ids": list(active.keys()),
        "session_count": len(ws_sessions),
        "grok_home": str(grok_home),
    }




# Back-compat aliases used by older imports / CLI
def collect_dashboard_projects(**kwargs) -> dict[str, Any]:
    return collect_workspace_dashboard(**kwargs)


if __name__ == "__main__":
    import sys

    only = "--only-touched" in sys.argv
    json.dump(
        collect_workspace_dashboard(only_touched=only),
        sys.stdout,
        indent=2,
    )
    print()
