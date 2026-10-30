"""Hip flexor / adductor / abductor catalog rows and swap scoping."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from rt_dashboard import catalog_guard as cg
from rt_dashboard import workout_planner as wp

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
BUNDLED = ROOT / "fitness" / "exercises" / "catalog.json"
ROOT_CATALOG = REPO / "fitness" / "exercises" / "catalog.json"

HIP_TAGS = {"adductors", "abductors", "hip_flexors"}
HIP_PATTERN = {"adductors": "hip_adduction", "abductors": "hip_abduction", "hip_flexors": "hip_flexion"}
NEW_IDS = {
    "machine-abduction", "cable-hip-adduction", "cable-hip-abduction", "copenhagen-plank",
    "cable-hip-flexion", "hanging-knee-raise", "captains-chair-knee-raise", "band-hip-flexion-march",
}
AVAILABLE = {"machine-adduction", "machine-abduction", "cable-hip-adduction",
             "cable-hip-abduction", "cable-hip-flexion"}


def _catalog() -> dict:
    return json.loads(BUNDLED.read_text(encoding="utf-8"))


def _hip_rows(cat: dict):
    return [r for r in cat["exercises"] if set(r["primary_muscles"]) & HIP_TAGS]


class HipCatalogRows(unittest.TestCase):
    def test_copies_identical(self):
        self.assertEqual(ROOT_CATALOG.read_text(encoding="utf-8"), BUNDLED.read_text(encoding="utf-8"))

    def test_new_rows_follow_schema(self):
        cat = _catalog()
        by_id = {r["id"]: r for r in cat["exercises"]}
        template = set(by_id["db-flat-press"])
        for rid in NEW_IDS | {"machine-adduction"}:
            row = by_id[rid]
            self.assertTrue(template <= set(row), rid)
            self.assertEqual(row["movement"], "isolation", rid)
            self.assertEqual(row["rep_range"], [8, 15], rid)
            self.assertEqual(row["session_types"], ["legs"], rid)
            self.assertEqual(len(row["primary_muscles"]), 1, rid)
            self.assertEqual(row["movement_pattern"], HIP_PATTERN[row["primary_muscles"][0]], rid)
            self.assertEqual(row["available"], rid in AVAILABLE, rid)

    def test_every_hip_row_has_its_muscle_pattern(self):
        for row in _hip_rows(_catalog()):
            self.assertEqual(row["movement_pattern"], HIP_PATTERN[row["primary_muscles"][0]], row["id"])

    def test_abduction_credits_glutes_secondary(self):
        by_id = {r["id"]: r for r in _catalog()["exercises"]}
        for rid in ("machine-abduction", "cable-hip-abduction"):
            self.assertEqual(by_id[rid]["secondary_muscles"], ["glutes"])
            self.assertEqual(by_id[rid]["volume_secondary"], ["glutes"])

    def test_progression_steps(self):
        by_id = {r["id"]: r for r in _catalog()["exercises"]}
        for rid in ("machine-adduction", "machine-abduction", "cable-hip-adduction",
                    "cable-hip-abduction", "cable-hip-flexion"):
            self.assertEqual(wp._implement_kind(by_id[rid], None), "pin", rid)
            self.assertEqual(wp.load_step_lbs(by_id[rid], 50.0), 5.0, rid)
        self.assertEqual(wp.ISOLATION_REP_BAND, (8, 15))

    def test_not_in_planned_programs(self):
        for p in (ROOT / "fitness" / "exercises" / "goals.json", REPO / "fitness" / "exercises" / "goals.json"):
            text = p.read_text(encoding="utf-8")
            for rid in NEW_IDS:
                self.assertNotIn(rid, text, f"{p} {rid}")


class HipMuscleTags(unittest.TestCase):
    def test_tags_are_majors_and_legs(self):
        for tag in HIP_TAGS:
            self.assertIn(tag, wp.MAJOR_MUSCLES)
            self.assertIn(tag, wp.SESSION_MUSCLES["legs"])
            self.assertIn(tag, wp.LOWER_BODY_MUSCLES)
            self.assertEqual(wp.normalize_muscle(tag), tag)
        self.assertEqual(wp.normalize_muscle("hip flexor"), "hip_flexors")
        self.assertEqual(wp.normalize_muscle("abductor"), "abductors")

    def test_ui_lists_the_tags(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for tag in HIP_TAGS:
            self.assertEqual(html.count(f'<option value="{tag}">'), 2, tag)
            self.assertIn(f'"{tag}"', js)

    def test_suggestion_bands(self):
        bands = json.loads((ROOT / "fitness" / "exercises" / "suggestion_bands.json").read_text())
        ids = {g["id"] for g in bands["groups"]}
        self.assertTrue(HIP_TAGS <= ids)


class HipSwapScoping(unittest.TestCase):
    def setUp(self):
        self.cat = cg.apply_catalog_guard(_catalog(), {})
        self.by_id = {r["id"]: r for r in self.cat["exercises"]}

    def _sub(self, row, exclude=()):
        return cg.find_substitute(row, self.cat, [], exclude_ids=exclude)

    def test_hip_adduction_machine_swaps_only_to_adductors(self):
        row = {"name": "Hip Adduction Machine", "movement": "isolation", "primary_muscles": ["adductors"]}
        seen = set()
        while True:
            sub = self._sub(row, seen)
            if sub is None:
                break
            self.assertEqual(sub["primary_muscles"], ["adductors"], sub["id"])
            seen.add(sub["id"])
        avail_add = {r["id"] for r in wp.available_exercises(self.cat) if r["primary_muscles"] == ["adductors"]}
        self.assertEqual(seen, avail_add)
        self.assertIn("machine-adduction", seen)

    def test_name_only_rows_stay_in_muscle(self):
        cases = {
            "Hip Adduction Machine": "adductors",
            "Hip Abduction Machine": "abductors",
            "Standing Cable Knee Raise": "hip_flexors",
        }
        for name, muscle in cases.items():
            sub = self._sub({"name": name, "movement": "isolation"})
            self.assertIsNotNone(sub, name)
            self.assertEqual(sub["primary_muscles"], [muscle], name)

    def test_abduction_never_swaps_to_adduction(self):
        row = {"name": "Hip Abduction Machine", "primary_muscles": ["abductors"]}
        abd = {r["id"] for r in self.cat["exercises"] if r["primary_muscles"] == ["abductors"]}
        self.assertIsNone(self._sub(row, abd))

    def test_other_isolations_do_not_swap_into_hip_rows(self):
        sub = self._sub({"name": "Mystery Curl", "movement": "isolation", "primary_muscles": ["biceps"]})
        self.assertTrue(sub is None or not (set(sub["primary_muscles"]) & HIP_TAGS))


if __name__ == "__main__":
    unittest.main()
