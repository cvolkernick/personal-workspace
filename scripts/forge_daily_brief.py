#!/usr/bin/env python3
"""Forge daily outcomes brief, built from live GitHub.

Board lines come from open issues labeled status:ready,
status:pending-review, or status:in-progress. Shipped lines are pull
requests merged at or after --last-brief. A blocker number is listed
only when that issue is still open. Pi notes are not GitHub state and
are printed with an explicit last-known date. This module does not
read a prior brief or agent memory for those board lines.

Fixes #983.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

REPO_DEFAULT = "cvolkernick/personal-workspace"

STATUS_LABELS = {
    "status:ready": "ready",
    "status:pending-review": "pending_review",
    "status:in-progress": "in_progress",
}

SECTION_HEADINGS = (
    ("ready", "Ready"),
    ("pending_review", "Pending review"),
    ("in_progress", "In progress"),
    ("blockers", "Blockers"),
    ("shipped", "Shipped since last brief"),
    ("pi", "Pi"),
)

GhRunner = Callable[[list[str]], Any]


def parse_time(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp needs a timezone: {value}")
    return parsed.astimezone(timezone.utc)


def label_names(raw: Any) -> list[str]:
    names: list[str] = []
    for item in raw or []:
        if isinstance(item, str) and item:
            names.append(item)
        elif isinstance(item, Mapping):
            name = item.get("name") or ""
            if name:
                names.append(str(name))
    return names


def normalize_issue(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "number": int(raw["number"]),
        "title": str(raw.get("title") or ""),
        "state": str(raw.get("state") or "").lower(),
        "labels": label_names(raw.get("labels")),
    }


def closing_numbers(raw: Any) -> list[int]:
    numbers: list[int] = []
    for item in raw or []:
        if isinstance(item, int):
            numbers.append(item)
        elif isinstance(item, str) and item.lstrip("#").isdigit():
            numbers.append(int(item.lstrip("#")))
        elif isinstance(item, Mapping) and item.get("number") is not None:
            numbers.append(int(item["number"]))
    return numbers


def normalize_pr(raw: Mapping[str, Any]) -> dict[str, Any]:
    merged = raw.get("mergedAt") or raw.get("merged_at") or ""
    return {
        "number": int(raw["number"]),
        "title": str(raw.get("title") or ""),
        "merged_at": str(merged),
        "closes": closing_numbers(
            raw.get("closingIssuesReferences") or raw.get("closes") or []
        ),
    }


def open_status_issues(issues: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {key: [] for key in STATUS_LABELS.values()}
    seen: dict[str, set[int]] = {key: set() for key in buckets}
    for raw in issues:
        issue = normalize_issue(raw)
        if issue["state"] != "open":
            continue
        for label, section in STATUS_LABELS.items():
            if label in issue["labels"] and issue["number"] not in seen[section]:
                buckets[section].append(
                    {"number": issue["number"], "title": issue["title"]}
                )
                seen[section].add(issue["number"])
    for section in buckets:
        buckets[section].sort(key=lambda item: item["number"])
    return buckets


def shipped_since(
    prs: Sequence[Mapping[str, Any]], last_brief: datetime
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for raw in prs:
        pr = raw if "merged_at" in raw and "closes" in raw else normalize_pr(raw)
        if not pr["merged_at"]:
            continue
        merged = parse_time(str(pr["merged_at"]))
        if merged >= last_brief:
            kept.append(
                {
                    "number": int(pr["number"]),
                    "title": str(pr["title"]),
                    "merged_at": merged.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "closes": list(pr["closes"]),
                }
            )
    kept.sort(key=lambda item: (item["merged_at"], item["number"]))
    return kept


def resolve_blockers(
    candidates: Sequence[int],
    open_issues: Sequence[Mapping[str, Any]],
    fetch_one: Callable[[int], Mapping[str, Any] | None],
) -> list[dict[str, Any]]:
    open_by: dict[int, dict[str, Any]] = {}
    for raw in open_issues:
        issue = normalize_issue(raw)
        if issue["state"] == "open":
            open_by[issue["number"]] = issue
    kept: list[dict[str, Any]] = []
    seen: set[int] = set()
    for number in candidates:
        number = int(number)
        if number in seen:
            continue
        seen.add(number)
        live = open_by.get(number)
        if live is None:
            fetched = fetch_one(number)
            if fetched is None:
                continue
            live = normalize_issue(fetched)
        if live["state"] != "open":
            continue
        kept.append({"number": live["number"], "title": live["title"]})
    kept.sort(key=lambda item: item["number"])
    return kept


def normalize_pi_facts(facts: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for fact in facts:
        text = str(fact.get("text") or "").strip()
        as_of = str(fact.get("as_of") or "").strip()
        if not text or not as_of:
            raise ValueError("a Pi fact needs both text and as_of")
        out.append({"text": text, "as_of": as_of})
    return out


def build_brief(
    *,
    open_issues: Sequence[Mapping[str, Any]],
    merged_prs: Sequence[Mapping[str, Any]],
    blocker_candidates: Sequence[int],
    fetch_one: Callable[[int], Mapping[str, Any] | None],
    pi_facts: Sequence[Mapping[str, str]],
    last_brief: datetime,
    as_of: datetime,
) -> dict[str, Any]:
    sections = open_status_issues(open_issues)
    blockers = resolve_blockers(blocker_candidates, open_issues, fetch_one)
    shipped = shipped_since(merged_prs, last_brief)
    pi = normalize_pi_facts(pi_facts)
    brief: dict[str, Any] = {
        "as_of": as_of.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_brief": last_brief.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ready": sections["ready"],
        "pending_review": sections["pending_review"],
        "in_progress": sections["in_progress"],
        "blockers": blockers,
        "shipped": shipped,
        "pi": pi,
    }
    brief["text"] = render_brief(brief)
    return brief


def render_brief(brief: Mapping[str, Any]) -> str:
    lines = [
        "Forge eng brief",
        f"as_of: {brief['as_of']}",
        f"last_brief: {brief['last_brief']}",
        "",
    ]
    for key, heading in SECTION_HEADINGS:
        lines.append(f"## {heading}")
        rows = brief[key]
        if not rows:
            lines.append("- (none)")
        elif key == "shipped":
            for pr in rows:
                closes = ", ".join(f"#{n}" for n in pr["closes"])
                suffix = f" fixes {closes}" if closes else ""
                lines.append(
                    f"- PR #{pr['number']} {pr['title']} (merged {pr['merged_at']}){suffix}"
                )
        elif key == "pi":
            for fact in rows:
                lines.append(f"- {fact['text']} (last known {fact['as_of']})")
        else:
            for issue in rows:
                lines.append(f"- #{issue['number']} {issue['title']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def board_mismatches(brief: Mapping[str, Any], open_issues: Sequence[Mapping[str, Any]]) -> list[str]:
    """Status sections must match the open label query that produced them.

    Blockers are not in that query. resolve_blockers drops any candidate
    whose own issue view is closed.
    """
    by_number: dict[int, dict[str, Any]] = {}
    for raw in open_issues:
        issue = normalize_issue(raw)
        if issue["state"] == "open":
            by_number[issue["number"]] = issue
    problems: list[str] = []
    for key, label in (
        ("ready", "status:ready"),
        ("pending_review", "status:pending-review"),
        ("in_progress", "status:in-progress"),
    ):
        for item in brief[key]:
            live = by_number.get(int(item["number"]))
            if live is None:
                problems.append(f"#{item['number']} listed in {key} but not open")
            elif label not in live["labels"]:
                problems.append(f"#{item['number']} listed in {key} without {label}")
    return problems


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def gh_json(args: list[str]) -> Any:
    env = os.environ.copy()
    env.pop("GH_FORCE_TTY", None)
    env["NO_COLOR"] = "1"
    proc = subprocess.run(
        ["gh", *args], check=False, capture_output=True, text=True, env=env
    )
    if proc.returncode != 0:
        detail = _ANSI.sub("", proc.stderr or proc.stdout or "").strip()
        raise SystemExit(detail or f"gh exited {proc.returncode}")
    text = _ANSI.sub("", proc.stdout).strip()
    if not text:
        return []
    return json.loads(text)


def fetch_open_issues(repo: str, runner: GhRunner = gh_json) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[int] = set()
    for label in STATUS_LABELS:
        rows = runner(
            [
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--label",
                label,
                "--limit",
                "200",
                "--json",
                "number,title,state,labels",
            ]
        )
        if not isinstance(rows, list):
            raise SystemExit(f"expected a list for {label}")
        for raw in rows:
            issue = normalize_issue(raw)
            if issue["number"] in seen:
                # Keep every status label if a second query returns the same issue.
                existing = next(item for item in found if item["number"] == issue["number"])
                for name in issue["labels"]:
                    if name not in existing["labels"]:
                        existing["labels"].append(name)
                continue
            seen.add(issue["number"])
            found.append(issue)
    return found


def fetch_merged_prs(
    repo: str, last_brief: datetime, runner: GhRunner = gh_json
) -> list[dict[str, Any]]:
    day = last_brief.astimezone(timezone.utc).strftime("%Y-%m-%d")
    rows = runner(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "merged",
            "--limit",
            "200",
            "--search",
            f"merged:>={day}",
            "--json",
            "number,title,mergedAt,closingIssuesReferences",
        ]
    )
    if not isinstance(rows, list):
        raise SystemExit("expected a list of merged pull requests")
    return [normalize_pr(raw) for raw in rows]


def fetch_issue(repo: str, number: int, runner: GhRunner = gh_json) -> dict[str, Any] | None:
    try:
        raw = runner(
            [
                "issue",
                "view",
                str(number),
                "--repo",
                repo,
                "--json",
                "number,title,state,labels",
            ]
        )
    except SystemExit as exc:
        if "Could not resolve" in str(exc) or "not found" in str(exc).lower():
            return None
        raise
    if not isinstance(raw, Mapping):
        raise SystemExit(f"expected one issue for #{number}")
    return normalize_issue(raw)


def load_snapshot(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SystemExit("snapshot must be a JSON object")
    return payload


def parse_pi_arg(value: str) -> dict[str, str]:
    text, sep, as_of = value.partition("|")
    if not sep:
        raise argparse.ArgumentTypeError("Pi fact must be 'text|as_of'")
    return {"text": text.strip(), "as_of": as_of.strip()}


def build_from_sources(
    *,
    repo: str,
    last_brief: datetime,
    as_of: datetime,
    blocker_candidates: Sequence[int],
    pi_facts: Sequence[Mapping[str, str]],
    snapshot: Mapping[str, Any] | None = None,
    runner: GhRunner = gh_json,
) -> dict[str, Any]:
    if snapshot is not None:
        open_issues = list(snapshot.get("open_issues") or [])
        merged = list(snapshot.get("merged_prs") or [])
        by_number = {
            int(key): value
            for key, value in (snapshot.get("issues_by_number") or {}).items()
        }

        def fetch_one(number: int) -> Mapping[str, Any] | None:
            raw = by_number.get(number)
            return raw if isinstance(raw, Mapping) else None

    else:
        open_issues = fetch_open_issues(repo, runner)
        merged = fetch_merged_prs(repo, last_brief, runner)

        def fetch_one(number: int) -> Mapping[str, Any] | None:
            return fetch_issue(repo, number, runner)

    brief = build_brief(
        open_issues=open_issues,
        merged_prs=merged,
        blocker_candidates=blocker_candidates,
        fetch_one=fetch_one,
        pi_facts=pi_facts,
        last_brief=last_brief,
        as_of=as_of,
    )
    problems = board_mismatches(brief, open_issues)
    if problems:
        raise SystemExit("brief does not match live open issues: " + "; ".join(problems))
    return brief


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=REPO_DEFAULT)
    parser.add_argument("--last-brief", required=True, help="ISO-8601 timestamp of the previous brief")
    parser.add_argument("--as-of", default=None, help="ISO-8601 timestamp. Default: now UTC")
    parser.add_argument("--blocker", action="append", type=int, default=[])
    parser.add_argument(
        "--pi",
        action="append",
        type=parse_pi_arg,
        default=[],
        help="Pi fact that GitHub cannot answer, as 'text|YYYY-MM-DD'",
    )
    parser.add_argument("--snapshot", help="JSON fixture instead of live gh")
    parser.add_argument("--json", action="store_true", help="print the structured brief")
    args = parser.parse_args(argv)

    last_brief = parse_time(args.last_brief)
    as_of = parse_time(args.as_of) if args.as_of else datetime.now(timezone.utc)
    snapshot = load_snapshot(args.snapshot) if args.snapshot else None
    brief = build_from_sources(
        repo=args.repo,
        last_brief=last_brief,
        as_of=as_of,
        blocker_candidates=args.blocker,
        pi_facts=args.pi,
        snapshot=snapshot,
    )
    if args.json:
        payload = {key: value for key, value in brief.items() if key != "text"}
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(brief["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
