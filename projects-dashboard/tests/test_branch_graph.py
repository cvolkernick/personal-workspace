"""Tests for gitk-style branch graph collector."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

from branch_graph import collect_branch_graph  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
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
    return (proc.stdout or "").strip()


class TestBranchGraph(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="bg-")
        self.repo = Path(self._td.name) / "ws"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "master")
        (self.repo / "a.txt").write_text("1\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "init")
        # Feature branch with two commits
        _git(self.repo, "checkout", "-b", "feature/demo")
        (self.repo / "a.txt").write_text("2\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "feature work")
        (self.repo / "a.txt").write_text("3\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "more feature")
        # work branch from master
        _git(self.repo, "checkout", "master")
        _git(self.repo, "checkout", "-b", "work/treasury")
        (self.repo / "b.txt").write_text("t\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "treasury work")
        # merge feature into master for a merge node
        _git(self.repo, "checkout", "master")
        _git(self.repo, "merge", "feature/demo", "-m", "merge feature")

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_graph_ok_and_lanes(self) -> None:
        g = collect_branch_graph(self.repo, max_commits=50, include_remotes=False)
        self.assertTrue(g.get("ok"), g.get("error"))
        self.assertGreaterEqual(g["commit_count"], 4)
        self.assertGreaterEqual(g["lane_count"], 1)
        shas = {c["sha"] for c in g["commits"]}
        self.assertEqual(len(shas), g["commit_count"])
        for c in g["commits"]:
            self.assertIn("lane", c)
            self.assertIsInstance(c["lane"], int)
            self.assertGreaterEqual(c["lane"], 0)
            self.assertLess(c["lane"], g["lane_count"])
        # edges point at known commits
        for e in g["edges"]:
            self.assertIn(e["from_sha"], shas)
            self.assertIn(e["to_sha"], shas)
            self.assertIn(e["kind"], ("first", "merge"))

    def test_branch_labels_on_tips(self) -> None:
        g = collect_branch_graph(self.repo, max_commits=50, include_remotes=False)
        labeled = {
            lb["name"]
            for c in g["commits"]
            for lb in (c.get("labels") or [])
        }
        self.assertIn("master", labeled)
        self.assertIn("work/treasury", labeled)
        self.assertIn("feature/demo", labeled)

    def test_refs_inventory(self) -> None:
        g = collect_branch_graph(self.repo, max_commits=20, include_remotes=False)
        names = {r["name"] for r in g["refs"]}
        self.assertIn("master", names)
        self.assertIn("work/treasury", names)
        self.assertTrue(any(r.get("is_work") for r in g["refs"]))

    def test_newest_first_with_dates(self) -> None:
        """Graph walks newest → oldest (row 0 = top = most recent)."""
        g = collect_branch_graph(self.repo, max_commits=50, include_remotes=False)
        self.assertTrue(g.get("ok"), g.get("error"))
        commits = g["commits"]
        self.assertGreaterEqual(len(commits), 2)
        for c in commits:
            self.assertIn("date", c)
            self.assertTrue(c["date"], msg="each commit needs a committer timestamp")
            # ISO-8601 from git %cI
            self.assertRegex(c["date"], r"^\d{4}-\d{2}-\d{2}T")
        # Strict newest-first by committer date
        dates = [c["date"] for c in commits]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual(commits[0]["row"], 0)


if __name__ == "__main__":
    unittest.main()
