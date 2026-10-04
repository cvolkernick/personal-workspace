"""Logged or deleted gym chips are not recreated (#1064)."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.gym_calendar import (
    PROP_DATE,
    PROP_GYM,
    PROP_PLANNED_START,
    apply_civil_booking_flags,
    civil_closed_letter,
    gym_day_from_workout,
    gym_desc_tag,
    sync_gym_from_workout,
)
from rt_dashboard.workout_plan_store import (
    clear_memory_workout_plans,
    load_last_good_workout_plan,
    save_last_good_workout_plan,
)
from rt_dashboard import gym_calendar

ET = ZoneInfo("America/New_York")
DAY = "2026-10-04"
WAKE = "2026-10-03T12:05:00-04:00"
CLOSED_AT = "2026-10-04T04:48:00-04:00"
NOW = datetime(2026, 10, 4, 9, 6, tzinfo=ET)


def _legs_session(*, date: str = "2026-10-03", closed_at: str = CLOSED_AT) -> dict:
    return {
        "date": date,
        "session_type": "legs",
        "closed_at": closed_at,
        "exercises": [
            {"name": "Romanian Deadlift", "sets": [{"weight_lbs": 45, "reps": 7}]},
            {"name": "Leg Press", "sets": [{"weight_lbs": 185, "reps": 5}]},
        ],
    }


def _open_legs() -> dict:
    return {
        "session_type": "legs",
        "is_rest_day": False,
        "exercises": [{"name": "Leg Press", "sets": 3, "reps": 8, "weight_lbs": 180}],
        "context": {},
    }


class _FakeCalendar:
    def __init__(self):
        self.events = {}
        self.creates = []
        self.deletes = []

    def list_events(self, _cid, **kw):
        items = list(self.events.values())
        props = kw.get("private_props") or {}
        if props:
            kept = []
            for ev in items:
                private = ((ev.get("extendedProperties") or {}).get("private") or {})
                if all(str(private.get(k) or "") == str(v) for k, v in props.items()):
                    kept.append(ev)
            items = kept
        return items

    def create_event(self, _cid, body):
        eid = f"created-{len(self.creates) + 1}"
        ev = {"id": eid, **body}
        self.events[eid] = ev
        self.creates.append(body)
        return ev

    def update_event(self, _cid, eid, body):
        ev = {"id": eid, **body}
        self.events[eid] = ev
        return ev

    def delete_event(self, _cid, eid):
        self.events.pop(eid, None)
        self.deletes.append(eid)
        return {"ok": True, "deleted": True, "event_id": eid}


def _patch_cal(cal):
    return mock.patch.multiple(
        "rt_dashboard.gym_calendar.gcal",
        credentials_status=mock.Mock(return_value={"ok": True}),
        resolve_calendar_id=mock.Mock(return_value="primary"),
        list_events=mock.Mock(side_effect=cal.list_events),
        create_event=mock.Mock(side_effect=cal.create_event),
        update_event=mock.Mock(side_effect=cal.update_event),
        delete_event=mock.Mock(side_effect=cal.delete_event),
    )


def _chip():
    start = f"{DAY}T22:00:00-04:00"
    end = f"{DAY}T23:30:00-04:00"
    return {
        "id": "chip-1",
        "summary": "Gym · Legs",
        "description": gym_desc_tag(DAY),
        "created": f"{DAY}T04:00:00-04:00",
        "start": {"dateTime": start, "timeZone": "America/New_York"},
        "end": {"dateTime": end, "timeZone": "America/New_York"},
        "extendedProperties": {
            "private": {
                PROP_GYM: "1",
                PROP_DATE: DAY,
                PROP_PLANNED_START: start,
            }
        },
    }


class CivilCloseIgnoresStaleWake(unittest.TestCase):
    def test_0448_close_on_prior_session_date_counts_for_today(self):
        letter = civil_closed_letter([_legs_session()], DAY)
        self.assertEqual(letter, "legs")
        gym = gym_day_from_workout(
            {
                "session_type": "legs",
                "exercises": [{"name": "Romanian Deadlift"}],
                "last_wake_at": WAKE,
                "sessions": [_legs_session()],
            },
            DAY,
            now=NOW,
        )
        self.assertTrue(gym.session_closed)
        self.assertTrue(gym.workout_logged)

    def test_coach_sync_deletes_unlocked_chip_and_does_not_create(self):
        cal = _FakeCalendar()
        cal.events["chip-1"] = _chip()
        workout = {
            "session_type": "legs",
            "is_rest_day": False,
            "exercises": [{"name": "Romanian Deadlift"}],
            "last_wake_at": WAKE,
            "sessions": [_legs_session()],
        }
        with _patch_cal(cal):
            result = sync_gym_from_workout(workout, day=DAY, now=NOW, role="coach")
        self.assertTrue(result["ok"], result)
        self.assertEqual(cal.creates, [])
        self.assertEqual(result["created"], 0)
        self.assertEqual(cal.deletes, ["chip-1"])
        self.assertNotIn("chip-1", cal.events)

    def test_board_flag_alone_drops_the_chip(self):
        workout = {
            "session_type": "legs",
            "is_rest_day": False,
            "exercises": [{"name": "Leg Press"}],
        }
        apply_civil_booking_flags(workout, [_legs_session()], DAY)
        self.assertTrue(workout["session_closed_today"])
        cal = _FakeCalendar()
        cal.events["chip-1"] = _chip()
        with _patch_cal(cal):
            result = sync_gym_from_workout(workout, day=DAY, now=NOW, role="coach")
        self.assertEqual(result["created"], 0)
        self.assertEqual(cal.creates, [])
        self.assertEqual(cal.deletes, ["chip-1"])

    def test_partial_log_does_not_create_a_missing_chip(self):
        cal = _FakeCalendar()
        workout = {
            "session_type": "legs",
            "is_rest_day": False,
            "exercises": [{"name": "Leg Press"}],
            "sessions": [
                {
                    "date": DAY,
                    "session_type": "legs",
                    "exercises": [{"name": "Leg Press"}],
                }
            ],
        }
        gym = gym_day_from_workout(workout, DAY, now=NOW)
        self.assertFalse(gym.session_closed)
        self.assertTrue(gym.workout_logged)
        with _patch_cal(cal):
            result = sync_gym_from_workout(workout, day=DAY, now=NOW, role="coach")
        self.assertEqual(result["created"], 0)
        self.assertEqual(cal.creates, [])
        self.assertEqual(cal.deletes, [])


class DeletedChipStaysDeleted(unittest.TestCase):
    def setUp(self):
        gym_calendar._PROCESS_CHIPS.clear()
        clear_memory_workout_plans()

    def tearDown(self):
        gym_calendar._PROCESS_CHIPS.clear()
        clear_memory_workout_plans()

    def test_second_coach_sync_does_not_recreate(self):
        cal = _FakeCalendar()
        workout = _open_legs()
        with _patch_cal(cal):
            first = sync_gym_from_workout(workout, day=DAY, now=NOW, role="coach")
            self.assertEqual(first["created"], 1, first)
            self.assertEqual(len(cal.creates), 1)
            self.assertIn(DAY, (workout.get("context") or {}).get("gym_chip_created") or [])
            # Deleted outside FitDash.
            cal.events.clear()
            cal.deletes.clear()
            second = sync_gym_from_workout(workout, day=DAY, now=NOW, role="coach")
        self.assertEqual(second["created"], 0, second)
        self.assertEqual(len(cal.creates), 1)
        self.assertEqual(cal.events, {})

    def test_explicit_rearm_may_create_again(self):
        cal = _FakeCalendar()
        workout = _open_legs()
        with _patch_cal(cal):
            first = sync_gym_from_workout(workout, day=DAY, now=NOW, role="coach")
            self.assertEqual(first["created"], 1, first)
            cal.events.clear()
            again = sync_gym_from_workout(
                workout, day=DAY, now=NOW, role="coach", rearm=True
            )
        self.assertEqual(again["created"], 1, again)
        self.assertEqual(len(cal.creates), 2)

    def test_saved_plan_stamp_blocks_a_fresh_workout(self):
        cal = _FakeCalendar()
        uid = "gym-chip-1064"
        plan = _open_legs()
        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=False
        ), _patch_cal(cal):
            stored = save_last_good_workout_plan(uid, DAY, plan)
            self.assertTrue(stored.get("ok"), stored)
            first = sync_gym_from_workout(
                plan, day=DAY, now=NOW, role="coach", user_id=uid
            )
            self.assertEqual(first["created"], 1, first)
            saved = load_last_good_workout_plan(uid, DAY)
            self.assertIn(DAY, (saved.get("context") or {}).get("gym_chip_created") or [])
            cal.events.clear()
            fresh = _open_legs()
            second = sync_gym_from_workout(
                fresh, day=DAY, now=NOW, role="coach", user_id=uid
            )
        self.assertEqual(second["created"], 0, second)
        self.assertEqual(len(cal.creates), 1)


class AgentTodayClosedFlag(unittest.TestCase):
    def test_session_closed_today_on_stale_wake(self):
        body = export_agent_today(
            {
                "meta": {"local_today": DAY},
                "coach": {
                    "today": {
                        "date": DAY,
                        "workout": {
                            "session_type": "legs",
                            "is_rest_day": False,
                            "exercises": [],
                        },
                    }
                },
                "workout": {
                    "session_type": "legs",
                    "is_rest_day": False,
                    "exercises": [],
                },
                "sessions": [_legs_session()],
                "recovery": {"sleep_battery": {"last_wake_at": WAKE}},
            }
        )
        wo = body["today"]["workout"]
        self.assertTrue(wo["session_closed_today"])
        self.assertEqual(wo["session_type"], "legs")

    def test_open_day_is_false(self):
        body = export_agent_today(
            {
                "meta": {"local_today": DAY},
                "coach": {"today": {"date": DAY}},
                "workout": {
                    "session_type": "legs",
                    "is_rest_day": False,
                    "exercises": [{"name": "Leg Press"}],
                },
            }
        )
        self.assertFalse(body["today"]["workout"]["session_closed_today"])


if __name__ == "__main__":
    unittest.main()
