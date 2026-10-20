"""Behind-the-Neck Smith Press is a catalog option. Class band stays 5–9."""

from __future__ import annotations

import unittest
from pathlib import Path

from rt_dashboard.workout_planner import (
    COMPOUND_REP_BAND,
    VERTICAL_PRESS_FAMILY,
    available_exercises,
    load_json_file,
    pattern_family,
    prescribe,
    progression_band,
)
from rt_dashboard.workout_store import load_workspace_catalog

ROW = {
    "id": "smith-btn-press",
    "name": "Behind-the-Neck Smith Press",
    "session_types": ["push"],
    "primary_muscles": ["shoulders"],
    "secondary_muscles": ["triceps", "traps"],
    "volume_primary": ["side_delts"],
    "volume_secondary": ["front_delts", "triceps", "traps"],
    "movement": "compound",
    "equipment": ["smith_machine"],
    "default_sets": 2,
    "default_reps": 12,
    "rep_range": [10, 15],
    "priority": 5,
    "available": True,
    "notes": (
        "Seated; lower bar behind head to ~ear level. Warm up first; "
        "moderate load, 10-15 reps; stop short of any shoulder pinch. "
        "Source: @CoachFHM."
    ),
}


def _last(weight, reps, date="2026-10-16"):
    return {"weight_lbs": weight, "sets": 2, "reps": reps, "date": date}


class SmithBtnPress1129(unittest.TestCase):
    def test_both_copies_append_only_this_row(self):
        root = Path(__file__).resolve().parents[2] / "fitness" / "exercises" / "catalog.json"
        bundled = Path(__file__).resolve().parents[1] / "fitness" / "exercises" / "catalog.json"
        self.assertEqual(root.read_text(encoding="utf-8"), bundled.read_text(encoding="utf-8"))
        catalog = load_json_file(root, {})
        self.assertEqual(catalog["updated_at"], "2026-10-16")
        exercises = catalog["exercises"]
        ids = [ex["id"] for ex in exercises]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids.count("smith-btn-press"), 1)
        self.assertEqual(ids[-1], "smith-btn-press")
        self.assertEqual(len(ids), 48)
        self.assertEqual(exercises[-1], ROW)
        names = [ex["name"] for ex in exercises]
        self.assertEqual(names.count(ROW["name"]), 1)
        shoulder = next(ex for ex in exercises if ex["id"] == "db-shoulder-press")
        self.assertEqual(shoulder["priority"], 9)
        self.assertEqual(shoulder["rep_range"], [8, 12])
        self.assertEqual(shoulder["name"], "DB Shoulder Press")
        grip = next(ex for ex in exercises if ex["id"] == "fat-grip-curl")
        self.assertFalse(grip["available"])
        self.assertEqual(grip["notes"], "Suggestion unlock for grip.")

    def test_library_and_log_can_select_it(self):
        root = Path(__file__).resolve().parents[2] / "fitness" / "exercises" / "catalog.json"
        catalog = load_json_file(root, {})
        names = [ex["name"] for ex in available_exercises(catalog)]
        self.assertIn(ROW["name"], names)
        loaded, _src = load_workspace_catalog()
        loaded_ids = {ex["id"] for ex in loaded["exercises"]}
        self.assertIn("smith-btn-press", loaded_ids)
        self.assertEqual(
            [ex["name"] for ex in loaded["exercises"] if ex["id"] == "smith-btn-press"],
            [ROW["name"]],
        )

    def test_class_band_beats_catalog_rep_range(self):
        self.assertEqual(ROW["rep_range"], [10, 15])
        self.assertEqual(progression_band(ROW), COMPOUND_REP_BAND)
        self.assertEqual(COMPOUND_REP_BAND, (5, 9))
        self.assertEqual(pattern_family(ROW), VERTICAL_PRESS_FAMILY)
        top = prescribe(ROW, _last(50, 12), recovery_score=80)
        self.assertEqual(top["progression_reason"], "add_load")
        self.assertEqual(top["weight_lbs"], 55.0)
        self.assertEqual(top["target_reps"], 5)
        self.assertEqual(top["rep_range_label"], "5-9")
        mid = prescribe(ROW, _last(50, 7), recovery_score=80)
        self.assertEqual(mid["progression_reason"], "hold_plus1")
        self.assertEqual(mid["weight_lbs"], 50.0)
        self.assertEqual(mid["target_reps"], 8)
        self.assertEqual(mid["rep_range_label"], "5-9")


if __name__ == "__main__":
    unittest.main()
