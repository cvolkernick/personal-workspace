#!/usr/bin/env python3
"""Drift checks (#580 E2/E8). Clean runs stay silent."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MOD_PATH = ROOT / "ops" / "context" / "persist.py"


def _load():
    spec = importlib.util.spec_from_file_location("ctx_persist", MOD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


class TestDrift(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="ctx-drift-")
        self.ws = Path(self.td.name) / "ws"
        self.ws.mkdir()
        _git(self.ws, "init")
        _git(self.ws, "config", "user.name", "Test")
        _git(self.ws, "config", "user.email", "test@example.invalid")
        (self.ws / "CONTEXT.md").write_text("# x\n## Canonical locations\none place\n", encoding="utf-8")
        fcc = self.ws / "financial-command"
        fcc.mkdir()
        (fcc / "manifest.webmanifest").write_text("{}\n", encoding="utf-8")
        (fcc / "index.html").write_text("ok\n", encoding="utf-8")
        units = self.ws / "deploy" / "units"
        units.mkdir(parents=True)
        (units / "harness-learn.timer").write_text("[Timer]\n", encoding="utf-8")
        (units / "workflow-dashboard.service").write_text("[Service]\n", encoding="utf-8")
        jobs = self.ws / "ops" / "jobs"
        jobs.mkdir(parents=True)
        (jobs / "harness-learn.md").write_text(
            "---\nunit: harness-learn.timer\n---\nweekly\n", encoding="utf-8"
        )
        _git(self.ws, "add", ".")
        _git(self.ws, "commit", "-m", "init")
        _git(self.ws, "branch", "-M", "master")
        _git(self.ws, "branch", "work/treasury")
        # remote-style refs the checker uses
        _git(self.ws, "update-ref", "refs/remotes/origin/master", "HEAD")
        _git(self.ws, "update-ref", "refs/remotes/origin/work/treasury", "HEAD")

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_clean_is_silent(self) -> None:
        rc = M.main(
            [
                "drift",
                "--workspace",
                str(self.ws),
                "--master-ref",
                "origin/master",
                "--treasury-ref",
                "origin/work/treasury",
            ]
        )
        self.assertEqual(rc, 0)

    def test_dirty_older_than_24h(self) -> None:
        dirty = self.ws / "CONTEXT.md"
        dirty.write_text("# x\n## Canonical locations\nstale edit\n", encoding="utf-8")
        old = time.time() - 25 * 3600
        os.utime(dirty, (old, old))
        rc = M.main(
            [
                "drift",
                "--workspace",
                str(self.ws),
                "--dirty-hours",
                "24",
                "--master-ref",
                "origin/master",
                "--treasury-ref",
                "origin/work/treasury",
            ]
        )
        self.assertEqual(rc, 1)

    def test_unmirrored_timer(self) -> None:
        (self.ws / "deploy" / "units" / "orphan.timer").write_text("[Timer]\n", encoding="utf-8")
        rc = M.main(
            [
                "drift",
                "--workspace",
                str(self.ws),
                "--master-ref",
                "origin/master",
                "--treasury-ref",
                "origin/work/treasury",
            ]
        )
        self.assertEqual(rc, 1)


class TestContradict(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="ctx-con-")
        self.home = Path(self.td.name)
        mem = self.home / "MEMORY.md"
        mem.write_text(
            "## Facts\n"
            "- **fleet repo**: old-name\n"
            "- **timezone**: America/New_York\n"
            "- **fleet repo**: personal-workspace\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_report_only(self) -> None:
        rc = M.main(["contradict", "--paths", str(self.home / "MEMORY.md")])
        self.assertEqual(rc, 1)
        text = (self.home / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("old-name", text)

    def test_apply_reconciles_in_place(self) -> None:
        rc = M.main(["contradict", "--apply", "--paths", str(self.home / "MEMORY.md")])
        self.assertEqual(rc, 0)
        text = (self.home / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("superseded", text)
        self.assertIn("personal-workspace", text)
        self.assertTrue((self.home / "CONTRADICTION_LOG.md").exists())
        rc2 = M.main(["contradict", "--paths", str(self.home / "MEMORY.md")])
        self.assertEqual(rc2, 0)


if __name__ == "__main__":
    unittest.main()
