"""Repo-root pytest hooks. Applies CI quarantine to every per-suite run (#584).

Canonical workflow: ops/github-workflows/test.yml
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
QUARANTINE = ROOT / "scripts" / "ci_quarantine.txt"


def load_quarantine(path: Path = QUARANTINE) -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = []
    if not path.is_file():
        return rules
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        pattern = parts[0]
        issue = parts[1] if len(parts) > 1 else "untracked"
        rules.append((pattern, issue))
    return rules


def repo_nodeid(item: pytest.Item, root: Path = ROOT) -> str:
    path = Path(str(item.fspath)).resolve()
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()
    node = item.nodeid
    suffix = node[node.find("::") :] if "::" in node else ""
    return rel + suffix


def quarantine_issue(repo_id: str, rules: list[tuple[str, str]]) -> str | None:
    for pattern, issue in rules:
        if repo_id == pattern or repo_id.startswith(pattern + "::") or pattern == repo_id.split("::", 1)[0]:
            return issue
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    rules = load_quarantine()
    if not rules:
        return
    for item in items:
        issue = quarantine_issue(repo_nodeid(item), rules)
        if issue:
            item.add_marker(pytest.mark.skip(reason=f"quarantine {issue}"))
