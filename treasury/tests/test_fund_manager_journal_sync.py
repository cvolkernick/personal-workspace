"""Commit + push fund-manager journal after each run (#737)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.fund_manager_journal_sync import (  # noqa: E402
    JOURNAL_REL,
    JSONL_REL,
    _abort_rebase_if_needed,
    _git,
    infer_as_of,
    infer_kind,
    journal_commit_message,
    producer_allowed,
    sync_journal,
)


def _git_cwd(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return (proc.stdout or "").strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestJournalCommitMessage(unittest.TestCase):
    def test_convention(self) -> None:
        when = datetime(2026, 9, 14, 17, 45, tzinfo=timezone.utc)
        self.assertEqual(
            journal_commit_message("HOLD", when),
            "journal: fund-manager hold 2026-09-14 17:45",
        )
        self.assertEqual(
            journal_commit_message("Deploy $25", when),
            "journal: fund-manager deploy-25 2026-09-14 17:45",
        )

    def test_infer_kind_from_jsonl_then_journal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write(
                repo / JSONL_REL,
                json.dumps({"kind": "rebalance", "summary": "x"}) + "\n",
            )
            self.assertEqual(infer_kind(repo), "rebalance")
            _write(repo / JSONL_REL, "not-json\n")
            _write(
                repo / JOURNAL_REL,
                "# Fund manager journal\n\n## 2026-09-14T12:00:00 — deploy\n",
            )
            self.assertEqual(infer_kind(repo), "deploy")

    def test_infer_as_of_from_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write(
                repo / JSONL_REL,
                json.dumps({"kind": "hold", "as_of": "2026-09-14T12:34:00Z"}) + "\n",
            )
            self.assertEqual(infer_as_of(repo), "2026-09-14T12:34:00Z")


class TestProducerGate(unittest.TestCase):
    def test_prism_tag_allows(self) -> None:
        with mock.patch.dict(
            os.environ, {"FCC_HOST_TAG": "prism", "FM_JOURNAL_SYNC": ""}, clear=False
        ):
            os.environ.pop("FM_JOURNAL_SYNC", None)
            self.assertTrue(producer_allowed())

    def test_mac_default_denies(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in {"FCC_HOST_TAG", "FM_JOURNAL_SYNC"}}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(producer_allowed())

    def test_explicit_off_wins(self) -> None:
        with mock.patch.dict(
            os.environ, {"FCC_HOST_TAG": "prism", "FM_JOURNAL_SYNC": "0"}, clear=False
        ):
            self.assertFalse(producer_allowed())


class TestRebaseAbortGuard(unittest.TestCase):
    def test_does_not_abort_when_no_rebase(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            calls: list[tuple] = []

            def fake_git(_repo, *args, timeout=60.0):
                calls.append(args)
                if args[:2] == ("rev-parse", "--git-path"):
                    return 0, str(repo / args[2]), ""
                return 0, "", ""

            with mock.patch(
                "treasury.fund_manager_journal_sync._git", side_effect=fake_git
            ):
                _abort_rebase_if_needed(repo)
            self.assertFalse(any(a[:2] == ("rebase", "--abort") for a in calls))


class TestForcePushGuard(unittest.TestCase):
    def test_force_flags_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            for args in (
                ("push", "--force", "origin", "HEAD"),
                ("push", "--force-with-lease", "origin", "HEAD"),
                ("push", "-f", "origin", "HEAD"),
            ):
                code, _, err = _git(repo, *args)
                self.assertEqual(code, 1, args)
                self.assertIn("force-push forbidden", err)


class TestSyncJournal(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="fm-jnl-")
        self.repo = Path(self._td.name) / "ws"
        self.repo.mkdir()
        _git_cwd(self.repo, "init", "-b", "work/treasury")
        _git_cwd(self.repo, "config", "user.name", "Test")
        _git_cwd(self.repo, "config", "user.email", "t@example.com")
        _write(self.repo / JOURNAL_REL, "# Fund manager journal\n\n")
        _write(self.repo / JSONL_REL, "")
        _write(self.repo / "other.txt", "keep\n")
        _git_cwd(self.repo, "add", ".")
        _git_cwd(self.repo, "commit", "-m", "init")
        self.bare = Path(self._td.name) / "remote.git"
        _git_cwd(Path(self._td.name), "clone", "--bare", str(self.repo), str(self.bare))
        _git_cwd(self.repo, "remote", "add", "origin", str(self.bare))
        _git_cwd(self.repo, "push", "-u", "origin", "work/treasury")
        self.env = {
            "FM_JOURNAL_SYNC": "1",
            "FM_JOURNAL_SYNC_IN_TEST": "1",
            "FCC_HOST_TAG": "prism",
            "FM_JOURNAL_BRANCH": "work/treasury",
            "PYTEST_CURRENT_TEST": os.environ.get("PYTEST_CURRENT_TEST") or "1",
        }

    def tearDown(self) -> None:
        self._td.cleanup()

    def _sync(self, **kwargs):
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "treasury.fund_manager_journal_sync.post_ops_github",
            return_value={"ok": True, "posted": False, "skipped": "test"},
        ) as post:
            out = sync_journal(repo=self.repo, notify=kwargs.pop("notify", True), **kwargs)
        return out, post

    def test_pytest_skip_without_override(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PYTEST_CURRENT_TEST": "test_foo",
                "FM_JOURNAL_SYNC": "1",
                "FCC_HOST_TAG": "prism",
            },
            clear=False,
        ):
            os.environ.pop("FM_JOURNAL_SYNC_IN_TEST", None)
            out = sync_journal(repo=self.repo, notify=False)
        self.assertEqual(out.get("skipped"), "pytest")
        self.assertFalse(out.get("committed"))

    def test_not_producer_skip(self) -> None:
        env = {
            **self.env,
            "FM_JOURNAL_SYNC": "0",
            "FCC_HOST_TAG": "macbook",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            out = sync_journal(repo=self.repo, notify=False)
        self.assertEqual(out.get("skipped"), "not-producer")

    def test_clean_skip(self) -> None:
        out, _ = self._sync()
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("skipped"), "clean")
        self.assertFalse(out.get("committed"))
        self.assertFalse(out.get("pushed"))

    def test_dry_run_does_not_commit(self) -> None:
        _write(
            self.repo / JOURNAL_REL,
            "# Fund manager journal\n\n## 2026-09-14T17:00:00 — hold\n",
        )
        out, _ = self._sync(dry_run=True, kind="hold")
        self.assertEqual(out.get("skipped"), "dry-run")
        self.assertIn(JOURNAL_REL, out.get("dirty") or [])
        self.assertTrue(out.get("message", "").startswith("journal: fund-manager hold "))
        status = _git_cwd(self.repo, "status", "--porcelain", "--", JOURNAL_REL)
        self.assertTrue(status.strip())

    def test_commit_and_push_journal_only(self) -> None:
        _write(self.repo / "other.txt", "dirty leftover\n")
        _write(
            self.repo / JOURNAL_REL,
            "# Fund manager journal\n\n## 2026-09-14T17:00:00 — hold\n**Summary:** in band\n",
        )
        _write(
            self.repo / JSONL_REL,
            json.dumps({"kind": "hold", "summary": "in band"}) + "\n",
        )
        out, post = self._sync(kind="hold", as_of="2026-09-14T17:00:00+00:00")
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("committed"), out)
        self.assertTrue(out.get("pushed"), out)
        self.assertEqual(out.get("message"), "journal: fund-manager hold 2026-09-14 17:00")
        self.assertFalse(post.called)
        subject = _git_cwd(self.repo, "log", "-1", "--format=%s")
        self.assertEqual(subject, "journal: fund-manager hold 2026-09-14 17:00")
        files = _git_cwd(self.repo, "show", "--name-only", "--format=", "HEAD")
        self.assertIn(JOURNAL_REL, files)
        self.assertIn(JSONL_REL, files)
        self.assertNotIn("other.txt", files)
        # leftover dirty file must survive --autostash pull/push
        self.assertIn("dirty leftover", (self.repo / "other.txt").read_text(encoding="utf-8"))
        remote_tip = subprocess.run(
            ["git", "--git-dir", str(self.bare), "log", "-1", "--format=%s", "work/treasury"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(remote_tip, subject)

    def test_rebase_then_push_when_origin_is_ahead(self) -> None:
        peer = Path(self._td.name) / "peer"
        _git_cwd(Path(self._td.name), "clone", str(self.bare), str(peer))
        _git_cwd(peer, "config", "user.name", "Peer")
        _git_cwd(peer, "config", "user.email", "peer@example.com")
        _write(peer / "other.txt", "remote moved\n")
        _git_cwd(peer, "add", "other.txt")
        _git_cwd(peer, "commit", "-m", "journal: fund-manager hold 2026-09-14 16:00")
        _git_cwd(peer, "push", "origin", "work/treasury")

        _write(
            self.repo / JOURNAL_REL,
            "# Fund manager journal\n\n## 2026-09-14T17:00:00 — hold\n",
        )
        out, post = self._sync(kind="hold", as_of="2026-09-14T17:00:00+00:00")
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("committed"), out)
        self.assertTrue(out.get("pushed"), out)
        self.assertFalse(post.called)
        remote_log = subprocess.run(
            [
                "git",
                "--git-dir",
                str(self.bare),
                "log",
                "--format=%s",
                "work/treasury",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().splitlines()
        self.assertEqual(remote_log[0], "journal: fund-manager hold 2026-09-14 17:00")
        self.assertEqual(remote_log[1], "journal: fund-manager hold 2026-09-14 16:00")

    def test_empty_email_notifies(self) -> None:
        with mock.patch(
            "treasury.fund_manager_journal_sync._user_email", return_value=""
        ):
            out, post = self._sync()
        self.assertFalse(out.get("ok"))
        self.assertIn("user.email", out.get("error") or "")
        self.assertTrue(post.called)

    def test_mixed_unpushed_history_refuses_push(self) -> None:
        _write(self.repo / "other.txt", "product\n")
        _git_cwd(self.repo, "add", "other.txt")
        _git_cwd(self.repo, "commit", "-m", "feat: do not push me")
        _write(
            self.repo / JOURNAL_REL,
            "# Fund manager journal\n\n## 2026-09-14 — deploy\n",
        )
        out, post = self._sync(kind="deploy")
        self.assertFalse(out.get("ok"))
        self.assertTrue(out.get("committed"))
        self.assertFalse(out.get("pushed"))
        self.assertIn("mixed unpushed", out.get("error") or "")
        self.assertTrue(post.called)
        title, text = post.call_args[0][:2]
        self.assertIn("journal sync failed", title)
        self.assertIn("mixed unpushed", text)

    def test_push_failure_notifies_and_does_not_raise(self) -> None:
        _write(
            self.repo / JOURNAL_REL,
            "# Fund manager journal\n\n## 2026-09-14 — error\n",
        )
        real_git = __import__("treasury.fund_manager_journal_sync", fromlist=["_git"])._git

        def fake_git(repo, *args, timeout=60.0):
            if args and args[0] == "push":
                return 1, "", "remote rejected (auth)"
            if args[:2] == ("pull", "--rebase"):
                return 0, "", ""
            return real_git(repo, *args, timeout=timeout)

        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "treasury.fund_manager_journal_sync._git", side_effect=fake_git
        ), mock.patch(
            "treasury.fund_manager_journal_sync.post_ops_github",
            return_value={"ok": True, "posted": True, "issue": "701"},
        ) as post:
            out = sync_journal(repo=self.repo, kind="error", notify=True)
        self.assertFalse(out.get("ok"))
        self.assertTrue(out.get("committed"))
        self.assertFalse(out.get("pushed"))
        self.assertTrue(post.called)
        self.assertIn("remote rejected", out.get("error") or "")

    def test_wrong_branch_notifies(self) -> None:
        _git_cwd(self.repo, "checkout", "-b", "fix/something")
        out, post = self._sync()
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("skipped"), "wrong-branch")
        self.assertTrue(post.called)

    def test_cli_exit_zero_on_failure(self) -> None:
        from treasury.fund_manager_journal_sync import main

        with mock.patch(
            "treasury.fund_manager_journal_sync.sync_journal",
            return_value={"ok": False, "error": "boom"},
        ):
            rc = main(["--repo", str(self.repo), "--no-notify"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
