"""Unit tests for projects-dashboard collectors against real temp git fixtures."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Ship path: import real collectors module under test
DASH_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH_ROOT))

from collectors import (  # noqa: E402
    collect_all_projects,
    collect_repo_status,
    discover_repos,
    is_git_repo,
)


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "Test",
             "GIT_AUTHOR_EMAIL": "test@example.com",
             "GIT_COMMITTER_NAME": "Test",
             "GIT_COMMITTER_EMAIL": "test@example.com"},
    )
    return (proc.stdout or "").strip()


class TestCollectors(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="projdash-")
        self.root = Path(self._td.name)
        self.repo = self.root / "sample-app"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main")
        (self.repo / "README.md").write_text("hello\n", encoding="utf-8")
        _git(self.repo, "add", "README.md")
        _git(self.repo, "commit", "-m", "init")
        _git(self.repo, "remote", "add", "origin", "https://github.com/example/sample-app.git")

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_is_git_repo(self) -> None:
        self.assertTrue(is_git_repo(self.repo))
        self.assertFalse(is_git_repo(self.root))

    def test_discover_repos_finds_nested(self) -> None:
        found = discover_repos([str(self.root)], max_depth=2)
        paths = {str(p.resolve()) for p in found}
        self.assertIn(str(self.repo.resolve()), paths)

    def test_collect_clean_with_remote(self) -> None:
        st = collect_repo_status(self.repo)
        self.assertTrue(st["is_git"])
        self.assertEqual(st["name"], "sample-app")
        self.assertEqual(st["path"], str(self.repo.resolve()))
        self.assertEqual(st["branch"], "main")
        self.assertFalse(st["dirty"])
        self.assertEqual(len(st["remotes"]), 1)
        self.assertEqual(st["remotes"][0]["name"], "origin")
        self.assertIn("github.com/example/sample-app", st["remotes"][0]["url"])
        self.assertIn("clean", st["status_label"])
        self.assertIsNone(st["error"])

    def test_collect_dirty_working_tree(self) -> None:
        (self.repo / "dirty.txt").write_text("changed\n", encoding="utf-8")
        st = collect_repo_status(self.repo)
        self.assertTrue(st["dirty"])
        self.assertIn("dirty", st["status_label"])

    def test_collect_ahead_behind_with_upstream(self) -> None:
        # Bare remote + clone style: add a second branch as fake upstream via local remote
        bare = self.root / "remote.git"
        _git(self.root, "clone", "--bare", str(self.repo), str(bare))
        # Re-point origin to bare and push
        _git(self.repo, "remote", "set-url", "origin", str(bare))
        _git(self.repo, "push", "-u", "origin", "main")
        # Create local commit (ahead)
        (self.repo / "ahead.txt").write_text("x\n", encoding="utf-8")
        _git(self.repo, "add", "ahead.txt")
        _git(self.repo, "commit", "-m", "ahead commit")
        st = collect_repo_status(self.repo)
        self.assertEqual(st["branch"], "main")
        self.assertIsNotNone(st["upstream"])
        self.assertEqual(st["ahead"], 1)
        self.assertEqual(st["behind"], 0)
        self.assertIn("ahead", st["status_label"])

    def test_collect_all_projects_payload(self) -> None:
        payload = collect_all_projects([str(self.root)], max_depth=2)
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["count"], 1)
        names = {p["name"] for p in payload["projects"]}
        self.assertIn("sample-app", names)
        sample = next(p for p in payload["projects"] if p["name"] == "sample-app")
        self.assertEqual(sample["branch"], "main")
        self.assertTrue(sample["remotes"])

    def test_non_git_path(self) -> None:
        st = collect_repo_status(self.root)
        self.assertFalse(st["is_git"])
        self.assertIsNotNone(st["error"])


if __name__ == "__main__":
    unittest.main()
