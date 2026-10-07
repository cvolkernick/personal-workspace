#!/usr/bin/env python3
"""Tests for deploy/grok_auth_keepalive.sh (#1086)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "grok_auth_keepalive.sh"
SECRET = "SUPERSECRETTOKENVALUE"
REFRESH = "REFRESHSECRETVALUE"


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class KeepaliveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.auth = self.root / "auth.json"
        self.state = self.root / "state"
        self.alerts = self.root / "alerts.log"
        self.grok = self.root / "grok"
        self.mode = self.root / "mode"
        self.write_auth(hours=1)
        self.write_grok()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_auth(self, *, hours: float, key: str = SECRET) -> None:
        expires = _iso(datetime.now(timezone.utc) + timedelta(hours=hours))
        body = {
            "https://auth.example/client": {
                "key": key,
                "refresh_token": REFRESH,
                "auth_mode": "oidc",
                "expires_at": expires,
            }
        }
        self.auth.write_text(json.dumps(body), encoding="utf-8")
        self.auth.chmod(0o600)

    def write_grok(self) -> None:
        self.grok.write_text(
            """#!/bin/bash
set -euo pipefail
mode="$(cat "$GROK_AUTH_MODE")"
if [[ "$mode" == "fail" ]]; then
  exit 9
fi
if [[ "$mode" == "noop" ]]; then
  exit 0
fi
if [[ "$mode" == "short" ]]; then
  python3 - << 'PY'
import json, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
p = Path(os.environ["GROK_AUTH_JSON"])
d = json.loads(p.read_text())
for entry in d.values():
    if isinstance(entry, dict) and "expires_at" in entry:
        when = datetime.now(timezone.utc) + timedelta(minutes=10)
        entry["expires_at"] = when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
p.write_text(json.dumps(d))
PY
  exit 0
fi
python3 - << 'PY'
import json, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
p = Path(os.environ["GROK_AUTH_JSON"])
d = json.loads(p.read_text())
for entry in d.values():
    if isinstance(entry, dict) and "expires_at" in entry:
        when = datetime.now(timezone.utc) + timedelta(hours=6)
        entry["expires_at"] = when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
p.write_text(json.dumps(d))
PY
exit 0
""",
            encoding="utf-8",
        )
        self.grok.chmod(self.grok.stat().st_mode | stat.S_IEXEC)
        self.mode.write_text("renew", encoding="utf-8")

    def env(self, **extra: str) -> dict[str, str]:
        base = os.environ.copy()
        base.update(
            {
                "HOME": str(self.root),
                "GROK_AUTH_BIN": str(self.grok),
                "GROK_AUTH_JSON": str(self.auth),
                "GROK_AUTH_STATE_DIR": str(self.state),
                "GROK_AUTH_MODE": str(self.mode),
                "GROK_AUTH_ALERT_LOG": str(self.alerts),
                "GROK_AUTH_ALERT_CMD": 'printf "%s\\n" "$GROK_AUTH_ALERT_REASON" >> "$GROK_AUTH_ALERT_LOG"',
                "GROK_AUTH_EARLY_INVALIDATION_SECS": "25200",
                "GROK_AUTH_TIMEOUT_SECS": "5",
                "GROK_AUTH_MIN_REMAINING_SECS": "3600",
                "GROK_AUTH_ALERT_DEDUPE_SECS": "21600",
                "GROK_AUTH_WORKSPACE": str(self.root),
                "PATH": "/usr/bin:/bin",
            }
        )
        base.update(extra)
        return base

    def run_script(self, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=self.env(**extra),
            text=True,
            capture_output=True,
            check=False,
        )

    def expires(self) -> str:
        data = json.loads(self.auth.read_text(encoding="utf-8"))
        return next(iter(data.values()))["expires_at"]

    def test_renews_and_hides_secrets(self) -> None:
        before = self.expires()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("outcome=renewed", result.stdout)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNotIn(REFRESH, result.stdout + result.stderr)
        self.assertGreater(self.expires(), before)
        self.assertFalse(self.alerts.exists())

    def test_fresh_token_does_not_require_a_move(self) -> None:
        self.write_auth(hours=10)
        self.mode.write_text("noop", encoding="utf-8")
        before = self.expires()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("outcome=fresh", result.stdout)
        self.assertEqual(self.expires(), before)
        self.assertFalse(self.alerts.exists())

    def test_failure_alerts_once(self) -> None:
        self.mode.write_text("fail", encoding="utf-8")
        first = self.run_script()
        second = self.run_script()
        self.assertEqual(first.returncode, 1)
        self.assertEqual(second.returncode, 1)
        self.assertIn("outcome=alerted reason=exit-9", first.stdout)
        self.assertIn("outcome=alert-suppressed reason=exit-9", second.stdout)
        self.assertEqual(self.alerts.read_text(encoding="utf-8").splitlines(), ["exit-9"])
        self.assertNotIn(SECRET, first.stdout + second.stdout)

    def test_stale_expires_alerts(self) -> None:
        self.mode.write_text("noop", encoding="utf-8")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("outcome=expires-not-advanced", result.stdout)
        self.assertIn("expires-not-advanced", self.alerts.read_text(encoding="utf-8"))

    def test_short_expiry_alerts(self) -> None:
        self.write_auth(hours=0.02)
        self.mode.write_text("short", encoding="utf-8")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("outcome=expires-short", result.stdout)

    def test_timeout_alerts(self) -> None:
        shim = self.root / "timeout-shim"
        shim.write_text("#!/bin/bash\nexit 124\n", encoding="utf-8")
        shim.chmod(0o755)
        result = self.run_script(GROK_AUTH_TIMEOUT_BIN=str(shim))
        self.assertEqual(result.returncode, 1)
        self.assertIn("outcome=timeout", result.stdout)
        self.assertEqual(self.alerts.read_text(encoding="utf-8").strip(), "timeout")

    def test_missing_binary_alerts(self) -> None:
        result = self.run_script(GROK_AUTH_BIN=str(self.root / "missing-grok"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("outcome=missing-bin", result.stdout)


if __name__ == "__main__":
    unittest.main()
