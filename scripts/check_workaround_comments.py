#!/usr/bin/env python3
"""Lint: no unexplained TODO / FIXME / HACK / XXX / workaround comments in live code (#1184).

A marker comment is allowed only when it names a tracked issue (``#1234``), e.g.
``# TODO(#1234): handle pagination`` or ``# workaround, see #1234``. Anything else
fails: remove the comment, do the fix, or file an issue and reference it.

Only comments are checked (Python via ``tokenize``; other source types by comment
prefix), so strings, docs, and test data that mention the words are fine.
Archived, vendored, and dependency dirs are skipped.

  python3 scripts/check_workaround_comments.py          # lint tracked files
  python3 scripts/check_workaround_comments.py PATH...  # lint given files
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "archive",
    "experimental",
    "node_modules",
    "personal-workspace-worktrees",
    "vendor",
}
# This guard and its tests have to spell the markers out.
SELF_EXEMPT = {
    "scripts/check_workaround_comments.py",
    "scripts/tests/test_check_workaround_comments.py",
}
PY_EXT = {".py"}
HASH_EXT = {".sh", ".yml", ".yaml", ".service", ".timer", ".command", ".ini", ".toml", ".cfg", ".txt"}
SLASH_EXT = {".js", ".mjs", ".cjs", ".ts", ".html", ".css"}

# TODO/FIXME/HACK/XXX count only in capitals (prose "todo"/"hack" is fine); "workaround" in any case.
_UPPER = re.compile(r"\b(?:TODO|FIXME|HACK|XXX)\b")
_WORKAROUND = re.compile(r"\bwork-?arounds?\b", re.IGNORECASE)
ISSUE_REF = re.compile(r"#\d+")
HASH_COMMENT = re.compile(r"(?:^|\s)#\s?(.*)$")
SLASH_COMMENT = re.compile(r"(?://|/\*|<!--|^\s*\*)\s?(.*)$")


def _violates(comment: str) -> bool:
    return bool(_UPPER.search(comment) or _WORKAROUND.search(comment)) and not ISSUE_REF.search(comment)


def _python_comments(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Fall back to line scan so a file that does not tokenize is still checked.
        out = []
        for n, line in enumerate(text.splitlines(), 1):
            m = HASH_COMMENT.search(line)
            if m:
                out.append((n, m.group(0)))
    return out


def scan_text(text: str, suffix: str) -> list[tuple[int, str]]:
    """Return ``(line, comment)`` for every unexplained marker comment."""
    hits: list[tuple[int, str]] = []
    if suffix in PY_EXT:
        comments = _python_comments(text)
    elif suffix in HASH_EXT:
        comments = [
            (n, m.group(0).strip())
            for n, line in enumerate(text.splitlines(), 1)
            if (m := HASH_COMMENT.search(line))
        ]
    elif suffix in SLASH_EXT:
        comments = [
            (n, m.group(0).strip())
            for n, line in enumerate(text.splitlines(), 1)
            if (m := SLASH_COMMENT.search(line))
        ]
    else:
        return hits
    for n, comment in comments:
        if _violates(comment):
            hits.append((n, comment.strip()))
    return hits


def tracked_files(root: Path = ROOT) -> list[Path]:
    try:
        proc = subprocess.run(["git", "ls-files", "-z"], cwd=str(root), capture_output=True, check=True)
        rels = [r for r in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if r]
    except (OSError, subprocess.CalledProcessError):
        rels = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
    return [root / r for r in rels]


def in_scope(path: Path, root: Path = ROOT) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    if rel.as_posix() in SELF_EXEMPT:
        return False
    if any(part in SKIP_DIR_NAMES for part in rel.parts):
        return False
    return path.suffix in PY_EXT | HASH_EXT | SLASH_EXT


def scan_paths(paths: list[Path], root: Path = ROOT) -> list[str]:
    errs: list[str] = []
    for path in paths:
        if not in_scope(path, root) or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = str(path)
        for n, comment in scan_text(text, path.suffix):
            errs.append(f"{rel}:{n}: unexplained marker comment (remove, fix, or reference an issue #N): {comment[:100]}")
    return errs


def main(argv: list[str]) -> int:
    paths = [Path(a).resolve() for a in argv] if argv else tracked_files()
    errs = scan_paths(paths)
    for e in errs:
        print(e)
    if errs:
        print(f"{len(errs)} unexplained TODO/FIXME/HACK/XXX/workaround comment(s) (#1184)")
        return 1
    print("ok: no unexplained TODO/FIXME/HACK/XXX/workaround comments")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
