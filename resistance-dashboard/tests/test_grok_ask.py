"""Unit tests for fitness context packing (no live API)."""

from __future__ import annotations

import unittest

from rt_dashboard.grok_ask import build_fitness_context, _shrink_context


class TestGrokAskContext(unittest.TestCase):
    def test_build_context_includes_core_sections(self):
        dash = {
            "meta": {"generated_at": "2026-07-12T00:00:00Z"},
            "recovery": {"label": "ready", "reasons": ["slept well"]},
            "sessions": [
                {
                    "date": "2026-07-11",
                    "session_type": "push",
                    "exercises": [
                        {
                            "name": "Bench",
                            "weight_lbs": 135,
                            "sets": 3,
                            "reps": 8,
                        }
                    ],
                }
            ],
            "health": {
                "weight": [{"date": "2026-07-11", "lbs": 180}],
                "sleep": [{"date": "2026-07-11", "hours": 7.5}],
                "nutrition": [],
                "hydration": [],
                "calories_burned": [],
            },
            "nutrition_store": {
                "targets": {"calories": 2100, "protein_g": 210},
                "today_consumed": {"calories": 500, "protein_g": 40},
                "inventory": {
                    "ingredients": [
                        {
                            "id": "chicken",
                            "name": "Chicken",
                            "in_stock": True,
                            "calories": 200,
                            "protein_g": 40,
                            "carbs_g": 0,
                            "fat_g": 4,
                        }
                    ]
                },
                "meal_plan": {"message": "ok", "items": []},
            },
        }
        ctx = build_fitness_context(dash)
        self.assertEqual(ctx["recovery"]["label"], "ready")
        self.assertEqual(len(ctx["sessions"]), 1)
        self.assertEqual(ctx["sessions"][0]["exercises"][0]["name"], "Bench")
        self.assertEqual(ctx["nutrition_store"]["targets"]["calories"], 2100)
        self.assertEqual(len(ctx["nutrition_store"]["inventory"]), 1)

    def test_shrink_context_respects_max_chars(self):
        sessions = [
            {
                "date": f"2026-01-{i:02d}",
                "session_type": "push",
                "exercises": [
                    {"name": f"Ex{j}", "weight_lbs": 100, "sets": 3, "reps": 10}
                    for j in range(20)
                ],
            }
            for i in range(1, 40)
        ]
        ctx = {
            "sessions": sessions,
            "health": {
                "weight": [{"date": str(i), "lbs": 180} for i in range(100)],
                "sleep": [{"date": str(i), "hours": 7} for i in range(100)],
                "nutrition": [],
                "hydration": [],
                "calories_burned": [],
            },
            "nutrition_store": {"inventory": [], "targets": {}},
        }
        small, trimmed = _shrink_context(ctx, max_chars=2000)
        self.assertTrue(trimmed)
        import json

        self.assertLessEqual(len(json.dumps(small, separators=(",", ":"))), 2000 + 500)


if __name__ == "__main__":
    unittest.main()
