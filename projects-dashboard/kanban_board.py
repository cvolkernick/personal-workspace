#!/usr/bin/env python3
"""Read-only eng backlog board over GitHub issues.

Issues in cvolkernick/personal-workspace stay the source of truth. This module
maps them into columns and, only when KANBAN_WRITE=1, replaces status:* labels.
It never writes rank, priority, sprint, or assignees. The token stays here.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from notion_sprint_sync import parse_worker

API_HOST = "https://api.github.com"
DEFAULT_REPO = "cvolkernick/personal-workspace"
DONE_WINDOW = timedelta(days=14)
MAX_PAGES = 40
TOKEN_ENV_KEYS = ("GITHUB_TOKEN", "GH_TOKEN", "BUZZ_BOARD_GITHUB_TOKEN")

COLUMNS = (
    {"id": "backlog", "title": "Backlog", "droppable": True},
    {"id": "ready", "title": "Ready", "droppable": True},
    {"id": "in_progress", "title": "In Progress", "droppable": True},
    {"id": "pending_review", "title": "Pending Review", "droppable": True},
    {"id": "done", "title": "Done", "droppable": False},
)

# pending-review beats in-progress beats ready. status:done is last so an
# open issue with both ready and done stays in Ready and shows a conflict.
STATUS_PRECEDENCE = (
    "status:pending-review",
    "status:in-progress",
    "status:ready",
    "status:done",
)
COLUMN_FOR_LABEL = {
    "status:pending-review": "pending_review",
    "status:in-progress": "in_progress",
    "status:ready": "ready",
    "status:done": "done",
}
LABEL_FOR_COLUMN = {
    "backlog": None,
    "ready": "status:ready",
    "in_progress": "status:in-progress",
    "pending_review": "status:pending-review",
}
BADGE_LABELS = ("status:parked", "parked", "idea", "human-only")
FORBIDDEN_WRITE_KEYS = ("priority", "sprint", "rank", "ranking", "assignee", "assignees")

ISSUES_QUERY = """
query($cursor: String, $owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    issues(first: 50, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        body
        state
        stateReason
        closedAt
        url
        labels(first: 30) { nodes { name } }
        closedByPullRequestsReferences(first: 10) {
          nodes { number state url isDraft }
        }
        timelineItems(first: 20, itemTypes: [CROSS_REFERENCED_EVENT]) {
          nodes {
            ... on CrossReferencedEvent {
              source {
                __typename
                ... on PullRequest { number state url isDraft }
              }
            }
          }
        }
      }
    }
  }
}
""".strip()


class KanbanAuthError(RuntimeError):
    pass


class GitHubError(RuntimeError):
    pass


def write_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    raw = source.get("KANBAN_WRITE")
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _gh_auth_token() -> str:
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def resolve_token(env: Mapping[str, str] | None = None) -> str:
    """Env token first. An omitted env may use `gh auth token`. An explicit env does not."""
    source = os.environ if env is None else env
    for key in TOKEN_ENV_KEYS:
        value = (source.get(key) or "").strip()
        if value:
            return value
    if env is None:
        token = _gh_auth_token()
        if token:
            return token
    raise KanbanAuthError("GitHub token is not configured")


def worker_name(issue: Mapping[str, Any]) -> str | None:
    """**Worker:** line, then an owner:* label. Empty or broken lines fall through."""
    parsed = parse_worker(issue.get("body"))
    if parsed:
        return parsed
    for name in issue.get("labels") or []:
        text = str(name)
        if text.lower().startswith("owner:"):
            value = text.split(":", 1)[1].strip()
            if value:
                return value
    return None


def chips_for(labels: list[str]) -> list[dict[str, str]]:
    chips: list[dict[str, str]] = []
    for name in labels:
        if name.startswith("status:") or name in {"parked", "idea", "human-only"}:
            continue
        if len(name) >= 2 and name[0] == "P" and name[1:].isdigit():
            kind = "priority"
        elif name.startswith("owner:"):
            kind = "owner"
        elif name.startswith("sprint:"):
            kind = "sprint"
        else:
            kind = "area"
        chips.append({"name": name, "kind": kind})
    return chips


def badges_for(labels: set[str], conflict: bool) -> list[str]:
    badges = [name for name in BADGE_LABELS if name in labels]
    if conflict:
        badges.append("label conflict")
    return badges


def pr_state(node: Mapping[str, Any]) -> str:
    raw = str(node.get("state") or "").upper()
    if node.get("isDraft") and raw == "OPEN":
        return "draft"
    if raw == "MERGED":
        return "merged"
    if raw == "OPEN":
        return "open"
    if raw == "CLOSED":
        return "closed"
    return raw.lower() or "closed"


def extract_pull_requests(node: Mapping[str, Any]) -> list[dict[str, Any]]:
    found: dict[int, dict[str, Any]] = {}

    def add(pr: Mapping[str, Any] | None) -> None:
        if not pr or not isinstance(pr.get("number"), int):
            return
        number = int(pr["number"])
        found[number] = {
            "number": number,
            "state": pr_state(pr),
            "url": pr.get("url") or "",
        }

    closed_by = (node.get("closedByPullRequestsReferences") or {}).get("nodes") or []
    for pr in closed_by:
        add(pr)
    for item in (node.get("timelineItems") or {}).get("nodes") or []:
        source = (item or {}).get("source") or {}
        if source.get("__typename") == "PullRequest":
            add(source)
    return [found[number] for number in sorted(found)]


def _label_names(raw: Any) -> list[str]:
    if isinstance(raw, dict):
        nodes = raw.get("nodes") or []
    elif isinstance(raw, list):
        nodes = raw
    else:
        return []
    names: list[str] = []
    for item in nodes:
        if isinstance(item, str) and item:
            names.append(item)
        elif isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return names


def parse_issue_node(node: Mapping[str, Any]) -> dict[str, Any]:
    reason = node.get("stateReason", node.get("state_reason"))
    if isinstance(reason, str):
        reason = reason.lower()
    pulls = node.get("pull_requests")
    if "closedByPullRequestsReferences" in node or "timelineItems" in node:
        pulls = extract_pull_requests(node)
    return {
        "number": int(node["number"]),
        "title": node.get("title") or "",
        "body": node.get("body") or "",
        "state": str(node.get("state") or "").lower(),
        "state_reason": reason,
        "closed_at": node.get("closedAt", node.get("closed_at")),
        "url": node.get("url") or "",
        "labels": _label_names(node.get("labels")),
        "pull_requests": list(pulls or []),
    }


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _in_done_window(closed_at: Any, now: datetime) -> bool:
    closed = _parse_time(closed_at)
    if closed is None:
        return False
    return now - closed <= DONE_WINDOW


def classify(issue: Mapping[str, Any], *, now: datetime) -> dict[str, Any] | None:
    labels = list(issue.get("labels") or [])
    label_set = set(labels)
    status_labels = [name for name in labels if name.startswith("status:")]
    conflict = len(status_labels) > 1
    state = str(issue.get("state") or "").lower()
    reason = str(issue.get("state_reason") or "").lower()

    if state == "closed" and reason == "not_planned":
        return None

    if state == "closed":
        recent = _in_done_window(issue.get("closed_at"), now)
        completed = reason == "completed" and recent
        marked_done = "status:done" in label_set and recent
        if not (completed or marked_done):
            return None
        column = "done"
    else:
        column = "backlog"
        for name in STATUS_PRECEDENCE:
            if name in label_set:
                column = COLUMN_FOR_LABEL[name]
                break

    return {
        "number": int(issue["number"]),
        "title": issue.get("title") or "",
        "url": issue.get("url") or "",
        "column": column,
        "conflict": conflict,
        "badges": badges_for(label_set, conflict),
        "worker": worker_name(issue),
        "chips": chips_for(labels),
        "pullRequests": list(issue.get("pull_requests") or []),
    }


def build_board(
    issues: list[Mapping[str, Any]],
    *,
    now: datetime,
    write_enabled: bool,
) -> dict[str, Any]:
    cards = []
    for issue in issues:
        card = classify(issue, now=now)
        if card is not None:
            cards.append(card)
    cards.sort(key=lambda card: card["number"], reverse=True)
    return {
        "ok": True,
        "writeEnabled": bool(write_enabled),
        "repo": DEFAULT_REPO,
        "columns": [dict(column) for column in COLUMNS],
        "cards": cards,
    }


def labels_after_move(labels: list[str], column: str) -> list[str]:
    if column == "done":
        raise ValueError("Done is not a drop target")
    if column not in LABEL_FOR_COLUMN:
        raise ValueError("unknown column")
    kept = [name for name in labels if not name.startswith("status:")]
    target = LABEL_FOR_COLUMN[column]
    if target:
        kept.append(target)
    return kept


def move_issue(
    number: int,
    column: str,
    client: Any,
    *,
    write_enabled: bool,
) -> dict[str, Any]:
    """Apply one status-label replacement. Read-only and Done drops call nothing."""
    if not write_enabled:
        return {"ok": False, "error": "read-only", "moved": False}
    if column == "done" or column not in LABEL_FOR_COLUMN:
        return {"ok": False, "error": "Done is not a drop target" if column == "done" else "unknown column", "moved": False}
    issue = client.get_issue(number)
    updated = labels_after_move(list(issue.get("labels") or []), column)
    try:
        client.set_labels(number, updated)
    except GitHubError as exc:
        return {"ok": False, "error": str(exc), "moved": False}
    return {"ok": True, "number": number, "column": column, "labels": updated, "moved": True}


def move_request(body: Mapping[str, Any], client: Any, *, write_enabled: bool) -> dict[str, Any]:
    """Accept only number and column. Extra keys are ignored and never forwarded."""
    if not isinstance(body, Mapping):
        return {"ok": False, "error": "expected a JSON object", "moved": False}
    number = body.get("number")
    column = body.get("column")
    if isinstance(number, str) and number.isdigit():
        number = int(number)
    if not isinstance(number, int) or isinstance(number, bool) or not isinstance(column, str):
        return {"ok": False, "error": "number and column are required", "moved": False}
    return move_issue(number, column, client, write_enabled=write_enabled)


def collect_pages(fetch_page: Callable[[str | None], Mapping[str, Any]]) -> list[Any]:
    """Walk a cursor until has_next is false. A repeated cursor stops the loop."""
    nodes: list[Any] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(MAX_PAGES):
        page = fetch_page(cursor)
        nodes.extend(page.get("nodes") or [])
        if not page.get("has_next"):
            return nodes
        end = page.get("end_cursor")
        if not end or end in seen:
            return nodes
        seen.add(str(end))
        cursor = str(end)
    return nodes


def load_board(client: Any, *, now: datetime | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    issues = [parse_issue_node(node) for node in client.list_issue_nodes()]
    return build_board(issues, now=moment, write_enabled=write_enabled(env))


class GitHubClient:
    """Talks only to api.github.com. The token is an Authorization header."""

    def __init__(
        self,
        token: str,
        owner: str = "cvolkernick",
        repo: str = "personal-workspace",
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if not token:
            raise KanbanAuthError("GitHub token is not configured")
        self._token = token
        self.owner = owner
        self.repo = repo
        self._opener = opener or urllib.request.urlopen

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "GitHubClient":
        return cls(resolve_token(env))

    def list_issue_nodes(self) -> list[Any]:
        owner, repo = self.owner, self.repo

        def fetch(cursor: str | None) -> Mapping[str, Any]:
            payload = self.graphql(
                ISSUES_QUERY,
                {"cursor": cursor, "owner": owner, "name": repo},
            )
            block = payload["data"]["repository"]["issues"]
            page = block["pageInfo"]
            return {
                "nodes": block.get("nodes") or [],
                "has_next": bool(page.get("hasNextPage")),
                "end_cursor": page.get("endCursor"),
            }

        return collect_pages(fetch)

    def get_issue(self, number: int) -> dict[str, Any]:
        payload = self._json(
            "GET",
            f"/repos/{self.owner}/{self.repo}/issues/{number}",
        )
        return parse_issue_node(payload)

    def set_labels(self, number: int, labels: list[str]) -> None:
        self._json(
            "PATCH",
            f"/repos/{self.owner}/{self.repo}/issues/{number}",
            {"labels": list(labels)},
        )

    def graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        return self._json("POST", "/graphql", {"query": query, "variables": dict(variables)})

    def _json(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not path.startswith("/"):
            raise GitHubError("refusing a non-api path")
        url = API_HOST + path
        if urllib.parse.urlsplit(url).netloc != "api.github.com":
            raise GitHubError("refusing a non-github host")
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "personal-workspace-eng-kanban",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener(req, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise GitHubError(f"GitHub {exc.code}") from None
        except urllib.error.URLError:
            raise GitHubError("GitHub network error") from None
        if not raw:
            return {}
        parsed = json.loads(raw.decode("utf-8"))
        if isinstance(parsed, dict) and parsed.get("errors") and "data" not in parsed:
            raise GitHubError("GitHub graphql error")
        return parsed
