"""Tests for Schedule/Process tab (#61)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

import process_schedule as ps  # noqa: E402


SAMPLE_YAML = """\
name: cadence-daily-replenish
trigger:
  on: schedule
  cron: "0 16 * * *"
steps:
  - id: kickoff
    action: send_message
    text: |
      @Cadence **Scheduled: Daily replenish**
      @Primary each row
    mentions:
      - "0092be61e5d369d74c59bf64d7aaa2ce4fe6932efa7e647645cf4e3fb396a99f"
"""

SAMPLE_ENG = """\
name: eng-gate-sweep
trigger:
  on: schedule
  cron: "*/15 * * * *"
steps:
  - id: kickoff
    action: send_message
    text: "@Grok **Eng-gate sweep**"
    mentions:
      - "213349578fbf53a20fda8b56d0229fca699033d349aa0af00d0a860070f2f2b1"
"""

SAMPLE_ZZZ = """\
name: zzz-retired-cadence-sprint-planning
trigger:
  on: schedule
  cron: "0 0 1 1 *"
steps:
  - id: noop
    action: send_message
    text: "[retired]"
"""


class TestCron(unittest.TestCase):
    def test_daily_16utc(self) -> None:
        # After 15:00 → next is same day 16:00
        after = datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc)
        nxt = ps.next_cron_fire("0 16 * * *", after=after)
        self.assertIsNotNone(nxt)
        assert nxt is not None
        self.assertEqual(nxt.hour, 16)
        self.assertEqual(nxt.minute, 0)
        self.assertEqual(nxt.day, 8)

    def test_every_15(self) -> None:
        after = datetime(2026, 8, 8, 12, 7, tzinfo=timezone.utc)
        nxt = ps.next_cron_fire("*/15 * * * *", after=after)
        self.assertIsNotNone(nxt)
        assert nxt is not None
        self.assertEqual(nxt.minute, 15)
        self.assertEqual(nxt.hour, 12)

    def test_wed_only(self) -> None:
        # 2026-08-08 is Saturday; next Wed is 2026-08-12
        after = datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)
        nxt = ps.next_cron_fire("0 16 * * 3", after=after)
        self.assertIsNotNone(nxt)
        assert nxt is not None
        self.assertEqual(nxt.weekday(), 2)  # Wed
        self.assertEqual(nxt.day, 12)

    def test_mon_thu(self) -> None:
        after = datetime(2026, 8, 8, 18, 0, tzinfo=timezone.utc)  # Sat
        nxt = ps.next_cron_fire("0 17 * * 1,4", after=after)
        self.assertIsNotNone(nxt)
        assert nxt is not None
        # Next Mon 2026-08-10
        self.assertEqual(nxt.day, 10)
        self.assertEqual(nxt.weekday(), 0)


class TestParseWorkflow(unittest.TestCase):
    def test_parse_replenish(self) -> None:
        p = ps._parse_workflow_yaml(SAMPLE_YAML)
        self.assertEqual(p["name"], "cadence-daily-replenish")
        self.assertEqual(p["cron"], "0 16 * * *")
        self.assertIn("Cadence", p["kick_targets"])
        self.assertNotIn("Primary", p["kick_targets"])

    def test_status_zzz(self) -> None:
        self.assertEqual(ps._status_for_name("zzz-probe-inert"), "inert")
        self.assertEqual(ps._status_for_name("cadence-daily-status"), "active")


class TestPayload(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="process-")
        self.ws = Path(self._td.name) / "ws"
        self.ws.mkdir()
        self.proc = self.ws / "ops" / "process"
        self.proc.mkdir(parents=True)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _write_snap(self, workflows: list[dict]) -> None:
        snap = {
            "schema_version": 1,
            "updated_at": "2026-08-08T00:00:00Z",
            "updated_by": "test",
            "workflows": workflows,
        }
        (self.proc / "workflows_snapshot.json").write_text(
            json.dumps(snap), encoding="utf-8"
        )

    def test_snapshot_only_hides_inert(self) -> None:
        self._write_snap(
            [
                {
                    "id": "85e7e98e-4267-4fe7-8d72-fac49ed3e75b",
                    "name": "cadence-daily-replenish",
                    "cron": "0 16 * * *",
                    "channel_id": ps.CHANNEL_WORKFLOW,
                    "channel_name": "#workflow",
                    "kick_targets": ["Cadence", "Primary"],
                    "status": "active",
                },
                {
                    "id": "b85c12fa-e7e5-43b3-8292-295a1e9f9783",
                    "name": "zzz-retired-cadence-sprint-planning",
                    "cron": "0 0 1 1 *",
                    "channel_id": ps.CHANNEL_WORKFLOW,
                    "channel_name": "#workflow",
                    "kick_targets": [],
                    "status": "inert",
                },
                {
                    "id": "59d56951-77a0-4275-a68c-b1fd794bdba3",
                    "name": "eng-gate-sweep",
                    "cron": "*/15 * * * *",
                    "channel_id": ps.CHANNEL_WORKFLOW,
                    "channel_name": "#workflow",
                    "kick_targets": ["Grok"],
                    "status": "active",
                },
            ]
        )
        now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
        payload = ps.process_payload(
            self.ws, live=False, include_inert=False, now=now
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "snapshot")
        names = [w["name"] for w in payload["workflows"]]
        self.assertIn("cadence-daily-replenish", names)
        self.assertIn("eng-gate-sweep", names)
        self.assertNotIn("zzz-retired-cadence-sprint-planning", names)
        self.assertEqual(payload["counts"]["inert"], 1)
        # Kick placeholders cleaned
        for w in payload["workflows"]:
            self.assertNotIn("Primary", w.get("kick_targets") or [])
        # Process flow present
        self.assertIn("nodes", payload["process_flow"])
        self.assertIn("edges", payload["process_flow"])
        self.assertTrue(payload["day_timeline"] is not None)
        self.assertEqual(len(payload["week"]), 7)
        self.assertIn("ops/backlog", payload["disclaimer"])
        self.assertIn("read-only", payload["disclaimer"].lower())

    def test_include_inert(self) -> None:
        self._write_snap(
            [
                {
                    "id": "x",
                    "name": "zzz-probe-inert",
                    "cron": "0 0 1 1 *",
                    "channel_name": "#workflow",
                    "status": "inert",
                },
            ]
        )
        payload = ps.process_payload(self.ws, live=False, include_inert=True)
        self.assertEqual(len(payload["workflows"]), 1)
        self.assertEqual(payload["workflows"][0]["status"], "inert")

    def test_live_preferred_over_snapshot(self) -> None:
        self._write_snap(
            [
                {
                    "id": "old",
                    "name": "cadence-daily-status",
                    "cron": "0 13 * * *",
                    "channel_name": "#standup",
                    "status": "active",
                }
            ]
        )
        live_rows = [
            ps._normalize_row(
                workflow_id="85e7e98e-4267-4fe7-8d72-fac49ed3e75b",
                content=SAMPLE_YAML,
                channel_id=ps.CHANNEL_WORKFLOW,
                channel_name="#workflow",
            ),
            ps._normalize_row(
                workflow_id="59d56951-77a0-4275-a68c-b1fd794bdba3",
                content=SAMPLE_ENG,
                channel_id=ps.CHANNEL_WORKFLOW,
                channel_name="#workflow",
            ),
            ps._normalize_row(
                workflow_id="b85c12fa-e7e5-43b3-8292-295a1e9f9783",
                content=SAMPLE_ZZZ,
                channel_id=ps.CHANNEL_WORKFLOW,
                channel_name="#workflow",
            ),
        ]
        with mock.patch.object(ps, "fetch_live_workflows", return_value=(live_rows, [])):
            payload = ps.process_payload(self.ws, live=True, include_inert=False)
        self.assertEqual(payload["source"], "relay")
        names = {w["name"] for w in payload["workflows"]}
        self.assertEqual(names, {"cadence-daily-replenish", "eng-gate-sweep"})

    def test_missing_snapshot_no_live(self) -> None:
        with mock.patch.object(
            ps, "fetch_live_workflows", return_value=([], ["buzz missing"])
        ):
            payload = ps.process_payload(self.ws, live=True)
        self.assertFalse(payload["ok"])
        self.assertIn("buzz missing", payload["errors"])

    def test_normalize_from_content(self) -> None:
        row = ps._normalize_row(
            workflow_id="abc",
            content=SAMPLE_YAML,
            channel_id=ps.CHANNEL_WORKFLOW,
            channel_name="#workflow",
        )
        self.assertEqual(row["name"], "cadence-daily-replenish")
        self.assertEqual(row["status"], "active")
        self.assertIn("Cadence", row["kick_targets"])


class TestHumanize(unittest.TestCase):
    def test_known(self) -> None:
        self.assertIn("15", ps.humanize_cron("*/15 * * * *"))
        self.assertIn("13:00", ps.humanize_cron("0 13 * * *"))


if __name__ == "__main__":
    unittest.main()
