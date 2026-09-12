#!/usr/bin/env python3
"""Tests for deploy/fcc_tip_health.py (issue #562)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
        self.assertNotIn("ntfy.sh", src)
        self.assertNotIn("post_ntfy_page", src)


class TestInspect(unittest.TestCase):
    def test_healthy_attached_tip(self) -> None:
        td, repo = _repo()
        try:
            result = M.inspect(repo)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["attached"], "work/treasury")
            self.assertEqual(result["current_branch_txt"], "work/treasury")
            self.assertEqual(result["head"], result["origin_sha"])
        finally:
            td.cleanup()

    def test_detached_is_mismatch(self) -> None:
        td, repo = _repo()
        try:
            _git(repo, "checkout", "--detach")
            result = M.inspect(repo)
            self.assertFalse(result["ok"], result)
            self.assertTrue(any("detached" in m for m in result["mismatches"]))
            # inspect must not re-attach
            self.assertEqual(_git(repo, "branch", "--show-current"), "")
        finally:
            td.cleanup()

    def test_wrong_stamp_is_mismatch(self) -> None:
        td, repo = _repo()
        try:
            (repo / "financial-command" / "current-branch.txt").write_text(
                "master\n", encoding="utf-8"
            )
            result = M.inspect(repo)
            self.assertFalse(result["ok"], result)
            self.assertTrue(
                any("current-branch.txt" in m for m in result["mismatches"])
            )
        finally:
            td.cleanup()

    def test_sha_drift_is_mismatch(self) -> None:
        td, repo = _repo()
        try:
            (repo / "README").write_text("y\n", encoding="utf-8")
            _git(repo, "add", "README")
            _git(repo, "commit", "-m", "ahead")
            result = M.inspect(repo)
            self.assertFalse(result["ok"], result)
            self.assertTrue(any("HEAD" in m for m in result["mismatches"]))
        finally:
            td.cleanup()


class TestAlertOnce(unittest.TestCase):
    def test_healthy_skips(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            out = M.alert_mismatch(
                {"ok": True, "mismatches": []},
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
                "mismatches": ["detached"],
                "expected_branch": "work/treasury",
                "head": "abc",
                "origin_sha": "abc",
                "attached": "detached",
                "current_branch_txt": "work/treasury",
            }
            first = M.alert_mismatch(
                result,
                workspace=Path(td),
                state_path=state,
                dry_run=True,
            )
            self.assertEqual(first.get("skipped"), "dry-run")
            second = M.alert_mismatch(
                result,
                workspace=Path(td),
                state_path=state,
                dry_run=True,
            )
            self.assertEqual(second.get("skipped"), "cooldown")

    def test_sustained_bypasses_6h_cooldown(self) -> None:
        """Issue #661: still-red after 1h comments again (1h cooldown), not 6h silence."""
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            now = datetime.now(timezone.utc)
            past = (now - timedelta(hours=2)).replace(microsecond=0).isoformat().replace(
                "+00:00", "Z"
            )
            notified = (now - timedelta(hours=1, minutes=30)).replace(
                microsecond=0
            ).isoformat().replace("+00:00", "Z")
            state.write_text(
                json.dumps(
                    {
                        "last_notified_at": notified,
                        "first_violation_at": past,
                        "last_mismatches": ["HEAD abc != origin/work/treasury def"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = {
                "ok": False,
                "mismatches": ["HEAD abc != origin/work/treasury def"],
                "expected_branch": "work/treasury",
                "head": "abc",
                "origin_sha": "def",
                "attached": "work/treasury",
                "current_branch_txt": "work/treasury",
            }
            out = M.alert_mismatch(
                result,
                workspace=Path(td),
                state_path=state,
                dry_run=True,
            )
            self.assertTrue(out.get("sustained"), out)
            self.assertEqual(out.get("skipped"), "dry-run")
            self.assertIn("SUSTAINED", out.get("title") or "")
            self.assertIn("#workflow", out.get("text") or "")
            self.assertTrue(out.get("page"), out)

    def test_routine_mismatch_github_not_ntfy(self) -> None:
        """#704: first mismatch comments #701; does not POST ntfy.sh."""
        result = {
            "ok": False,
            "mismatches": ["detached"],
            "expected_branch": "work/treasury",
            "head": "abc",
            "origin_sha": "abc",
            "attached": "detached",
            "current_branch_txt": "work/treasury",
        }
        captured: list[str] = []

        def fake_urlopen(req, timeout=15):
            captured.append(req.full_url)
            resp = mock.MagicMock()
            resp.status = 201
            resp.getcode.return_value = 201
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            env = {
                "GITHUB_TOKEN": "ghs_test",
                "PI_OPS_ALERT_ISSUE": "701",
                "FCC_ALERT_KILL_SWITCH": "",
                "NTFY_TOKEN": "",
                "FCC_NTFY_TOKEN": "",
            }
            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "urllib.request.urlopen", side_effect=fake_urlopen
            ):
                out = M.alert_mismatch(
                    result,
                    workspace=Path(td),
                    state_path=state,
                )
        self.assertFalse(out.get("page"), out)
        self.assertTrue(out.get("notified"), out)
        self.assertTrue((out.get("github") or {}).get("posted"), out)
        self.assertTrue(any("issues/701/comments" in u for u in captured), captured)
        self.assertFalse(any("ntfy.sh" in u for u in captured), captured)

    def test_sustained_github_not_ntfy(self) -> None:
        now = datetime.now(timezone.utc)
        past = (now - timedelta(hours=2)).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
        result = {
            "ok": False,
            "mismatches": ["HEAD abc != origin/work/treasury def"],
            "expected_branch": "work/treasury",
            "head": "abc",
            "origin_sha": "def",
            "attached": "work/treasury",
            "current_branch_txt": "work/treasury",
        }
        captured: list[str] = []

        def fake_urlopen(req, timeout=15):
            captured.append(req.full_url)
            resp = mock.MagicMock()
            resp.status = 200
            resp.getcode.return_value = 200
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            state.write_text(
                json.dumps({"first_violation_at": past}) + "\n", encoding="utf-8"
            )
            env = {
                "GITHUB_TOKEN": "ghs_test",
                "NTFY_TOKEN": "tk_test",
                "PI_OPS_ALERT_ISSUE": "701",
                "FCC_ALERT_KILL_SWITCH": "",
            }
            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "urllib.request.urlopen", side_effect=fake_urlopen
            ):
                out = M.alert_mismatch(
                    result,
                    workspace=Path(td),
                    state_path=state,
                )
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("page"), out)
        self.assertTrue(out.get("notified"), out)
        self.assertIn("SUSTAINED", out.get("title") or "")
        self.assertTrue(any("issues/701/comments" in u for u in captured), captured)
        self.assertFalse(any("ntfy.sh" in u for u in captured), captured)

    def test_kill_switch_github_not_ntfy(self) -> None:
        result = {
            "ok": False,
            "mismatches": ["detached"],
            "expected_branch": "work/treasury",
            "head": "abc",
            "origin_sha": "abc",
            "attached": "detached",
            "current_branch_txt": "work/treasury",
        }
        captured: list[str] = []

        def fake_urlopen(req, timeout=15):
            captured.append(req.full_url)
            resp = mock.MagicMock()
            resp.status = 200
            resp.getcode.return_value = 200
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            env = {
                "GITHUB_TOKEN": "ghs_test",
                "FCC_ALERT_KILL_SWITCH": "1",
                "NTFY_TOKEN": "tk_test",
                "PI_OPS_ALERT_ISSUE": "701",
            }
            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "urllib.request.urlopen", side_effect=fake_urlopen
            ):
                out = M.alert_mismatch(
                    result,
                    workspace=Path(td),
                    state_path=state,
                )
        self.assertTrue(out.get("page"), out)
        self.assertIn("KILL-SWITCH", out.get("title") or "")
        self.assertTrue(out.get("notified"), out)
        self.assertTrue(any("issues/701/comments" in u for u in captured), captured)
        self.assertFalse(any("ntfy.sh" in u for u in captured), captured)


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

    def test_main_mismatch_exit_1_does_not_reattach(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
