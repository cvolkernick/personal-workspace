"""Unit tests for fitness context packing (no live API)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.grok_ask import (
    CAPACITY_USER_MSG,
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    GrokAskError,
    _chat_request_body,
    _http_ask_error,
    _is_capacity_error,
    _shrink_context,
    build_fitness_context,
    chat_completions,
)


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
        self.assertLessEqual(len(json.dumps(small, separators=(",", ":"))), 2000 + 500)


class TestGrokAskCapacity(unittest.TestCase):
    def test_http_ask_error_strips_429_json(self):
        raw = (
            '{"code":"resource-exhausted","error":"The model is currently at '
            'capacity due to high demand. Please try again in a few minutes, '
            'or use a higher service tier for priority processing: '
            'https://docs.x.ai/developers/advanced-api-usage/priority-processing"}'
        )
        err = _http_ask_error(429, raw)
        self.assertEqual(err.status, 429)
        self.assertEqual(str(err), CAPACITY_USER_MSG)
        self.assertNotIn("resource-exhausted", str(err))
        self.assertNotIn("priority-processing", str(err))
        self.assertTrue(_is_capacity_error(err))

    def test_http_ask_error_keeps_non_capacity_body(self):
        err = _http_ask_error(500, '{"error":"upstream boom"}')
        self.assertEqual(err.status, 500)
        self.assertIn("HTTP 500", str(err))
        self.assertIn("upstream boom", str(err))
        self.assertFalse(_is_capacity_error(err))

    def test_fallback_body_sets_grok_43_non_reasoning(self):
        body = _chat_request_body("grok-4.3", [{"role": "user", "content": "hi"}], 10, 0.2)
        self.assertEqual(body["model"], "grok-4.3")
        self.assertEqual(body["reasoning_effort"], "none")
        primary = _chat_request_body(
            "grok-4.20-non-reasoning", [{"role": "user", "content": "hi"}], 10, 0.2
        )
        self.assertNotIn("reasoning_effort", primary)

    def test_chat_completions_falls_back_on_429(self):
        calls = []

        def fake_post(token, body):
            calls.append(dict(body))
            if body["model"] == DEFAULT_MODEL:
                raise GrokAskError(CAPACITY_USER_MSG, status=429, body='{"code":"resource-exhausted"}')
            return {
                "choices": [{"message": {"content": "try the DB press"}}],
                "model": body["model"],
                "usage": {"total_tokens": 9},
            }

        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={"token": "tok", "source": "supergrok_session", "expired": False},
        ), mock.patch("rt_dashboard.grok_ask._post_chat", side_effect=fake_post):
            out = chat_completions([{"role": "user", "content": "what today?"}])
        self.assertEqual([c["model"] for c in calls], [DEFAULT_MODEL, FALLBACK_MODEL])
        self.assertEqual(calls[1].get("reasoning_effort"), "none")
        self.assertEqual(out["answer"], "try the DB press")
        self.assertEqual(out["model"], FALLBACK_MODEL)
        self.assertTrue(out["fallback_used"])

    def test_chat_completions_both_capacity_stays_clean(self):
        def fake_post(token, body):
            raise GrokAskError(
                CAPACITY_USER_MSG,
                status=429,
                body='{"code":"resource-exhausted","error":"at capacity"}',
            )

        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={"token": "tok", "source": "xai_api_key", "expired": False},
        ), mock.patch("rt_dashboard.grok_ask._post_chat", side_effect=fake_post):
            with self.assertRaises(GrokAskError) as ctx:
                chat_completions([{"role": "user", "content": "hi"}])
        self.assertEqual(str(ctx.exception), CAPACITY_USER_MSG)
        self.assertNotIn("{", str(ctx.exception))

    def test_chat_completions_does_not_fallback_on_401(self):
        calls = []

        def fake_post(token, body):
            calls.append(body["model"])
            raise GrokAskError("Session expired", status=401)

        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={"token": "tok", "source": "xai_api_key", "expired": False},
        ), mock.patch("rt_dashboard.grok_ask._post_chat", side_effect=fake_post):
            with self.assertRaises(GrokAskError) as ctx:
                chat_completions([{"role": "user", "content": "hi"}])
        self.assertEqual(calls, [DEFAULT_MODEL])
        self.assertEqual(ctx.exception.status, 401)


class TestAskErrorHint(unittest.TestCase):
    def test_app_js_does_not_always_blame_supergrok(self):
        src = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("function askErrorHint", src)
        self.assertNotIn(
            "Error: ${e.message}\\n\\nIf SuperGrok expired, open More → Connect SuperGrok and retry.",
            src,
        )
        self.assertIn("If SuperGrok expired, open More → Connect SuperGrok and retry.", src)


if __name__ == "__main__":
    unittest.main()
