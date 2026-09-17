#!/usr/bin/env python3
"""Periodic FCC tip SHA / branch-attachment assert (issues #562, #628, #630 / #704).

Three outcomes — a check that cannot determine state never reports drift:

1. **ok** / kind=healthy — HEAD, origin/work/treasury, attached branch, and
   ``financial-command/current-branch.txt`` all verified good. Silent.
2. **violation** / kind=drift — those preconditions passed and the tip/branch
   /stamp is wrong. GitHub title says git tip drift.
3. **unknown** / kind=not_a_repo or kind=unknown — directory missing, not a
   git repo, HEAD/origin unreadable, timeout, or ``current-branch.txt``
   missing. GitHub title says git check path. Never "git tip drift".

On mismatch: log + GitHub comment on the standing ops issue (#701).
Routine and SUSTAINED comments are silent to the repo owner — no GitHub
``@`` mention, no Buzz owner mention, no ``#Orchestration`` page (#659).
``FCC_ALERT_KILL_SWITCH`` adds an owner ``@`` mention so the #562
user-alert still reaches them. ntfy is retired.
Never mutates git — no checkout, reset, merge, or SYNC_BRANCH change.

``not a git repository`` is kind=not_a_repo (check-path / broken gitdir),
not kind=drift (wrong branch or SHA). Alerts include the workspace path.
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
DEFAULT_OPS_ISSUE = "701"
DEFAULT_WORKSPACE = Path.home() / "personal-workspace"
_NTFY_RETIRED_WARNED = False
OPS_REPO = "cvolkernick/personal-workspace"
OWNER_GITHUB_LOGIN = "cvolkernick"
SCHEDULER_ENV = Path.home() / ".config" / "workflow-scheduler.env"
STATE_NAME = "fcc_tip_health_state.json"
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
    kind: str = "",
    unknown_reason: str = "",
    head: str = "",
    origin_sha: str = "",
    attached: str = "",
    stamp: Optional[str] = None,
    mismatches: Optional[list[str]] = None,
) -> dict[str, Any]:
    if not kind:
        if outcome == "ok":
            kind = "healthy"
        elif outcome == "violation":
            kind = "drift"
        else:
            kind = "unknown"
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
    """Read-only inspect. Preconditions first; first failure is UNKNOWN (#630)."""
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
            kind="not_a_repo",
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
            kind="not_a_repo",
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
            kind="unknown",
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
            kind="unknown",
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
            kind="unknown",
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
            kind="unknown",
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
    if result.get("ok"):
        return "ok"
    kind = str(result.get("kind") or "")
    if kind in {"not_a_repo", "unknown"}:
        return "unknown"
    return "violation"


def _leftover_ntfy_topic(workspace: Path) -> Optional[str]:
    """Return leftover ntfy topic config so we can warn. Never used to POST."""
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
            topic = str(ncfg.get("ntfy_topic") or "").strip()
            if topic:
                return topic
    return None


def _warn_retired_ntfy(*, topic: Optional[str] = None) -> None:
    """If leftover ntfy config is set, warn once and ignore. Never fail (#704)."""
    global _NTFY_RETIRED_WARNED
    if _NTFY_RETIRED_WARNED:
        return
    _load_scheduler_env()
    leftover: list[str] = []
    if (os.environ.get("NTFY_TOKEN") or "").strip():
        leftover.append("NTFY_TOKEN")
    if (os.environ.get("FCC_NTFY_TOKEN") or "").strip():
        leftover.append("FCC_NTFY_TOKEN")
    if (os.environ.get("FCC_NTFY_TOPIC") or "").strip():
        leftover.append("FCC_NTFY_TOPIC")
    if (topic or "").strip():
        leftover.append("notifications.ntfy_topic")
    if not leftover:
        return
    _NTFY_RETIRED_WARNED = True
    print(
        "WARN: ntfy is retired (#704); ignoring "
        + ", ".join(leftover)
        + " — alerts go to GitHub issue #701 only",
        file=sys.stderr,
    )


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
        "kind": payload.get("kind"),
        "outcome": _outcome(payload),
        "workspace": payload.get("workspace"),
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
    """Persist first_violation_at even when the alert is on cooldown (#661)."""
    prev = _load_state(path)
    body = dict(prev)
    body["last_mismatches"] = payload.get("mismatches")
    body["kind"] = payload.get("kind")
    body["outcome"] = _outcome(payload)
    body["workspace"] = payload.get("workspace")
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


def _ops_issue() -> str:
    return (os.environ.get("PI_OPS_ALERT_ISSUE") or DEFAULT_OPS_ISSUE).strip()


def _kill_switch() -> bool:
    v = (os.environ.get("FCC_ALERT_KILL_SWITCH") or "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def _page_owner(*, kill: bool) -> bool:
    """#562 / #659: GitHub-mention the repo owner only on kill-switch."""
    return bool(kill)


def build_ops_comment_body(title: str, text: str, *, page_owner: bool) -> str:
    """Compose the #701 comment. Owner @mention sits outside the fence.

    Routine / sustained: no owner login @mention (skip-Chris). Team still
    sees the standing issue comment. Kill-switch: @mention the owner.
    """
    if page_owner:
        routing = (
            f"@{OWNER_GITHUB_LOGIN} kill-switch / dangerous-path user-alert "
            "(#562). Team: this #701 comment."
        )
    else:
        routing = (
            "Silent to owner (#659): no GitHub owner @mention, no Buzz owner "
            "mention, no #Orchestration page. Team path: this #701 comment "
            "(and/or #workflow)."
        )
    return f"**{title}**\n\n{routing}\n\n```\n{text}\n```\n"


def _http_post(url: str, data: bytes, headers: dict[str, str], timeout: float = 15.0) -> dict[str, Any]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {"ok": True, "status": getattr(resp, "status", None) or resp.getcode()}


def post_ops_github(
    title: str,
    text: str,
    *,
    dry_run: bool = False,
    page_owner: bool = False,
) -> dict[str, Any]:
    """Comment on the standing ops issue (#701). Never logs tokens."""
    _load_scheduler_env()
    _warn_retired_ntfy()
    issue = _ops_issue()
    if not issue:
        return {"ok": True, "posted": False, "skipped": "no-issue"}
    if dry_run:
        return {"ok": True, "posted": False, "skipped": "dry-run", "issue": issue}
    token = _github_token()
    if not token:
        return {"ok": True, "posted": False, "skipped": "no-github-token", "issue": issue}
    body = build_ops_comment_body(title, text, page_owner=page_owner)
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


def _alert_kind_label(kind: str) -> str:
    if kind in {"not_a_repo", "unknown"}:
        return "git check path"
    return "git tip drift"


def _alert_title(kind: str, *, sustained: bool, age_h: float, kill: bool) -> str:
    label = _alert_kind_label(kind)
    if sustained:
        return f"FCC · {label} SUSTAINED {age_h:.1f}h · prism-gateway"
    if kill:
        return f"FCC · {label} KILL-SWITCH · prism-gateway"
    return f"FCC · {label} · prism-gateway"


def alert_mismatch(
    result: dict[str, Any],
    *,
    workspace: Path,
    state_path: Path,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    sustained_hours: float = DEFAULT_SUSTAINED_HOURS,
    sustained_cooldown_hours: float = DEFAULT_SUSTAINED_COOLDOWN_HOURS,
    dry_run: bool = False,
) -> dict[str, Any]:
    _warn_retired_ntfy(topic=_leftover_ntfy_topic(workspace))
    outcome = _outcome(result)
    if outcome == "ok":
        if not dry_run:
            _mark_healthy(state_path)
        return {"ok": True, "notified": False, "skipped": "healthy"}

    kind = str(result.get("kind") or "drift")
    if outcome == "unknown" and kind == "drift":
        kind = "unknown"
    prev = _load_state(state_path)
    # --dry-run (workspace-sync hook) must not write state / consume cooldown.
    if not dry_run and not prev.get("first_violation_at"):
        _record_violation_seen(state_path, result)
        prev = _load_state(state_path)
    age_h = _violation_age_hours(prev)
    sustained = age_h >= float(sustained_hours)
    result = {
        **result,
        "kind": kind,
        "outcome": outcome,
        "sustained": sustained,
        "sustained_hours": age_h,
    }

    effective_cd = (
        float(sustained_cooldown_hours) if sustained else float(cooldown_hours)
    )
    if _on_cooldown(state_path, effective_cd):
        if not dry_run:
            _record_violation_seen(state_path, result)
        return {
            "ok": True,
            "notified": False,
            "skipped": "cooldown",
            "sustained": sustained,
        }

    kill = _kill_switch()
    owner_page = _page_owner(kill=kill)
    page = bool(sustained or kill)
    title = _alert_title(kind, sustained=sustained, age_h=age_h, kill=kill)
    if sustained:
        action = (
            "Sustained >1h — page #workflow (Forge). Silent to owner. "
            "Do not auto-reset to master/holistic."
        )
    elif kill:
        action = (
            "Kill-switch — owner user-alert (#562). "
            "Do not auto-reset to master/holistic."
        )
    else:
        action = (
            "Do not auto-reset to master/holistic. "
            "Silent to owner unless kill-switch."
        )
    lines = [
        f"outcome={outcome}",
        f"kind={kind}",
        f"workspace={result.get('workspace') or workspace}",
        f"expected={result.get('expected_branch')}",
        f"attached={result.get('attached')!r}",
        f"HEAD={(result.get('head') or '')[:12]}",
        f"origin={(result.get('origin_sha') or '')[:12]}",
        f"current-branch.txt={result.get('current_branch_txt')!r}",
        f"sustained_hours={age_h:.2f}",
        f"page={page} page_owner={owner_page} kill_switch={kill}",
        "mismatches:",
        *[f"- {m}" for m in (result.get("mismatches") or [])],
        action,
    ]
    if outcome == "unknown":
        reason = str(result.get("unknown_reason") or "check failed")
        lines.insert(1, f"unknown_reason={reason}")
        lines.insert(
            0,
            "CHECK FAILED — could not determine git tip. This is not verified drift.",
        )
    text = "\n".join(lines)
    if dry_run:
        return {
            "ok": True,
            "notified": False,
            "skipped": "dry-run",
            "title": title,
            "text": text,
            "body": build_ops_comment_body(title, text, page_owner=owner_page),
            "sustained": sustained,
            "page": page,
            "page_owner": owner_page,
            "github": {
                "ok": True,
                "posted": False,
                "skipped": "dry-run",
                "issue": _ops_issue(),
            },
        }
    github = post_ops_github(
        title, text, dry_run=False, page_owner=owner_page
    )
    gh_ok = bool(
        github.get("posted")
        or github.get("skipped") in {"no-github-token", "no-issue"}
    )
    if gh_ok:
        _mark_notified(state_path, result)
    skipped = None
    if not github.get("posted"):
        skipped = github.get("skipped")
    return {
        "ok": bool(github.get("ok", True)),
        "notified": bool(github.get("posted")),
        "skipped": skipped,
        "title": title,
        "text": text,
        "body": build_ops_comment_body(title, text, page_owner=owner_page),
        "sustained": sustained,
        "page": page,
        "page_owner": owner_page,
        "github": github,
        "error": github.get("error"),
    }


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
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not POST GitHub or write cooldown state",
    )
    p.add_argument("--state", type=Path, default=None)
    p.add_argument("--cooldown-hours", type=float, default=DEFAULT_COOLDOWN_HOURS)
    args = p.parse_args(argv)
    workspace = args.workspace if args.workspace is not None else DEFAULT_WORKSPACE
    workspace = workspace.expanduser().resolve()
    fetch = bool(args.fetch) and not args.no_fetch
    result = inspect(workspace, fetch=fetch)
    alert = alert_mismatch(
        result,
        workspace=workspace,
        state_path=_state_path(args.state),
        cooldown_hours=args.cooldown_hours,
        dry_run=args.dry_run,
    )
    result["alert"] = alert
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
            f"github={((alert.get('github') or {}).get('posted'))} "
            f"skip={alert.get('skipped')}",
            file=sys.stderr,
        )
    if args.json:
        print(json.dumps(result, indent=2))
    # Non-zero so the timer unit shows failed, but comments are cooldown-gated.
    # 1 = verified violation (drift). 2 = check could not determine state.
    if outcome == "ok":
        return 0
    if outcome == "unknown":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
