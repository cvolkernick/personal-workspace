#!/usr/bin/env python3
"""Backup / restore round-trip (#580 E1). Secrets never land on the remote."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
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


def _init_user(cwd: Path) -> None:
    _git(cwd, "config", "user.name", "Test")
    _git(cwd, "config", "user.email", "test@example.invalid")


class TestBackupRestore(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="ctx-bak-")
        self.root = Path(self.td.name)
        self.home = self.root / "home"
        self.grok = self.root / "grok"
        self.bare = self.root / "remote.git"
        self.clone = self.root / "clone"
        self.restored = self.root / "restored"
        self.out = self.root / "out"
        self.home.mkdir()
        (self.home / "MEMORY.md").write_text("# mem\n- **fleet repo**: personal-workspace\n", encoding="utf-8")
        (self.home / "USER.md").write_text("Name: Chris\nTimezone: America/New_York\n", encoding="utf-8")
        (self.home / "skills").mkdir()
        (self.home / "skills" / "x-post-reader").mkdir()
        (self.home / "skills" / "x-post-reader" / "SKILL.md").write_text("portable as-is\n", encoding="utf-8")
        (self.home / ".env").write_text("AWS_SECRET=nope\n", encoding="utf-8")
        ssh = self.home / ".ssh"
        ssh.mkdir()
        (ssh / "id_ed25519").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nAAA\n", encoding="utf-8")
        (self.home / "wallet.dat").write_bytes(b"\x00wallet")
        (self.home / "cron").mkdir()
        (self.home / "cron" / "jobs.json").write_text('{"jobs":[{"secret":"x"}]}\n', encoding="utf-8")
        self.grok.mkdir()
        (self.grok / "skills").mkdir()
        (self.grok / "skills" / "demo").mkdir()
        (self.grok / "skills" / "demo" / "SKILL.md").write_text("grok skill\n", encoding="utf-8")
        (self.grok / "mcp_credentials.json").write_text('{"access_token":"nope"}\n', encoding="utf-8")
        _git(self.root, "init", "--bare", str(self.bare))
        # seed HEAD so clone works
        seed = self.root / "seed"
        seed.mkdir()
        _git(seed, "init")
        _init_user(seed)
        (seed / "README.md").write_text("agent-context\n", encoding="utf-8")
        _git(seed, "add", "README.md")
        _git(seed, "commit", "-m", "init")
        _git(seed, "branch", "-M", "main")
        _git(seed, "remote", "add", "origin", str(self.bare))
        _git(seed, "push", "-u", "origin", "main")

    def tearDown(self) -> None:
        self.td.cleanup()

    def _run(self, argv: list[str]) -> int:
        return M.main(argv)

    def test_dry_run_does_not_push(self) -> None:
        rc = self._run(
            [
                "backup",
                "--home",
                str(self.home),
                "--grok-home",
                str(self.grok),
                "--remote",
                str(self.bare),
                "--clone-dir",
                str(self.clone),
                "--staging",
                str(self.root / "staging"),
                "--workspace",
                str(self.root),
            ]
        )
        self.assertEqual(rc, 0)
        self.assertFalse(self.clone.exists())

    def test_apply_roundtrip_excludes_secrets(self) -> None:
        rc = self._run(
            [
                "backup",
                "--apply",
                "--home",
                str(self.home),
                "--grok-home",
                str(self.grok),
                "--remote",
                str(self.bare),
                "--branch",
                "main",
                "--clone-dir",
                str(self.clone),
                "--staging",
                str(self.root / "staging"),
                "--workspace",
                str(self.root),
            ]
        )
        self.assertEqual(rc, 0)
        files = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", "HEAD"],
            cwd=str(self.clone),
            text=True,
        ).splitlines()
        self.assertIn("MEMORY.md", files)
        self.assertIn("USER.md", files)
        self.assertIn("skills/x-post-reader/SKILL.md", files)
        self.assertIn("grok/skills/demo/SKILL.md", files)
        for banned in (".env", "id_ed25519", "wallet.dat", "mcp_credentials.json", "jobs.json"):
            self.assertTrue(all(banned not in f for f in files), msg=f"{banned} in {files}")

        rc = self._run(
            [
                "restore",
                "--apply",
                "--source",
                str(self.clone),
                "--to",
                str(self.restored),
            ]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(
            (self.restored / "MEMORY.md").read_text(encoding="utf-8"),
            (self.home / "MEMORY.md").read_text(encoding="utf-8"),
        )
        self.assertTrue((self.restored / "skills/x-post-reader/SKILL.md").exists())
        self.assertFalse((self.restored / ".env").exists())
        self.assertFalse((self.restored / ".ssh" / "id_ed25519").exists())
        self.assertFalse((self.restored / "wallet.dat").exists())

    def test_apply_failure_notifies(self) -> None:
        hook = self.root / "hook.sh"
        flag = self.root / "hook.fired"
        hook.write_text(f"#!/bin/sh\necho \"$1\" > '{flag}'\n", encoding="utf-8")
        hook.chmod(0o755)
        cfg = self.root / "cfg.json"
        cfg.write_text(json.dumps({"notify_hook": [str(hook)]}), encoding="utf-8")
        rc = self._run(
            [
                "backup",
                "--config",
                str(cfg),
                "--apply",
                "--home",
                str(self.home),
                "--grok-home",
                str(self.grok),
                "--remote",
                str(self.root / "no-such-remote.git"),
                "--clone-dir",
                str(self.clone),
                "--staging",
                str(self.root / "staging"),
                "--workspace",
                str(self.root),
                "--out",
                str(self.out),
            ]
        )
        self.assertEqual(rc, 2)
        self.assertTrue((self.out / "FAILURE.txt").exists())
        self.assertTrue(flag.exists())


if __name__ == "__main__":
    unittest.main()
