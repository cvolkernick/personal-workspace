"""Display-only saturated-fat chip. Synthetic rows only."""

from __future__ import annotations

import unittest

from rt_dashboard.nutrition_micros import (
    build_sat_fat_chip,
    micros_from_nutrients,
    sat_fat_day,
)


def _entry(day, kcal, nutrients):
    return {"date": day, "calories": kcal, "nutrients": nutrients}


class SatFatMicros(unittest.TestCase):
    def test_micros_sat_fat_key_aliases(self):
        self.assertEqual(micros_from_nutrients({"SATURATED_FAT": 4})["sat_fat_g"], 4)
        self.assertEqual(micros_from_nutrients({"FAT_SATURATED": 3.5})["sat_fat_g"], 3.5)
        self.assertEqual(micros_from_nutrients({"saturatedFat": 2})["sat_fat_g"], 2)
        self.assertEqual(
            micros_from_nutrients(
                [{"nutrient": "SATURATED_FAT", "quantity": {"grams": 1.25}}]
            )["sat_fat_g"],
            1.25,
        )
        self.assertNotIn("sat_fat_g", micros_from_nutrients({"DIETARY_FIBER": 8, "SUGAR": 4}))
        self.assertNotIn("sat_fat_g", micros_from_nutrients({}))

    def test_sat_fat_pct_of_kcal(self):
        # 18 g * 9 / 2000 kcal = 8.1% → 8%. Covered entries only.
        day = "2026-10-28"
        entries = [
            _entry(day, 400, {"SATURATED_FAT": 4}),
            _entry(day, 400, {"SATURATED_FAT": 4}),
            _entry(day, 400, {"SATURATED_FAT": 4}),
            _entry(day, 400, {"SATURATED_FAT": 3}),
            _entry(day, 400, {"SATURATED_FAT": 3}),
            _entry(day, 200, {}),
            _entry(day, 200, {}),
            _entry(day, 200, {"SUGAR": 2}),
        ]
        row = sat_fat_day(entries, day)
        self.assertEqual(row["with_entries"], 5)
        self.assertEqual(row["entries"], 8)
        self.assertEqual(row["covered_kcal"], 2000)
        self.assertAlmostEqual(row["sat_fat_g"], 18)
        self.assertAlmostEqual(row["pct"], 8.1, places=1)
        chip = build_sat_fat_chip(entries, as_of=day, enabled=True)
        self.assertIsNotNone(chip)
        self.assertIn("Sat fat 18 g (8% of kcal)", chip["text"])
        self.assertIn("guide ≤ 10%", chip["text"])
        self.assertIn("from 5/8 entries", chip["text"])
        self.assertIn("1/7", chip["text"])

    def test_sat_fat_chip_hidden_when_absent(self):
        day = "2026-10-28"
        empty = [_entry(day, 400, {}), _entry(day, 500, {"DIETARY_FIBER": 3})]
        self.assertIsNone(build_sat_fat_chip(empty, as_of=day, enabled=True))
        self.assertIsNone(build_sat_fat_chip([], as_of=day, enabled=True))
        present = [_entry(day, 400, {"SATURATED_FAT": 2})]
        self.assertIsNone(
            build_sat_fat_chip(
                present, as_of=day, enabled=False, rules={"sat_fat_chip": {"enabled": True}}
            )
        )
        hidden = build_sat_fat_chip(
            present, as_of=day, rules={"sat_fat_chip": {"enabled": False}}
        )
        self.assertIsNone(hidden)

    def test_sat_fat_partial_coverage_uses_covered_kcal_only(self):
        day = "2026-10-22"
        # 4 g on 200 kcal would be 18% if the other 800 kcal were included (understated).
        # Covered kcal only: 4*9/200 = 18%. Total day kcal 1000, coverage 20% < 50%.
        thin = [
            _entry(day, 200, {"SATURATED_FAT": 4}),
            _entry(day, 800, {}),
        ]
        row = sat_fat_day(thin, day)
        self.assertFalse(row["qualified"])
        self.assertTrue(row["no_data"])
        self.assertIsNone(row["pct"])
        chip = build_sat_fat_chip(thin, as_of=day, enabled=True)
        self.assertIn("no data", chip["text"])
        self.assertIn("from 1/2 entries", chip["text"])
        self.assertIn("0/7", chip["text"])
        self.assertNotIn("% of kcal", chip["text"])

        # Same grams, but covered kcal is half the day, so the day counts.
        # 4 g * 9 / 500 = 7.2% → 7%, and that day is 1/7.
        half = [
            _entry(day, 500, {"SATURATED_FAT": 4}),
            _entry(day, 500, {}),
        ]
        counted = sat_fat_day(half, day)
        self.assertTrue(counted["qualified"])
        self.assertAlmostEqual(counted["pct"], 7.2, places=1)
        shown = build_sat_fat_chip(half, as_of=day, enabled=True)
        self.assertIn("Sat fat 4 g (7% of kcal)", shown["text"])
        self.assertIn("from 1/2 entries", shown["text"])
        self.assertIn("1/7", shown["text"])
        self.assertTrue(shown["days_under_guide"] == 1)


if __name__ == "__main__":
    unittest.main()
