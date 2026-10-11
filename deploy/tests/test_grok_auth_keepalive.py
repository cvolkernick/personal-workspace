#!/usr/bin/env python3
"""Tests for deploy/grok_auth_keepalive.sh (#1086).

Every run uses a temp HOME, a temp auth.json, and a fake grok CLI. The real
~/.grok/auth.json of the runner is passed to the script only as
GROK_AUTH_REFUSE_PATH, which makes the script refuse it before any read.
"""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "grok_auth_keepalive.sh"
SERVICE = ROOT / "deploy" / "units" / "grok-auth-keepalive.service"
TIMER = ROOT / "deploy" / "units" / "grok-auth-keepalive.timer"
SECRET = "SUPERSECRETTOKENVALUE"
REFRESH = "REFRESHSECRETVALUE"
REAL_AUTH = str(Path(pwd.getpwuid(os.getuid()).pw_dir) / ".grok" / "auth.json")

# Fake CLI. Like the real one, it checks the token at up to three points
# (init, watcher, pre-request) and refreshes each time it is inside
# GROK_AUTH_EARLY_INVALIDATION_SECS. Each refresh appends to refreshes.log.
FAKE_GROK = r'''#!{python}
import json, os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

root = Path(os.environ["FAKE_ROOT"])
mode = (root / "mode").read_text().strip()
auth = Path(os.environ["GROK_AUTH_JSON"])
log = root / "logs" / "unified.jsonl"
(root / "invocations.log").open("a").write("models\n")


def iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse(raw):
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def refresh(hours=6.0):
    data = json.loads(auth.read_text())
    for entry in data.values():
        entry["expires_at"] = iso(datetime.now(timezone.utc) + timedelta(hours=hours))
        entry["key"] = entry["key"] + "x"
    auth.write_text(json.dumps(data))
    with (root / "refreshes.log").open("a") as fh:
        fh.write("refresh\n")


def maybe_refresh():
    early = int(os.environ.get("GROK_AUTH_EARLY_INVALIDATION_SECS", "0"))
    data = json.loads(auth.read_text())
    entry = next(iter(data.values()))
    left = (parse(entry["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
    if left < early:
        refresh()


if mode == "fail":
    sys.exit(9)
if mode == "noop":
    sys.exit(0)
if mode == "hang":
    time.sleep(30)
    sys.exit(0)
if mode == "short":
    refresh(hours=10 / 60)
    sys.exit(0)
if mode == "revoked":
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as fh:
        fh.write('{"msg":"auth.refresh error invalid_grant"}\n')
        fh.write('{"msg":"auth.refresh.permanent_failure RefreshTokenRejected"}\n')
    auth.unlink()
    print("You are not authenticated.")
    sys.exit(0)
if mode == "notauth-renew":
    # What the real CLI does for a due token: prints the message, then the
    # PreRequest refresh renews it.
    print("You are not authenticated.")
    maybe_refresh()
    sys.exit(0)
if mode == "notauth-noop":
    print("You are not authenticated.")
    sys.exit(0)
if mode == "rejected-but-renewed":
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as fh:
        fh.write('{"msg":"auth.refresh error invalid_grant","ctx":{"rt_prefix":"RTPREFIXLEAK1"}}\n')
    maybe_refresh()
    sys.exit(0)
if mode == "hang-child":
    import subprocess as sp
    child = sp.Popen(["sleep", "60"])
    (root / "child.pid").write_text(str(child.pid))
    time.sleep(30)
    sys.exit(0)
if mode == "slow":
    # Refresh still in flight well past the soft timeout.
    time.sleep(float(os.environ.get("FAKE_SLOW_SECS", "3")))
    maybe_refresh()
    (root / "slow-done").write_text("1")
    sys.exit(0)
for _ in range(3):
    maybe_refresh()
sys.exit(0)
'''


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class KeepaliveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        (self.home / ".grok").mkdir(parents=True)
        self.auth = self.home / ".grok" / "auth.json"
        self.state = self.root / "state"
        self.alerts = self.root / "alerts.log"
        self.grok = self.root / "grok"
        self.lock = self.root / "run" / "grok-auth-keepalive.lock"
        self.grok.write_text(FAKE_GROK.replace("{python}", sys.executable), encoding="utf-8")
        self.grok.chmod(self.grok.stat().st_mode | stat.S_IEXEC)
        self.set_mode("renew")
        self.write_auth(hours=1)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # helpers

    def set_mode(self, mode: str) -> None:
        (self.root / "mode").write_text(mode, encoding="utf-8")

    def write_auth(self, *, hours: float) -> None:
        body = {
            "https://auth.example/client": {
                "key": SECRET,
                "refresh_token": REFRESH,
                "auth_mode": "oidc",
                "expires_at": _iso(datetime.now(timezone.utc) + timedelta(hours=hours)),
            }
        }
        self.auth.write_text(json.dumps(body), encoding="utf-8")
        self.auth.chmod(0o600)

    def env(self, **extra: str) -> dict[str, str]:
        base = {
            "HOME": str(self.home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "FAKE_ROOT": str(self.root),
            "GROK_AUTH_BIN": str(self.grok),
            "GROK_AUTH_JSON": str(self.auth),
            "GROK_AUTH_CLI_LOG": str(self.root / "logs" / "unified.jsonl"),
            "GROK_AUTH_STATE_DIR": str(self.state),
            "GROK_AUTH_LOCK_FILE": str(self.lock),
            "GROK_AUTH_ALERT_LOG": str(self.alerts),
            "GROK_AUTH_ALERT_CMD": 'printf "%s\\n" "$GROK_AUTH_ALERT_REASON" >> "$GROK_AUTH_ALERT_LOG"; '
            'printf "%s" "$GROK_AUTH_ALERT_TEXT" >> "$GROK_AUTH_ALERT_LOG.text"',
            "GROK_AUTH_TIMEOUT_SECS": "1",
            "GROK_AUTH_HARD_TIMEOUT_SECS": "20",
            "GROK_AUTH_WORKSPACE": str(self.root),
            "GROK_AUTH_REFUSE_PATH": REAL_AUTH,
        }
        base.update(extra)
        return {k: v for k, v in base.items() if v is not None}

    def run_script(self, **extra: str) -> subprocess.CompletedProcess[str]:
        env = self.env(**extra)
        effective = env.get("GROK_AUTH_JSON") or str(Path(env["HOME"]) / ".grok" / "auth.json")
        self.assertNotEqual(os.path.realpath(effective), os.path.realpath(REAL_AUTH))
        self.assertTrue(effective.startswith(self.tmp.name), effective)
        result = subprocess.run(
            ["bash", str(SCRIPT)], env=env, text=True, capture_output=True, check=False, timeout=60
        )
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNotIn(REFRESH, result.stdout + result.stderr)
        return result

    def refreshes(self) -> int:
        p = self.root / "refreshes.log"
        return len(p.read_text().splitlines()) if p.exists() else 0

    def invocations(self) -> int:
        p = self.root / "invocations.log"
        return len(p.read_text().splitlines()) if p.exists() else 0

    def alert_lines(self) -> list[str]:
        return self.alerts.read_text().splitlines() if self.alerts.exists() else []

    def expires(self) -> str:
        return next(iter(json.loads(self.auth.read_text()).values()))["expires_at"]

    # window

    def test_window_is_under_token_life_and_over_timer_gap(self) -> None:
        unit = SERVICE.read_text()
        self.assertIn("Environment=GROK_AUTH_EARLY_INVALIDATION_SECS=10800", unit)
        self.assertNotIn("25200", unit)
        self.assertRegex(SCRIPT.read_text(), r'EARLY="\$\{GROK_AUTH_EARLY_INVALIDATION_SECS:-10800\}"')
        self.assertIn("OnUnitActiveSec=2h", TIMER.read_text())
        hard = int(re.search(r"GROK_AUTH_HARD_TIMEOUT_SECS=(\d+)", unit).group(1))
        start = int(re.search(r"TimeoutStartSec=(\d+)", unit).group(1))
        self.assertLess(hard, start)

    # not due / due

    def test_not_due_does_not_start_cli(self) -> None:
        self.write_auth(hours=5)
        before = self.expires()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("outcome=fresh", result.stdout)
        self.assertEqual(self.invocations(), 0)
        self.assertEqual(self.refreshes(), 0)
        self.assertEqual(self.expires(), before)
        self.assertEqual(self.alert_lines(), [])

    def test_due_refreshes_exactly_once_with_default_window(self) -> None:
        before = self.expires()
        result = self.run_script(GROK_AUTH_EARLY_INVALIDATION_SECS=None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("outcome=renewed", result.stdout)
        self.assertEqual(self.invocations(), 1)
        self.assertEqual(self.refreshes(), 1)
        self.assertGreater(self.expires(), before)
        # Second run right after: not due, no CLI, no refresh.
        again = self.run_script(GROK_AUTH_EARLY_INVALIDATION_SECS=None)
        self.assertIn("outcome=fresh", again.stdout)
        self.assertEqual(self.refreshes(), 1)

    def test_old_7h_window_would_refresh_more_than_once(self) -> None:
        # Documents the #1088 bug the fake reproduces.
        self.run_script(GROK_AUTH_EARLY_INVALIDATION_SECS="25200")
        self.assertGreater(self.refreshes(), 1)

    # lock / concurrency

    def test_held_lock_exits_clean_without_refresh(self) -> None:
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        with self.lock.open("a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("outcome=busy detail=lock-held", result.stdout)
        self.assertEqual(self.invocations(), 0)
        self.assertEqual(self.refreshes(), 0)
        self.assertEqual(self.alert_lines(), [])

    def test_running_cli_exits_clean_without_refresh(self) -> None:
        self.set_mode("hang")
        proc = subprocess.Popen([str(self.grok), "chat"], env=self.env(), stdout=subprocess.DEVNULL)
        try:
            time.sleep(1.0)
            self.set_mode("renew")
            result = self.run_script()
        finally:
            proc.kill()
            proc.wait()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("outcome=busy detail=cli-running", result.stdout)
        self.assertEqual(self.refreshes(), 0)

    def test_in_flight_refresh_is_not_abandoned(self) -> None:
        self.set_mode("slow")
        result = self.run_script(FAKE_SLOW_SECS="3", GROK_AUTH_TIMEOUT_SECS="1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("outcome=waiting", result.stdout)
        self.assertIn("outcome=renewed", result.stdout)
        self.assertTrue((self.root / "slow-done").exists())
        self.assertEqual(self.refreshes(), 1)

    def test_hard_timeout_stops_cli_and_alerts_once(self) -> None:
        self.set_mode("hang")
        result = self.run_script(GROK_AUTH_HARD_TIMEOUT_SECS="2")
        self.assertEqual(result.returncode, 1)
        self.assertIn("outcome=timeout", result.stdout)
        self.assertEqual(self.alert_lines(), ["timeout"])
        self.assertEqual(self.invocations(), 1)

    # logged out

    def test_missing_auth_alerts_once_per_state_change(self) -> None:
        self.auth.unlink()
        first = self.run_script()
        second = self.run_script()
        self.assertEqual(first.returncode, 1)
        self.assertEqual(second.returncode, 1)
        self.assertIn("outcome=alerted reason=logged-out detail=auth-missing", first.stdout)
        self.assertIn("outcome=alert-suppressed reason=logged-out", second.stdout)
        self.assertEqual(self.alert_lines(), ["logged-out"])
        self.assertIn("Chris must re-login: grok login --device-code", (self.root / "alerts.log.text").read_text())
        self.assertEqual(self.invocations(), 0)
        # Back to healthy clears the marker, so a later logout alerts again.
        self.write_auth(hours=5)
        self.assertEqual(self.run_script().returncode, 0)
        self.auth.unlink()
        self.run_script()
        self.assertEqual(self.alert_lines(), ["logged-out", "logged-out"])

    def test_rejected_refresh_alerts_once(self) -> None:
        self.set_mode("revoked")
        first = self.run_script()
        self.assertEqual(first.returncode, 1)
        self.assertIn("reason=logged-out", first.stdout)
        self.assertIn("grok login --device-code", first.stdout)
        second = self.run_script()
        self.assertEqual(second.returncode, 1)
        self.assertIn("alert-suppressed reason=logged-out", second.stdout)
        self.assertEqual(self.alert_lines(), ["logged-out"])
        self.assertEqual(self.invocations(), 1)

    def test_old_log_lines_are_not_a_logout(self) -> None:
        log = self.root / "logs" / "unified.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text('{"msg":"old invalid_grant before this run"}\n')
        self.set_mode("noop")
        # Old log lines alone are not a logout: the run reports not-advanced.
        result = self.run_script()
        self.assertIn("outcome=expires-not-advanced", result.stdout)
        self.assertNotIn("logged-out", result.stdout)

    def test_not_authenticated_text_then_renew_is_renewed(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "logged-out").write_text("0 cli-not-authenticated\n")
        self.set_mode("notauth-renew")
        before = self.expires()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("outcome=renewed exit=0", result.stdout)
        self.assertNotIn("logged-out", result.stdout)
        self.assertGreater(self.expires(), before)
        self.assertEqual(self.refreshes(), 1)
        self.assertEqual(self.alert_lines(), [])
        self.assertFalse((self.state / "logged-out").exists())

    def test_not_authenticated_text_without_advance_alerts_once(self) -> None:
        self.set_mode("notauth-noop")
        first = self.run_script()
        second = self.run_script()
        self.assertEqual(first.returncode, 1)
        self.assertEqual(second.returncode, 1)
        self.assertIn("outcome=logged-out detail=cli-not-authenticated exit=1", first.stdout)
        self.assertIn("alert-suppressed reason=logged-out", second.stdout)
        self.assertEqual(self.alert_lines(), ["logged-out"])

    def test_rejection_line_with_advance_is_renewed_and_not_echoed(self) -> None:
        self.set_mode("rejected-but-renewed")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("outcome=renewed", result.stdout)
        self.assertEqual(self.alert_lines(), [])
        self.assertNotIn("RTPREFIXLEAK1", result.stdout + result.stderr)

    def test_failure_lines_report_script_exit(self) -> None:
        self.set_mode("noop")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("outcome=expires-not-advanced exit=1 cli_exit=0", result.stdout)
        self.set_mode("short")
        self.write_auth(hours=0.05)
        short = self.run_script()
        self.assertEqual(short.returncode, 1)
        self.assertIn("outcome=expires-short exit=1 cli_exit=0", short.stdout)
        for line in (result.stdout + short.stdout).splitlines():
            if line.startswith(("outcome=expires", "outcome=logged-out", "outcome=timeout")):
                self.assertNotIn(" exit=0", line)

    def test_hard_timeout_kills_cli_children(self) -> None:
        self.set_mode("hang-child")
        result = self.run_script(GROK_AUTH_HARD_TIMEOUT_SECS="2")
        self.assertEqual(result.returncode, 1)
        self.assertIn("outcome=timeout exit=1 cli_exit=124", result.stdout)
        pid = int((self.root / "child.pid").read_text())
        deadline = time.time() + 5
        alive = True
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
                break
            # Reaped by init? A zombie still answers kill 0; check its state.
            try:
                if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
                    alive = False
                    break
            except OSError:
                alive = False
                break
            time.sleep(0.1)
        self.assertFalse(alive, "CLI child survived the hard timeout")

    def test_guard_resolves_symlink_to_real_auth(self) -> None:
        link = self.root / "auth-link.json"
        link.symlink_to(REAL_AUTH)
        env = self.env(GROK_AUTH_JSON=str(link))
        result = subprocess.run(
            ["bash", str(SCRIPT)], env=env, text=True, capture_output=True, check=False, timeout=30
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("outcome=refused-live-auth", result.stdout)
        self.assertEqual(self.invocations(), 0)

    # other failures

    def test_failure_alerts_once(self) -> None:
        self.set_mode("fail")
        first = self.run_script()
        second = self.run_script()
        self.assertEqual(first.returncode, 1)
        self.assertEqual(second.returncode, 1)
        self.assertIn("outcome=alerted reason=exit-9", first.stdout)
        self.assertIn("outcome=alert-suppressed reason=exit-9", second.stdout)
        self.assertEqual(self.alert_lines(), ["exit-9"])

    def test_stale_expires_alerts(self) -> None:
        self.set_mode("noop")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("outcome=expires-not-advanced", result.stdout)

    def test_short_expiry_alerts(self) -> None:
        self.write_auth(hours=0.05)
        self.set_mode("short")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("outcome=expires-short", result.stdout)

    def test_missing_binary_alerts(self) -> None:
        result = self.run_script(GROK_AUTH_BIN=str(self.root / "missing-grok"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("outcome=missing-bin", result.stdout)

    # live-file guard

    def test_guard_refuses_real_auth_path(self) -> None:
        env = self.env(GROK_AUTH_JSON=REAL_AUTH)
        result = subprocess.run(
            ["bash", str(SCRIPT)], env=env, text=True, capture_output=True, check=False, timeout=30
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("outcome=refused-live-auth", result.stdout)
        self.assertEqual(self.invocations(), 0)
        self.assertFalse(self.state.exists())

    def test_default_auth_path_follows_temp_home(self) -> None:
        self.write_auth(hours=5)
        result = self.run_script(GROK_AUTH_JSON=None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("outcome=fresh", result.stdout)

    def test_helper_rejects_real_default_path(self) -> None:
        with self.assertRaises(AssertionError):
            self.run_script(GROK_AUTH_JSON=REAL_AUTH)
        with self.assertRaises(AssertionError):
            self.run_script(GROK_AUTH_JSON=None, HOME=str(Path(REAL_AUTH).parents[1]))


if __name__ == "__main__":
    unittest.main()
