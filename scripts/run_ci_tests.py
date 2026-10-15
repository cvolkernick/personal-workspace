#!/usr/bin/env python3
"""Unified CI test runner (#584).

Canonical invocation is ops/github-workflows/test.yml (this script). Docs link
there instead of duplicating the suite list. Install copy: .github/workflows/.

Python: pytest per suite (isolated cwd + PYTHONPATH) so duplicate test
module names and sibling `server.py` files do not collide.
JS: existing standalone `node resistance-dashboard/tests/*.js` scripts.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    "archive",
    "node_modules",
    "personal-workspace-worktrees",
}
EXTRA_PY_REL = ("research/test_coinbase_feasibility_doc.py",)


def tree_state(root: Path = ROOT) -> dict[str, str] | None:
    """Fingerprint of every modified/untracked (non-ignored) path: ``path -> status:sha256``.

    Returns None when ``root`` is not a git checkout (guard is skipped)."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "-uall"],
            cwd=str(root),
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    state: dict[str, str] = {}
    entries = proc.stdout.decode("utf-8", "surrogateescape").split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, rel = entry[:2], entry[3:]
        if code[0] in "RC":  # rename/copy: next field is the original path
            i += 1
        path = root / rel
        digest = ""
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        state[rel] = f"{code}:{digest}"
    return state


def tree_changes(before: dict[str, str] | None, after: dict[str, str] | None) -> list[str]:
    """Paths whose git state or content differs between two ``tree_state`` snapshots."""
    if before is None or after is None:
        return []
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


def discover_python_suites(root: Path = ROOT) -> list[Path]:
    found: list[Path] = []
    for tests_dir in sorted(root.rglob("tests")):
        if not tests_dir.is_dir():
            continue
        if any(part in SKIP_DIR_NAMES for part in tests_dir.relative_to(root).parts):
            continue
        if any(tests_dir.glob("test_*.py")):
            found.append(tests_dir)
    return found


def discover_js_tests(root: Path = ROOT) -> list[Path]:
    d = root / "resistance-dashboard" / "tests"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.js") if p.is_file())


def extra_python_files(root: Path = ROOT) -> list[Path]:
    return [root / rel for rel in EXTRA_PY_REL if (root / rel).is_file()]


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> int:
    print("+", " ".join(cmd), f"(cwd={cwd.relative_to(ROOT) if cwd != ROOT else '.'})")
    sys.stdout.flush()
    return subprocess.run(cmd, cwd=str(cwd), env=env).returncode


def _pytest_env(suite_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    parts = [str(suite_root), str(ROOT)]
    existing = env.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_python_suite(tests_dir: Path, extra: list[str]) -> int:
    suite_root = tests_dir.parent
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "--import-mode=importlib",
        "-q",
        "--tb=line",
        *extra,
    ]
    return _run(cmd, cwd=suite_root, env=_pytest_env(suite_root))


def run_python_file(path: Path, extra: list[str]) -> int:
    del extra
    # Standalone scripts (not pytest collected).
    rel = str(path.relative_to(ROOT))
    return _run([sys.executable, rel], cwd=ROOT, env=_pytest_env(ROOT))


def run_js(path: Path) -> int:
    cmd = ["node", str(path.relative_to(ROOT))]
    return _run(cmd, cwd=ROOT, env=os.environ.copy())


def discover_gethelpfrom_node_tests(root: Path = ROOT) -> list[Path]:
    d = root / "products" / "gethelpfrom.ai" / "tests"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("web.test.js") if p.is_file())


def run_gethelpfrom_node() -> int:
    root = ROOT / "products" / "gethelpfrom.ai"
    if not discover_gethelpfrom_node_tests():
        return 0
    if not (root / "package-lock.json").is_file():
        print("products/gethelpfrom.ai/package-lock.json missing")
        return 1
    code = _run(["npm", "ci", "--ignore-scripts"], cwd=root, env=os.environ.copy())
    if code != 0:
        return code
    return _run(["node", "--test", "tests/web.test.js"], cwd=root, env=os.environ.copy())


def discover_kanban_node(root: Path = ROOT) -> Path | None:
    ui = root / "projects-dashboard" / "eng-kanban"
    if (ui / "package.json").is_file() and (ui / "package-lock.json").is_file():
        return ui
    return None


def run_kanban_node() -> int:
    root = discover_kanban_node()
    if root is None:
        print("projects-dashboard/eng-kanban package-lock.json missing")
        return 1
    code = _run(["npm", "ci", "--ignore-scripts"], cwd=root, env=os.environ.copy())
    if code != 0:
        return code
    return _run(["npm", "test"], cwd=root, env=os.environ.copy())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--python-only", action="store_true")
    p.add_argument("--js-only", action="store_true")
    p.add_argument("--list", action="store_true", help="print discovered suites and exit")
    p.add_argument(
        "--no-clean-check",
        action="store_true",
        help="do not fail when the suites modify tracked files or leave new untracked ones",
    )
    p.add_argument("pytest_args", nargs="*", help="extra args forwarded to pytest")
    args = p.parse_args(argv)

    suites = discover_python_suites()
    extras = extra_python_files()
    js = discover_js_tests()
    if args.list:
        print("python suites:")
        for s in suites:
            print(" ", s.relative_to(ROOT))
        print("python extras:")
        for s in extras:
            print(" ", s.relative_to(ROOT))
        print("js:")
        for s in js:
            print(" ", s.relative_to(ROOT))
        print("gethelpfrom:")
        for s in discover_gethelpfrom_node_tests():
            print(" ", s.relative_to(ROOT))
        kanban = discover_kanban_node()
        print("kanban:")
        if kanban is not None:
            print(" ", kanban.relative_to(ROOT))
        return 0

    results: list[tuple[str, int]] = []
    before = None if args.no_clean_check else tree_state()
    if not args.js_only:
        for tests_dir in suites:
            label = str(tests_dir.relative_to(ROOT))
            results.append((label, run_python_suite(tests_dir, args.pytest_args)))
        for path in extras:
            label = str(path.relative_to(ROOT))
            results.append((label, run_python_file(path, args.pytest_args)))
    if not args.python_only:
        for path in js:
            label = str(path.relative_to(ROOT))
            results.append((label, run_js(path)))
        results.append(("products/gethelpfrom.ai/tests/web.test.js", run_gethelpfrom_node()))
        results.append(("projects-dashboard/eng-kanban", run_kanban_node()))

    if not args.no_clean_check:
        changed = tree_changes(before, tree_state())
        if changed:
            print()
            print("tests modified the working tree (tests must write to a tmp dir, never to tracked paths):")
            for rel in changed:
                print(f"  {rel}")
        results.append(("working tree unchanged by tests", 1 if changed else 0))

    print()
    print("| Suite | Result |")
    print("|---|---|")
    failed = 0
    for label, code in results:
        status = "OK" if code == 0 else f"FAIL ({code})"
        if code != 0:
            failed += 1
        print(f"| {label} | {status} |")
    print(f"{len(results) - failed} ok · {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
