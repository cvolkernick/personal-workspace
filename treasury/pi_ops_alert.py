"""Pi alert sinks for #699 Option B: GitHub standing issue + ntfy pages.

Routine / observability → GitHub comment on #701.
Pages (pri-5 / kill-switch / fund-manager error) → ntfy.sh as well.

Tokens are read from the environment (workflow-scheduler.env). Never log them.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_OPS_ISSUE = "701"
OPS_REPO = "cvolkernick/personal-workspace"
SCHEDULER_ENV = Path.home() / ".config" / "workflow-scheduler.env"


def load_scheduler_env(path: Optional[Path] = None) -> None:
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


def github_token() -> str:
    return (
        (os.environ.get("GITHUB_TOKEN") or "").strip()
        or (os.environ.get("GH_TOKEN") or "").strip()
        or (os.environ.get("BUZZ_BOARD_GITHUB_TOKEN") or "").strip()
    )


def ntfy_token() -> str:
    return (
        (os.environ.get("NTFY_TOKEN") or "").strip()
        or (os.environ.get("FCC_NTFY_TOKEN") or "").strip()
    )


def ops_issue() -> str:
    return (os.environ.get("PI_OPS_ALERT_ISSUE") or DEFAULT_OPS_ISSUE).strip()


def kill_switch() -> bool:
    v = (os.environ.get("FCC_ALERT_KILL_SWITCH") or "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def _http_post(url: str, data: bytes, headers: dict[str, str], timeout: float = 15.0) -> dict[str, Any]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {"ok": True, "status": getattr(resp, "status", None) or resp.getcode()}


def post_ops_github(title: str, text: str, *, dry_run: bool = False) -> dict[str, Any]:
    load_scheduler_env()
    issue = ops_issue()
    if not issue:
        return {"ok": True, "posted": False, "skipped": "no-issue"}
    if dry_run:
        return {"ok": True, "posted": False, "skipped": "dry-run", "issue": issue}
    token = github_token()
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
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
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
    load_scheduler_env()
    if not topic:
        return {"ok": True, "notified": False, "skipped": "no-topic"}
    if dry_run:
        return {"ok": True, "notified": False, "skipped": "dry-run", "title": title}
    headers = {
        "Title": title,
        "Priority": str(priority),
        "Tags": tags,
        "Click": f"https://github.com/{OPS_REPO}/issues/{ops_issue()}",
    }
    token = ntfy_token()
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
