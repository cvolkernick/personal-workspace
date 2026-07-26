"""Tests for operator intent store."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

from intent import (  # noqa: E402
    intent_for_context,
    load_intent,
    save_intent,
)
from payload import build_orchestra_payload  # noqa: E402


class IntentStoreTests(unittest.TestCase):
    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategy").mkdir()
            saved = save_intent(
                {
                    "accomplishing": "Ship one automation",
                    "balancing": "Deep work\nRecovery",
                    "constraints": ["one next action"],
                    "streamline_goals": ["no dashboard thrash"],
                    "time_horizon": "today",
                },
                ws,
            )
            self.assertTrue(saved.get("exists"))
            self.assertEqual(saved["accomplishing"], "Ship one automation")
            self.assertEqual(saved["balancing"], ["Deep work", "Recovery"])
            self.assertEqual(saved["constraints"], ["one next action"])
            again = load_intent(ws)
            self.assertEqual(again["accomplishing"], "Ship one automation")
            slim = intent_for_context(again)
            self.assertIn("accomplishing", slim)
            self.assertEqual(slim["time_horizon"], "today")

    def test_payload_includes_intent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategy").mkdir()
            (ws / "strategy" / "bets.md").write_text("# Bets\n- **AI**\n", encoding="utf-8")
            (ws / "strategy" / "today.md").write_text(
                "# Today\n- [ ] **Do the thing**\n", encoding="utf-8"
            )
            (ws / "initiatives").mkdir()
            (ws / "ops" / "backlog").mkdir(parents=True)
            (ws / "ops" / "backlog" / "items.json").write_text(
                json.dumps({"items": []}), encoding="utf-8"
            )
            save_intent({"accomplishing": "Focus test"}, ws)
            payload = build_orchestra_payload(ws, probe_ports=False)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["intent"]["accomplishing"], "Focus test")
            self.assertEqual(payload["operator_intent"]["accomplishing"], "Focus test")


if __name__ == "__main__":
    unittest.main()
