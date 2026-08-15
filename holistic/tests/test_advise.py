"""Fixed-clock tests for the Time Allocator advisor composer."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.advise import (  # noqa: E402
    STATUS_NOT_LOADED,
    STATUS_OK,
    STATUS_STALE,
    compose_advise,
    load_advise_inputs,
    people_on_event,
)

TZ = timezone(timedelta(hours=-4))
NOW = datetime(2026, 8, 15, 10, 20, 0, tzinfo=TZ)


def _event(
    title: str,
    start: datetime,
    end: datetime,
    *,
    eid: str = "cal-1",
    attendees: list | None = None,
) -> dict:
    row = {
        "id": eid,
        "title": title,
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
        "source": "google_calendar",
    }
    if attendees is not None:
        row["attendees"] = attendees
    return row


def _inputs(**overrides: object) -> dict:
    base: dict = {
        "calendar_events": [],
        "calendar_meta": {},
        "items": [],
        "targets": [],
        "filed_plan": None,
        "day_plan": None,
        "board": None,
        "body": None,
        "capital": None,
        "sleep_battery": None,
    }
    base.update(overrides)
    return base


def _filed_plan(block_id: str = "deep-work", title: str = "Deep work") -> dict:
    start = datetime(2026, 8, 15, 10, 0, 0, tzinfo=TZ)
    return {
        "window_start": start.isoformat(timespec="seconds"),
        "window_end": (start + timedelta(hours=8)).isoformat(timespec="seconds"),
        "blocks": [
            {"id": block_id, "title": title, "role": "adhoc", "minutes": 120},
            {"id": "lyft", "title": "Lyft driving", "role": "fill", "minutes": 360},
        ],
    }


class PeopleOnEventTests(unittest.TestCase):
    def test_reads_attendees_does_not_invent(self) -> None:
        ev = _event("Sync", NOW, NOW + timedelta(hours=1), attendees=["Will Pfleger"])
        self.assertEqual(people_on_event(ev), ["Will Pfleger"])
        self.assertEqual(people_on_event(_event("Solo", NOW, NOW + timedelta(hours=1))), [])


class ComposeAdviseTests(unittest.TestCase):
    def test_calendar_fixed_beats_flexible_work(self) -> None:
        packet = compose_advise(
            _inputs(
                calendar_events=[
                    _event(
                        "Client call",
                        datetime(2026, 8, 15, 10, 0, tzinfo=TZ),
                        datetime(2026, 8, 15, 11, 0, tzinfo=TZ),
                        attendees=[{"displayName": "Avery Chen"}],
                    )
                ],
                calendar_meta={"synced_at": NOW.isoformat(timespec="seconds"), "ok": True},
                items=[
                    {
                        "id": "deep-work",
                        "title": "Ship advisor",
                        "kind": "task",
                        "priority": 9,
                        "minutes": 90,
                    }
                ],
                filed_plan=_filed_plan(),
            ),
            now=NOW,
        )
        self.assertFalse(packet["stale"])
        now = packet["now"]
        self.assertIsNotNone(now)
        self.assertEqual(now["id"], "cal-1")
        self.assertEqual(now["role"], "calendar")
        self.assertIn("Client call", now["why"])
        self.assertIn("Avery Chen", now["why"])
        self.assertTrue(now["disagrees_with_filed"])
        self.assertEqual(now["filed_now"]["id"], "deep-work")
        self.assertGreaterEqual(len(packet["schedule"]), 2)
        self.assertEqual(packet["sources"]["calendar"]["status"], STATUS_OK)

    def test_soon_calendar_beats_body_gate(self) -> None:
        packet = compose_advise(
            _inputs(
                calendar_events=[
                    _event(
                        "Client call",
                        datetime(2026, 8, 15, 10, 30, tzinfo=TZ),
                        datetime(2026, 8, 15, 11, 0, tzinfo=TZ),
                        attendees=["Avery Chen"],
                    )
                ],
                calendar_meta={"synced_at": NOW.isoformat(timespec="seconds"), "ok": True},
                targets=[
                    {
                        "id": "workout",
                        "title": "Workout",
                        "kind": "weekly_frequency",
                        "session_minutes": 60,
                        "priority": 8,
                    }
                ],
                body={
                    "as_of": NOW.isoformat(timespec="seconds"),
                    "train_recommendation": "rest",
                    "summary": "rec=rest",
                },
            ),
            now=NOW,
        )
        self.assertEqual(packet["now"]["id"], "cal-1")
        self.assertEqual(packet["now"]["role"], "calendar")
        self.assertIn("Avery Chen", packet["now"]["why"])

    def test_red_body_gate_suppresses_filed_workout(self) -> None:
        packet = compose_advise(
            _inputs(
                calendar_events=[],
                calendar_meta={"synced_at": NOW.isoformat(timespec="seconds"), "ok": True},
                targets=[
                    {
                        "id": "workout",
                        "title": "Workout",
                        "kind": "weekly_frequency",
                        "session_minutes": 60,
                        "priority": 8,
                    },
                    {
                        "id": "duchess-walk",
                        "title": "Walk Duchess",
                        "kind": "daily_duration",
                        "minutes": 45,
                        "priority": 9,
                    },
                ],
                body={
                    "as_of": NOW.isoformat(timespec="seconds"),
                    "fresh_for_hours": 24,
                    "stale": False,
                    "train_recommendation": "rest",
                    "session_type": "rest",
                    "summary": "rec=rest; session blocked",
                },
                filed_plan=_filed_plan("workout", "Workout"),
            ),
            now=NOW,
        )
        now = packet["now"]
        self.assertIsNotNone(now)
        self.assertNotEqual(now["id"], "workout")
        self.assertNotIn("workout", str(now["title"]).lower())
        self.assertTrue(now["disagrees_with_filed"])
        self.assertIn("Body:", now["why"])
        ids = {row["id"] for row in packet["schedule"]}
        self.assertNotIn("workout", ids)

    def test_red_capital_gate_suppresses_capital_work(self) -> None:
        packet = compose_advise(
            _inputs(
                calendar_meta={"synced_at": NOW.isoformat(timespec="seconds"), "ok": True},
                items=[
                    {
                        "id": "dca-buy",
                        "title": "Deploy free cash / DCA",
                        "kind": "task",
                        "priority": 10,
                        "minutes": 30,
                    },
                    {
                        "id": "write-spec",
                        "title": "Write spec",
                        "kind": "task",
                        "priority": 4,
                        "minutes": 45,
                    },
                ],
                capital={
                    "as_of": NOW.isoformat(timespec="seconds"),
                    "red_mode": True,
                    "free_cash_gate": "block_new_risk",
                    "stress": {"overall": "red"},
                },
                filed_plan=_filed_plan("dca-buy", "Deploy free cash / DCA"),
            ),
            now=NOW,
        )
        now = packet["now"]
        self.assertIsNotNone(now)
        self.assertNotEqual(now["id"], "dca-buy")
        self.assertNotIn("dca", str(now["id"]).lower())
        ids = {row["id"] for row in packet["schedule"]}
        self.assertNotIn("dca-buy", ids)

    def test_empty_calendar_does_not_invent_meetings(self) -> None:
        packet = compose_advise(
            _inputs(
                calendar_events=[],
                calendar_meta={"synced_at": NOW.isoformat(timespec="seconds"), "ok": True},
                items=[
                    {
                        "id": "ship-pr",
                        "title": "Ship PR",
                        "kind": "task",
                        "priority": 6,
                        "minutes": 40,
                    }
                ],
            ),
            now=NOW,
        )
        self.assertEqual(packet["sources"]["calendar"]["status"], STATUS_OK)
        self.assertEqual(packet["now"]["id"], "ship-pr")
        self.assertNotEqual(packet["now"]["role"], "calendar")
        self.assertTrue(all(row.get("role") != "calendar" for row in packet["schedule"]))
        self.assertNotIn("meeting", json.dumps(packet).lower())

    def test_missing_packets_are_not_loaded(self) -> None:
        packet = compose_advise(_inputs(), now=NOW)
        for key in ("calendar", "day_plan", "board", "body", "capital"):
            self.assertEqual(packet["sources"][key]["status"], STATUS_NOT_LOADED, key)
        self.assertEqual(packet["sources"]["objectives"]["status"], STATUS_OK)
        self.assertIsNone(packet["now"])
        self.assertEqual(packet["schedule"], [])
        self.assertIn("no objectives", packet["reason"] or "")

    def test_stale_packets_marked_stale(self) -> None:
        old = (NOW - timedelta(hours=30)).isoformat(timespec="seconds")
        packet = compose_advise(
            _inputs(
                calendar_events=[
                    _event(
                        "Standup",
                        datetime(2026, 8, 15, 10, 0, tzinfo=TZ),
                        datetime(2026, 8, 15, 10, 30, tzinfo=TZ),
                    )
                ],
                calendar_meta={"synced_at": old, "ok": True},
                day_plan={"as_of": old, "fresh_for_hours": 4, "next3": []},
                board={"as_of": old, "fresh_for_hours": 4, "ready_top": []},
                items=[{"id": "x", "title": "X", "priority": 1, "minutes": 20}],
            ),
            now=NOW,
        )
        self.assertEqual(packet["sources"]["calendar"]["status"], STATUS_STALE)
        self.assertEqual(packet["sources"]["day_plan"]["status"], STATUS_STALE)
        self.assertEqual(packet["sources"]["board"]["status"], STATUS_STALE)
        self.assertEqual(packet["now"]["id"], "cal-1")

    def test_no_objectives_honest_empty(self) -> None:
        packet = compose_advise(
            _inputs(
                calendar_events=[],
                calendar_meta={"synced_at": NOW.isoformat(timespec="seconds"), "ok": True},
            ),
            now=NOW,
        )
        self.assertIsNone(packet["now"])
        self.assertEqual(packet["schedule"], [])
        self.assertEqual(packet["reason"], "no objectives")
        self.assertFalse(any("Lyft" in json.dumps(packet) for _ in (0,)))

    def test_schedule_more_than_two_when_more_exist(self) -> None:
        packet = compose_advise(
            _inputs(
                calendar_events=[
                    _event(
                        "Lunch with Sam",
                        datetime(2026, 8, 15, 12, 0, tzinfo=TZ),
                        datetime(2026, 8, 15, 13, 0, tzinfo=TZ),
                        eid="lunch",
                        attendees=["Sam"],
                    ),
                    _event(
                        "Dentist",
                        datetime(2026, 8, 15, 15, 0, tzinfo=TZ),
                        datetime(2026, 8, 15, 15, 45, tzinfo=TZ),
                        eid="dentist",
                    ),
                ],
                calendar_meta={"synced_at": NOW.isoformat(timespec="seconds"), "ok": True},
                items=[
                    {"id": "a", "title": "Write tests", "priority": 8, "minutes": 45},
                    {"id": "b", "title": "Review PR", "priority": 5, "minutes": 30},
                ],
            ),
            now=NOW,
        )
        self.assertGreaterEqual(len(packet["schedule"]), 3)
        titles = [row["title"] for row in packet["schedule"]]
        self.assertIn("Lunch with Sam", titles)
        self.assertIn("Dentist", titles)
        self.assertTrue(any(t in titles for t in ("Write tests", "Review PR")))

    def test_why_cites_real_input(self) -> None:
        packet = compose_advise(
            _inputs(
                items=[{"id": "spec", "title": "Draft AC", "priority": 7, "minutes": 25}],
            ),
            now=NOW,
        )
        self.assertIn("Draft AC", packet["now"]["why"])
        self.assertIn("Objective", packet["now"]["why"])


class LoadAdviseInputsTests(unittest.TestCase):
    def test_missing_files_are_none_overrides_win(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            loaded = load_advise_inputs(
                {
                    "items": [{"id": "i", "title": "T", "priority": 1}],
                    "calendar_events": [],
                },
                workspace=root,
                packets={"board": {"as_of": NOW.isoformat(), "ready_top": []}},
            )
            self.assertIsNone(loaded["day_plan"])
            self.assertIsNone(loaded["body"])
            self.assertIsNone(loaded["capital"])
            self.assertEqual(loaded["board"]["ready_top"], [])
            self.assertEqual(loaded["items"][0]["id"], "i")


if __name__ == "__main__":
    unittest.main()
