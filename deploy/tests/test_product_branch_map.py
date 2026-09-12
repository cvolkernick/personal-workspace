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

    def test_non_fcc_checkout_allows_master(self):
        self.assertEqual(
            M.check_sync_branch("master", checkout="other"),
            "master",
        )


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

    def _run_sync(self, repo: Path, branch: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "WORKSPACE_DIR": str(repo),
            "SYNC_BRANCH": branch,
            "HOME": str(repo),
        }
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


class TestUnitPin(unittest.TestCase):
    def test_service_pins_work_treasury(self):
        text = (ROOT / "deploy" / "units" / "workspace-sync.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("Environment=SYNC_BRANCH=work/treasury", text)
        self.assertNotIn("SYNC_BRANCH=master", text)
        self.assertIn("work/treasury", text)
        self.assertIn("ExecStart=/bin/bash %h/.config/personal-workspace/workspace_sync.sh", text)

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


if __name__ == "__main__":
    unittest.main()
