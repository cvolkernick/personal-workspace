"""Thin tests for git helpers still used via workspace.collect_repo_status."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

from workspace import collect_repo_status, path_is_dirty  # noqa: E402


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


class TestGitStatus(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="git-st-")
        self.repo = Path(self._td.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main")
        (self.repo / "README.md").write_text("hi\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "init")
        _git(self.repo, "remote", "add", "origin", "https://github.com/example/repo.git")

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_clean_with_remote(self) -> None:
        st = collect_repo_status(self.repo)
        self.assertTrue(st["is_git"])
        self.assertEqual(st["branch"], "main")
        self.assertFalse(st["dirty"])
        self.assertEqual(st["remotes"][0]["url"], "https://github.com/example/repo.git")
        self.assertIn("clean", st["status_label"])

    def test_dirty(self) -> None:
        (self.repo / "a.txt").write_text("x\n", encoding="utf-8")
        st = collect_repo_status(self.repo)
        self.assertTrue(st["dirty"])
        self.assertIn("a.txt", st["dirty_paths"])

    def test_ahead_behind(self) -> None:
        bare = Path(self._td.name) / "remote.git"
        _git(Path(self._td.name), "clone", "--bare", str(self.repo), str(bare))
        _git(self.repo, "remote", "set-url", "origin", str(bare))
        _git(self.repo, "push", "-u", "origin", "main")
        (self.repo / "ahead.txt").write_text("y\n", encoding="utf-8")
        _git(self.repo, "add", "ahead.txt")
        _git(self.repo, "commit", "-m", "ahead")
        st = collect_repo_status(self.repo)
        self.assertEqual(st["ahead"], 1)
        self.assertEqual(st["behind"], 0)

    def test_path_is_dirty_helper(self) -> None:
        self.assertTrue(path_is_dirty("foo", ["foo/bar.py"]))
        self.assertFalse(path_is_dirty("foo", ["baz/x"]))


if __name__ == "__main__":
    unittest.main()
