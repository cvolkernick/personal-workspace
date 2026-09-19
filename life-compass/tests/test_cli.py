"""CLI one-thing / missing / sweep against a fixture snapshot."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
CLI = PKG / "cli.py"
ROOT = PKG.parent


class TestCli(unittest.TestCase):
    def test_sweep_quiet_snapshot_zero_pings(self) -> None:
        snap = {
            "as_of": "2026-09-19T14:00:00+00:00",
            "fitness": {"wired": True, "days_since_resistance": 1},
            "financial": {"wired": True, "bills": [], "categories": [], "payoff": []},
            "operations": {
                "turo_trips": [],
                "invoice_ready": {"wired": True, "unassigned": []},
            },
            "radar": {"candidates": []},
        }
        with tempfile.TemporaryDirectory() as td:
            snap_path = Path(td) / "snap.json"
            state_path = Path(td) / "state.json"
            snap_path.write_text(json.dumps(snap), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "--state",
                    str(state_path),
                    "sweep",
                    "--snapshot",
                    str(snap_path),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout)
            self.assertEqual(out["ping_count"], 0)
            self.assertTrue(out["quiet"])

            proc2 = subprocess.run(
                [sys.executable, str(CLI), "--state", str(state_path), "one-thing"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            ot = json.loads(proc2.stdout)
            self.assertEqual(ot["kind"], "all-quiet")

            proc3 = subprocess.run(
                [sys.executable, str(CLI), "--state", str(state_path), "missing"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            miss = json.loads(proc3.stdout)
            self.assertFalse(miss["missing"])
