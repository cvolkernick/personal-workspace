"""Natural-language coach action parsing."""

from __future__ import annotations

import unittest

from rt_dashboard.coach_actions import try_parse_coach_action


class TestCoachActions(unittest.TestCase):
    def test_legacy_key_equals(self):
        a = try_parse_coach_action(
            "set targets cal=2100 protein=200 carbs=180 fat=55"
        )
        self.assertEqual(a["action"], "set_targets")
        self.assertEqual(a["targets"]["calories"], 2100)
        self.assertEqual(a["targets"]["protein_g"], 200)

    def test_natural_protein_only(self):
        a = try_parse_coach_action("set protein to 220")
        self.assertEqual(a["action"], "set_targets")
        self.assertEqual(a["targets"]["protein_g"], 220)
        self.assertNotIn("calories", a["targets"])

    def test_natural_multi_macro(self):
        a = try_parse_coach_action(
            "update my macros to 220g protein, 150 carbs, and 55 fat"
        )
        self.assertEqual(a["action"], "set_targets")
        self.assertEqual(a["targets"]["protein_g"], 220)
        self.assertEqual(a["targets"]["carbs_g"], 150)
        self.assertEqual(a["targets"]["fat_g"], 55)

    def test_compact_shorthand(self):
        a = try_parse_coach_action("change macros to 220p 150c 55f")
        self.assertEqual(a["action"], "set_targets")
        self.assertEqual(a["targets"]["protein_g"], 220)
        self.assertEqual(a["targets"]["carbs_g"], 150)
        self.assertEqual(a["targets"]["fat_g"], 55)

    def test_calories_phrase(self):
        a = try_parse_coach_action("please set my calories to 2000")
        self.assertEqual(a["action"], "set_targets")
        self.assertEqual(a["targets"]["calories"], 2000)

    def test_question_not_action(self):
        self.assertIsNone(
            try_parse_coach_action(
                "what do you think about setting protein to 220?"
            )
        )
        self.assertIsNone(
            try_parse_coach_action("should I set calories to 2000?")
        )

    def test_apply_from_history(self):
        history = [
            {
                "role": "assistant",
                "content": (
                    "I'd bump protein to 220g, drop carbs to 150g, "
                    "keep fat at 55g, and calories around 2000 kcal."
                ),
            }
        ]
        a = try_parse_coach_action("apply those recommendations", history=history)
        self.assertIsNotNone(a)
        self.assertEqual(a["action"], "set_targets")
        self.assertEqual(a["targets"]["protein_g"], 220)
        self.assertEqual(a["targets"]["carbs_g"], 150)
        self.assertEqual(a["targets"]["fat_g"], 55)
        self.assertEqual(a["targets"]["calories"], 2000)

    def test_apply_without_history_falls_through(self):
        self.assertIsNone(try_parse_coach_action("apply those"))

    def test_stock_natural(self):
        a = try_parse_coach_action("mark chicken out of stock")
        self.assertEqual(a["action"], "set_stock")
        self.assertFalse(a["in_stock"])
        self.assertEqual(a["id_or_name"], "chicken")

    def test_refresh_meal_natural(self):
        a = try_parse_coach_action("please regenerate my meal plan")
        self.assertEqual(a["action"], "refresh_meal_plan")


if __name__ == "__main__":
    unittest.main()
