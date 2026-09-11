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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--python-only", action="store_true")
    p.add_argument("--js-only", action="store_true")
    p.add_argument("--list", action="store_true", help="print discovered suites and exit")
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
        return 0

    results: list[tuple[str, int]] = []
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
