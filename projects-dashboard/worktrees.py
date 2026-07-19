#!/usr/bin/env python3
"""Git worktrees for parallel multi-dashboard work.

Problem
-------
Each ``work/<area>`` branch owns a different domain. Checking out one branch
in the main monorepo tree replaces *all* top-level folders with that branch's
versions — so starting Fitness while the main tree is on ``work/holistic``
serves stale resistance-dashboard code.

Solution
--------
Keep a dedicated worktree per domain under::

  ~/personal-workspace-worktrees/<area>/

Dashboard start scripts prefer that path when present. Agents should edit
Fitness on ``work/resistance-dashboard`` (worktree or checkout), not on
``work/orchestra`` / ``work/holistic``.

Usage
-----
  python3 projects-dashboard/worktrees.py ensure
  python3 projects-dashboard/worktrees.py list
  python3 projects-dashboard/worktrees.py path resistance-dashboard
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

# area slug → (branch, description)
AREA_WORKTREES: Dict[str, Tuple[str, str]] = {
    "resistance-dashboard": (
        "work/resistance-dashboard",
        "Fitness (resistance-dashboard/ + fitness/)",
    ),
    "orchestra": ("work/orchestra", "Orchestra hub"),
    "holistic": ("work/holistic", "Time allocator"),
    "iot": ("work/iot", "IoT / Wiz"),
    "projects-dashboard": ("work/projects-dashboard", "Workflow / protect"),
    "treasury": ("work/treasury", "Finance / FCC"),
}

DEFAULT_BASE = Path(
    os.environ.get(
        "PERSONAL_WORKSPACE_WORKTREES",
        str(Path.home() / "personal-workspace-worktrees"),
    )
)


def _run(*args: str, check: bool = False) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(WORKSPACE_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        out, err = (p.stdout or "").strip(), (p.stderr or "").strip()
        if check and p.returncode != 0:
            raise RuntimeError(err or out or f"git {' '.join(args)} failed")
        return p.returncode, out, err
    except (subprocess.TimeoutExpired, OSError) as e:
        if check:
            raise
        return 1, "", str(e)


def worktree_base() -> Path:
    return Path(DEFAULT_BASE).expanduser().resolve()


def worktree_path(area: str) -> Path:
    return worktree_base() / area


def current_branch() -> Optional[str]:
    code, out, _ = _run("branch", "--show-current")
    return out if code == 0 and out else None


def list_worktrees() -> List[dict]:
    code, out, _ = _run("worktree", "list", "--porcelain")
    if code != 0 or not out:
        return []
    items: List[dict] = []
    cur: dict = {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                items.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            if cur:
                items.append(cur)
            cur = {"path": line[len("worktree ") :]}
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD ") :]
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch ") :].replace("refs/heads/", "")
        elif line == "detached":
            cur["detached"] = True
    if cur:
        items.append(cur)
    return items


def branch_checked_out_elsewhere(branch: str) -> Optional[str]:
    """Return path if branch is already checked out in some worktree."""
    want = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
    for wt in list_worktrees():
        b = wt.get("branch") or ""
        if b == branch or b == want or b.endswith("/" + branch.split("/")[-1]) and b.endswith(branch):
            return wt.get("path")
        if wt.get("branch") == branch:
            return wt.get("path")
        # porcelain gives refs/heads/work/foo
        if wt.get("branch") in (branch, f"refs/heads/{branch}"):
            return wt.get("path")
    for wt in list_worktrees():
        br = (wt.get("branch") or "").replace("refs/heads/", "")
        if br == branch:
            return wt.get("path")
    return None


def ensure_area(area: str, *, base: Optional[Path] = None) -> dict:
    if area not in AREA_WORKTREES:
        raise ValueError(
            f"Unknown area {area!r}. Choose from: {', '.join(AREA_WORKTREES)}"
        )
    branch, desc = AREA_WORKTREES[area]
    base = Path(base or worktree_base())
    base.mkdir(parents=True, exist_ok=True)
    path = base / area

    existing = branch_checked_out_elsewhere(branch)
    if existing:
        existing_p = Path(existing).resolve()
        # Already have this branch somewhere — if it's our intended path, OK
        if path.exists() and path.resolve() == existing_p:
            return {
                "ok": True,
                "area": area,
                "branch": branch,
                "path": str(path),
                "status": "exists",
                "description": desc,
            }
        # Branch is checked out in main tree or another path
        if path.exists():
            return {
                "ok": True,
                "area": area,
                "branch": branch,
                "path": str(path),
                "status": "exists",
                "description": desc,
                "note": f"branch also listed at {existing}",
            }
        # Soft OK: branch is the main monorepo checkout — use that path for this area.
        return {
            "ok": True,
            "area": area,
            "branch": branch,
            "path": existing,
            "status": "main_checkout",
            "description": desc,
            "note": (
                f"Branch {branch} is checked out in the main monorepo at {existing} "
                f"(no separate worktree needed until you switch the main tree away)."
            ),
            "active_path": existing,
        }

    if path.exists():
        return {
            "ok": True,
            "area": area,
            "branch": branch,
            "path": str(path),
            "status": "exists",
            "description": desc,
        }

    # Ensure branch exists locally
    code, _, err = _run("rev-parse", "--verify", branch)
    if code != 0:
        code2, _, err2 = _run("fetch", "origin", branch)
        if code2 != 0:
            return {
                "ok": False,
                "area": area,
                "branch": branch,
                "status": "missing_branch",
                "error": err2 or err or f"branch {branch} not found",
            }
        _run("branch", "--track", branch, f"origin/{branch}")

    code, out, err = _run("worktree", "add", str(path), branch)
    if code != 0:
        return {
            "ok": False,
            "area": area,
            "branch": branch,
            "path": str(path),
            "status": "error",
            "error": err or out,
            "description": desc,
        }
    return {
        "ok": True,
        "area": area,
        "branch": branch,
        "path": str(path),
        "status": "created",
        "description": desc,
    }


def ensure_all() -> List[dict]:
    results = []
    for area in AREA_WORKTREES:
        results.append(ensure_area(area))
    return results


def resolve_dashboard_root(area: str, subdir: str) -> Path:
    """Path to dashboard package: worktree preferred, else monorepo root."""
    wt = worktree_path(area)
    candidate = wt / subdir
    if candidate.is_dir():
        return candidate
    fallback = WORKSPACE_ROOT / subdir
    return fallback


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List configured areas and git worktrees")
    ens = sub.add_parser("ensure", help="Create missing worktrees for all areas")
    ens.add_argument(
        "area",
        nargs="?",
        default=None,
        help="Optional single area (default: all)",
    )
    path_p = sub.add_parser("path", help="Print worktree path for an area")
    path_p.add_argument("area")
    path_p.add_argument(
        "--subdir",
        default="",
        help="Optional package subdir (e.g. resistance-dashboard)",
    )

    args = p.parse_args(argv)

    if args.cmd == "list":
        print(f"Worktree base: {worktree_base()}")
        print(f"Main repo:     {WORKSPACE_ROOT} (branch: {current_branch()})")
        print()
        print("Configured areas:")
        for area, (branch, desc) in AREA_WORKTREES.items():
            path = worktree_path(area)
            mark = "ready" if path.is_dir() else "missing"
            print(f"  [{mark:7}] {area:22} {branch:28} {path}")
            print(f"           {desc}")
        print()
        print("git worktree list:")
        for wt in list_worktrees():
            br = (wt.get("branch") or "").replace("refs/heads/", "") or (
                "detached" if wt.get("detached") else "?"
            )
            print(f"  {wt.get('path')}  ({br})")
        return 0

    if args.cmd == "ensure":
        if args.area:
            results = [ensure_area(args.area)]
        else:
            results = ensure_all()
        failed = 0
        for r in results:
            status = r.get("status")
            if r.get("ok"):
                print(f"OK  {r['area']}: {status} → {r.get('path')}")
            else:
                failed += 1
                print(f"ERR {r['area']}: {r.get('error') or status}")
                if r.get("active_path"):
                    print(f"    use: {r['active_path']}")
        return 1 if failed else 0

    if args.cmd == "path":
        area = args.area
        if area not in AREA_WORKTREES and args.subdir:
            # allow alias
            pass
        subdir = args.subdir or (
            "resistance-dashboard" if area in ("resistance-dashboard", "fitness") else ""
        )
        if area == "fitness":
            area = "resistance-dashboard"
        path = resolve_dashboard_root(area, subdir) if subdir else worktree_path(area)
        print(path)
        return 0 if path.exists() or path.parent.exists() else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
