#!/usr/bin/env python3
"""Periodic FCC tip SHA / branch-attachment assert (issue #562 / #699).

Live clone HEAD must equal origin/work/treasury, the checkout must be
attached to that branch, and financial-command/current-branch.txt must
match. On mismatch: log + GitHub comment on the standing ops issue
(#701). ntfy only for SUSTAINED (>1h red) or FCC_ALERT_KILL_SWITCH.
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
DEFAULT_SUSTAINED_HOURS = 1.0
DEFAULT_SUSTAINED_COOLDOWN_HOURS = 1.0
DEFAULT_NTFY_TOPIC = "cvolk-grok-7f3k9x"
DEFAULT_OPS_ISSUE = "701"
OPS_REPO = "cvolkernick/personal-workspace"
SCHEDULER_ENV = Path.home() / ".config" / "workflow-scheduler.env"
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


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _on_cooldown(path: Path, cooldown_hours: float, *, now: Optional[datetime] = None) -> bool:
    last_dt = _parse_iso(_load_state(path).get("last_notified_at"))
    if last_dt is None:
        return False
    now_dt = now or datetime.now(timezone.utc)
    return (now_dt - last_dt).total_seconds() < float(cooldown_hours) * 3600.0


def _violation_age_hours(data: dict[str, Any], *, now: Optional[datetime] = None) -> float:
    first = _parse_iso(data.get("first_violation_at"))
    if first is None:
        return 0.0
    now_dt = now or datetime.now(timezone.utc)
    return max(0.0, (now_dt - first).total_seconds() / 3600.0)


def _mark_notified(path: Path, payload: dict[str, Any]) -> None:
    prev = _load_state(path)
    first = prev.get("first_violation_at") or utc_now_iso()
    body = {
        "last_notified_at": utc_now_iso(),
        "last_mismatches": payload.get("mismatches"),
        "head": payload.get("head"),
        "attached": payload.get("attached"),
        "first_violation_at": first,
        "sustained": bool(payload.get("sustained")),
    }
    _write_state(path, body)


def _mark_healthy(path: Path) -> None:
    prev = _load_state(path)
    if not prev:
        return
    body = {k: v for k, v in prev.items() if k != "first_violation_at"}
    body["outcome"] = "ok"
    body["sustained"] = False
    _write_state(path, body)


def _record_violation_seen(path: Path, payload: dict[str, Any]) -> None:
    """Persist first_violation_at even when ntfy is on cooldown (#661)."""
    prev = _load_state(path)
    body = dict(prev)
    body["last_mismatches"] = payload.get("mismatches")
    body["head"] = payload.get("head")
    body["attached"] = payload.get("attached")
    if not body.get("first_violation_at"):
        body["first_violation_at"] = utc_now_iso()
    _write_state(path, body)


def _load_scheduler_env(path: Optional[Path] = None) -> None:
    """Load KEY=VALUE from workflow-scheduler.env without overwriting or logging."""
    env_path = Path(path) if path is not None else SCHEDULER_ENV
    if not env_path.is_file():
        return
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def _github_token() -> str:
    return (
        (os.environ.get("GITHUB_TOKEN") or "").strip()
        or (os.environ.get("GH_TOKEN") or "").strip()
        or (os.environ.get("BUZZ_BOARD_GITHUB_TOKEN") or "").strip()
    )


def _ntfy_token() -> str:
    return (
        (os.environ.get("NTFY_TOKEN") or "").strip()
        or (os.environ.get("FCC_NTFY_TOKEN") or "").strip()
    )


def _ops_issue() -> str:
    return (os.environ.get("PI_OPS_ALERT_ISSUE") or DEFAULT_OPS_ISSUE).strip()


def _kill_switch() -> bool:
    v = (os.environ.get("FCC_ALERT_KILL_SWITCH") or "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def _http_post(url: str, data: bytes, headers: dict[str, str], timeout: float = 15.0) -> dict[str, Any]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {"ok": True, "status": getattr(resp, "status", None) or resp.getcode()}


def post_ops_github(title: str, text: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Comment on the standing ops issue (#701). Never logs tokens."""
    _load_scheduler_env()
    issue = _ops_issue()
    if not issue:
        return {"ok": True, "posted": False, "skipped": "no-issue"}
    if dry_run:
        return {"ok": True, "posted": False, "skipped": "dry-run", "issue": issue}
    token = _github_token()
    if not token:
        return {"ok": True, "posted": False, "skipped": "no-github-token", "issue": issue}
    body = f"**{title}**\n\n```\n{text}\n```\n"
    url = f"https://api.github.com/repos/{OPS_REPO}/issues/{issue}/comments"
    payload = json.dumps({"body": body}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "personal-workspace-pi-alerts",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        out = _http_post(url, payload, headers)
        out["posted"] = True
        out["issue"] = issue
        return out
    except (urllib.error.URLError, TimeoutError, OSError, urllib.error.HTTPError) as exc:
        return {"ok": False, "posted": False, "error": str(exc), "issue": issue}


def post_ntfy_page(
    topic: Optional[str],
    title: str,
    text: str,
    *,
    priority: str = "5",
    tags: str = "warning,rotating_light",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Page via ntfy. Auth header when NTFY_TOKEN is set; public topic otherwise."""
    _load_scheduler_env()
    if not topic:
        return {"ok": True, "notified": False, "skipped": "no-topic"}
    if dry_run:
        return {"ok": True, "notified": False, "skipped": "dry-run", "title": title}
    headers = {
        "Title": title,
        "Priority": str(priority),
        "Tags": tags,
        "Click": f"https://github.com/{OPS_REPO}/issues/{_ops_issue()}",
    }
    token = _ntfy_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://ntfy.sh/{topic}"
    try:
        out = _http_post(url, text.encode("utf-8"), headers)
        out["notified"] = True
        out["title"] = title
        out["authed"] = bool(token)
        return out
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "notified": False, "error": str(exc)}


def ntfy_mismatch(
    result: dict[str, Any],
    *,
    workspace: Path,
    state_path: Path,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    sustained_hours: float = DEFAULT_SUSTAINED_HOURS,
    sustained_cooldown_hours: float = DEFAULT_SUSTAINED_COOLDOWN_HOURS,
    dry_run: bool = False,
) -> dict[str, Any]:
    if result.get("ok"):
        _mark_healthy(state_path)
        return {"ok": True, "notified": False, "skipped": "healthy"}

    prev = _load_state(state_path)
    if not prev.get("first_violation_at"):
        _record_violation_seen(state_path, result)
        prev = _load_state(state_path)
    age_h = _violation_age_hours(prev)
    sustained = age_h >= float(sustained_hours)
    result = {**result, "sustained": sustained, "sustained_hours": age_h}

    effective_cd = (
        float(sustained_cooldown_hours) if sustained else float(cooldown_hours)
    )
    if _on_cooldown(state_path, effective_cd):
        _record_violation_seen(state_path, result)
        return {
            "ok": True,
            "notified": False,
            "skipped": "cooldown",
            "sustained": sustained,
        }

    kill = _kill_switch()
    page = bool(sustained or kill)
    title = (
        f"FCC · git tip drift SUSTAINED {age_h:.1f}h · prism-gateway"
        if sustained
        else "FCC · git tip drift · prism-gateway"
    )
    if kill and not sustained:
        title = "FCC · git tip drift KILL-SWITCH · prism-gateway"
    action = (
        "Sustained >1h — page #workflow (Forge). Do not auto-reset to master/holistic."
        if sustained
        else "Do not auto-reset to master/holistic. Silent to Chris unless kill-switch."
    )
    lines = [
        f"expected={result.get('expected_branch')}",
        f"attached={result.get('attached')}",
        f"HEAD={(result.get('head') or '')[:12]}",
        f"origin={(result.get('origin_sha') or '')[:12]}",
        f"current-branch.txt={result.get('current_branch_txt')!r}",
        f"sustained_hours={age_h:.2f}",
        f"page={page} kill_switch={kill}",
        "mismatches:",
        *[f"- {m}" for m in (result.get("mismatches") or [])],
        action,
    ]
    text = "\n".join(lines)
    github = post_ops_github(title, text, dry_run=dry_run)
    ntfy_out: dict[str, Any]
    if page:
        ntfy_out = post_ntfy_page(
            _topic(workspace),
            title,
            text,
            priority="5",
            tags="warning,git",
            dry_run=dry_run,
        )
    else:
        ntfy_out = {
            "ok": True,
            "notified": False,
            "skipped": "routine",
            "title": title,
        }
    gh_ok = bool(
        github.get("posted")
        or github.get("skipped") in {"dry-run", "no-github-token", "no-issue"}
    )
    ntfy_ok = bool(
        (not page)
        or ntfy_out.get("notified")
        or ntfy_out.get("skipped") in {"dry-run", "no-topic"}
    )
    if gh_ok and ntfy_ok:
        _mark_notified(state_path, result)
    skipped = None
    if dry_run:
        skipped = "dry-run"
    elif not page:
        skipped = ntfy_out.get("skipped") or "routine"
    elif not ntfy_out.get("notified"):
        skipped = ntfy_out.get("skipped")
    return {
        "ok": bool(github.get("ok", True) and ntfy_out.get("ok", True)),
        "notified": bool(ntfy_out.get("notified")),
        "skipped": skipped,
        "title": title,
        "text": text,
        "sustained": sustained,
        "page": page,
        "github": github,
        "ntfy": ntfy_out,
        "error": github.get("error") or ntfy_out.get("error"),
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", type=Path, default=None)
    p.add_argument("--fetch", action="store_true", help="git fetch origin work/treasury (read-only)")
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not POST GitHub or ntfy",
    )
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
            f"ntfy={ntfy.get('notified')} github={((ntfy.get('github') or {}).get('posted'))} "
            f"skip={ntfy.get('skipped')}",
            file=sys.stderr,
        )
    if args.json:
        print(json.dumps(result, indent=2))
    # Non-zero so the timer unit shows failed, but ntfy is cooldown-gated.
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
