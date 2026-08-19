"""Prove Vercel dashboard reads fitness/nutrition/targets.json, not {}."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.calorie_bars import build_calorie_bars_payload
from rt_dashboard.nutrition_planner import DEFAULT_TARGETS, TARGETS_PATH
from rt_dashboard.nutrition_store import load_workspace_targets


REPO_TARGETS = Path(__file__).resolve().parents[2] / TARGETS_PATH
BUNDLE_TARGETS = Path(__file__).resolve().parents[1] / TARGETS_PATH
DASHBOARD_PY = Path(__file__).resolve().parents[1] / "api" / "dashboard.py"


class VercelTargetsFromFile(unittest.TestCase):
    def test_repo_file_has_real_numbers(self):
        raw = json.loads(REPO_TARGETS.read_text(encoding="utf-8"))
        self.assertEqual(raw["calories"], 2100.0)
        self.assertEqual(raw["protein_g"], 210.0)
        self.assertEqual(raw["carbs_g"], 180.0)
        self.assertEqual(raw["fat_g"], 55.0)
        self.assertEqual(raw["weight_goal_lbs"], 150.0)
        self.assertEqual(raw["updated_at"], "2026-08-11")

    def test_bundle_copy_matches_repo_file(self):
        self.assertTrue(BUNDLE_TARGETS.is_file(), BUNDLE_TARGETS)
        self.assertEqual(BUNDLE_TARGETS.read_bytes(), REPO_TARGETS.read_bytes())

    def test_loader_reads_file_not_default_stub(self):
        targets, source = load_workspace_targets()
        raw = json.loads(REPO_TARGETS.read_text(encoding="utf-8"))
        self.assertEqual(source, TARGETS_PATH)
        self.assertEqual(targets["calories"], raw["calories"])
        self.assertEqual(targets["protein_g"], raw["protein_g"])
        self.assertEqual(targets["carbs_g"], raw["carbs_g"])
        self.assertEqual(targets["fat_g"], raw["fat_g"])
        # Discriminator vs DEFAULT_TARGETS (weight_goal_lbs is None there)
        self.assertEqual(targets["weight_goal_lbs"], raw["weight_goal_lbs"])
        self.assertEqual(targets["updated_at"], raw["updated_at"])
        self.assertNotEqual(targets["weight_goal_lbs"], DEFAULT_TARGETS["weight_goal_lbs"])

    def test_macro_pace_not_no_target_when_consumed(self):
        targets, source = load_workspace_targets()
        self.assertEqual(source, TARGETS_PATH)
        payload = build_calorie_bars_payload(
            today_consumed={
                "calories": 1234,
                "protein_g": 80,
                "carbs_g": 90,
                "fat_g": 40,
            },
            targets=targets,
        )
        cal = payload["macro_pace"]["calories"]
        self.assertNotEqual(cal["status"], "no_target")
        self.assertEqual(cal["target"], float(targets["calories"]))
        self.assertEqual(payload["pacing"]["target"], float(targets["calories"]))
        self.assertNotEqual(payload["pacing"]["status"], "no_target")


    def test_vercel_bundle_path_alone_is_enough(self):
        from rt_dashboard import nutrition_store as ns

        with mock.patch.object(ns, "_targets_file_candidates", return_value=[BUNDLE_TARGETS]):
            targets, source = ns.load_workspace_targets()
        self.assertEqual(source, TARGETS_PATH)
        self.assertEqual(targets["calories"], 2100.0)
        self.assertEqual(targets["weight_goal_lbs"], 150.0)

    def test_dashboard_empty_targets_stub_gone(self):
        text = DASHBOARD_PY.read_text(encoding="utf-8")
        self.assertNotIn('"targets": {}', text)
        self.assertNotIn("'targets': {}", text)
        self.assertNotIn('"targets": "unset"', text)
        self.assertIn("load_workspace_targets", text)


if __name__ == "__main__":
    unittest.main()
