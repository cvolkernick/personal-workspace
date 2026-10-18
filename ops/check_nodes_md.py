#!/usr/bin/env python3
"""Lint: ops/NODES.md is public. Fail on ports, IPs, paths, unit names, usernames.

  python3 ops/check_nodes_md.py [path]                 # lint
  python3 ops/check_nodes_md.py --compare OTHER_FILE   # identical copy; CI retries once on differ
  python3 ops/check_nodes_md.py --sibling-branch REF   # fetch REF, compare, retry once

Issue and PR refs (#109) and plain numbers are allowed.

``--compare`` is what ``nodes-md.yml`` calls after its own fetch. When that copy
differs and ``GITHUB_BASE_REF`` or ``GITHUB_REF_NAME`` is set, the checker waits
``RETRY_WAIT_SECONDS`` (60), fetches the sibling branch once, and fails only if
that copy still differs. The failure says it retried.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("ipv4 address", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")),
    ("tailnet/ipv6 address", re.compile(r"\b(?:fd7a|100\.\d+)[:.]|[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){3,}", re.I)),
    ("tailnet domain", re.compile(r"\.ts\.net\b|\btail[0-9a-f]{4,}\b", re.I)),
    ("port", re.compile(r"(?<![#\w]):\d{2,5}\b|\bport\s*\d{2,5}\b|(?<![#\w.])\d{4,5}\b(?![-/]\d)", re.I)),
    ("filesystem path", re.compile(r"(?:~/|%h/|/home/|/etc/|/usr/|/var/|\$HOME)")),
    ("systemd unit", re.compile(r"\b[\w@-]+\.(?:service|timer|socket)\b")),
    ("username", re.compile(r"\b[\w-]+-agent\b")),
    ("env var / secret", re.compile(r"\b[A-Z][A-Z0-9]*_(?:PATH|TOKEN|KEY|SECRET|DIR)\b")),
]
# Dates (2026-10-14) and years are fine; drop them before the number rules run.
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# One fixed wait. A sibling push can land a few seconds after this branch (#1239).
RETRY_WAIT_SECONDS = 60


class FetchFailed(RuntimeError):
    """git fetch of the sibling branch failed. Not a content mismatch."""


def classify_sibling(local_text: str, sibling: str | None) -> str:
    if sibling is None:
        return "missing"
    if sibling != local_text:
        return "differs"
    return "identical"


def compare_sibling_once_with_retry(
    local_text: str,
    branch: str,
    *,
    fetch,
    sleep,
    wait_s: float = RETRY_WAIT_SECONDS,
) -> str:
    """Fetch ``branch`` and classify the copy.

    On ``differs`` only, sleep ``wait_s`` and fetch once more. Returns
    ``identical``, ``identical-after-retry``, ``missing``, ``missing-after-retry``,
    or ``differs-after-retry``.
    """
    first = classify_sibling(local_text, fetch(branch))
    if first != "differs":
        return first
    sleep(wait_s)
    second = classify_sibling(local_text, fetch(branch))
    if second == "identical":
        return "identical-after-retry"
    if second == "missing":
        return "missing-after-retry"
    return "differs-after-retry"


def sibling_branch_name() -> str | None:
    """Same choice as the workflow: master compares work/treasury, anything else compares master.

    ``GITHUB_BASE_REF`` is set on pull_request. Push uses ``GITHUB_REF_NAME``.
    """
    base = os.environ.get("GITHUB_BASE_REF") or os.environ.get("GITHUB_REF_NAME") or ""
    if not base:
        return None
    return "work/treasury" if base == "master" else "master"


def sibling_text(branch: str) -> str | None:
    """Fetch ``origin/branch`` and return ``ops/NODES.md``, or None if that path is absent."""
    fetch = subprocess.run(
        ["git", "fetch", "--depth=1", "origin", branch],
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        detail = (fetch.stderr or fetch.stdout or "git fetch failed").strip()
        raise FetchFailed(f"git fetch origin {branch} failed: {detail}"[:500])
    show = subprocess.run(
        ["git", "show", "FETCH_HEAD:ops/NODES.md"],
        capture_output=True,
        text=True,
    )
    if show.returncode != 0:
        return None
    return show.stdout


def _take_option(args: list[str], flag: str) -> str | None:
    if flag not in args:
        return None
    i = args.index(flag)
    if i + 1 >= len(args):
        raise SystemExit(f"{flag} needs a value")
    value = args[i + 1]
    del args[i : i + 2]
    return value


def sibling_messages(path: Path, branch: str, status: str, *, warn: bool, wait_s: float) -> tuple[int, str, bool]:
    """Return (rc, ok suffix, suppress_ok)."""
    if status == "identical":
        return 0, ", identical", False
    if status == "identical-after-retry":
        return 0, ", identical after one retry", False
    if status == "missing":
        print(f"::warning::{branch} ops/NODES.md missing; cannot compare NODES.md copies")
        return 0, "", False
    if status == "missing-after-retry":
        print(f"::warning::{branch} ops/NODES.md missing after one retry; cannot compare NODES.md copies")
        return 0, "", False
    message = (
        f"{path}: differs from {branch} after one retry (waited {wait_s:g}s). "
        "Retried once. master and work/treasury must hold identical NODES.md"
    )
    if warn:
        print(f"::warning::{message}")
        return 0, "", True
    print(message)
    return 1, "", False


def lint(text: str) -> list[str]:
    errs = []
    in_front = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip() == "---":
            in_front = not in_front
            continue
        if in_front:
            continue
        probe = _DATE.sub("", line)
        for name, rx in RULES:
            m = rx.search(probe)
            if m:
                errs.append(f"line {n}: {name}: {m.group(0)!r}")
    return errs


def main(argv: list[str]) -> int:
    args = list(argv)
    warn = "--warn-on-differ" in args
    if warn:
        args.remove("--warn-on-differ")
    try:
        other_raw = _take_option(args, "--compare")
        sibling = _take_option(args, "--sibling-branch")
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    if other_raw is not None and sibling is not None:
        print("pass only one of --compare and --sibling-branch", file=sys.stderr)
        return 2
    other = Path(other_raw) if other_raw is not None else None
    path = Path(args[0]) if args else Path(__file__).resolve().parent / "NODES.md"
    text = path.read_text(encoding="utf-8")
    errs = lint(text)
    for e in errs:
        print(f"{path}: {e}")
    rc = 1 if errs else 0
    suffix = ", identical" if other is not None else ""
    suppress_ok = False
    if sibling is not None:
        try:
            status = compare_sibling_once_with_retry(
                text,
                sibling,
                fetch=sibling_text,
                sleep=time.sleep,
            )
        except FetchFailed as e:
            print(str(e), file=sys.stderr)
            return 1
        sibling_rc, suffix, suppress_ok = sibling_messages(
            path, sibling, status, warn=warn, wait_s=RETRY_WAIT_SECONDS
        )
        rc = rc or sibling_rc
    elif other is not None:
        if not other.is_file():
            print(f"::warning::{other} missing; cannot compare NODES.md copies")
        elif other.read_text(encoding="utf-8") != text:
            branch = sibling_branch_name()
            if branch is None:
                print(f"{path}: differs from {other} (master and work/treasury must hold identical NODES.md)")
                rc = 1
            else:
                # The workflow already fetched once. Wait, then fetch that sibling once more.
                time.sleep(RETRY_WAIT_SECONDS)
                try:
                    fresh = sibling_text(branch)
                except FetchFailed as e:
                    print(str(e), file=sys.stderr)
                    return 1
                status = classify_sibling(text, fresh)
                if status == "identical":
                    suffix = ", identical after one retry"
                elif status == "missing":
                    suffix = ""
                    print(f"::warning::{branch} ops/NODES.md missing after one retry; cannot compare NODES.md copies")
                else:
                    suffix = ""
                    print(
                        f"{path}: differs from {branch} after one retry (waited {RETRY_WAIT_SECONDS:g}s). "
                        "Retried once. master and work/treasury must hold identical NODES.md"
                    )
                    rc = 1
    if rc == 0 and not suppress_ok:
        print(f"{path}: ok (public-safe{suffix})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
