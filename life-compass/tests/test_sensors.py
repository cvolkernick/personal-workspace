"""Sensors fail honest when live tools are absent."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from sensors import (  # noqa: E402
    _classify_turo_kind,
    collect_calendar,
    empty_snapshot,
)

NOW = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)

HELM_EVENTS = [
    {
        "id": "qkban2fkfrjio2t2j64akqcda8",
        "title": "Pickup ready Sat 19 10:00 ET",
        "start": "2026-09-19T14:00:00Z",
    },
    {
        "id": "rvv2bqu4on0jbl4pg8gdrqjtkc",
        "title": "Drop-off Mon 21 10:00 ET",
        "start": "2026-09-21T14:00:00Z",
    },
    {
        "id": "drop-turnover-m3",
        "title": "Drop-off/turnover Tesla Model 3",
        "start": "2026-09-21T14:00:00Z",
    },
    {
        "id": "turo-return-corolla",
        "title": "Turo return Corolla",
        "start": "2026-09-19T18:00:00Z",
    },
]


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


class TestHelmTuroTitles(unittest.TestCase):
    def test_classify_helm_dropoff_as_return(self) -> None:
        self.assertEqual(_classify_turo_kind("Pickup ready Sat 19 10:00 ET"), "pickup")
        self.assertEqual(_classify_turo_kind("Drop-off Mon 21 10:00 ET"), "return")
        self.assertEqual(
            _classify_turo_kind("Drop-off/turnover Tesla Model 3"), "return"
        )
        self.assertEqual(_classify_turo_kind("Turo return Corolla"), "return")
        self.assertIsNone(_classify_turo_kind("Gym — chest"))

    def test_collect_calendar_json_keeps_helm_dropoff_trips(self) -> None:
        proc = Mock(returncode=0, stdout=json.dumps(HELM_EVENTS), stderr="")
        with patch("sensors.shutil.which", return_value="/usr/bin/hatch_gws_cli"), patch(
            "sensors.subprocess.run", return_value=proc
        ):
            cal, trips, _training = collect_calendar(now=NOW, prepped_event_ids=set())
        self.assertTrue(cal.get("wired"))
        by_title = {t["title"]: t for t in trips}
        self.assertEqual(len(trips), 4)
        self.assertEqual(by_title["Pickup ready Sat 19 10:00 ET"]["kind"], "pickup")
        self.assertEqual(by_title["Drop-off Mon 21 10:00 ET"]["kind"], "return")
        self.assertEqual(
            by_title["Drop-off/turnover Tesla Model 3"]["kind"], "return"
        )
        self.assertEqual(by_title["Turo return Corolla"]["kind"], "return")
        for trip in trips:
            self.assertTrue(trip["today"], trip)
            self.assertIn(trip["kind"], ("pickup", "return"))

    def test_collect_calendar_text_agenda_keeps_dropoff_turnover(self) -> None:
        proc = Mock(
            returncode=0,
            stdout="Pickup ready Sat 19 10:00 ET\nDrop-off/turnover Tesla Model 3\n",
            stderr="",
        )
        with patch("sensors.shutil.which", return_value="/usr/bin/hatch_gws_cli"), patch(
            "sensors.subprocess.run", return_value=proc
        ):
            _cal, trips, _training = collect_calendar(
                now=NOW, prepped_event_ids=set()
            )
        kinds = {t["title"]: t["kind"] for t in trips}
        self.assertEqual(kinds["Pickup ready Sat 19 10:00 ET"], "pickup")
        self.assertEqual(kinds["Drop-off/turnover Tesla Model 3"], "return")
        self.assertTrue(all(t["today"] for t in trips))
