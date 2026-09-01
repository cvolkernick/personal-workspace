"""Assemble the OOMWOO project-status payload from README + GitHub."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from ghclient import GitHubClient, GitHubError
from parse import parse_deliverables, parse_modules, parse_v0_targets, summarize

HUB = "makerspet/oomwoo"
RELATED_REPOS = (
    "makerspet/oomwoo",
    "makerspet/oomwoo-install",
    "makerspet/oomwoo-one",
    "makerspet/oomwoo-one-cad",
    "makerspet/oomwoo-pcb",
    "makerspet/oomwoo-io-firmware",
    "makerspet/oomwoo_urdf",
    "makerspet/oomwoo-ros2-tools",
    "makerspet/oomwoo-esp32s3-cm",
    "makerspet/proscenic-m6pro",
)

LINKS = (
    {"label": "GitHub", "url": "https://github.com/makerspet/oomwoo"},
    {"label": "oomwoo.com", "url": "https://oomwoo.com/"},
    {"label": "BOM", "url": "https://github.com/makerspet/oomwoo/blob/main/BOM.md"},
    {"label": "Build (Fall 2026)", "url": "https://github.com/makerspet/oomwoo/blob/main/docs/BUILD_INSTRUCTIONS.md"},
    {"label": "Discord", "url": "https://discord.gg/3y2JKz5T25"},
    {"label": "X", "url": "https://x.com/@0OMWO0"},
    {"label": "Discussions", "url": "https://github.com/makerspet/oomwoo/discussions"},
    {"label": "Architecture", "url": "https://github.com/makerspet/oomwoo/blob/main/docs/ARCHITECTURE.md"},
)

_CACHE: dict[str, Any] = {"payload": None, "at": 0.0}
DEFAULT_TTL = 180.0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_card(data: dict[str, Any]) -> dict[str, Any]:
    license_info = data.get("license") if isinstance(data.get("license"), dict) else {}
    return {
        "full_name": data.get("full_name") or "",
        "description": data.get("description") or "",
        "html_url": data.get("html_url") or "",
        "homepage": data.get("homepage") or "",
        "stars": int(data.get("stargazers_count") or 0),
        "forks": int(data.get("forks_count") or 0),
        "open_issues": int(data.get("open_issues_count") or 0),
        "pushed_at": data.get("pushed_at") or "",
        "updated_at": data.get("updated_at") or "",
        "language": data.get("language") or "",
        "default_branch": data.get("default_branch") or "",
        "topics": list(data.get("topics") or []),
        "license": license_info.get("spdx_id") or license_info.get("name") or "",
        "archived": bool(data.get("archived")),
    }


def _issue_card(item: dict[str, Any]) -> dict[str, Any]:
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    labels = []
    for lab in item.get("labels") or []:
        if isinstance(lab, dict) and lab.get("name"):
            labels.append(str(lab["name"]))
        elif isinstance(lab, str):
            labels.append(lab)
    return {
        "number": item.get("number"),
        "title": item.get("title") or "",
        "html_url": item.get("html_url") or "",
        "user": user.get("login") or "",
        "updated_at": item.get("updated_at") or "",
        "labels": labels,
        "draft": bool(item.get("draft")),
    }


def _commit_card(item: dict[str, Any]) -> dict[str, Any]:
    commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
    author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
    login_obj = item.get("author") if isinstance(item.get("author"), dict) else {}
    message = (commit.get("message") or "").splitlines()[0].strip()
    login = login_obj.get("login") or author.get("name") or ""
    return {
        "sha": (item.get("sha") or "")[:7],
        "html_url": item.get("html_url") or "",
        "message": message,
        "author": login,
        "date": author.get("date") or "",
        "bot": login.endswith("[bot]") or login == "github-actions[bot]",
    }


def build_status(
    client: Optional[GitHubClient] = None,
    *,
    ttl: float = DEFAULT_TTL,
    refresh: bool = False,
) -> dict[str, Any]:
    now = time.time()
    cached = _CACHE.get("payload")
    if cached is not None and not refresh and now - float(_CACHE.get("at") or 0) < ttl:
        return cached

    gh = client or GitHubClient()
    errors: list[str] = []
    readme = ""
    hub: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    pulls: list[dict[str, Any]] = []
    commits: list[dict[str, Any]] = []
    related: list[dict[str, Any]] = []

    try:
        readme = gh.get_text(f"/repos/{HUB}/contents/README.md")
    except GitHubError as exc:
        errors.append(f"readme: {exc}")

    try:
        raw_hub = gh.get_json(f"/repos/{HUB}")
        if isinstance(raw_hub, dict):
            hub = _repo_card(raw_hub)
    except GitHubError as exc:
        errors.append(f"hub: {exc}")

    try:
        raw_issues = gh.get_json(f"/repos/{HUB}/issues?state=open&per_page=20")
        if isinstance(raw_issues, list):
            issues = [
                _issue_card(it)
                for it in raw_issues
                if isinstance(it, dict) and "pull_request" not in it
            ]
    except GitHubError as exc:
        errors.append(f"issues: {exc}")

    try:
        raw_pulls = gh.get_json(f"/repos/{HUB}/pulls?state=open&per_page=15")
        if isinstance(raw_pulls, list):
            pulls = [_issue_card(it) for it in raw_pulls if isinstance(it, dict)]
    except GitHubError as exc:
        errors.append(f"pulls: {exc}")

    try:
        raw_commits = gh.get_json(f"/repos/{HUB}/commits?per_page=12")
        if isinstance(raw_commits, list):
            commits = [_commit_card(it) for it in raw_commits if isinstance(it, dict)]
    except GitHubError as exc:
        errors.append(f"commits: {exc}")

    for full_name in RELATED_REPOS:
        try:
            raw = gh.get_json(f"/repos/{full_name}")
            if isinstance(raw, dict):
                related.append(_repo_card(raw))
        except GitHubError as exc:
            if getattr(exc, "status", 0) == 404:
                continue
            errors.append(f"{full_name}: {exc}")

    modules = parse_modules(readme) if readme else []
    deliverables = parse_deliverables(readme) if readme else []
    v0 = parse_v0_targets(readme) if readme else []
    progress = summarize(modules, deliverables)
    human_commits = [c for c in commits if not c.get("bot")]
    payload = {
        "ok": not errors or bool(modules or hub),
        "service": "oomwoo",
        "fetched_at": _now(),
        "hub": hub,
        "progress": progress,
        "modules": modules,
        "deliverables": deliverables,
        "v0": v0,
        "related": related,
        "issues": issues,
        "pulls": pulls,
        "commits": commits,
        "last_human_commit": human_commits[0] if human_commits else None,
        "links": [dict(x) for x in LINKS],
        "errors": errors,
        "note": "Tracker for makerspet/oomwoo (open-source robot vacuum). Not a product of this workspace.",
    }
    _CACHE["payload"] = payload
    _CACHE["at"] = now
    return payload


def load_fixture(path: str) -> dict[str, Any]:
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("fixture must be a JSON object")
    data.setdefault("ok", True)
    data.setdefault("service", "oomwoo")
    data.setdefault("fetched_at", _now())
    return data
