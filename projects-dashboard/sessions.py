"""Match Grok Build sessions to local projects (git roots).

Reads ~/.grok/sessions/<encoded-cwd>/<session-id>/summary.json and
hunk_records.jsonl (file edits). Maps edited file paths → git repo root,
then groups sessions under each project.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

DEFAULT_GROK_HOME = Path(os.environ.get("GROK_HOME", Path.home() / ".grok"))

# Paths we never treat as "projects"
_SKIP_PREFIX_NAMES = (".grok", "Library", ".Trash", ".cache", "node_modules")


def _is_under_grok(p: str, grok_home: Path) -> bool:
    try:
        Path(p).resolve().relative_to(grok_home.resolve())
        return True
    except (ValueError, OSError):
        return p.startswith(str(grok_home))


def _skip_path(p: str, grok_home: Path, *, allow_git_root: bool = False) -> bool:
    """Noise paths we never treat as projects.

    Git worktrees under /tmp or /var/folders are allowed when allow_git_root is True
    (temp fixtures / legitimate checkouts). Paths inside GROK_HOME are always skipped.
    """
    if not p:
        return True
    if _is_under_grok(p, grok_home):
        return True
    parts = Path(p).parts
    for name in _SKIP_PREFIX_NAMES:
        if name in parts:
            return True
    # System areas without a real checkout
    if not allow_git_root:
        for prefix in ("/System/", "/usr/", "/bin/", "/sbin/"):
            if p.startswith(prefix):
                return True
    # Dot-dirs under home (e.g. ~/.config) — not projects
    home = Path.home()
    try:
        rel = Path(p).resolve().relative_to(home)
        if rel.parts and rel.parts[0].startswith("."):
            return True
    except (ValueError, OSError):
        pass
    return False


def _run_git(cwd: Path, *args: str, timeout: float = 4.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return proc.returncode, (proc.stdout or "").strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 1, ""


def git_toplevel(path: Path, cache: dict[str, Optional[str]]) -> Optional[str]:
    """Nearest git work tree root for path (or its parent). Cached."""
    key = str(path)
    if key in cache:
        return cache[key]
    probe = path if path.is_dir() else path.parent
    # Walk up caching negatives lightly
    cur = probe
    for _ in range(16):
        ck = str(cur)
        if ck in cache:
            cache[key] = cache[ck]
            return cache[key]
        if (cur / ".git").exists():
            code, out = _run_git(cur, "rev-parse", "--show-toplevel")
            top = out if code == 0 and out else str(cur)
            cache[key] = top
            cache[ck] = top
            return top
        if cur == cur.parent:
            break
        cur = cur.parent
    cache[key] = None
    return None


def project_root_for_file(
    file_path: str,
    *,
    grok_home: Path,
    cache: dict[str, Optional[str]],
) -> Optional[str]:
    """Map an edited file path to a project (git root). Skip noise paths."""
    # Edits inside Grok's own session/store dirs never map to a user project
    if not file_path or _is_under_grok(file_path, grok_home):
        return None
    p = Path(file_path)
    probe = p if p.exists() and p.is_dir() else p.parent
    top = git_toplevel(probe, cache)
    if top and not _skip_path(top, grok_home, allow_git_root=True):
        return top
    return None


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


def _session_meta(summary: dict[str, Any], session_dir: Path) -> dict[str, Any]:
    info = summary.get("info") or {}
    sid = info.get("id") or session_dir.name
    return {
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
    }


def iter_session_dirs(sessions_root: Path):
    if not sessions_root.is_dir():
        return
    for workspace in sorted(sessions_root.iterdir()):
        if not workspace.is_dir() or workspace.name.startswith("."):
            continue
        # skip sqlite etc
        if workspace.suffix in (".sqlite", ".db"):
            continue
        cwd_decoded = unquote(workspace.name)
        for sdir in workspace.iterdir():
            if not sdir.is_dir():
                continue
            if not (sdir / "summary.json").is_file():
                continue
            yield cwd_decoded, sdir


def collect_session_project_map(
    grok_home: Optional[Path] = None,
    *,
    include_subagents: bool = False,
) -> dict[str, Any]:
    """Scan Grok sessions and group by project path.

    Returns:
      {
        "ok": True,
        "grok_home": "...",
        "projects": {
           "/path/to/repo": {
              "path": "...",
              "edit_count": N,
              "areas": [{"name": "subdir", "edits": n}, ...],
              "sessions": [ {...}, ... ],
              "top_files": [{"path": "...", "edits": n}, ...],
           }
        },
        "sessions": [ all parent sessions with project links ],
        "orphan_sessions": [ sessions with no project edits ],
        "active_session_ids": [...],
      }
    """
    grok_home = Path(grok_home or DEFAULT_GROK_HOME)
    sessions_root = grok_home / "sessions"
    active = load_active_sessions(grok_home)
    git_cache: dict[str, Optional[str]] = {}

    # path -> accumulators
    proj_edits: dict[str, int] = defaultdict(int)
    proj_files: dict[str, Counter] = defaultdict(Counter)
    proj_sessions: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    all_sessions: list[dict[str, Any]] = []

    for _cwd_decoded, sdir in iter_session_dirs(sessions_root):
        try:
            summary = json.loads((sdir / "summary.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        kind = summary.get("session_kind") or "primary"
        if not include_subagents and kind in ("subagent", "subagent_fork"):
            continue
        meta = _session_meta(summary, sdir)
        sid = meta["id"]
        meta["active"] = sid in active
        if sid in active:
            meta["active_pid"] = active[sid].get("pid")
            meta["opened_at"] = active[sid].get("opened_at")
        projects_touched: set[str] = set()

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
                        root = project_root_for_file(
                            fp, grok_home=grok_home, cache=git_cache
                        )
                        if not root:
                            continue
                        proj_edits[root] += 1
                        proj_files[root][fp] += 1
                        proj_sessions[root][sid] = meta
                        projects_touched.add(root)
            except OSError:
                pass

        meta["projects"] = sorted(projects_touched)
        all_sessions.append(meta)

    projects_out: dict[str, Any] = {}
    for path, edits in proj_edits.items():
        files = proj_files[path]
        # Areas = first path segment under the git root
        areas: Counter = Counter()
        root_p = Path(path)
        for fp, n in files.items():
            try:
                rel = Path(fp).resolve().relative_to(root_p)
            except (ValueError, OSError):
                try:
                    rel = Path(fp).relative_to(path)
                except ValueError:
                    continue
            if rel.parts:
                areas[rel.parts[0]] += n
        sessions = list(proj_sessions[path].values())
        sessions.sort(key=lambda s: s.get("last_active_at") or "", reverse=True)
        top_files = [
            {"path": fp, "edits": n}
            for fp, n in files.most_common(8)
        ]
        projects_out[path] = {
            "path": path,
            "name": Path(path).name,
            "edit_count": edits,
            "areas": [{"name": k, "edits": v} for k, v in areas.most_common(12)],
            "sessions": sessions,
            "session_count": len(sessions),
            "top_files": top_files,
            "last_active_at": sessions[0].get("last_active_at") if sessions else None,
            "active_session_count": sum(1 for s in sessions if s.get("active")),
        }

    orphan = [s for s in all_sessions if not s.get("projects")]
    orphan.sort(key=lambda s: s.get("last_active_at") or "", reverse=True)
    all_sessions.sort(key=lambda s: s.get("last_active_at") or "", reverse=True)

    return {
        "ok": True,
        "grok_home": str(grok_home),
        "projects": projects_out,
        "sessions": all_sessions,
        "orphan_sessions": orphan,
        "active_session_ids": list(active.keys()),
    }


def collect_grok_projects(
    grok_home: Optional[Path] = None,
    *,
    collect_repo_status_fn=None,
) -> dict[str, Any]:
    """Full dashboard payload: Grok-matched projects + git status per project.

    collect_repo_status_fn defaults to collectors.collect_repo_status (lazy import).
    """
    if collect_repo_status_fn is None:
        from collectors import collect_repo_status as collect_repo_status_fn  # noqa: WPS433

    mapped = collect_session_project_map(grok_home)
    projects_map = mapped["projects"]

    # Sort: active sessions first, then last_active, then edit_count
    ordered = sorted(
        projects_map.items(),
        key=lambda kv: (
            kv[1].get("active_session_count") or 0,
            kv[1].get("last_active_at") or "",
            kv[1].get("edit_count") or 0,
        ),
        reverse=True,
    )

    projects: list[dict[str, Any]] = []
    for path, info in ordered:
        status = collect_repo_status_fn(path)
        status.update(
            {
                "edit_count": info["edit_count"],
                "areas": info["areas"],
                "sessions": info["sessions"],
                "session_count": info["session_count"],
                "active_session_count": info["active_session_count"],
                "top_files": info["top_files"],
                "last_session_at": info.get("last_active_at"),
                "source": "grok-session",
            }
        )
        projects.append(status)

    return {
        "ok": True,
        "mode": "grok-sessions",
        "count": len(projects),
        "grok_home": mapped["grok_home"],
        "projects": projects,
        "orphan_sessions": mapped["orphan_sessions"],
        "active_session_ids": mapped["active_session_ids"],
        "session_count": len(mapped["sessions"]),
    }


if __name__ == "__main__":
    import sys

    json.dump(collect_grok_projects(), sys.stdout, indent=2)
    print()
