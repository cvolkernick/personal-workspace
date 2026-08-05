"""Branch-aware git workflow for personal-workspace.

Conventions
-----------
- ``master`` — integration branch; keep green and pushed.
- ``work/<area>`` — active work for a monorepo top-level area
  (e.g. work/treasury, work/projects-dashboard).
- ``feature/<slug>`` / ``fix/<slug>`` — reviewable product changes (PR).

``protect_work`` has two modes:

- **full** — intentional protect (dashboard button, or ``protect "msg"`` /
  ``sync "msg"``). Stages non-secret dirty files; may push ``work/*`` or
  ``feature/*`` / ``fix/*``. Never auto-pushes ``master``.
- **auto** — survival save only (default when no message is given, or
  ``protect --auto``). Stages **durable operational paths only** (snapshots,
  journals, session-index, backlog state). Pushes **only** on ``work/*``.
  Leaves product code dirty so it cannot ride an auto-push into a PR base.

Policy one-liner: **auto-save keeps the lights on; PRs change the product.**
"""

from __future__ import annotations

import json
import os
import re
import socket
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

# Auto-protect allowlist: operational / reboot-survival only (not product code).
# Matched against repo-relative paths with forward slashes.
_DURABLE_PATH_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)snapshots?/", re.I),
    re.compile(r"(^|/)session-index/", re.I),
    re.compile(r"^ops/session-index/", re.I),
    re.compile(r"^ops/backlog/(items|suggestions)\.json$", re.I),
    re.compile(r"(^|/)journals?/", re.I),
    re.compile(r"_journal\.md$", re.I),
    re.compile(r"fund_manager_journal\.md$", re.I),
    re.compile(r"treasury_latest\.json$", re.I),
    re.compile(r"_latest\.json$", re.I),
    re.compile(r"fund_manager_decisions\.jsonl$", re.I),
    re.compile(r"ntfy_.*\.json$", re.I),
    re.compile(r"^ops/backlog/seeds/.*\.(md|txt|goal\.txt|prompt\.txt)$", re.I),
)

# Product / reviewable code — never auto-committed (defense in depth)
_PRODUCT_PATH_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\.(py|sh|js|ts|tsx|jsx|css|html|go|rs|java|swift)$", re.I),
    re.compile(r"(^|/)tests?/", re.I),
    re.compile(r"(^|/)static/", re.I),
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


def list_worktrees(repo: Path = WORKSPACE_ROOT) -> list[dict[str, str]]:
    """Return [{path, branch, bare}] from ``git worktree list --porcelain``."""
    repo = Path(repo).resolve()
    code, out, _ = _run(repo, "worktree", "list", "--porcelain")
    if code != 0 or not out:
        return []
    trees: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    for line in out.splitlines():
        if not line.strip():
            if cur.get("path"):
                trees.append(cur)
            cur = {}
            continue
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree ") :].strip()}
        elif line.startswith("branch "):
            ref = line[len("branch ") :].strip()
            if ref.startswith("refs/heads/"):
                cur["branch"] = ref[len("refs/heads/") :]
            else:
                cur["branch"] = ref
        elif line == "bare":
            cur["bare"] = "1"
        elif line.startswith("detached"):
            cur["branch"] = cur.get("branch") or "(detached)"
    if cur.get("path"):
        trees.append(cur)
    return trees


def branch_worktree_path(repo: Path, branch: str) -> Optional[str]:
    """If *branch* is checked out in another worktree, return that path; else None."""
    repo = Path(repo).resolve()
    want = (branch or "").strip()
    if not want:
        return None
    for wt in list_worktrees(repo):
        b = (wt.get("branch") or "").strip()
        p = (wt.get("path") or "").strip()
        if not b or not p:
            continue
        try:
            elsewhere = Path(p).resolve() != repo
        except OSError:
            elsewhere = True
        if b == want and elsewhere:
            return p
    return None


def branch_name_for_area(area: str) -> str:
    area = re.sub(r"[^a-zA-Z0-9._-]+", "-", area.strip()).strip("-").lower()
    # Resolve TLD aliases (financial-command → treasury, fitness → resistance-dashboard)
    try:
        from workspace import work_area_for_tld  # noqa: WPS433
    except Exception:
        work_area_for_tld = None  # type: ignore
    if work_area_for_tld is not None:
        resolved = work_area_for_tld(area)
        if resolved and resolved not in ("_meta", "_root"):
            area = resolved
    return f"work/{area}"


def area_from_path(rel: str) -> str:
    """Top-level monorepo path segment (raw TLD), or ``_root`` for root files."""
    rel = rel.replace("\\", "/").lstrip("./")
    if "/" in rel:
        return rel.split("/", 1)[0]
    return "_root"


def work_area_from_path(rel: str) -> str:
    """Work-branch area for a path (aliases applied: FCC → treasury, etc.)."""
    top = area_from_path(rel)
    if top == "_root":
        return "_root"
    try:
        from workspace import work_area_for_tld  # noqa: WPS433
    except Exception:
        return top
    resolved = work_area_for_tld(top)
    if resolved in ("_meta",):
        # Meta edits: prefer projects-dashboard so protect doesn't invent work/ops
        return "projects-dashboard" if top == "ops" else "projects-dashboard"
    return resolved


def should_skip_path(rel: str) -> bool:
    name = Path(rel).name
    if name in _NEVER_COMMIT:
        return True
    for p in _NEVER_COMMIT_PREFIXES:
        if rel.startswith(p):
            return True
    return False


def is_durable_path(rel: str) -> bool:
    """True if *rel* is operational state safe for auto-protect."""
    rel = rel.replace("\\", "/").lstrip("./")
    if should_skip_path(rel):
        return False
    if any(rx.search(rel) for rx in _PRODUCT_PATH_RES):
        return False
    return any(rx.search(rel) for rx in _DURABLE_PATH_RES)


def is_work_branch(name: Optional[str]) -> bool:
    return bool(name) and name.startswith("work/")


def is_review_branch(name: Optional[str]) -> bool:
    """feature/* or fix/* — PR slices, not long-lived auto-push bases."""
    if not name:
        return False
    return name.startswith("feature/") or name.startswith("fix/")


def is_master_branch(name: Optional[str]) -> bool:
    return name in ("master", "main")


def resolve_protect_mode(
    mode: Optional[str],
    message: Optional[str],
) -> str:
    """Return ``auto`` or ``full``.

    Default: no message → auto (survival). Explicit message → full.
    """
    if mode in ("auto", "full"):
        return mode
    if mode is not None and mode not in ("auto", "full"):
        raise ValueError(f"invalid protect mode: {mode!r} (use auto|full)")
    if message is None or not str(message).strip():
        return "auto"
    # Generated-looking auto-save titles still count as auto if caller passed them
    msg = str(message).strip()
    if re.match(r"^protect\([^)]+\):\s*auto-save durable work", msg, re.I):
        return "auto"
    return "full"


def _short_hostname() -> str:
    try:
        name = socket.gethostname() or "local"
    except OSError:
        name = "local"
    # strip domain: MacBookPro.home.local → MacBookPro
    return name.split(".")[0] or name


def _clone_reports_dir(repo: Path) -> Path:
    """Optional multi-machine branch reports under ops/branch-clones/."""
    return Path(repo).resolve() / "ops" / "branch-clones"


def load_remote_clone_reports(repo: Path = WORKSPACE_ROOT) -> list[dict[str, Any]]:
    """Load peer-machine branch inventories from ``ops/branch-clones/*.json``.

    Each file::

        {
          "machine": "pi",          # column id (stable)
          "label": "Pi",            # display name (optional)
          "hostname": "prism",      # optional
          "updated_at": "ISO-8601", # optional
          "branches": [
            {"name": "master", "sha": "abc1234", "current": true}
          ]
        }

    This machine is always built live from git; files here are *other* clones.
    """
    d = _clone_reports_dir(repo)
    if not d.is_dir():
        return []
    reports: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(raw, dict):
            continue
        machine = str(raw.get("machine") or path.stem).strip()
        if not machine or machine in ("origin", "local"):
            continue
        branches_in = raw.get("branches") or []
        if not isinstance(branches_in, list):
            continue
        cleaned: list[dict[str, Any]] = []
        for b in branches_in:
            if not isinstance(b, dict):
                continue
            name = str(b.get("name") or "").strip()
            if not name:
                continue
            cleaned.append(
                {
                    "name": name,
                    "sha": (str(b.get("sha") or "")[:12] or None),
                    "current": bool(b.get("current")),
                }
            )
        reports.append(
            {
                "id": machine,
                "label": str(raw.get("label") or raw.get("hostname") or machine),
                "hostname": raw.get("hostname"),
                "updated_at": raw.get("updated_at"),
                "source": str(path.relative_to(Path(repo).resolve()))
                if path.is_relative_to(Path(repo).resolve())
                else str(path),
                "branches": cleaned,
            }
        )
    return reports


def build_branch_matrix(
    repo: Path = WORKSPACE_ROOT,
    *,
    branches: Optional[list[dict[str, Any]]] = None,
    remote_branches: Optional[list[dict[str, str]]] = None,
    current: Optional[str] = None,
    peer_reports: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Color-coded origin × local-clone presence matrix.

    Columns: ``origin`` always first, then this machine (live git), then any
    peer clone reports under ``ops/branch-clones/``.

    Cell states (for UI color)::

        present  — exists, no drift info (origin or peer)
        synced   — local matches origin tip (or both absent N/A)
        ahead    — local has commits not on origin
        behind   — origin has commits not local
        diverged — ahead and behind
        local_only — on clone, not on origin
        remote_only — on origin, not on this clone
        gone     — upstream tracked but deleted on origin
        absent   — not present on that column
    """
    repo = Path(repo).resolve()
    host = _short_hostname()
    local_id = f"local:{host}"

    if branches is None or remote_branches is None or current is None:
        # Allow standalone use without double-collect when caller already has data
        st = collect_branch_status(repo, include_matrix=False)
        branches = branches if branches is not None else st.get("branches") or []
        remote_branches = (
            remote_branches
            if remote_branches is not None
            else st.get("remote_branches") or []
        )
        current = current if current is not None else st.get("current")

    if peer_reports is None:
        peer_reports = load_remote_clone_reports(repo)

    # Map origin short name → sha
    origin_map: dict[str, str] = {}
    for rb in remote_branches or []:
        name = rb.get("name") or ""
        if name.startswith("origin/"):
            short = name[len("origin/") :]
        else:
            short = name
        if short in ("HEAD",) or short.endswith("/HEAD"):
            continue
        origin_map[short] = rb.get("sha") or ""

    local_map: dict[str, dict[str, Any]] = {
        b["name"]: b for b in (branches or []) if b.get("name")
    }

    # Worktree checkouts on this machine
    wt_by_branch: dict[str, list[str]] = {}
    for wt in list_worktrees(repo):
        b = (wt.get("branch") or "").strip()
        p = (wt.get("path") or "").strip()
        if not b or not p or b == "(detached)":
            continue
        # shorten path for display
        try:
            home = str(Path.home())
            disp = p.replace(home, "~") if p.startswith(home) else p
        except OSError:
            disp = p
        wt_by_branch.setdefault(b, []).append(disp)

    peer_maps: list[tuple[dict[str, Any], dict[str, dict[str, Any]]]] = []
    for rep in peer_reports or []:
        pm: dict[str, dict[str, Any]] = {}
        for b in rep.get("branches") or []:
            n = b.get("name")
            if n:
                pm[n] = b
        peer_maps.append((rep, pm))

    all_names = set(origin_map) | set(local_map)
    for _, pm in peer_maps:
        all_names |= set(pm)

    def _sort_key(n: str) -> tuple:
        if n in ("master", "main"):
            return (0, n)
        if n.startswith("work/"):
            return (1, n)
        if n.startswith("feature/") or n.startswith("fix/"):
            return (2, n)
        return (3, n)

    columns: list[dict[str, Any]] = [
        {
            "id": "origin",
            "kind": "origin",
            "label": "origin",
            "subtitle": "remote",
        },
        {
            "id": local_id,
            "kind": "local",
            "label": host,
            "subtitle": "this machine",
            "hostname": host,
            "live": True,
        },
    ]
    for rep, _ in peer_maps:
        columns.append(
            {
                "id": f"clone:{rep['id']}",
                "kind": "clone",
                "label": rep.get("label") or rep["id"],
                "subtitle": rep.get("hostname") or rep["id"],
                "hostname": rep.get("hostname"),
                "updated_at": rep.get("updated_at"),
                "source": rep.get("source"),
                "live": False,
            }
        )

    rows: list[dict[str, Any]] = []
    for name in sorted(all_names, key=_sort_key):
        loc = local_map.get(name)
        on_origin = name in origin_map
        on_local = loc is not None
        ahead = (loc or {}).get("ahead")
        behind = (loc or {}).get("behind")
        upstream = (loc or {}).get("upstream")
        gone = bool(upstream and "(gone)" in str(upstream))

        # Origin cell
        origin_cell: dict[str, Any] = {
            "present": on_origin,
            "sha": origin_map.get(name) or None,
            "state": "present" if on_origin else "absent",
        }

        # Local cell state
        if on_local and not on_origin:
            local_state = "gone" if gone else "local_only"
        elif on_origin and not on_local:
            local_state = "remote_only"
        elif on_local and on_origin:
            a = int(ahead or 0)
            b = int(behind or 0)
            if a > 0 and b > 0:
                local_state = "diverged"
            elif a > 0:
                local_state = "ahead"
            elif b > 0:
                local_state = "behind"
            else:
                local_state = "synced"
        else:
            local_state = "absent"

        local_cell: dict[str, Any] = {
            "present": on_local,
            "sha": (loc or {}).get("sha"),
            "state": local_state,
            "ahead": ahead,
            "behind": behind,
            "current": bool((loc or {}).get("current")),
            "worktrees": wt_by_branch.get(name) or [],
        }

        cells: dict[str, dict[str, Any]] = {
            "origin": origin_cell,
            local_id: local_cell,
        }

        for rep, pm in peer_maps:
            pb = pm.get(name)
            col_id = f"clone:{rep['id']}"
            if pb:
                # Compare peer sha to origin when both exist
                psha = (pb.get("sha") or "")[:7]
                osha = (origin_map.get(name) or "")[:7]
                if on_origin and psha and osha and psha == osha:
                    pstate = "synced"
                elif on_origin and psha and osha and psha != osha:
                    pstate = "diverged"
                elif not on_origin:
                    pstate = "local_only"
                else:
                    pstate = "present"
                cells[col_id] = {
                    "present": True,
                    "sha": pb.get("sha"),
                    "state": pstate,
                    "current": bool(pb.get("current")),
                }
            else:
                cells[col_id] = {
                    "present": False,
                    "sha": None,
                    "state": "remote_only" if on_origin else "absent",
                }

        # Rollup status for filtering / row tint
        if local_state in ("local_only", "gone"):
            rollup = local_state
        elif local_state == "remote_only" and not any(
            cells[c["id"]].get("present")
            for c in columns
            if c["kind"] != "origin"
        ):
            rollup = "remote_only"
        elif local_state in ("ahead", "behind", "diverged"):
            rollup = local_state
        elif on_origin and on_local:
            rollup = "synced"
        elif on_origin:
            rollup = "remote_only"
        else:
            rollup = "local_only"

        rows.append(
            {
                "name": name,
                "is_work": name.startswith("work/"),
                "is_feature": name.startswith("feature/") or name.startswith("fix/"),
                "is_master": name in ("master", "main"),
                "current": name == current,
                "rollup": rollup,
                "cells": cells,
            }
        )

    counts = {
        "total": len(rows),
        "on_origin": sum(1 for r in rows if r["cells"]["origin"]["present"]),
        "on_local": sum(1 for r in rows if r["cells"][local_id]["present"]),
        "local_only": sum(1 for r in rows if r["rollup"] == "local_only"),
        "remote_only": sum(1 for r in rows if r["rollup"] == "remote_only"),
        "diverged": sum(
            1 for r in rows if r["rollup"] in ("ahead", "behind", "diverged")
        ),
        "synced": sum(1 for r in rows if r["rollup"] == "synced"),
    }

    return {
        "columns": columns,
        "rows": rows,
        "counts": counts,
        "machine": host,
        "local_column_id": local_id,
        "legend": [
            {"state": "synced", "label": "synced", "color": "green"},
            {"state": "ahead", "label": "ahead of origin", "color": "yellow"},
            {"state": "behind", "label": "behind origin", "color": "yellow"},
            {"state": "diverged", "label": "diverged", "color": "orange"},
            {"state": "local_only", "label": "local only", "color": "purple"},
            {"state": "remote_only", "label": "origin only", "color": "blue"},
            {"state": "gone", "label": "upstream gone", "color": "red"},
            {"state": "present", "label": "present", "color": "green"},
            {"state": "absent", "label": "absent", "color": "muted"},
        ],
        "note": (
            "Origin vs clones on each machine. Peer columns load from "
            "ops/branch-clones/*.json when other hosts publish reports."
        ),
    }


def collect_branch_status(
    repo: Path = WORKSPACE_ROOT,
    *,
    include_matrix: bool = True,
) -> dict[str, Any]:
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
                    "is_feature": name.startswith("feature/") or name.startswith("fix/"),
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

    result: dict[str, Any] = {
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
    if include_matrix:
        result["matrix"] = build_branch_matrix(
            repo,
            branches=branches,
            remote_branches=remote_branches,
            current=current,
        )
    return result


def start_work(
    area: str,
    repo: Path = WORKSPACE_ROOT,
    *,
    from_branch: str = "HEAD",
    create: bool = True,
) -> dict[str, Any]:
    """Checkout or create work/<area>.

    By default new branches are created from the current tip (``HEAD``) so a
    monorepo work branch inherits the latest integrated tree. Pass
    ``from_branch='master'`` only when intentionally branching from integration.
    """
    repo = Path(repo).resolve()
    branch = branch_name_for_area(area)
    code, _existing, _ = _run(repo, "rev-parse", "--verify", branch)
    if code == 0:
        elsewhere = branch_worktree_path(repo, branch)
        if elsewhere:
            return {
                "ok": False,
                "error": (
                    f"Branch {branch} is already checked out in worktree "
                    f"{elsewhere}. Stay on the current branch or run protect "
                    f"from that worktree."
                ),
                "branch": branch,
                "worktree": elsewhere,
                "code": "worktree_busy",
            }
        code2, _, err = _run(repo, "checkout", branch)
        if code2 != 0:
            # Surface worktree errors clearly
            if "already used by worktree" in (err or ""):
                return {
                    "ok": False,
                    "error": err,
                    "branch": branch,
                    "code": "worktree_busy",
                }
            return {"ok": False, "error": err or "checkout failed", "branch": branch}
        return {
            "ok": True,
            "branch": branch,
            "created": False,
            "message": f"Checked out existing {branch}",
        }

    if not create:
        return {"ok": False, "error": f"branch {branch} does not exist", "branch": branch}

    base = (from_branch or "HEAD").strip()
    if base not in ("HEAD", "head", "@"):
        # Try to use local/remote integration base when requested
        _run(repo, "fetch", "origin", base)
        code_b, _, _ = _run(repo, "rev-parse", "--verify", base)
        if code_b != 0:
            code_b, _, _ = _run(repo, "rev-parse", "--verify", f"origin/{base}")
            if code_b == 0:
                base = f"origin/{base}"
            else:
                base = "HEAD"
        else:
            # Prefer staying on current tip if already ahead of master
            pass
        code, _, err = _run(repo, "checkout", "-b", branch, base)
    else:
        code, _, err = _run(repo, "checkout", "-b", branch)
    if code != 0:
        return {"ok": False, "error": err or "create branch failed", "branch": branch}
    return {
        "ok": True,
        "branch": branch,
        "created": True,
        "message": f"Created and checked out {branch} from {base}",
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
    mode: Optional[str] = None,
) -> dict[str, Any]:
    """Stage changes, commit on an appropriate branch, optionally push.

    Parameters
    ----------
    mode:
        ``\"auto\"`` — durable paths only; push only on ``work/*``.
        ``\"full\"`` — all non-secret dirty (product + durable); push on
        ``work/*`` / ``feature/*`` / ``fix/*``; never ``master``.
        ``None`` — resolved via :func:`resolve_protect_mode` (no message → auto).

    - Skips secrets always.
    - If on master with dirty area-scoped files and ensure_work_branch, switches
      to work/<primary-area> first (creates if needed).
    - Snapshot JSON included by default (unless ``include_snapshots=False``).
    """
    repo = Path(repo).resolve()
    try:
        resolved_mode = resolve_protect_mode(mode, message)
    except ValueError as e:
        return {"ok": False, "error": str(e), "committed": False, "pushed": False}

    all_dirty = dirty_paths(repo)
    if paths is not None:
        candidates = paths
    else:
        candidates = all_dirty

    to_stage: list[str] = []
    skipped: list[str] = []
    skipped_product: list[str] = []
    for p in candidates:
        if should_skip_path(p):
            skipped.append(p)
            continue
        if not include_snapshots and _SNAPSHOTISH.search(p):
            skipped.append(p)
            continue
        if resolved_mode == "auto" and not is_durable_path(p):
            skipped_product.append(p)
            skipped.append(p)
            continue
        to_stage.append(p)

    if not to_stage and not all_dirty:
        # maybe only need push (respect branch policy)
        br = collect_branch_status(repo)
        if push and br.get("current"):
            return _push_current(
                repo,
                br,
                mode=resolved_mode,
                extra={
                    "staged": [],
                    "committed": False,
                    "mode": resolved_mode,
                    "message": "Working tree clean — pushed if needed",
                },
            )
        return {
            "ok": True,
            "committed": False,
            "pushed": False,
            "mode": resolved_mode,
            "message": "Nothing to protect — working tree clean",
            "branch": br.get("current"),
            "skipped": skipped,
        }

    if not to_stage:
        # Auto mode with only product dirty is success (left alone), not failure
        if resolved_mode == "auto" and skipped_product and all_dirty:
            return {
                "ok": True,
                "committed": False,
                "pushed": False,
                "mode": resolved_mode,
                "message": (
                    "Auto-protect skipped product/code paths — "
                    "use feature/* + protect \"msg\" for reviewable work"
                ),
                "skipped": skipped,
                "skipped_product": skipped_product,
                "dirty": all_dirty,
            }
        return {
            "ok": False,
            "error": "All dirty paths were skipped (secrets/snapshots/auto filter)",
            "skipped": skipped,
            "skipped_product": skipped_product,
            "dirty": all_dirty,
            "mode": resolved_mode,
            "message": "Nothing staged — dirty files remain",
        }

    # Branch selection (use work-area aliases so FCC/investment → work/treasury)
    areas = [
        work_area_from_path(p)
        for p in to_stage
        if work_area_from_path(p) not in ("_root",)
    ]
    area_counts: dict[str, int] = {}
    for a in areas:
        area_counts[a] = area_counts.get(a, 0) + 1
    primary_area = (
        max(area_counts, key=area_counts.get) if area_counts else "misc"  # type: ignore[arg-type]
    )

    code, current, _ = _run(repo, "branch", "--show-current")
    current = current if code == 0 else None
    branch_actions: list[str] = []

    # Prefer the work/<area> that matches dirty paths (FCC → work/treasury, not work/iot).
    # If that branch is checked out in another git worktree, stay put and commit here
    # (cannot checkout the same branch in two worktrees).
    expected_branch = branch_name_for_area(primary_area)
    need_switch = ensure_work_branch and primary_area not in ("misc", "_root") and (
        current in ("master", "main", None)
        or (current and current.startswith("work/") and current != expected_branch)
    )
    if need_switch:
        elsewhere = branch_worktree_path(repo, expected_branch)
        if elsewhere:
            branch_actions.append(
                f"stayed on {current or 'HEAD'}; {expected_branch} is checked out at "
                f"{elsewhere} — committing here instead"
            )
        else:
            sw = start_work(primary_area, repo=repo, from_branch="HEAD")
            branch_actions.append(sw.get("message") or str(sw))
            if not sw.get("ok"):
                # Worktree race or other checkout failure: fall back to current branch
                if sw.get("code") == "worktree_busy" or "worktree" in (
                    sw.get("error") or ""
                ).lower():
                    branch_actions.append(
                        f"checkout blocked ({sw.get('error')}); staying on {current}"
                    )
                else:
                    return {
                        "ok": False,
                        "error": sw.get("error"),
                        "branch_actions": branch_actions,
                    }
            else:
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
                mode=resolved_mode,
                extra={
                    "staged": [],
                    "committed": False,
                    "mode": resolved_mode,
                    "message": "Nothing to commit — already clean",
                    "skipped": skipped,
                    "branch_actions": branch_actions,
                },
            )
        return {
            "ok": True,
            "committed": False,
            "mode": resolved_mode,
            "message": "Nothing staged",
            "skipped": skipped,
            "branch_actions": branch_actions,
        }

    if not message:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if resolved_mode == "auto":
            message = f"protect({primary_area}): auto-save durable work ({ts})"
        else:
            message = f"protect({primary_area}): save work ({ts})"

    # Refresh branch after possible switch
    code_br, head_now, _ = _run(repo, "branch", "--show-current")
    if code_br == 0 and head_now:
        current = head_now

    # Auto mode only commits on work/* (survival bases — not PR slices / master)
    if resolved_mode == "auto" and not is_work_branch(current):
        if staged_list:
            _run(repo, "reset", "HEAD", "--", *staged_list)
        return {
            "ok": True,
            "committed": False,
            "pushed": False,
            "mode": resolved_mode,
            "branch": current,
            "message": (
                f"Auto-protect refuses commit on {current or 'detached'} — "
                "use work/<area> for survival saves, or full protect with a message"
            ),
            "staged": [],
            "skipped": skipped,
            "skipped_product": skipped_product,
            "branch_actions": branch_actions,
        }

    code, _, err = _run(repo, "commit", "-m", message)
    if code != 0:
        return {
            "ok": False,
            "error": err or "commit failed",
            "staged": staged_list,
            "branch": current,
            "mode": resolved_mode,
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
        "skipped_product": skipped_product,
        "branch": current,
        "primary_area": primary_area,
        "branch_actions": branch_actions,
        "mode": resolved_mode,
        "pushed": False,
    }

    if push:
        br = collect_branch_status(repo)
        push_result = _push_current(repo, br, mode=resolved_mode)
        result["pushed"] = push_result.get("pushed", False)
        result["push"] = push_result
        # Soft-skip push (policy) is still ok=True; hard push failure is not
        if not push_result.get("ok") and not push_result.get("skipped_policy"):
            result["ok"] = False
            result["error"] = push_result.get("error")
    return result


def _push_allowed(branch: Optional[str], mode: str) -> tuple[bool, str]:
    """Whether protect may push *branch* in *mode*."""
    if not branch:
        return False, "detached HEAD — cannot push"
    if is_master_branch(branch):
        return False, "refusing to push master/main from protect (merge via PR)"
    if mode == "auto":
        if not is_work_branch(branch):
            return (
                False,
                f"auto-protect only pushes work/* (not {branch})",
            )
        return True, ""
    # full: work/* and review branches OK
    if is_work_branch(branch) or is_review_branch(branch):
        return True, ""
    return False, f"refusing to push branch {branch} (not work/* or feature/*|fix/*)"


def _push_current(
    repo: Path,
    br: dict[str, Any],
    *,
    mode: str = "full",
    extra: Optional[dict] = None,
) -> dict[str, Any]:
    current = br.get("current")
    allowed, reason = _push_allowed(current, mode)
    if not allowed:
        result = {
            "ok": True,
            "pushed": False,
            "skipped_policy": True,
            "branch": current,
            "mode": mode,
            "message": reason,
            "error": None,
        }
        if extra:
            result.update(extra)
        return result
    # set upstream if missing
    local = next((b for b in br.get("branches") or [] if b["name"] == current), None)
    if local and not local.get("upstream"):
        code, out, err = _run(repo, "push", "-u", "origin", current)
    else:
        code, out, err = _run(repo, "push", "origin", current)
    result = {
        "ok": code == 0,
        "pushed": code == 0,
        "skipped_policy": False,
        "branch": current,
        "mode": mode,
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
    mode: Optional[str] = None,
) -> dict[str, Any]:
    """Session index snapshot + protect_work + push.

    Without a message (or with ``mode=\"auto\"``), only durable paths are
    committed — product code must use ``sync \"msg\"`` / full mode on a
    feature or work branch.
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

    prot = protect_work(
        repo,
        message=message,
        push=True,
        ensure_work_branch=True,
        mode=mode,
    )
    steps.append({"step": "protect_work", **prot})
    return {
        "ok": all(s.get("ok", True) for s in steps if "ok" in s),
        "steps": steps,
        "branch": prot.get("branch"),
        "committed": prot.get("committed"),
        "pushed": prot.get("pushed"),
        "mode": prot.get("mode"),
    }


if __name__ == "__main__":
    import json
    import sys

    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    rest = args[1:]

    def _parse_mode_msg(tokens: list[str]) -> tuple[Optional[str], Optional[str]]:
        mode: Optional[str] = None
        msg_parts: list[str] = []
        for t in tokens:
            if t in ("--auto", "-a"):
                mode = "auto"
            elif t in ("--full", "-f"):
                mode = "full"
            else:
                msg_parts.append(t)
        msg = " ".join(msg_parts) if msg_parts else None
        return mode, msg

    if cmd == "status":
        json.dump(collect_branch_status(), sys.stdout, indent=2)
    elif cmd == "start" and rest:
        json.dump(start_work(rest[0]), sys.stdout, indent=2)
    elif cmd == "protect":
        mode, msg = _parse_mode_msg(rest)
        json.dump(protect_work(message=msg, mode=mode), sys.stdout, indent=2)
    elif cmd == "sync":
        mode, msg = _parse_mode_msg(rest)
        json.dump(sync_after_work(message=msg, mode=mode), sys.stdout, indent=2)
    elif cmd == "durable" and rest:
        # Debug helper: is path durable?
        print(json.dumps({"path": rest[0], "durable": is_durable_path(rest[0])}))
    else:
        print(
            "Usage: git_workflow.py [status|start <area>|"
            "protect [--auto|--full] [msg]|sync [--auto|--full] [msg]]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print()
