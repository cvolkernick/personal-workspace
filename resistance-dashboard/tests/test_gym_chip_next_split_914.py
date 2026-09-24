"""Gym chip + session_type use next-in-rotation after last closed (#914).

Late-night log + civil morning with a stale yesterday wake must not pin
session_type / today.logged / the Gym chip to last night's letter. Plan
exercises, session_type, and chip title must agree. A stale-letter chip is
retitled in place (no second event; #901).
"""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from rt_dashboard.agent_today import assemble_dashboard_slice, export_agent_today
from rt_dashboard.gym_calendar import (
    EVENT_TITLE,
    PROP_DATE,
    PROP_GYM,
    PROP_PLANNED_START,
    GymDay,
    event_body,
    event_summary,
    gym_day_from_workout,
    gym_desc_tag,
    pick_placement,
    reset_busyness_cache,
    sync_gym_from_workout,
    sync_gym_sessions,
)
from rt_dashboard.training_day import ppl_logged_for_planning, wake_covers_as_of
from rt_dashboard.workout_store import load_workspace_goals, stamp_today_session

ET = ZoneInfo("America/New_York")


class _FakeCalendar:
    def __init__(self):
        self.events = {}
        self.creates = []
        self.updates = []
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
        if kw.get("time_min") or kw.get("time_max"):
            items = sorted(
                items,
                key=lambda ev: (
                    str(
                        ((ev.get("start") or {}).get("dateTime")
                         or (ev.get("start") or {}).get("date")
                         or "")
                    ),
                    str(ev.get("id") or ""),
                ),
            )
        return items

    def create_event(self, _cid, body):
        eid = f"created-{len(self.creates) + 1}"
        ev = {"id": eid, **body}
        self.events[eid] = ev
        self.creates.append(eid)
        return ev

    def update_event(self, _cid, eid, body):
        ev = {"id": eid, **body}
        self.events[eid] = ev
        self.updates.append(eid)
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


class WakeCoversDawnCutoff914(unittest.TestCase):
    WAKE = "2026-09-23T07:00:00-04:00"

    def test_after_midnight_before_dawn_still_covers(self):
        now = datetime(2026, 9, 24, 0, 30, tzinfo=ET)
        self.assertTrue(
            wake_covers_as_of(
                self.WAKE, "2026-09-24", now=now, tz_name="America/New_York"
            )
        )

    def test_civil_morning_with_yesterday_wake_does_not_cover(self):
        now = datetime(2026, 9, 24, 7, 51, tzinfo=ET)
        self.assertFalse(
            wake_covers_as_of(
                self.WAKE, "2026-09-24", now=now, tz_name="America/New_York"
            )
        )


class LateNightLogMorningLetter914(unittest.TestCase):
    """Push closed 22:49 → next morning session_type=Pull; logged empty."""

    def setUp(self):
        self.goals, _ = load_workspace_goals()
        self.sessions = [
            {
                "date": "2026-09-21",
                "session_type": "push",
                "closed_at": "2026-09-21T22:49:00-04:00",
                "exercises": [
                    {"name": "Bench", "sets": [{"weight_lbs": 135, "reps": 8}]}
                ],
            }
        ]
        self.wake = "2026-09-21T07:00:00-04:00"
        self.recovery = {
            "score": 80,
            "sparse": False,
            "sleep_battery": {"last_wake_at": self.wake},
        }

    def test_morning_booking_advances_to_pull(self):
        now = datetime(2026, 9, 22, 7, 51, tzinfo=ET)
        slot = stamp_today_session(
            {"exercises": [], "empty": True},
            self.sessions,
            self.goals,
            self.recovery,
            as_of="2026-09-22",
            now=now,
        )
        self.assertFalse(slot.get("ppl_logged_today"))
        self.assertEqual(slot["session_type"], "pull")
        self.assertEqual(slot["next_session_type"], "pull")
        self.assertIsNone(
            ppl_logged_for_planning(
                self.sessions,
                as_of="2026-09-22",
                last_wake_at=self.wake,
                now=now,
            )
        )

    def test_after_midnight_same_wake_still_pins_push(self):
        now = datetime(2026, 9, 22, 0, 30, tzinfo=ET)
        slot = stamp_today_session(
            {"exercises": [], "empty": True},
            self.sessions,
            self.goals,
            self.recovery,
            as_of="2026-09-22",
            now=now,
        )
        self.assertEqual(slot.get("ppl_logged_today"), "push")
        self.assertEqual(slot["session_type"], "push")


class LetterAgreement914(unittest.TestCase):
    """session_type, chip title, and plan_exercises never disagree."""

    CASES = (
        # last_letter, log_day, civil_morning, expect
        ("push", "2026-09-21", "2026-09-22", "pull"),
        ("pull", "2026-09-22", "2026-09-23", "legs"),
        ("legs", "2026-09-23", "2026-09-24", "push"),
    )
    PLAN = {
        "push": "DB Incline Press",
        "pull": "Chest-Supported Row",
        "legs": "Leg Press",
    }

    def setUp(self):
        self.goals, _ = load_workspace_goals()

    def _check(self, last_letter, log_day, civil, expect):
        sessions = [
            {
                "date": log_day,
                "session_type": last_letter,
                "closed_at": f"{log_day}T22:30:00-04:00",
                "exercises": [
                    {
                        "name": f"{last_letter}-lift",
                        "sets": [{"weight_lbs": 100, "reps": 8}],
                    }
                ],
            }
        ]
        wake = f"{log_day}T07:00:00-04:00"
        now = datetime(
            int(civil[:4]), int(civil[5:7]), int(civil[8:10]), 7, 51, tzinfo=ET
        )
        recovery = {
            "score": 80,
            "sparse": False,
            "sleep_battery": {"last_wake_at": wake},
        }
        slot = stamp_today_session(
            {"exercises": [], "empty": True},
            sessions,
            self.goals,
            recovery,
            as_of=civil,
            now=now,
        )
        self.assertEqual(slot["session_type"], expect)
        self.assertFalse(slot.get("ppl_logged_today"))
        title = event_summary(slot["session_type"])
        self.assertEqual(title, f"Gym · {expect.capitalize()}")

        gym = gym_day_from_workout(
            {
                "session_type": slot["session_type"],
                "is_rest_day": False,
                "exercises": [{"name": self.PLAN[expect]}],
            },
            civil,
            now=now,
        )
        self.assertIsNotNone(gym)
        self.assertEqual(gym.session_type, expect)
        body = event_body(civil, pick_placement(civil, []), session_type=gym.session_type)
        self.assertEqual(body["summary"], title)

        payload = assemble_dashboard_slice(
            date=civil,
            sessions=sessions,
            workout_plan={
                "session_type": expect,
                "is_rest_day": False,
                "exercises": [
                    {
                        "name": self.PLAN[expect],
                        "target_sets": 3,
                        "target_reps": 8,
                    }
                ],
                "next_session_type": expect,
            },
            workout={},
            sleep_battery={"last_wake_at": wake},
            recovery=recovery,
            goals=self.goals,
        )

        def _stamp(*a, **k):
            k.setdefault("now", now)
            return stamp_today_session(*a, **k)

        with mock.patch(
            "rt_dashboard.workout_store.stamp_today_session", side_effect=_stamp
        ), mock.patch("rt_dashboard.timeutil.local_now", return_value=now):
            out = export_agent_today(payload)
        wo = out["today"]["workout"]
        self.assertEqual(wo["session_type"], expect)
        self.assertEqual(wo["logged_exercises"], [])
        self.assertEqual(
            [e["name"] for e in wo["plan_exercises"]], [self.PLAN[expect]]
        )

    def test_push_to_pull(self):
        self._check(*self.CASES[0])

    def test_pull_to_legs(self):
        self._check(*self.CASES[1])

    def test_legs_to_push(self):
        self._check(*self.CASES[2])


class LoggedTodayStillPins914(unittest.TestCase):
    """A session closed in the current wake still pins today's letter."""

    def test_same_wake_morning_log_pins(self):
        goals, _ = load_workspace_goals()
        now = datetime(2026, 9, 24, 10, 0, tzinfo=ET)
        wake = "2026-09-24T07:00:00-04:00"
        sessions = [
            {
                "date": "2026-09-24",
                "session_type": "push",
                "closed_at": "2026-09-24T09:30:00-04:00",
                "exercises": [
                    {"name": "Bench", "sets": [{"weight_lbs": 135, "reps": 8}]}
                ],
            }
        ]
        slot = stamp_today_session(
            {"exercises": [], "empty": True},
            sessions,
            goals,
            {
                "score": 80,
                "sparse": False,
                "sleep_battery": {"last_wake_at": wake},
            },
            as_of="2026-09-24",
            now=now,
        )
        self.assertEqual(slot.get("ppl_logged_today"), "push")
        self.assertEqual(slot["session_type"], "push")


class StaleChipRetitledInPlace914(unittest.TestCase):
    """Reconcile retitles a stale-letter chip in place; never duplicates."""

    DAY = "2026-09-24"
    MORNING = datetime(2026, 9, 24, 11, 0, tzinfo=ET)

    def setUp(self):
        reset_busyness_cache()

    def tearDown(self):
        reset_busyness_cache()

    def test_monitor_updates_summary_keeps_id(self):
        cal = _FakeCalendar()
        start = f"{self.DAY}T21:20:00-04:00"
        end = f"{self.DAY}T22:50:00-04:00"
        cal.events["morning-1"] = {
            "id": "morning-1",
            "summary": "Gym · Legs",
            "description": f"Legs day per FitDash\n{gym_desc_tag(self.DAY)}",
            "created": "2026-09-24T07:51:00-04:00",
            "start": {"dateTime": start, "timeZone": "America/New_York"},
            "end": {"dateTime": end, "timeZone": "America/New_York"},
            "extendedProperties": {
                "private": {
                    PROP_GYM: "1",
                    PROP_DATE: self.DAY,
                    PROP_PLANNED_START: start,
                }
            },
        }
        with _patch_cal(cal):
            result = sync_gym_sessions(
                [GymDay(day=self.DAY, is_rest=False, session_type="push")],
                now=self.MORNING,
                role="monitor",
            )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(cal.creates, [])
        self.assertEqual(cal.updates, ["morning-1"])
        self.assertEqual(len(cal.events), 1)
        body = cal.events["morning-1"]
        self.assertEqual(body["summary"], "Gym · Push")
        self.assertIn(gym_desc_tag(self.DAY), body["description"])

    def test_coach_rerun_also_retitles_not_duplicates(self):
        cal = _FakeCalendar()
        start = f"{self.DAY}T21:20:00-04:00"
        end = f"{self.DAY}T22:50:00-04:00"
        cal.events["morning-1"] = {
            "id": "morning-1",
            "summary": "Gym · Legs",
            "description": gym_desc_tag(self.DAY),
            "created": "2026-09-24T07:51:00-04:00",
            "start": {"dateTime": start, "timeZone": "America/New_York"},
            "end": {"dateTime": end, "timeZone": "America/New_York"},
            "extendedProperties": {
                "private": {
                    PROP_GYM: "1",
                    PROP_DATE: self.DAY,
                    PROP_PLANNED_START: start,
                }
            },
        }
        with _patch_cal(cal):
            result = sync_gym_from_workout(
                {
                    "is_rest_day": False,
                    "session_type": "push",
                    "exercises": [{"name": "DB Incline Press"}],
                },
                day=self.DAY,
                now=self.MORNING,
                role="coach",
            )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(cal.creates, [])
        self.assertEqual(cal.updates, ["morning-1"])
        self.assertEqual(cal.events["morning-1"]["summary"], "Gym · Push")


class EventSummaryHelper914(unittest.TestCase):
    def test_letter_titles(self):
        self.assertEqual(event_summary("push"), "Gym · Push")
        self.assertEqual(event_summary("PULL"), "Gym · Pull")
        self.assertEqual(event_summary("legs"), "Gym · Legs")
        self.assertEqual(event_summary(""), EVENT_TITLE)
        self.assertEqual(event_summary("rest"), EVENT_TITLE)


if __name__ == "__main__":
    unittest.main()
