"""Branch-aware git workflow for personal-workspace.

Conventions
-----------
- ``master`` — integration branch; keep green and pushed.
- ``work/<area>`` — active work for a monorepo top-level area
  (e.g. work/treasury, work/projects-dashboard).
- ``feature/<slug>`` — optional longer-lived features (legacy pattern OK).

``protect_work`` commits dirty durable files and pushes the current branch.
Agents should call this (or the dashboard button) when a unit of work completes
instead of relying on memory.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

# Paths never auto-committed (secrets / local env)
_NEVER_COMMIT = {
    ".env",
    ".env.local",
    "credentials.json",
    "auth.json",
}
_NEVER_COMMIT_PREFIXES = (
    "resistance-dashboard/.env",
)

# Generated snapshots that are OK to commit but optional
_SNAPSHOTISH = re.compile(
    r"(snapshots?/.*\.json$|treasury_latest\.json$|_latest\.json$)",
    re.I,
)


def _run(
    repo: Path, *args: str, check: bool = False, timeout: float = 60.0
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        out, err = (proc.stdout or "").strip(), (proc.stderr or "").strip()
        if check and proc.returncode != 0:
            raise RuntimeError(err or out or f"git {' '.join(args)} failed")
        return proc.returncode, out, err
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        if check:
            raise
        return 1, "", str(e)


def branch_name_for_area(area: str) -> str:
    area = re.sub(r"[^a-zA-Z0-9._-]+", "-", area.strip()).strip("-").lower()
    return f"work/{area}"


def area_from_path(rel: str) -> str:
    rel = rel.replace("\\", "/").lstrip("./")
    if "/" in rel:
        return rel.split("/", 1)[0]
    return "_root"


def should_skip_path(rel: str) -> bool:
    name = Path(rel).name
    if name in _NEVER_COMMIT:
        return True
    for p in _NEVER_COMMIT_PREFIXES:
        if rel.startswith(p):
            return True
    return False


def collect_branch_status(repo: Path = WORKSPACE_ROOT) -> dict[str, Any]:
    """Current branch + local/remote branch inventory with ahead/behind."""
    repo = Path(repo).resolve()
    code, head, _ = _run(repo, "branch", "--show-current")
    current = head if code == 0 and head else None

    code, porcelain, _ = _run(repo, "status", "--porcelain")
    dirty = bool(porcelain) if code == 0 else None

    # Porcelain v2-ish via for-each-ref
    code, refs, _ = _run(
        repo,
        "for-each-ref",
        "--format=%(refname:short)|%(upstream:short)|%(upstream:track)|%(objectname:short)|%(committerdate:iso8601)",
        "refs/heads",
    )
    branches: list[dict[str, Any]] = []
    if code == 0 and refs:
        for line in refs.splitlines():
            parts = line.split("|")
            if len(parts) < 5:
                continue
            name, upstream, track, sha, cdate = parts[0], parts[1], parts[2], parts[3], parts[4]
            ahead = behind = None
            if track:
                # e.g. [ahead 1, behind 2] or [gone]
                m_a = re.search(r"ahead\s+(\d+)", track)
                m_b = re.search(r"behind\s+(\d+)", track)
                if m_a:
                    ahead = int(m_a.group(1))
                if m_b:
                    behind = int(m_b.group(1))
                if "gone" in track:
                    upstream = upstream or "(gone)"
            branches.append(
                {
                    "name": name,
                    "current": name == current,
                    "upstream": upstream or None,
                    "ahead": ahead,
                    "behind": behind,
                    "sha": sha,
                    "committerdate": cdate,
                    "is_work": name.startswith("work/"),
                    "is_feature": name.startswith("feature/"),
                    "is_master": name in ("master", "main"),
                }
            )

    # Remote branches not checked out
    code, remotes, _ = _run(
        repo,
        "for-each-ref",
        "--format=%(refname:short)|%(objectname:short)",
        "refs/remotes/origin",
    )
    remote_branches: list[dict[str, str]] = []
    if code == 0 and remotes:
        for line in remotes.splitlines():
            if "|" not in line:
                continue
            name, sha = line.split("|", 1)
            if name.endswith("/HEAD"):
                continue
            remote_branches.append({"name": name, "sha": sha})

    work_branches = [b for b in branches if b["is_work"] or b["is_feature"]]
    unpushed = [
        b for b in branches if b.get("ahead") and b["ahead"] > 0
    ] + [b for b in branches if b.get("upstream") is None and not b["is_master"]]

    return {
        "current": current,
        "dirty": dirty,
        "branches": branches,
        "work_branches": work_branches,
        "remote_branches": remote_branches,
        "unpushed_local": [
            {"name": b["name"], "ahead": b.get("ahead"), "upstream": b.get("upstream")}
            for b in branches
            if (b.get("ahead") or 0) > 0
            or (not b.get("upstream") and not b.get("is_master"))
        ],
        "convention": {
            "master": "Integration branch — merge work/* when stable, keep pushed",
            "work/<area>": "Active work for a top-level monorepo area",
            "feature/<slug>": "Longer-lived features (optional)",
        },
    }


def start_work(
    area: str,
    repo: Path = WORKSPACE_ROOT,
    *,
    from_branch: str = "master",
    create: bool = True,
) -> dict[str, Any]:
    """Checkout or create work/<area> from from_branch."""
    repo = Path(repo).resolve()
    branch = branch_name_for_area(area)
    code, existing, _ = _run(repo, "rev-parse", "--verify", branch)
    if code == 0:
        code2, _, err = _run(repo, "checkout", branch)
        if code2 != 0:
            return {"ok": False, "error": err or "checkout failed", "branch": branch}
        return {
            "ok": True,
            "branch": branch,
            "created": False,
            "message": f"Checked out existing {branch}",
        }

    if not create:
        return {"ok": False, "error": f"branch {branch} does not exist", "branch": branch}

    # Ensure from_branch is available
    _run(repo, "fetch", "origin", from_branch)
    code, _, err = _run(repo, "checkout", from_branch)
    if code != 0:
        # try local only
        pass
    _run(repo, "pull", "--ff-only", "origin", from_branch)
    code, _, err = _run(repo, "checkout", "-b", branch)
    if code != 0:
        return {"ok": False, "error": err or "create branch failed", "branch": branch}
    return {
        "ok": True,
        "branch": branch,
        "created": True,
        "message": f"Created and checked out {branch} from {from_branch}",
    }


def parse_porcelain_path(line: str) -> Optional[str]:
    """Parse a path from `git status --porcelain` (v1) output.

    Standard form is ``XY PATH`` (2 status chars + space + path). Some lines
    appear as ``M path`` with a single space; naive ``line[3:]`` then yields a
    corrupted path (e.g. ``ps/backlog/...``) that never stages.
    """
    if not line:
        return None
    # Untracked: "?? path" or "!! path"
    if line.startswith("?? ") or line.startswith("!! "):
        path = line[3:].strip()
    elif len(line) >= 3 and line[2] == " ":
        # Normal XY + space
        path = line[3:].strip()
    elif len(line) >= 2 and line[1] == " ":
        # Single-letter status + space (defensive)
        path = line[2:].strip()
    else:
        parts = line.split(None, 1)
        path = parts[1].strip() if len(parts) > 1 else ""
    if path.startswith('"') and path.endswith('"'):
        # git quotes unusual paths; strip quotes (good enough for our tree)
        path = path[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip()
    return path or None


def dirty_paths(repo: Path = WORKSPACE_ROOT) -> list[str]:
    code, porcelain, _ = _run(repo, "status", "--porcelain")
    if code != 0 or not porcelain:
        return []
    paths = []
    for line in porcelain.splitlines():
        path = parse_porcelain_path(line)
        if path:
            paths.append(path)
    return paths


def protect_work(
    repo: Path = WORKSPACE_ROOT,
    *,
    message: Optional[str] = None,
    push: bool = True,
    include_snapshots: bool = True,
    paths: Optional[list[str]] = None,
    ensure_work_branch: bool = True,
) -> dict[str, Any]:
    """Stage durable changes, commit on an appropriate branch, optionally push.

    - Skips secrets.
    - If on master with dirty area-scoped files and ensure_work_branch, switches
      to work/<primary-area> first (creates if needed).
    - Snapshot JSON can be included (default) so reboot-safe state is remote.
    """
    repo = Path(repo).resolve()
    all_dirty = dirty_paths(repo)
    if paths is not None:
        candidates = [p for p in paths if p in all_dirty or True]
        # still only stage if they exist as dirty or as files
        candidates = paths
    else:
        candidates = all_dirty

    to_stage: list[str] = []
    skipped: list[str] = []
    for p in candidates:
        if should_skip_path(p):
            skipped.append(p)
            continue
        if not include_snapshots and _SNAPSHOTISH.search(p):
            skipped.append(p)
            continue
        to_stage.append(p)

    if not to_stage and not all_dirty:
        # maybe only need push
        br = collect_branch_status(repo)
        if push and br.get("current"):
            return _push_current(
                repo,
                br,
                extra={
                    "staged": [],
                    "committed": False,
                    "message": "Working tree clean — pushed if needed",
                },
            )
        return {
            "ok": True,
            "committed": False,
            "pushed": False,
            "message": "Nothing to protect — working tree clean",
            "branch": br.get("current"),
            "skipped": skipped,
        }

    if not to_stage:
        return {
            "ok": False,
            "error": "All dirty paths were skipped (secrets/snapshots filter)",
            "skipped": skipped,
            "dirty": all_dirty,
            "message": "Nothing staged — dirty files remain",
        }

    # Branch selection
    areas = [area_from_path(p) for p in to_stage if area_from_path(p) != "_root"]
    area_counts: dict[str, int] = {}
    for a in areas:
        area_counts[a] = area_counts.get(a, 0) + 1
    primary_area = (
        max(area_counts, key=area_counts.get) if area_counts else "misc"  # type: ignore[arg-type]
    )

    code, current, _ = _run(repo, "branch", "--show-current")
    current = current if code == 0 else None
    branch_actions: list[str] = []

    if ensure_work_branch and current in ("master", "main", None):
        sw = start_work(primary_area, repo=repo, from_branch=current or "master")
        branch_actions.append(sw.get("message") or str(sw))
        if not sw.get("ok"):
            return {"ok": False, "error": sw.get("error"), "branch_actions": branch_actions}
        current = sw.get("branch")

    # Stage
    for p in to_stage:
        _run(repo, "add", "--", p)

    code, staged, _ = _run(repo, "diff", "--cached", "--name-only")
    staged_list = staged.splitlines() if code == 0 and staged else []
    if not staged_list:
        remaining = dirty_paths(repo)
        br = collect_branch_status(repo)
        # Still dirty after add → treat as failure so UI does not show false "OK"
        if remaining:
            return {
                "ok": False,
                "committed": False,
                "pushed": False,
                "error": (
                    "Nothing staged but working tree still dirty: "
                    + ", ".join(remaining[:12])
                ),
                "message": "Protect failed — dirty paths not staged",
                "dirty": remaining,
                "skipped": skipped,
                "branch": br.get("current"),
                "branch_actions": branch_actions,
            }
        if push:
            return _push_current(
                repo,
                br,
                extra={
                    "staged": [],
                    "committed": False,
                    "message": "Nothing to commit — already clean",
                    "skipped": skipped,
                    "branch_actions": branch_actions,
                },
            )
        return {
            "ok": True,
            "committed": False,
            "message": "Nothing staged",
            "skipped": skipped,
            "branch_actions": branch_actions,
        }

    if not message:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        message = f"protect({primary_area}): auto-save durable work ({ts})"

    code, _, err = _run(repo, "commit", "-m", message)
    if code != 0:
        return {
            "ok": False,
            "error": err or "commit failed",
            "staged": staged_list,
            "branch": current,
            "branch_actions": branch_actions,
        }

    code, sha, _ = _run(repo, "rev-parse", "--short", "HEAD")
    result: dict[str, Any] = {
        "ok": True,
        "committed": True,
        "sha": sha if code == 0 else None,
        "message": message,
        "staged": staged_list,
        "skipped": skipped,
        "branch": current,
        "primary_area": primary_area,
        "branch_actions": branch_actions,
        "pushed": False,
    }

    if push:
        br = collect_branch_status(repo)
        push_result = _push_current(repo, br)
        result["pushed"] = push_result.get("pushed", False)
        result["push"] = push_result
        if not push_result.get("ok"):
            result["ok"] = False
            result["error"] = push_result.get("error")
    return result


def _push_current(
    repo: Path, br: dict[str, Any], extra: Optional[dict] = None
) -> dict[str, Any]:
    current = br.get("current")
    if not current:
        return {"ok": False, "error": "detached HEAD — cannot push", "pushed": False}
    # set upstream if missing
    local = next((b for b in br.get("branches") or [] if b["name"] == current), None)
    if local and not local.get("upstream"):
        code, out, err = _run(repo, "push", "-u", "origin", current)
    else:
        code, out, err = _run(repo, "push", "origin", current)
    result = {
        "ok": code == 0,
        "pushed": code == 0,
        "branch": current,
        "stdout": out,
        "stderr": err,
        "error": None if code == 0 else (err or out or "push failed"),
    }
    if extra:
        result.update(extra)
    return result


def sync_after_work(
    repo: Path = WORKSPACE_ROOT,
    *,
    message: Optional[str] = None,
    snapshot_sessions: bool = True,
) -> dict[str, Any]:
    """Full post-work automation: session index snapshot + protect_work + push.

    Intended CLI for agents when a task unit completes.
    """
    repo = Path(repo).resolve()
    steps: list[dict[str, Any]] = []

    if snapshot_sessions:
        try:
            from session_backup import write_session_index  # noqa: WPS433

            snap = write_session_index(repo=repo, commit=False)
            steps.append({"step": "session_index", **snap})
            # include snapshot files in protect
        except Exception as e:
            steps.append({"step": "session_index", "ok": False, "error": str(e)})

    prot = protect_work(repo, message=message, push=True, ensure_work_branch=True)
    steps.append({"step": "protect_work", **prot})
    return {
        "ok": all(s.get("ok", True) for s in steps if "ok" in s),
        "steps": steps,
        "branch": prot.get("branch"),
        "committed": prot.get("committed"),
        "pushed": prot.get("pushed"),
    }


if __name__ == "__main__":
    import json
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        json.dump(collect_branch_status(), sys.stdout, indent=2)
    elif cmd == "start" and len(sys.argv) > 2:
        json.dump(start_work(sys.argv[2]), sys.stdout, indent=2)
    elif cmd == "protect":
        msg = sys.argv[2] if len(sys.argv) > 2 else None
        json.dump(protect_work(message=msg), sys.stdout, indent=2)
    elif cmd == "sync":
        msg = sys.argv[2] if len(sys.argv) > 2 else None
        json.dump(sync_after_work(message=msg), sys.stdout, indent=2)
    else:
        print(
            "Usage: git_workflow.py [status|start <area>|protect [msg]|sync [msg]]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print()
