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
FIXTURE = PKG / "tests" / "fixtures" / "places_listings.json"


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
        for token in ("--geo", "--category", "--batch-size", "--dry-run"):
            self.assertIn(token, help_text)

    def test_dry_run_source_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store.json"
            src = subprocess.run(
                [
                    sys.executable,
                    str(RUN),
                    "--geo",
                    "Austin, TX",
                    "--category",
                    "",
                    "--batch-size",
                    "10",
                    "--dry-run",
                    "--store",
                    str(store),
                    "--fixture",
                    str(FIXTURE),
                    "source",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(src.returncode, 0, src.stderr + src.stdout)
            payload = json.loads(src.stdout)
            self.assertGreaterEqual(payload["count"], 2)
            names = {row["name"] for row in payload["leads"]}
            self.assertIn("Oak Street Bakery", names)
            self.assertNotIn("Pixel Plumbing", names)
            for row in payload["leads"]:
                self.assertTrue(row["name"])
                self.assertTrue(row["address"])
                self.assertTrue(row["phone"])
                self.assertTrue(row["hours"])
                self.assertTrue(row["category"])
                self.assertEqual(row["website"], "")

            ran = subprocess.run(
                [
                    sys.executable,
                    str(RUN),
                    "--geo",
                    "Austin, TX",
                    "--batch-size",
                    "10",
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
                env={**os.environ, "DEMO_SITE_OUTREACH_LIVE": ""},
            )
            self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
            body = json.loads(ran.stdout)
            self.assertTrue(body["dry_run"])
            self.assertGreaterEqual(body["counts"].get("handed_off", 0), 1)
