"""Gitk-style commit graph data for the workflow dashboard.

Builds a lane-layout graph (linked-list / DAG visualization) similar to
``gitk`` / ``git log --graph`` from local git history — no GitHub API required.

CLI::

  python3 projects-dashboard/branch_graph.py
  python3 projects-dashboard/branch_graph.py --max 40 --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"


def _run(
    repo: Path, *args: str, timeout: float = 30.0
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return (
            proc.returncode,
            (proc.stdout or "").strip(),
            (proc.stderr or "").strip(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return 1, "", str(e)


def _github_web_base(repo: Path) -> Optional[str]:
    code, url, _ = _run(repo, "remote", "get-url", "origin")
    if code != 0 or not url:
        return None
    url = url.strip()
    # git@github.com:owner/repo.git  or  https://github.com/owner/repo.git
    m = re.match(r"git@github\.com:([^/]+)/(.+?)(?:\.git)?$", url)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"
    m = re.match(r"https?://github\.com/([^/]+)/(.+?)(?:\.git)?$", url)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"
    return None


def _list_refs(repo: Path, include_remotes: bool) -> list[dict[str, str]]:
    """Local heads (+ optional origin/*) as tip labels."""
    code, out, _ = _run(
        repo,
        "for-each-ref",
        "--format=%(objectname)|%(refname:short)|%(refname)",
        "refs/heads",
    )
    refs: list[dict[str, str]] = []
    if code == 0 and out:
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) < 2:
                continue
            sha, name = parts[0], parts[1]
            refs.append(
                {
                    "sha": sha,
                    "name": name,
                    "kind": "local",
                    "is_work": name.startswith("work/"),
                    "is_feature": name.startswith("feature/") or name.startswith("fix/"),
                    "is_master": name in ("master", "main"),
                }
            )
    if include_remotes:
        code, out, _ = _run(
            repo,
            "for-each-ref",
            "--format=%(objectname)|%(refname:short)",
            "refs/remotes/origin",
        )
        local_names = {r["name"] for r in refs}
        if code == 0 and out:
            for line in out.splitlines():
                if "|" not in line:
                    continue
                sha, name = line.split("|", 1)
                if name.endswith("/HEAD") or name == "origin/HEAD":
                    continue
                short = name[len("origin/") :] if name.startswith("origin/") else name
                if short in local_names:
                    # Mark that local has remote tracking tip (same or different sha handled later)
                    continue
                refs.append(
                    {
                        "sha": sha,
                        "name": name,
                        "kind": "remote",
                        "is_work": short.startswith("work/"),
                        "is_feature": short.startswith("feature/")
                        or short.startswith("fix/"),
                        "is_master": short in ("master", "main"),
                    }
                )
    return refs


def _load_commits(
    repo: Path, max_commits: int, include_remotes: bool
) -> list[dict[str, Any]]:
    """Newest-first commit list with parents, via a single git log."""
    pretty = (
        f"%H{_FIELD_SEP}%h{_FIELD_SEP}%P{_FIELD_SEP}%s{_FIELD_SEP}"
        f"%cI{_FIELD_SEP}%an{_FIELD_SEP}%D{_RECORD_SEP}"
    )
    args = [
        "log",
        f"--max-count={max(1, int(max_commits))}",
        "--date-order",
        f"--pretty=format:{pretty}",
    ]
    if include_remotes:
        args.append("--all")
    else:
        args.append("--branches")
    code, out, err = _run(repo, *args, timeout=45.0)
    if code != 0:
        raise RuntimeError(err or "git log failed")
    commits: list[dict[str, Any]] = []
    if not out:
        return commits
    # Split on record separator; trailing empty ok
    for rec in out.split(_RECORD_SEP):
        rec = rec.strip()
        if not rec:
            continue
        parts = rec.split(_FIELD_SEP)
        if len(parts) < 6:
            continue
        full, short, parents_s, subject, date, author = parts[:6]
        decorate = parts[6] if len(parts) > 6 else ""
        parents = [p for p in parents_s.split() if p]
        commits.append(
            {
                "sha": full,
                "short": short,
                "parents": parents,
                "subject": subject,
                "date": date,
                "author": author,
                "decorate": decorate.strip(),
            }
        )
    return commits


def _assign_lanes(
    commits: list[dict[str, Any]],
) -> tuple[dict[str, int], list[dict[str, Any]], int]:
    """Assign a column (lane) to each commit; return edges for SVG drawing.

    Walks newest → oldest (gitk / ``git log --graph`` style). First parent
    continues the lane; additional parents open or reuse lanes (merges).
    """
    sha_set = {c["sha"] for c in commits}
    sha_to_row = {c["sha"]: i for i, c in enumerate(commits)}
    lanes: dict[str, int] = {}
    # active[lane] = sha we expect next (walking down the page), or None if free
    active: list[Optional[str]] = []
    edges: list[dict[str, Any]] = []

    def free_lane() -> int:
        for i, v in enumerate(active):
            if v is None:
                return i
        active.append(None)
        return len(active) - 1

    for c in commits:
        sha = c["sha"]
        lane: Optional[int] = None
        for i, expect in enumerate(active):
            if expect == sha:
                lane = i
                break
        if lane is None:
            lane = free_lane()
        lanes[sha] = lane
        active[lane] = None

        parents = [p for p in c["parents"] if p in sha_set]
        if not parents:
            continue

        # First parent continues this lane when free or already expecting that parent
        p0 = parents[0]
        if active[lane] is None or active[lane] == p0:
            active[lane] = p0
            p0_lane = lane
        else:
            p0_lane = free_lane()
            active[p0_lane] = p0
        edges.append(
            {
                "from_sha": sha,
                "to_sha": p0,
                "from_row": sha_to_row[sha],
                "to_row": sha_to_row[p0],
                "from_lane": lane,
                "to_lane": p0_lane,
                "kind": "first",
            }
        )

        for p in parents[1:]:
            # Prefer a lane already waiting for this parent (merge into branch)
            p_lane: Optional[int] = None
            for i, expect in enumerate(active):
                if expect == p:
                    p_lane = i
                    break
            if p_lane is None:
                p_lane = free_lane()
                active[p_lane] = p
            edges.append(
                {
                    "from_sha": sha,
                    "to_sha": p,
                    "from_row": sha_to_row[sha],
                    "to_row": sha_to_row[p],
                    "from_lane": lane,
                    "to_lane": p_lane,
                    "kind": "merge",
                }
            )

    max_lane = max(lanes.values()) if lanes else 0
    return lanes, edges, max_lane


def _color_for_ref(name: str) -> str:
    if name in ("master", "main") or name.endswith("/master") or name.endswith("/main"):
        return "#3dd68c"
    if "work/" in name:
        return "#c084fc"
    if "feature/" in name:
        return "#7c9cff"
    if "fix/" in name:
        return "#f5c542"
    return "#5b9fd4"


def collect_branch_graph(
    repo: Path = WORKSPACE_ROOT,
    *,
    max_commits: int = 80,
    include_remotes: bool = True,
) -> dict[str, Any]:
    """Return JSON-serializable graph payload for the dashboard SVG."""
    repo = Path(repo).resolve()
    max_commits = max(10, min(int(max_commits), 300))

    code, head, _ = _run(repo, "rev-parse", "HEAD")
    head_sha = head if code == 0 else None
    code, head_branch, _ = _run(repo, "branch", "--show-current")
    current_branch = head_branch if code == 0 and head_branch else None

    try:
        commits = _load_commits(repo, max_commits, include_remotes)
    except RuntimeError as e:
        return {"ok": False, "error": str(e), "commits": [], "edges": [], "refs": []}

    refs = _list_refs(repo, include_remotes=include_remotes)
    # Map tip sha → ref labels (may be multiple)
    tips: dict[str, list[dict[str, Any]]] = {}
    for r in refs:
        tips.setdefault(r["sha"], []).append(
            {
                "name": r["name"],
                "kind": r["kind"],
                "color": _color_for_ref(r["name"]),
                "is_work": r["is_work"],
                "is_feature": r["is_feature"],
                "is_master": r["is_master"],
                "current": r["name"] == current_branch,
            }
        )

    lanes, edges, max_lane = _assign_lanes(commits)
    gh = _github_web_base(repo)

    rows: list[dict[str, Any]] = []
    for i, c in enumerate(commits):
        labels = tips.get(c["sha"], [])
        # Also parse decorate for HEAD tags etc. when tip map missed
        if not labels and c.get("decorate"):
            for piece in re.split(r",\s*", c["decorate"]):
                piece = piece.strip()
                if not piece or piece == "HEAD":
                    continue
                piece = re.sub(r"^HEAD\s*->\s*", "", piece).strip()
                if (
                    not piece
                    or piece.startswith("tag:")
                    or piece == "origin"
                    or piece.startswith("refs/")
                    or piece == "stash"
                ):
                    continue
                labels.append(
                    {
                        "name": piece,
                        "kind": "decorate",
                        "color": _color_for_ref(piece),
                        "is_work": piece.startswith("work/")
                        or "/work/" in piece,
                        "is_feature": piece.startswith("feature/")
                        or piece.startswith("fix/")
                        or "/feature/" in piece
                        or "/fix/" in piece,
                        "is_master": piece in ("master", "main")
                        or piece.endswith("/master")
                        or piece.endswith("/main"),
                        "current": piece == current_branch,
                    }
                )
        rows.append(
            {
                "row": i,
                "sha": c["sha"],
                "short": c["short"],
                "subject": c["subject"],
                "date": c["date"],
                "author": c["author"],
                "parents": c["parents"],
                "lane": lanes.get(c["sha"], 0),
                "labels": labels,
                "is_head": c["sha"] == head_sha,
                "url": f"{gh}/commit/{c['sha']}" if gh else None,
            }
        )

    # Worktrees for context (path + branch)
    worktrees: list[dict[str, str]] = []
    code, wt_out, _ = _run(repo, "worktree", "list", "--porcelain")
    if code == 0 and wt_out:
        cur: dict[str, str] = {}
        for line in wt_out.splitlines():
            if not line.strip():
                if cur.get("path"):
                    worktrees.append(cur)
                cur = {}
                continue
            if line.startswith("worktree "):
                cur = {"path": line[len("worktree ") :].strip()}
            elif line.startswith("branch "):
                ref = line[len("branch ") :].strip()
                cur["branch"] = (
                    ref[len("refs/heads/") :]
                    if ref.startswith("refs/heads/")
                    else ref
                )
            elif line.startswith("detached"):
                cur["branch"] = "(detached)"
        if cur.get("path"):
            worktrees.append(cur)

    return {
        "ok": True,
        "repo": str(repo),
        "github_base": gh,
        "current_branch": current_branch,
        "head": head_sha,
        "max_commits": max_commits,
        "include_remotes": include_remotes,
        "lane_count": max_lane + 1,
        "commit_count": len(rows),
        "commits": rows,
        "edges": edges,
        "refs": [
            {
                "name": r["name"],
                "sha": r["sha"][:7],
                "sha_full": r["sha"],
                "kind": r["kind"],
                "color": _color_for_ref(r["name"]),
                "is_work": r["is_work"],
                "is_feature": r["is_feature"],
                "is_master": r["is_master"],
                "current": r["name"] == current_branch,
                "url": f"{gh}/tree/{r['name']}"
                if gh and r["kind"] == "local"
                else (f"{gh}/tree/{r['name'].replace('origin/', '', 1)}" if gh else None),
            }
            for r in sorted(
                refs,
                key=lambda x: (
                    0 if x["name"] == current_branch else 1,
                    0 if x["is_master"] else 1,
                    0 if x["is_work"] else 1,
                    x["name"],
                ),
            )
        ],
        "worktrees": worktrees,
        "links": {
            "github_branches": f"{gh}/branches" if gh else None,
            "github_network": f"{gh}/network" if gh else None,
        },
        "style_note": (
            "gitk-style lane graph from local git (git log --graph equivalent). "
            "Not embedding git-gui/gitk; same linked-list topology, in-browser."
        ),
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Branch commit graph for dashboard")
    p.add_argument("--max", type=int, default=40, help="Max commits (default 40)")
    p.add_argument("--no-remotes", action="store_true", help="Local branches only")
    p.add_argument("--json", action="store_true", help="Print full JSON")
    p.add_argument("--repo", type=Path, default=WORKSPACE_ROOT)
    args = p.parse_args(argv)
    data = collect_branch_graph(
        args.repo,
        max_commits=args.max,
        include_remotes=not args.no_remotes,
    )
    if args.json:
        json.dump(data, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if data.get("ok") else 1
    if not data.get("ok"):
        print(data.get("error") or "failed", file=sys.stderr)
        return 1
    print(
        f"commits={data['commit_count']} lanes={data['lane_count']} "
        f"branch={data.get('current_branch')} refs={len(data.get('refs') or [])}"
    )
    for c in (data.get("commits") or [])[:15]:
        labels = ",".join(x["name"] for x in (c.get("labels") or [])[:3])
        lab = f" [{labels}]" if labels else ""
        print(f"  L{c['lane']} {c['short']} {c['subject'][:60]}{lab}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
