"""Trip-change detection + calendar/sheet/task apply. No network."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import turo_calendar  # noqa: E402
import turo_changes  # noqa: E402
import turo_inbox  # noqa: E402
import turo_sheet  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROSTER_UNITS = [
    {
        "id": "m3-2022",
        "year": 2022,
        "make": "Tesla",
        "model": "Model 3",
        "role": "turo",
        "vin": "5YJ3E1EA6NF289917",
    },
    {
        "id": "corolla-2022",
        "year": 2022,
        "make": "Toyota",
        "model": "Corolla",
        "role": "turo",
        "vin": "5YFVPMAE9NP362974",
    },
    {
        "id": "corolla-2024",
        "year": 2024,
        "make": "Toyota",
        "model": "Corolla",
        "role": "turo",
        "vin": "5YFB4MDE9RP121896",
    },
    {
        "id": "r1s-2023",
        "year": 2023,
        "make": "Rivian",
        "model": "R1S",
        "role": "turo",
        "vin": "7PDSGABA3PN028624",
    },
]


class FakeCalendar:
    calendar_id = turo_calendar.TURO_CALENDAR_ID

    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = list(events or [])
        self.patches: list[tuple[str, dict]] = []
        self.creates: list[dict] = []

    def list_events(self, *, query: str = "") -> list[dict]:
        if not query:
            return list(self.events)
        needle = query.lower()
        return [e for e in self.events if needle in json.dumps(e).lower()]

    def patch_event(self, event_id: str, body: dict) -> dict:
        self.patches.append((event_id, dict(body)))
        return {"ok": True, "event": {"id": event_id, **body}}

    def create_event(self, body: dict) -> dict:
        eid = f"created-{len(self.creates) + 1}"
        rec = {"id": eid, **body}
        self.creates.append(rec)
        self.events.append(rec)
        return {"ok": True, "event": rec}


class FakeSheets:
    spreadsheet_id = turo_sheet.RIVIAN_SHEET_ID
    tab = "Turo Bookings"

    def __init__(self, values: list[list]) -> None:
        self.values = [list(r) for r in values]
        self.updates: list[tuple[str, list]] = []

    def get_values(self, a1: str) -> list[list]:
        return self.values

    def update_values(self, a1: str, rows: list) -> dict:
        self.updates.append((a1, [list(r) for r in rows]))
        return {"ok": True}


class FakeGT:
    def __init__(self, tasks: list[dict] | None = None) -> None:
        self.lists = [{"id": "turo-1", "title": "Turo"}]
        self.tasks = list(tasks or [])
        self.completed: list[tuple[str, str, bool]] = []
        self.updated: list[tuple[str, str, str]] = []

    def list_tasklists(self) -> dict:
        return {"ok": True, "lists": self.lists}

    def create_tasklist(self, title: str) -> dict:
        return {"ok": True, "list": {"id": "turo-1", "title": title}}

    def list_tasks(self, list_id: str, **kwargs) -> dict:
        return {
            "ok": True,
            "list_id": list_id,
            "tasks": [t for t in self.tasks if t.get("list_id") == list_id],
        }

    def complete_task(self, list_id: str, task_id: str, *, completed: bool = True) -> dict:
        self.completed.append((list_id, task_id, completed))
        for t in self.tasks:
            if t.get("id") == task_id:
                t["status"] = "completed" if completed else "needsAction"
                return {"ok": True, "task": t}
        return {"ok": False, "error": "not found"}

    def update_task(self, list_id: str, task_id: str, *, title: str | None = None, **kwargs) -> dict:
        self.updated.append((list_id, task_id, title or ""))
        for t in self.tasks:
            if t.get("id") == task_id:
                if title is not None:
                    t["title"] = title
                return {"ok": True, "task": t}
        return {"ok": False, "error": "not found"}


def _payload(name: str) -> dict:
    return turo_inbox.turo_payload(
        inbox_path=FIXTURES / name, units=ROSTER_UNITS
    )


class DetectTests(unittest.TestCase):
    def test_formal_change_email_moves_window(self) -> None:
        payload = _payload("turo_trip_change_formal.json")
        changes = payload["changes"]
        self.assertEqual(len(changes), 1)
        ch = changes[0]
        self.assertEqual(ch["source"], "formal_change")
        self.assertEqual(ch["trip_id"], "60615645")
        self.assertEqual(ch["unit_id"], "corolla-2024")
        self.assertTrue(ch["time_changed"])
        self.assertFalse(ch["place_changed"])
        self.assertIn("2026-09-17T11:00:00", ch["start"])
        self.assertIn("2026-09-19T16:00:00", ch["end"])
        self.assertIn("2026-09-18T10:00:00", ch["prior_end"])
        subjects = {b["subject"] for b in payload["bookings"]}
        self.assertTrue(any("changed their trip" in s.lower() for s in subjects))

    def test_guest_message_extension_giovanni(self) -> None:
        payload = _payload("turo_guest_extend_giovanni.json")
        booked = [b for b in payload["bookings"] if b.get("status") == "booked"]
        self.assertEqual(len(booked), 1)
        self.assertTrue(
            all("sent you a message" not in (b.get("subject") or "").lower() for b in booked)
        )
        changes = payload["changes"]
        self.assertEqual(len(changes), 1)
        ch = changes[0]
        self.assertEqual(ch["source"], "guest_message")
        self.assertEqual(ch["trip_id"], "61378854")
        self.assertEqual(ch["guest"], "Giovanni")
        self.assertEqual(ch["unit_id"], "corolla-2024")
        self.assertTrue(ch["time_changed"])
        self.assertIn("2026-09-19T10:00:00", ch["end"])
        self.assertIn("2026-09-18T10:00:00", ch["prior_end"])

    def test_location_only_matthew_fbo(self) -> None:
        payload = _payload("turo_location_matthew_fbo.json")
        changes = payload["changes"]
        self.assertEqual(len(changes), 1)
        ch = changes[0]
        self.assertEqual(ch["source"], "formal_change")
        self.assertEqual(ch["trip_id"], "61106426")
        self.assertEqual(ch["unit_id"], "r1s-2023")
        self.assertTrue(ch["place_changed"])
        self.assertFalse(ch["time_changed"])
        self.assertIn("Punta Gorda Airport FBO", ch["pickup"])
        self.assertEqual(ch["prior_pickup"], "Host location")

    def test_guest_photo_only_is_not_a_change(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_photo_claim_only.json",
            units=ROSTER_UNITS,
        )
        self.assertEqual(payload["changes"], [])
        self.assertEqual(payload["photo_messages"][0]["kind"], "guest_message")


class ApplyTests(unittest.TestCase):
    def test_formal_change_patches_both_timed_events(self) -> None:
        payload = _payload("turo_trip_change_formal.json")
        ch = payload["changes"][0]
        cal = FakeCalendar(
            [
                {
                    "id": "ev-pick",
                    "summary": "Pickup ready Alex Corolla #60615645",
                    "description": "Reservation ID #60615645",
                },
                {
                    "id": "ev-drop",
                    "summary": "Drop-off/turnover Alex Corolla #60615645",
                    "description": "Reservation ID #60615645",
                },
            ]
        )
        result = turo_changes.apply_change(ch, calendar=cal, sheets=None, gt=FakeGT())
        self.assertTrue(result["calendar"]["ok"])
        kinds = {a["kind"] for a in result["calendar"]["actions"]}
        self.assertEqual(kinds, {"pickup", "dropoff"})
        self.assertEqual({p[0] for p in cal.patches}, {"ev-pick", "ev-drop"})
        pick = next(b for eid, b in cal.patches if eid == "ev-pick")
        drop = next(b for eid, b in cal.patches if eid == "ev-drop")
        self.assertIn("2026-09-17T11:00:00", pick["start"]["dateTime"])
        self.assertIn("2026-09-19T16:00:00", drop["start"]["dateTime"])
        self.assertEqual(pick["start"]["timeZone"], "America/New_York")

    def test_giovanni_guest_extend_closes_stale_end_task(self) -> None:
        payload = _payload("turo_guest_extend_giovanni.json")
        ch = payload["changes"][0]
        cal = FakeCalendar(
            [
                {
                    "id": "gio-pick",
                    "summary": "Pickup ready Giovanni Corolla #61378854",
                    "description": "#61378854",
                },
                {
                    "id": "gio-drop",
                    "summary": "Drop-off Giovanni Corolla #61378854",
                    "description": "#61378854",
                },
            ]
        )
        gt = FakeGT(
            [
                {
                    "id": "task-end",
                    "list_id": "turo-1",
                    "title": "Update Giovanni Corolla Turo END Fri Sep 18 10:00",
                    "notes": "old window #61378854",
                    "status": "needsAction",
                }
            ]
        )
        result = turo_changes.apply_change(ch, calendar=cal, sheets=None, gt=gt)
        self.assertTrue(result["calendar"]["ok"])
        drop = next(b for eid, b in cal.patches if eid == "gio-drop")
        self.assertIn("2026-09-19T10:00:00", drop["start"]["dateTime"])
        self.assertTrue(result["tasks"]["ok"])
        self.assertEqual(result["tasks"]["completed"], ["task-end"])
        self.assertEqual(gt.completed, [("turo-1", "task-end", True)])

    def test_tasks_stay_open_when_calendar_skipped(self) -> None:
        payload = _payload("turo_guest_extend_giovanni.json")
        ch = payload["changes"][0]
        gt = FakeGT(
            [
                {
                    "id": "task-end",
                    "list_id": "turo-1",
                    "title": "Update Giovanni Corolla Turo END Fri Sep 18 10:00",
                    "notes": "",
                    "status": "needsAction",
                }
            ]
        )
        result = turo_changes.apply_change(ch, calendar=None, sheets=None, gt=gt)
        self.assertTrue(result["tasks"]["skipped"])
        self.assertEqual(result["tasks"]["reason"], "calendar_not_applied")
        self.assertEqual(gt.completed, [])

    def test_matthew_fbo_upserts_rivian_sheet_skips_totals(self) -> None:
        payload = _payload("turo_location_matthew_fbo.json")
        ch = payload["changes"][0]
        cal = FakeCalendar(
            [
                {
                    "id": "m-pick",
                    "summary": "Pickup ready Matthew Rivian #61106426",
                    "description": "#61106426",
                },
                {
                    "id": "m-drop",
                    "summary": "Drop-off/turnover Matthew Rivian #61106426",
                    "description": "#61106426",
                },
            ]
        )
        sheets = FakeSheets(
            [
                ["Reservation", "Guest", "Start", "End", "Pickup location", "Totals"],
                ["61106426", "Matthew", "2026-09-13", "2026-09-15", "Host location", ""],
                ["Totals", "", "", "", "", "999"],
            ]
        )
        result = turo_changes.apply_change(ch, calendar=cal, sheets=sheets, gt=FakeGT())
        self.assertTrue(result["calendar"]["ok"])
        pick = next(b for eid, b in cal.patches if eid == "m-pick")
        drop = next(b for eid, b in cal.patches if eid == "m-drop")
        self.assertEqual(pick["location"], "Punta Gorda Airport FBO")
        self.assertEqual(drop["location"], "Punta Gorda Airport FBO")
        self.assertTrue(result["sheet"]["ok"])
        self.assertFalse(result["sheet"]["wrote_totals"])
        self.assertEqual(result["sheet"]["op"], "update")
        self.assertEqual(len(sheets.updates), 1)
        a1, rows = sheets.updates[0]
        self.assertIn("A2:", a1)
        self.assertIn("Punta Gorda Airport FBO", rows[0])
        self.assertNotIn("999", json.dumps(rows))

    def test_sheet_refuses_totals_row(self) -> None:
        change = {
            "trip_id": "999",
            "unit_id": "r1s-2023",
            "vehicle": "2023 Rivian R1S",
            "start": "2026-09-20T10:00:00-04:00",
            "end": "2026-09-21T10:00:00-04:00",
            "pickup": "FBO",
        }
        sheets = FakeSheets(
            [
                ["Reservation", "Guest", "Totals"],
                ["Totals", "", "100"],
            ]
        )
        result = turo_sheet.apply_rivian_sheet(change, sheets)
        self.assertFalse(result["ok"])
        self.assertIn("Totals", result["error"])
        self.assertEqual(sheets.updates, [])

    def test_plan_written_without_apply(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "turo_inbox.json"
            dest.write_text(
                (FIXTURES / "turo_guest_extend_giovanni.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            result = turo_changes.sync_from_dump(dest, apply=False)
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["changes"]), 1)
            self.assertEqual(result["applied"], [])
            plan = Path(result["plan_path"])
            self.assertTrue(plan.is_file())
            data = json.loads(plan.read_text(encoding="utf-8"))
            self.assertEqual(data["changes"][0]["trip_id"], "61378854")
            self.assertEqual(data["calendar_id"], turo_calendar.TURO_CALENDAR_ID)


class DocsTests(unittest.TestCase):
    def test_readme_names_ingest_sources_and_patterns(self) -> None:
        readme = (PKG / "README.md").read_text(encoding="utf-8")
        self.assertIn("Pi dump", readme)
        self.assertIn("Bot overnight", readme)
        self.assertIn("Gmail MCP", readme)
        self.assertIn("changed their trip", readme)
        self.assertIn("sent you a message", readme)
        self.assertIn("Pickup ready", readme)
        self.assertIn("Drop-off/turnover", readme)
        self.assertIn("Turo Bookings", readme)
        self.assertIn("never written", readme.lower() + readme)


if __name__ == "__main__":
    unittest.main()
