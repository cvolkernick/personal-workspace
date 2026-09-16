#!/usr/bin/env python3
"""workspace_sync attaches live FCC when another worktree owns work/treasury (#561)."""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYNC_SH = ROOT / "deploy" / "workspace_sync.sh"
MAP_PY = ROOT / "deploy" / "product_branch_map.py"

GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env=GIT_ENV,
    )
    return (proc.stdout or "").strip()


class TestAttachReleasesOtherWorktree(unittest.TestCase):
    def test_detached_main_attaches_after_releasing_treasury_worktree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ws-attach-") as td:
            td_path = Path(td)
            repo = td_path / "main"
            repo.mkdir()
            _git(repo, "init", "-b", "master")
            _git(repo, "config", "user.email", "t@example.com")
            _git(repo, "config", "user.name", "Test")
            (repo / "README").write_text("x\n", encoding="utf-8")
            _git(repo, "add", "README")
            _git(repo, "commit", "-m", "init")
            _git(repo, "checkout", "-b", "work/treasury")
            bare = td_path / "remote.git"
            _git(td_path, "clone", "--bare", str(repo), str(bare))
            _git(repo, "remote", "add", "origin", str(bare))
            _git(repo, "push", "-u", "origin", "work/treasury")
            _git(repo, "push", "-u", "origin", "master")

            other = td_path / "treasury-wt"
            _git(repo, "checkout", "master")
            _git(repo, "worktree", "add", str(other), "work/treasury")
            _git(repo, "checkout", "--detach")

            deploy = repo / "deploy"
            deploy.mkdir()
            shutil.copy(MAP_PY, deploy / "product_branch_map.py")
            shutil.copy(SYNC_SH, deploy / "workspace_sync.sh")

            env = {
                **GIT_ENV,
                "WORKSPACE_DIR": str(repo),
                "SYNC_BRANCH": "work/treasury",
                "HOME": str(td_path),
                "WORKSPACE_SYNC_KEEP_REMOTE": "1",
            }
            proc = subprocess.run(
                ["bash", str(deploy / "workspace_sync.sh")],
                capture_output=True,
                text=True,
                env=env,
            )
            combined = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, combined)
            self.assertEqual(_git(repo, "branch", "--show-current"), "work/treasury")
            other_br = _git(other, "branch", "--show-current")
            self.assertEqual(other_br, "")
            self.assertIn("releasing work/treasury", combined)
            hook = repo / ".git" / "hooks" / "pre-commit"
            self.assertTrue(hook.is_file(), "live clone must refuse local commits")
            hook_rc = subprocess.run(
                ["bash", str(hook)], capture_output=True, text=True
            )
            self.assertNotEqual(hook_rc.returncode, 0)
            self.assertIn("#661", hook_rc.stderr)
            served = td_path / ".config" / "personal-workspace" / "last_served_origin_sha"
            self.assertTrue(served.is_file())
            self.assertEqual(served.read_text(encoding="utf-8").strip(), _git(repo, "rev-parse", "HEAD"))


class TestSyncScriptGuards(unittest.TestCase):
    def test_does_not_overlay_master_fitdash(self) -> None:
        text = SYNC_SH.read_text(encoding="utf-8")
        self.assertNotIn("checkout \"$REMOTE/master\" -- resistance-dashboard", text)
        self.assertNotIn("checkout origin/master -- fitness", text)
        self.assertIn("last_served_origin_sha", text)
        self.assertIn("install_live_commit_hook", text)
        self.assertIn("refuses local commits", text)

    def test_journal_preserve_globs_skip_python(self) -> None:
        text = SYNC_SH.read_text(encoding="utf-8")
        self.assertIn("-name '*journal.md'", text)
        self.assertIn("-name '*journal.jsonl'", text)
        self.assertNotRegex(text, r"-name '\*journal\*'")
        self.assertIn("! -name '*.py'", text)
        self.assertIn("! -name '*.pyc'", text)
        self.assertIn("#661", text)

        names = (
            "fund_manager_journal.md",
            "fund_manager_journal.jsonl",
            "fund_manager_decisions.jsonl",
            "fund_manager_journal_sync.py",
            "fund_manager_journal_sync.pyc",
            "secrets.json",
            "treasury_latest.json",
        )
        journal_ok = {"fund_manager_journal.md", "fund_manager_journal.jsonl"}
        always_ok = {"secrets.json", "treasury_latest.json"}
        for name in names:
            if name.endswith(".py") or name.endswith(".pyc"):
                matched = False
            else:
                matched = any(
                    fnmatch.fnmatch(name, pat)
                    for pat in ("*journal.md", "*journal.jsonl", "*_latest.json", "secrets.json")
                )
            if name in journal_ok or name in always_ok:
                self.assertTrue(matched, name)
            else:
                self.assertFalse(matched, name)

        with tempfile.TemporaryDirectory(prefix="ws-journal-glob-") as td:
            root = Path(td)
            for d in (
                "treasury",
                "investment",
                "ops",
                "fitness",
                "financial-command",
                "iot",
            ):
                (root / d).mkdir()
            (root / "treasury" / "fund_manager_journal_sync.py").write_text("x\n")
            (root / "treasury" / "fund_manager_journal_sync.pyc").write_bytes(b"x")
            (root / "investment" / "fund_manager_journal.md").write_text("x\n")
            (root / "investment" / "fund_manager_journal.jsonl").write_text("{}\n")
            (root / "investment" / "fund_manager_decisions.jsonl").write_text("{}\n")
            proc = subprocess.run(
                [
                    "bash",
                    "-c",
                    "find treasury investment ops fitness financial-command iot -maxdepth 3 "
                    "\\( -name '*journal.md' -o -name '*journal.jsonl' "
                    "-o -name '*_latest.json' -o -name 'secrets.json' \\) "
                    "! -name '*.py' ! -name '*.pyc'",
                ],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=True,
            )
            found = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
            self.assertIn("investment/fund_manager_journal.md", found)
            self.assertIn("investment/fund_manager_journal.jsonl", found)
            self.assertNotIn("investment/fund_manager_decisions.jsonl", found)
            self.assertNotIn("treasury/fund_manager_journal_sync.py", found)
            self.assertNotIn("treasury/fund_manager_journal_sync.pyc", found)


if __name__ == "__main__":
    unittest.main()
