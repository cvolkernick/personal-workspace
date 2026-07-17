"""Repo discovery and git status collection for the projects dashboard.

Callable without the UI — unit tests and the HTTP API both use these.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


# Default roots: personal-workspace itself + known sibling git projects under home.
_HOME = Path.home()
_PERSONAL = Path(__file__).resolve().parent.parent

DEFAULT_ROOTS: list[str] = [
    str(_PERSONAL),
    str(_HOME / "tab-out"),
    str(_HOME / "clawd"),
    str(_HOME / "AwesomeProject"),
    str(_HOME / "PycharmProjects" / "HNTpayments"),
]


def _run_git(repo: Path, *args: str, timeout: float = 8.0) -> tuple[int, str, str]:
    """Run git in repo; return (returncode, stdout, stderr)."""
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


def is_git_repo(path: Path) -> bool:
    """True if path is a git working tree (not a bare repo alone)."""
    if not path.is_dir():
        return False
    code, out, _ = _run_git(path, "rev-parse", "--is-inside-work-tree")
    return code == 0 and out.lower() == "true"


def discover_repos(
    roots: list[str] | None = None,
    *,
    max_depth: int = 2,
) -> list[Path]:
    """Find git repos under configured roots.

    - If a root itself is a git repo, include it.
    - Also scan immediate children (and up to max_depth) for nested .git dirs.
    - Skips common noise: node_modules, .venv, venv, __pycache__, .git internals.
    """
    if roots is None:
        roots = list(DEFAULT_ROOTS)

    skip_names = {
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        "Library",
        ".Trash",
        "site-packages",
        "dist",
        "build",
        ".tox",
    }
    found: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            found.append(p)

    for root_s in roots:
        root = Path(os.path.expanduser(root_s)).resolve()
        if not root.is_dir():
            continue
        if is_git_repo(root):
            add(root)
        # Depth-limited walk for nested repos
        if max_depth <= 0:
            continue
        for dirpath, dirnames, _ in os.walk(root):
            rel = Path(dirpath).relative_to(root)
            depth = len(rel.parts)
            # Prune skip names in-place
            dirnames[:] = [d for d in dirnames if d not in skip_names and not d.startswith(".")]
            if depth >= max_depth:
                dirnames.clear()
                continue
            p = Path(dirpath)
            if p != root and (p / ".git").exists() and is_git_repo(p):
                add(p)
                # Don't recurse into a found repo
                dirnames.clear()

    found.sort(key=lambda p: str(p).lower())
    return found


def collect_repo_status(path: str | Path) -> dict[str, Any]:
    """Inspect one git repo; return status dict with path, remotes, branch, dirty, ahead/behind.

    Non-git paths return a minimal dict with error set.
    """
    repo = Path(path).expanduser().resolve()
    name = repo.name
    result: dict[str, Any] = {
        "name": name,
        "path": str(repo),
        "is_git": False,
        "branch": None,
        "remotes": [],
        "dirty": None,
        "ahead": None,
        "behind": None,
        "upstream": None,
        "status_label": "not a git repo",
        "error": None,
    }

    if not repo.is_dir():
        result["error"] = "path does not exist"
        result["status_label"] = "missing"
        return result

    if not is_git_repo(repo):
        result["error"] = "not a git repository"
        return result

    result["is_git"] = True

    # Current branch (or detached HEAD short sha)
    code, branch, err = _run_git(repo, "branch", "--show-current")
    if code != 0:
        result["error"] = err or "failed to read branch"
        result["status_label"] = "error"
        return result
    if branch:
        result["branch"] = branch
    else:
        code2, sha, _ = _run_git(repo, "rev-parse", "--short", "HEAD")
        result["branch"] = f"detached@{sha}" if code2 == 0 and sha else "detached HEAD"

    # Remotes: list of {name, url, fetch/push}
    code, remote_out, _ = _run_git(repo, "remote", "-v")
    remotes: list[dict[str, str]] = []
    seen_remote: set[tuple[str, str]] = set()
    if code == 0 and remote_out:
        for line in remote_out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                rname, url = parts[0], parts[1]
                key = (rname, url)
                if key not in seen_remote:
                    seen_remote.add(key)
                    remotes.append({"name": rname, "url": url})
    result["remotes"] = remotes

    # Dirty working tree
    code, status_porcelain, _ = _run_git(repo, "status", "--porcelain")
    dirty = code == 0 and bool(status_porcelain)
    result["dirty"] = dirty if code == 0 else None

    # Upstream / ahead-behind
    code, upstream, _ = _run_git(repo, "rev-parse", "--abbrev-ref", "@{upstream}")
    if code == 0 and upstream:
        result["upstream"] = upstream
        code_ab, ab_out, _ = _run_git(
            repo, "rev-list", "--left-right", "--count", f"{upstream}...HEAD"
        )
        # rev-list --left-right --count A...B → behind ahead (left=behind, right=ahead)
        # Actually: left-right counts commits reachable from left but not right, then right but not left
        # For upstream...HEAD: left=upstream only (behind), right=HEAD only (ahead)
        if code_ab == 0 and ab_out:
            parts = ab_out.split()
            if len(parts) >= 2:
                try:
                    result["behind"] = int(parts[0])
                    result["ahead"] = int(parts[1])
                except ValueError:
                    pass

    # Human-readable status label
    result["status_label"] = _status_label(result)
    return result


def _status_label(r: dict[str, Any]) -> str:
    if r.get("error"):
        return "error"
    if not r.get("is_git"):
        return "not a git repo"
    parts: list[str] = []
    if r.get("dirty"):
        parts.append("dirty")
    else:
        parts.append("clean")
    ahead = r.get("ahead")
    behind = r.get("behind")
    if ahead is not None and behind is not None:
        if ahead == 0 and behind == 0:
            parts.append("synced")
        else:
            if ahead:
                parts.append(f"ahead {ahead}")
            if behind:
                parts.append(f"behind {behind}")
    elif not r.get("remotes"):
        parts.append("no remote")
    elif not r.get("upstream"):
        parts.append("no upstream")
    return ", ".join(parts)


def collect_all_projects(roots: list[str] | None = None, *, max_depth: int = 2) -> dict[str, Any]:
    """Discover repos and collect status for each. Returns payload for API/UI."""
    repos = discover_repos(roots, max_depth=max_depth)
    projects = [collect_repo_status(p) for p in repos]
    return {
        "ok": True,
        "count": len(projects),
        "roots": [str(Path(os.path.expanduser(r)).resolve()) if Path(os.path.expanduser(r)).exists() else r for r in (roots or DEFAULT_ROOTS)],
        "projects": projects,
    }


if __name__ == "__main__":
    import json
    import sys

    payload = collect_all_projects()
    json.dump(payload, sys.stdout, indent=2)
    print()
