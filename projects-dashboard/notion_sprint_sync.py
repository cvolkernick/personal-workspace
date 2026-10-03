#!/usr/bin/env python3
"""Mirror cvolkernick/personal-workspace issues onto the Notion Eng Sprint Board.

Notion stays the source of truth for Sprint, Priority, Rank, Chris gate,
and Notes. GitHub refreshes Name and Owner, fills an empty Issue # and
GitHub Issue URL, and applies a one-way status map.

New rows are created only for open issues labeled status:ready or spec.
Closed issues update an existing row (completed → Done) and are not backfilled.

Auth failures exit 2 and print NOTION_SPRINT_AUTH_ERROR. ntfy is retired
(#704); the oneshot failure is the loud signal. The Notion token is
NOTION_SPRINT_TOKEN and is never read from the repo.

The Pi timer is :00/:15/:30/:45 so it does not land on the FCC restart
minutes (:x1:18 and :x6:18 America/New_York). A manual start in that
window exits 0 without calling the APIs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
NOTION_VERSION = "2025-09-03"
DEFAULT_DATA_SOURCE = "856a80c1-0191-492c-9d62-b5ab71ed3cc5"
DEFAULT_REPO = "cvolkernick/personal-workspace"
DEFAULT_PROJECT_OWNER = "cvolkernick"
DEFAULT_PROJECT_NUMBER = 1

STATUS_BACKLOG = "Backlog"
STATUS_SPRINT = "Sprint"
STATUS_IN_PROGRESS = "In Progress"
STATUS_IN_REVIEW = "In Review"
STATUS_DONE = "Done"
STATUS_BLOCKED = "Blocked"
MAPPED_STATUSES = frozenset(
    {STATUS_IN_PROGRESS, STATUS_IN_REVIEW, STATUS_DONE, STATUS_BLOCKED}
)
HELD_STATUSES = frozenset({STATUS_SPRINT, STATUS_BACKLOG})
CREATE_LABELS = frozenset({"status:ready", "spec"})
PROTECTED = ("Sprint", "Priority", "Rank", "Chris gate", "Notes")
PROTECTED_FOLDED = frozenset(name.casefold() for name in PROTECTED)

REQUIRED_FIELDS = ("Name", "Issue #", "Status")
OPTIONAL_FIELDS = ("Owner", "GitHub Issue URL")

_WORKER_RE = re.compile(r"^\s*\*\*Worker:\*\*\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_ISSUE_URL_RE = re.compile(r"/issues/(\d+)\b")
_ISSUE_REF_RE = re.compile(r"(?:^|[^A-Za-z0-9])#(\d+)\b")
_NAME_NUM_RE = re.compile(r"^\s*#(\d+)\b")


class AuthError(RuntimeError):
    def __init__(self, source: str, status: int | None, detail: str) -> None:
        self.source = source
        self.status = status
        self.detail = detail
        super().__init__(f"{source} auth {status}: {detail}")


@dataclass
class GhIssue:
    number: int
    title: str
    body: str
    state: str
    state_reason: str | None
    labels: set[str]
    url: str
    open_pr: bool = False
    buzz_assignee: bool = False


@dataclass
class BoardRow:
    page_id: str
    properties: dict[str, Any]


@dataclass
class Action:
    op: str
    issue_number: int
    page_id: str | None
    properties: dict[str, Any]


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unchanged: int = 0


def parse_worker(body: str | None) -> str | None:
    """Return the name on the first **Worker:** line, without a trailing sentence."""
    if not body:
        return None
    match = _WORKER_RE.search(body)
    if not match:
        return None
    raw = match.group(1).strip().split(".", 1)[0].strip()
    raw = raw.strip("*_` ").strip()
    return raw or None


def in_fcc_restart_window(now: datetime) -> bool:
    """True at minute 18 of hours ending in 1 or 6, America/New_York."""
    if now.tzinfo is None:
        local = now.replace(tzinfo=ET)
    else:
        local = now.astimezone(ET)
    return local.minute == 18 and local.hour % 10 in (1, 6)


def desired_status(issue: GhIssue) -> str | None:
    """GitHub → Notion status. None means leave the current status alone."""
    reason = (issue.state_reason or "").lower()
    if issue.state.lower() == "closed" and reason == "completed":
        return STATUS_DONE
    if "blocked" in issue.labels:
        return STATUS_BLOCKED
    if issue.open_pr:
        return STATUS_IN_REVIEW
    if "status:in-progress" in issue.labels or issue.buzz_assignee:
        return STATUS_IN_PROGRESS
    return None


def next_status(current: str | None, desired: str | None, *, creating: bool) -> str | None:
    """Apply the one-way map. Sprint and Backlog only leave for a mapped status."""
    if creating:
        if desired in MAPPED_STATUSES:
            return desired
        return STATUS_BACKLOG
    cur = current or ""
    if not cur:
        if desired in MAPPED_STATUSES:
            return desired
        return STATUS_BACKLOG
    if desired is None:
        return None
    if cur in HELD_STATUSES:
        if desired in MAPPED_STATUSES and desired != cur:
            return desired
        return None
    if desired != cur:
        return desired
    return None


def eligible_to_create(issue: GhIssue) -> bool:
    return issue.state.lower() == "open" and bool(CREATE_LABELS & set(issue.labels))


def _chunks(value: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": str(value)[:2000]}}]


def read_plain(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    kind = prop.get("type")
    if kind == "title":
        parts = prop.get("title") or []
    elif kind == "rich_text":
        parts = prop.get("rich_text") or []
    elif kind == "select":
        return ((prop.get("select") or {}) or {}).get("name") or ""
    elif kind == "status":
        return ((prop.get("status") or {}) or {}).get("name") or ""
    elif kind == "url":
        return prop.get("url") or ""
    elif kind == "number" and prop.get("number") is not None:
        number = prop.get("number")
        if isinstance(number, float) and number.is_integer():
            return str(int(number))
        return str(number)
    else:
        return ""
    return "".join(str(part.get("plain_text") or "") for part in parts).strip()


def property_issue_number(properties: dict[str, Any]) -> int | None:
    """Return the Issue # property only. Empty means the field still needs a fill."""
    prop = _prop(properties, "Issue #")
    if not prop:
        return None
    if prop.get("type") == "number" and isinstance(prop.get("number"), (int, float)):
        return int(prop["number"])
    text = read_plain(prop).lstrip("#").strip()
    if text.isdigit():
        return int(text)
    return None


def read_issue_number(properties: dict[str, Any]) -> int | None:
    """Match a row on Issue #, else the GitHub URL, else a leading #N title."""
    numbered = property_issue_number(properties)
    if numbered is not None:
        return numbered
    url_prop = _prop(properties, "GitHub Issue URL")
    url_match = _ISSUE_URL_RE.search(read_plain(url_prop))
    if url_match:
        return int(url_match.group(1))
    name_match = _NAME_NUM_RE.match(read_plain(_prop(properties, "Name")))
    if name_match:
        return int(name_match.group(1))
    return None


def _prop(properties: dict[str, Any], canonical: str) -> dict[str, Any] | None:
    wanted = canonical.casefold()
    for key, value in properties.items():
        if key.casefold() == wanted and isinstance(value, dict):
            return value
    return None


def index_schema(schema: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    indexed: dict[str, tuple[str, dict[str, Any]]] = {}
    for name, spec in schema.items():
        if isinstance(spec, dict):
            indexed[name.casefold()] = (name, spec)
    return indexed


def _require_field(
    indexed: dict[str, tuple[str, dict[str, Any]]], canonical: str
) -> tuple[str, dict[str, Any]]:
    found = indexed.get(canonical.casefold())
    if not found:
        raise RuntimeError(f"Notion schema missing {canonical}")
    return found


def write_value(spec: dict[str, Any], value: Any) -> dict[str, Any] | None:
    kind = spec.get("type")
    if kind == "title":
        return {"title": _chunks(str(value))}
    if kind == "rich_text":
        return {"rich_text": _chunks(str(value))}
    if kind == "number":
        return {"number": int(value)}
    if kind == "url":
        return {"url": str(value) or None}
    if kind in ("select", "status"):
        name = str(value)
        options = ((spec.get(kind) or {}).get("options")) or []
        if options and not any(opt.get("name") == name for opt in options):
            return None
        return {kind: {"name": name}}
    return None


def materialize_properties(
    schema: dict[str, Any], writes: dict[str, Any]
) -> dict[str, Any]:
    """Turn a Notion write payload back into the read shape tests compare."""
    indexed = index_schema(schema)
    stored: dict[str, Any] = {}
    for key, payload in writes.items():
        spec = indexed[key.casefold()][1]
        kind = spec.get("type")
        if kind == "title":
            text = payload["title"][0]["text"]["content"]
            stored[key] = {"type": "title", "title": [{"plain_text": text}]}
        elif kind == "rich_text":
            text = payload["rich_text"][0]["text"]["content"]
            stored[key] = {"type": "rich_text", "rich_text": [{"plain_text": text}]}
        elif kind == "number":
            stored[key] = {"type": "number", "number": payload["number"]}
        elif kind == "url":
            stored[key] = {"type": "url", "url": payload["url"]}
        elif kind == "select":
            stored[key] = {"type": "select", "select": {"name": payload["select"]["name"]}}
        elif kind == "status":
            stored[key] = {"type": "status", "status": {"name": payload["status"]["name"]}}
    return stored


def _drop_protected(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in properties.items()
        if key.casefold() not in PROTECTED_FOLDED
    }


def changes_for(
    issue: GhIssue,
    row: BoardRow | None,
    schema: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    indexed = index_schema(schema)
    existing = row.properties if row else {}
    creating = row is None
    out: dict[str, Any] = {}

    name_key, name_spec = _require_field(indexed, "Name")
    current_name = read_plain(_prop(existing, "Name"))
    title = issue.title or f"#{issue.number}"
    if creating or (title and title != current_name):
        written = write_value(name_spec, title)
        if written:
            out[name_key] = written

    owner = indexed.get("owner")
    if owner:
        owner_key, owner_spec = owner
        worker = parse_worker(issue.body)
        current_owner = read_plain(_prop(existing, "Owner"))
        if worker and worker != current_owner:
            written = write_value(owner_spec, worker)
            if written is None:
                warnings.append(
                    f"#{issue.number} owner {worker!r} is not a Notion option"
                )
            else:
                out[owner_key] = written

    issue_key, issue_spec = _require_field(indexed, "Issue #")
    current_number = property_issue_number(existing) if existing else None
    if creating or current_number is None:
        written = write_value(issue_spec, issue.number)
        if written is None:
            warnings.append(f"#{issue.number} Issue # cannot be written")
            if creating:
                return {}
        else:
            out[issue_key] = written

    url_field = indexed.get("github issue url")
    if url_field and issue.url:
        url_key, url_spec = url_field
        if not read_plain(_prop(existing, "GitHub Issue URL")):
            written = write_value(url_spec, issue.url)
            if written:
                out[url_key] = written

    status_key, status_spec = _require_field(indexed, "Status")
    current_status = read_plain(_prop(existing, "Status")) or None
    nxt = next_status(current_status, desired_status(issue), creating=creating)
    if nxt and nxt != (current_status or ""):
        written = write_value(status_spec, nxt)
        if written is None:
            warnings.append(f"#{issue.number} status {nxt!r} is not a Notion option")
            if creating:
                return {}
        else:
            out[status_key] = written
    elif creating and status_key not in out:
        warnings.append(f"#{issue.number} has no status to create")
        return {}
    return _drop_protected(out)


def plan_sync(
    issues: Iterable[GhIssue], rows: Iterable[BoardRow], schema: dict[str, Any]
) -> Plan:
    plan = Plan()
    grouped: dict[int, list[BoardRow]] = {}
    unkeyed = 0
    for row in rows:
        number = read_issue_number(row.properties)
        if number is None:
            unkeyed += 1
            continue
        grouped.setdefault(number, []).append(row)
    if unkeyed:
        plan.warnings.append(f"{unkeyed} board rows have no Issue #")

    seen: set[int] = set()
    for issue in issues:
        if issue.number in seen:
            plan.warnings.append(f"duplicate GitHub issue #{issue.number}")
            continue
        seen.add(issue.number)
        matches = grouped.get(issue.number) or []
        if len(matches) > 1:
            plan.warnings.append(
                f"issue #{issue.number} matches {len(matches)} board rows"
            )
        if not matches:
            if not eligible_to_create(issue):
                plan.unchanged += 1
                continue
            properties = changes_for(issue, None, schema, plan.warnings)
            if not properties:
                plan.unchanged += 1
                continue
            plan.actions.append(
                Action("create", issue.number, None, properties)
            )
            continue
        wrote = False
        for row in matches:
            properties = changes_for(issue, row, schema, plan.warnings)
            if not properties:
                continue
            wrote = True
            plan.actions.append(
                Action("update", issue.number, row.page_id, properties)
            )
        if not wrote:
            plan.unchanged += 1
    return plan


def _safe_detail(text: str, secret: str) -> str:
    if secret and secret in text:
        text = text.replace(secret, "[redacted]")
    return " ".join(text.split())[:300]


def _retry_after(headers: Any) -> float:
    raw = ""
    if headers is not None:
        raw = headers.get("Retry-After") or ""
    try:
        wait = float(raw) if raw else 1.0
    except ValueError:
        wait = 1.0
    return max(0.0, min(wait, 30.0))


def _request(
    method: str,
    url: str,
    *,
    token: str,
    source: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as err:
            detail = _safe_detail(err.read().decode("utf-8", errors="replace"), token)
            remaining = err.headers.get("X-RateLimit-Remaining") if err.headers else None
            rate_limited = err.code == 429 or (err.code == 403 and remaining == "0")
            if rate_limited and attempt < 3:
                time.sleep(_retry_after(err.headers))
                last_error = err
                continue
            if err.code in (401, 403):
                raise AuthError(source, err.code, detail or err.reason) from err
            raise RuntimeError(f"{source} HTTP {err.code}: {detail or err.reason}") from err
        except urllib.error.URLError as err:
            raise RuntimeError(f"{source} network error: {err.reason}") from err
        except TimeoutError as err:
            raise RuntimeError(f"{source} network error: {err}") from err
    raise RuntimeError(f"{source} rate limited") from last_error


def _notion_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
        "User-Agent": "notion-sprint-sync",
    }


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "notion-sprint-sync",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def normalize_data_source_id(raw: str) -> str:
    text = (raw or "").strip()
    prefix = "collection://"
    if text.startswith(prefix):
        text = text[len(prefix) :]
    return text


def read_gh_auth_token() -> str | None:
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    token = (proc.stdout or "").strip()
    return token or None


def resolve_github_token(env: dict[str, str]) -> str:
    """Prefer an explicit sprint token, then `gh auth token`, then GITHUB_TOKEN.

    ~/.config/workflow-scheduler.env is a known 401 on the Pi. A rejected
    candidate is skipped. If every candidate is rejected, that is an auth error.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def add(token: str | None) -> None:
        text = (token or "").strip()
        if text and text not in seen:
            seen.add(text)
            candidates.append(text)

    add(env.get("NOTION_SPRINT_GITHUB_TOKEN"))
    add(read_gh_auth_token())
    add(env.get("GITHUB_TOKEN"))
    add(env.get("GH_TOKEN"))
    if not candidates:
        raise AuthError("github", None, "no GitHub token")
    last: AuthError | None = None
    for token in candidates:
        try:
            _request(
                "GET",
                "https://api.github.com/user",
                token=token,
                source="github",
                headers=_github_headers(token),
            )
            return token
        except AuthError as err:
            last = err
            continue
    assert last is not None
    raise last


class NotionClient:
    def __init__(self, token: str, data_source_id: str) -> None:
        self.token = token
        self.data_source_id = normalize_data_source_id(data_source_id)

    def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return _request(
            method,
            "https://api.notion.com" + path,
            token=self.token,
            source="notion",
            headers=_notion_headers(self.token),
            body=body,
        )

    def schema(self) -> dict[str, Any]:
        payload = self._call("GET", f"/v1/data_sources/{self.data_source_id}")
        props = payload.get("properties")
        if not isinstance(props, dict):
            raise RuntimeError("Notion data source has no properties")
        return props

    def query_rows(self) -> list[BoardRow]:
        rows: list[BoardRow] = []
        cursor: str | None = None
        for _ in range(50):
            body: dict[str, Any] = {"page_size": 100, "result_type": "page"}
            if cursor:
                body["start_cursor"] = cursor
            payload = self._call(
                "POST", f"/v1/data_sources/{self.data_source_id}/query", body
            )
            for item in payload.get("results") or []:
                if item.get("object") != "page":
                    continue
                props = item.get("properties") or {}
                page_id = item.get("id")
                if page_id and isinstance(props, dict):
                    rows.append(BoardRow(str(page_id), props))
            if not payload.get("has_more"):
                return rows
            cursor = payload.get("next_cursor")
            if not cursor:
                break
        raise RuntimeError("Notion query truncated")

    def create(self, properties: dict[str, Any]) -> str:
        payload = self._call(
            "POST",
            "/v1/pages",
            {
                "parent": {
                    "type": "data_source_id",
                    "data_source_id": self.data_source_id,
                },
                "properties": properties,
            },
        )
        page_id = payload.get("id")
        if not page_id:
            raise RuntimeError("Notion create returned no page id")
        return str(page_id)

    def update(self, page_id: str, properties: dict[str, Any]) -> None:
        self._call("PATCH", f"/v1/pages/{page_id}", {"properties": properties})


class GithubClient:
    def __init__(self, token: str, repo: str, project_owner: str, project_number: int) -> None:
        self.token = token
        owner, _, name = repo.partition("/")
        if not owner or not name:
            raise RuntimeError(f"invalid repo {repo!r}")
        self.owner = owner
        self.name = name
        self.project_owner = project_owner
        self.project_number = project_number

    def _rest(self, url: str) -> dict[str, Any] | list[Any]:
        payload = _request(
            "GET",
            url,
            token=self.token,
            source="github",
            headers=_github_headers(self.token),
        )
        return payload

    def _gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = _request(
            "POST",
            "https://api.github.com/graphql",
            token=self.token,
            source="github",
            headers=_github_headers(self.token),
            body={"query": query, "variables": variables},
        )
        errors = payload.get("errors") or []
        if errors:
            message = "; ".join(str(err.get("message") or err) for err in errors[:4])
            folded = message.lower()
            if "resource not accessible" in folded or "credentials" in folded or "unauthorized" in folded:
                raise AuthError("github", 403, message)
            raise RuntimeError(f"GitHub GraphQL: {message}")
        return payload.get("data") or {}

    def _issue_from_rest(self, item: dict[str, Any]) -> GhIssue | None:
        if "pull_request" in item:
            return None
        number = item.get("number")
        if not isinstance(number, int):
            return None
        labels = {
            str(label.get("name"))
            for label in (item.get("labels") or [])
            if isinstance(label, dict) and label.get("name")
        }
        return GhIssue(
            number=number,
            title=str(item.get("title") or ""),
            body=str(item.get("body") or ""),
            state=str(item.get("state") or ""),
            state_reason=item.get("state_reason"),
            labels=labels,
            url=str(item.get("html_url") or f"https://github.com/{self.owner}/{self.name}/issues/{number}"),
        )

    def _search_open_candidates(self) -> dict[int, GhIssue]:
        found: dict[int, GhIssue] = {}
        query = (
            f"repo:{self.owner}/{self.name} is:issue is:open "
            '(label:spec OR label:"status:ready")'
        )
        for page in range(1, 11):
            params = urllib.parse.urlencode(
                {"q": query, "per_page": "100", "page": str(page)}
            )
            payload = self._rest(f"https://api.github.com/search/issues?{params}")
            if not isinstance(payload, dict):
                break
            items = payload.get("items") or []
            for item in items:
                issue = self._issue_from_rest(item)
                if issue:
                    found[issue.number] = issue
            if len(items) < 100:
                break
        return found

    def _fetch_numbers(self, numbers: Iterable[int]) -> dict[int, GhIssue]:
        found: dict[int, GhIssue] = {}
        for number in numbers:
            try:
                payload = self._rest(
                    f"https://api.github.com/repos/{self.owner}/{self.name}/issues/{number}"
                )
            except RuntimeError as err:
                if "HTTP 404" in str(err):
                    continue
                raise
            if isinstance(payload, dict):
                issue = self._issue_from_rest(payload)
                if issue:
                    found[issue.number] = issue
        return found

    def _open_pr_numbers(self) -> set[int]:
        linked: set[int] = set()
        query = """
        query($owner:String!, $name:String!, $cursor:String) {
          repository(owner:$owner, name:$name) {
            pullRequests(states: OPEN, first: 50, after: $cursor) {
              pageInfo { hasNextPage endCursor }
              nodes {
                title
                body
                closingIssuesReferences(first: 20) { nodes { number } }
              }
            }
          }
        }
        """
        cursor: str | None = None
        for _ in range(10):
            data = self._gql(
                query,
                {"owner": self.owner, "name": self.name, "cursor": cursor},
            )
            conn = ((data.get("repository") or {}).get("pullRequests")) or {}
            for node in conn.get("nodes") or []:
                for ref in ((node.get("closingIssuesReferences") or {}).get("nodes")) or []:
                    number = ref.get("number")
                    if isinstance(number, int):
                        linked.add(number)
                text = f"{node.get('title') or ''}\n{node.get('body') or ''}"
                for match in _ISSUE_REF_RE.finditer(text):
                    linked.add(int(match.group(1)))
            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
            if not cursor:
                break
        return linked

    def _buzz_assignees(self) -> set[int]:
        data = self._gql(
            """
            query($login:String!, $number:Int!) {
              user(login:$login) {
                projectV2(number:$number) { id }
              }
            }
            """,
            {"login": self.project_owner, "number": self.project_number},
        )
        project = ((data.get("user") or {}).get("projectV2")) or {}
        project_id = project.get("id")
        if not project_id:
            raise RuntimeError(
                f"Buzz board project not found: {self.project_owner} #{self.project_number}"
            )
        query = """
        query($id:ID!, $cursor:String) {
          node(id:$id) {
            ... on ProjectV2 {
              items(first: 50, after: $cursor) {
                pageInfo { hasNextPage endCursor }
                nodes {
                  content {
                    __typename
                    ... on Issue {
                      number
                      repository { nameWithOwner }
                      assignees(first: 10) { nodes { login } }
                    }
                  }
                }
              }
            }
          }
        }
        """
        assigned: set[int] = set()
        cursor = None
        repo_name = f"{self.owner}/{self.name}"
        for _ in range(20):
            payload = self._gql(query, {"id": project_id, "cursor": cursor})
            conn = ((payload.get("node") or {}).get("items")) or {}
            for node in conn.get("nodes") or []:
                content = node.get("content") or {}
                if content.get("__typename") != "Issue":
                    continue
                repo = ((content.get("repository") or {}).get("nameWithOwner")) or ""
                if repo != repo_name:
                    continue
                assignees = ((content.get("assignees") or {}).get("nodes")) or []
                number = content.get("number")
                if assignees and isinstance(number, int):
                    assigned.add(number)
            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return assigned
            cursor = page.get("endCursor")
            if not cursor:
                break
        raise RuntimeError("Buzz board assignee query truncated")

    def fetch(self, board_numbers: Iterable[int]) -> list[GhIssue]:
        issues = self._search_open_candidates()
        missing = [number for number in board_numbers if number not in issues]
        issues.update(self._fetch_numbers(missing))
        linked = self._open_pr_numbers()
        assigned = self._buzz_assignees()
        for issue in issues.values():
            issue.open_pr = issue.number in linked
            issue.buzz_assignee = issue.number in assigned
        return list(issues.values())


def default_notify(message: str) -> None:
    print(message, file=sys.stderr)


def _summary(
    plan: Plan,
    *,
    created: int,
    updated: int,
    dry_run: bool,
    errors: list[str],
    skipped: str | None = None,
) -> dict[str, Any]:
    warnings = list(plan.warnings)
    warnings.extend(errors)
    return {
        "ok": not errors,
        "skipped": skipped,
        "dry_run": dry_run,
        "created": created,
        "updated": updated,
        "unchanged": plan.unchanged,
        "warnings": warnings,
    }


def run_sync(
    *,
    env: dict[str, str],
    now: datetime | None = None,
    github: Any | None = None,
    notion: Any | None = None,
    notify: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> tuple[int, dict[str, Any]]:
    moment = now or datetime.now(ET)
    empty = Plan()
    if in_fcc_restart_window(moment):
        return 0, _summary(empty, created=0, updated=0, dry_run=dry_run, errors=[], skipped="fcc_restart_window")
    token = (env.get("NOTION_SPRINT_TOKEN") or "").strip()
    if not token:
        return 0, _summary(empty, created=0, updated=0, dry_run=dry_run, errors=[], skipped="not_configured")
    notify = notify or default_notify
    try:
        if notion is None:
            data_source = env.get("NOTION_SPRINT_DATA_SOURCE_ID") or DEFAULT_DATA_SOURCE
            notion = NotionClient(token, data_source)
        schema = notion.schema()
        rows = notion.query_rows()
        numbers = []
        for row in rows:
            number = read_issue_number(row.properties)
            if number is not None:
                numbers.append(number)
        if github is None:
            repo = env.get("NOTION_SPRINT_REPO") or DEFAULT_REPO
            owner = env.get("BUZZ_BOARD_OWNER") or DEFAULT_PROJECT_OWNER
            try:
                project_number = int(env.get("BUZZ_BOARD_PROJECT_NUMBER") or DEFAULT_PROJECT_NUMBER)
            except ValueError:
                project_number = DEFAULT_PROJECT_NUMBER
            github = GithubClient(resolve_github_token(env), repo, owner, project_number)
        issues = github.fetch(numbers)
        plan = plan_sync(issues, rows, schema)
        fetched = {issue.number for issue in issues}
        for number in numbers:
            if number not in fetched:
                plan.warnings.append(f"issue #{number} is on the board but missing from GitHub")
        if dry_run:
            return 0, _summary(plan, created=len([a for a in plan.actions if a.op == "create"]), updated=len([a for a in plan.actions if a.op == "update"]), dry_run=True, errors=[])
        created = 0
        updated = 0
        errors: list[str] = []
        for action in plan.actions:
            try:
                if action.op == "create":
                    notion.create(action.properties)
                    created += 1
                else:
                    notion.update(action.page_id, action.properties)
                    updated += 1
            except AuthError:
                raise
            except Exception as err:  # noqa: BLE001 — one row must not abort the rest
                errors.append(f"#{action.issue_number} {action.op}: {err}")
        code = 1 if errors else 0
        return code, _summary(plan, created=created, updated=updated, dry_run=False, errors=errors)
    except AuthError as err:
        notify(f"NOTION_SPRINT_AUTH_ERROR source={err.source} status={err.status}")
        return 2, {
            "ok": False,
            "error": "auth",
            "source": err.source,
            "status": err.status,
            "skipped": None,
            "dry_run": dry_run,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "warnings": [],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync GitHub issues to the Notion Eng Sprint Board")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read both sides and print the plan without writing to Notion",
    )
    args = parser.parse_args(argv)
    code, summary = run_sync(env=os.environ.copy(), dry_run=args.dry_run)
    print(json.dumps(summary, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
