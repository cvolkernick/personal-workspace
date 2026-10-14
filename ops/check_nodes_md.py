#!/usr/bin/env python3
"""Lint: ops/NODES.md is public. Fail on ports, IPs, paths, unit names, usernames.

  python3 ops/check_nodes_md.py [path]                 # lint
  python3 ops/check_nodes_md.py --compare OTHER_FILE   # also require identical copy

Issue and PR refs (#109) and plain numbers are allowed.
"""

from __future__ import annotations

import re
import sys
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
    other = None
    if "--compare" in args:
        i = args.index("--compare")
        other = Path(args[i + 1])
        del args[i : i + 2]
    path = Path(args[0]) if args else Path(__file__).resolve().parent / "NODES.md"
    text = path.read_text(encoding="utf-8")
    errs = lint(text)
    for e in errs:
        print(f"{path}: {e}")
    rc = 1 if errs else 0
    if other is not None:
        if not other.is_file():
            print(f"::warning::{other} missing; cannot compare NODES.md copies")
        elif other.read_text(encoding="utf-8") != text:
            print(f"{path}: differs from {other} (master and work/treasury must hold identical NODES.md)")
            rc = 1
    if rc == 0:
        print(f"{path}: ok (public-safe{', identical' if other else ''})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
