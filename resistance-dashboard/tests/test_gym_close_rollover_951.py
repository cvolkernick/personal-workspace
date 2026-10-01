"""Same-day close: cancel the gym chip, roll the next letter, keep a real plan (#951)."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from rt_dashboard.agent_plan import fill_stamped_workout
from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.workout_plan_store import clear_memory_workout_plans
from rt_dashboard.equipment_store import load_workspace_equipment
from rt_dashboard.gym_calendar import (
    PROP_DATE,
    PROP_GYM,
    PROP_PLANNED_START,
    ChosenSlot,
    GymDay,
    QuietWindow,
    event_summary,
    gym_day_from_workout,
    gym_desc_tag,
    pick_placement,
    slot_starts_quiet_hours,
    sync_gym_from_workout,
    sync_gym_sessions,
)
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import generate_workout_plan, next_letter_after
from rt_dashboard.workout_store import load_workspace_catalog, load_workspace_goals, stamp_today_session

ET = ZoneInfo("America/New_York")
ROTATION = ("push", "pull", "legs")


def _closed(day: str, letter: str, at: str, name: str = "Log") -> Session:
    return Session(
        date=day,
        session_type=letter,
        closed_at=at,
        exercises=[
            ExerciseEntry(
                name=name,
                sets=[SetEntry(weight_lbs=40, sets=2, reps=8)],
            )
        ],
    )


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


class QuietHours951(unittest.TestCase):
    def test_midnight_through_0359_is_quiet(self):
        self.assertTrue(
            slot_starts_quiet_hours(datetime(2026, 9, 27, 0, 0, tzinfo=ET))
        )
        self.assertTrue(
            slot_starts_quiet_hours(datetime(2026, 9, 27, 3, 59, tzinfo=ET))
        )
        self.assertFalse(
            slot_starts_quiet_hours(datetime(2026, 9, 27, 4, 0, tzinfo=ET))
        )

    def test_create_refuses_a_0200_slot(self):
        overnight = ChosenSlot(
            start=datetime(2026, 9, 27, 2, 0, tzinfo=ET),
            end=datetime(2026, 9, 27, 3, 30, tzinfo=ET),
            occupancy_pct=10,
            window=QuietWindow("02:00", "03:30", 10),
        )
        cal = _FakeCalendar()
        with _patch_cal(cal), mock.patch(
            "rt_dashboard.gym_calendar.pick_placement", return_value=overnight
        ):
            result = sync_gym_sessions(
                [GymDay(day="2026-09-27", session_type="push")],
                now=datetime(2026, 9, 27, 1, 0, tzinfo=ET),
                role="coach",
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(cal.creates, [])
        self.assertEqual(result["created"], 0)


class BookingAfterClose951(unittest.TestCase):
    DAY = "2026-09-27"

    def _chip(self):
        start = f"{self.DAY}T22:00:00-04:00"
        end = f"{self.DAY}T23:30:00-04:00"
        return {
            "id": "chip-1",
            "summary": "Gym · Legs",
            "description": gym_desc_tag(self.DAY),
            "created": f"{self.DAY}T07:53:00-04:00",
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

    def test_closed_day_does_not_insert(self):
        cal = _FakeCalendar()
        workout = {
            "session_type": "legs",
            "is_rest_day": False,
            "exercises": [{"name": "Leg Press"}],
            "session_closed_today": True,
            "already_trained_today": True,
        }
        with _patch_cal(cal):
            result = sync_gym_from_workout(
                workout,
                day=self.DAY,
                now=datetime(2026, 9, 27, 7, 53, tzinfo=ET),
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(cal.creates, [])
        self.assertEqual(result["created"], 0)
        self.assertEqual(len(cal.events), 0)

    def test_existing_chip_is_deleted_on_close(self):
        cal = _FakeCalendar()
        cal.events["chip-1"] = self._chip()
        workout = {
            "session_type": "legs",
            "is_rest_day": False,
            "exercises": [{"name": "Leg Press"}],
            "sessions": [
                {
                    "date": self.DAY,
                    "session_type": "legs",
                    "closed_at": "2026-09-27T05:10:00-04:00",
                    "exercises": [{"name": "Leg Press"}],
                }
            ],
        }
        gym = gym_day_from_workout(
            workout, self.DAY, now=datetime(2026, 9, 27, 7, 53, tzinfo=ET)
        )
        self.assertTrue(gym.session_closed)
        with _patch_cal(cal):
            result = sync_gym_from_workout(
                workout,
                day=self.DAY,
                now=datetime(2026, 9, 27, 7, 53, tzinfo=ET),
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(cal.creates, [])
        self.assertEqual(cal.deletes, ["chip-1"])
        self.assertEqual(len(cal.events), 0)
        self.assertNotIn("chip-1", cal.events)

    def test_partial_log_keeps_the_chip(self):
        cal = _FakeCalendar()
        cal.events["chip-1"] = self._chip()
        workout = {
            "session_type": "legs",
            "is_rest_day": False,
            "exercises": [{"name": "Leg Press"}],
            "ppl_logged_today": "legs",
            "already_trained_today": False,
            "sessions": [
                {
                    "date": self.DAY,
                    "session_type": "legs",
                    "exercises": [{"name": "Leg Press"}],
                }
            ],
        }
        gym = gym_day_from_workout(workout, self.DAY)
        self.assertFalse(gym.session_closed)
        with _patch_cal(cal):
            result = sync_gym_from_workout(workout, day=self.DAY)
        self.assertTrue(result["ok"], result)
        self.assertEqual(cal.deletes, [])
        self.assertIn("chip-1", cal.events)


class RotationAfterClose951(unittest.TestCase):
    def setUp(self):
        self.goals, _ = load_workspace_goals()

    def _stamp(self, letter, day, at, now):
        wake_day = day
        hour = now.hour
        if hour < 5:
            wake = (
                datetime(now.year, now.month, now.day, tzinfo=ET)
                .replace(hour=7)
            )
            # Overnight close still belongs to the previous wake.
            from datetime import timedelta

            wake = wake - timedelta(days=1)
            wake_iso = wake.isoformat()
        else:
            wake_iso = f"{wake_day}T04:00:00-04:00"
        slot = stamp_today_session(
            {"exercises": [], "empty": True},
            [_closed(day, letter, at, name="Logged")],
            self.goals,
            {
                "score": 80,
                "sparse": False,
                "sleep_battery": {"last_wake_at": wake_iso},
            },
            as_of=day,
            now=now,
        )
        return slot

    def test_push_pull_legs_and_overnight(self):
        cases = (
            ("push", "pull", "2026-09-25", "2026-09-25T06:20:00-04:00", datetime(2026, 9, 25, 8, 0, tzinfo=ET)),
            ("pull", "legs", "2026-09-26", "2026-09-26T18:00:00-04:00", datetime(2026, 9, 26, 19, 0, tzinfo=ET)),
            ("legs", "push", "2026-09-27", "2026-09-27T05:10:00-04:00", datetime(2026, 9, 27, 7, 53, tzinfo=ET)),
            ("push", "pull", "2026-09-28", "2026-09-28T00:38:00-04:00", datetime(2026, 9, 28, 0, 45, tzinfo=ET)),
        )
        for done, nxt, day, at, now in cases:
            slot = self._stamp(done, day, at, now)
            self.assertEqual(slot["session_type"], done, (done, day))
            self.assertEqual(slot["next_session_type"], nxt, (done, day))
            self.assertEqual(next_letter_after(done, self.goals), nxt)
            self.assertTrue(slot.get("session_closed_today"), (done, day))


class PlanAfterClose951(unittest.TestCase):
    def setUp(self):
        self.catalog, _ = load_workspace_catalog()
        self.equipment, _ = load_workspace_equipment()
        self.goals, _ = load_workspace_goals()

    def _plan(self, sessions, day, now, wake):
        return generate_workout_plan(
            self.catalog,
            self.goals,
            sessions,
            recovery_score=80,
            recovery_sparse=True,
            as_of=day,
            equipment=self.equipment,
            last_wake_at=wake,
            now=now,
        )

    def test_each_letter_with_library_is_nonempty(self):
        prior = {
            "push": None,
            "pull": ("push", "2026-09-20"),
            "legs": ("pull", "2026-09-21"),
        }
        for letter in ROTATION:
            sessions = []
            if prior[letter]:
                prev, prev_day = prior[letter]
                sessions.append(
                    _closed(prev_day, prev, f"{prev_day}T18:00:00-04:00", name="Prior")
                )
            plan = self._plan(
                sessions,
                "2026-09-22",
                datetime(2026, 9, 22, 9, 0, tzinfo=ET),
                "2026-09-22T07:00:00-04:00",
            )
            self.assertEqual(plan["session_type"], letter, letter)
            self.assertTrue(plan["exercises"], letter)
            self.assertIsNone(plan.get("generate_error"), letter)

    def test_sep27_legs_close_plans_push(self):
        now = datetime(2026, 9, 27, 7, 53, tzinfo=ET)
        wake = "2026-09-27T04:30:00-04:00"
        plan = self._plan(
            [_closed("2026-09-27", "legs", "2026-09-27T05:10:00-04:00", name="Leg Press")],
            "2026-09-27",
            now,
            wake,
        )
        self.assertEqual(plan["session_type"], "legs")
        self.assertEqual(plan["next_session_type"], "push")
        self.assertTrue(plan["exercises"])
        self.assertIsNone(plan.get("generate_error"))
        names = [e["name"] for e in plan["exercises"]]
        self.assertTrue(names)
        payload = {
            "sessions": [s.to_dict() for s in [
                _closed("2026-09-27", "legs", "2026-09-27T05:10:00-04:00", name="Leg Press")
            ]],
            "workout": plan,
            "workout_store": {"plan": plan, "goals": self.goals},
            "recovery": {
                "score": 80,
                "sparse": True,
                "sleep_battery": {"last_wake_at": wake},
            },
            "goals": self.goals,
            "coach": {"today": {"date": "2026-09-27"}},
        }
        with mock.patch(
            "rt_dashboard.timeutil.local_now", return_value=now
        ), mock.patch(
            "rt_dashboard.workout_store.stamp_today_session",
            side_effect=lambda *a, **k: stamp_today_session(
                *a, **{**k, "now": k.get("now") or now}
            ),
        ):
            body = export_agent_today(payload)
        wo = body["today"]["workout"]
        self.assertEqual(wo["session_type"], "legs")
        self.assertEqual(wo["next_session_type"], "push")
        self.assertTrue(wo["plan_exercises"])
        self.assertIsNone(wo.get("generate_error"))
        self.assertEqual(
            [e["name"] for e in wo["plan_exercises"]],
            names,
        )

    def test_sep28_overnight_push_plans_pull(self):
        now = datetime(2026, 9, 28, 0, 45, tzinfo=ET)
        wake = "2026-09-27T05:00:00-04:00"
        sessions = [
            _closed("2026-09-27", "legs", "2026-09-27T05:10:00-04:00", name="Leg Press"),
            _closed("2026-09-28", "push", "2026-09-28T00:38:00-04:00", name="DB Flat Press"),
        ]
        plan = self._plan(sessions, "2026-09-28", now, wake)
        self.assertEqual(plan["session_type"], "push")
        self.assertEqual(plan["next_session_type"], "pull")
        self.assertTrue(plan["exercises"])
        self.assertIsNone(plan.get("generate_error"))
        self.assertNotIn("Suggested PUSH session (0 exercises", plan["message"])

    def test_today_fill_loads_library_when_context_omits_it(self):
        """GET /api/agent/today fills after the stamp and does not pass catalog."""
        now = datetime(2026, 9, 27, 7, 53, tzinfo=ET)
        wake = "2026-09-27T04:30:00-04:00"
        sessions = [
            _closed("2026-09-27", "legs", "2026-09-27T05:10:00-04:00", name="Leg Press")
        ]
        recovery = {
            "score": 80,
            "sparse": True,
            "sleep_battery": {"last_wake_at": wake},
        }
        stamped = stamp_today_session(
            {"exercises": [], "empty": True},
            sessions,
            self.goals,
            recovery,
            as_of="2026-09-27",
            now=now,
        )
        self.assertTrue(stamped.get("session_closed_today"))
        clear_memory_workout_plans()
        try:
            with mock.patch(
                "rt_dashboard.timeutil.local_now", return_value=now
            ), mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=False
            ), mock.patch(
                "rt_dashboard.gym_calendar.sync_gym_from_workout",
                return_value={"ok": True, "skipped": True},
            ):
                filled = fill_stamped_workout(
                    "sub-951",
                    day="2026-09-27",
                    workout=stamped,
                    sessions=sessions,
                    goals=self.goals,
                    recovery=recovery,
                )
        finally:
            clear_memory_workout_plans()
        self.assertEqual(filled["session_type"], "legs")
        self.assertEqual(filled["next_session_type"], "push")
        self.assertTrue(filled.get("exercises"))
        self.assertIsNone(filled.get("generate_error"))
        names = {str(ex.get("name") or "") for ex in filled["exercises"]}
        self.assertNotIn("Leg Press", names)


class PreLogAgreement951(unittest.TestCase):
    """#914 still holds before any session is logged that day."""

    def test_morning_letter_plan_and_chip_agree(self):
        catalog, _ = load_workspace_catalog()
        equipment, _ = load_workspace_equipment()
        goals, _ = load_workspace_goals()
        now = datetime(2026, 9, 27, 7, 51, tzinfo=ET)
        sessions = [
            _closed("2026-09-26", "pull", "2026-09-26T21:00:00-04:00", name="Row")
        ]
        plan = generate_workout_plan(
            catalog,
            goals,
            sessions,
            recovery_score=80,
            recovery_sparse=True,
            as_of="2026-09-27",
            equipment=equipment,
            last_wake_at="2026-09-26T07:00:00-04:00",
            now=now,
        )
        self.assertEqual(plan["session_type"], "legs")
        self.assertEqual(plan["next_session_type"], "legs")
        self.assertFalse(plan.get("session_closed_today"))
        self.assertTrue(plan["exercises"])
        self.assertIsNone(plan.get("generate_error"))
        gym = gym_day_from_workout(plan, "2026-09-27", now=now)
        self.assertIsNotNone(gym)
        self.assertFalse(gym.session_closed)
        self.assertEqual(gym.session_type, "legs")
        self.assertEqual(event_summary(gym.session_type), "Gym · Legs")
        placed = pick_placement("2026-09-27", [])
        self.assertIsNotNone(placed)
        self.assertFalse(slot_starts_quiet_hours(placed.start))


if __name__ == "__main__":
    unittest.main()
