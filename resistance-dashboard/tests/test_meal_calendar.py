"""Meal eat_at Calendar reminders. Tasks stay the checklist. No invented times."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from api.auth.session_util import (
    CALENDAR_EVENTS_SCOPE,
    LOGIN_SCOPES,
    TASKS_SCOPE,
    compact_session_scope,
    session_has_calendar_scope,
)
from rt_dashboard.auth_login import LOGIN_SCOPES as PI_LOGIN_SCOPES
from rt_dashboard.daily_plan_tasks import (
    PlannedGroup,
    PlannedItem,
    _meal_slots_from_plan,
    complete_leaf,
    ensure_daily_tasks,
    plan_from_today_board,
)
from rt_dashboard.gcal_session import (
    MISSING_CALENDAR_SCOPE,
    classify_calendar_http_error,
    credentials_status as gcal_status,
)
from rt_dashboard.gtasks_session import bound_session_google
from rt_dashboard.meal_calendar import (
    MealSlotReminder,
    cancel_reminder_for_task,
    event_body,
    event_end_iso,
    event_links_task,
    should_create_missing,
    sync_meal_reminders,
)


def _board_with_meals(*, eat_at=True):
    meals = [
        {
            "label": "Next meal",
            "items": [
                {"name": "Chicken", "serving_label": "170g"},
                {"name": "Rice", "serving_label": "195g"},
            ],
        },
        {
            "label": "Later meal",
            "items": [{"name": "Yogurt", "serving_label": "200g"}],
        },
    ]
    if eat_at:
        meals[0]["eat_at"] = "2026-08-23T15:30:00-04:00"
        meals[0]["eat_at_label"] = "3:30 PM"
        meals[1]["eat_at"] = "2026-08-23T19:00:00-04:00"
        meals[1]["eat_at_label"] = "7:00 PM"
    return {
        "date": "2026-08-23",
        "actions": [],
        "workout": {"is_rest_day": True, "exercises": []},
        "meal": {"meals": meals, "items": []},
        "purchases": [],
    }


def _slot(**kwargs):
    data = {
        "day": "2026-08-23",
        "slot": "meal-0",
        "title": "Next meal · 3:30 PM: Chicken · 170g",
        "eat_at": "2026-08-23T15:30:00-04:00",
        "task_ids": ["gt-1", "gt-2"],
        "all_completed": False,
        "next_eat_at": "2026-08-23T19:00:00-04:00",
    }
    data.update(kwargs)
    return MealSlotReminder(**data)


class LoginCalendarScope(unittest.TestCase):
    def test_login_requests_calendar_events_same_oauth(self):
        self.assertIn(CALENDAR_EVENTS_SCOPE, LOGIN_SCOPES)
        self.assertIn(CALENDAR_EVENTS_SCOPE, PI_LOGIN_SCOPES)
        self.assertIn(TASKS_SCOPE, LOGIN_SCOPES)
        self.assertEqual(list(LOGIN_SCOPES), list(PI_LOGIN_SCOPES))

    def test_compact_keeps_tasks_and_calendar(self):
        compact = compact_session_scope(" ".join(LOGIN_SCOPES))
        self.assertIn(TASKS_SCOPE, compact.split())
        self.assertIn(CALENDAR_EVENTS_SCOPE, compact.split())
        self.assertTrue(session_has_calendar_scope({"scope": compact}))
        self.assertFalse(session_has_calendar_scope({"scope": TASKS_SCOPE}))
        self.assertFalse(session_has_calendar_scope({"scope": ""}))


class PlanCapturesEatAt(unittest.TestCase):
    def test_bucket_eat_at_lands_on_items_same_slot(self):
        groups = plan_from_today_board(_board_with_meals(), day="2026-08-23")
        nutrition = next(g for g in groups if g.group == "nutrition")
        self.assertEqual(len(nutrition.items), 3)
        first = [i for i in nutrition.items if i.meal_slot == "meal-0"]
        later = [i for i in nutrition.items if i.meal_slot == "meal-1"]
        self.assertEqual(len(first), 2)
        self.assertEqual(len(later), 1)
        self.assertEqual(first[0].eat_at, "2026-08-23T15:30:00-04:00")
        self.assertEqual(later[0].eat_at, "2026-08-23T19:00:00-04:00")

    def test_no_eat_at_means_no_slot_for_calendar(self):
        groups = plan_from_today_board(_board_with_meals(eat_at=False), day="2026-08-23")
        nutrition = next(g for g in groups if g.group == "nutrition")
        self.assertTrue(nutrition.items)
        self.assertTrue(all(not i.eat_at for i in nutrition.items))
        slots = _meal_slots_from_plan(
            groups,
            {"nutrition|meal-0-chicken-0": "t1"},
            {},
            "2026-08-23",
        )
        self.assertEqual(slots, [])

    def test_one_slot_reminder_not_per_food(self):
        planned = [
            PlannedGroup(
                group="nutrition",
                title="Nutrition",
                items=[
                    PlannedItem(
                        group="nutrition",
                        slug="meal-0-chicken-0",
                        title="Next meal · 3:30 PM: Chicken · 170g",
                        eat_at="2026-08-23T15:30:00-04:00",
                        meal_slot="meal-0",
                    ),
                    PlannedItem(
                        group="nutrition",
                        slug="meal-0-rice-1",
                        title="Next meal · 3:30 PM: Rice · 195g",
                        eat_at="2026-08-23T15:30:00-04:00",
                        meal_slot="meal-0",
                    ),
                ],
            )
        ]
        slots = _meal_slots_from_plan(
            planned,
            {
                "nutrition|meal-0-chicken-0": "gt-1",
                "nutrition|meal-0-rice-1": "gt-2",
            },
            {},
            "2026-08-23",
        )
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].slot, "meal-0")
        self.assertEqual(slots[0].task_ids, ["gt-1", "gt-2"])
        self.assertFalse(slots[0].all_completed)
        self.assertIn("Chicken", slots[0].title)


class EventShape(unittest.TestCase):
    def test_body_links_quest_and_uses_eat_at(self):
        body = event_body(_slot())
        self.assertEqual(body["summary"], "Next meal · 3:30 PM: Chicken · 170g")
        self.assertEqual(body["start"]["dateTime"], "2026-08-23T15:30:00-04:00")
        self.assertIn("[fitdash-meal:2026-08-23:meal-0]", body["description"])
        self.assertIn("[fitdash-tasks:gt-1,gt-2]", body["description"])
        self.assertIn("checklist stays in Google Tasks", body["description"])
        private = body["extendedProperties"]["private"]
        self.assertEqual(private["fitdashMeal"], "1")
        self.assertEqual(private["fitdashDay"], "2026-08-23")
        self.assertEqual(private["fitdashSlot"], "meal-0")
        self.assertEqual(private["fitdashTaskId"], "gt-1")
        self.assertEqual(private["fitdashTaskIds"], "gt-1,gt-2")

    def test_short_duration_not_next_slot_hours_away(self):
        start = datetime(2026, 8, 23, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        nxt = datetime(2026, 8, 23, 19, 0, tzinfo=ZoneInfo("America/New_York"))
        end = event_end_iso(start, nxt)
        self.assertTrue(end.endswith("15:50:00-04:00"))

    def test_next_slot_used_when_close(self):
        start = datetime(2026, 8, 23, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        nxt = datetime(2026, 8, 23, 15, 50, tzinfo=ZoneInfo("America/New_York"))
        end = event_end_iso(start, nxt)
        self.assertTrue(end.endswith("15:50:00-04:00"))

    def test_event_body_requires_real_eat_at(self):
        with self.assertRaises(ValueError):
            event_body(_slot(eat_at=""))


class SyncReminders(unittest.TestCase):
    def test_missing_scope_is_honest_skip_no_fake_events(self):
        created = []
        with mock.patch(
            "rt_dashboard.meal_calendar.gcal.credentials_status",
            return_value={
                "ok": False,
                "skipped": True,
                "error": MISSING_CALENDAR_SCOPE,
                "error_code": "missing_calendar_scope",
            },
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.create_event",
            side_effect=lambda *a, **k: created.append(a),
        ):
            result = sync_meal_reminders([_slot()], day="2026-08-23")
        self.assertFalse(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["error"], MISSING_CALENDAR_SCOPE)
        self.assertEqual(created, [])
        self.assertEqual(result["upserted"], 0)

    def test_no_session_skip_does_not_invent(self):
        google = {"refresh_token": "1//rt", "access_token": "ya29", "scope": TASKS_SCOPE}
        with bound_session_google(google):
            status = gcal_status()
        self.assertFalse(status["ok"])
        self.assertEqual(status["error_code"], "missing_calendar_scope")
        self.assertIn("Calendar permission", status["error"])

    def test_upsert_one_event_per_slot_no_duplicate_stack(self):
        created = []
        updated = []
        deleted = []
        existing = [
            {
                "id": "ev-old",
                "extendedProperties": {
                    "private": {
                        "fitdashMeal": "1",
                        "fitdashDay": "2026-08-23",
                        "fitdashSlot": "meal-0",
                    }
                },
            },
            {
                "id": "ev-dup",
                "extendedProperties": {
                    "private": {
                        "fitdashMeal": "1",
                        "fitdashDay": "2026-08-23",
                        "fitdashSlot": "meal-0",
                    }
                },
            },
            {
                "id": "ev-dropped",
                "extendedProperties": {
                    "private": {
                        "fitdashMeal": "1",
                        "fitdashDay": "2026-08-23",
                        "fitdashSlot": "meal-9",
                    }
                },
            },
        ]
        now = datetime(2026, 8, 23, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        later = _slot(
            slot="meal-1",
            title="Later meal · 7:00 PM: Yogurt · 200g",
            eat_at="2026-08-23T19:00:00-04:00",
            task_ids=["gt-3"],
            next_eat_at="",
        )
        with mock.patch(
            "rt_dashboard.meal_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.resolve_calendar_id",
            return_value="cvolkern@gmail.com",
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.list_events",
            return_value=existing,
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.create_event",
            side_effect=lambda cid, body: created.append(body) or {"id": "ev-new"},
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.update_event",
            side_effect=lambda cid, eid, body: updated.append((eid, body)),
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.delete_event",
            side_effect=lambda cid, eid: deleted.append(eid) or {"ok": True},
        ):
            result = sync_meal_reminders([_slot(), later], day="2026-08-23", now=now)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["calendar_id"], "cvolkern@gmail.com")
        self.assertEqual([eid for eid, _ in updated], ["ev-old"])
        self.assertEqual(len(created), 1)
        self.assertIn("Yogurt", created[0]["summary"])
        self.assertIn("ev-dup", deleted)
        self.assertIn("ev-dropped", deleted)
        self.assertNotIn("ev-old", deleted)

    def test_no_eat_at_slot_creates_nothing(self):
        created = []
        with mock.patch(
            "rt_dashboard.meal_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.resolve_calendar_id",
            return_value="primary",
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.list_events",
            return_value=[],
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.create_event",
            side_effect=lambda *a, **k: created.append(1),
        ):
            result = sync_meal_reminders(
                [_slot(eat_at="")],
                day="2026-08-23",
                now=datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(created, [])
        self.assertEqual(result["created"], 0)

    def test_completed_slot_deletes_existing(self):
        deleted = []
        existing = [
            {
                "id": "ev-done",
                "extendedProperties": {
                    "private": {
                        "fitdashMeal": "1",
                        "fitdashDay": "2026-08-23",
                        "fitdashSlot": "meal-0",
                    }
                },
            }
        ]
        with mock.patch(
            "rt_dashboard.meal_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.resolve_calendar_id",
            return_value="primary",
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.list_events",
            return_value=existing,
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.delete_event",
            side_effect=lambda cid, eid: deleted.append(eid) or {"ok": True},
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.create_event",
        ) as create:
            result = sync_meal_reminders(
                [_slot(all_completed=True)],
                day="2026-08-23",
                now=datetime(2026, 8, 23, 12, 0, tzinfo=ZoneInfo("America/New_York")),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(deleted, ["ev-done"])
        create.assert_not_called()

    def test_uncomplete_does_not_resurrect_past_eat_at(self):
        self.assertFalse(
            should_create_missing(
                "2026-08-23T15:30:00-04:00",
                now=datetime(2026, 8, 23, 16, 0, tzinfo=ZoneInfo("America/New_York")),
            )
        )
        self.assertTrue(
            should_create_missing(
                "2026-08-23T15:30:00-04:00",
                now=datetime(2026, 8, 23, 12, 0, tzinfo=ZoneInfo("America/New_York")),
            )
        )
        created = []
        with mock.patch(
            "rt_dashboard.meal_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.resolve_calendar_id",
            return_value="primary",
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.list_events",
            return_value=[],
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.create_event",
            side_effect=lambda *a, **k: created.append(1),
        ):
            sync_meal_reminders(
                [_slot()],
                day="2026-08-23",
                now=datetime(2026, 8, 23, 16, 0, tzinfo=ZoneInfo("America/New_York")),
            )
        self.assertEqual(created, [])


class CancelOnComplete(unittest.TestCase):
    def test_complete_deletes_linked_event(self):
        deleted = []
        events = [
            {
                "id": "ev-1",
                "description": "[fitdash-meal:2026-08-23:meal-0]\n[fitdash-tasks:gt-1,gt-2]",
                "extendedProperties": {
                    "private": {
                        "fitdashMeal": "1",
                        "fitdashDay": "2026-08-23",
                        "fitdashSlot": "meal-0",
                        "fitdashTaskId": "gt-1",
                        "fitdashTaskIds": "gt-1,gt-2",
                    }
                },
            }
        ]
        with mock.patch(
            "rt_dashboard.meal_calendar.gcal.credentials_status",
            return_value={"ok": True},
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.resolve_calendar_id",
            return_value="cvolkern@gmail.com",
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.list_events",
            return_value=events,
        ), mock.patch(
            "rt_dashboard.meal_calendar.gcal.delete_event",
            side_effect=lambda cid, eid: deleted.append(eid) or {"ok": True},
        ):
            result = cancel_reminder_for_task(
                "gt-2",
                day="2026-08-23",
                notes="[fitdash-quest:2026-08-23]",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(deleted, ["ev-1"])
        self.assertTrue(event_links_task(events[0], "gt-2"))

    def test_complete_leaf_cancels_calendar_and_keeps_gt(self):
        with mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.complete_task",
            return_value={
                "ok": True,
                "task": {
                    "id": "gt-1",
                    "notes": "[fitdash-quest:2026-08-23]",
                    "status": "completed",
                },
            },
        ), mock.patch(
            "rt_dashboard.meal_calendar.cancel_reminder_for_task",
            return_value={"ok": True, "deleted": 1},
        ) as cancel:
            result = complete_leaf("L1", "gt-1", completed=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["calendar"]["deleted"], 1)
        cancel.assert_called_once()
        self.assertEqual(cancel.call_args[0][0], "gt-1")

    def test_uncomplete_does_not_call_cancel_or_create(self):
        with mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.complete_task",
            return_value={
                "ok": True,
                "task": {"id": "gt-1", "status": "needsAction"},
            },
        ), mock.patch(
            "rt_dashboard.meal_calendar.cancel_reminder_for_task"
        ) as cancel, mock.patch(
            "rt_dashboard.meal_calendar.sync_meal_reminders"
        ) as sync:
            result = complete_leaf("L1", "gt-1", completed=False)
        self.assertTrue(result["ok"])
        self.assertIsNone(result.get("calendar"))
        cancel.assert_not_called()
        sync.assert_not_called()


class EnsureWiresCalendar(unittest.TestCase):
    def test_ensure_syncs_calendar_beside_gt(self):
        created_gt = []

        def fake_create(list_id, title, notes="", due=None, parent=None):
            tid = f"t{len(created_gt) + 1}"
            created_gt.append(title)
            return {
                "ok": True,
                "task": {"id": tid, "title": title, "status": "needsAction"},
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
                side_effect=fake_create,
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
                return_value={"ok": True, "upserted": 2, "deleted": 0},
            ) as sync:
                result = ensure_daily_tasks(_board_with_meals(), day="2026-08-23")
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(created_gt)
        self.assertEqual(result["calendar"]["upserted"], 2)
        slots = sync.call_args[0][0]
        self.assertEqual({s.slot for s in slots}, {"meal-0", "meal-1"})
        self.assertTrue(all(s.eat_at for s in slots))


class ClassifyCalendarErrors(unittest.TestCase):
    def test_insufficient_scope_is_honest(self):
        err = classify_calendar_http_error(
            403,
            '{"error":{"message":"Request had insufficient authentication scopes."}}',
        )
        self.assertEqual(err, MISSING_CALENDAR_SCOPE)

    def test_api_not_enabled_is_distinct(self):
        err = classify_calendar_http_error(
            403,
            "Google Calendar API has not been used in project 1 before or it is "
            "disabled. Enable it by visiting https://console.developers.google.com/"
            "apis/api/calendar-json.googleapis.com/overview",
        )
        self.assertIn("Calendar API is not enabled", err)
        self.assertNotEqual(err, MISSING_CALENDAR_SCOPE)


if __name__ == "__main__":
    unittest.main()
