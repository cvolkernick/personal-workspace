"""Tests for branch workflow + session index (temp git repo)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

from git_workflow import (  # noqa: E402
    branch_name_for_area,
    branch_worktree_path,
    collect_branch_status,
    dirty_paths,
    is_durable_path,
    list_worktrees,
    parse_porcelain_path,
    protect_work,
    resolve_protect_mode,
    start_work,
)
from session_backup import build_session_index, write_session_index  # noqa: E402


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


class TestGitWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="gw-")
        self.repo = Path(self._td.name) / "ws"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "master")
        (self.repo / "treasury").mkdir()
        (self.repo / "treasury" / "a.txt").write_text("1\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "init")
        # bare remote for push
        self.bare = Path(self._td.name) / "remote.git"
        _git(Path(self._td.name), "clone", "--bare", str(self.repo), str(self.bare))
        _git(self.repo, "remote", "add", "origin", str(self.bare))
        _git(self.repo, "push", "-u", "origin", "master")

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_branch_name(self) -> None:
        self.assertEqual(branch_name_for_area("Projects Dashboard"), "work/projects-dashboard")
        # Finance TLDs share work/treasury
        self.assertEqual(branch_name_for_area("financial-command"), "work/treasury")
        self.assertEqual(branch_name_for_area("investment"), "work/treasury")
        self.assertEqual(branch_name_for_area("treasury"), "work/treasury")
        self.assertEqual(branch_name_for_area("iot"), "work/iot")

    def test_parse_porcelain_paths(self) -> None:
        self.assertEqual(parse_porcelain_path(" M ops/backlog/items.json"), "ops/backlog/items.json")
        self.assertEqual(parse_porcelain_path("M  ops/backlog/items.json"), "ops/backlog/items.json")
        self.assertEqual(parse_porcelain_path("M ops/backlog/items.json"), "ops/backlog/items.json")
        self.assertEqual(parse_porcelain_path("?? new.txt"), "new.txt")
        self.assertEqual(
            parse_porcelain_path("R  old.txt -> new.txt"),
            "new.txt",
        )

    def test_dirty_paths_single_space_status(self) -> None:
        (self.repo / "treasury" / "c.txt").write_text("3\n", encoding="utf-8")
        # ensure porcelain is readable
        paths = dirty_paths(self.repo)
        self.assertTrue(any(p.endswith("c.txt") for p in paths), paths)

    def test_start_work_creates_branch(self) -> None:
        r = start_work("treasury", repo=self.repo)
        self.assertTrue(r["ok"])
        self.assertEqual(r["branch"], "work/treasury")
        self.assertTrue(r["created"])
        st = collect_branch_status(self.repo)
        self.assertEqual(st["current"], "work/treasury")

    def test_protect_switches_off_master_and_pushes(self) -> None:
        (self.repo / "treasury" / "b.txt").write_text("2\n", encoding="utf-8")
        # stay on master
        _git(self.repo, "checkout", "master")
        r = protect_work(
            self.repo,
            message="test protect",
            push=True,
            ensure_work_branch=True,
        )
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["committed"])
        self.assertEqual(r["branch"], "work/treasury")
        self.assertTrue(r["pushed"])
        # remote has branch
        refs = _git(self.bare, "branch")
        self.assertIn("work/treasury", refs)

    def test_protect_stays_when_branch_in_other_worktree(self) -> None:
        """If work/<area> is checked out elsewhere, commit on current branch."""
        (self.repo / "projects-dashboard").mkdir(exist_ok=True)
        (self.repo / "projects-dashboard" / "x.txt").write_text("x\n", encoding="utf-8")
        _git(self.repo, "checkout", "-b", "work/holistic")
        # Create branch that will be "busy" in another worktree
        _git(self.repo, "branch", "work/projects-dashboard")
        wt = Path(self._td.name) / "other-wt"
        _git(self.repo, "worktree", "add", str(wt), "work/projects-dashboard")
        # Dirty projects-dashboard path while on work/holistic
        (self.repo / "projects-dashboard" / "y.txt").write_text("y\n", encoding="utf-8")
        r = protect_work(
            self.repo,
            message="test protect worktree busy",
            push=True,
            ensure_work_branch=True,
        )
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["committed"])
        # Must remain on holistic (cannot checkout projects-dashboard)
        self.assertEqual(r["branch"], "work/holistic")
        self.assertTrue(
            any("worktree" in str(a).lower() or "stayed on" in str(a).lower()
                for a in (r.get("branch_actions") or [])),
            r.get("branch_actions"),
        )

    def test_durable_path_classifier(self) -> None:
        self.assertTrue(is_durable_path("treasury/snapshots/fund_manager_latest.json"))
        self.assertTrue(is_durable_path("ops/session-index/latest.json"))
        self.assertTrue(is_durable_path("investment/fund_manager_journal.md"))
        self.assertTrue(is_durable_path("ops/backlog/items.json"))
        self.assertFalse(is_durable_path("treasury/fund_manager.py"))
        self.assertFalse(is_durable_path("treasury/fund_manager_bp_poll.sh"))
        self.assertFalse(is_durable_path("projects-dashboard/git_workflow.py"))

    def test_resolve_mode(self) -> None:
        self.assertEqual(resolve_protect_mode(None, None), "auto")
        self.assertEqual(resolve_protect_mode(None, ""), "auto")
        self.assertEqual(resolve_protect_mode(None, "feat: real change"), "full")
        self.assertEqual(resolve_protect_mode("auto", "feat: x"), "auto")
        self.assertEqual(resolve_protect_mode("full", None), "full")

    def test_auto_skips_product_code(self) -> None:
        """Bare protect (auto) must not commit .py — only durable paths."""
        start_work("treasury", repo=self.repo)
        (self.repo / "treasury" / "fund_manager.py").write_text("print(1)\n", encoding="utf-8")
        snap = self.repo / "treasury" / "snapshots"
        snap.mkdir(parents=True)
        (snap / "latest.json").write_text('{"ok":true}\n', encoding="utf-8")
        r = protect_work(self.repo, message=None, push=True, mode="auto")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["committed"], r)
        self.assertEqual(r.get("mode"), "auto")
        staged = r.get("staged") or []
        self.assertTrue(any("snapshots" in s for s in staged), staged)
        self.assertFalse(any(s.endswith(".py") for s in staged), staged)
        # product still dirty
        dirty = dirty_paths(self.repo)
        self.assertTrue(any(p.endswith("fund_manager.py") for p in dirty), dirty)

    def test_auto_refuses_feature_branch(self) -> None:
        _git(self.repo, "checkout", "-b", "fix/ntfy-quiet")
        snap = self.repo / "treasury" / "snapshots"
        snap.mkdir(parents=True)
        (snap / "x.json").write_text("{}\n", encoding="utf-8")
        r = protect_work(self.repo, mode="auto", push=True)
        self.assertTrue(r["ok"], r)
        self.assertFalse(r.get("committed"), r)
        self.assertIn("refuses", (r.get("message") or "").lower())


class TestSessionIndex(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="si-")
        self.root = Path(self._td.name)
        self.ws = self.root / "ws"
        self.ws.mkdir()
        self.grok = self.root / "grok"
        sid = "019f0000-aaaa-bbbb-cccc-ddddeeeeffff"
        sdir = self.grok / "sessions" / "%2Fws" / sid
        sdir.mkdir(parents=True)
        (sdir / "summary.json").write_text(
            json.dumps(
                {
                    "generated_title": "Hello",
                    "last_active_at": "2026-07-17T00:00:00Z",
                    "info": {"id": sid, "cwd": str(self.ws)},
                    "num_chat_messages": 3,
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_build_index(self) -> None:
        idx = build_session_index(self.grok)
        self.assertEqual(idx["count"], 1)
        self.assertEqual(idx["sessions"][0]["title"], "Hello")
        self.assertIn("grok --resume", idx["sessions"][0]["resume_cmd"])

    def test_write_index(self) -> None:
        r = write_session_index(repo=self.ws, grok_home=self.grok, commit=False)
        self.assertTrue(r["ok"])
        latest = self.ws / "ops" / "session-index" / "latest.json"
        self.assertTrue(latest.is_file())
        data = json.loads(latest.read_text(encoding="utf-8"))
        self.assertEqual(data["count"], 1)


if __name__ == "__main__":
    unittest.main()
