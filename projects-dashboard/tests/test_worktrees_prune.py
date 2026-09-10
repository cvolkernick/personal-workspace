"""Tests for worktree prune classification helpers."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

import worktrees as wt


class TestMonorepoRoot(unittest.TestCase):
    def test_monorepo_root_returns_path(self) -> None:
        root = wt.monorepo_root()
        self.assertTrue(isinstance(root, Path))
        self.assertTrue(str(root))


class TestFccLiveTreeMain(unittest.TestCase):
    def test_env_true(self) -> None:
        with mock.patch.dict("os.environ", {"FCC_LIVE_TREE": "main"}, clear=False):
            self.assertTrue(wt.fcc_live_tree_is_main())

    def test_env_unset(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "FCC_LIVE_TREE"}
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertFalse(wt.fcc_live_tree_is_main())

    def test_ensure_skips_treasury(self) -> None:
        with mock.patch.dict("os.environ", {"FCC_LIVE_TREE": "main"}, clear=False):
            r = wt.ensure_area("treasury")
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("status"), "skipped_fcc_live_main")


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
