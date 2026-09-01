#!/usr/bin/env python3
"""Unit tests for deploy/map_changed_paths.py (issue #25 path-scoped auto-deploy)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = ROOT / "deploy" / "map_changed_paths.py"


def _load():
    spec = importlib.util.spec_from_file_location("map_changed_paths", MOD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["map_changed_paths"] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()


class TestClassifyPaths(unittest.TestCase):
    def test_single_dashboard_prefix(self):
        r = M.classify_paths(["projects-dashboard/server.py"])
        self.assertEqual(r["action"], "restart")
        self.assertEqual(r["units"], ["workflow-dashboard.service"])
        self.assertIn("workflow", r["only"])
        self.assertFalse(r["thrash_all"])

    def test_iot_maps_only_iot(self):
        r = M.classify_paths(["iot/server.py", "iot/wiz_adapter.py"])
        self.assertEqual(r["action"], "restart")
        self.assertEqual(r["units"], ["iot-dashboard.service"])
        self.assertEqual(len(r["units"]), 1)

    def test_treasury_is_manual_never_restart(self):
        r = M.classify_paths(["treasury/config.json", "treasury/snapshots/x.json"])
        self.assertEqual(r["action"], "manual")
        self.assertEqual(r["units"], [])
        self.assertTrue(r["manual_paths"])

    def test_secrets_glob_manual(self):
        r = M.classify_paths(["iot/secrets.json"])
        self.assertEqual(r["action"], "manual")
        self.assertEqual(r["units"], [])

    def test_deploy_glue_manual(self):
        r = M.classify_paths(["deploy/install_remote.sh", "deploy/units/iot-dashboard.service"])
        self.assertEqual(r["action"], "manual")
        self.assertEqual(r["units"], [])

    def test_markdown_noop(self):
        r = M.classify_paths(["README.md", "projects-dashboard/NOTES.md"])
        self.assertEqual(r["action"], "noop")
        self.assertEqual(r["units"], [])

    def test_multi_prefix_only_those_units(self):
        r = M.classify_paths(
            ["orchestra/server.py", "holistic/server.py", "README.md"]
        )
        self.assertEqual(r["action"], "restart")
        self.assertEqual(
            set(r["units"]),
            {"orchestra-dashboard.service", "holistic-dashboard.service"},
        )
        self.assertFalse(r["thrash_all"])

    def test_shared_glue_restarts_auto_units_not_treasury(self):
        r = M.classify_paths(["remote_backend.py"])
        self.assertEqual(r["action"], "restart")
        self.assertIn("orchestra-dashboard.service", r["units"])
        self.assertIn("workflow-dashboard.service", r["units"])
        self.assertGreaterEqual(len(r["units"]), 5)
        # Must not invent a treasury unit
        self.assertFalse(any("treasury" in u for u in r["units"]))
        self.assertFalse(r["thrash_all"])

    def test_mixed_auto_and_manual_still_path_scoped(self):
        r = M.classify_paths(
            ["iot/server.py", "treasury/config.json", "deploy/README.md"]
        )
        self.assertEqual(r["action"], "restart")
        self.assertEqual(r["units"], ["iot-dashboard.service"])
        self.assertTrue(r["manual_paths"])

    def test_unmapped_safe_default(self):
        r = M.classify_paths(["strategy/bets.md"])  # md ignored first
        # .md ignored → noop
        self.assertEqual(r["action"], "noop")
        r2 = M.classify_paths(["strategy/unknown_module.py"])
        self.assertEqual(r2["action"], "unmapped")
        self.assertEqual(r2["units"], [])

    def test_horizon_prefix(self):
        r = M.classify_paths(["research/horizon/server.py"])
        self.assertEqual(r["action"], "restart")
        self.assertEqual(r["units"], ["horizon-dashboard.service"])

    def test_auto_fleet_prefix(self):
        r = M.classify_paths(["auto-fleet/server.py", "auto-fleet/turo_gmail.py"])
        self.assertEqual(r["action"], "restart")
        self.assertEqual(r["units"], ["auto-fleet.service"])
        self.assertEqual(r["only"], "auto-fleet")
        self.assertFalse(r["thrash_all"])

    def test_oomwoo_prefix(self):
        r = M.classify_paths(["oomwoo/server.py", "oomwoo/parse.py"])
        self.assertEqual(r["action"], "restart")
        self.assertEqual(r["units"], ["oomwoo-dashboard.service"])
        self.assertEqual(r["only"], "oomwoo")
        self.assertFalse(r["thrash_all"])

    def test_shared_glue_includes_auto_fleet(self):
        r = M.classify_paths(["remote_backend.py"])
        self.assertIn("auto-fleet.service", r["units"])
        self.assertIn("oomwoo-dashboard.service", r["units"])
        self.assertFalse(any("treasury" in u for u in r["units"]))

    def test_never_thrash_all_flag(self):
        r = M.classify_paths(["iot/server.py"])
        self.assertFalse(r["thrash_all"])
        r2 = M.classify_paths(["remote_backend.py"])
        self.assertFalse(r2["thrash_all"])


if __name__ == "__main__":
    unittest.main()
