#!/usr/bin/env python3
"""install_remote refuses pinned clones and keeps --dry-run non-mutating (#866)."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "install_remote.sh"
STUB_NAMES = ("ssh", "scp", "rsync", "systemctl")


class PathStubs:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.bin = root / "bin"
        self.logs = root / "logs"
        self.bin.mkdir()
        self.logs.mkdir()
        for name in STUB_NAMES:
            self._write(name)

    def _write(self, name: str) -> None:
        log = shlex.quote(str(self.logs / f"{name}.log"))
        stdin_log = shlex.quote(str(self.logs / f"{name}.stdin"))
        lines = [
            "#!/bin/bash",
            f"printf '%s\\n' \"$*\" >> {log}",
            f"cat >> {stdin_log} || true",
        ]
        if name == "ssh":
            lines.extend(
                [
                    'if [[ "$*" == *rev-parse* && "${STUB_SSH_RC:-0}" == "0" ]]; then',
                    "  printf '%s\\n' \"${STUB_REMOTE_HEAD:-master}\"",
                    "fi",
                    'exit "${STUB_SSH_RC:-0}"',
                ]
            )
        else:
            lines.append("exit 0")
        path = self.bin / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.chmod(0o755)

    def clear(self) -> None:
        for name in STUB_NAMES:
            (self.logs / f"{name}.log").write_text("", encoding="utf-8")
            (self.logs / f"{name}.stdin").write_text("", encoding="utf-8")

    def log(self, name: str) -> str:
        return (self.logs / f"{name}.log").read_text(encoding="utf-8")

    def stdin(self, name: str) -> str:
        return (self.logs / f"{name}.stdin").read_text(encoding="utf-8")

    def run(
        self,
        *args: str,
        head: str = "master",
        allow: str | None = None,
        ssh_rc: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.clear()
        env = os.environ.copy()
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["STUB_REMOTE_HEAD"] = head
        env.pop("ALLOW_PINNED_CLONE_RSYNC", None)
        env.pop("STUB_SSH_RC", None)
        if allow is not None:
            env["ALLOW_PINNED_CLONE_RSYNC"] = allow
        if ssh_rc is not None:
            env["STUB_SSH_RC"] = ssh_rc
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
        )


class TestInstallRemotePinnedClone(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="install-remote-866-")
        self.stubs = PathStubs(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def assert_no_mutation(self) -> None:
        self.assertEqual(self.stubs.log("rsync"), "")
        self.assertEqual(self.stubs.log("scp"), "")
        self.assertEqual(self.stubs.log("systemctl"), "")
        blob = self.stubs.log("ssh") + self.stubs.stdin("ssh")
        self.assertNotIn("systemctl", blob)
        self.assertNotIn("mkdir", self.stubs.log("ssh"))
        self.assertNotIn("scp", blob)

    def test_refuses_work_treasury_before_rsync(self) -> None:
        proc = self.stubs.run("user@host", head="work/treasury")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn(
            "Refusing to rsync onto pinned clone /home/user/personal-workspace (HEAD work/treasury).",
            proc.stderr,
        )
        self.assertIn("rev-parse", self.stubs.log("ssh"))
        self.assert_no_mutation()

    def test_refuses_work_holistic_before_rsync(self) -> None:
        proc = self.stubs.run("pi@host", head="work/holistic")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("(HEAD work/holistic).", proc.stderr)
        self.assert_no_mutation()

    def test_whitespace_around_pinned_head_still_refuses(self) -> None:
        proc = self.stubs.run("user@host", head="  work/treasury  ")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assert_no_mutation()

    def test_near_miss_branch_is_not_pinned(self) -> None:
        proc = self.stubs.run("user@host", "--dry-run", head="work/treasury-notes")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Dry run: no mkdir, rsync, scp, or systemctl.", proc.stdout)
        self.assert_no_mutation()

    def test_dry_run_exits_0_without_scp_or_systemctl(self) -> None:
        proc = self.stubs.run("prism-agent@prism-gateway", "--dry-run", head="master")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Dry run: no mkdir, rsync, scp, or systemctl.", proc.stdout)
        self.assertIn("rev-parse", self.stubs.log("ssh"))
        self.assertEqual(self.stubs.log("scp"), "")
        self.assertEqual(self.stubs.log("systemctl"), "")
        self.assertNotIn("systemctl", self.stubs.stdin("ssh"))
        self.assert_no_mutation()

    def test_dry_run_does_not_bypass_pin(self) -> None:
        proc = self.stubs.run("user@host", "--dry-run", head="work/holistic")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assert_no_mutation()

    def test_dry_run_stays_non_mutating_when_override_set(self) -> None:
        proc = self.stubs.run(
            "user@host", "--dry-run", head="work/treasury", allow="1"
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Dry run: no mkdir, rsync, scp, or systemctl.", proc.stdout)
        self.assert_no_mutation()

    def test_allow_env_permits_rsync_and_still_installs_units(self) -> None:
        proc = self.stubs.run(
            "user@host", "--only", "orchestra", head="work/treasury", allow="1"
        )
        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, combined)
        self.assertIn("ALLOW_PINNED_CLONE_RSYNC=1 set; pin check skipped", proc.stdout)
        self.assertNotEqual(self.stubs.log("rsync").strip(), "")
        self.assertNotEqual(self.stubs.log("scp").strip(), "")
        self.assertIn("systemctl --user enable --now", self.stubs.stdin("ssh"))

    def test_unreadable_ssh_exits_2_before_rsync(self) -> None:
        proc = self.stubs.run("user@host", head="work/treasury", ssh_rc="255")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("could not read remote HEAD", proc.stderr)
        self.assertIn("ssh exit 255", proc.stderr)
        self.assert_no_mutation()

    def test_zero_match_only_exits_1(self) -> None:
        proc = self.stubs.run("user@host", "--only", "zzznomatch", head="work/treasury")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("No units matched --only zzznomatch", proc.stderr)
        self.assertEqual(self.stubs.log("ssh"), "")
        self.assert_no_mutation()

    def test_zero_match_only_beats_dry_run(self) -> None:
        proc = self.stubs.run(
            "user@host", "--only", "zzznomatch", "--dry-run", head="master"
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("No units matched", proc.stderr)
        self.assertEqual(self.stubs.log("ssh"), "")
        self.assert_no_mutation()


if __name__ == "__main__":
    unittest.main()
