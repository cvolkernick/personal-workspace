"""README parser — no network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from parse import parse_deliverables, parse_modules, parse_v0_targets, summarize  # noqa: E402

README = (Path(__file__).resolve().parent / "fixtures" / "readme.md").read_text(encoding="utf-8")


class ParseModulesTests(unittest.TestCase):
    def test_counts_and_duplicate_id_merge(self) -> None:
        modules = parse_modules(README)
        by_id = {m["id"]: m for m in modules if m["id"]}
        untitled = [m for m in modules if not m["id"]]
        self.assertEqual(len(untitled), 2)
        self.assertIn("urdf-gazebo-sim", by_id)
        self.assertEqual(by_id["urdf-gazebo-sim"]["status"], "done")
        self.assertEqual(by_id["clean-and-map"]["status"], "in_progress")
        self.assertEqual(by_id["dock-cycle"]["status"], "ready")
        # Two compute-benchmark rows collapse to the stronger status.
        self.assertEqual(by_id["compute-benchmark"]["status"], "done")
        self.assertIn("2GB", by_id["compute-benchmark"]["status_label"])
        self.assertEqual(len(modules), 7)

    def test_empty_id_rows_kept(self) -> None:
        modules = parse_modules(README)
        titles = {m["title"] for m in modules}
        self.assertIn("Auto cleaning", titles)
        self.assertIn("Regression tests", titles)


class ParseDeliverableTests(unittest.TestCase):
    def test_checkbox_counts(self) -> None:
        items = parse_deliverables(README)
        self.assertEqual(len(items), 10)
        self.assertEqual(sum(1 for i in items if i["done"]), 4)
        env = next(i for i in items if "Software development" in i["title"])
        self.assertTrue(env["done"])
        self.assertIn("oomwoo-install", env["url"])
        demo = next(i for i in items if "Demo video" in i["title"])
        self.assertFalse(demo["done"])

    def test_v0_targets(self) -> None:
        v0 = parse_v0_targets(README)
        self.assertEqual(len(v0), 4)
        self.assertTrue(any("3D-printed" in x for x in v0))
        self.assertTrue(any("CM4" in x or "CM5" in x for x in v0))

    def test_summarize(self) -> None:
        progress = summarize(parse_modules(README), parse_deliverables(README))
        self.assertEqual(progress["modules_total"], 7)
        self.assertEqual(progress["modules_done"], 3)
        self.assertEqual(progress["modules_in_progress"], 3)
        self.assertEqual(progress["modules_ready"], 1)
        self.assertEqual(progress["deliverables_done"], 4)
        self.assertEqual(progress["deliverables_total"], 10)
        self.assertGreater(progress["module_score"], 0.5)
        self.assertLess(progress["module_score"], 0.8)


if __name__ == "__main__":
    unittest.main()
