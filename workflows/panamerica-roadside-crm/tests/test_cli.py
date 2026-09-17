from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
RUN = PKG / "run.py"
FIXTURE = PKG / "tests" / "fixtures" / "photo_sets.json"


class CliTests(unittest.TestCase):
    def test_help_documents_harness_args(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(RUN), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        help_text = proc.stdout
        for token in ("--dry-run", "--live", "--fixture", "--store"):
            self.assertIn(token, help_text)

    def test_dry_run_weekly_pass_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store.json"
            weekly = subprocess.run(
                [
                    sys.executable,
                    str(RUN),
                    "--dry-run",
                    "--store",
                    str(store),
                    "--fixture",
                    str(FIXTURE),
                    "weekly-pass",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(weekly.returncode, 0, weekly.stderr + weekly.stdout)
            payload = json.loads(weekly.stdout)
            created_phones = {row["phone"] for row in payload["created"]}
            self.assertIn("2395550101", created_phones)
            self.assertIn("2395550199", created_phones)
            self.assertEqual(len(payload["needs_info"]), 1)

            ran = subprocess.run(
                [
                    sys.executable,
                    str(RUN),
                    "--dry-run",
                    "--store",
                    str(store),
                    "--fixture",
                    str(FIXTURE),
                    "--simulate-replies",
                    "--simulate-interest",
                    "run",
                ],
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "PANAMERICA_ROADSIDE_LIVE": ""},
            )
            self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
            body = json.loads(ran.stdout)
            self.assertTrue(body["dry_run"])
            self.assertGreaterEqual(body["counts"].get("interested", 0), 1)
