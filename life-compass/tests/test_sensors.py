"""Sensors fail honest when live tools are absent."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from sensors import collect_calendar, empty_snapshot  # noqa: E402

NOW = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)


class TestSensorsHonest(unittest.TestCase):
    def test_empty_snapshot_marks_unwired(self) -> None:
        snap = empty_snapshot(now=NOW)
        self.assertFalse(snap["fitness"]["wired"])
        self.assertFalse(snap["operations"]["invoice_ready"]["wired"])

    def test_calendar_without_cli_is_not_ok(self) -> None:
        cal, trips, training = collect_calendar(now=NOW, prepped_event_ids=set())
        # hatch_gws_cli is typically absent in CI — never paint as wired OK.
        if not cal.get("wired"):
            self.assertTrue(cal.get("error"))
            self.assertEqual(trips, [])
        else:
            self.assertIn("event_count", cal)
