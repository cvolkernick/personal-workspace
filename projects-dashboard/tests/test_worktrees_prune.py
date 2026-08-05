"""Tests for worktree prune classification helpers."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import worktrees as wt


class TestMonorepoRoot(unittest.TestCase):
    def test_monorepo_root_returns_path(self) -> None:
        root = wt.monorepo_root()
        self.assertTrue(isinstance(root, Path))
        self.assertTrue(str(root))


class TestClassifyMain(unittest.TestCase):
    def test_main_never_pruned(self) -> None:
        main = wt.monorepo_root()
        c = wt.classify_worktree({"path": str(main), "branch": "refs/heads/master"})
        self.assertTrue(c["is_main"])
        report = wt.prune_stale(apply=False)
        for r in report["results"]:
            if r.get("is_main"):
                self.assertEqual(r["action"], "keep")


if __name__ == "__main__":
    unittest.main()
