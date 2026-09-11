"""Nightly wind-down Calendar events from sleep battery empty_at (#653)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from rt_dashboard.daily_plan_tasks import ensure_daily_tasks
from rt_dashboard.gcal_session import MISSING_CALENDAR_SCOPE
from rt_dashboard.winddown_calendar import (
    DURATION,
    EVENT_TITLE,
    PROP_DATE,
    PROP_PLANNED_START,
    PROP_WINDDOWN,
    event_body,
    is_user_locked,
    is_winddown_event,
    night_date_of,
    reconcile_winddown,
    sync_winddown_from_battery,
    winddown_desc_tag,
)

ET = ZoneInfo("America/New_York")


def _battery(empty_at: str, *, mode: str = "awake") -> dict:
    return {
        "mode": mode,
        "empty_at": empty_at,
        "pct_charged": 40.0,
    }


def _ev(*, eid, day, start, end=None, planned=None, title=EVENT_TITLE, tagged=True):
    start_dt = start if "T" in start else f"{day}T{start}"
    if end is None:
        s = datetime.fromisoformat(start_dt)
        end_dt = (s + DURATION).isoformat(timespec="seconds")
    else:
        end_dt = end if "T" in str(end) else f"{day}T{end}"
    planned_iso = planned if planned is not None else start_dt
    desc = winddown_desc_tag(day) if tagged else "Sleep from elsewhere"
    private = {}
    if tagged:
        private = {
            PROP_WINDDOWN: "1",
            PROP_DATE: day,
            PROP_PLANNED_START: planned_iso,
        }
    return {
        "id": eid,
        "summary": title,
        "description": desc,
        "start": {"dateTime": start_dt, "timeZone": "America/New_York"},
        "end": {"dateTime": end_dt, "timeZone": "America/New_York"},
        "extendedProperties": {"private": private},
    }


def _gcal_ok(**list_side):
    created, updated, deleted = [], [], []
    tagged = list(list_side.get("tagged") or [])
    nearby = list(list_side.get("nearby") or tagged)

    def list_events(cid, **kw):
        props = kw.get("private_props") or {}
        if props.get(PROP_DATE):
            day = props.get(PROP_DATE)
            return [
                ev
                for ev in tagged
                if (ev.get("extendedProperties") or {})
                .get("private", {})
                .get(PROP_DATE)
                == day
            ]
        if kw.get("time_min"):
            return list(nearby)
        return list(tagged)

    patches = [
        mock.patch(
            "rt_dashboard.winddown_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ),
        mock.patch(
            "rt_dashboard.winddown_calendar.gcal.resolve_calendar_id",
            return_value="cvolkern@gmail.com",
        ),
        mock.patch(
            "rt_dashboard.winddown_calendar.gcal.list_events",
            side_effect=list_side.get("list_events") or list_events,
        ),
        mock.patch(
            "rt_dashboard.winddown_calendar.gcal.create_event",
            side_effect=lambda cid, body: created.append(body) or {"id": "ev-new"},
        ),
        mock.patch(
            "rt_dashboard.winddown_calendar.gcal.update_event",
            side_effect=lambda cid, eid, body: updated.append((eid, body)),
        ),
        mock.patch(
            "rt_dashboard.winddown_calendar.gcal.delete_event",
            side_effect=lambda cid, eid: deleted.append(eid) or {"ok": True},
        ),
    ]
    return patches, created, updated, deleted


class EventShape(unittest.TestCase):
    def test_tag_duration_props_from_empty_at(self):
        start = datetime(2026, 9, 11, 0, 18, tzinfo=ET)
        end = start + DURATION
        body = event_body("2026-09-11", start, end)
        self.assertEqual(body["summary"], EVENT_TITLE)
        self.assertEqual(body["start"]["dateTime"], start.isoformat(timespec="seconds"))
        self.assertIn("[fitdash-winddown:2026-09-11]", body["description"])
        self.assertIn("empty_at", body["description"])
        private = body["extendedProperties"]["private"]
        self.assertEqual(private[PROP_WINDDOWN], "1")
        self.assertEqual(private[PROP_DATE], "2026-09-11")
        self.assertEqual(private[PROP_PLANNED_START], body["start"]["dateTime"])
        self.assertEqual(body["reminders"]["overrides"][0]["minutes"], 10)
        self.assertEqual(
            datetime.fromisoformat(body["end"]["dateTime"])
            - datetime.fromisoformat(body["start"]["dateTime"]),
            timedelta(minutes=30),
        )
        self.assertEqual(DURATION, timedelta(minutes=30))

    def test_night_date_follows_empty_at_civil_day(self):
        late = datetime(2026, 9, 10, 23, 0, tzinfo=ET)
        after_midnight = datetime(2026, 9, 11, 0, 18, tzinfo=ET)
        self.assertEqual(night_date_of(late), "2026-09-10")
        self.assertEqual(night_date_of(after_midnight), "2026-09-11")

    def test_user_locked_when_start_differs_from_planned(self):
        ev = _ev(
            eid="ev-1",
            day="2026-09-11",
            start="2026-09-11T22:00:00-04:00",
            planned="2026-09-11T00:18:00-04:00",
        )
        self.assertTrue(is_user_locked(ev))
        ev2 = _ev(
            eid="ev-2",
            day="2026-09-11",
            start="2026-09-11T00:18:00-04:00",
            planned="2026-09-11T00:18:00-04:00",
        )
        self.assertFalse(is_user_locked(ev2))

    def test_untagged_sleep_is_not_a_winddown_event(self):
        sleep = _ev(
            eid="sleep-manual",
            day="2026-09-11",
            start="2026-09-11T00:30:00-04:00",
            title="Sleep",
            tagged=False,
        )
        self.assertFalse(is_winddown_event(sleep))
        self.assertEqual(sleep["summary"], "Sleep")


class SyncCoach(unittest.TestCase):
    def test_no_empty_at_does_not_create(self):
        created = []
        with mock.patch(
            "rt_dashboard.winddown_calendar.gcal.create_event",
            side_effect=lambda *a, **k: created.append(1),
        ):
            result = sync_winddown_from_battery({"mode": "sleeping", "empty_at": None})
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["error_code"], "no_empty_at")
        self.assertEqual(created, [])

    def test_missing_scope_is_honest_skip(self):
        created = []
        with mock.patch(
            "rt_dashboard.winddown_calendar.gcal.credentials_status",
            return_value={
                "ok": False,
                "skipped": True,
                "error": MISSING_CALENDAR_SCOPE,
                "error_code": "missing_calendar_scope",
            },
        ), mock.patch(
            "rt_dashboard.winddown_calendar.gcal.create_event",
            side_effect=lambda *a, **k: created.append(1),
        ):
            result = sync_winddown_from_battery(
                _battery("2026-09-11T00:18:00-04:00")
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["error"], MISSING_CALENDAR_SCOPE)
        self.assertEqual(created, [])

    def test_creates_one_tagged_event_from_empty_at(self):
        patches, created, updated, deleted = _gcal_ok(tagged=[], nearby=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_winddown_from_battery(
                _battery("2026-09-11T00:18:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["summary"], "Wind-down")
        self.assertIn("[fitdash-winddown:2026-09-11]", created[0]["description"])
        self.assertEqual(
            created[0]["start"]["dateTime"], "2026-09-11T00:18:00-04:00"
        )
        self.assertEqual(created[0]["end"]["dateTime"], "2026-09-11T00:48:00-04:00")
        self.assertEqual(updated, [])
        self.assertEqual(deleted, [])
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["night"], "2026-09-11")

    def test_rerun_updates_existing_does_not_stack(self):
        existing = [
            _ev(
                eid="ev-keep",
                day="2026-09-11",
                start="2026-09-11T00:18:00-04:00",
            ),
            _ev(
                eid="ev-dup",
                day="2026-09-11",
                start="2026-09-11T00:18:00-04:00",
            ),
        ]
        patches, created, updated, deleted = _gcal_ok(tagged=existing, nearby=existing)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_winddown_from_battery(
                _battery("2026-09-11T01:00:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(created, [])
        self.assertEqual([eid for eid, _ in updated], ["ev-keep"])
        self.assertEqual(updated[0][1]["start"]["dateTime"], "2026-09-11T01:00:00-04:00")
        self.assertEqual(deleted, ["ev-dup"])

    def test_user_moved_event_is_locked(self):
        existing = [
            _ev(
                eid="ev-locked",
                day="2026-09-11",
                start="2026-09-10T22:00:00-04:00",
                planned="2026-09-11T00:18:00-04:00",
            )
        ]
        patches, created, updated, deleted = _gcal_ok(tagged=existing, nearby=existing)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_winddown_from_battery(
                _battery("2026-09-11T00:18:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["locked"], 1)
        self.assertEqual(created, [])
        self.assertEqual(updated, [])
        self.assertEqual(deleted, [])

    def test_manual_sleep_event_is_never_touched(self):
        sleep = _ev(
            eid="sleep-manual",
            day="2026-09-11",
            start="2026-09-11T00:30:00-04:00",
            title="Sleep",
            tagged=False,
        )

        def list_events(cid, **kw):
            # Untagged Sleep is not in the private-prop listing; include it
            # only on a naive unfiltered call so a bug would still see it.
            if (kw.get("private_props") or {}).get(PROP_WINDDOWN) == "1":
                return []
            return [sleep]

        patches, created, updated, deleted = _gcal_ok()
        patches[2] = mock.patch(
            "rt_dashboard.winddown_calendar.gcal.list_events",
            side_effect=list_events,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_winddown_from_battery(
                _battery("2026-09-11T00:18:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(created), 1)
        self.assertNotIn("sleep-manual", [eid for eid, _ in updated])
        self.assertNotIn("sleep-manual", deleted)

    def test_stale_unlocked_other_night_is_deleted(self):
        stale = _ev(
            eid="ev-stale",
            day="2026-09-10",
            start="2026-09-10T23:00:00-04:00",
        )
        patches, created, updated, deleted = _gcal_ok(tagged=[], nearby=[stale])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_winddown_from_battery(
                _battery("2026-09-11T00:18:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(created), 1)
        self.assertEqual(deleted, ["ev-stale"])

    def test_stale_locked_other_night_is_kept(self):
        stale = _ev(
            eid="ev-stale-locked",
            day="2026-09-10",
            start="2026-09-10T21:00:00-04:00",
            planned="2026-09-10T23:00:00-04:00",
        )
        patches, created, updated, deleted = _gcal_ok(tagged=[], nearby=[stale])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_winddown_from_battery(
                _battery("2026-09-11T00:18:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(created), 1)
        self.assertEqual(deleted, [])
        self.assertGreaterEqual(result["locked"], 1)


class MonitorReconcile(unittest.TestCase):
    def test_monitor_creates_when_night_has_none(self):
        patches, created, updated, deleted = _gcal_ok(tagged=[], nearby=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = reconcile_winddown(_battery("2026-09-11T00:18:00-04:00"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["role"], "monitor")
        self.assertEqual(len(created), 1)

    def test_monitor_does_not_move_locked(self):
        existing = [
            _ev(
                eid="ev-locked",
                day="2026-09-11",
                start="2026-09-10T22:00:00-04:00",
                planned="2026-09-11T00:18:00-04:00",
            )
        ]
        patches, created, updated, deleted = _gcal_ok(tagged=existing, nearby=existing)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = reconcile_winddown(_battery("2026-09-11T01:30:00-04:00"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["locked"], 1)
        self.assertEqual(created, [])
        self.assertEqual(updated, [])


class EnsureWiresWinddown(unittest.TestCase):
    def test_ensure_syncs_winddown_beside_gym(self):
        board = {
            "date": "2026-09-11",
            "actions": [],
            "workout": {
                "is_rest_day": False,
                "session_type": "push",
                "exercises": [{"name": "Bench"}],
            },
            "meal": {"meals": [], "items": []},
            "purchases": [],
        }
        bat = _battery("2026-09-11T00:18:00-04:00")
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ", {"RESISTANCE_DASHBOARD_CONFIG_DIR": tmp}
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.credentials_status",
                return_value={"ok": True},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.resolve_list_id",
                return_value="L1",
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.list_tasks",
                return_value={"ok": True, "tasks": []},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.create_task",
                side_effect=lambda *a, **k: {
                    "ok": True,
                    "task": {
                        "id": "t1",
                        "title": a[1] if len(a) > 1 else "x",
                        "status": "needsAction",
                    },
                },
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.delete_task",
                return_value={"ok": True},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._get_task_safe",
                return_value=None,
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._load_cache",
                return_value={},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._save_cache"
            ), mock.patch(
                "rt_dashboard.meal_calendar.sync_meal_reminders",
                return_value={"ok": True, "upserted": 0, "deleted": 0},
            ), mock.patch(
                "rt_dashboard.gym_calendar.sync_gym_from_workout",
                return_value={"ok": True, "created": 1, "upserted": 1},
            ), mock.patch(
                "rt_dashboard.winddown_calendar.sync_winddown_from_battery",
                return_value={"ok": True, "created": 1, "upserted": 1},
            ) as wind:
                result = ensure_daily_tasks(
                    board, day="2026-09-11", sleep_battery=bat
                )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["winddown_calendar"]["created"], 1)
        wind.assert_called_once()
        self.assertEqual(wind.call_args[0][0]["empty_at"], bat["empty_at"])


if __name__ == "__main__":
    unittest.main()
