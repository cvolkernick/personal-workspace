#!/usr/bin/env python3
"""Swarm ROI telemetry (#773).

Per-merged-PR records of spend, rework, review burden, and outcome.
Never silently estimates — missing data is ``unavailable`` with a reason.

Storage: ``ops/swarm-roi/`` (see README there).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SCHEMA_VERSION = 1
DEFAULT_REPO = "cvolkernick/personal-workspace"
REWORK_WINDOW_DAYS = 7
LEDGER_REL = Path("ops/swarm-roi")
RECORDS_REL = LEDGER_REL / "records"
WEEKLY_REL = LEDGER_REL / "weekly"
CONFIG_REL = LEDGER_REL / "config.json"
SWARM_ROI_PATH_PREFIX = "ops/swarm-roi/"

ISSUE_REF_RE = re.compile(
    r"(?i)\b(?:fixes|closes|resolves|fix(?:es)?)\s+#(\d+)\b"
)
COAUTHOR_RE = re.compile(
    r"^Co-authored-by:\s*([^<\n]+?)(?:\s*<[^>]+>)?\s*$",
    re.I | re.M,
)

DEFAULT_KNOWN_AGENTS = (
    "Assay",
    "Byline",
    "Cadence",
    "Fizz",
    "Fizzbuzz",
    "Forge",
    "Frankenfit",
    "Grok",
    "Honey",
    "Launch",
    "Meridian",
    "Nakatoshi",
    "Pollen",
    "Volt",
)
PARALLEL_LABELS = frozenset({"parallel-agent", "swarm"})
SINGLE_LABELS = frozenset({"single-agent"})
HUMAN_LABELS = frozenset({"human-only"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "unavailable", "reason": reason}
    out.update(extra)
    return out


def ok(**fields: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "ok"}
    out.update(fields)
    return out


def load_config(root: Path) -> dict[str, Any]:
    path = root / CONFIG_REL
    if not path.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "repo": DEFAULT_REPO,
            "rework_window_days": REWORK_WINDOW_DAYS,
            "known_agents": list(DEFAULT_KNOWN_AGENTS),
            "parallel_labels": sorted(PARALLEL_LABELS),
            "single_agent_labels": sorted(SINGLE_LABELS),
            "human_labels": sorted(HUMAN_LABELS),
        }
    return json.loads(path.read_text(encoding="utf-8"))


def label_names(labels: Iterable[Any]) -> set[str]:
    names: set[str] = set()
    for item in labels or []:
        if isinstance(item, str):
            names.add(item.strip().lower())
        elif isinstance(item, dict) and item.get("name"):
            names.add(str(item["name"]).strip().lower())
    return names


def parse_issue_refs(*texts: Optional[str]) -> list[int]:
    found: list[int] = []
    seen: set[int] = set()
    for text in texts:
        if not text:
            continue
        for match in ISSUE_REF_RE.finditer(text):
            n = int(match.group(1))
            if n not in seen:
                seen.add(n)
                found.append(n)
    return found


def _norm_agent(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def agent_names_from_messages(
    messages: Iterable[str], known_agents: Iterable[str]
) -> list[str]:
    known = {_norm_agent(a): a for a in known_agents}
    found: list[str] = []
    seen: set[str] = set()
    for message in messages:
        for match in COAUTHOR_RE.finditer(message or ""):
            raw = match.group(1).strip()
            key = _norm_agent(raw)
            canon = known.get(key)
            if canon and key not in seen:
                seen.add(key)
                found.append(canon)
    return found


def classify_parallelism(
    *,
    labels: Iterable[Any],
    commit_messages: Iterable[str],
    known_agents: Iterable[str],
) -> dict[str, Any]:
    names = label_names(labels)
    agents = agent_names_from_messages(commit_messages, known_agents)
    if names & {n.lower() for n in PARALLEL_LABELS} or "parallel-agent" in names or "swarm" in names:
        return {
            "kind": "parallel",
            "evidence": "label",
            "labels": sorted(names),
            "agents": agents,
        }
    if names & {n.lower() for n in SINGLE_LABELS} or "single-agent" in names:
        return {
            "kind": "single_agent",
            "evidence": "label",
            "labels": sorted(names),
            "agents": agents,
        }
    if names & {n.lower() for n in HUMAN_LABELS} or "human-only" in names:
        return {
            "kind": "human",
            "evidence": "label",
            "labels": sorted(names),
            "agents": agents,
        }
    if len(agents) >= 2:
        return {
            "kind": "parallel",
            "evidence": "coauthored_by",
            "agents": agents,
        }
    if len(agents) == 1:
        return {
            "kind": "single_agent",
            "evidence": "coauthored_by",
            "agents": agents,
        }
    return {
        "kind": "unavailable",
        "reason": (
            "git author is the operator identity; no agent Co-authored-by "
            "trailer and no parallel-agent/single-agent/human-only label. "
            "Not classified as human — that would be a silent inference."
        ),
        "agents": [],
    }


def spend_metric(usage: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not usage:
        return unavailable(
            "Grok Build / SuperGrok Heavy does not expose per-issue token "
            "usage to this repo (no API payload on the merge event)."
        )
    tokens = usage.get("tokens")
    if tokens is None:
        return unavailable(
            usage.get("reason")
            or "usage payload present but tokens field is missing; not estimated",
            source=usage.get("source"),
        )
    if not isinstance(tokens, int) or tokens < 0:
        return unavailable(
            "usage payload tokens is not a non-negative int; not estimated",
            source=usage.get("source"),
        )
    out = ok(tokens=tokens)
    if usage.get("source"):
        out["source"] = usage["source"]
    if usage.get("unit"):
        out["unit"] = usage["unit"]
    return out


def review_burden_metric(
    pr: dict[str, Any],
    reviews: Iterable[dict[str, Any]],
    comments: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    created = parse_dt(pr.get("created_at"))
    merged = parse_dt(pr.get("merged_at"))
    if not created or not merged:
        return unavailable(
            "PR created_at or merged_at missing; open→merge latency not computed"
        )
    rounds = 0
    for review in reviews or []:
        state = str(review.get("state") or "").upper()
        if state in {"APPROVED", "CHANGES_REQUESTED"}:
            rounds += 1
    comment_count = 0
    for comment in comments or []:
        user = comment.get("user") or {}
        login = str(user.get("login") or "")
        if login.endswith("[bot]"):
            continue
        comment_count += 1
    latency = int((merged - created).total_seconds())
    if latency < 0:
        return unavailable("merged_at is before created_at; clock skew, not estimated")
    return ok(
        review_rounds=rounds,
        review_comment_count=comment_count,
        open_to_merge_seconds=latency,
    )


def outcome_metric(issues: Iterable[dict[str, Any]]) -> dict[str, Any]:
    issues = list(issues or [])
    if not issues:
        return unavailable(
            "no linked Fixes/Closes issue on the PR; outcome not inferred from title"
        )
    rows = []
    for issue in issues:
        rows.append(
            {
                "number": issue.get("number"),
                "state": issue.get("state"),
                "state_reason": issue.get("state_reason"),
            }
        )
    closed_done = any(
        str(i.get("state") or "").lower() == "closed"
        and str(i.get("state_reason") or "").lower() in {"completed", "merged", ""}
        for i in issues
    )
    closed_not_planned = any(
        str(i.get("state") or "").lower() == "closed"
        and str(i.get("state_reason") or "").lower() in {"not_planned", "duplicate"}
        for i in issues
    )
    still_open = any(str(i.get("state") or "").lower() == "open" for i in issues)
    if closed_done and not still_open and not closed_not_planned:
        kind = "closed_as_done"
    elif closed_not_planned and not closed_done:
        kind = "closed_without_landing"
    elif still_open:
        kind = "issue_still_open"
    else:
        kind = "mixed"
    return ok(kind=kind, issues=rows)


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git failed")
    return proc.stdout


def commits_touching_paths(
    repo: Path,
    *,
    since: datetime,
    until: datetime,
    paths: Iterable[str],
    run_git: Callable[..., str] = _run_git,
) -> list[dict[str, Any]]:
    """Follow-up commits in [since, until] that touch any of *paths*.

    Excludes commits whose remaining paths are only under ops/swarm-roi/.
    """
    want = {p.strip().lstrip("./") for p in paths if p and p.strip()}
    want = {p for p in want if not p.startswith(SWARM_ROI_PATH_PREFIX)}
    if not want:
        return []
    log = run_git(
        repo,
        "log",
        "--no-merges",
        f"--since={iso(since)}",
        f"--until={iso(until)}",
        "--format=%H%x09%s",
        "--name-only",
    )
    commits: list[dict[str, Any]] = []
    sha = ""
    subject = ""
    files: list[str] = []

    def flush() -> None:
        nonlocal sha, subject, files
        if not sha:
            return
        kept = [f for f in files if f and not f.startswith(SWARM_ROI_PATH_PREFIX)]
        if kept and (set(kept) & want):
            commits.append(
                {
                    "sha": sha,
                    "subject": subject,
                    "paths": kept,
                    "revert": subject.startswith("Revert"),
                }
            )
        sha, subject, files = "", "", []

    for line in log.splitlines():
        if not line.strip():
            continue
        if "\t" in line and re.match(r"^[0-9a-f]{7,40}\t", line):
            flush()
            sha, subject = line.split("\t", 1)
        else:
            files.append(line.strip())
    flush()
    return commits


def rework_metric(
    repo: Path,
    *,
    merge_at: datetime,
    now: datetime,
    pr_paths: Iterable[str],
    reopened_issues: Optional[list[dict[str, Any]]] = None,
    window_days: int = REWORK_WINDOW_DAYS,
    run_git: Callable[..., str] = _run_git,
) -> dict[str, Any]:
    paths = [p for p in pr_paths if p and not str(p).startswith(SWARM_ROI_PATH_PREFIX)]
    if not paths:
        return unavailable("PR file list empty or only swarm-roi ledger paths")
    elapsed = now - merge_at
    window = timedelta(days=window_days)
    if elapsed < window:
        return {
            "status": "window_open",
            "reason": f"{window_days}-day rework window has not elapsed",
            "followup_commits": None,
            "reverts": None,
            "reopened_issues": None,
            "window_days": window_days,
        }
    until = merge_at + window
    # Start just after merge so the merge commit itself is not a follow-up.
    since = merge_at + timedelta(seconds=1)
    commits = commits_touching_paths(
        repo, since=since, until=until, paths=paths, run_git=run_git
    )
    reopened = list(reopened_issues or [])
    return ok(
        followup_commits=len(commits),
        reverts=sum(1 for c in commits if c.get("revert")),
        reopened_issues=len(reopened),
        window_days=window_days,
        followup_shas=[c["sha"] for c in commits],
    )


def build_record(
    *,
    pr: dict[str, Any],
    reviews: Iterable[dict[str, Any]],
    comments: Iterable[dict[str, Any]],
    commit_messages: Iterable[str],
    pr_paths: Iterable[str],
    issues: Iterable[dict[str, Any]],
    repo_path: Path,
    now: datetime,
    usage: Optional[dict[str, Any]] = None,
    config: Optional[dict[str, Any]] = None,
    reopened_issues: Optional[list[dict[str, Any]]] = None,
    run_git: Callable[..., str] = _run_git,
) -> dict[str, Any]:
    cfg = config or {}
    known = cfg.get("known_agents") or list(DEFAULT_KNOWN_AGENTS)
    window_days = int(cfg.get("rework_window_days") or REWORK_WINDOW_DAYS)
    merge_at = parse_dt(pr.get("merged_at"))
    if not merge_at:
        raise ValueError("PR is not merged (merged_at missing)")
    labels = pr.get("labels") or []
    parallelism = classify_parallelism(
        labels=labels, commit_messages=commit_messages, known_agents=known
    )
    linked = parse_issue_refs(pr.get("title"), pr.get("body"), pr.get("head_ref") or "")
    for issue in issues:
        n = issue.get("number")
        if isinstance(n, int) and n not in linked:
            linked.append(n)
    persisted_paths = [
        p for p in pr_paths if p and not str(p).startswith(SWARM_ROI_PATH_PREFIX)
    ]
    record = {
        "schema_version": SCHEMA_VERSION,
        "pr": pr.get("number"),
        "repo": (pr.get("base") or {}).get("repo", {}).get("full_name")
        or cfg.get("repo")
        or DEFAULT_REPO,
        "title": pr.get("title"),
        "html_url": pr.get("html_url"),
        "merge_sha": pr.get("merge_commit_sha"),
        "merged_at": iso(merge_at),
        "linked_issues": linked,
        "pr_paths": persisted_paths,
        "parallelism": parallelism,
        "metrics": {
            "spend": spend_metric(usage),
            "rework": rework_metric(
                repo_path,
                merge_at=merge_at,
                now=now,
                pr_paths=pr_paths,
                reopened_issues=reopened_issues,
                window_days=window_days,
                run_git=run_git,
            ),
            "review_burden": review_burden_metric(pr, reviews, comments),
            "outcome": outcome_metric(issues),
        },
        "sources": {
            "pr_metadata": "github",
            "rework": "git",
            "spend": "usage_payload" if usage else "none",
        },
        "recorded_at": iso(now),
    }
    return record


def record_path(store_dir: Path, pr_number: int) -> Path:
    return store_dir / f"pr-{int(pr_number)}.json"


def write_record(store_dir: Path, record: dict[str, Any]) -> Path:
    store_dir.mkdir(parents=True, exist_ok=True)
    path = record_path(store_dir, int(record["pr"]))
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_records(store_dir: Path) -> list[dict[str, Any]]:
    if not store_dir.is_dir():
        return []
    rows = []
    for path in sorted(store_dir.glob("pr-*.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def existing_pr_numbers(store_dir: Path) -> set[int]:
    found: set[int] = set()
    for rec in load_records(store_dir):
        n = rec.get("pr")
        if isinstance(n, int):
            found.add(n)
    return found


def merged_prs_since(
    pulls: Iterable[dict[str, Any]], since: datetime
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pr in pulls:
        merged = parse_dt(pr.get("merged_at"))
        if merged and merged >= since:
            out.append(pr)
    return out


def missing_merged_prs(
    pulls: Iterable[dict[str, Any]],
    *,
    since: datetime,
    have: Iterable[int],
) -> list[int]:
    have_set = set(have)
    missing: list[int] = []
    for pr in merged_prs_since(pulls, since):
        n = pr.get("number")
        if isinstance(n, int) and n not in have_set:
            missing.append(n)
    return missing


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return float(statistics.median(values))


def _week_bounds(now: datetime) -> tuple[datetime, datetime]:
    utc = now.astimezone(timezone.utc)
    weekday = utc.weekday()  # Monday=0
    start = datetime(utc.year, utc.month, utc.day, tzinfo=timezone.utc) - timedelta(
        days=weekday
    )
    end = start + timedelta(days=7)
    return start, end


def weekly_rollup(
    records: Iterable[dict[str, Any]],
    *,
    week_start: datetime,
    week_end: datetime,
) -> dict[str, Any]:
    in_week: list[dict[str, Any]] = []
    for rec in records:
        merged = parse_dt(rec.get("merged_at"))
        if merged and week_start <= merged < week_end:
            in_week.append(rec)

    buckets = {
        "parallel": [],
        "single_agent": [],
        "human": [],
        "unavailable": [],
    }
    for rec in in_week:
        kind = (rec.get("parallelism") or {}).get("kind") or "unavailable"
        buckets.setdefault(kind, [])
        buckets[kind].append(rec)

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        rounds: list[float] = []
        tokens: list[float] = []
        elapsed = 0
        with_followup = 0
        spend_gap = 0
        rework_open = 0
        for rec in rows:
            metrics = rec.get("metrics") or {}
            rb = metrics.get("review_burden") or {}
            if rb.get("status") == "ok" and isinstance(rb.get("review_rounds"), int):
                rounds.append(float(rb["review_rounds"]))
            sp = metrics.get("spend") or {}
            if sp.get("status") == "ok" and isinstance(sp.get("tokens"), int):
                tokens.append(float(sp["tokens"]))
            else:
                spend_gap += 1
            rw = metrics.get("rework") or {}
            if rw.get("status") == "window_open":
                rework_open += 1
            elif rw.get("status") == "ok":
                elapsed += 1
                if int(rw.get("followup_commits") or 0) > 0 or int(
                    rw.get("reverts") or 0
                ) > 0 or int(rw.get("reopened_issues") or 0) > 0:
                    with_followup += 1
        med_rounds = _median(rounds)
        med_tokens = _median(tokens)
        out: dict[str, Any] = {"count": len(rows)}
        if med_rounds is None:
            out["median_review_rounds"] = unavailable(
                "no review_burden.ok values in this bucket"
            )
        else:
            out["median_review_rounds"] = ok(value=med_rounds, n=len(rounds))
        if elapsed == 0:
            out["rework_rate"] = unavailable(
                "no records with elapsed 7-day rework window in this bucket",
                window_open=rework_open,
            )
        else:
            out["rework_rate"] = ok(
                value=with_followup / elapsed,
                n_elapsed=elapsed,
                n_with_followup=with_followup,
                window_open=rework_open,
            )
        if med_tokens is None:
            out["token_spend"] = unavailable(
                "no spend.ok token values in this bucket; not estimated",
                n_unavailable=spend_gap,
            )
        else:
            out["token_spend"] = ok(median_tokens=med_tokens, n=len(tokens))
        return out

    by_kind = {kind: summarize(rows) for kind, rows in buckets.items()}
    return {
        "schema_version": SCHEMA_VERSION,
        "week_start": iso(week_start)[:10],
        "week_end": iso(week_end)[:10],
        "pr_count": len(in_week),
        "by_parallelism": by_kind,
        "overall": summarize(in_week),
    }


# --- GitHub fetch (CLI only) -------------------------------------------------

def _gh_token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not t:
        raise SystemExit("error: set GITHUB_TOKEN or GH_TOKEN")
    return t


def gh_rest(method: str, path: str, token: Optional[str] = None) -> Any:
    req = Request(
        f"https://api.github.com{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {token or _gh_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "swarm-roi-773",
        },
    )
    try:
        with urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except HTTPError as e:
        err = e.read().decode()
        raise SystemExit(f"error: REST {method} {path} → {e.code}: {err}") from e


def gh_paginate(path: str, token: Optional[str] = None) -> list[Any]:
    rows: list[Any] = []
    url_path = path
    sep = "&" if "?" in url_path else "?"
    if "per_page=" not in url_path:
        url_path = f"{url_path}{sep}per_page=100"
    page = 1
    while True:
        chunk = gh_rest("GET", f"{url_path}&page={page}" if "page=" not in url_path else url_path, token)
        if not chunk:
            break
        if not isinstance(chunk, list):
            raise SystemExit(f"error: expected list from {path}")
        rows.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
        if page > 50:
            break
    return rows


def owner_repo(spec: str) -> tuple[str, str]:
    owner, repo = spec.split("/", 1)
    return owner, repo


def fetch_pr_bundle(repo: str, number: int) -> dict[str, Any]:
    owner, name = owner_repo(repo)
    base = f"/repos/{owner}/{name}"
    pr = gh_rest("GET", f"{base}/pulls/{number}")
    reviews = gh_paginate(f"{base}/pulls/{number}/reviews")
    issue_comments = gh_paginate(f"{base}/issues/{number}/comments")
    review_comments = gh_paginate(f"{base}/pulls/{number}/comments")
    commits = gh_paginate(f"{base}/pulls/{number}/commits")
    files = gh_paginate(f"{base}/pulls/{number}/files")
    messages = [((c.get("commit") or {}).get("message") or "") for c in commits]
    paths = [f.get("filename") for f in files if f.get("filename")]
    refs = parse_issue_refs(pr.get("title"), pr.get("body"))
    issues = []
    for n in refs:
        issues.append(gh_rest("GET", f"{base}/issues/{n}"))
    comments = list(issue_comments) + list(review_comments)
    return {
        "pr": pr,
        "reviews": reviews,
        "comments": comments,
        "commit_messages": messages,
        "pr_paths": paths,
        "issues": issues,
    }


def load_usage_payload() -> Optional[dict[str, Any]]:
    raw = os.environ.get("SWARM_ROI_USAGE_JSON")
    if not raw:
        return None
    path = Path(raw)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(raw)


def cmd_record(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    repo = args.repo or cfg.get("repo") or DEFAULT_REPO
    bundle = fetch_pr_bundle(repo, args.pr)
    if not bundle["pr"].get("merged_at"):
        print(f"error: PR #{args.pr} is not merged", file=sys.stderr)
        return 1
    record = build_record(
        pr=bundle["pr"],
        reviews=bundle["reviews"],
        comments=bundle["comments"],
        commit_messages=bundle["commit_messages"],
        pr_paths=bundle["pr_paths"],
        issues=bundle["issues"],
        repo_path=root,
        now=utcnow(),
        usage=load_usage_payload(),
        config=cfg,
    )
    store = Path(args.store) if args.store else root / RECORDS_REL
    path = write_record(store, record)
    print(path)
    return 0


def cmd_rollup(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    store = Path(args.store) if args.store else root / RECORDS_REL
    now = utcnow()
    start, end = _week_bounds(now)
    if args.week_start:
        start = parse_dt(args.week_start + "T00:00:00Z") or start
        end = start + timedelta(days=7)
    rollup = weekly_rollup(load_records(store), week_start=start, week_end=end)
    out_dir = Path(args.out) if args.out else root / WEEKLY_REL
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{rollup['week_start']}.json"
    out_path.write_text(json.dumps(rollup, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out_path)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Record any merged PRs since collect_merged_after that lack a ledger file."""
    root = Path(args.root).resolve()
    cfg = load_config(root)
    repo = args.repo or cfg.get("repo") or DEFAULT_REPO
    store = Path(args.store) if args.store else root / RECORDS_REL
    since = parse_dt(cfg.get("collect_merged_after")) or utcnow()
    owner, name = owner_repo(repo)
    pulls = gh_paginate(f"/repos/{owner}/{name}/pulls?state=closed&sort=updated&direction=desc")
    missing = missing_merged_prs(
        pulls, since=since, have=existing_pr_numbers(store)
    )
    for n in missing:
        bundle_pr = next((p for p in pulls if p.get("number") == n), None)
        if bundle_pr and not bundle_pr.get("merged_at"):
            continue
        print(f"recording missing PR #{n}", file=sys.stderr)
        ns = argparse.Namespace(
            root=str(root), pr=n, repo=repo, store=str(store)
        )
        rc = cmd_record(ns)
        if rc != 0:
            return rc
    print(f"recorded {len(missing)} missing PRs")
    return 0


def cmd_backfill_rework(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    store = Path(args.store) if args.store else root / RECORDS_REL
    now = utcnow()
    changed = 0
    for rec in load_records(store):
        rw = (rec.get("metrics") or {}).get("rework") or {}
        if rw.get("status") != "window_open":
            continue
        merge_at = parse_dt(rec.get("merged_at"))
        if not merge_at:
            continue
        paths = rec.get("pr_paths") or []
        # Paths are not always persisted on older records; skip rather than guess.
        if not paths:
            rec["metrics"]["rework"] = unavailable(
                "record has no pr_paths; cannot compute rework without guessing files"
            )
            write_record(store, rec)
            changed += 1
            continue
        rec["metrics"]["rework"] = rework_metric(
            root,
            merge_at=merge_at,
            now=now,
            pr_paths=paths,
            window_days=int(cfg.get("rework_window_days") or REWORK_WINDOW_DAYS),
        )
        rec["recorded_at"] = iso(now)
        write_record(store, rec)
        changed += 1
    print(f"updated {changed} records")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=".", help="repo root")
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="write ops/swarm-roi/records/pr-N.json for a merged PR")
    rec.add_argument("--pr", type=int, required=True)
    rec.add_argument("--repo", default="")
    rec.add_argument("--store", default="")
    rec.set_defaults(func=cmd_record)

    roll = sub.add_parser("rollup", help="write weekly rollup JSON")
    roll.add_argument("--store", default="")
    roll.add_argument("--out", default="")
    roll.add_argument("--week-start", default="", help="YYYY-MM-DD (Monday)")
    roll.set_defaults(func=cmd_rollup)

    bf = sub.add_parser("backfill-rework", help="close elapsed 7-day rework windows")
    bf.add_argument("--store", default="")
    bf.set_defaults(func=cmd_backfill_rework)

    syn = sub.add_parser("sync", help="record merged PRs since collect_merged_after that lack a file")
    syn.add_argument("--repo", default="")
    syn.add_argument("--store", default="")
    syn.set_defaults(func=cmd_sync)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
