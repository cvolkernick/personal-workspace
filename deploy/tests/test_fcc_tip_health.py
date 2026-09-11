#!/usr/bin/env python3
"""Tests for deploy/fcc_tip_health.py (issues #562, #628, #630)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = ROOT / "deploy" / "fcc_tip_health.py"

GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _load():
    spec = importlib.util.spec_from_file_location("fcc_tip_health", MOD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


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


def _repo():
    td = tempfile.TemporaryDirectory(prefix="fcc-tip-")
    root = Path(td.name)
    repo = root / "live"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "init")
    _git(repo, "checkout", "-b", "work/treasury")
    fcc = repo / "financial-command"
    fcc.mkdir()
    (fcc / "current-branch.txt").write_text("work/treasury\n", encoding="utf-8")
    _git(repo, "add", "financial-command/current-branch.txt")
    _git(repo, "commit", "-m", "stamp")
    bare = root / "remote.git"
    _git(root, "clone", "--bare", str(repo), str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-u", "origin", "work/treasury")
    _git(repo, "push", "-u", "origin", "master")
    return td, repo


class TestNoGitMutation(unittest.TestCase):
    def test_source_has_no_reset_or_checkout(self) -> None:
        src = MOD_PATH.read_text(encoding="utf-8")
        for needle in (
            "reset --hard",
            "checkout -B",
            "checkout -b",
            "git merge",
            "SYNC_BRANCH=master",
            "work/holistic",
        ):
            if needle == "work/holistic":
                self.assertIn(needle, src)
                continue
            self.assertNotIn(needle, src)
        self.assertIn("Never mutates git", src)
        self.assertIn("rev-parse", src)
        self.assertIn("unknown", src)


class TestInspect(unittest.TestCase):
    def test_healthy_attached_tip(self) -> None:
        td, repo = _repo()
        try:
            result = M.inspect(repo)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["outcome"], "ok")
            self.assertEqual(result["kind"], "healthy")
            self.assertEqual(result["attached"], "work/treasury")
            self.assertEqual(result["current_branch_txt"], "work/treasury")
            self.assertEqual(result["head"], result["origin_sha"])
        finally:
            td.cleanup()

    def test_detached_is_violation(self) -> None:
        td, repo = _repo()
        try:
            _git(repo, "checkout", "--detach")
            result = M.inspect(repo)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["outcome"], "violation")
            self.assertEqual(result["kind"], "drift")
            self.assertTrue(any("detached" in m for m in result["mismatches"]))
            self.assertEqual(_git(repo, "branch", "--show-current"), "")
        finally:
            td.cleanup()

    def test_wrong_stamp_is_violation(self) -> None:
        td, repo = _repo()
        try:
            (repo / "financial-command" / "current-branch.txt").write_text(
                "master\n", encoding="utf-8"
            )
            result = M.inspect(repo)
            self.assertEqual(result["outcome"], "violation")
            self.assertTrue(
                any("current-branch.txt" in m for m in result["mismatches"])
            )
        finally:
            td.cleanup()

    def test_missing_stamp_is_unknown_not_drift(self) -> None:
        td, repo = _repo()
        try:
            (repo / "financial-command" / "current-branch.txt").unlink()
            result = M.inspect(repo)
            self.assertEqual(result["outcome"], "unknown")
            self.assertEqual(result["unknown_reason"], "current-branch.txt missing")
            self.assertNotEqual(result["kind"], "drift")
        finally:
            td.cleanup()

    def test_sha_drift_is_violation(self) -> None:
        td, repo = _repo()
        try:
            (repo / "README").write_text("y\n", encoding="utf-8")
            _git(repo, "add", "README")
            _git(repo, "commit", "-m", "ahead")
            result = M.inspect(repo)
            self.assertEqual(result["outcome"], "violation")
            self.assertEqual(result["kind"], "drift")
            self.assertTrue(any("HEAD" in m for m in result["mismatches"]))
        finally:
            td.cleanup()

    def test_not_a_repo_is_unknown_not_drift(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            result = M.inspect(ws)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["outcome"], "unknown")
            self.assertEqual(result["unknown_reason"], "not a git repository")
            self.assertEqual(result["kind"], "unknown")
            self.assertEqual(result["attached"], "")
            self.assertEqual(result["head"], "")
            self.assertTrue(
                any("not a git repository" in m for m in result["mismatches"])
            )
            self.assertFalse(
                any(m.startswith("attached=") for m in result["mismatches"])
            )
            self.assertIn(str(ws.resolve()), result["workspace"])

    def test_broken_worktree_gitdir_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "live"
            ws.mkdir()
            (ws / ".git").write_text("gitdir: /no/such/gitdir\n", encoding="utf-8")
            result = M.inspect(ws)
            self.assertEqual(result["outcome"], "unknown")
            self.assertEqual(result["unknown_reason"], "not a git repository")
            self.assertTrue(
                any("gitdir" in m.lower() for m in result["mismatches"]),
                result["mismatches"],
            )

    def test_head_unreadable_is_unknown_not_drift(self) -> None:
        td, repo = _repo()
        try:
            (repo / ".git" / "HEAD").write_text(
                "ref: refs/heads/does-not-exist\n", encoding="utf-8"
            )
            result = M.inspect(repo)
            self.assertEqual(result["outcome"], "unknown")
            self.assertEqual(result["unknown_reason"], "HEAD unreadable")
            self.assertFalse(
                any(m.startswith("attached=") for m in result["mismatches"]),
                result["mismatches"],
            )
        finally:
            td.cleanup()

    def test_origin_unreadable_is_unknown(self) -> None:
        td, repo = _repo()
        try:
            _git(repo, "remote", "remove", "origin")
            result = M.inspect(repo)
            self.assertEqual(result["outcome"], "unknown")
            self.assertEqual(result["unknown_reason"], "origin ref unreadable")
        finally:
            td.cleanup()

    def test_ignores_git_dir_env(self) -> None:
        td, repo = _repo()
        try:
            with mock.patch.dict(os.environ, {"GIT_DIR": "/tmp/does-not-exist-fcc-tip"}):
                result = M.inspect(repo)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["outcome"], "ok")
        finally:
            td.cleanup()


class TestNtfyOnce(unittest.TestCase):
    def test_healthy_skips(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            out = M.ntfy_mismatch(
                {"ok": True, "outcome": "ok", "mismatches": []},
                workspace=Path(td),
                state_path=state,
            )
            self.assertEqual(out.get("skipped"), "healthy")
            self.assertFalse(state.exists())

    def test_cooldown_second_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            result = {
                "ok": False,
                "outcome": "violation",
                "kind": "drift",
                "mismatches": ["detached"],
                "expected_branch": "work/treasury",
                "head": "abc",
                "origin_sha": "abc",
                "attached": "detached",
                "current_branch_txt": "work/treasury",
            }
            first = M.ntfy_mismatch(
                result,
                workspace=Path(td),
                state_path=state,
                dry_run=True,
            )
            self.assertEqual(first.get("skipped"), "dry-run")
            self.assertFalse(state.exists(), "dry-run must not consume ntfy cooldown")
            M._mark_notified(state, result)
            second = M.ntfy_mismatch(
                result,
                workspace=Path(td),
                state_path=state,
                dry_run=True,
            )
            self.assertEqual(second.get("skipped"), "cooldown")

    def test_unknown_title_is_check_failed_not_drift(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            result = M.inspect(ws)
            out = M.ntfy_mismatch(
                result,
                workspace=ws,
                state_path=ws / "state.json",
                dry_run=True,
            )
            title = out["title"].lower()
            self.assertIn("check failed", title)
            self.assertIn("not a git repository", title)
            self.assertNotIn("git tip drift", title)
            self.assertIn(str(ws.resolve()), out["text"])
            self.assertIn("outcome=unknown", out["text"])
            self.assertIn("CHECK FAILED", out["text"])

    def test_violation_title_names_expected_and_found(self) -> None:
        td, repo = _repo()
        try:
            _git(repo, "checkout", "--detach")
            result = M.inspect(repo)
            out = M.ntfy_mismatch(
                result,
                workspace=repo,
                state_path=repo / "state.json",
                dry_run=True,
            )
            title = out["title"]
            self.assertIn("git tip drift", title)
            self.assertIn("expected work/treasury", title)
            self.assertIn("found detached", title)
            self.assertNotIn("check failed", title.lower())
        finally:
            td.cleanup()


class TestCli(unittest.TestCase):
    def test_main_healthy_exit_0(self) -> None:
        td, repo = _repo()
        try:
            with tempfile.TemporaryDirectory() as sd:
                code = M.main(
                    [
                        "--workspace",
                        str(repo),
                        "--no-fetch",
                        "--dry-run",
                        "--state",
                        str(Path(sd) / "s.json"),
                    ]
                )
            self.assertEqual(code, 0)
        finally:
            td.cleanup()

    def test_main_violation_exit_1_does_not_reattach(self) -> None:
        td, repo = _repo()
        try:
            _git(repo, "checkout", "--detach")
            with tempfile.TemporaryDirectory() as sd:
                code = M.main(
                    [
                        "--workspace",
                        str(repo),
                        "--no-fetch",
                        "--dry-run",
                        "--state",
                        str(Path(sd) / "s.json"),
                    ]
                )
            self.assertEqual(code, 1)
            self.assertEqual(_git(repo, "branch", "--show-current"), "")
        finally:
            td.cleanup()

    def test_main_unknown_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            code = M.main(
                [
                    "--workspace",
                    td,
                    "--no-fetch",
                    "--dry-run",
                    "--state",
                    str(Path(td) / "s.json"),
                ]
            )
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
