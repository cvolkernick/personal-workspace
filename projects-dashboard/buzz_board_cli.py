#!/usr/bin/env python3
"""Agent-facing CLI for Buzz Board (GitHub Project #1 + issues).

Chat agents (Grok, Cadence, Forge, …) should use this instead of flaky GitHub
Projects MCP tools. Auth: GITHUB_TOKEN / GH_TOKEN / BUZZ_BOARD_GITHUB_TOKEN
with `repo` + `project` (classic) or equivalent fine-grained scopes.

Examples:
  python3 projects-dashboard/buzz_board_cli.py list
  python3 projects-dashboard/buzz_board_cli.py list --status Ready
  python3 projects-dashboard/buzz_board_cli.py show 21
  python3 projects-dashboard/buzz_board_cli.py create --title "…" --body "…"
  python3 projects-dashboard/buzz_board_cli.py set-status --item-id <node> --status Ready
  python3 projects-dashboard/buzz_board_cli.py set-status 58 --status Done

Canonical eng-gate Done path (issue #58): scripts/buzz-board set-status N Done
  (+ scripts/eng_gate_post_merge.py after merge). See ops/ENG_GATE_BOARD_DONE.md

See: GUIDES/BUZZ_BOARD_AGENT_ACCESS.md (Buzz nest) and projects-dashboard/BOARD_ACCESS.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sprint_board import (  # noqa: E402
    STATUS_ORDER,
    _token,
    credentials_status,
    set_item_status,
    sprint_payload,
)

DEFAULT_REPO = "cvolkernick/personal-workspace"
API = "https://api.github.com"


def _repo() -> str:
    return (
        os.environ.get("BUZZ_BOARD_REPO")
        or os.environ.get("GITHUB_REPOSITORY")
        or DEFAULT_REPO
    )


def _rest(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    token = _token()
    if not token:
        raise RuntimeError(
            "Missing GITHUB_TOKEN (or GH_TOKEN / BUZZ_BOARD_GITHUB_TOKEN)"
        )
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method.upper(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "buzz-board-cli",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8")
            code = int(resp.status)
            return code, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {"message": str(e)}
        except json.JSONDecodeError:
            parsed = {"message": raw[:500]}
        return int(e.code), parsed


def cmd_auth(_: argparse.Namespace) -> int:
    st = credentials_status()
    # Probe project without dumping secrets
    if st.get("token_present"):
        try:
            payload = sprint_payload(include_done=False)
            st["board_ok"] = bool(payload.get("ok"))
            st["board_error"] = payload.get("error")
            st["counts"] = payload.get("counts")
        except Exception as e:
            st["board_ok"] = False
            st["board_error"] = str(e)
    print(json.dumps(st, indent=2))
    return 0 if st.get("token_present") and st.get("board_ok", True) else 1


def cmd_list(args: argparse.Namespace) -> int:
    payload = sprint_payload(include_done=bool(args.include_done))
    if not payload.get("ok"):
        print(json.dumps(payload, indent=2))
        return 1

    status_filter = (args.status or "").strip()
    rows: list[dict[str, Any]] = []
    columns = payload.get("columns") or {}
    order = list(STATUS_ORDER)
    if args.include_done is False and "Done" in order:
        # still allow filtering Done if requested via --status
        pass
    for col in order:
        for it in columns.get(col) or []:
            if status_filter:
                st = it.get("status") or ""
                if (
                    st != status_filter
                    and status_filter.lower() not in st.lower()
                ):
                    continue
            rows.append(
                {
                    "number": it.get("number"),
                    "title": it.get("title"),
                    "status": it.get("status"),
                    "url": it.get("url"),
                    "repo": it.get("repo"),
                    "type": it.get("type"),
                    "item_id": it.get("item_id"),
                    "updated_at": it.get("updated_at"),
                }
            )
    # Uncategorized
    if not status_filter or status_filter.lower() in ("(none)", "none"):
        for it in payload.get("uncategorized") or []:
            if status_filter and status_filter.lower() not in ("(none)", "none"):
                continue
            rows.append(
                {
                    "number": it.get("number"),
                    "title": it.get("title"),
                    "status": it.get("status"),
                    "url": it.get("url"),
                    "repo": it.get("repo"),
                    "type": it.get("type"),
                    "item_id": it.get("item_id"),
                    "updated_at": it.get("updated_at"),
                }
            )

    out = {
        "ok": True,
        "board": payload.get("board"),
        "counts": payload.get("counts"),
        "items": rows,
        "item_count": len(rows),
        "decision": "stay-on-github",
        "access_path": "GraphQL ProjectV2 via GITHUB_TOKEN (not MCP Projects)",
    }
    if args.format == "table":
        print(
            f"Board: {(payload.get('board') or {}).get('title')} "
            f"({(payload.get('board') or {}).get('url')})"
        )
        print(f"Counts: {payload.get('counts')}")
        print(f"{'#':>5}  {'Status':<16}  Title")
        for r in rows:
            num = r.get("number") or "—"
            print(f"{num!s:>5}  {(r.get('status') or ''):<16}  {r.get('title')}")
        return 0
    print(json.dumps(out, indent=2))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    number = int(args.number)
    owner_repo = _repo()
    if "/" not in owner_repo:
        print(json.dumps({"ok": False, "error": f"bad repo {owner_repo}"}))
        return 1
    owner, repo = owner_repo.split("/", 1)
    code, issue = _rest("GET", f"/repos/{owner}/{repo}/issues/{number}")
    if code >= 400:
        print(json.dumps({"ok": False, "http": code, "error": issue}, indent=2))
        return 1

    # Enrich with board status if present
    board_status = None
    item_id = None
    payload = sprint_payload(include_done=True)
    if payload.get("ok"):
        for col, items in (payload.get("columns") or {}).items():
            for it in items:
                if it.get("number") == number and it.get("repo") in (
                    None,
                    owner_repo,
                    f"{owner}/{repo}",
                ):
                    board_status = it.get("status") or col
                    item_id = it.get("item_id")
                    break
            if board_status:
                break
        if not board_status:
            for it in payload.get("uncategorized") or []:
                if it.get("number") == number:
                    board_status = it.get("status")
                    item_id = it.get("item_id")
                    break

    out = {
        "ok": True,
        "number": issue.get("number"),
        "title": issue.get("title"),
        "state": issue.get("state"),
        "url": issue.get("html_url"),
        "body": issue.get("body"),
        "labels": [lab.get("name") for lab in (issue.get("labels") or [])],
        "board_status": board_status,
        "project_item_id": item_id,
        "repo": owner_repo,
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    owner_repo = _repo()
    owner, repo = owner_repo.split("/", 1)
    body = {
        "title": args.title,
        "body": args.body or "",
    }
    if args.labels:
        body["labels"] = [x.strip() for x in args.labels.split(",") if x.strip()]
    code, issue = _rest("POST", f"/repos/{owner}/{repo}/issues", body)
    if code >= 400:
        print(json.dumps({"ok": False, "http": code, "error": issue}, indent=2))
        return 1
    out = {
        "ok": True,
        "number": issue.get("number"),
        "title": issue.get("title"),
        "url": issue.get("html_url"),
        "note": (
            "Issue created on repo. Add to Buzz Board in GitHub UI or "
            "set-status once it is a project item (auto-add depends on project settings)."
        ),
    }
    print(json.dumps(out, indent=2))
    return 0


def _find_item_id_for_issue(number: int) -> str | None:
    """Resolve ProjectV2 item id for a repo issue number (include Done column)."""
    owner_repo = _repo()
    payload = sprint_payload(include_done=True)
    if not payload.get("ok"):
        return None
    for col, items in (payload.get("columns") or {}).items():
        for it in items or []:
            if it.get("number") == number and it.get("repo") in (
                None,
                owner_repo,
            ):
                return it.get("item_id")
    for it in payload.get("uncategorized") or []:
        if it.get("number") == number:
            return it.get("item_id")
    return None


def cmd_set_status(args: argparse.Namespace) -> int:
    item_id = args.item_id
    issue_number = getattr(args, "issue", None)
    if not item_id and issue_number is not None:
        item_id = _find_item_id_for_issue(int(issue_number))
        if not item_id:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"issue #{issue_number} not found on Buzz Board",
                        "hint": "use scripts/buzz-board set-status N Done (auto-adds) "
                        "or pass --item-id from list",
                    },
                    indent=2,
                )
            )
            return 1
    if not item_id:
        print(json.dumps({"ok": False, "error": "need --item-id or issue number"}))
        return 1
    result = set_item_status(item_id, args.status)
    if issue_number is not None:
        result = {**result, "number": int(issue_number)}
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Buzz Board agent CLI (GitHub Project GraphQL + Issues REST)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    auth = sub.add_parser("auth", help="Check token + board reachability")
    auth.set_defaults(func=cmd_auth)

    lst = sub.add_parser("list", help="List board items (optionally by Status)")
    lst.add_argument(
        "--status",
        default="",
        help="Filter by Status name (e.g. Ready, 'In Progress', Parked)",
    )
    lst.add_argument(
        "--include-done",
        action="store_true",
        help="Include Done column",
    )
    lst.add_argument(
        "--format",
        choices=("json", "table"),
        default="json",
    )
    lst.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="Read one issue by number")
    show.add_argument("number", type=int)
    show.set_defaults(func=cmd_show)

    create = sub.add_parser("create", help="Create a repo issue")
    create.add_argument("--title", required=True)
    create.add_argument("--body", default="")
    create.add_argument("--labels", default="", help="Comma-separated labels")
    create.set_defaults(func=cmd_create)

    st = sub.add_parser(
        "set-status",
        help="Set project Status by --item-id or issue number (eng-gate Done path)",
    )
    st.add_argument(
        "--item-id",
        default="",
        help="ProjectV2 item node id (optional if issue number given)",
    )
    st.add_argument(
        "issue",
        nargs="?",
        type=int,
        default=None,
        help="Issue number (preferred eng-gate form: set-status 58 --status Done)",
    )
    st.add_argument(
        "--status",
        required=True,
        help="Status option name (Parked, Ready, In Progress, Done, …)",
    )
    st.set_defaults(func=cmd_set_status)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
