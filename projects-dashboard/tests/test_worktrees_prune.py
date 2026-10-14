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


PORCELAIN = """worktree /srv/pw
HEAD 1111111111111111111111111111111111111111
branch refs/heads/work/treasury

worktree /srv/pw-worktrees/workflow-master
HEAD 2222222222222222222222222222222222222222
branch refs/heads/master
locked serves live user units; do not prune

worktree /srv/pw-worktrees/feat-old
HEAD 3333333333333333333333333333333333333333
branch refs/heads/feat/old
"""


class TestLockedWorktrees(unittest.TestCase):
    """#1187: a locked worktree is 'keep', never a failing prune."""

    def test_porcelain_parses_lock_and_reason(self) -> None:
        with mock.patch.object(wt, "_run", return_value=(0, PORCELAIN, "")):
            items = wt.list_worktrees()
        locked = [i for i in items if i.get("locked")]
        self.assertEqual([i["path"] for i in locked], ["/srv/pw-worktrees/workflow-master"])
        self.assertEqual(locked[0]["lock_reason"], "serves live user units; do not prune")
        self.assertFalse(items[2].get("locked"))

    def _prune(self, *, apply: bool, force: bool = False) -> dict:
        def fake_classify(item: dict) -> dict:
            merged = item["path"] != "/srv/pw"
            return {
                "path": item["path"], "branch": item.get("branch", ""), "is_main": item["path"] == "/srv/pw",
                "area": None, "dirty": False, "remote_ok": True, "merged_master": merged,
                "expected_branch": None, "wrong_area_branch": False,
                "stale_feature": merged, "repair_area": False,
                "locked": bool(item.get("locked")), "lock_reason": item.get("lock_reason") or "",
            }

        removed: list[str] = []

        def fake_subprocess(cmd, **kw):
            removed.append(cmd[-1])
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(wt, "_run", side_effect=lambda *a, **k: (0, PORCELAIN, "") if a[:2] == ("worktree", "list") else (0, "", "")), \
             mock.patch.object(wt, "classify_worktree", side_effect=fake_classify), \
             mock.patch.object(wt, "monorepo_root", return_value=Path("/srv/pw")), \
             mock.patch.object(wt.subprocess, "run", side_effect=fake_subprocess):
            report = wt.prune_stale(apply=apply, force=force)
        report["removed"] = removed
        return report

    def test_locked_is_keep_and_never_removed(self) -> None:
        for force in (False, True):
            report = self._prune(apply=True, force=force)
            by = {r["path"]: r for r in report["results"]}
            locked = by["/srv/pw-worktrees/workflow-master"]
            self.assertEqual(locked["action"], "keep")
            self.assertIn("locked", locked["reason"])
            self.assertNotIn("/srv/pw-worktrees/workflow-master", report["removed"])
            self.assertTrue(report["ok"], report)
            # Unlocked merged feature worktree is still pruned.
            self.assertEqual(by["/srv/pw-worktrees/feat-old"]["action"], "prune")
            self.assertIn("/srv/pw-worktrees/feat-old", report["removed"])


if __name__ == "__main__":
    unittest.main()
