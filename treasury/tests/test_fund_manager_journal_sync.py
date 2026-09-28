"""Commit + push fund-manager journal after each run (#737)."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
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
    _git_blob_sha1,
    _insteadOf_config,
    _redact,
    _report_failure,
    infer_as_of,
    infer_kind,
    is_live_clone,
    journal_commit_message,
    live_clone_reason,
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


class TestGitAuthInsteadOf(unittest.TestCase):
    TOKEN = "ghs_testtokenABC"

    def _env_without_github(self) -> dict:
        drop = {"GITHUB_TOKEN", "GH_TOKEN", "BUZZ_BOARD_GITHUB_TOKEN"}
        return {k: v for k, v in os.environ.items() if k not in drop}

    def test_insteadOf_matches_workspace_sync(self) -> None:
        self.assertEqual(
            _insteadOf_config(self.TOKEN),
            "url.https://x-access-token:ghs_testtokenABC@github.com/.insteadOf=https://github.com/",
        )

    def test_network_git_uses_scheduler_env_insteadOf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / "workflow-scheduler.env"
            env_path.write_text(f'GITHUB_TOKEN="{self.TOKEN}"\n', encoding="utf-8")
            repo = Path(td) / "repo"
            repo.mkdir()
            captured: dict = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = list(cmd)
                captured["env"] = kwargs.get("env") or {}
                class P:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return P()

            with mock.patch.dict(os.environ, self._env_without_github(), clear=True), mock.patch(
                "treasury.pi_ops_alert.SCHEDULER_ENV", env_path
            ), mock.patch(
                "treasury.fund_manager_journal_sync.subprocess.run", side_effect=fake_run
            ):
                code, _, _ = _git(repo, "push", "origin", "HEAD:work/treasury")
            self.assertEqual(code, 0)
            cmd = captured["cmd"]
            self.assertEqual(cmd[0], "git")
            self.assertIn("-c", cmd)
            cfg = cmd[cmd.index("-c") + 1]
            self.assertEqual(cfg, _insteadOf_config(self.TOKEN))
            self.assertEqual(cmd[-3:], ["push", "origin", "HEAD:work/treasury"])
            self.assertEqual(captured["env"].get("GIT_TERMINAL_PROMPT"), "0")

    def test_pull_and_fetch_also_use_insteadOf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            seen: list[list[str]] = []

            def fake_run(cmd, **kwargs):
                seen.append(list(cmd))
                class P:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return P()

            env = {**self._env_without_github(), "GITHUB_TOKEN": self.TOKEN}
            with mock.patch.dict(os.environ, env, clear=True), mock.patch(
                "treasury.fund_manager_journal_sync.subprocess.run", side_effect=fake_run
            ):
                _git(repo, "pull", "--rebase", "origin", "work/treasury")
                _git(repo, "fetch", "origin")
            self.assertEqual(len(seen), 2)
            for cmd in seen:
                self.assertIn(_insteadOf_config(self.TOKEN), cmd)

    def test_local_git_does_not_inject_insteadOf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            captured: dict = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = list(cmd)
                class P:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return P()

            env = {**self._env_without_github(), "GITHUB_TOKEN": self.TOKEN}
            with mock.patch.dict(os.environ, env, clear=True), mock.patch(
                "treasury.fund_manager_journal_sync.subprocess.run", side_effect=fake_run
            ):
                _git(repo, "status", "--porcelain")
            joined = " ".join(captured["cmd"])
            self.assertNotIn("insteadOf", joined)
            self.assertNotIn("x-access-token", joined)
            self.assertNotIn(self.TOKEN, joined)

    def test_missing_token_skips_insteadOf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / "missing.env"
            repo = Path(td)
            captured: dict = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = list(cmd)
                class P:
                    returncode = 128
                    stdout = ""
                    stderr = "could not read Username for 'https://github.com'"
                return P()

            with mock.patch.dict(os.environ, self._env_without_github(), clear=True), mock.patch(
                "treasury.pi_ops_alert.SCHEDULER_ENV", env_path
            ), mock.patch(
                "treasury.fund_manager_journal_sync.subprocess.run", side_effect=fake_run
            ):
                code, _, err = _git(repo, "push", "origin", "HEAD")
            self.assertEqual(code, 128)
            self.assertNotIn("insteadOf", " ".join(captured["cmd"]))
            self.assertIn("could not read Username", err)

    def test_network_stderr_redacts_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)

            def fake_run(cmd, **kwargs):
                class P:
                    returncode = 128
                    stdout = ""
                    stderr = (
                        "fatal: could not read Username for "
                        f"'https://x-access-token:{self.TOKEN}@github.com'"
                    )
                return P()

            env = {**self._env_without_github(), "GITHUB_TOKEN": self.TOKEN}
            with mock.patch.dict(os.environ, env, clear=True), mock.patch(
                "treasury.fund_manager_journal_sync.subprocess.run", side_effect=fake_run
            ):
                code, _, err = _git(repo, "push", "origin", "HEAD")
            self.assertEqual(code, 128)
            self.assertNotIn(self.TOKEN, err)
            self.assertIn("REDACTED", err)

    def test_report_failure_redacts_token_on_github(self) -> None:
        env = {**self._env_without_github(), "GITHUB_TOKEN": self.TOKEN}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "treasury.fund_manager_journal_sync.post_ops_github",
            return_value={"ok": True, "posted": True, "issue": "701"},
        ) as post:
            out = _report_failure(
                f"url=https://x-access-token:{self.TOKEN}@github.com/cvolkernick/personal-workspace.git",
                {"stderr": f"Authorization: Bearer {self.TOKEN}"},
            )
        self.assertFalse(out.get("ok"))
        self.assertNotIn(self.TOKEN, out.get("error") or "")
        title, text = post.call_args[0][:2]
        self.assertIn("journal sync failed", title)
        self.assertNotIn(self.TOKEN, text)
        self.assertIn("REDACTED", text)
        self.assertEqual(_redact(self.TOKEN), "REDACTED")


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


_LIVE_HOOK = """#!/bin/bash
# Installed by deploy/workspace_sync.sh — FCC live clone (issue #661).
echo "FCC live clone refuses local commits (#661)." >&2
exit 1
"""


class _FakeGitHub:
    def __init__(self) -> None:
        self.files = {
            JOURNAL_REL: "# Fund manager journal\n\n",
            JSONL_REL: "",
        }
        self.head = "aaa111"
        self.tree = "tree111"
        self.calls: list[tuple] = []
        self.blobs = 0
        self.force_seen = False
        self.patch_fail_once = False
        self.blob_shas: dict[str, str] = {}
        self.omit_content: set[str] = set()
        self.sizes: dict[str, int] = {}
        self.same_tree = False

    def __call__(self, method, path, body=None, timeout=30.0):
        self.calls.append((method, path, body))
        if method == "GET" and "/contents/" in path:
            rel = path.split("/contents/", 1)[1].split("?", 1)[0]
            rel = urllib.parse.unquote(rel)
            if rel not in self.files:
                return None, {"status": 404, "error": "not found"}
            raw = base64.b64encode(self.files[rel].encode()).decode()
            payload: dict = {
                "content": raw,
                "encoding": "base64",
                "size": self.sizes.get(rel, len(self.files[rel].encode())),
            }
            if rel in self.blob_shas:
                payload["sha"] = self.blob_shas[rel]
            if rel in self.omit_content:
                payload["content"] = ""
                payload["encoding"] = "none"
            return payload, None
        if method == "GET" and "/commits/" in path:
            return {"sha": self.head, "commit": {"tree": {"sha": self.tree}}}, None
        if method == "POST" and path.endswith("/git/blobs"):
            self.blobs += 1
            sha = f"blob{self.blobs}"
            if isinstance(body, dict) and body.get("path"):
                pass
            if isinstance(body, dict) and "content" in body:
                # keep origin in sync after a successful ref update only
                self._pending = getattr(self, "_pending", {})
            return {"sha": sha}, None
        if method == "POST" and path.endswith("/git/trees"):
            # Creating a tree object does not move the branch. HEAD stays
            # `self.tree` until the ref PATCH succeeds.
            if self.same_tree:
                return {"sha": self.tree}, None
            return {"sha": "tree222"}, None
        if method == "POST" and path.endswith("/git/commits"):
            self.head = "ccc333"
            return {"sha": self.head}, None
        if method == "PATCH" and "/git/refs/" in path:
            if isinstance(body, dict) and body.get("force"):
                self.force_seen = True
            if self.patch_fail_once:
                self.patch_fail_once = False
                return None, {"status": 422, "error": "Update is not a fast forward"}
            for _method, pth, bdy in self.calls:
                if _method == "POST" and pth.endswith("/git/blobs") and isinstance(bdy, dict):
                    pass
            return {"object": {"sha": self.head}}, None
        return None, {"error": f"unhandled {method} {path}"}


def _install_live_hook(repo: Path) -> None:
    hookdir = repo / ".git" / "hooks"
    hookdir.mkdir(parents=True, exist_ok=True)
    hook = hookdir / "pre-commit"
    hook.write_text(_LIVE_HOOK, encoding="utf-8")
    hook.chmod(0o755)


class TestLiveClone(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="fm-live-")
        self.repo = Path(self._td.name) / "ws"
        self.repo.mkdir()
        _git_cwd(self.repo, "init", "-b", "work/treasury")
        _git_cwd(self.repo, "config", "user.name", "Test")
        _git_cwd(self.repo, "config", "user.email", "t@example.com")
        _write(self.repo / JOURNAL_REL, "# Fund manager journal\n\n")
        _write(self.repo / JSONL_REL, "")
        _git_cwd(self.repo, "add", ".")
        _git_cwd(self.repo, "commit", "-m", "init")
        _install_live_hook(self.repo)
        self.env = {
            "FM_JOURNAL_SYNC": "1",
            "FM_JOURNAL_SYNC_IN_TEST": "1",
            "FCC_HOST_TAG": "prism",
            "FM_JOURNAL_BRANCH": "work/treasury",
            "GITHUB_TOKEN": "ghs_testtoken",
            "PYTEST_CURRENT_TEST": os.environ.get("PYTEST_CURRENT_TEST") or "1",
        }

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_hook_marks_live_clone(self) -> None:
        self.assertTrue(is_live_clone(self.repo))
        self.assertEqual(live_clone_reason(self.repo), "pre-commit-hook")

    def test_fcc_live_tree_env(self) -> None:
        other = Path(self._td.name) / "other"
        other.mkdir()
        with mock.patch.dict(os.environ, {"FCC_LIVE_TREE": "main"}, clear=False):
            self.assertEqual(live_clone_reason(other), "FCC_LIVE_TREE")

    def test_pre_commit_hook_still_refuses_git_commit(self) -> None:
        _write(self.repo / JOURNAL_REL, "# Fund manager journal\n\n## hold\n")
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-am", "should fail"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("refuses local commits", proc.stderr)

    def test_git_helper_blocks_mutating_commands(self) -> None:
        code, _, err = _git(self.repo, "commit", "-m", "nope")
        self.assertEqual(code, 1)
        self.assertIn("#742", err)
        code, _, err = _git(self.repo, "push", "origin", "HEAD")
        self.assertEqual(code, 1)
        self.assertIn("#742", err)

    def test_sync_uses_github_api_not_local_git(self) -> None:
        gh = _FakeGitHub()
        git_calls: list[tuple] = []

        def fake_git(repo, *args, timeout=60.0):
            git_calls.append(args)
            return 0, "", ""

        _write(
            self.repo / JOURNAL_REL,
            "# Fund manager journal\n\n## 2026-09-14T18:00:00 — hold\n",
        )
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "treasury.fund_manager_journal_sync._github_json", side_effect=gh
        ), mock.patch(
            "treasury.fund_manager_journal_sync._git", side_effect=fake_git
        ), mock.patch(
            "treasury.fund_manager_journal_sync.post_ops_github",
            return_value={"ok": True, "posted": False},
        ) as post:
            out = sync_journal(
                repo=self.repo,
                kind="hold",
                as_of="2026-09-14T18:00:00+00:00",
                notify=True,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("committed"), out)
        self.assertTrue(out.get("pushed"), out)
        self.assertEqual(out.get("via"), "github-api")
        self.assertFalse(post.called)
        mutating = {a[0] for a in git_calls if a}
        self.assertFalse(mutating & {"add", "commit", "push", "pull", "fetch", "rebase"})
        methods = [c[0] for c in gh.calls]
        self.assertIn("POST", methods)
        self.assertIn("PATCH", methods)
        self.assertFalse(gh.force_seen)
        patch_bodies = [c[2] for c in gh.calls if c[0] == "PATCH"]
        self.assertTrue(patch_bodies)
        self.assertFalse(any(isinstance(b, dict) and b.get("force") for b in patch_bodies))

    def test_sync_skips_when_origin_matches(self) -> None:
        gh = _FakeGitHub()
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "treasury.fund_manager_journal_sync._github_json", side_effect=gh
        ), mock.patch(
            "treasury.fund_manager_journal_sync._git"
        ) as git:
            out = sync_journal(repo=self.repo, notify=False)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("skipped"), "clean")
        self.assertEqual(out.get("via"), "github-api")
        self.assertFalse(out.get("committed"))
        git.assert_not_called()

    def test_unchanged_large_snapshot_does_not_commit(self) -> None:
        """Contents omits a >1 MB body. Matching blob sha must not move HEAD."""
        snapshot = '{"kind":"hold"}\n' * 40
        _write(self.repo / JSONL_REL, snapshot)
        gh = _FakeGitHub()
        gh.files[JSONL_REL] = snapshot
        gh.omit_content.add(JSONL_REL)
        gh.sizes[JSONL_REL] = 1_841_402
        gh.blob_shas[JOURNAL_REL] = _git_blob_sha1(
            (self.repo / JOURNAL_REL).read_bytes()
        )
        gh.blob_shas[JSONL_REL] = _git_blob_sha1(snapshot.encode("utf-8"))
        head = gh.head
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "treasury.fund_manager_journal_sync._github_json", side_effect=gh
        ):
            out = sync_journal(repo=self.repo, kind="hold", notify=False)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("skipped"), "clean")
        self.assertFalse(out.get("committed"))
        self.assertFalse(out.get("pushed"))
        self.assertEqual(gh.head, head)
        self.assertFalse(any(c[0] in {"POST", "PATCH"} for c in gh.calls))

    def test_journal_line_commits_journal_only(self) -> None:
        """A real journal append commits that file, not an unchanged snapshot."""
        snapshot = '{"kind":"hold"}\n' * 40
        _write(self.repo / JSONL_REL, snapshot)
        journal = "# Fund manager journal\n\n## 2026-09-14T18:00:00 — hold\n"
        _write(self.repo / JOURNAL_REL, journal)
        gh = _FakeGitHub()
        gh.files[JSONL_REL] = snapshot
        gh.omit_content.add(JSONL_REL)
        gh.sizes[JSONL_REL] = 1_841_402
        gh.blob_shas[JSONL_REL] = _git_blob_sha1(snapshot.encode("utf-8"))
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "treasury.fund_manager_journal_sync._github_json", side_effect=gh
        ), mock.patch(
            "treasury.fund_manager_journal_sync.post_ops_github",
            return_value={"ok": True, "posted": False},
        ):
            out = sync_journal(
                repo=self.repo,
                kind="hold",
                as_of="2026-09-14T18:00:00+00:00",
                notify=True,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("committed"), out)
        self.assertEqual(out.get("paths"), [JOURNAL_REL])
        blob_posts = [
            c[2] for c in gh.calls if c[0] == "POST" and str(c[1]).endswith("/git/blobs")
        ]
        self.assertEqual(len(blob_posts), 1)
        self.assertEqual(blob_posts[0].get("content"), journal)
        tree_posts = [
            c[2] for c in gh.calls if c[0] == "POST" and str(c[1]).endswith("/git/trees")
        ]
        self.assertEqual(len(tree_posts), 1)
        paths = [entry["path"] for entry in tree_posts[0]["tree"]]
        self.assertEqual(paths, [JOURNAL_REL])

    def test_identical_tree_does_not_move_branch(self) -> None:
        gh = _FakeGitHub()
        gh.same_tree = True
        _write(
            self.repo / JOURNAL_REL,
            "# Fund manager journal\n\n## 2026-09-14T18:00:00 — hold\n",
        )
        head = gh.head
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "treasury.fund_manager_journal_sync._github_json", side_effect=gh
        ):
            out = sync_journal(
                repo=self.repo,
                kind="hold",
                as_of="2026-09-14T18:00:00+00:00",
                notify=False,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("skipped"), "empty-diff")
        self.assertFalse(out.get("committed"))
        self.assertFalse(out.get("pushed"))
        self.assertEqual(gh.head, head)
        self.assertFalse(
            any(c[0] == "POST" and str(c[1]).endswith("/git/commits") for c in gh.calls)
        )
        self.assertFalse(any(c[0] == "PATCH" for c in gh.calls))

    def test_api_conflict_retries_without_force(self) -> None:
        gh = _FakeGitHub()
        gh.patch_fail_once = True
        _write(
            self.repo / JOURNAL_REL,
            "# Fund manager journal\n\n## 2026-09-14T18:00:00 — hold\n",
        )
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "treasury.fund_manager_journal_sync._github_json", side_effect=gh
        ), mock.patch(
            "treasury.fund_manager_journal_sync.post_ops_github",
            return_value={"ok": True, "posted": False},
        ):
            out = sync_journal(
                repo=self.repo,
                kind="hold",
                as_of="2026-09-14T18:00:00+00:00",
                notify=True,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("attempt"), 2)
        self.assertFalse(gh.force_seen)
        self.assertEqual(sum(1 for c in gh.calls if c[0] == "PATCH"), 2)

    def test_live_hook_message_is_the_661_guard(self) -> None:
        hook = (self.repo / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
        self.assertIn("FCC live clone refuses local commits (#661).", hook)
        src = (ROOT / "treasury" / "fund_manager_journal_sync.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("install_live_commit_hook", src)
        self.assertIn("refuses local git mutation (#742)", src)


if __name__ == "__main__":
    unittest.main()
