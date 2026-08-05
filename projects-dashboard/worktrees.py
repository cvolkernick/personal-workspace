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
  python3 projects-dashboard/worktrees.py prune-stale [--apply] [--force]
  python3 projects-dashboard/worktrees.py repair-areas [--apply]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

def monorepo_root() -> Path:
    """Primary clone root (not a linked worktree path).

    ``WORKSPACE_ROOT`` follows this file, so inside an area worktree it points at
    the worktree. Prune/repair must use the shared main checkout instead.
    """
    try:
        p = subprocess.run(
            ["git", "-C", str(WORKSPACE_ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        common = Path((p.stdout or "").strip())
        if p.returncode == 0 and common.name == ".git":
            return common.parent.resolve()
        if p.returncode == 0 and str(common).endswith("/.git"):
            return common.parent.resolve()
        # bare or unusual — fall back
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return WORKSPACE_ROOT.resolve()


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



def _worktree_is_dirty(path: Path) -> bool:
    try:
        p = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return bool((p.stdout or "").strip())
    except (subprocess.TimeoutExpired, OSError):
        return True  # treat unknown as dirty (safe)


def _branch_remote_exists(branch: str) -> bool:
    code, _, _ = _run("ls-remote", "--exit-code", "--heads", "origin", branch)
    return code == 0


def _branch_merged_into(branch: str, into: str = "origin/master") -> bool:
    """True if every commit on branch is reachable from *into*."""
    code, _, _ = _run("merge-base", "--is-ancestor", branch, into)
    return code == 0


def _area_for_path(path: Path) -> Optional[str]:
    try:
        path = path.resolve()
        base = worktree_base()
        if path.parent.resolve() != base:
            return None
        name = path.name
        return name if name in AREA_WORKTREES else None
    except OSError:
        return None


def classify_worktree(wt: dict) -> dict:
    """Classify a git worktree for prune / repair decisions."""
    path = Path(wt.get("path") or "")
    branch = (wt.get("branch") or "").replace("refs/heads/", "")
    detached = bool(wt.get("detached"))
    try:
        is_main = path.resolve() == monorepo_root()
    except OSError:
        is_main = False

    area = None if is_main else _area_for_path(path)
    dirty = False if is_main else _worktree_is_dirty(path)
    remote_ok = None
    merged_master = None
    if branch and not detached:
        remote_ok = _branch_remote_exists(branch)
        # Prefer origin/master; fall back to master
        if _run("rev-parse", "--verify", "origin/master")[0] == 0:
            merged_master = _branch_merged_into(branch, "origin/master")
        elif _run("rev-parse", "--verify", "master")[0] == 0:
            merged_master = _branch_merged_into(branch, "master")

    expected_branch = AREA_WORKTREES[area][0] if area else None
    wrong_area_branch = bool(
        area and branch and expected_branch and branch != expected_branch
    )

    # Stale non-area feature worktree
    stale_feature = (
        (not is_main)
        and (area is None)
        and (not dirty)
        and branch
        and not detached
        and (
            remote_ok is False
            or (merged_master is True and branch not in {b for b, _ in AREA_WORKTREES.values()})
        )
    )
    # Area worktree on dead/wrong branch and clean → repair candidate
    repair_area = (
        (not is_main)
        and (area is not None)
        and (not dirty)
        and (
            wrong_area_branch
            or (branch and remote_ok is False and expected_branch and branch != expected_branch)
            or (branch and remote_ok is False and expected_branch and branch == expected_branch)
        )
    )

    return {
        "path": str(path),
        "branch": branch or ("detached" if detached else "?"),
        "is_main": is_main,
        "area": area,
        "dirty": dirty,
        "remote_ok": remote_ok,
        "merged_master": merged_master,
        "expected_branch": expected_branch,
        "wrong_area_branch": wrong_area_branch,
        "stale_feature": stale_feature,
        "repair_area": repair_area,
    }


def prune_stale(*, apply: bool = False, force: bool = False) -> dict:
    """Remove clean stale feature worktrees (not main, not area-correct).

    Safety:
    - Never removes the main monorepo checkout
    - Never removes dirty worktrees unless force=True
    - Never removes a clean area worktree on its *correct* branch
    - Area worktrees on wrong/dead branches are reported for repair-areas
    """
    _run("fetch", "origin", "--prune")
    results = []
    for wt in list_worktrees():
        c = classify_worktree(wt)
        action = "keep"
        reason = "active or protected"

        if c["is_main"]:
            action, reason = "keep", "main monorepo"
        elif c["dirty"] and not force:
            action, reason = "skip_dirty", "has uncommitted changes"
        elif c["area"] and not c["wrong_area_branch"] and c["remote_ok"] is not False:
            action, reason = "keep", f"area worktree on {c['branch']}"
        elif c["area"] and (c["wrong_area_branch"] or c["remote_ok"] is False):
            action, reason = "needs_repair", "area worktree on stale/wrong branch — use repair-areas"
        elif c["stale_feature"] or (
            force and c["area"] is None and not c["is_main"] and (c["remote_ok"] is False or c["merged_master"])
        ):
            action, reason = "prune", (
                "remote gone" if c["remote_ok"] is False else "merged into origin/master"
            )
        elif c["area"] is None and c["remote_ok"] is False and (force or not c["dirty"]):
            if c["dirty"] and not force:
                action, reason = "skip_dirty", "remote gone but dirty"
            else:
                action, reason = "prune", "remote gone (feature/temp worktree)"

        entry = {**c, "action": action, "reason": reason, "applied": False}
        if apply and action == "prune":
            args = ["worktree", "remove"]
            if force:
                args.append("--force")
            args.append(c["path"])
            # Prefer main repo for worktree admin commands
            root = monorepo_root()
            try:
                cmd = ["git", "-C", str(root), *args]
                p = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                )
                code, out, err = p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
            except (subprocess.TimeoutExpired, OSError) as e:
                code, out, err = 1, "", str(e)
            if code == 0:
                entry["applied"] = True
                # Drop local branch if remote gone and not an area target
                br = c["branch"]
                area_branches = {b for b, _ in AREA_WORKTREES.values()}
                if br and br not in area_branches and c["remote_ok"] is False:
                    try:
                        subprocess.run(
                            ["git", "-C", str(root), "branch", "-D", br],
                            capture_output=True,
                            text=True,
                            timeout=30,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                        )
                    except (subprocess.TimeoutExpired, OSError):
                        pass
            else:
                entry["action"] = "error"
                entry["reason"] = err or out or "worktree remove failed"
                entry["applied"] = False
        results.append(entry)

    return {
        "ok": all(r["action"] != "error" for r in results),
        "apply": apply,
        "results": results,
    }


def repair_areas(*, apply: bool = False) -> dict:
    """Point configured area worktrees at their expected branches.

    If the expected branch is missing on origin, create/update it from origin/master
    (workflow area often lands on master after merge).
    """
    _run("fetch", "origin", "--prune")
    # Ensure origin/master exists
    _run("fetch", "origin", "master")
    results = []
    base = worktree_base()
    for area, (branch, desc) in AREA_WORKTREES.items():
        path = base / area
        item = {
            "area": area,
            "branch": branch,
            "path": str(path),
            "description": desc,
            "action": "missing",
            "applied": False,
        }
        if not path.is_dir():
            item["action"] = "missing_worktree"
            item["reason"] = "path not present — run ensure"
            results.append(item)
            continue

        # current branch in that worktree
        try:
            p = subprocess.run(
                ["git", "-C", str(path), "branch", "--show-current"],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            current = (p.stdout or "").strip()
        except (subprocess.TimeoutExpired, OSError) as e:
            item["action"] = "error"
            item["reason"] = str(e)
            results.append(item)
            continue

        dirty = _worktree_is_dirty(path)
        item["current_branch"] = current
        item["dirty"] = dirty

        if dirty:
            item["action"] = "skip_dirty"
            item["reason"] = "uncommitted changes — not switching"
            results.append(item)
            continue

        if current == branch and _branch_remote_exists(branch):
            item["action"] = "ok"
            item["reason"] = "already on expected branch with remote"
            results.append(item)
            continue

        # Ensure local branch exists: prefer origin/<branch>, else origin/master
        if _branch_remote_exists(branch):
            start_point = f"origin/{branch}"
        else:
            start_point = "origin/master"

        if not apply:
            item["action"] = "would_repair"
            item["reason"] = f"{current or '?'} → {branch} (from {start_point})"
            results.append(item)
            continue

        # Create/reset local branch and check it out in the worktree
        code, out, err = _run("branch", "-f", branch, start_point)
        if code != 0:
            # branch may be checked out — force update from within worktree
            try:
                p = subprocess.run(
                    ["git", "-C", str(path), "fetch", "origin"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                )
                p2 = subprocess.run(
                    ["git", "-C", str(path), "checkout", "-B", branch, start_point],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                )
                if p2.returncode != 0:
                    item["action"] = "error"
                    item["reason"] = (p2.stderr or p2.stdout or err or out).strip()
                    results.append(item)
                    continue
            except (subprocess.TimeoutExpired, OSError) as e:
                item["action"] = "error"
                item["reason"] = str(e)
                results.append(item)
                continue
        else:
            try:
                p2 = subprocess.run(
                    ["git", "-C", str(path), "checkout", branch],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                )
                if p2.returncode != 0:
                    # try -B
                    p2 = subprocess.run(
                        ["git", "-C", str(path), "checkout", "-B", branch, start_point],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                    )
                if p2.returncode != 0:
                    item["action"] = "error"
                    item["reason"] = (p2.stderr or p2.stdout or "").strip()
                    results.append(item)
                    continue
            except (subprocess.TimeoutExpired, OSError) as e:
                item["action"] = "error"
                item["reason"] = str(e)
                results.append(item)
                continue

        item["action"] = "repaired"
        item["applied"] = True
        item["reason"] = f"now on {branch} from {start_point}"
        results.append(item)

    return {
        "ok": all(r.get("action") not in ("error",) for r in results),
        "apply": apply,
        "results": results,
    }



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

    pr = sub.add_parser(
        "prune-stale",
        help="Remove clean stale feature/temp worktrees (dry-run by default)",
    )
    pr.add_argument(
        "--apply",
        action="store_true",
        help="Actually remove worktrees (default: report only)",
    )
    pr.add_argument(
        "--force",
        action="store_true",
        help="Allow removing dirty worktrees (dangerous)",
    )
    pr.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON summary",
    )

    rp = sub.add_parser(
        "repair-areas",
        help="Point area worktrees at expected work/<area> branches (dry-run default)",
    )
    rp.add_argument("--apply", action="store_true", help="Apply branch repairs")
    rp.add_argument("--json", action="store_true", help="Emit JSON summary")

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

    if args.cmd == "prune-stale":
        import json as _json

        report = prune_stale(apply=bool(args.apply), force=bool(args.force))
        if args.json:
            print(_json.dumps(report, indent=2))
        else:
            mode = "APPLY" if args.apply else "DRY-RUN"
            print(f"prune-stale [{mode}] base={worktree_base()}")
            for r in report["results"]:
                mark = {
                    "prune": "PRUNE",
                    "keep": "keep ",
                    "skip_dirty": "DIRTY",
                    "needs_repair": "REPAIR",
                    "error": "ERR  ",
                }.get(r["action"], r["action"][:5])
                applied = " ✓" if r.get("applied") else ""
                print(f"  [{mark}] {r['path']}")
                print(
                    f"         branch={r['branch']} area={r['area'] or '-'} "
                    f"remote={'ok' if r['remote_ok'] else ('gone' if r['remote_ok'] is False else '?')} "
                    f"— {r['reason']}{applied}"
                )
            pruned = sum(1 for r in report["results"] if r["action"] == "prune")
            print(f"Summary: {pruned} prune candidate(s); apply={args.apply}")
        return 0 if report.get("ok") else 1

    if args.cmd == "repair-areas":
        import json as _json

        report = repair_areas(apply=bool(args.apply))
        if args.json:
            print(_json.dumps(report, indent=2))
        else:
            mode = "APPLY" if args.apply else "DRY-RUN"
            print(f"repair-areas [{mode}]")
            for r in report["results"]:
                print(
                    f"  [{r['action']:14}] {r['area']:22} "
                    f"{r.get('current_branch') or '?'} → {r['branch']}  "
                    f"{r.get('reason') or ''}"
                )
        return 0 if report.get("ok") else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
