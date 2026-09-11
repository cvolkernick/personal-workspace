#!/usr/bin/env python3
"""Periodic FCC tip SHA / branch-attachment assert (issues #562, #630).

Three outcomes — a check that cannot determine state never reports drift:

1. **ok** — HEAD, origin/work/treasury, attached branch, and
   ``financial-command/current-branch.txt`` all verified good. Silent.
2. **violation** — those preconditions passed and the tip/branch/stamp is
   wrong. ntfy title says git tip drift.
3. **unknown** — directory missing, not a git repo, HEAD/origin unreadable,
   timeout, or ``current-branch.txt`` missing. ntfy title says the check
   failed. Never "git tip drift".

Never mutates git — no checkout, reset, merge, or SYNC_BRANCH change.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

EXPECTED_BRANCH = "work/treasury"
REFUSED_BRANCHES = frozenset({"master", "main", "work/holistic"})
DEFAULT_COOLDOWN_HOURS = 6.0
DEFAULT_NTFY_TOPIC = "cvolk-grok-7f3k9x"
DEFAULT_WORKSPACE = Path.home() / "personal-workspace"
STATE_NAME = "fcc_tip_health_state.json"
HOST = "prism-gateway"
_GIT_ENV_BLOCK = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _git_env() -> dict[str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for key in _GIT_ENV_BLOCK:
        env.pop(key, None)
    return env


def _git(workspace: Path, *args: str, timeout: float = 20.0) -> tuple[int, str, str]:
    cfg = ["-c", "gc.auto=0", "-c", "maintenance.auto=false"]
    try:
        proc = subprocess.run(
            ["git", *cfg, *args],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_git_env(),
        )
    except subprocess.TimeoutExpired as exc:
        return 124, "", f"timeout after {timeout}s: {exc}"
    except OSError as exc:
        return 1, "", str(exc)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _read_current_branch_file(workspace: Path) -> Optional[str]:
    path = workspace / "financial-command" / "current-branch.txt"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return None
    value = text[0].strip() if text else ""
    return value or None


def _gitdir_status(workspace: Path) -> tuple[str, str]:
    """Return ('git', '') or ('not_a_repo', detail). Does not mutate git."""
    git_path = workspace / ".git"
    if not git_path.exists():
        return "not_a_repo", f"{workspace} has no .git"
    if git_path.is_file():
        try:
            text = git_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return "not_a_repo", f"cannot read {git_path}: {exc}"
        if text.lower().startswith("gitdir:"):
            raw = text.split(":", 1)[1].strip()
            gitdir = Path(raw)
            if not gitdir.is_absolute():
                gitdir = (workspace / gitdir).resolve()
            if not gitdir.exists():
                return "not_a_repo", f"broken worktree gitdir: {gitdir} missing"
    rc, out, err = _git(workspace, "rev-parse", "--is-inside-work-tree")
    if rc != 0 or out != "true":
        detail = (err or out or "rev-parse --is-inside-work-tree failed").strip()
        return "not_a_repo", detail
    return "git", ""


def _base_result(
    ws: Path,
    *,
    expected_branch: str,
    origin_ref: str,
    outcome: str,
    unknown_reason: str = "",
    head: str = "",
    origin_sha: str = "",
    attached: str = "",
    stamp: Optional[str] = None,
    mismatches: Optional[list[str]] = None,
) -> dict[str, Any]:
    kind = "healthy" if outcome == "ok" else ("drift" if outcome == "violation" else "unknown")
    return {
        "ok": outcome == "ok",
        "outcome": outcome,
        "kind": kind,
        "unknown_reason": unknown_reason,
        "workspace": str(ws),
        "expected_branch": expected_branch,
        "head": head,
        "origin_sha": origin_sha,
        "origin_ref": origin_ref,
        "attached": attached,
        "current_branch_txt": stamp,
        "mismatches": list(mismatches or []),
        "as_of": utc_now_iso(),
    }


def inspect(
    workspace: Path,
    *,
    expected_branch: str = EXPECTED_BRANCH,
    fetch: bool = False,
    remote: str = "origin",
) -> dict[str, Any]:
    """Read-only inspect. Preconditions first; first failure is UNKNOWN."""
    ws = Path(workspace).expanduser()
    try:
        ws = ws.resolve()
    except OSError:
        ws = Path(workspace).expanduser()
    origin_ref = f"{remote}/{expected_branch}"

    if not ws.exists() or not ws.is_dir():
        return _base_result(
            ws,
            expected_branch=expected_branch,
            origin_ref=origin_ref,
            outcome="unknown",
            unknown_reason="workspace missing",
            mismatches=[f"{ws} is not a directory"],
        )

    repo_kind, repo_detail = _gitdir_status(ws)
    if repo_kind == "not_a_repo":
        return _base_result(
            ws,
            expected_branch=expected_branch,
            origin_ref=origin_ref,
            outcome="unknown",
            unknown_reason="not a git repository",
            stamp=_read_current_branch_file(ws),
            mismatches=[f"not a git repository: {repo_detail}"],
        )

    rc, head, err = _git(ws, "rev-parse", "HEAD")
    if rc != 0 or not head:
        return _base_result(
            ws,
            expected_branch=expected_branch,
            origin_ref=origin_ref,
            outcome="unknown",
            unknown_reason="HEAD unreadable",
            mismatches=[f"cannot read HEAD: {err or 'rev-parse failed'}"],
        )

    attached_rc, attached, attached_err = _git(ws, "branch", "--show-current")
    if attached_rc != 0:
        return _base_result(
            ws,
            expected_branch=expected_branch,
            origin_ref=origin_ref,
            outcome="unknown",
            unknown_reason="HEAD unreadable",
            head=head,
            mismatches=[
                f"cannot read attached branch: {attached_err or 'branch --show-current failed'}"
            ],
        )

    if fetch:
        _git(ws, "fetch", "--prune", remote, expected_branch)

    rc_o, origin_sha, err_o = _git(ws, "rev-parse", origin_ref)
    if rc_o != 0 or not origin_sha:
        return _base_result(
            ws,
            expected_branch=expected_branch,
            origin_ref=origin_ref,
            outcome="unknown",
            unknown_reason="origin ref unreadable",
            head=head,
            attached=attached or "detached",
            mismatches=[f"cannot read {origin_ref}: {err_o or 'missing ref'}"],
        )

    stamp = _read_current_branch_file(ws)
    if stamp is None:
        return _base_result(
            ws,
            expected_branch=expected_branch,
            origin_ref=origin_ref,
            outcome="unknown",
            unknown_reason="current-branch.txt missing",
            head=head,
            origin_sha=origin_sha,
            attached=attached or "detached",
            stamp=None,
            mismatches=["current-branch.txt missing"],
        )

    mismatches: list[str] = []
    if attached != expected_branch:
        mismatches.append(
            f"attached={attached or 'detached'} expected={expected_branch}"
        )
    if attached in REFUSED_BRANCHES:
        mismatches.append(f"refused branch attached: {attached}")
    if stamp != expected_branch:
        mismatches.append(f"current-branch.txt={stamp!r} expected={expected_branch}")
    if head != origin_sha:
        mismatches.append(
            f"HEAD {head[:12]} != {origin_ref} {origin_sha[:12]}"
        )

    if mismatches:
        return _base_result(
            ws,
            expected_branch=expected_branch,
            origin_ref=origin_ref,
            outcome="violation",
            head=head,
            origin_sha=origin_sha,
            attached=attached or "detached",
            stamp=stamp,
            mismatches=mismatches,
        )
    return _base_result(
        ws,
        expected_branch=expected_branch,
        origin_ref=origin_ref,
        outcome="ok",
        head=head,
        origin_sha=origin_sha,
        attached=attached or "detached",
        stamp=stamp,
    )


def _outcome(result: dict[str, Any]) -> str:
    raw = result.get("outcome")
    if raw in {"ok", "violation", "unknown"}:
        return raw
    return "ok" if result.get("ok") else "violation"


def _topic(workspace: Path) -> Optional[str]:
    env = (os.environ.get("FCC_NTFY_TOPIC") or "").strip()
    if env:
        return env
    cfg_path = Path(workspace) / "treasury" / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
        ncfg = cfg.get("notifications") if isinstance(cfg, dict) else None
        if isinstance(ncfg, dict):
            if ncfg.get("enabled") is False:
                return None
            topic = str(ncfg.get("ntfy_topic") or "").strip()
            if topic:
                return topic
    return DEFAULT_NTFY_TOPIC


def _state_path(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path.home() / ".config" / "personal-workspace" / STATE_NAME


def _on_cooldown(path: Path, cooldown_hours: float, *, now: Optional[datetime] = None) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    last = data.get("last_notified_at")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    now_dt = now or datetime.now(timezone.utc)
    return (now_dt - last_dt.astimezone(timezone.utc)).total_seconds() < float(
        cooldown_hours
    ) * 3600.0


def _mark_notified(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "last_notified_at": utc_now_iso(),
        "last_mismatches": payload.get("mismatches"),
        "kind": payload.get("kind"),
        "outcome": _outcome(payload),
        "workspace": payload.get("workspace"),
        "head": payload.get("head"),
        "attached": payload.get("attached"),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _ntfy_title(result: dict[str, Any]) -> str:
    outcome = _outcome(result)
    if outcome == "unknown":
        reason = str(result.get("unknown_reason") or "check failed")
        return f"FCC drift check failed on {HOST} ({reason})"
    expected = result.get("expected_branch") or EXPECTED_BRANCH
    found = result.get("attached") or "unknown"
    return f"FCC git tip drift on {HOST} (expected {expected}, found {found})"


def ntfy_mismatch(
    result: dict[str, Any],
    *,
    workspace: Path,
    state_path: Path,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    dry_run: bool = False,
) -> dict[str, Any]:
    outcome = _outcome(result)
    if outcome == "ok":
        return {"ok": True, "notified": False, "skipped": "healthy"}
    if _on_cooldown(state_path, cooldown_hours):
        return {"ok": True, "notified": False, "skipped": "cooldown"}
    topic = _topic(workspace)
    title = _ntfy_title(result)
    unknown = outcome == "unknown"
    lines = [
        f"outcome={outcome}",
        f"kind={result.get('kind')}",
        f"workspace={result.get('workspace') or workspace}",
        f"expected={result.get('expected_branch')}",
        f"attached={result.get('attached')!r}",
        f"HEAD={(result.get('head') or '')[:12]}",
        f"origin={(result.get('origin_sha') or '')[:12]}",
        f"current-branch.txt={result.get('current_branch_txt')!r}",
        "mismatches:",
        *[f"- {m}" for m in (result.get("mismatches") or [])],
        "Do not auto-reset to master/holistic. Silent to Chris unless kill-switch.",
    ]
    if unknown:
        lines.insert(
            0,
            "CHECK FAILED — could not determine git tip. This is not verified drift.",
        )
    text = "\n".join(lines)
    headers = {
        "Title": title,
        "Priority": "3" if unknown else "4",
        "Tags": "warning,monitor" if unknown else "warning,git",
    }
    if dry_run:
        # Do not consume ntfy cooldown — workspace-sync calls --dry-run.
        return {
            "ok": True,
            "notified": False,
            "skipped": "dry-run",
            "title": title,
            "text": text,
        }
    if not topic:
        _mark_notified(state_path, result)
        return {"ok": True, "notified": False, "skipped": "no-topic", "title": title}
    url = f"https://ntfy.sh/{topic}"
    try:
        req = urllib.request.Request(
            url,
            data=text.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            _mark_notified(state_path, result)
            return {
                "ok": True,
                "notified": True,
                "status": getattr(resp, "status", None) or resp.getcode(),
                "title": title,
            }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "notified": False, "error": str(exc)}


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="FCC live clone (default: ~/personal-workspace, not cwd)",
    )
    p.add_argument("--fetch", action="store_true", help="git fetch origin work/treasury (read-only)")
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="Do not POST ntfy")
    p.add_argument("--state", type=Path, default=None)
    p.add_argument("--cooldown-hours", type=float, default=DEFAULT_COOLDOWN_HOURS)
    args = p.parse_args(argv)
    workspace = args.workspace if args.workspace is not None else DEFAULT_WORKSPACE
    workspace = workspace.expanduser().resolve()
    fetch = bool(args.fetch) and not args.no_fetch
    result = inspect(workspace, fetch=fetch)
    ntfy = ntfy_mismatch(
        result,
        workspace=workspace,
        state_path=_state_path(args.state),
        cooldown_hours=args.cooldown_hours,
        dry_run=args.dry_run,
    )
    result["ntfy"] = ntfy
    outcome = _outcome(result)
    if outcome == "ok":
        print(
            f"[fcc-tip-health] ok HEAD={(result.get('head') or '')[:8]} "
            f"on {result.get('attached')}",
            file=sys.stderr,
        )
    else:
        print(
            f"[fcc-tip-health] {outcome} kind={result.get('kind')} "
            f"workspace={result.get('workspace')} "
            f"mismatches={result.get('mismatches')} "
            f"ntfy={ntfy.get('notified')} skip={ntfy.get('skipped')}",
            file=sys.stderr,
        )
    if args.json:
        print(json.dumps(result, indent=2))
    # Non-zero so the timer unit shows failed, but ntfy is cooldown-gated.
    # 1 = verified violation (drift). 2 = check could not determine state.
    if outcome == "ok":
        return 0
    if outcome == "unknown":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
