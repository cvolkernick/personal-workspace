#!/usr/bin/env python3
"""Vercel Ignored Build Step for the FitDash project.

Vercel runs this from Root Directory = resistance-dashboard.
Exit 0 = skip / Canceled. Exit 1 = proceed with the build.
Any git/shallow-clone failure also exits 1 (build, never 128).

Docs: https://vercel.com/docs/project-configuration/vercel-json#ignorecommand
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
PATHS_FILE = APP_ROOT / "vercel-ignore-paths.txt"


def load_fitdash_prefixes(path: Path = PATHS_FILE) -> List[str]:
    prefixes: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        prefixes.append(line.replace("\\", "/"))
    return prefixes


def normalize_repo_path(path: str) -> str:
    norm = path.replace("\\", "/").lstrip("./")
    while norm.startswith("/"):
        norm = norm[1:]
    return norm


def is_fitdash_path(path: str, prefixes: Sequence[str] | None = None) -> bool:
    """True when a repo-relative path should trigger a FitDash Vercel build."""
    prefixes = list(prefixes) if prefixes is not None else load_fitdash_prefixes()
    norm = normalize_repo_path(path)
    if not norm or norm.startswith(".git/"):
        return False
    for prefix in prefixes:
        p = prefix.rstrip("/")
        if norm == p or norm.startswith(p + "/"):
            return True
        # Allow a prefix written with a trailing slash to match the dir itself.
        if prefix.endswith("/") and norm == prefix:
            return True
    return False


def should_skip_build(
    changed_paths: Iterable[str], prefixes: Sequence[str] | None = None
) -> bool:
    """True → exit 0 (Canceled). False → exit 1 (build)."""
    prefixes = list(prefixes) if prefixes is not None else load_fitdash_prefixes()
    return not any(is_fitdash_path(p, prefixes) for p in changed_paths)


def _git(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )


def git_object_exists(sha: str, cwd: Path) -> bool:
    if not sha or sha in (".",):
        return False
    proc = _git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd)
    return proc.returncode == 0


def resolve_compare_base(cwd: Path, previous_sha: Optional[str] = None) -> Optional[str]:
    """Last successful deploy SHA if present in this clone, else HEAD^."""
    if previous_sha is None:
        previous_sha = os.environ.get("VERCEL_GIT_PREVIOUS_SHA", "").strip() or None
    if previous_sha and git_object_exists(previous_sha, cwd):
        return previous_sha
    if git_object_exists("HEAD^", cwd):
        return "HEAD^"
    return None


def list_changed_paths(base: str, cwd: Path, head: str = "HEAD") -> Optional[List[str]]:
    proc = _git(["diff", "--name-only", base, head], cwd)
    if proc.returncode != 0:
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def decide_exit(
    changed_paths: Optional[Iterable[str]] = None,
    repo_root: Path = REPO_ROOT,
    previous_sha: Optional[str] = None,
    prefixes: Sequence[str] | None = None,
) -> int:
    """Return 0 (skip) or 1 (build). Never raises; never returns 128."""
    try:
        prefixes = list(prefixes) if prefixes is not None else load_fitdash_prefixes()
        if changed_paths is None:
            base = resolve_compare_base(repo_root, previous_sha=previous_sha)
            if base is None:
                print("fitdash-ignore: no compare base (shallow clone?) → build")
                return 1
            listed = list_changed_paths(base, repo_root)
            if listed is None:
                print(f"fitdash-ignore: git diff {base} failed → build")
                return 1
            changed_paths = listed
        paths = [normalize_repo_path(p) for p in changed_paths]
        hits = [p for p in paths if is_fitdash_path(p, prefixes)]
        if hits:
            print("fitdash-ignore: FitDash paths changed → build")
            for p in hits[:20]:
                print(f"  {p}")
            return 1
        print("fitdash-ignore: no FitDash paths in diff → skip")
        return 0
    except Exception as exc:  # noqa: BLE001 — fail open to a build, never ERROR
        print(f"fitdash-ignore: {exc.__class__.__name__}: {exc} → build")
        return 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--changed",
        nargs="*",
        metavar="PATH",
        help="Repo-relative paths to evaluate (no git / no Vercel).",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=REPO_ROOT,
        help="Git repo root (default: monorepo parent of this app).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    changed = args.changed if args.changed else None
    return decide_exit(changed_paths=changed, repo_root=Path(args.repo))


if __name__ == "__main__":
    raise SystemExit(main())
