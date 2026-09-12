"""Nightly 9h sleep-block Calendar events from sleep battery empty_at (#672)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from rt_dashboard.daily_plan_tasks import ensure_daily_tasks
from rt_dashboard.gcal_session import MISSING_CALENDAR_SCOPE
from rt_dashboard.sleep_block_calendar import (
    DEFAULT_DURATION,
    EVENT_TITLE,
    PROP_DATE,
    PROP_PLANNED_START,
    PROP_SLEEP,
    block_end,
    duration_from_battery,
    event_body,
    is_foreign_sleep_event,
    is_sleep_event,
    is_user_locked,
    night_date_of,
    reconcile_sleep_block,
    sleep_desc_tag,
    sync_sleep_block_from_battery,
)
from rt_dashboard.winddown_calendar import (
    PROP_DATE as WD_DATE,
    PROP_PLANNED_START as WD_PLANNED,
    PROP_WINDDOWN,
    winddown_desc_tag,
)

ET = ZoneInfo("America/New_York")


def _battery(empty_at: str, **extra) -> dict:
    row = {
        "mode": extra.pop("mode", "awake"),
        "empty_at": empty_at,
        "pct_charged": 40.0,
        "sleep_target_hours": 8.0,
        "onset_buffer_hours": 1.0,
        "sleep_around_hours": 9.0,
    }
    row.update(extra)
    return row


def _ev(
    *,
    eid,
    day,
    start,
    end=None,
    planned=None,
    title=EVENT_TITLE,
    tagged=True,
    duration=None,
):
    start_dt = start if "T" in start else f"{day}T{start}"
    if end is None:
        s = datetime.fromisoformat(start_dt)
        span = duration if duration is not None else DEFAULT_DURATION
        end_dt = (s + span).isoformat(timespec="seconds")
    else:
        end_dt = end if "T" in str(end) else f"{day}T{end}"
    planned_iso = planned if planned is not None else start_dt
    desc = sleep_desc_tag(day) if tagged else "Sleep from elsewhere"
    private = {}
    if tagged:
        private = {
            PROP_SLEEP: "1",
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


def _wd(*, eid, day, start, planned=None):
    start_dt = start if "T" in start else f"{day}T{start}"
    s = datetime.fromisoformat(start_dt)
    end_dt = (s + timedelta(minutes=30)).isoformat(timespec="seconds")
    planned_iso = planned if planned is not None else start_dt
    return {
        "id": eid,
        "summary": "Wind-down",
        "description": winddown_desc_tag(day),
        "start": {"dateTime": start_dt, "timeZone": "America/New_York"},
        "end": {"dateTime": end_dt, "timeZone": "America/New_York"},
        "extendedProperties": {
            "private": {
                PROP_WINDDOWN: "1",
                WD_DATE: day,
                WD_PLANNED: planned_iso,
            }
        },
    }


def _gcal_ok(**list_side):
    created, updated, deleted = [], [], []
    tagged = list(list_side.get("tagged") or [])
    nearby = list(list_side.get("nearby") or tagged)
    winddowns = list(list_side.get("winddowns") or [])
    foreign = list(list_side.get("foreign") or [])

    def list_events(cid, **kw):
        props = kw.get("private_props") or {}
        if props.get(PROP_SLEEP) == "1":
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
        if props.get(PROP_WINDDOWN) == "1":
            if props.get(WD_DATE):
                day = props.get(WD_DATE)
                return [
                    ev
                    for ev in winddowns
                    if (ev.get("extendedProperties") or {})
                    .get("private", {})
                    .get(WD_DATE)
                    == day
                ]
            return list(winddowns)
        if kw.get("time_min"):
            return list(foreign) + list(tagged) + list(winddowns)
        return list(tagged)

    patches = [
        mock.patch(
            "rt_dashboard.sleep_block_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ),
        mock.patch(
            "rt_dashboard.sleep_block_calendar.gcal.resolve_calendar_id",
            return_value="cvolkern@gmail.com",
        ),
        mock.patch(
            "rt_dashboard.sleep_block_calendar.gcal.list_events",
            side_effect=list_side.get("list_events") or list_events,
        ),
        mock.patch(
            "rt_dashboard.sleep_block_calendar.gcal.create_event",
            side_effect=lambda cid, body: created.append(body) or {"id": "ev-new"},
        ),
        mock.patch(
            "rt_dashboard.sleep_block_calendar.gcal.update_event",
            side_effect=lambda cid, eid, body: updated.append((eid, body)),
        ),
        mock.patch(
            "rt_dashboard.sleep_block_calendar.gcal.delete_event",
            side_effect=lambda cid, eid: deleted.append(eid) or {"ok": True},
        ),
    ]
    return patches, created, updated, deleted


class EventShape(unittest.TestCase):
    def test_tag_duration_props_from_empty_at(self):
        start = datetime(2026, 9, 12, 4, 0, tzinfo=ET)
        end = start + DEFAULT_DURATION
        body = event_body("2026-09-12", start, end)
        self.assertEqual(body["summary"], EVENT_TITLE)
        self.assertEqual(body["start"]["dateTime"], start.isoformat(timespec="seconds"))
        self.assertIn("[fitdash-sleep:2026-09-12]", body["description"])
        self.assertIn("empty_at", body["description"])
        self.assertIn("wind-down", body["description"])
        private = body["extendedProperties"]["private"]
        self.assertEqual(private[PROP_SLEEP], "1")
        self.assertEqual(private[PROP_DATE], "2026-09-12")
        self.assertEqual(private[PROP_PLANNED_START], body["start"]["dateTime"])
        self.assertEqual(body["reminders"]["overrides"][0]["minutes"], 10)
        self.assertEqual(
            datetime.fromisoformat(body["end"]["dateTime"])
            - datetime.fromisoformat(body["start"]["dateTime"]),
            timedelta(hours=9),
        )
        self.assertEqual(DEFAULT_DURATION, timedelta(hours=9))
        self.assertEqual(end, datetime(2026, 9, 12, 13, 0, tzinfo=ET))

    def test_night_date_follows_empty_at_civil_day(self):
        late = datetime(2026, 9, 11, 23, 0, tzinfo=ET)
        after_midnight = datetime(2026, 9, 12, 4, 0, tzinfo=ET)
        self.assertEqual(night_date_of(late), "2026-09-11")
        self.assertEqual(night_date_of(after_midnight), "2026-09-12")

    def test_user_locked_when_start_differs_from_planned(self):
        ev = _ev(
            eid="ev-1",
            day="2026-09-12",
            start="2026-09-12T02:00:00-04:00",
            planned="2026-09-12T04:00:00-04:00",
        )
        self.assertTrue(is_user_locked(ev))
        ev2 = _ev(
            eid="ev-2",
            day="2026-09-12",
            start="2026-09-12T04:00:00-04:00",
            planned="2026-09-12T04:00:00-04:00",
        )
        self.assertFalse(is_user_locked(ev2))

    def test_untagged_sleep_is_foreign_not_ours(self):
        sleep = _ev(
            eid="sleep-pulse",
            day="2026-09-12",
            start="2026-09-12T00:20:00-04:00",
            end="2026-09-12T08:50:00-04:00",
            title="Sleep",
            tagged=False,
        )
        self.assertFalse(is_sleep_event(sleep))
        self.assertTrue(is_foreign_sleep_event(sleep))
        self.assertEqual(sleep["summary"], "Sleep")

    def test_duration_from_battery_around_hours(self):
        self.assertEqual(
            duration_from_battery(_battery("2026-09-12T04:00:00-04:00")),
            timedelta(hours=9),
        )
        self.assertEqual(
            duration_from_battery(
                _battery("2026-09-12T04:00:00-04:00", sleep_around_hours=10)
            ),
            timedelta(hours=10),
        )

    def test_planned_wake_wins_when_plausible(self):
        start = datetime(2026, 9, 12, 4, 0, tzinfo=ET)
        bat = _battery(
            "2026-09-12T04:00:00-04:00",
            planned_wake_at="2026-09-12T12:30:00-04:00",
        )
        self.assertEqual(block_end(start, bat), datetime(2026, 9, 12, 12, 30, tzinfo=ET))

    def test_stale_planned_wake_before_empty_at_is_ignored(self):
        start = datetime(2026, 9, 12, 4, 0, tzinfo=ET)
        bat = _battery(
            "2026-09-12T04:00:00-04:00",
            planned_wake_at="2026-09-12T01:00:00-04:00",
        )
        self.assertEqual(block_end(start, bat), start + timedelta(hours=9))


class SyncCoach(unittest.TestCase):
    def test_no_empty_at_does_not_create(self):
        created = []
        with mock.patch(
            "rt_dashboard.sleep_block_calendar.gcal.create_event",
            side_effect=lambda *a, **k: created.append(1),
        ):
            result = sync_sleep_block_from_battery(
                {"mode": "sleeping", "empty_at": None}
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["error_code"], "no_empty_at")
        self.assertEqual(created, [])

    def test_missing_scope_is_honest_skip(self):
        created = []
        with mock.patch(
            "rt_dashboard.sleep_block_calendar.gcal.credentials_status",
            return_value={
                "ok": False,
                "skipped": True,
                "error": MISSING_CALENDAR_SCOPE,
                "error_code": "missing_calendar_scope",
            },
        ), mock.patch(
            "rt_dashboard.sleep_block_calendar.gcal.create_event",
            side_effect=lambda *a, **k: created.append(1),
        ):
            result = sync_sleep_block_from_battery(
                _battery("2026-09-12T04:00:00-04:00")
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["error"], MISSING_CALENDAR_SCOPE)
        self.assertEqual(created, [])

    def test_creates_one_tagged_9h_event_from_empty_at(self):
        patches, created, updated, deleted = _gcal_ok(tagged=[], nearby=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_sleep_block_from_battery(
                _battery("2026-09-12T04:00:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["summary"], "Sleep")
        self.assertIn("[fitdash-sleep:2026-09-12]", created[0]["description"])
        self.assertEqual(
            created[0]["start"]["dateTime"], "2026-09-12T04:00:00-04:00"
        )
        self.assertEqual(created[0]["end"]["dateTime"], "2026-09-12T13:00:00-04:00")
        self.assertEqual(updated, [])
        self.assertEqual(deleted, [])
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["night"], "2026-09-12")
        self.assertEqual(result["end_at"], "2026-09-12T13:00:00-04:00")

    def test_rerun_updates_existing_does_not_stack(self):
        existing = [
            _ev(
                eid="ev-keep",
                day="2026-09-12",
                start="2026-09-12T04:00:00-04:00",
            ),
            _ev(
                eid="ev-dup",
                day="2026-09-12",
                start="2026-09-12T04:00:00-04:00",
            ),
        ]
        patches, created, updated, deleted = _gcal_ok(tagged=existing, nearby=existing)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_sleep_block_from_battery(
                _battery("2026-09-12T04:30:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(created, [])
        self.assertEqual([eid for eid, _ in updated], ["ev-keep"])
        self.assertEqual(updated[0][1]["start"]["dateTime"], "2026-09-12T04:30:00-04:00")
        self.assertEqual(updated[0][1]["end"]["dateTime"], "2026-09-12T13:30:00-04:00")
        self.assertEqual(deleted, ["ev-dup"])

    def test_user_moved_event_is_locked(self):
        existing = [
            _ev(
                eid="ev-locked",
                day="2026-09-12",
                start="2026-09-12T02:00:00-04:00",
                planned="2026-09-12T04:00:00-04:00",
            )
        ]
        patches, created, updated, deleted = _gcal_ok(tagged=existing, nearby=existing)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_sleep_block_from_battery(
                _battery("2026-09-12T04:00:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["locked"], 1)
        self.assertEqual(created, [])
        self.assertEqual(updated, [])
        self.assertEqual(deleted, [])

    def test_manual_sleep_event_is_never_touched(self):
        sleep = _ev(
            eid="sleep-manual",
            day="2026-09-12",
            start="2026-09-12T00:20:00-04:00",
            end="2026-09-12T08:50:00-04:00",
            title="Sleep",
            tagged=False,
        )

        def list_events(cid, **kw):
            props = kw.get("private_props") or {}
            if props.get(PROP_SLEEP) == "1" or props.get(PROP_WINDDOWN) == "1":
                return []
            return [sleep]

        patches, created, updated, deleted = _gcal_ok()
        patches[2] = mock.patch(
            "rt_dashboard.sleep_block_calendar.gcal.list_events",
            side_effect=list_events,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_sleep_block_from_battery(
                _battery("2026-09-12T04:00:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(created), 1)
        self.assertNotIn("sleep-manual", [eid for eid, _ in updated])
        self.assertNotIn("sleep-manual", deleted)
        self.assertEqual(result["flagged_count"], 1)
        self.assertEqual(result["flagged"][0]["id"], "sleep-manual")
        self.assertIn("untagged Sleep", created[0]["description"])

    def test_pulse_sleep_flagged_not_deleted(self):
        pulse = _ev(
            eid="pulse-sleep",
            day="2026-09-12",
            start="2026-09-12T00:20:00-04:00",
            end="2026-09-12T08:50:00-04:00",
            title="Sleep",
            tagged=False,
        )
        patches, created, updated, deleted = _gcal_ok(
            tagged=[], nearby=[], foreign=[pulse]
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_sleep_block_from_battery(
                _battery("2026-09-12T04:51:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["start"]["dateTime"], "2026-09-12T04:51:00-04:00")
        self.assertEqual(created[0]["end"]["dateTime"], "2026-09-12T13:51:00-04:00")
        self.assertNotIn("pulse-sleep", deleted)
        self.assertEqual(result["flagged_count"], 1)
        self.assertEqual(result["flagged"][0]["reason"], "foreign_sleep")

    def test_unlocked_winddown_is_retired(self):
        wd = _wd(
            eid="wd-old",
            day="2026-09-12",
            start="2026-09-12T04:00:00-04:00",
        )
        patches, created, updated, deleted = _gcal_ok(
            tagged=[], nearby=[], winddowns=[wd]
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_sleep_block_from_battery(
                _battery("2026-09-12T04:00:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(created), 1)
        self.assertEqual(deleted, ["wd-old"])
        self.assertEqual(result["winddown_retired"], 1)

    def test_locked_winddown_is_kept(self):
        wd = _wd(
            eid="wd-locked",
            day="2026-09-12",
            start="2026-09-12T02:00:00-04:00",
            planned="2026-09-12T04:00:00-04:00",
        )
        patches, created, updated, deleted = _gcal_ok(
            tagged=[], nearby=[], winddowns=[wd]
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_sleep_block_from_battery(
                _battery("2026-09-12T04:00:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(created), 1)
        self.assertEqual(deleted, [])
        self.assertGreaterEqual(result["locked"], 1)
        self.assertEqual(result["winddown_retired"], 0)

    def test_stale_unlocked_other_night_is_deleted(self):
        stale = _ev(
            eid="ev-stale",
            day="2026-09-11",
            start="2026-09-11T23:00:00-04:00",
        )
        patches, created, updated, deleted = _gcal_ok(tagged=[], nearby=[stale])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_sleep_block_from_battery(
                _battery("2026-09-12T04:00:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(created), 1)
        self.assertEqual(deleted, ["ev-stale"])

    def test_stale_locked_other_night_is_kept(self):
        stale = _ev(
            eid="ev-stale-locked",
            day="2026-09-11",
            start="2026-09-11T21:00:00-04:00",
            planned="2026-09-11T23:00:00-04:00",
        )
        patches, created, updated, deleted = _gcal_ok(tagged=[], nearby=[stale])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = sync_sleep_block_from_battery(
                _battery("2026-09-12T04:00:00-04:00")
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(created), 1)
        self.assertEqual(deleted, [])
        self.assertGreaterEqual(result["locked"], 1)


class MonitorReconcile(unittest.TestCase):
    def test_monitor_creates_when_night_has_none(self):
        patches, created, updated, deleted = _gcal_ok(tagged=[], nearby=[])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = reconcile_sleep_block(_battery("2026-09-12T04:00:00-04:00"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["role"], "monitor")
        self.assertEqual(len(created), 1)

    def test_monitor_does_not_move_locked(self):
        existing = [
            _ev(
                eid="ev-locked",
                day="2026-09-12",
                start="2026-09-12T02:00:00-04:00",
                planned="2026-09-12T04:00:00-04:00",
            )
        ]
        patches, created, updated, deleted = _gcal_ok(tagged=existing, nearby=existing)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = reconcile_sleep_block(_battery("2026-09-12T05:00:00-04:00"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["locked"], 1)
        self.assertEqual(created, [])
        self.assertEqual(updated, [])


class EnsureWiresSleepBlock(unittest.TestCase):
    def test_ensure_syncs_sleep_block_beside_gym_not_winddown(self):
        board = {
            "date": "2026-09-12",
            "actions": [],
            "workout": {
                "is_rest_day": False,
                "session_type": "push",
                "exercises": [{"name": "Bench"}],
            },
            "meal": {"meals": [], "items": []},
            "purchases": [],
        }
        bat = _battery("2026-09-12T04:00:00-04:00")
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
                "rt_dashboard.sleep_block_calendar.sync_sleep_block_from_battery",
                return_value={"ok": True, "created": 1, "upserted": 1},
            ) as sleep, mock.patch(
                "rt_dashboard.winddown_calendar.sync_winddown_from_battery",
            ) as wind:
                result = ensure_daily_tasks(
                    board, day="2026-09-12", sleep_battery=bat
                )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["sleep_block_calendar"]["created"], 1)
        self.assertNotIn("winddown_calendar", result)
        sleep.assert_called_once()
        self.assertEqual(sleep.call_args[0][0]["empty_at"], bat["empty_at"])
        wind.assert_not_called()


if __name__ == "__main__":
    unittest.main()
