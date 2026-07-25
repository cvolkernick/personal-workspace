"""Tests for Ask Grok time-context packing (no live API required)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.grok_ask import (  # noqa: E402
    GrokAskError,
    ask_about_time,
    build_time_context,
)


class GrokAskContextTests(unittest.TestCase):
    def test_build_time_context_includes_core_slices(self) -> None:
        payload = {
            "path": "/tmp/tasks.json",
            "sleep_battery": {
                "asleep_hours": 7.5,
                "target_hours": 8,
                "pct_charged": 62.5,
                "hours_until_empty": 10.0,
                "model": "wake_full_drain_awake",
                "summary": "ok",
            },
            "lyft_duty": {"driven_minutes": 120, "remaining_drive_minutes": 600},
            "plan": {"blocks": [{"id": "lyft", "title": "Lyft", "minutes": 600, "role": "fill"}]},
            "plan_recommended": {"blocks": [{"id": "sleep", "minutes": 480, "role": "reserve"}]},
            "actual": {"total_logged_minutes": 100, "blocks": []},
            "allocation_delta": [{"id": "lyft", "planned_minutes": 600, "actual_minutes": 0}],
            "suggestions": [{"id": "lyft", "title": "Lyft", "urgency": "low"}],
            "targets": [{"id": "lyft", "kind": "fill_remainder"}],
            "kpi_status": [],
            "items": [],
            "walk_candidates": [],
            "logs": [{"date": "2026-07-20", "target_id": "duchess-walk", "value": 30}],
        }
        ctx = build_time_context(payload)
        self.assertEqual(ctx["sleep_battery"]["asleep_hours_24h"], 7.5)
        self.assertEqual(ctx["sleep_battery"]["pct_charged"], 62.5)
        self.assertEqual(ctx["sleep_battery"]["model"], "wake_full_drain_awake")
        self.assertEqual(ctx["lyft_duty"]["driven_minutes"], 120)
        self.assertEqual(ctx["plan_remaining"]["blocks"][0]["id"], "lyft")
        self.assertEqual(ctx["recent_logs"][0]["value"], 30)

    def test_ask_about_time_uses_chat(self) -> None:
        payload = {
            "sleep_battery": {},
            "lyft_duty": {},
            "plan": {"blocks": []},
            "plan_recommended": {"blocks": []},
            "actual": {"blocks": []},
            "allocation_delta": [],
            "suggestions": [],
            "targets": [],
            "logs": [],
        }
        fake = {
            "model": "test-model",
            "choices": [{"message": {"content": "You have 2h of Lyft left."}}],
            "usage": {"total_tokens": 42},
        }
        with mock.patch(
            "holistic.time_allocator.grok_ask.chat_completions", return_value=fake
        ):
            with mock.patch(
                "holistic.time_allocator.grok_ask.resolve_xai_credentials",
                return_value={"token": "x", "source": "test", "expired": False},
            ):
                out = ask_about_time("How much Lyft left?", payload)
        self.assertTrue(out["ok"])
        self.assertIn("Lyft", out["answer"])
        self.assertEqual(out["model"], "test-model")

    def test_empty_question_errors(self) -> None:
        with self.assertRaises(GrokAskError):
            ask_about_time("  ", {})


if __name__ == "__main__":
    unittest.main()
