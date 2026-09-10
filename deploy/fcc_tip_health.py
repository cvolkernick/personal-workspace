#!/usr/bin/env python3
"""Periodic FCC tip SHA / branch-attachment assert (issue #562).

Live clone HEAD must equal origin/work/treasury, the checkout must be
attached to that branch, and financial-command/current-branch.txt must
match. On mismatch: log + ntfy once (cooldown). Never mutates git — no
checkout, reset, merge, or SYNC_BRANCH change.
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
STATE_NAME = "fcc_tip_health_state.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _git(workspace: Path, *args: str, timeout: float = 20.0) -> tuple[int, str, str]:
    cfg = ["-c", "gc.auto=0", "-c", "maintenance.auto=false"]
    proc = subprocess.run(
        ["git", *cfg, *args],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _read_current_branch_file(workspace: Path) -> Optional[str]:
    path = workspace / "financial-command" / "current-branch.txt"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return None
    return text[0].strip() if text else ""


def inspect(
    workspace: Path,
    *,
    expected_branch: str = EXPECTED_BRANCH,
    fetch: bool = False,
    remote: str = "origin",
) -> dict[str, Any]:
    """Read-only inspect. Never checkout/reset."""
    ws = Path(workspace)
    mismatches: list[str] = []
    rc, head, err = _git(ws, "rev-parse", "HEAD")
    if rc != 0:
        mismatches.append(f"cannot read HEAD: {err or 'rev-parse failed'}")
        head = ""
    attached_rc, attached, _ = _git(ws, "branch", "--show-current")
    attached = attached if attached_rc == 0 else ""
    stamp = _read_current_branch_file(ws)
    origin_ref = f"{remote}/{expected_branch}"
    if fetch:
        _git(ws, "fetch", "--prune", remote, expected_branch)
    rc_o, origin_sha, err_o = _git(ws, "rev-parse", origin_ref)
    if rc_o != 0:
        mismatches.append(f"cannot read {origin_ref}: {err_o or 'missing ref'}")
        origin_sha = ""

    if attached != expected_branch:
        mismatches.append(
            f"attached={attached or 'detached'} expected={expected_branch}"
        )
    if attached in REFUSED_BRANCHES:
        mismatches.append(f"refused branch attached: {attached}")
    if stamp is None:
        mismatches.append("current-branch.txt missing")
    elif stamp != expected_branch:
        mismatches.append(f"current-branch.txt={stamp!r} expected={expected_branch}")
    if head and origin_sha and head != origin_sha:
        mismatches.append(
            f"HEAD {head[:12]} != {origin_ref} {origin_sha[:12]}"
        )

    return {
        "ok": not mismatches,
        "workspace": str(ws),
        "expected_branch": expected_branch,
        "head": head,
        "origin_sha": origin_sha,
        "origin_ref": origin_ref,
        "attached": attached or "detached",
        "current_branch_txt": stamp,
        "mismatches": mismatches,
        "as_of": utc_now_iso(),
    }


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
        "head": payload.get("head"),
        "attached": payload.get("attached"),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def ntfy_mismatch(
    result: dict[str, Any],
    *,
    workspace: Path,
    state_path: Path,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    dry_run: bool = False,
) -> dict[str, Any]:
    if result.get("ok"):
        return {"ok": True, "notified": False, "skipped": "healthy"}
    if _on_cooldown(state_path, cooldown_hours):
        return {"ok": True, "notified": False, "skipped": "cooldown"}
    topic = _topic(workspace)
    if not topic:
        _mark_notified(state_path, result)
        return {"ok": True, "notified": False, "skipped": "no-topic"}
    title = "FCC · git tip drift · prism-gateway"
    lines = [
        f"expected={result.get('expected_branch')}",
        f"attached={result.get('attached')}",
        f"HEAD={(result.get('head') or '')[:12]}",
        f"origin={(result.get('origin_sha') or '')[:12]}",
        f"current-branch.txt={result.get('current_branch_txt')!r}",
        "mismatches:",
        *[f"- {m}" for m in (result.get("mismatches") or [])],
        "Do not auto-reset to master/holistic. Silent to Chris unless kill-switch.",
    ]
    text = "\n".join(lines)
    if dry_run:
        _mark_notified(state_path, result)
        return {"ok": True, "notified": False, "skipped": "dry-run", "title": title, "text": text}
    url = f"https://ntfy.sh/{topic}"
    try:
        req = urllib.request.Request(
            url,
            data=text.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "4",
                "Tags": "warning,git",
            },
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
    p.add_argument("--workspace", type=Path, default=None)
    p.add_argument("--fetch", action="store_true", help="git fetch origin work/treasury (read-only)")
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="Do not POST ntfy")
    p.add_argument("--state", type=Path, default=None)
    p.add_argument("--cooldown-hours", type=float, default=DEFAULT_COOLDOWN_HOURS)
    args = p.parse_args(argv)
    workspace = (args.workspace or Path.cwd()).resolve()
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
    if result["ok"]:
        print(
            f"[fcc-tip-health] ok HEAD={(result.get('head') or '')[:8]} "
            f"on {result.get('attached')}",
            file=sys.stderr,
        )
    else:
        print(
            f"[fcc-tip-health] MISMATCH {result.get('mismatches')} "
            f"ntfy={ntfy.get('notified')} skip={ntfy.get('skipped')}",
            file=sys.stderr,
        )
    if args.json:
        print(json.dumps(result, indent=2))
    # Non-zero so the timer unit shows failed, but ntfy is cooldown-gated.
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
