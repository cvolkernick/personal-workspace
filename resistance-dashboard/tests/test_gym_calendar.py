"""Coach-scheduled gym Calendar events + monitor reconciliation (#610)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from rt_dashboard.daily_plan_tasks import ensure_daily_tasks
from rt_dashboard.gcal_session import MISSING_CALENDAR_SCOPE
from rt_dashboard.gym_calendar import (
    BUSYNESS_REL,
    DURATION,
    EVENT_TITLE,
    GymDay,
    PROP_DATE,
    PROP_GYM,
    PROP_PLANNED_START,
    clock_label,
    event_body,
    gym_day_from_workout,
    gym_desc_tag,
    gym_quest_label_for_day,
    is_user_locked,
    load_busyness,
    location_for,
    pick_slot,
    ranked_windows_for,
    reconcile_gym_sessions,
    reset_busyness_cache,
    sync_gym_from_workout,
    weekday_key,
)
from rt_dashboard.agent_plan import ensure_today_grok_plan
from rt_dashboard.workout_plan_store import clear_memory_workout_plans

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[2]
RD_ROOT = Path(__file__).resolve().parents[1]


def _ev(*, eid, day, start, end=None, planned=None, title="Gym"):
    start_dt = start if "T" in start else f"{day}T{start}"
    if end is None:
        from datetime import timedelta

        s = datetime.fromisoformat(start_dt)
        end_dt = (s + DURATION).isoformat(timespec="seconds")
    else:
        end_dt = end if "T" in str(end) else f"{day}T{end}"
    planned_iso = planned if planned is not None else start_dt
    return {
        "id": eid,
        "summary": title,
        "description": gym_desc_tag(day),
        "start": {"dateTime": start_dt, "timeZone": "America/New_York"},
        "end": {"dateTime": end_dt, "timeZone": "America/New_York"},
        "extendedProperties": {
            "private": {
                PROP_GYM: "1",
                PROP_DATE: day,
                PROP_PLANNED_START: planned_iso,
            }
        },
    }


def _busy(eid, start, end):
    return {
        "id": eid,
        "summary": "Drive",
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }


class BusynessBundle(unittest.TestCase):
    def setUp(self):
        reset_busyness_cache()

    def tearDown(self):
        reset_busyness_cache()

    def test_json_exists_at_repo_and_vercel_copy(self):
        sot = ROOT / BUSYNESS_REL
        bundled = RD_ROOT / BUSYNESS_REL
        self.assertTrue(sot.is_file(), sot)
        self.assertTrue(bundled.is_file(), bundled)
        self.assertEqual(sot.read_bytes(), bundled.read_bytes())

    def test_captured_occupancy_and_ranked_shortlist(self):
        data = load_busyness()
        self.assertEqual(data.get("captured"), "2026-09-10")
        self.assertEqual(data.get("timezone"), "America/New_York")
        self.assertEqual(data["occupancy"]["tue"]["18"], 100)
        ranked = ranked_windows_for("2026-09-14")  # Monday
        self.assertEqual(ranked[0].start_hhmm, "05:00")
        self.assertEqual(ranked[0].end_hhmm, "06:30")
        wed = ranked_windows_for("2026-09-16")
        self.assertEqual(wed[0].start_hhmm, "22:30")
        thu = ranked_windows_for("2026-09-17")
        self.assertEqual(thu[0].start_hhmm, "22:00")

    def test_weekday_key(self):
        self.assertEqual(weekday_key("2026-09-14"), "mon")
        self.assertEqual(weekday_key("2026-09-19"), "sat")


class QuietPick(unittest.TestCase):
    def setUp(self):
        reset_busyness_cache()

    def test_monday_default_is_5am(self):
        slot = pick_slot("2026-09-14", [])
        self.assertEqual(slot.start.hour, 5)
        self.assertEqual(slot.start.minute, 0)
        self.assertEqual(slot.end - slot.start, DURATION)

    def test_wednesday_default_is_1030pm(self):
        slot = pick_slot("2026-09-16", [])
        self.assertEqual(slot.start.hour, 22)
        self.assertEqual(slot.start.minute, 30)
        self.assertEqual(slot.end.day, 17)
        self.assertEqual(slot.end.hour, 0)

    def test_drive_block_does_not_steal_monday_5am(self):
        drive = (
            datetime(2026, 9, 14, 18, 45, tzinfo=ET),
            datetime(2026, 9, 15, 3, 45, tzinfo=ET),
        )
        slot = pick_slot("2026-09-14", [drive])
        self.assertEqual(slot.start.strftime("%H:%M"), "05:00")

    def test_morning_conflict_falls_to_next_ranked(self):
        morning = (
            datetime(2026, 9, 14, 4, 30, tzinfo=ET),
            datetime(2026, 9, 14, 7, 0, tzinfo=ET),
        )
        slot = pick_slot("2026-09-14", [morning])
        self.assertEqual(slot.start.strftime("%H:%M"), "22:30")

    def test_keep_existing_free_window(self):
        prefer = (
            datetime(2026, 9, 14, 6, 30, tzinfo=ET),
            datetime(2026, 9, 14, 8, 0, tzinfo=ET),
        )
        slot = pick_slot("2026-09-14", [], prefer=prefer)
        self.assertEqual(slot.start.strftime("%H:%M"), "06:30")


class LocationClosure(unittest.TestCase):
    def setUp(self):
        reset_busyness_cache()

    def test_home_club_outside_closure(self):
        start = datetime(2026, 9, 17, 5, 0, tzinfo=ET)
        end = datetime(2026, 9, 17, 6, 30, tzinfo=ET)
        loc, alt = location_for(start, end)
        self.assertIn("3853 Cleveland", loc)
        self.assertFalse(alt)

    def test_alternate_during_install_window(self):
        start = datetime(2026, 9, 18, 5, 0, tzinfo=ET)
        end = datetime(2026, 9, 18, 6, 30, tzinfo=ET)
        loc, alt = location_for(start, end)
        self.assertIn("18911 S Tamiami", loc)
        self.assertTrue(alt)
        body = event_body(
            "2026-09-18",
            pick_slot("2026-09-18", []),
            session_type="pull",
        )
        self.assertIn("18911 S Tamiami", body["location"])
        self.assertIn("15201 N Cleveland Ave", body["description"])
        self.assertIn("equipment install", body["description"])

    def test_sep19_morning_still_closed_afternoon_home(self):
        loc_am, alt_am = location_for(
            datetime(2026, 9, 19, 5, 0, tzinfo=ET),
            datetime(2026, 9, 19, 6, 30, tzinfo=ET),
        )
        loc_pm, alt_pm = location_for(
            datetime(2026, 9, 19, 13, 0, tzinfo=ET),
            datetime(2026, 9, 19, 14, 30, tzinfo=ET),
        )
        self.assertTrue(alt_am)
        self.assertIn("Tamiami", loc_am)
        self.assertFalse(alt_pm)
        self.assertIn("3853 Cleveland", loc_pm)


class EventShape(unittest.TestCase):
    def setUp(self):
        reset_busyness_cache()

    def test_tag_duration_props_and_session_blurb(self):
        slot = pick_slot("2026-09-15", [])  # Tuesday
        body = event_body("2026-09-15", slot, session_type="pull")
        self.assertEqual(body["summary"], EVENT_TITLE)
        self.assertEqual(body["start"]["dateTime"], slot.start.isoformat(timespec="seconds"))
        self.assertIn("[fitdash-gym:2026-09-15]", body["description"])
        self.assertIn("Pull day per FitDash", body["description"])
        self.assertIn("least-busy window", body["description"])
        private = body["extendedProperties"]["private"]
        self.assertEqual(private[PROP_GYM], "1")
        self.assertEqual(private[PROP_DATE], "2026-09-15")
        self.assertEqual(private[PROP_PLANNED_START], body["start"]["dateTime"])
        self.assertEqual(body["reminders"]["overrides"][0]["minutes"], 30)
        start = datetime.fromisoformat(body["start"]["dateTime"])
        end = datetime.fromisoformat(body["end"]["dateTime"])
        self.assertEqual(end - start, DURATION)

    def test_user_locked_when_start_differs_from_planned(self):
        ev = _ev(
            eid="ev-1",
            day="2026-09-14",
            start="2026-09-14T19:00:00-04:00",
            planned="2026-09-14T05:00:00-04:00",
        )
        self.assertTrue(is_user_locked(ev))
        ev2 = _ev(
            eid="ev-2",
            day="2026-09-14",
            start="2026-09-14T05:00:00-04:00",
            planned="2026-09-14T05:00:00-04:00",
        )
        self.assertFalse(is_user_locked(ev2))

    def test_rest_workout_is_rest_day(self):
        gym = gym_day_from_workout(
            {"is_rest_day": True, "session_type": "rest", "exercises": []},
            "2026-09-14",
        )
        self.assertTrue(gym.is_rest)
        train = gym_day_from_workout(
            {"is_rest_day": False, "session_type": "legs", "exercises": [{"name": "Hack"}]},
            "2026-09-14",
        )
        self.assertFalse(train.is_rest)
        self.assertEqual(train.session_type, "legs")
        self.assertIsNone(gym_day_from_workout({}, "2026-09-14"))
        self.assertIsNone(
            gym_day_from_workout(
                {"is_rest_day": False, "session_type": "", "exercises": []},
                "2026-09-14",
            )
        )


class SyncCoach(unittest.TestCase):
    def setUp(self):
        reset_busyness_cache()

    def test_empty_workout_does_not_create(self):
        created = []
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.create_event",
            side_effect=lambda *a, **k: created.append(1),
        ):
            result = sync_gym_from_workout({}, day="2026-09-14")
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["error_code"], "no_workout")
        self.assertEqual(created, [])

    def test_missing_scope_is_honest_skip(self):
        created = []
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={
                "ok": False,
                "skipped": True,
                "error": MISSING_CALENDAR_SCOPE,
                "error_code": "missing_calendar_scope",
            },
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.create_event",
            side_effect=lambda *a, **k: created.append(1),
        ):
            result = sync_gym_from_workout(
                {"is_rest_day": False, "session_type": "push"},
                day="2026-09-14",
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["error"], MISSING_CALENDAR_SCOPE)
        self.assertEqual(created, [])

    def test_creates_one_tagged_event_no_duplicate(self):
        created, updated, deleted = [], [], []
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.resolve_calendar_id",
            return_value="cvolkern@gmail.com",
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.list_events",
            return_value=[],
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.create_event",
            side_effect=lambda cid, body: created.append(body) or {"id": "ev-new"},
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.update_event",
            side_effect=lambda *a, **k: updated.append(1),
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.delete_event",
            side_effect=lambda *a, **k: deleted.append(1),
        ):
            result = sync_gym_from_workout(
                {"is_rest_day": False, "session_type": "push"},
                day="2026-09-14",
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["summary"], "Gym")
        self.assertIn("[fitdash-gym:2026-09-14]", created[0]["description"])
        self.assertEqual(updated, [])
        self.assertEqual(deleted, [])
        self.assertEqual(result["created"], 1)

    def test_rerun_updates_existing_does_not_stack(self):
        created, updated, deleted = [], [], []
        existing = [
            _ev(eid="ev-keep", day="2026-09-14", start="2026-09-14T05:00:00-04:00"),
            _ev(eid="ev-dup", day="2026-09-14", start="2026-09-14T05:00:00-04:00"),
        ]
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.resolve_calendar_id",
            return_value="primary",
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.list_events",
            side_effect=lambda cid, **kw: list(existing)
            if (kw.get("private_props") or {}).get(PROP_GYM) == "1"
            else [],
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.create_event",
            side_effect=lambda *a, **k: created.append(1),
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.update_event",
            side_effect=lambda cid, eid, body: updated.append(eid),
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.delete_event",
            side_effect=lambda cid, eid: deleted.append(eid) or {"ok": True},
        ):
            result = sync_gym_from_workout(
                {"is_rest_day": False, "session_type": "push"},
                day="2026-09-14",
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(created, [])
        self.assertEqual(updated, ["ev-keep"])
        self.assertEqual(deleted, ["ev-dup"])

    def test_user_moved_event_is_locked(self):
        created, updated, deleted = [], [], []
        existing = [
            _ev(
                eid="ev-locked",
                day="2026-09-14",
                start="2026-09-14T19:00:00-04:00",
                planned="2026-09-14T05:00:00-04:00",
            )
        ]
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.resolve_calendar_id",
            return_value="primary",
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.list_events",
            side_effect=lambda cid, **kw: list(existing)
            if (kw.get("private_props") or {}).get(PROP_GYM) == "1"
            else [],
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.create_event",
            side_effect=lambda *a, **k: created.append(1),
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.update_event",
            side_effect=lambda *a, **k: updated.append(1),
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.delete_event",
            side_effect=lambda *a, **k: deleted.append(1),
        ):
            result = sync_gym_from_workout(
                {"is_rest_day": False, "session_type": "push"},
                day="2026-09-14",
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["locked"], 1)
        self.assertEqual(created, [])
        self.assertEqual(updated, [])
        self.assertEqual(deleted, [])

    def test_rest_day_deletes_tagged_event(self):
        deleted = []
        existing = [_ev(eid="ev-rest", day="2026-09-14", start="2026-09-14T05:00:00-04:00")]
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.resolve_calendar_id",
            return_value="primary",
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.list_events",
            side_effect=lambda cid, **kw: list(existing)
            if (kw.get("private_props") or {}).get(PROP_GYM) == "1"
            else [],
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.create_event",
        ) as create, mock.patch(
            "rt_dashboard.gym_calendar.gcal.delete_event",
            side_effect=lambda cid, eid: deleted.append(eid) or {"ok": True},
        ):
            result = sync_gym_from_workout(
                {"is_rest_day": True, "session_type": "rest"},
                day="2026-09-14",
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(deleted, ["ev-rest"])
        create.assert_not_called()

    def test_busy_list_uses_time_bounds(self):
        seen = []

        def list_events(cid, **kwargs):
            seen.append(kwargs)
            return []

        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.resolve_calendar_id",
            return_value="primary",
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.list_events",
            side_effect=list_events,
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.create_event",
            return_value={"id": "n"},
        ):
            sync_gym_from_workout(
                {"is_rest_day": False, "session_type": "pull"},
                day="2026-09-15",
            )
        timed = [kw for kw in seen if kw.get("time_min")]
        self.assertTrue(timed)
        self.assertTrue(timed[0]["time_min"].startswith("2026-09-15T00:00:00"))
        self.assertTrue(timed[0]["time_max"].startswith("2026-09-16T00:00:00"))


class MonitorReconcile(unittest.TestCase):
    def setUp(self):
        reset_busyness_cache()

    def test_monitor_creates_when_training_day_has_none(self):
        created = []
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.resolve_calendar_id",
            return_value="primary",
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.list_events",
            return_value=[],
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.create_event",
            side_effect=lambda cid, body: created.append(body) or {"id": "m1"},
        ):
            result = reconcile_gym_sessions(
                [GymDay(day="2026-09-14", is_rest=False, session_type="push")]
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["role"], "monitor")
        self.assertEqual(len(created), 1)

    def test_monitor_moves_on_overlap_and_reports(self):
        existing = [_ev(eid="ev-1", day="2026-09-14", start="2026-09-14T05:00:00-04:00")]
        busy = [
            _busy(
                "drive-am",
                "2026-09-14T04:30:00-04:00",
                "2026-09-14T07:00:00-04:00",
            )
        ]
        updated = []
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.resolve_calendar_id",
            return_value="primary",
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.list_events",
            side_effect=lambda cid, **kw: list(existing)
            if (kw.get("private_props") or {}).get(PROP_GYM) == "1"
            else list(busy),
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.update_event",
            side_effect=lambda cid, eid, body: updated.append(body),
        ):
            result = reconcile_gym_sessions(
                [GymDay(day="2026-09-14", is_rest=False, session_type="push")]
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["moves"]), 1)
        self.assertEqual(result["moves"][0]["from"], "2026-09-14T05:00:00-04:00")
        self.assertIn("22:30", result["moves"][0]["to"])
        self.assertEqual(updated[0]["start"]["dateTime"], result["moves"][0]["to"])

    def test_monitor_does_not_move_locked(self):
        existing = [
            _ev(
                eid="ev-locked",
                day="2026-09-14",
                start="2026-09-14T19:00:00-04:00",
                planned="2026-09-14T05:00:00-04:00",
            )
        ]
        updated = []
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.resolve_calendar_id",
            return_value="primary",
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.list_events",
            side_effect=lambda cid, **kw: list(existing)
            if (kw.get("private_props") or {}).get(PROP_GYM) == "1"
            else [
                _busy(
                    "x",
                    "2026-09-14T18:00:00-04:00",
                    "2026-09-14T21:00:00-04:00",
                )
            ],
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.update_event",
            side_effect=lambda *a, **k: updated.append(1),
        ):
            result = reconcile_gym_sessions(
                [GymDay(day="2026-09-14", is_rest=False, session_type="push")]
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["locked"], 1)
        self.assertEqual(result["moves"], [])
        self.assertEqual(updated, [])


class EnsureWiresGym(unittest.TestCase):
    def test_ensure_syncs_gym_beside_meals(self):
        board = {
            "date": "2026-09-14",
            "actions": [],
            "workout": {
                "is_rest_day": False,
                "session_type": "push",
                "exercises": [{"name": "Bench"}],
            },
            "meal": {"meals": [], "items": []},
            "purchases": [],
        }
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
                    "task": {"id": "t1", "title": a[1] if len(a) > 1 else "x", "status": "needsAction"},
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
            ) as gym:
                result = ensure_daily_tasks(board, day="2026-09-14")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["gym_calendar"]["created"], 1)
        gym.assert_called_once()
        self.assertEqual(gym.call_args.kwargs.get("day") or gym.call_args[1].get("day"), "2026-09-14")
        workout = gym.call_args[0][0]
        self.assertEqual(workout["session_type"], "push")
        self.assertFalse(workout["is_rest_day"])


class CoachGenerateWiresGym(unittest.TestCase):
    def setUp(self):
        clear_memory_workout_plans()

    def tearDown(self):
        clear_memory_workout_plans()

    def test_rest_skip_syncs_gym_delete(self):
        with mock.patch(
            "rt_dashboard.agent_plan.generate_grok_plans"
        ) as gen, mock.patch(
            "rt_dashboard.gym_calendar.sync_gym_from_workout",
            return_value={"ok": True, "deleted": 1, "upserted": 0},
        ) as gym:
            out = ensure_today_grok_plan(
                "sub-1",
                day="2026-09-14",
                context={
                    "day": "2026-09-14",
                    "catalog": {"exercises": []},
                    "sessions": [],
                    "goals": {"rotation": ["push", "pull", "legs"]},
                    "recovery": {},
                    "stamped": {
                        "session_type": "rest",
                        "is_rest_day": True,
                        "exercises": [],
                        "empty": False,
                    },
                },
            )
        gen.assert_not_called()
        self.assertEqual(out["skipped"], "rest")
        gym.assert_called_once()
        self.assertTrue(gym.call_args[0][0]["is_rest_day"])
        self.assertEqual(out["gym_calendar"]["deleted"], 1)

    def test_generated_plan_syncs_gym_create(self):
        grok = {
            "ok": True,
            "workout": {
                "session_type": "pull",
                "is_rest_day": False,
                "exercises": [{"name": "DB Row", "prescription": {"sets": 2, "reps": 8}}],
                "message": "Generated",
                "source": "grok",
                "empty": False,
            },
            "meal": {"items": [], "empty": True},
            "model": "grok-test",
        }
        ctx = {
            "day": "2026-09-15",
            "catalog": {"exercises": [{"name": "DB Row"}]},
            "sessions": [],
            "goals": {"rotation": ["push", "pull", "legs"]},
            "recovery": {},
            "stamped": {
                "session_type": "pull",
                "is_rest_day": False,
                "exercises": [],
                "next_session_type": "pull",
            },
            "next_session_type": "pull",
        }
        with mock.patch(
            "rt_dashboard.agent_plan.generate_grok_plans",
            return_value=grok,
        ), mock.patch(
            "rt_dashboard.gym_calendar.sync_gym_from_workout",
            return_value={"ok": True, "created": 1, "upserted": 1},
        ) as gym:
            out = ensure_today_grok_plan("sub-1", day="2026-09-15", context=ctx)
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["generated"])
        gym.assert_called_once()
        self.assertEqual(gym.call_args[0][0]["session_type"], "pull")
        self.assertEqual(out["gym_calendar"]["created"], 1)


class VercelBundlesBusyness(unittest.TestCase):
    def test_include_files(self):
        cfg = json.loads((RD_ROOT / "vercel.json").read_text(encoding="utf-8"))
        for key in ("api/dashboard.py", "api/ask.py", "api/ask/plan.py"):
            files = cfg["functions"][key]["includeFiles"]
            self.assertIn("fitness/gym/busyness.json", files, key)


class QuestGymTime(unittest.TestCase):
    """#691: Today's Quests training header uses the tagged gym event start."""

    def test_clock_label_is_et_ampm(self):
        self.assertEqual(
            clock_label(datetime(2026, 9, 14, 5, 0, tzinfo=ET)), "5:00 AM"
        )
        self.assertEqual(
            clock_label(datetime(2026, 9, 14, 22, 30, tzinfo=ET)), "10:30 PM"
        )
        # 09:00Z is 05:00 America/New_York (EDT).
        from datetime import timezone

        self.assertEqual(
            clock_label(datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)),
            "5:00 AM",
        )

    def test_label_empty_without_calendar_session(self):
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={
                "ok": False,
                "skipped": True,
                "error_code": "no_session",
            },
        ):
            self.assertEqual(gym_quest_label_for_day("2026-09-14"), "")

    def test_label_from_tagged_event(self):
        ev = _ev(eid="ev-1", day="2026-09-14", start="2026-09-14T05:00:00-04:00")
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.resolve_calendar_id",
            return_value="cvolkern@gmail.com",
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.list_events",
            return_value=[ev],
        ):
            self.assertEqual(gym_quest_label_for_day("2026-09-14"), "Gym · 5:00 AM")

    def test_label_empty_when_no_event(self):
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.resolve_calendar_id",
            return_value="cvolkern@gmail.com",
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.list_events",
            return_value=[],
        ):
            self.assertEqual(gym_quest_label_for_day("2026-09-14"), "")

    def test_user_moved_event_shows_current_start(self):
        ev = _ev(
            eid="ev-moved",
            day="2026-09-14",
            start="2026-09-14T07:00:00-04:00",
            planned="2026-09-14T05:00:00-04:00",
        )
        with mock.patch(
            "rt_dashboard.gym_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.resolve_calendar_id",
            return_value="cvolkern@gmail.com",
        ), mock.patch(
            "rt_dashboard.gym_calendar.gcal.list_events",
            return_value=[ev],
        ):
            self.assertEqual(gym_quest_label_for_day("2026-09-14"), "Gym · 7:00 AM")

    def test_ensure_stamps_training_quest_meal_label(self):
        board = {
            "date": "2026-09-14",
            "actions": [
                {
                    "kind": "training",
                    "text": "Complete today's PUSH session",
                    "id": "train-session",
                }
            ],
            "workout": {
                "is_rest_day": False,
                "session_type": "push",
                "exercises": [{"name": "Bench"}],
            },
            "meal": {"meals": [], "items": []},
            "purchases": [],
        }
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
                "rt_dashboard.gym_calendar.gym_quest_label_for_day",
                return_value="Gym · 5:00 AM",
            ):
                result = ensure_daily_tasks(board, day="2026-09-14")
        self.assertTrue(result.get("ok"), result)
        training = next(g for g in result["groups"] if g["group"] == "training")
        session = next(
            i for i in training["items"] if i.get("slug") == "train-session"
        )
        self.assertEqual(session["meal_label"], "Gym · 5:00 AM")
        bench = next(
            i for i in training["items"] if str(i.get("slug") or "").startswith("ex-")
        )
        self.assertEqual(bench["meal_label"], "Gym · 5:00 AM")

    def test_renderer_buckets_meal_label_for_any_group(self):
        js = (RD_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        render = js.split("function buildDailyQuestBodyHtml", 1)[1].split(
            "function applyQuestsCollapseDom", 1
        )[0]
        self.assertIn("if (it.meal_label)", render)
        self.assertIn("quest-meal-label", render)
        # Header is not gated on nutrition — training reuses the same bucket.
        after = render.split("if (it.meal_label)", 1)[1][:400]
        self.assertNotIn("nutrition", after.lower())


if __name__ == "__main__":
    unittest.main()
