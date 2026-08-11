"""Launcher domain resolve + argv builder (nav v1 ensure-and-open)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
ROOT = ORCH.parent
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

from launcher import (  # noqa: E402
    build_launch_argv,
    domain_spec,
    probe_port,
    status_all,
)


class LauncherTests(unittest.TestCase):
    def test_domain_aliases(self) -> None:
        self.assertEqual(domain_spec("fcc")["id"], "finance")
        self.assertEqual(domain_spec("fitdash")["id"], "fitness")
        self.assertEqual(domain_spec("macro")["id"], "horizon")
        self.assertEqual(domain_spec("seasonal")["id"], "seasonal")
        self.assertEqual(domain_spec("b2")["port"], 8792)

    def test_fitness_positional_port(self) -> None:
        cmd = build_launch_argv(
            "fitness", ROOT / "resistance-dashboard" / "server.py", 8787
        )
        self.assertEqual(cmd[-1], "8787")
        self.assertNotIn("--port", cmd)

    def test_workflow_bind(self) -> None:
        cmd = build_launch_argv(
            "workflow", ROOT / "projects-dashboard" / "server.py", 8765
        )
        self.assertIn("--bind", cmd)
        self.assertIn("--port", cmd)

    def test_status_all_shape(self) -> None:
        st = status_all()
        self.assertTrue(st["ok"])
        self.assertIsInstance(st["domains"], list)
        self.assertGreaterEqual(st["total"], 5)

    def test_probe_port_closed(self) -> None:
        # Unlikely anything listens on this high port in tests
        self.assertFalse(probe_port(59999, timeout=0.05))


if __name__ == "__main__":
    unittest.main()
