"""personal-workspace monorepo status + Grok Build project areas.

Strict scope: this dashboard is a status viewer for the personal-workspace repo
only. "Projects" are top-level areas inside that repo (e.g. resistance-dashboard,
financial-command, treasury) matched from Grok session edit hunks.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

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
        for line in porcelain.splitlines():
            # format: XY path  or XY orig -> path
            path = line[3:].strip() if len(line) > 3 else ""
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
    """Top-level directories that look like projects (always listed if they exist)."""
    out: list[Path] = []
    if not workspace.is_dir():
        return out
    for child in sorted(workspace.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name in _SKIP_TOP or child.name.startswith("."):
            continue
        out.append(child)
    return out


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


def _session_meta(summary: dict[str, Any], session_dir: Path, active: dict) -> dict[str, Any]:
    info = summary.get("info") or {}
    sid = info.get("id") or session_dir.name
    meta = {
        "id": sid,
        "title": summary.get("generated_title")
        or summary.get("session_summary")
        or sid,
        "last_active_at": summary.get("last_active_at") or summary.get("updated_at"),
        "created_at": summary.get("created_at"),
        "cwd": info.get("cwd"),
        "agent_name": summary.get("agent_name"),
        "model": summary.get("current_model_id"),
        "num_chat_messages": summary.get("num_chat_messages"),
        "session_kind": summary.get("session_kind") or "primary",
        "active": sid in active,
    }
    if sid in active:
        meta["active_pid"] = active[sid].get("pid")
        meta["opened_at"] = active[sid].get("opened_at")
    return meta


def area_for_workspace_file(file_path: str, workspace: Path) -> Optional[str]:
    """Map a file path to a top-level project area name under workspace, or None."""
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
    # Only treat as a project area if it's a directory (or was)
    candidate = workspace / top
    if candidate.is_dir() or top in {p.name for p in known_project_dirs(workspace)}:
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
                meta = _session_meta(summary, sdir, active)
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

    # Build project list from known dirs + any extra areas seen in hunks
    known = {p.name: p for p in known_project_dirs(workspace)}
    area_names = set(known) | {a for a in area_edits if a != "_root"}

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
                "active_session_count": sum(1 for s in sessions if s.get("active")),
                "last_session_at": sessions[0].get("last_active_at") if sessions else None,
                "top_files": [
                    {"path": fp, "edits": n}
                    for fp, n in area_files.get(name, Counter()).most_common(6)
                ],
                "status_label": (
                    f"dirty ({len(dirty_in_area)})" if dirty else "clean"
                )
                + (f", {edits} grok edits" if edits else ", no grok edits"),
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

    return {
        "ok": True,
        "mode": "personal-workspace",
        "workspace": repo,
        "projects": projects,
        "count": len(projects),
        "root_edits": {
            "edit_count": root_edits,
            "sessions": root_sessions,
            "dirty": path_is_dirty("", dirty_paths) and any(
                "/" not in p for p in dirty_paths
            ),
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
