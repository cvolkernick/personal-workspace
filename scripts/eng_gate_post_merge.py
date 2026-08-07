#!/usr/bin/env python3
"""Eng-gate post-merge board hygiene (issue #58).

After Grok (or human override) **merges** a PR and the linked issue is **closed**,
board Status does **not** auto-sync (ceremony lock 2026-08-06). This script is the
automation path; the single-owner manual path is still:

  scripts/buzz-board set-status N Done

Usage:
  # Mark Done for a closed issue (fails if still open unless --force-open)
  python3 scripts/eng_gate_post_merge.py --issue 58

  # From merged PR: resolve Fixes/Closes #N, require merged, then Done
  python3 scripts/eng_gate_post_merge.py --pr 47

  # Deployable residual: leave In Progress until Pi health evidence
  python3 scripts/eng_gate_post_merge.py --issue 58 --residual "await Pi health :8765"

  # Sweep closed issues stuck on Pending Review / In Progress
  python3 scripts/eng_gate_post_merge.py --sweep
  python3 scripts/eng_gate_post_merge.py --sweep --apply   # set Done for clean closes

  # Dry-run
  python3 scripts/eng_gate_post_merge.py --issue 58 --dry-run

Auth: GITHUB_TOKEN or GH_TOKEN (scopes: repo, project).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

OWNER = "cvolkernick"
REPO = "personal-workspace"
PROJECT_NUMBER = 1
PROJECT_ID = "PVT_kwHOAQX9MM4BfNqD"
STATUS_FIELD_ID = "PVTSSF_lAHOAQX9MM4BfNqDzhZiHaI"
STATUS_OPTIONS = {
    "Parked": "b6f4402a",
    "Validate ($0)": "ef8c7263",
    "Ready": "776093d7",
    "In Progress": "e9e09126",
    "Pending Review": "73462e08",
    "Done": "af0819f5",
}
ISSUE_REF_RE = re.compile(
    r"(?i)\b(?:fixes|closes|resolves|fix(?:es)?)\s+#(\d+)\b"
)
# also bare "Fixes: #N" and PR body "Closes #N"
ISSUE_HASH_RE = re.compile(r"(?i)(?:^|[\s(,])#(\d+)\b")


def token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not t:
        print("error: set GITHUB_TOKEN or GH_TOKEN (repo + project)", file=sys.stderr)
        sys.exit(2)
    return t


def rest(method: str, path: str, body: dict | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token()}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "eng-gate-post-merge",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"error: REST {method} {path} → {e.code}: {err}", file=sys.stderr)
        sys.exit(1)


def gql(query: str, variables: dict | None = None) -> dict:
    payload = {"query": query, "variables": variables or {}}
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token()}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "eng-gate-post-merge",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"error: GraphQL HTTP {e.code}: {err}", file=sys.stderr)
        sys.exit(1)
    if out.get("errors"):
        print(f"error: GraphQL: {json.dumps(out['errors'], indent=2)}", file=sys.stderr)
        sys.exit(1)
    return out["data"]


def fetch_board_items() -> list[dict]:
    items: list[dict] = []
    cursor: Optional[str] = None
    while True:
        data = gql(
            """
            query($login:String!, $n:Int!, $after:String) {
              user(login:$login) {
                projectV2(number:$n) {
                  items(first:50, after:$after) {
                    pageInfo { hasNextPage endCursor }
                    nodes {
                      id
                      fieldValueByName(name:"Status") {
                        ... on ProjectV2ItemFieldSingleSelectValue { name }
                      }
                      content {
                        __typename
                        ... on Issue {
                          number title url state closedAt
                          repository { nameWithOwner }
                        }
                        ... on PullRequest {
                          number title url state
                          repository { nameWithOwner }
                        }
                      }
                    }
                  }
                }
              }
            }
            """,
            {"login": OWNER, "n": PROJECT_NUMBER, "after": cursor},
        )
        conn = data["user"]["projectV2"]["items"]
        for node in conn["nodes"]:
            content = node.get("content") or {}
            status = None
            fv = node.get("fieldValueByName")
            if fv:
                status = fv.get("name")
            items.append(
                {
                    "item_id": node["id"],
                    "status": status,
                    "kind": content.get("__typename") or "Unknown",
                    "number": content.get("number"),
                    "title": content.get("title"),
                    "url": content.get("url"),
                    "state": content.get("state"),
                    "closed_at": content.get("closedAt"),
                    "repo": (content.get("repository") or {}).get("nameWithOwner"),
                }
            )
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return items


def find_issue_item(number: int) -> Optional[dict]:
    want = f"{OWNER}/{REPO}"
    for i in fetch_board_items():
        if (
            i.get("kind") == "Issue"
            and i.get("number") == number
            and i.get("repo") == want
        ):
            return i
    return None


def set_status(item_id: str, status_name: str) -> None:
    if status_name not in STATUS_OPTIONS:
        raise SystemExit(f"error: unknown status {status_name!r}")
    gql(
        """
        mutation($projectId:ID!, $itemId:ID!, $fieldId:ID!, $optionId:String!) {
          updateProjectV2ItemFieldValue(input:{
            projectId:$projectId
            itemId:$itemId
            fieldId:$fieldId
            value:{ singleSelectOptionId:$optionId }
          }) { projectV2Item { id } }
        }
        """,
        {
            "projectId": PROJECT_ID,
            "itemId": item_id,
            "fieldId": STATUS_FIELD_ID,
            "optionId": STATUS_OPTIONS[status_name],
        },
    )


def add_to_project(issue_node_id: str) -> str:
    data = gql(
        """
        mutation($projectId:ID!, $contentId:ID!) {
          addProjectV2ItemById(input:{projectId:$projectId, contentId:$contentId}) {
            item { id }
          }
        }
        """,
        {"projectId": PROJECT_ID, "contentId": issue_node_id},
    )
    return data["addProjectV2ItemById"]["item"]["id"]


def parse_issue_refs(*texts: str) -> list[int]:
    found: list[int] = []
    for text in texts:
        if not text:
            continue
        for m in ISSUE_REF_RE.finditer(text):
            n = int(m.group(1))
            if n not in found:
                found.append(n)
    return found


def mark_issue(
    number: int,
    *,
    residual: Optional[str] = None,
    force_open: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    issue = rest("GET", f"/repos/{OWNER}/{REPO}/issues/{number}")
    state = (issue.get("state") or "").lower()
    item = find_issue_item(number)
    item_id = item["item_id"] if item else None
    prev = item.get("status") if item else None

    if residual:
        target = "In Progress"
        reason = f"deployable residual: {residual}"
    else:
        if state != "closed" and not force_open:
            return {
                "ok": False,
                "number": number,
                "error": "issue still open — close issue after merge, or pass --force-open / --residual",
                "issue_state": state,
                "board_status": prev,
            }
        target = "Done"
        reason = "merged path complete; issue closed"

    if not item_id:
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "number": number,
                "would": f"add-to-board + {target}",
                "reason": reason,
            }
        item_id = add_to_project(issue["node_id"])

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "number": number,
            "from": prev,
            "would": target,
            "reason": reason,
            "item_id": item_id,
        }

    set_status(item_id, target)
    return {
        "ok": True,
        "number": number,
        "from": prev,
        "board_status": target,
        "reason": reason,
        "item_id": item_id,
        "issue_state": state,
        "url": issue.get("html_url"),
    }


def from_pr(
    pr_number: int,
    *,
    residual: Optional[str] = None,
    force_open: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    pr = rest("GET", f"/repos/{OWNER}/{REPO}/pulls/{pr_number}")
    if not pr.get("merged"):
        return {
            "ok": False,
            "pr": pr_number,
            "error": "PR is not merged",
            "state": pr.get("state"),
            "merged": False,
        }
    body = pr.get("body") or ""
    title = pr.get("title") or ""
    refs = parse_issue_refs(body, title)
    if not refs:
        return {
            "ok": False,
            "pr": pr_number,
            "error": "no Fixes/Closes #N in PR title/body — pass --issue N explicitly",
            "merged": True,
        }
    results = []
    for n in refs:
        results.append(
            mark_issue(
                n, residual=residual, force_open=force_open, dry_run=dry_run
            )
        )
    ok = all(r.get("ok") for r in results)
    return {"ok": ok, "pr": pr_number, "merged": True, "issues": results}


def parse_closed_at(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    # GitHub: 2026-08-06T01:20:18Z
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def sweep(
    *,
    max_hours: float = 24.0,
    apply: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Find closed issues still on Pending Review or In Progress."""
    now = datetime.now(timezone.utc)
    stuck: list[dict] = []
    for i in fetch_board_items():
        if i.get("kind") != "Issue":
            continue
        if (i.get("state") or "").upper() != "CLOSED":
            continue
        if i.get("repo") != f"{OWNER}/{REPO}":
            continue
        st = i.get("status") or ""
        if st not in ("Pending Review", "In Progress"):
            continue
        closed_at = parse_closed_at(i.get("closed_at"))
        age_h = None
        if closed_at:
            age_h = (now - closed_at).total_seconds() / 3600.0
        over = age_h is not None and age_h > max_hours
        stuck.append(
            {
                "number": i.get("number"),
                "title": i.get("title"),
                "board_status": st,
                "closed_at": i.get("closed_at"),
                "age_hours": round(age_h, 2) if age_h is not None else None,
                "over_sla": over,
                "item_id": i.get("item_id"),
                "url": i.get("url"),
            }
        )

    actions: list[dict] = []
    if apply:
        for s in stuck:
            # In Progress may be intentional residual — only auto-Done Pending Review
            # unless --apply-all (not exposed; document residual path)
            if s["board_status"] != "Pending Review":
                actions.append(
                    {
                        "number": s["number"],
                        "skipped": True,
                        "reason": "In Progress after close may be deploy residual — manual review",
                    }
                )
                continue
            if dry_run:
                actions.append(
                    {
                        "number": s["number"],
                        "dry_run": True,
                        "would": "Done",
                    }
                )
                continue
            set_status(s["item_id"], "Done")
            actions.append({"number": s["number"], "board_status": "Done", "ok": True})

    return {
        "ok": True,
        "stuck_count": len(stuck),
        "over_sla_count": sum(1 for s in stuck if s.get("over_sla")),
        "max_hours": max_hours,
        "stuck": stuck,
        "actions": actions,
        "sla": "No closed issue may remain on Pending Review >24h (issue #58)",
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--issue", type=int, help="Issue number to mark Done / residual")
    p.add_argument("--pr", type=int, help="Merged PR number (resolves Fixes #N)")
    p.add_argument(
        "--residual",
        metavar="NOTE",
        help="Leave board In Progress (Pi deploy evidence still required)",
    )
    p.add_argument(
        "--force-open",
        action="store_true",
        help="Allow Done while issue still open (prefer closing issue first)",
    )
    p.add_argument("--sweep", action="store_true", help="List closed issues stuck off Done")
    p.add_argument(
        "--apply",
        action="store_true",
        help="With --sweep: set Done for closed+Pending Review stuck cards",
    )
    p.add_argument(
        "--max-hours",
        type=float,
        default=24.0,
        help="SLA hours for closed+Pending Review (default 24)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true", help="Always JSON out")
    args = p.parse_args(argv)

    if args.sweep:
        out = sweep(max_hours=args.max_hours, apply=args.apply, dry_run=args.dry_run)
    elif args.pr is not None:
        out = from_pr(
            args.pr,
            residual=args.residual,
            force_open=args.force_open,
            dry_run=args.dry_run,
        )
    elif args.issue is not None:
        out = mark_issue(
            args.issue,
            residual=args.residual,
            force_open=args.force_open,
            dry_run=args.dry_run,
        )
    else:
        p.print_help()
        return 2

    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
