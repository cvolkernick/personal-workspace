"""Tests for Sprint tab ceremony load + API payload (#56 schema v2)."""

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
import sprint_board as sb  # noqa: E402


class TestSprintCeremony(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="sprint-")
        self.ws = Path(self._td.name) / "ws"
        self.ws.mkdir()
        self.sprint_dir = self.ws / "ops" / "sprint"
        self.sprint_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _write(self, obj: dict) -> None:
        (self.sprint_dir / "current.json").write_text(
            json.dumps(obj), encoding="utf-8"
        )

    def test_missing_file_returns_empty_seed(self) -> None:
        c = sp.load_ceremony(self.ws)
        self.assertFalse(c["_exists"])
        self.assertEqual(c["schema_version"], 2)
        self.assertEqual(c["agent_wip_cap"], 1)
        self.assertEqual(c["card_overlays"], {})

    def test_load_schema_v2(self) -> None:
        self._write(
            {
                "schema_version": 2,
                "sprint_id": "2026-W32",
                "goal": "Ship sprint tab",
                "agent_wip_cap": 1,
                "card_overlays": {
                    "56": {
                        "size": 5,
                        "priority": "P2",
                        "owner": "Forge",
                        "notes": "real WIP",
                    }
                },
                "not_this_sprint": [{"number": 13, "reason": "capital"}],
                "agents": [
                    {"name": "Forge", "seat": "implement"},
                    {"name": "Grok", "seat": "gate"},
                ],
                "notes": ["test"],
                "updated_at": "2026-08-08T00:00:00Z",
                "updated_by": "cadence",
            }
        )
        c = sp.load_ceremony(self.ws)
        self.assertTrue(c["_exists"])
        self.assertEqual(c["schema_version"], 2)
        self.assertEqual(c["goal"], "Ship sprint tab")
        self.assertEqual(c["card_overlays"]["56"]["owner"], "Forge")
        self.assertEqual(c["agent_wip_cap"], 1)

    def test_payload_ceremony_only_no_live_board(self) -> None:
        """Without live board, degrade to v1 lists if present — never blank."""
        self._write(
            {
                "schema_version": 2,
                "sprint_id": "2026-W32",
                "goal": "Goal",
                "agent_wip_cap": 1,
                "card_overlays": {
                    "1": {"owner": "Forge", "size": 3},
                },
                # v1 degrade lists when board off
                "in_progress": [{"number": 1, "title": "A", "owner": "Forge"}],
                "ready": [{"number": 2, "title": "B"}],
                "agents": [
                    {"name": "Forge", "seat": "implement"},
                    {"name": "Grok", "seat": "gate"},
                    {"name": "Frankenfit", "seat": "implement"},
                ],
                "notes": [],
            }
        )
        payload = sp.sprint_payload(self.ws, live_board=False)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["ceremony"]["goal"], "Goal")
        self.assertEqual(payload["ceremony"]["schema_version"], 2)
        self.assertEqual(payload["board"]["source"], "ceremony_only")
        for key in sp.BOARD_COLUMN_KEYS:
            self.assertIn(key, payload["board"]["columns"])
        self.assertIn("Pending Review", payload["board"]["columns"])
        self.assertIn("ops/backlog", payload["disclaimer"])
        self.assertIn("never writes Board Status", payload["disclaimer"])
        # Forge busy
        self.assertIn("Forge", [b["name"] for b in payload["agents"]["busy"]])
        self.assertNotIn("Forge", payload["agents"]["free"])
        self.assertIn("Frankenfit", payload["agents"]["free"])
        self.assertEqual(payload["wip"]["model"], "per_agent")
        self.assertEqual(payload["wip"]["cap"], 1)

    def test_invalid_json_degrades(self) -> None:
        (self.sprint_dir / "current.json").write_text("{not-json", encoding="utf-8")
        c = sp.load_ceremony(self.ws)
        self.assertTrue(c["_exists"])
        self.assertIn("Failed", c["notes"][0])

    def test_board_live_error_still_ok(self) -> None:
        self._write(
            {
                "schema_version": 2,
                "goal": "Still show",
                "agent_wip_cap": 1,
                "card_overlays": {"9": {"owner": "Forge"}},
                "in_progress": [{"number": 9, "title": "X", "owner": "Forge"}],
                "agents": [{"name": "Forge", "seat": "implement"}],
                "notes": [],
            }
        )

        def boom(**_kw):
            raise RuntimeError("no token")

        import types

        fake = types.ModuleType("sprint_board")
        fake.sprint_payload = boom  # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"sprint_board": fake}):
            payload = sp.sprint_payload(self.ws, live_board=True)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["ceremony"]["goal"], "Still show")
        self.assertIn(payload["board"]["source"], ("error", "ceremony_only"))


class TestAgentCapacity(unittest.TestCase):
    def test_pending_review_does_not_busy(self) -> None:
        agents = [
            {"name": "Forge", "seat": "implement", "role": "eng"},
            {"name": "Grok", "seat": "gate", "role": "gate"},
        ]
        cap = sp.compute_agent_capacity(
            agents=agents,
            in_progress=[
                {"number": 56, "title": "Sprint tab", "owner": "Forge"},
            ],
            pending_review=[
                {"number": 77, "title": "Howell", "owner": "Grok"},
            ],
            board_source="github_project_v2",
        )
        self.assertEqual(cap["free"], ["Grok"])
        self.assertEqual(cap["busy"][0]["name"], "Forge")
        self.assertEqual(cap["busy"][0]["issue"], 56)
        self.assertEqual(cap["cap"], 1)
        self.assertEqual(len(cap["pending_review_holders"]), 1)

    def test_process_seat_excluded_from_wip_roster(self) -> None:
        agents = [
            {"name": "Forge", "seat": "implement", "role": "eng"},
            {"name": "Cadence", "seat": "process", "role": "scrum"},
            {"name": "Assay", "seat": "qa", "role": "qa"},
        ]
        roster = sp._wip_roster(agents)
        self.assertEqual(roster, ["Forge"])

    def test_missing_owner_data_gap(self) -> None:
        cap = sp.compute_agent_capacity(
            agents=[{"name": "Forge", "seat": "implement", "role": "eng"}],
            in_progress=[{"number": 1, "title": "No owner"}],
            board_source="github_project_v2",
        )
        self.assertEqual(cap["free"], ["Forge"])
        self.assertIsNotNone(cap["data_gap"])
        self.assertIn("#1", cap["data_gap"])

    def test_overlay_owner_resolution(self) -> None:
        cards = [{"number": 56, "title": "Sprint", "owner": None, "assignees": []}]
        overlays = {56: {"owner": "Forge", "size": 5}}
        merged = sp.apply_overlays(cards, overlays, ["Forge", "Grok"])
        self.assertEqual(merged[0]["owner"], "Forge")
        self.assertEqual(merged[0]["size"], 5)

    def test_board_status_order_includes_pending_review(self) -> None:
        self.assertIn("Pending Review", sb.STATUS_ORDER)
        ip = sb.STATUS_ORDER.index("In Progress")
        pr = sb.STATUS_ORDER.index("Pending Review")
        done = sb.STATUS_ORDER.index("Done")
        self.assertLess(ip, pr)
        self.assertLess(pr, done)

    def test_live_board_maps_pending_review(self) -> None:
        live = {
            "ok": True,
            "columns": {
                "Parked": [],
                "Validate ($0)": [{"number": 61, "title": "Flow"}],
                "Ready": [{"number": 40, "title": "Roster"}],
                "In Progress": [
                    {
                        "number": 56,
                        "title": "Sprint",
                        "owner": "Forge",
                        "url": "https://example.com/56",
                    }
                ],
                "Pending Review": [
                    {"number": 77, "title": "Howell", "url": "https://example.com/77"}
                ],
                "Done": [],
            },
            "board": {"url": "https://github.com/users/cvolkernick/projects/1"},
            "wip": {"limit": 3, "current": 1, "over": False},
        }
        board = sp._board_from_live(live)
        self.assertEqual(board["source"], "github_project_v2")
        self.assertEqual(len(board["columns"]["Pending Review"]), 1)
        self.assertEqual(board["columns"]["Validate"][0]["number"], 61)
        self.assertEqual(board["counts"]["Pending Review"], 1)
        self.assertEqual(board["counts"]["Ready"], 1)

    def test_payload_with_mocked_live_board(self) -> None:
        td = tempfile.TemporaryDirectory(prefix="sprint-live-")
        self.addCleanup(td.cleanup)
        ws = Path(td.name) / "ws"
        sprint_dir = ws / "ops" / "sprint"
        sprint_dir.mkdir(parents=True)
        (sprint_dir / "current.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "goal": "Live test",
                    "agent_wip_cap": 1,
                    "card_overlays": {
                        "56": {"owner": "Forge", "size": 5, "priority": "P2"}
                    },
                    "agents": [
                        {"name": "Forge", "seat": "implement"},
                        {"name": "Grok", "seat": "gate"},
                    ],
                    "ceremonies": {
                        "next_assay_qa": "2026-08-10",
                        "assay_qa_issue": 76,
                    },
                    "notes": [],
                }
            ),
            encoding="utf-8",
        )

        def fake_board(**_kw):
            return {
                "ok": True,
                "columns": {
                    "Parked": [],
                    "Validate ($0)": [],
                    "Ready": [{"number": 40, "title": "Roster hygiene"}],
                    "In Progress": [
                        {"number": 56, "title": "Sprint tab", "url": "u"}
                    ],
                    "Pending Review": [],
                    "Done": [],
                },
                "board": {
                    "url": "https://github.com/users/cvolkernick/projects/1",
                    "title": "Buzz Board",
                },
                "wip": {"limit": 3, "current": 1, "over": False},
            }

        import types

        fake = types.ModuleType("sprint_board")
        fake.sprint_payload = fake_board  # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"sprint_board": fake}):
            payload = sp.sprint_payload(ws, live_board=True)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["board"]["source"], "github_project_v2")
        ip = payload["board"]["columns"]["In Progress"]
        self.assertEqual(len(ip), 1)
        self.assertEqual(ip[0]["owner"], "Forge")  # from overlay
        self.assertEqual(ip[0]["size"], 5)
        self.assertIn("Forge", [b["name"] for b in payload["agents"]["busy"]])
        self.assertIn("Grok", payload["agents"]["free"])
        self.assertEqual(payload["ceremonies"]["next_assay_qa"], "2026-08-10")


if __name__ == "__main__":
    unittest.main()
