#!/usr/bin/env python3
"""Buzz Board (GitHub Project) adapter for the Workflow Management Sprint tab.

Board of record: GitHub user project "Buzz Board" (default project #1 under
GITHUB_OWNER / BUZZ_BOARD_OWNER). Status field options map to Cadence columns:

  Parked → Validate ($0) → Ready → In Progress → Done

Auth: GITHUB_TOKEN (or GH_TOKEN) with `repo` + `project` scopes.
Never logs or returns the token.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_OWNER = "cvolkernick"
DEFAULT_PROJECT_NUMBER = 1
DEFAULT_WIP_LIMIT = 3
GITHUB_GRAPHQL = "https://api.github.com/graphql"

# Display order for kanban (Cadence ceremony columns)
STATUS_ORDER = [
    "Parked",
    "Validate ($0)",
    "Ready",
    "In Progress",
    "Done",
]

# Ceremony schedule (UTC cron strings — same as GUIDES/CADENCE_SCRUM_CEREMONIES.md)
DEFAULT_CEREMONIES = [
    {
        "id": "grooming",
        "name": "Backlog Grooming",
        "cron": "0 16 * * 3",
        "when_label": "Wed 16:00 (server clock)",
        "owner": "Cadence",
        "workflow_id": "95d911df-509b-4eac-a4f5-ffeaa4c1e3da",
    },
    {
        "id": "sprint_planning",
        "name": "Sprint Planning",
        "cron": "0 16 * * 1",
        "when_label": "Mon 16:00 (server clock)",
        "owner": "Cadence",
        "workflow_id": "b85c12fa-e7e5-43b3-8292-295a1e9f9783",
    },
]


def _token() -> str | None:
    return (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("BUZZ_BOARD_GITHUB_TOKEN")
        or None
    )


def _owner() -> str:
    return (
        os.environ.get("BUZZ_BOARD_OWNER")
        or os.environ.get("GITHUB_OWNER")
        or DEFAULT_OWNER
    )


def _project_number() -> int:
    raw = os.environ.get("BUZZ_BOARD_PROJECT_NUMBER") or str(DEFAULT_PROJECT_NUMBER)
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PROJECT_NUMBER


def _wip_limit() -> int:
    raw = os.environ.get("BUZZ_BOARD_WIP_LIMIT") or str(DEFAULT_WIP_LIMIT)
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_WIP_LIMIT


def _gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    token = _token()
    if not token:
        raise RuntimeError(
            "Missing GITHUB_TOKEN (or GH_TOKEN) with repo + project scopes"
        )
    payload = json.dumps({"query": query, "variables": variables or {}}).encode(
        "utf-8"
    )
    req = urllib.request.Request(
        GITHUB_GRAPHQL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "projects-dashboard-sprint-board",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"GitHub GraphQL HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitHub GraphQL network error: {e}") from e
    if out.get("errors"):
        msgs = "; ".join(
            str(err.get("message") or err) for err in out["errors"][:5]
        )
        raise RuntimeError(f"GitHub GraphQL: {msgs}")
    return out


def credentials_status() -> dict[str, Any]:
    token = _token()
    return {
        "ok": bool(token),
        "token_present": bool(token),
        "owner": _owner(),
        "project_number": _project_number(),
        "hint": None
        if token
        else "Set GITHUB_TOKEN with repo + project scopes for Sprint tab",
    }


def _resolve_project() -> dict[str, Any]:
    """Return project id, title, url, status field metadata."""
    owner = _owner()
    number = _project_number()
    q = """
    query($login:String!, $number:Int!) {
      user(login:$login) {
        projectV2(number:$number) {
          id
          title
          number
          url
          fields(first:40) {
            nodes {
              ... on ProjectV2FieldCommon { id name dataType }
              ... on ProjectV2SingleSelectField {
                id
                name
                dataType
                options { id name }
              }
            }
          }
        }
      }
    }
    """
    data = _gql(q, {"login": owner, "number": number})
    project = (data.get("data") or {}).get("user", {}).get("projectV2")
    if not project:
        raise RuntimeError(
            f"Project not found: user/{owner} project #{number} "
            "(check BUZZ_BOARD_OWNER / BUZZ_BOARD_PROJECT_NUMBER)"
        )
    status_field = None
    for node in (project.get("fields") or {}).get("nodes") or []:
        if (node.get("name") or "").lower() == "status":
            status_field = node
            break
    options = []
    if status_field:
        options = [
            {"id": o["id"], "name": o["name"]}
            for o in (status_field.get("options") or [])
            if o.get("id") and o.get("name")
        ]
    return {
        "id": project["id"],
        "title": project.get("title") or "Buzz Board",
        "number": project.get("number") or number,
        "url": project.get("url")
        or f"https://github.com/users/{owner}/projects/{number}",
        "status_field_id": (status_field or {}).get("id"),
        "status_options": options,
    }


def _fetch_items(project_id: str, first: int = 100) -> list[dict[str, Any]]:
    q = """
    query($id:ID!, $first:Int!, $after:String) {
      node(id:$id) {
        ... on ProjectV2 {
          items(first:$first, after:$after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              fieldValueByName(name:"Status") {
                ... on ProjectV2ItemFieldSingleSelectValue {
                  name
                  optionId
                }
              }
              content {
                __typename
                ... on Issue {
                  number
                  title
                  state
                  url
                  body
                  updatedAt
                  labels(first:8) { nodes { name color } }
                  repository { nameWithOwner }
                }
                ... on DraftIssue {
                  title
                  body
                }
                ... on PullRequest {
                  number
                  title
                  state
                  url
                  updatedAt
                  repository { nameWithOwner }
                }
              }
            }
          }
        }
      }
    }
    """
    items: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        data = _gql(
            q, {"id": project_id, "first": min(first, 50), "after": after}
        )
        conn = ((data.get("data") or {}).get("node") or {}).get("items") or {}
        for node in conn.get("nodes") or []:
            items.append(node)
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
        if not after:
            break
        if len(items) >= first:
            break
    return items


def _normalize_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    content = raw.get("content") or {}
    if not content:
        return None
    typename = content.get("__typename") or ""
    status_val = raw.get("fieldValueByName") or {}
    status = status_val.get("name") or "(none)"
    status_option_id = status_val.get("optionId")
    labels = []
    for lab in ((content.get("labels") or {}).get("nodes") or []):
        if lab.get("name"):
            labels.append({"name": lab["name"], "color": lab.get("color")})
    repo = (content.get("repository") or {}).get("nameWithOwner")
    number = content.get("number")
    title = content.get("title") or "(untitled)"
    url = content.get("url")
    state = content.get("state")
    updated = content.get("updatedAt")
    body = content.get("body") or ""
    # Lightweight AC / size hints from body (optional convention)
    size_hint = None
    priority_hint = None
    for line in body.splitlines()[:40]:
        low = line.strip().lower()
        if low.startswith("size:") or low.startswith("**size:**"):
            size_hint = line.split(":", 1)[-1].strip().strip("*")
        if low.startswith("priority:") or low.startswith("**priority:**"):
            priority_hint = line.split(":", 1)[-1].strip().strip("*")
    return {
        "item_id": raw.get("id"),
        "status": status,
        "status_option_id": status_option_id,
        "type": typename or "Unknown",
        "number": number,
        "title": title,
        "url": url,
        "state": state,
        "repo": repo,
        "labels": labels,
        "updated_at": updated,
        "size_hint": size_hint,
        "priority_hint": priority_hint,
        "is_draft": typename == "DraftIssue",
        "is_pr": typename == "PullRequest",
        "is_issue": typename == "Issue",
    }


def sprint_payload(*, include_done: bool = True) -> dict[str, Any]:
    """Full Sprint tab payload: columns, WIP, ceremonies, board meta."""
    auth = credentials_status()
    if not auth.get("ok"):
        return {
            "ok": False,
            "error": auth.get("hint") or "GitHub token missing",
            "auth": auth,
            "columns": {s: [] for s in STATUS_ORDER},
            "counts": {},
            "wip": {"limit": _wip_limit(), "current": 0, "over": False},
            "ceremonies": DEFAULT_CEREMONIES,
            "board": None,
        }
    try:
        project = _resolve_project()
        raw_items = _fetch_items(project["id"])
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "auth": auth,
            "columns": {s: [] for s in STATUS_ORDER},
            "counts": {},
            "wip": {"limit": _wip_limit(), "current": 0, "over": False},
            "ceremonies": DEFAULT_CEREMONIES,
            "board": None,
        }

    columns: dict[str, list[dict[str, Any]]] = {s: [] for s in STATUS_ORDER}
    columns["(none)"] = []
    normalized: list[dict[str, Any]] = []
    for raw in raw_items:
        item = _normalize_item(raw)
        if not item:
            continue
        if not include_done and item["status"] == "Done":
            continue
        st = item["status"]
        if st not in columns:
            columns[st] = []
        columns[st].append(item)
        normalized.append(item)

    # Stable sort within columns: issues by number desc, then title
    for st, lst in columns.items():
        lst.sort(
            key=lambda it: (
                -(it.get("number") or 0),
                (it.get("title") or "").lower(),
            )
        )

    counts = {st: len(lst) for st, lst in columns.items() if lst or st in STATUS_ORDER}
    wip_n = len(columns.get("In Progress") or [])
    wip_limit = _wip_limit()
    return {
        "ok": True,
        "auth": auth,
        "board": {
            "title": project["title"],
            "number": project["number"],
            "url": project["url"],
            "owner": _owner(),
            "status_field_id": project.get("status_field_id"),
            "status_options": project.get("status_options") or [],
        },
        "columns": {k: columns[k] for k in STATUS_ORDER if k in columns},
        "uncategorized": columns.get("(none)") or [],
        "counts": counts,
        "wip": {
            "limit": wip_limit,
            "current": wip_n,
            "over": wip_n > wip_limit,
            "remaining": max(0, wip_limit - wip_n),
        },
        "ceremonies": DEFAULT_CEREMONIES,
        "playbook": "GUIDES/CADENCE_SCRUM_CEREMONIES.md",
        "item_count": len(normalized),
        "poker_scale": [1, 2, 3, 5, 8],
    }


def set_item_status(
    item_id: str,
    status_name: str,
    *,
    project_id: str | None = None,
    status_field_id: str | None = None,
    option_id: str | None = None,
) -> dict[str, Any]:
    """Move a project item to a Status option by name (or option_id)."""
    try:
        project = _resolve_project() if not (project_id and status_field_id) else None
        pid = project_id or (project or {}).get("id")
        fid = status_field_id or (project or {}).get("status_field_id")
        options = (project or {}).get("status_options") or []
        if not option_id:
            want = (status_name or "").strip().lower()
            for opt in options:
                if (opt.get("name") or "").strip().lower() == want:
                    option_id = opt["id"]
                    break
        if not pid or not fid or not option_id:
            return {
                "ok": False,
                "error": f"Cannot resolve status '{status_name}' "
                f"(project={bool(pid)} field={bool(fid)} option={bool(option_id)})",
            }
        mutation = """
        mutation($projectId:ID!, $itemId:ID!, $fieldId:ID!, $optionId:String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId
            itemId: $itemId
            fieldId: $fieldId
            value: { singleSelectOptionId: $optionId }
          }) {
            projectV2Item { id }
          }
        }
        """
        _gql(
            mutation,
            {
                "projectId": pid,
                "itemId": item_id,
                "fieldId": fid,
                "optionId": option_id,
            },
        )
        return {
            "ok": True,
            "message": f"Status → {status_name}",
            "item_id": item_id,
            "status": status_name,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    print(json.dumps(sprint_payload(), indent=2)[:4000])
