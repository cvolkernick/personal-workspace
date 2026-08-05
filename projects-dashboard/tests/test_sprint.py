"""Tests for Sprint tab ceremony load + API payload (slice 1)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

import sprint as sp  # noqa: E402


class TestSprintCeremony(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="sprint-")
        self.ws = Path(self._td.name) / "ws"
        self.ws.mkdir()
        self.sprint_dir = self.ws / "ops" / "sprint"
        self.sprint_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_missing_file_returns_empty_seed(self) -> None:
        c = sp.load_ceremony(self.ws)
        self.assertFalse(c["_exists"])
        self.assertEqual(c["wip_limit"], 3)
        self.assertEqual(c["in_progress"], [])
        self.assertEqual(c["ready"], [])

    def test_load_valid_ceremony(self) -> None:
        path = self.sprint_dir / "current.json"
        path.write_text(
            json.dumps(
                {
                    "sprint_id": "2026-W32",
                    "goal": "Ship sprint tab",
                    "wip_limit": 3,
                    "capacity_points": 13,
                    "committed_points": 5,
                    "in_progress": [
                        {
                            "number": 21,
                            "title": "Grok Board access",
                            "size": 5,
                            "priority": "P0",
                            "owner": "Grok",
                            "url": "https://example.com/21",
                        }
                    ],
                    "ready": [],
                    "not_this_sprint": [],
                    "notes": ["test"],
                    "updated_at": "2026-08-05T00:00:00Z",
                    "updated_by": "cadence",
                    "board_url": "https://github.com/users/cvolkernick/projects/1",
                }
            ),
            encoding="utf-8",
        )
        c = sp.load_ceremony(self.ws)
        self.assertTrue(c["_exists"])
        self.assertEqual(c["sprint_id"], "2026-W32")
        self.assertEqual(c["goal"], "Ship sprint tab")
        self.assertEqual(len(c["in_progress"]), 1)
        self.assertEqual(c["in_progress"][0]["number"], 21)

    def test_payload_ceremony_only_no_live_board(self) -> None:
        path = self.sprint_dir / "current.json"
        path.write_text(
            json.dumps(
                {
                    "sprint_id": "2026-W32",
                    "goal": "Goal",
                    "wip_limit": 3,
                    "in_progress": [{"number": 1, "title": "A"}],
                    "ready": [{"number": 2, "title": "B"}],
                    "not_this_sprint": [],
                    "notes": [],
                }
            ),
            encoding="utf-8",
        )
        payload = sp.sprint_payload(self.ws, live_board=False)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["ceremony"]["goal"], "Goal")
        self.assertEqual(payload["wip"]["current"], 1)
        self.assertEqual(payload["wip"]["limit"], 3)
        self.assertFalse(payload["wip"]["over"])
        self.assertEqual(payload["board"]["source"], "ceremony_only")
        self.assertEqual(len(payload["board"]["columns"]["In Progress"]), 1)
        self.assertEqual(len(payload["board"]["columns"]["Ready"]), 1)
        self.assertIn("ops/backlog", payload["disclaimer"])
        self.assertEqual(
            payload["ceremonies"]["grooming_workflow_id"],
            "95d911df-509b-4eac-a4f5-ffeaa4c1e3da",
        )

    def test_wip_over_limit(self) -> None:
        path = self.sprint_dir / "current.json"
        cards = [{"number": i, "title": f"T{i}"} for i in range(1, 5)]
        path.write_text(
            json.dumps(
                {
                    "wip_limit": 3,
                    "in_progress": cards,
                    "ready": [],
                    "not_this_sprint": [],
                    "notes": [],
                }
            ),
            encoding="utf-8",
        )
        payload = sp.sprint_payload(self.ws, live_board=False)
        self.assertEqual(payload["wip"]["current"], 4)
        self.assertTrue(payload["wip"]["over"])

    def test_invalid_json_degrades(self) -> None:
        path = self.sprint_dir / "current.json"
        path.write_text("{not-json", encoding="utf-8")
        c = sp.load_ceremony(self.ws)
        self.assertTrue(c["_exists"])
        self.assertIn("Failed", c["notes"][0])

    def test_board_live_error_still_ok(self) -> None:
        path = self.sprint_dir / "current.json"
        path.write_text(
            json.dumps(
                {
                    "goal": "Still show",
                    "wip_limit": 3,
                    "in_progress": [{"number": 9, "title": "X"}],
                    "ready": [],
                    "not_this_sprint": [],
                    "notes": [],
                }
            ),
            encoding="utf-8",
        )

        def boom(**_kw):
            raise RuntimeError("no token")

        with mock.patch.dict("sys.modules", {"sprint_board": mock.MagicMock()}):
            # Force import path inside sprint_payload to fail
            with mock.patch(
                "sprint_board.sprint_payload",
                side_effect=RuntimeError("no token"),
                create=True,
            ):
                # If module import works but call fails:
                import types

                fake = types.ModuleType("sprint_board")
                fake.sprint_payload = boom  # type: ignore[attr-defined]
                with mock.patch.dict(sys.modules, {"sprint_board": fake}):
                    payload = sp.sprint_payload(self.ws, live_board=True)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["ceremony"]["goal"], "Still show")
        self.assertEqual(payload["wip"]["current"], 1)
        self.assertIn(payload["board"]["source"], ("error", "ceremony_only"))


if __name__ == "__main__":
    unittest.main()
