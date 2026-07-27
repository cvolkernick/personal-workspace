"""Tests for domain server launch argv builder."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
ROOT = ORCH.parent
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

from launcher import build_launch_argv, domain_spec, probe_port  # noqa: E402


class LaunchArgvTests(unittest.TestCase):
    def test_finance_no_host_flag(self) -> None:
        cmd = build_launch_argv(
            "finance", ROOT / "financial-command" / "server.py", 8000
        )
        self.assertNotIn("--host", cmd)
        self.assertIn("--port", cmd)
        self.assertIn("8000", cmd)
        self.assertIn("--no-browser", cmd)

    def test_fitness_positional_port(self) -> None:
        cmd = build_launch_argv(
            "fitness", ROOT / "resistance-dashboard" / "server.py", 8787
        )
        self.assertEqual(cmd[-1], "8787")
        self.assertNotIn("--port", cmd)
        self.assertNotIn("--host", cmd)

    def test_workflow_uses_bind(self) -> None:
        cmd = build_launch_argv(
            "workflow",
            ROOT / "projects-dashboard" / "server.py",
            8765,
            bind_host="0.0.0.0",
        )
        self.assertIn("--bind", cmd)
        self.assertIn("0.0.0.0", cmd)

    def test_holistic_uses_host(self) -> None:
        cmd = build_launch_argv(
            "holistic", ROOT / "holistic" / "server.py", 8770, bind_host="127.0.0.1"
        )
        self.assertIn("--host", cmd)
        self.assertIn("127.0.0.1", cmd)

    def test_horizon_launch_argv(self) -> None:
        cmd = build_launch_argv(
            "horizon", ROOT / "horizon" / "server.py", 8791, bind_host="127.0.0.1"
        )
        self.assertIn("--port", cmd)
        self.assertIn("8791", cmd)
        self.assertIn("--no-browser", cmd)
        self.assertEqual(domain_spec("season")["id"], "horizon")
        self.assertEqual(domain_spec("horizon")["label"], "Seasonal plan")

    def test_horizon_macro_launch_argv(self) -> None:
        cmd = build_launch_argv(
            "horizon_macro",
            ROOT / "research" / "horizon" / "server.py",
            8795,
            bind_host="127.0.0.1",
        )
        self.assertIn("--port", cmd)
        self.assertIn("8795", cmd)
        self.assertIn("--bootstrap", cmd)
        self.assertNotIn("--host", cmd)
        self.assertEqual(domain_spec("macro")["id"], "horizon_macro")
        self.assertEqual(domain_spec("horizon_macro")["label"], "Horizon Macro")

    def test_b2_launch_argv_no_no_browser(self) -> None:
        cmd = build_launch_argv(
            "b2", ROOT / "b2-ux" / "server.py", 8792, bind_host="127.0.0.1"
        )
        self.assertIn("--host", cmd)
        self.assertIn("127.0.0.1", cmd)
        self.assertIn("--port", cmd)
        self.assertIn("8792", cmd)
        self.assertNotIn("--no-browser", cmd)
        self.assertEqual(domain_spec("obsidian")["id"], "b2")
        self.assertEqual(domain_spec("brain2")["id"], "b2")

    def test_domain_spec_aliases(self) -> None:
        self.assertEqual(domain_spec("treasury")["id"], "finance")
        self.assertEqual(domain_spec("resistance")["id"], "fitness")
        self.assertEqual(domain_spec("b2-ux")["id"], "b2")

    def test_probe_localhost_8790_or_skip(self) -> None:
        # Soft check — don't fail suite if orchestrator isn't running
        live = probe_port(8790)
        self.assertIsInstance(live, bool)


if __name__ == "__main__":
    unittest.main()
