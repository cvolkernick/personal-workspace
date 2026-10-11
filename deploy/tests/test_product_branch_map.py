#!/usr/bin/env python3
"""Unit tests for deploy/product_branch_map.py (issue #560)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = ROOT / "deploy" / "product_branch_map.py"
SYNC_SH = ROOT / "deploy" / "workspace_sync.sh"


def _load():
    spec = importlib.util.spec_from_file_location("product_branch_map", MOD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["product_branch_map"] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()


class TestCheckSyncBranch(unittest.TestCase):
    def test_work_treasury_allowed(self):
        self.assertEqual(M.check_sync_branch("work/treasury"), "work/treasury")

    def test_master_refused(self):
        with self.assertRaises(M.SyncBranchRefused) as ctx:
            M.check_sync_branch("master")
        self.assertIn("work/treasury", str(ctx.exception))
        self.assertIn("master", str(ctx.exception))

    def test_holistic_refused(self):
        with self.assertRaises(M.SyncBranchRefused):
            M.check_sync_branch("work/holistic")

    def test_main_refused(self):
        with self.assertRaises(M.SyncBranchRefused):
            M.check_sync_branch("main")

    def test_empty_refused(self):
        with self.assertRaises(M.SyncBranchRefused):
            M.check_sync_branch("")

    def test_explicit_fcc_live_refuses_master(self):
        with self.assertRaises(M.SyncBranchRefused):
            M.check_sync_branch("master", checkout="fcc-live")
        self.assertEqual(
            M.check_sync_branch("work/treasury", checkout="fcc-live"), "work/treasury"
        )

    def test_default_role_is_fcc_live(self):
        self.assertEqual(M.DEFAULT_SYNC_ROLE, "fcc-live")
        self.assertEqual(M.SYNC_ROLE_BRANCHES["fcc-live"], frozenset({"work/treasury"}))


class TestSyncRoles(unittest.TestCase):
    """SYNC_ROLE: explicit per-role allowlists; unknown roles fail closed."""

    def test_backup_host_allows_master(self):
        self.assertEqual(M.check_sync_branch("master", checkout="backup-host"), "master")

    def test_backup_host_refuses_everything_else(self):
        for b in ("work/treasury", "work/holistic", "main", "", "feature/x"):
            with self.assertRaises(M.SyncBranchRefused, msg=b):
                M.check_sync_branch(b, checkout="backup-host")

    def test_unknown_roles_fail_closed(self):
        for role in ("other", "", "  ", "FCC-LIVE", "backup", "backup-host-x", "prism"):
            for b in ("master", "work/treasury"):
                with self.assertRaises(M.SyncBranchRefused, msg=(role, b)) as ctx:
                    M.check_sync_branch(b, checkout=role)
                self.assertIn("refuses", str(ctx.exception))


class TestPrBase(unittest.TestCase):
    def test_product_fcc_is_work_treasury(self):
        self.assertEqual(M.pr_base_for_product("fcc"), "work/treasury")
        self.assertEqual(M.pr_base_for_product("treasury"), "work/treasury")

    def test_product_fitdash_is_master(self):
        self.assertEqual(M.pr_base_for_product("fitdash"), "master")

    def test_fcc_paths_target_work_treasury(self):
        self.assertEqual(
            M.pr_base_for_paths(["financial-command/index.html", "treasury/run_treasury.py"]),
            "work/treasury",
        )

    def test_fitdash_paths_target_master(self):
        self.assertEqual(
            M.pr_base_for_paths(["resistance-dashboard/server.py", "fitness/nutrition/x.json"]),
            "master",
        )

    def test_deploy_glue_targets_master(self):
        self.assertEqual(
            M.pr_base_for_paths(["deploy/workspace_sync.sh", "ops/SDLC_MERGE_DEPLOY.md"]),
            "master",
        )

    def test_mixed_fcc_and_fitdash_refused(self):
        with self.assertRaises(M.MixedProductError):
            M.pr_base_for_paths(
                ["financial-command/index.html", "resistance-dashboard/server.py"]
            )

    def test_mixed_fcc_and_deploy_refused(self):
        with self.assertRaises(M.MixedProductError):
            M.pr_base_for_paths(
                ["financial-command/index.html", "deploy/units/workspace-sync.service"]
            )


class TestCli(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(MOD_PATH), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_check_sync_ok(self):
        proc = self._run("check-sync", "--branch", "work/treasury")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "work/treasury")

    def test_check_sync_master_nonzero(self):
        proc = self._run("check-sync", "--branch", "master")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("refuses", proc.stderr)

    def test_check_sync_holistic_nonzero(self):
        proc = self._run("check-sync", "--branch", "work/holistic")
        self.assertEqual(proc.returncode, 1)

    def test_check_sync_backup_host_master_ok(self):
        proc = self._run("check-sync", "--branch", "master", "--role", "backup-host")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "master")
        proc = self._run("check-sync", "--branch", "master", "--checkout", "backup-host")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_check_sync_backup_host_treasury_nonzero(self):
        proc = self._run("check-sync", "--branch", "work/treasury", "--role", "backup-host")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("refuses", proc.stderr)

    def test_check_sync_unknown_role_nonzero(self):
        proc = self._run("check-sync", "--branch", "master", "--role", "bogus")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unknown SYNC_ROLE", proc.stderr)

    def test_pr_base_product(self):
        proc = self._run("pr-base", "--product", "fcc")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "work/treasury")


class TestWorkspaceSyncRefuse(unittest.TestCase):
    """workspace_sync.sh must exit non-zero before mutating git on a wrong branch."""

    def _init_repo(self, dest: Path, *, copy_map: bool) -> None:
        subprocess.run(
            ["git", "init", "-b", "master"],
            cwd=str(dest),
            check=True,
            capture_output=True,
        )
        (dest / "README").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "README"], cwd=str(dest), check=True, capture_output=True)
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(dest),
            check=True,
            capture_output=True,
            env=env,
        )
        (dest / "deploy").mkdir(exist_ok=True)
        if copy_map:
            shutil.copy(MOD_PATH, dest / "deploy" / "product_branch_map.py")

    def _head(self, repo: Path) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
        ).strip()

    def _run_sync(
        self, repo: Path, branch: str, role: Optional[str] = None
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "WORKSPACE_DIR": str(repo),
            "SYNC_BRANCH": branch,
            "HOME": str(repo),
        }
        env.pop("SYNC_ROLE", None)
        if role is not None:
            env["SYNC_ROLE"] = role
        return subprocess.run(
            ["bash", str(SYNC_SH)],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_refuses_master_no_head_move(self):
        with tempfile.TemporaryDirectory(prefix="ws-sync-") as td:
            repo = Path(td)
            self._init_repo(repo, copy_map=True)
            before = self._head(repo)
            proc = self._run_sync(repo, "master")
            self.assertNotEqual(proc.returncode, 0)
            combined = proc.stdout + proc.stderr
            self.assertIn("refuses", combined.lower())
            self.assertEqual(self._head(repo), before)

    def test_refuses_holistic_no_head_move(self):
        with tempfile.TemporaryDirectory(prefix="ws-sync-") as td:
            repo = Path(td)
            self._init_repo(repo, copy_map=True)
            before = self._head(repo)
            proc = self._run_sync(repo, "work/holistic")
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(self._head(repo), before)

    def test_explicit_fcc_live_refuses_master_no_head_move(self):
        with tempfile.TemporaryDirectory(prefix="ws-sync-") as td:
            repo = Path(td)
            self._init_repo(repo, copy_map=True)
            before = self._head(repo)
            proc = self._run_sync(repo, "master", role="fcc-live")
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("refuses", (proc.stdout + proc.stderr).lower())
            self.assertEqual(self._head(repo), before)

    def test_unknown_role_refused_no_head_move(self):
        for copy_map in (True, False):
            with tempfile.TemporaryDirectory(prefix="ws-sync-") as td:
                repo = Path(td)
                self._init_repo(repo, copy_map=copy_map)
                before = self._head(repo)
                proc = self._run_sync(repo, "master", role="bogus")
                self.assertNotEqual(proc.returncode, 0, msg=copy_map)
                combined = (proc.stdout + proc.stderr).lower()
                self.assertIn("refuse", combined, msg=copy_map)
                self.assertEqual(self._head(repo), before)

    def test_backup_host_refuses_work_treasury_no_head_move(self):
        for copy_map in (True, False):
            with tempfile.TemporaryDirectory(prefix="ws-sync-") as td:
                repo = Path(td)
                self._init_repo(repo, copy_map=copy_map)
                before = self._head(repo)
                proc = self._run_sync(repo, "work/treasury", role="backup-host")
                self.assertNotEqual(proc.returncode, 0, msg=copy_map)
                self.assertIn("refuse", (proc.stdout + proc.stderr).lower(), msg=copy_map)
                self.assertEqual(self._head(repo), before)

    def test_bash_fallback_refuses_master_when_map_missing(self):
        with tempfile.TemporaryDirectory(prefix="ws-sync-") as td:
            repo = Path(td)
            self._init_repo(repo, copy_map=False)
            before = self._head(repo)
            proc = self._run_sync(repo, "master")
            self.assertNotEqual(proc.returncode, 0)
            combined = proc.stdout + proc.stderr
            self.assertIn("refuses", combined.lower())
            self.assertEqual(self._head(repo), before)


GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True, env=GIT_ENV
    ).stdout.strip()


class TestBackupHostSyncsMaster(unittest.TestCase):
    """SYNC_ROLE=backup-host lands origin/master end to end, without FCC-only steps."""

    def test_backup_host_lands_master(self):
        with tempfile.TemporaryDirectory(prefix="ws-backup-") as td:
            td_path = Path(td)
            seed = td_path / "seed"
            seed.mkdir()
            _git(seed, "init", "-b", "master")
            (seed / "README").write_text("v1\n", encoding="utf-8")
            (seed / "deploy").mkdir()
            shutil.copy(MOD_PATH, seed / "deploy" / "product_branch_map.py")
            _git(seed, "add", "-A")
            _git(seed, "commit", "-m", "v1")
            bare = td_path / "remote.git"
            _git(td_path, "clone", "--bare", str(seed), str(bare))
            repo = td_path / "host"
            _git(td_path, "clone", str(bare), str(repo))
            # New commit on origin/master after the host cloned.
            (seed / "README").write_text("v2\n", encoding="utf-8")
            _git(seed, "commit", "-am", "v2")
            _git(seed, "push", str(bare), "master")
            origin_tip = _git(seed, "rev-parse", "HEAD")
            home = td_path / "home"
            home.mkdir()
            env = {
                **GIT_ENV,
                "WORKSPACE_DIR": str(repo),
                "SYNC_ROLE": "backup-host",
                "SYNC_BRANCH": "master",
                "HOME": str(home),
                "WORKSPACE_SYNC_KEEP_REMOTE": "1",
            }
            proc = subprocess.run(
                ["bash", str(SYNC_SH)], capture_output=True, text=True, env=env
            )
            combined = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, combined)
            self.assertEqual(_git(repo, "rev-parse", "HEAD"), origin_tip)
            self.assertEqual(_git(repo, "branch", "--show-current"), "master")
            # FCC-only steps skipped on a backup host.
            self.assertFalse((repo / "financial-command" / "current-branch.txt").exists())
            self.assertFalse((repo / ".git" / "hooks" / "pre-commit").exists())
            self.assertIn("skip FCC bounce", combined)
            self.assertEqual(_git(repo, "status", "--porcelain"), "")


class TestUnitPin(unittest.TestCase):
    def test_service_pins_work_treasury(self):
        text = (ROOT / "deploy" / "units" / "workspace-sync.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("Environment=SYNC_BRANCH=work/treasury", text)
        self.assertNotIn("SYNC_BRANCH=master", text)
        self.assertIn("work/treasury", text)
        self.assertIn("ExecStart=/bin/bash %h/.config/personal-workspace/workspace_sync.sh", text)

    def test_fcc_service_keeps_default_role(self):
        text = (ROOT / "deploy" / "units" / "workspace-sync.service").read_text(
            encoding="utf-8"
        )
        # No SYNC_ROLE (= fcc-live) on the FCC live-root unit: prism unaffected.
        self.assertNotIn("SYNC_ROLE", text)
        self.assertNotIn("backup-host", text)

    def test_timer_does_not_say_origin_master(self):
        text = (ROOT / "deploy" / "units" / "workspace-sync.timer").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("origin/master", text)
        self.assertIn("work/treasury", text)

    def test_units_dir_has_no_sync_branch_master(self):
        units = ROOT / "deploy" / "units"
        for path in units.iterdir():
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "SYNC_BRANCH=master",
                text,
                msg=f"{path.name} still pins SYNC_BRANCH=master",
            )


class TestBackupHostUnit(unittest.TestCase):
    """Finley (B2 puller) unit template: backup-host role, master only."""

    UNIT = ROOT / "deploy" / "b2-puller" / "units" / "workspace-sync.service"
    TIMER = ROOT / "deploy" / "b2-puller" / "units" / "workspace-sync.timer"

    def test_unit_sets_backup_host_master(self):
        text = self.UNIT.read_text(encoding="utf-8")
        self.assertIn("Environment=SYNC_ROLE=backup-host", text)
        self.assertIn("Environment=SYNC_BRANCH=master", text)
        self.assertIn(
            "ExecStart=/bin/bash %h/personal-workspace/deploy/workspace_sync.sh", text
        )
        timer = self.TIMER.read_text(encoding="utf-8")
        self.assertIn("Unit=workspace-sync.service", timer)
        self.assertNotIn("work/treasury", timer)

    def _pre(self) -> str:
        for line in self.UNIT.read_text(encoding="utf-8").splitlines():
            if line.startswith("ExecStartPre=/bin/bash -c '"):
                return line[len("ExecStartPre=/bin/bash -c '"):].rstrip("'")
        self.fail("no ExecStartPre guard")

    def test_exec_start_pre_guard(self):
        cmd = self._pre()

        def run(role, branch):
            env = {k: v for k, v in os.environ.items() if k not in ("SYNC_ROLE", "SYNC_BRANCH")}
            if role is not None:
                env["SYNC_ROLE"] = role
            if branch is not None:
                env["SYNC_BRANCH"] = branch
            return subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True).returncode

        self.assertEqual(run("backup-host", "master"), 0)
        self.assertNotEqual(run("backup-host", "work/treasury"), 0)
        self.assertNotEqual(run("fcc-live", "master"), 0)
        self.assertNotEqual(run(None, "master"), 0)
        self.assertNotEqual(run("backup-host", None), 0)

    def test_install_finley_uses_backup_host_units(self):
        text = (ROOT / "deploy" / "b2-puller" / "install_finley.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('FINLEY_UNITS_SRC="$ROOT/deploy/b2-puller/units"', text)
        self.assertIn('unit_src "$f"', text)
        self.assertNotIn('"$UNITS_SRC/$f"', text)


if __name__ == "__main__":
    unittest.main()
