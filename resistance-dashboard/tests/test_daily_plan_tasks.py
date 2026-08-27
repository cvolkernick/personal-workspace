"""Unit tests for daily plan task planning (no live Google Tasks calls)."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from rt_dashboard.daily_plan_tasks import (
    PROTEIN_REMAINING_CACHE_KEY,
    PROTEIN_REMAINING_SLUG,
    PROTECT_BEDTIME_CACHE_KEY,
    PROTECT_BEDTIME_SLUG,
    SLEEP_BATTERY_LOW_CACHE_KEY,
    SLEEP_BATTERY_LOW_SLUG,
    PlannedGroup,
    PlannedItem,
    _delete_order,
    _hydrate_ids_from_listed,
    cache_key,
    collect_fitdash_quest_ids,
    collect_meal_plan_task_ids,
    collect_protect_bedtime_tasks,
    collect_protein_remaining_tasks,
    collect_sleep_battery_low_tasks,
    collect_sleep_quest_tasks,
    ensure_daily_tasks,
    is_meal_plan_owned_task,
    is_protect_bedtime_owned_task,
    is_protein_remaining_owned_task,
    is_sleep_battery_low_owned_task,
    is_sleep_owned_task,
    looks_like_protect_bedtime_title,
    looks_like_protein_remaining_title,
    looks_like_sleep_battery_low_title,
    looks_like_sleep_quest_title,
    meal_quest_notes,
    plan_from_today_board,
    plan_preview,
    purge_meal_plan_tasks,
    purge_stale_quest_tasks,
    quest_mark_day,
    quest_notes,
    sleep_quest_action_slug,
    stable_action_slug,
)


class TestDailyPlanTasks(unittest.TestCase):
    def test_cache_key(self):
        self.assertEqual(cache_key("training", "session"), "training|session")

    def test_plan_preview_fast(self):
        prev = plan_preview(
            {
                "date": "2026-08-08",
                "actions": [{"kind": "training", "text": "Train", "id": "t"}],
                "workout": {"is_rest_day": True, "exercises": []},
                "meal": {"meals": [], "items": []},
                "purchases": [],
            }
        )
        self.assertEqual(prev["source"], "plan_preview")
        self.assertTrue(prev["groups"])

    def test_plan_groups_from_board(self):
        board = {
            "date": "2026-08-08",
            "actions": [
                {
                    "kind": "training",
                    "text": "Complete today's PUSH session",
                    "id": "train-session",
                },
                {
                    "kind": "nutrition",
                    "text": "Cover remaining protein",
                    "id": "protein-gap",
                },
                {
                    "kind": "shopping",
                    "text": "Restock chicken",
                    "id": "shop-chicken",
                },
                {
                    "kind": "sleep",
                    "text": "Protect bedtime",
                    "id": "sleep-bed",
                },
            ],
            "workout": {
                "is_rest_day": False,
                "exercises": [
                    {"name": "DB Press", "sets": 3, "reps": 10, "weight_lbs": 50}
                ],
            },
            "meal": {
                "meals": [
                    {
                        "label": "Next meal",
                        "items": [
                            {"name": "Chicken", "serving_label": "210g"},
                            {"name": "Rice", "serving_label": "195g"},
                        ],
                    },
                    {
                        "label": "Later meal",
                        "items": [{"name": "Yogurt", "serving_label": "200g"}],
                    },
                ],
                "items": [],  # flat list unused when meals present
            },
            "purchases": [{"name": "Greek yogurt", "action": "restock", "reason": "OOS"}],
        }
        groups = plan_from_today_board(board, day="2026-08-08")
        by = {g.group: g for g in groups}
        self.assertIn("training", by)
        self.assertIn("nutrition", by)
        self.assertIn("shopping", by)
        self.assertIn("sleep", by)
        # session + exercise
        self.assertGreaterEqual(len(by["training"].items), 2)
        # protein action + 3 foods across meal buckets
        self.assertGreaterEqual(len(by["nutrition"].items), 4)
        titles = " ".join(i.title for i in by["training"].items)
        self.assertIn("PUSH", titles)
        self.assertIn("DB Press", titles)
        meal_titles = " ".join(i.title for i in by["nutrition"].items)
        self.assertIn("Next meal", meal_titles)
        self.assertIn("Later meal", meal_titles)
        self.assertTrue(any(i.meal_label == "Next meal" for i in by["nutrition"].items))
        protein_items = [
            i for i in by["nutrition"].items if i.slug == PROTEIN_REMAINING_SLUG
        ]
        self.assertEqual(len(protein_items), 1)
        self.assertEqual(protein_items[0].title, "Cover remaining protein")
        bedtime = [i for i in by["sleep"].items if i.slug == PROTECT_BEDTIME_SLUG]
        self.assertEqual(len(bedtime), 1)
        self.assertEqual(bedtime[0].title, "Protect bedtime")

    def test_meal_bucket_clock_lands_on_quest_label(self):
        board = {
            "date": "2026-08-22",
            "actions": [],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {
                "meals": [
                    {
                        "label": "Next meal",
                        "eat_at": "2026-08-22T15:30:00-04:00",
                        "eat_at_label": "3:30 PM",
                        "items": [
                            {"name": "Chicken", "portion_g": 170, "serving_label": "170g"}
                        ],
                    }
                ],
            },
        }
        groups = plan_from_today_board(board, day="2026-08-22")
        nutrition = next(g for g in groups if g.group == "nutrition")
        self.assertTrue(nutrition.items)
        self.assertEqual(nutrition.items[0].meal_label, "Next meal · 3:30 PM")
        self.assertIn("3:30 PM", nutrition.items[0].title)
        self.assertIn("170g", nutrition.items[0].title)
        self.assertEqual(nutrition.items[0].eat_at, "2026-08-22T15:30:00-04:00")
        self.assertEqual(nutrition.items[0].meal_slot, "meal-0")
        self.assertEqual(nutrition.items[0].cal_label, "Next meal")
        self.assertEqual(nutrition.items[0].item_name, "Chicken")
        self.assertEqual(nutrition.items[0].portion_g, 170)

    def test_quest_title_uses_portion_g_as_primary_cue(self):
        board = {
            "date": "2026-08-23",
            "actions": [],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {
                "meals": [
                    {
                        "label": "Next meal",
                        "eat_at_label": "12:00 PM",
                        "items": [
                            {
                                "name": "Chicken",
                                "portion_g": 250,
                                "serving_label": "1 serving",
                            }
                        ],
                    }
                ],
            },
        }
        groups = plan_from_today_board(board, day="2026-08-23")
        nutrition = next(g for g in groups if g.group == "nutrition")
        self.assertTrue(nutrition.items)
        self.assertIn("250g", nutrition.items[0].title)
        self.assertNotIn("1 serving", nutrition.items[0].title)

    def test_rest_day_skips_exercises(self):
        board = {
            "date": "2026-08-08",
            "actions": [{"kind": "training", "text": "Rest day", "id": "rest"}],
            "workout": {
                "is_rest_day": True,
                "exercises": [{"name": "Squat", "sets": 3, "reps": 5}],
            },
            "meal": {"items": []},
            "purchases": [],
        }
        groups = plan_from_today_board(board)
        train = next(g for g in groups if g.group == "training")
        self.assertEqual(len(train.items), 1)
        self.assertIn("Rest", train.items[0].title)

    def test_delete_order_parents_last(self):
        ordered = _delete_order(
            {
                "training|group": "p1",
                "training|ex-squat": "c1",
                "nutrition|group": "p2",
                "nutrition|meal-0-rice-0": "c2",
            }
        )
        ids = [tid for _ck, tid in ordered]
        # children before their parent groups
        self.assertLess(ids.index("c1"), ids.index("p1"))
        self.assertLess(ids.index("c2"), ids.index("p2"))

    def test_quest_marker_roundtrip(self):
        notes = quest_notes("Hit protein", "2026-08-21")
        self.assertIn("Hit protein", notes)
        self.assertEqual(quest_mark_day(notes), "2026-08-21")
        self.assertEqual(quest_mark_day(quest_notes("", "2026-08-21")), "2026-08-21")
        self.assertIsNone(quest_mark_day("just a jot"))
        kinded = quest_notes("", "2026-08-26", kind_key="sleep|protect-bedtime")
        self.assertIn("[fitdash-quest:2026-08-26]", kinded)
        self.assertIn("[fitdash-kind:sleep|protect-bedtime]", kinded)

    def test_meal_food_name_ignores_clock_colon(self):
        from rt_dashboard.daily_plan_tasks import meal_food_name_from_title

        self.assertEqual(
            meal_food_name_from_title("Next meal · 12:00 PM: Chicken · 170g"),
            "Chicken",
        )
        self.assertEqual(
            meal_food_name_from_title("Later meal: Rice · 195g"),
            "Rice",
        )

    def test_collect_ids_marker_and_group_not_jots(self):
        tasks = [
            {"id": "p1", "title": "Training", "due": "2026-08-20T00:00:00.000Z"},
            {
                "id": "c1",
                "title": "DB Press",
                "parent": "p1",
                "due": "2026-08-20T00:00:00.000Z",
            },
            {
                "id": "m1",
                "title": "Cover remaining protein",
                "notes": "[fitdash-quest:2026-08-20]",
                "due": "2026-08-20T00:00:00.000Z",
            },
            {"id": "jot", "title": "Call the dentist", "due": "2026-08-19T00:00:00.000Z"},
        ]
        ids = collect_fitdash_quest_ids(tasks)
        self.assertEqual(ids, {"p1", "c1", "m1"})

    def test_purge_stale_deletes_prior_days_keeps_today(self):
        deleted: list[tuple[str, str]] = []

        def fake_delete(list_id: str, task_id: str):
            deleted.append((list_id, task_id))
            return {"ok": True, "deleted": True, "task_id": task_id}

        cache = {
            "2026-08-08": {
                "list_id": "L1",
                "ids": {
                    "training|group": "old-parent",
                    "training|ex-press": "old-leaf",
                },
            },
            "2026-08-09": {
                "list_id": "L1",
                "ids": {
                    "nutrition|group": "today-parent",
                    "nutrition|meal-0-chicken-0": "today-leaf",
                },
            },
        }
        with mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.delete_task", side_effect=fake_delete
        ), mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.list_tasks",
            return_value={"ok": True, "tasks": []},
        ), mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.get_task",
            return_value={"ok": True, "task": {"status": "needsAction"}},
        ), mock.patch(
            "rt_dashboard.daily_plan_tasks._save_cache"
        ) as save:
            stats = purge_stale_quest_tasks(
                list_id="L1", today="2026-08-09", cache=cache, save=True
            )

        self.assertEqual(stats["days_purged"], ["2026-08-08"])
        self.assertEqual(stats["deleted"], 2)
        self.assertIn(("L1", "old-leaf"), deleted)
        self.assertIn(("L1", "old-parent"), deleted)
        # children deleted before parent
        self.assertLess(
            deleted.index(("L1", "old-leaf")), deleted.index(("L1", "old-parent"))
        )
        self.assertNotIn("2026-08-08", cache)
        self.assertIn("2026-08-09", cache)
        save.assert_called_once()

    def test_purge_skips_completed_cached_yesterday(self):
        deleted: list[str] = []

        def fake_get(list_id, task_id):
            status = "completed" if task_id == "done-leaf" else "needsAction"
            return {"ok": True, "task": {"id": task_id, "status": status}}

        cache = {
            "2026-08-20": {
                "list_id": "L1",
                "ids": {"training|group": "p-old", "training|ex-a": "done-leaf"},
            }
        }
        with mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.delete_task",
            side_effect=lambda lid, tid: deleted.append(tid) or {"ok": True},
        ), mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.list_tasks",
            return_value={"ok": True, "tasks": []},
        ), mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.get_task", side_effect=fake_get
        ), mock.patch("rt_dashboard.daily_plan_tasks._save_cache"):
            purge_stale_quest_tasks(
                list_id="L1", today="2026-08-21", cache=cache, save=True
            )

        self.assertIn("p-old", deleted)
        self.assertNotIn("done-leaf", deleted)

    def test_purge_orphan_incomplete_past_due(self):
        deleted: list[str] = []

        def fake_delete(list_id: str, task_id: str):
            deleted.append(task_id)
            return {"ok": True}

        cache: dict = {}
        orphan = {
            "id": "orphan-1",
            "title": "Cover remaining protein",
            "notes": "[fitdash-quest:2026-08-08]",
            "status": "needsAction",
            "due": "2026-08-08T00:00:00.000Z",
        }
        keep_today = {
            "id": "today-1",
            "title": "Train",
            "notes": "[fitdash-quest:2026-08-09]",
            "status": "needsAction",
            "due": "2026-08-09T00:00:00.000Z",
        }
        keep_jot = {
            "id": "jot-1",
            "title": "Call the dentist",
            "status": "needsAction",
            "due": "2026-08-08T00:00:00.000Z",
        }
        keep_no_due = {
            "id": "manual-1",
            "title": "Custom",
            "status": "needsAction",
            "due": None,
        }
        with mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.delete_task", side_effect=fake_delete
        ), mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.list_tasks",
            return_value={
                "ok": True,
                "tasks": [orphan, keep_today, keep_jot, keep_no_due],
            },
        ), mock.patch("rt_dashboard.daily_plan_tasks._save_cache"):
            stats = purge_stale_quest_tasks(
                list_id="L1", today="2026-08-09", cache=cache, save=True
            )

        self.assertEqual(stats["orphan_deleted"], 1)
        self.assertEqual(deleted, ["orphan-1"])

    def test_ensure_daily_tasks_purges_before_create(self):
        """ensure_daily_tasks must purge prior days then create today's leaves."""
        deleted: list[str] = []
        created: list[str] = []
        cache = {
            "2026-08-08": {
                "list_id": "L1",
                "ids": {"training|group": "stale-p", "training|ex-a": "stale-c"},
            }
        }
        board = {
            "date": "2026-08-09",
            "actions": [
                {
                    "kind": "training",
                    "text": "Complete today's PUSH session",
                    "id": "train-session",
                }
            ],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {"items": []},
            "purchases": [],
        }

        def fake_delete(list_id: str, task_id: str):
            deleted.append(task_id)
            return {"ok": True}

        def fake_create(list_id, title, notes="", due=None, parent=None):
            tid = f"new-{len(created)}"
            created.append(title)
            return {"ok": True, "task": {"id": tid, "title": title, "status": "needsAction"}}

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp)
            with mock.patch.dict(
                "os.environ", {"RESISTANCE_DASHBOARD_CONFIG_DIR": str(cfg)}
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.credentials_status",
                return_value={"ok": True},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.resolve_list_id",
                return_value="L1",
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.delete_task",
                side_effect=fake_delete,
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.list_tasks",
                return_value={"ok": True, "tasks": []},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.create_task",
                side_effect=fake_create,
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._get_task_safe",
                return_value=None,
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._load_cache",
                return_value=cache,
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._save_cache"
            ):
                result = ensure_daily_tasks(board, day="2026-08-09")

        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("day"), "2026-08-09")
        self.assertIn("stale-c", deleted)
        self.assertIn("stale-p", deleted)
        self.assertTrue(created)  # regenerated today's tasks
        self.assertIsNotNone(result.get("purge"))
        self.assertEqual(result["purge"]["days_purged"], ["2026-08-08"])

    def test_unmarked_group_children_are_still_swept(self):
        """User-OAuth leftovers have no notes marker; group parent still IDs them."""
        deleted: list[str] = []
        yesterday_parent = {
            "id": "p-old",
            "title": "Training",
            "notes": "",
            "status": "needsAction",
            "due": "2026-08-20T00:00:00.000Z",
        }
        yesterday_leaf = {
            "id": "c-old",
            "title": "Complete today's PUSH session",
            "notes": "",
            "parent": "p-old",
            "status": "needsAction",
            "due": "2026-08-20T00:00:00.000Z",
        }
        jot = {
            "id": "jot",
            "title": "Buy stamps",
            "status": "needsAction",
            "due": "2026-08-20T00:00:00.000Z",
        }
        with mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.delete_task",
            side_effect=lambda lid, tid: deleted.append(tid) or {"ok": True},
        ), mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.list_tasks",
            return_value={
                "ok": True,
                "tasks": [yesterday_parent, yesterday_leaf, jot],
            },
        ), mock.patch("rt_dashboard.daily_plan_tasks._save_cache"):
            stats = purge_stale_quest_tasks(
                list_id="L1", today="2026-08-21", cache={}, save=True
            )
        self.assertEqual(set(deleted), {"c-old", "p-old"})
        self.assertNotIn("jot", deleted)
        self.assertEqual(stats["orphan_deleted"], 2)

    def test_vercel_empty_cache_rollover_replaces_yesterday_keeps_jot(self):
        """Empty cache (Vercel user-OAuth): yesterday incomplete quests gone,
        today's plan written, completed yesterday left, Chris jot untouched.
        """
        store = {
            "y-parent": {
                "id": "y-parent",
                "title": "Training",
                "notes": "[fitdash-quest:2026-08-20]",
                "status": "needsAction",
                "due": "2026-08-20T00:00:00.000Z",
            },
            "y-leaf": {
                "id": "y-leaf",
                "title": "Complete yesterday PUSH",
                "notes": "[fitdash-quest:2026-08-20]",
                "parent": "y-parent",
                "status": "needsAction",
                "due": "2026-08-20T00:00:00.000Z",
            },
            "y-done": {
                "id": "y-done",
                "title": "DB Press (yesterday)",
                "notes": "[fitdash-quest:2026-08-20]",
                "parent": "y-parent",
                "status": "completed",
                "due": "2026-08-20T00:00:00.000Z",
            },
            "jot": {
                "id": "jot",
                "title": "Call the dentist",
                "notes": "",
                "status": "needsAction",
                "due": "2026-08-19T00:00:00.000Z",
            },
            "turo-not-on-this-list": {
                "id": "turo-1",
                "title": "Message guest",
                "status": "needsAction",
                "due": "2026-08-20T00:00:00.000Z",
            },
        }
        # Turo item lives on another list — Fitness list payload omits it.
        store.pop("turo-not-on-this-list")
        created: list[dict] = []
        complete_calls: list[tuple[str, bool]] = []

        def fake_list(list_id, show_completed=True, show_hidden=True):
            items = list(store.values())
            if not show_completed:
                items = [t for t in items if t.get("status") != "completed"]
            return {"ok": True, "tasks": items}

        def fake_delete(list_id, task_id):
            store.pop(task_id, None)
            return {"ok": True}

        def fake_create(list_id, title, notes="", due=None, parent=None):
            tid = f"today-{len(created) + 1}"
            task = {
                "id": tid,
                "title": title,
                "notes": notes,
                "due": f"{due}T00:00:00.000Z" if due and len(str(due)) == 10 else due,
                "status": "needsAction",
                "parent": parent,
            }
            created.append(task)
            store[tid] = task
            return {"ok": True, "task": task}

        def fake_get(list_id, task_id):
            task = store.get(task_id)
            if not task:
                return {"ok": False, "error": "missing"}
            return {"ok": True, "task": task}

        def fake_complete(list_id, task_id, completed=True):
            complete_calls.append((task_id, completed))
            task = store.get(task_id)
            if task:
                task["status"] = "completed" if completed else "needsAction"
            return {"ok": True, "task": task}

        board = {
            "date": "2026-08-21",
            "actions": [
                {
                    "kind": "training",
                    "text": "Complete today's PULL session",
                    "id": "train-session",
                }
            ],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {"items": []},
            "purchases": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ", {"RESISTANCE_DASHBOARD_CONFIG_DIR": tmp}
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.credentials_status",
                return_value={"ok": True, "source": "session"},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.resolve_list_id",
                return_value="L1",
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.list_tasks", side_effect=fake_list
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.delete_task", side_effect=fake_delete
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.create_task", side_effect=fake_create
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.get_task", side_effect=fake_get
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.complete_task",
                side_effect=fake_complete,
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._load_cache",
                return_value={},
            ):
                result = ensure_daily_tasks(board, day="2026-08-21")

        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("day"), "2026-08-21")
        self.assertNotIn("y-leaf", store)
        self.assertNotIn("y-parent", store)
        self.assertIn("jot", store)
        self.assertEqual(store["jot"]["status"], "needsAction")
        self.assertIn("y-done", store)
        self.assertEqual(store["y-done"]["status"], "completed")
        self.assertNotIn(("y-done", False), complete_calls)
        titles = [t["title"] for t in created]
        self.assertTrue(any("PULL" in t for t in titles), created)
        self.assertTrue(all("[fitdash-quest:2026-08-21]" in (t.get("notes") or "") for t in created))
        today_ids = {t["id"] for t in created}
        remaining_open = [
            t
            for t in store.values()
            if t.get("status") != "completed" and t["id"] != "jot"
        ]
        self.assertTrue(remaining_open)
        self.assertTrue(all(t["id"] in today_ids or t.get("due", "").startswith("2026-08-21") for t in remaining_open))
        self.assertGreaterEqual(result.get("purge", {}).get("orphan_deleted") or 0, 1)

    def test_empty_plan_does_not_invent_quests(self):
        created: list[str] = []
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
                side_effect=lambda *a, **k: created.append(k.get("title") or (a[1] if len(a) > 1 else "")) or {
                    "ok": True,
                    "task": {"id": "x", "title": "nope"},
                },
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._load_cache",
                return_value={},
            ), mock.patch("rt_dashboard.daily_plan_tasks._save_cache"):
                result = ensure_daily_tasks(
                    {
                        "date": "2026-08-21",
                        "actions": [],
                        "workout": {"is_rest_day": True, "exercises": []},
                        "meal": {"meals": [], "items": []},
                        "purchases": [],
                    },
                    day="2026-08-21",
                )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("groups"), [])
        self.assertEqual(result.get("summary"), {"done": 0, "total": 0})
        self.assertEqual(created, [])

    def test_meal_ownership_marker_skips_jots_and_lifts(self):
        day = "2026-08-24"
        meal = {
            "id": "m1",
            "title": "Next meal · 12:00 PM: Chicken · 170g",
            "notes": meal_quest_notes("", day, "abc123def4567890"),
            "due": "2026-08-24T00:00:00.000Z",
        }
        legacy = {
            "id": "m-legacy",
            "title": "Later meal: Rice · 195g",
            "notes": "[fitdash-quest:2026-08-24]",
            "due": "2026-08-24T00:00:00.000Z",
        }
        lift = {
            "id": "t1",
            "title": "DB Press (50 lb 3×10)",
            "notes": "[fitdash-quest:2026-08-24]",
            "due": "2026-08-24T00:00:00.000Z",
        }
        jot = {
            "id": "jot",
            "title": "Call the dentist",
            "notes": "",
            "due": "2026-08-24T00:00:00.000Z",
        }
        protein = {
            "id": "p-act",
            "title": "Cover remaining protein (~40 g) from the meal plan",
            "notes": "[fitdash-quest:2026-08-24]",
            "due": "2026-08-24T00:00:00.000Z",
        }
        self.assertTrue(is_meal_plan_owned_task(meal, day=day))
        self.assertTrue(is_meal_plan_owned_task(legacy, day=day))
        self.assertFalse(is_meal_plan_owned_task(lift, day=day))
        self.assertFalse(is_meal_plan_owned_task(jot, day=day))
        self.assertFalse(is_meal_plan_owned_task(protein, day=day))
        ids = collect_meal_plan_task_ids(
            [meal, legacy, lift, jot, protein], day=day
        )
        self.assertEqual(ids, {"m1", "m-legacy"})

    def test_purge_meal_plan_silent_when_none(self):
        with mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.list_tasks",
            return_value={
                "ok": True,
                "tasks": [
                    {
                        "id": "jot",
                        "title": "Buy stamps",
                        "notes": "",
                        "status": "needsAction",
                    }
                ],
            },
        ), mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.delete_task",
            side_effect=AssertionError("must not delete"),
        ):
            stats = purge_meal_plan_tasks(
                list_id="L1", day="2026-08-24", cache={}, save=False
            )
        self.assertTrue(stats["ok"])
        self.assertTrue(stats["silent"])
        self.assertEqual(stats["purged"], [])

    def test_food_log_regen_purges_old_meal_recreates_keeps_jot(self):
        """Assay hook: one regen cycle — before/after GT ids on meal leaves."""
        prior_fp = "aaaaaaaaaaaaaaaa"
        new_fp = "bbbbbbbbbbbbbbbb"
        store = {
            "nut-h": {
                "id": "nut-h",
                "title": "Nutrition",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "old-meal": {
                "id": "old-meal",
                "title": "Next meal · 12:00 PM: Chicken · 250g",
                "notes": meal_quest_notes("", "2026-08-24", prior_fp),
                "parent": "nut-h",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "lift": {
                "id": "lift",
                "title": "Complete today's PUSH session",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "jot": {
                "id": "jot",
                "title": "Text the vet",
                "notes": "",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
        }
        created: list[dict] = []

        def fake_list(list_id, show_completed=True, show_hidden=True):
            return {"ok": True, "tasks": list(store.values())}

        def fake_delete(list_id, task_id):
            store.pop(task_id, None)
            return {"ok": True}

        def fake_create(list_id, title, notes="", due=None, parent=None):
            tid = f"new-{len(created) + 1}"
            task = {
                "id": tid,
                "title": title,
                "notes": notes,
                "due": f"{due}T00:00:00.000Z" if due and len(str(due)) == 10 else due,
                "status": "needsAction",
                "parent": parent,
            }
            created.append(task)
            store[tid] = task
            return {"ok": True, "task": task}

        def fake_get(list_id, task_id):
            task = store.get(task_id)
            if not task:
                return {"ok": False, "error": "missing"}
            return {"ok": True, "task": task}

        board = {
            "date": "2026-08-24",
            "actions": [
                {
                    "kind": "training",
                    "text": "Complete today's PUSH session",
                    "id": "train-session",
                }
            ],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {
                "meals": [
                    {
                        "label": "Next meal",
                        "eat_at_label": "3:30 PM",
                        "items": [
                            {
                                "name": "Greek yogurt",
                                "portion_g": 200,
                                "serving_label": "200g",
                            }
                        ],
                    }
                ],
                "items": [],
                "food_logs_today": [
                    {
                        "date": "2026-08-24",
                        "name": "Eggs",
                        "calories": 140,
                        "protein_g": 12,
                        "time": "08:10",
                    }
                ],
                "food_logs_fp": new_fp,
            },
            "nutrition": {
                "consumed": {"calories": 140, "protein_g": 12, "food_log_count": 1},
                "food_log_count": 1,
                "food_logs_fp": new_fp,
            },
            "purchases": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ", {"RESISTANCE_DASHBOARD_CONFIG_DIR": tmp}
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.credentials_status",
                return_value={"ok": True, "source": "session"},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.resolve_list_id",
                return_value="L1",
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.list_tasks", side_effect=fake_list
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.delete_task", side_effect=fake_delete
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.create_task", side_effect=fake_create
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.get_task", side_effect=fake_get
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.complete_task",
                return_value={"ok": True},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._load_cache",
                return_value={
                    "2026-08-24": {
                        "list_id": "L1",
                        "ids": {
                            "nutrition|group": "nut-h",
                            "nutrition|meal-0-chicken-0": "old-meal",
                            "training|action-training-0": "lift",
                        },
                    }
                },
            ):
                before_ids = collect_meal_plan_task_ids(
                    list(store.values()), day="2026-08-24"
                )
                result = ensure_daily_tasks(board, day="2026-08-24")

        self.assertTrue(result.get("ok"), result)
        regen = result.get("meal_regen") or {}
        self.assertTrue(regen.get("triggered"), regen)
        self.assertEqual(regen.get("reason"), "food_logs")
        self.assertEqual(regen.get("fingerprint"), new_fp)
        self.assertEqual(regen.get("prior_fingerprint"), prior_fp)
        self.assertIn("old-meal", regen.get("purged") or [])
        self.assertNotIn("old-meal", store)
        self.assertIn("jot", store)
        self.assertIn("lift", store)
        self.assertEqual(store["jot"]["title"], "Text the vet")
        after_meal = [
            t
            for t in store.values()
            if is_meal_plan_owned_task(t, day="2026-08-24")
        ]
        self.assertTrue(after_meal)
        self.assertTrue(all("Greek yogurt" in (t.get("title") or "") for t in after_meal))
        self.assertTrue(
            all(f"[fitdash-meal:2026-08-24]" in (t.get("notes") or "") for t in after_meal)
        )
        self.assertTrue(
            all(f"[fitdash-foods:{new_fp}]" in (t.get("notes") or "") for t in after_meal)
        )
        self.assertTrue(regen.get("created"))
        self.assertEqual(before_ids, {"old-meal"})
        self.assertTrue(set(regen["created"]).isdisjoint(before_ids))

    def test_refresh_meal_title_shift_upserts_same_id(self):
        """Same foods / same fp, slot label + clock change: upsert, no id rotate."""
        fp = "dddddddddddddddd"
        store = {
            "nut-h": {
                "id": "nut-h",
                "title": "Nutrition",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "old-meal": {
                "id": "old-meal",
                "title": "Next meal · 12:00 PM: Chicken · 170g",
                "notes": meal_quest_notes("", "2026-08-24", fp),
                "parent": "nut-h",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "lift": {
                "id": "lift",
                "title": "Complete today's PUSH session",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "shop": {
                "id": "shop",
                "title": "Restock: Greek yogurt",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "sleep": {
                "id": "sleep",
                "title": "Protect bedtime",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "protein": {
                "id": "protein",
                "title": "Cover remaining protein (~40 g) from the meal plan",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "jot": {
                "id": "jot",
                "title": "Text the vet",
                "notes": "",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
        }
        created: list[dict] = []

        def fake_list(list_id, show_completed=True, show_hidden=True):
            return {"ok": True, "tasks": list(store.values())}

        def fake_delete(list_id, task_id):
            store.pop(task_id, None)
            return {"ok": True}

        def fake_create(list_id, title, notes="", due=None, parent=None):
            tid = f"resync-{len(created) + 1}"
            task = {
                "id": tid,
                "title": title,
                "notes": notes,
                "due": f"{due}T00:00:00.000Z" if due and len(str(due)) == 10 else due,
                "status": "needsAction",
                "parent": parent,
            }
            created.append(task)
            store[tid] = task
            return {"ok": True, "task": task}

        def fake_get(list_id, task_id):
            task = store.get(task_id)
            if not task:
                return {"ok": False, "error": "missing"}
            return {"ok": True, "task": task}

        def fake_update(list_id, task_id, title=None, notes=None, due=None, status=None, clear_due=False):
            task = store.get(task_id)
            if not task:
                return {"ok": False, "error": "missing"}
            if title is not None:
                task["title"] = title
            if notes is not None:
                task["notes"] = notes
            if status is not None:
                task["status"] = status
            store[task_id] = task
            return {"ok": True, "task": task}

        board = {
            "date": "2026-08-24",
            "actions": [
                {
                    "kind": "training",
                    "text": "Complete today's PUSH session",
                    "id": "train-session",
                },
                {
                    "kind": "nutrition",
                    "text": "Cover remaining protein (~40 g) from the meal plan",
                    "id": "protein-gap",
                },
                {
                    "kind": "shopping",
                    "text": "Restock: Greek yogurt",
                    "id": "shop-yogurt",
                },
                {
                    "kind": "sleep",
                    "text": "Protect bedtime",
                    "id": "sleep-bed",
                },
            ],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {
                "meals": [
                    {
                        "label": "Later meal",
                        "eat_at_label": "3:30 PM",
                        "items": [
                            {
                                "name": "Chicken",
                                "portion_g": 170,
                                "serving_label": "170g",
                            }
                        ],
                    }
                ],
                "items": [],
                "food_logs_today": [],
                "food_logs_fp": fp,
            },
            "nutrition": {
                "food_log_count": 0,
                "food_logs_fp": fp,
            },
            "purchases": [
                {"name": "Greek yogurt", "action": "restock", "reason": "OOS"}
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ", {"RESISTANCE_DASHBOARD_CONFIG_DIR": tmp}
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.credentials_status",
                return_value={"ok": True, "source": "session"},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.resolve_list_id",
                return_value="L1",
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.list_tasks", side_effect=fake_list
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.delete_task", side_effect=fake_delete
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.create_task", side_effect=fake_create
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.get_task", side_effect=fake_get
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.update_task", side_effect=fake_update
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.complete_task",
                return_value={"ok": True},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._load_cache",
                return_value={},
            ):
                before_ids = collect_meal_plan_task_ids(
                    list(store.values()), day="2026-08-24"
                )
                result = ensure_daily_tasks(board, day="2026-08-24")

        self.assertTrue(result.get("ok"), result)
        regen = result.get("meal_regen") or {}
        self.assertFalse(regen.get("triggered"), regen)
        self.assertEqual(regen.get("purged") or [], [])
        self.assertEqual(regen.get("fingerprint"), fp)
        self.assertEqual(regen.get("prior_fingerprint"), fp)
        self.assertIn("old-meal", store)
        self.assertIn("jot", store)
        self.assertIn("lift", store)
        self.assertIn("shop", store)
        self.assertIn("sleep", store)
        self.assertIn("protein", store)
        self.assertEqual(store["jot"]["title"], "Text the vet")
        self.assertEqual(
            store["protein"]["title"],
            "Cover remaining protein (~40 g) from the meal plan",
        )
        after_meal = [
            t
            for t in store.values()
            if is_meal_plan_owned_task(t, day="2026-08-24")
        ]
        self.assertEqual(len(after_meal), 1)
        self.assertEqual(after_meal[0]["id"], "old-meal")
        self.assertIn("Later meal", after_meal[0]["title"])
        self.assertIn("Chicken", after_meal[0]["title"])
        self.assertTrue(
            all("[fitdash-meal:2026-08-24]" in (t.get("notes") or "") for t in after_meal)
        )
        self.assertTrue(
            all(f"[fitdash-foods:{fp}]" in (t.get("notes") or "") for t in after_meal)
        )
        self.assertEqual(before_ids, {"old-meal"})
        self.assertFalse(
            any(is_meal_plan_owned_task(t, day="2026-08-24") for t in created)
        )

    def test_first_create_meal_tasks_stays_silent(self):
        """No owned meal GT → create is silent (not a Refresh resync)."""
        fp = "eeeeeeeeeeeeeeee"
        store: dict = {}
        created: list[dict] = []

        def fake_create(list_id, title, notes="", due=None, parent=None):
            tid = f"first-{len(created) + 1}"
            task = {
                "id": tid,
                "title": title,
                "notes": notes,
                "due": f"{due}T00:00:00.000Z" if due and len(str(due)) == 10 else due,
                "status": "needsAction",
                "parent": parent,
            }
            created.append(task)
            store[tid] = task
            return {"ok": True, "task": task}

        board = {
            "date": "2026-08-24",
            "actions": [],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {
                "meals": [
                    {
                        "label": "Next meal",
                        "eat_at_label": "12:00 PM",
                        "items": [
                            {
                                "name": "Chicken",
                                "portion_g": 170,
                                "serving_label": "170g",
                            }
                        ],
                    }
                ],
                "food_logs_fp": fp,
            },
            "nutrition": {"food_logs_fp": fp, "food_log_count": 0},
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
                "rt_dashboard.daily_plan_tasks.gtb.delete_task",
                side_effect=AssertionError("must not purge"),
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.create_task", side_effect=fake_create
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.get_task",
                side_effect=lambda lid, tid: {"ok": True, "task": store[tid]}
                if tid in store
                else {"ok": False},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.complete_task",
                return_value={"ok": True},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._load_cache", return_value={}
            ), mock.patch("rt_dashboard.daily_plan_tasks._save_cache"):
                result = ensure_daily_tasks(board, day="2026-08-24")
        self.assertTrue(result.get("ok"), result)
        regen = result.get("meal_regen") or {}
        self.assertFalse(regen.get("triggered"), regen)
        self.assertTrue(regen.get("silent"), regen)
        self.assertEqual(regen.get("purged") or [], [])
        self.assertTrue(regen.get("created"))
        self.assertEqual(regen.get("reason"), None)

    def test_same_food_log_fp_does_not_purge_meal_tasks(self):
        fp = "cccccccccccccccc"
        store = {
            "nut-h": {
                "id": "nut-h",
                "title": "Nutrition",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "keep-meal": {
                "id": "keep-meal",
                "title": "Next meal · 12:00 PM: Chicken · 170g",
                "notes": meal_quest_notes("", "2026-08-24", fp),
                "parent": "nut-h",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
        }
        deleted: list[str] = []
        board = {
            "date": "2026-08-24",
            "actions": [],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {
                "meals": [
                    {
                        "label": "Next meal",
                        "eat_at_label": "12:00 PM",
                        "items": [
                            {
                                "name": "Chicken",
                                "portion_g": 170,
                                "serving_label": "170g",
                            }
                        ],
                    }
                ],
                "food_logs_fp": fp,
            },
            "nutrition": {"food_logs_fp": fp, "food_log_count": 0},
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
                return_value={"ok": True, "tasks": list(store.values())},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.delete_task",
                side_effect=lambda lid, tid: deleted.append(tid) or {"ok": True},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.get_task",
                side_effect=lambda lid, tid: {"ok": True, "task": store[tid]}
                if tid in store
                else {"ok": False},
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.create_task",
                side_effect=AssertionError("must hydrate existing meal task"),
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._load_cache",
                return_value={
                    "2026-08-24": {
                        "list_id": "L1",
                        "ids": {
                            "nutrition|group": "nut-h",
                            "nutrition|meal-0-chicken-0": "keep-meal",
                        },
                    }
                },
            ), mock.patch("rt_dashboard.daily_plan_tasks._save_cache"):
                result = ensure_daily_tasks(board, day="2026-08-24")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(deleted, [])
        regen = result.get("meal_regen") or {}
        self.assertFalse(regen.get("triggered"))
        self.assertTrue(regen.get("silent"))
        nut = next(g for g in result["groups"] if g["group"] == "nutrition")
        self.assertEqual(nut["items"][0]["task_id"], "keep-meal")

    def test_protein_remaining_slug_stable_without_id(self):
        board = {
            "date": "2026-08-24",
            "actions": [
                {
                    "kind": "nutrition",
                    "text": (
                        "Cover remaining protein (~210 g) from the meal plan "
                        "or a high-protein stocked staple."
                    ),
                },
                {
                    "kind": "nutrition",
                    "text": "Cover remaining protein (~180 g) from the meal plan",
                },
            ],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {"meals": [], "items": []},
            "purchases": [],
        }
        groups = plan_from_today_board(board, day="2026-08-24")
        nut = next(g for g in groups if g.group == "nutrition")
        protein = [i for i in nut.items if looks_like_protein_remaining_title(i.title)]
        self.assertEqual(len(protein), 1)
        self.assertEqual(protein[0].slug, PROTEIN_REMAINING_SLUG)
        self.assertIn("~210 g", protein[0].title)

    def test_hydrate_protein_remaining_ignores_gram_in_title(self):
        planned = [
            PlannedGroup(
                group="nutrition",
                title="Nutrition",
                items=[
                    PlannedItem(
                        group="nutrition",
                        slug=PROTEIN_REMAINING_SLUG,
                        title="Cover remaining protein (~180 g) from the meal plan",
                    )
                ],
            )
        ]
        listed = {
            "ok": True,
            "tasks": [
                {
                    "id": "n1",
                    "title": "Nutrition",
                    "due": "2026-08-24T00:00:00.000Z",
                    "notes": "[fitdash-quest:2026-08-24]",
                },
                {
                    "id": "p-old",
                    "title": "Cover remaining protein (~210 g) from the meal plan",
                    "notes": "[fitdash-quest:2026-08-24]",
                    "parent": "n1",
                    "due": "2026-08-24T00:00:00.000Z",
                    "status": "needsAction",
                },
            ],
        }
        ids = _hydrate_ids_from_listed({}, planned, listed, "2026-08-24")
        self.assertEqual(ids[PROTEIN_REMAINING_CACHE_KEY], "p-old")

    def test_protein_remaining_not_meal_owned(self):
        day = "2026-08-24"
        task = {
            "id": "p1",
            "title": "Cover remaining protein (~210 g) from the meal plan",
            "notes": "[fitdash-quest:2026-08-24]",
            "due": "2026-08-24T00:00:00.000Z",
        }
        self.assertTrue(looks_like_protein_remaining_title(task["title"]))
        self.assertTrue(is_protein_remaining_owned_task(task, day=day))
        self.assertFalse(is_meal_plan_owned_task(task, day=day))

    def _protein_board(self, grams: int, extra_actions=None):
        actions = list(extra_actions or [])
        actions.append(
            {
                "kind": "nutrition",
                "text": (
                    f"Cover remaining protein (~{grams} g) from the meal plan "
                    "or a high-protein stocked staple."
                ),
                "id": "protein-remaining",
            }
        )
        return {
            "date": "2026-08-24",
            "actions": actions,
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {"meals": [], "items": []},
            "purchases": [],
        }

    def _patch_ensure(self, store, created, tmp, cache=None):
        def fake_list(list_id, show_completed=True, show_hidden=True):
            return {"ok": True, "tasks": list(store.values())}

        def fake_delete(list_id, task_id):
            store.pop(task_id, None)
            return {"ok": True}

        def fake_create(list_id, title, notes="", due=None, parent=None):
            tid = f"new-{len(created) + 1}"
            task = {
                "id": tid,
                "title": title,
                "notes": notes,
                "due": f"{due}T00:00:00.000Z" if due and len(str(due)) == 10 else due,
                "status": "needsAction",
                "parent": parent,
            }
            created.append(task)
            store[tid] = task
            return {"ok": True, "task": task}

        def fake_get(list_id, task_id):
            task = store.get(task_id)
            if not task:
                return {"ok": False, "error": "missing"}
            return {"ok": True, "task": task}

        def fake_update(list_id, task_id, title=None, notes=None, due=None, status=None, clear_due=False):
            task = store.get(task_id)
            if not task:
                return {"ok": False, "error": "missing"}
            if title is not None:
                task["title"] = title
            if notes is not None:
                task["notes"] = notes
            if status is not None:
                task["status"] = status
            store[task_id] = task
            return {"ok": True, "task": task}

        stack = ExitStack()
        stack.enter_context(
            mock.patch.dict("os.environ", {"RESISTANCE_DASHBOARD_CONFIG_DIR": tmp})
        )
        stack.enter_context(
            mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.credentials_status",
                return_value={"ok": True, "source": "session"},
            )
        )
        stack.enter_context(
            mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.resolve_list_id",
                return_value="L1",
            )
        )
        stack.enter_context(
            mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.list_tasks", side_effect=fake_list
            )
        )
        stack.enter_context(
            mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.delete_task", side_effect=fake_delete
            )
        )
        stack.enter_context(
            mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.create_task", side_effect=fake_create
            )
        )
        stack.enter_context(
            mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.get_task", side_effect=fake_get
            )
        )
        stack.enter_context(
            mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.update_task", side_effect=fake_update
            )
        )
        stack.enter_context(
            mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.complete_task",
                return_value={"ok": True},
            )
        )
        stack.enter_context(
            mock.patch(
                "rt_dashboard.daily_plan_tasks._load_cache",
                return_value=cache if cache is not None else {},
            )
        )
        stack.enter_context(mock.patch("rt_dashboard.daily_plan_tasks._save_cache"))
        return stack

    def test_double_seed_protein_remaining_stays_one(self):
        store: dict = {}
        created: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_ensure(store, created, tmp):
                first = ensure_daily_tasks(self._protein_board(210), day="2026-08-24")
                second = ensure_daily_tasks(self._protein_board(210), day="2026-08-24")
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        incompletes = collect_protein_remaining_tasks(
            list(store.values()), day="2026-08-24", incomplete_only=True
        )
        self.assertEqual(len(incompletes), 1)
        protein_created = [
            t
            for t in created
            if looks_like_protein_remaining_title(t.get("title") or "")
        ]
        self.assertEqual(len(protein_created), 1)
        self.assertIn("~210 g", incompletes[0]["title"])

    def test_protein_remaining_gram_change_upserts_same_id(self):
        store: dict = {}
        created: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_ensure(store, created, tmp):
                first = ensure_daily_tasks(self._protein_board(210), day="2026-08-24")
                first_id = (first.get("protein_remaining") or {}).get("kept")
                second = ensure_daily_tasks(self._protein_board(180), day="2026-08-24")
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        incompletes = collect_protein_remaining_tasks(
            list(store.values()), day="2026-08-24", incomplete_only=True
        )
        self.assertEqual(len(incompletes), 1)
        self.assertEqual(incompletes[0]["id"], first_id)
        self.assertIn("~180 g", incompletes[0]["title"])
        self.assertNotIn("~210 g", incompletes[0]["title"])
        self.assertTrue((second.get("protein_remaining") or {}).get("upserted"))
        protein_created = [
            t
            for t in created
            if looks_like_protein_remaining_title(t.get("title") or "")
        ]
        self.assertEqual(len(protein_created), 1)

    def test_refresh_collapses_protein_remaining_duplicates(self):
        store = {
            "nut-h": {
                "id": "nut-h",
                "title": "Nutrition",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "p-a": {
                "id": "p-a",
                "title": "Cover remaining protein (~210 g) from the meal plan",
                "notes": "[fitdash-quest:2026-08-24]",
                "parent": "nut-h",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "p-b": {
                "id": "p-b",
                "title": "Cover remaining protein (~210 g) from the meal plan",
                "notes": "[fitdash-quest:2026-08-24]",
                "parent": "nut-h",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "p-c": {
                "id": "p-c",
                "title": "Cover remaining protein (~210 g) from the meal plan",
                "notes": "[fitdash-quest:2026-08-24]",
                "parent": "nut-h",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "lift": {
                "id": "lift",
                "title": "Complete today's PUSH session",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "jot": {
                "id": "jot",
                "title": "Text the vet",
                "notes": "",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
        }
        created: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_ensure(store, created, tmp):
                result = ensure_daily_tasks(
                    self._protein_board(
                        210,
                        extra_actions=[
                            {
                                "kind": "training",
                                "text": "Complete today's PUSH session",
                                "id": "train-session",
                            }
                        ],
                    ),
                    day="2026-08-24",
                )
        self.assertTrue(result.get("ok"), result)
        incompletes = collect_protein_remaining_tasks(
            list(store.values()), day="2026-08-24", incomplete_only=True
        )
        self.assertEqual(len(incompletes), 1)
        self.assertIn("lift", store)
        self.assertIn("jot", store)
        self.assertEqual(store["jot"]["title"], "Text the vet")
        purged = (result.get("protein_remaining") or {}).get("purged") or []
        self.assertEqual(len(purged), 2)
        self.assertFalse(
            any(
                looks_like_protein_remaining_title(t.get("title") or "")
                for t in created
            )
        )

    def test_completed_protein_remaining_not_resurrected(self):
        store = {
            "p-done": {
                "id": "p-done",
                "title": "Cover remaining protein (~210 g) from the meal plan",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "completed",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "jot": {
                "id": "jot",
                "title": "Call mom",
                "notes": "",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
        }
        created: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_ensure(store, created, tmp):
                result = ensure_daily_tasks(self._protein_board(180), day="2026-08-24")
        self.assertTrue(result.get("ok"), result)
        self.assertIn("p-done", store)
        self.assertEqual(store["p-done"]["status"], "completed")
        self.assertIn("~210 g", store["p-done"]["title"])
        incompletes = collect_protein_remaining_tasks(
            list(store.values()), day="2026-08-24", incomplete_only=True
        )
        self.assertEqual(incompletes, [])
        self.assertFalse(
            any(
                looks_like_protein_remaining_title(t.get("title") or "")
                for t in created
            )
        )
        self.assertIn("jot", store)

    def test_sleep_slug_stable_without_id(self):
        board = {
            "date": "2026-08-26",
            "actions": [
                {
                    "kind": "sleep",
                    "text": (
                        "Protect bedtime — battery 60.3% after wake 18:28."
                    ),
                },
                {
                    "kind": "sleep",
                    "text": (
                        "Protect bedtime — battery 81.7% after wake 18:28."
                    ),
                },
            ],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {"meals": [], "items": []},
            "purchases": [],
        }
        groups = plan_from_today_board(board, day="2026-08-26")
        sleep = next(g for g in groups if g.group == "sleep")
        bedtime = [
            i for i in sleep.items if looks_like_sleep_quest_title(i.title)
        ]
        self.assertEqual(len(bedtime), 1)
        self.assertEqual(bedtime[0].slug, PROTECT_BEDTIME_SLUG)
        self.assertIn("60.3%", bedtime[0].title)

    def test_protect_bedtime_different_ids_plan_to_one(self):
        board = {
            "date": "2026-08-26",
            "actions": [
                {
                    "kind": "sleep",
                    "id": "act-aaa",
                    "text": (
                        "Protect bedtime — battery 60.3% after wake 18:28."
                    ),
                },
                {
                    "kind": "sleep",
                    "id": "act-bbb",
                    "text": (
                        "Protect bedtime — battery 81.7% after wake 19:05."
                    ),
                },
                {
                    "kind": "sleep",
                    "id": "protect-bedtime",
                    "text": "Sleep battery low (22%) — plan bedtime soon.",
                },
            ],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {"meals": [], "items": []},
            "purchases": [],
        }
        groups = plan_from_today_board(board, day="2026-08-26")
        sleep = next(g for g in groups if g.group == "sleep")
        bedtime = [
            i for i in sleep.items if looks_like_protect_bedtime_title(i.title)
        ]
        low = [
            i for i in sleep.items if looks_like_sleep_battery_low_title(i.title)
        ]
        self.assertEqual(len(bedtime), 1)
        self.assertEqual(bedtime[0].slug, PROTECT_BEDTIME_SLUG)
        self.assertIn("60.3%", bedtime[0].title)
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0].slug, SLEEP_BATTERY_LOW_SLUG)
        self.assertEqual(
            sleep_quest_action_slug(board["actions"][0]), PROTECT_BEDTIME_SLUG
        )
        self.assertEqual(
            sleep_quest_action_slug(board["actions"][1]), PROTECT_BEDTIME_SLUG
        )
        self.assertEqual(
            sleep_quest_action_slug(board["actions"][2]), SLEEP_BATTERY_LOW_SLUG
        )
        self.assertEqual(
            stable_action_slug(board["actions"][0], 0), PROTECT_BEDTIME_SLUG
        )
        self.assertEqual(
            stable_action_slug(board["actions"][2], 2), SLEEP_BATTERY_LOW_SLUG
        )

    def test_hydrate_sleep_ignores_battery_in_title(self):
        planned = [
            PlannedGroup(
                group="sleep",
                title="Sleep & recovery",
                items=[
                    PlannedItem(
                        group="sleep",
                        slug=PROTECT_BEDTIME_SLUG,
                        title="Protect bedtime — battery 81.7% after wake 18:28.",
                    )
                ],
            )
        ]
        listed = {
            "ok": True,
            "tasks": [
                {
                    "id": "sleep-h",
                    "title": "Sleep & recovery",
                    "due": "2026-08-26T00:00:00.000Z",
                    "notes": "[fitdash-quest:2026-08-26]",
                },
                {
                    "id": "bed-old",
                    "title": "Protect bedtime — battery 60.3% after wake 18:28.",
                    "notes": "[fitdash-quest:2026-08-26]",
                    "parent": "sleep-h",
                    "due": "2026-08-26T00:00:00.000Z",
                    "status": "needsAction",
                },
            ],
        }
        ids = _hydrate_ids_from_listed({}, planned, listed, "2026-08-26")
        self.assertEqual(ids[PROTECT_BEDTIME_CACHE_KEY], "bed-old")

    def test_sleep_owned_not_jot(self):
        day = "2026-08-26"
        task = {
            "id": "s1",
            "title": "Protect bedtime — battery 65.5% after wake 18:28.",
            "notes": "[fitdash-quest:2026-08-26]",
            "due": "2026-08-26T00:00:00.000Z",
        }
        jot = {
            "id": "jot",
            "title": "Protect bedtime someday",
            "notes": "",
            "due": "2026-08-26T00:00:00.000Z",
        }
        self.assertTrue(looks_like_sleep_quest_title(task["title"]))
        self.assertTrue(is_sleep_owned_task(task, day=day))
        self.assertTrue(looks_like_sleep_quest_title(jot["title"]))
        self.assertFalse(is_sleep_owned_task(jot, day=day))

    def _sleep_board(self, pct, extra_actions=None, action_id=None, wake="18:28"):
        action = {
            "kind": "sleep",
            "text": (
                f"Protect bedtime — battery {pct}% after wake {wake}."
            ),
        }
        if action_id is not None:
            action["id"] = action_id
        actions = list(extra_actions or [])
        actions.append(action)
        return {
            "date": "2026-08-26",
            "actions": actions,
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {"meals": [], "items": []},
            "purchases": [],
        }

    def test_double_seed_sleep_stays_one(self):
        store: dict = {}
        created: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_ensure(store, created, tmp):
                first = ensure_daily_tasks(self._sleep_board(60.3), day="2026-08-26")
                second = ensure_daily_tasks(self._sleep_board(60.3), day="2026-08-26")
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        incompletes = collect_sleep_quest_tasks(
            list(store.values()), day="2026-08-26", incomplete_only=True
        )
        self.assertEqual(len(incompletes), 1)
        sleep_created = [
            t
            for t in created
            if looks_like_sleep_quest_title(t.get("title") or "")
        ]
        self.assertEqual(len(sleep_created), 1)

    def test_sleep_battery_change_upserts_same_id(self):
        store: dict = {}
        created: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_ensure(store, created, tmp):
                first = ensure_daily_tasks(self._sleep_board(60.3), day="2026-08-26")
                first_sleep = collect_sleep_quest_tasks(
                    list(store.values()), day="2026-08-26", incomplete_only=True
                )
                first_id = first_sleep[0]["id"]
                second = ensure_daily_tasks(self._sleep_board(81.7), day="2026-08-26")
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        incompletes = collect_sleep_quest_tasks(
            list(store.values()), day="2026-08-26", incomplete_only=True
        )
        self.assertEqual(len(incompletes), 1)
        self.assertEqual(incompletes[0]["id"], first_id)
        self.assertIn("81.7%", incompletes[0]["title"])
        self.assertNotIn("60.3%", incompletes[0]["title"])
        sleep_created = [
            t
            for t in created
            if looks_like_sleep_quest_title(t.get("title") or "")
        ]
        self.assertEqual(len(sleep_created), 1)

    def test_refresh_collapses_sleep_duplicates(self):
        store = {
            "sleep-h": {
                "id": "sleep-h",
                "title": "Sleep & recovery",
                "notes": "[fitdash-quest:2026-08-26]",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "s-a": {
                "id": "s-a",
                "title": "Protect bedtime — battery 60.3% after wake 18:28.",
                "notes": "[fitdash-quest:2026-08-26]",
                "parent": "sleep-h",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "s-b": {
                "id": "s-b",
                "title": "Protect bedtime — battery 65.5% after wake 18:28.",
                "notes": "[fitdash-quest:2026-08-26]",
                "parent": "sleep-h",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "s-c": {
                "id": "s-c",
                "title": "Protect bedtime — battery 70.9% after wake 18:28.",
                "notes": "[fitdash-quest:2026-08-26]",
                "parent": "sleep-h",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "lift": {
                "id": "lift",
                "title": "Complete today's PUSH session",
                "notes": "[fitdash-quest:2026-08-26]",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "protein": {
                "id": "protein",
                "title": "Cover remaining protein (~40 g) from the meal plan",
                "notes": "[fitdash-quest:2026-08-26]",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "jot": {
                "id": "jot",
                "title": "Text the vet",
                "notes": "",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
        }
        created: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_ensure(store, created, tmp):
                result = ensure_daily_tasks(
                    self._sleep_board(
                        81.7,
                        extra_actions=[
                            {
                                "kind": "training",
                                "text": "Complete today's PUSH session",
                                "id": "train-session",
                            },
                            {
                                "kind": "nutrition",
                                "text": (
                                    "Cover remaining protein (~40 g) from the "
                                    "meal plan"
                                ),
                                "id": "protein-remaining",
                            },
                        ],
                    ),
                    day="2026-08-26",
                )
        self.assertTrue(result.get("ok"), result)
        incompletes = collect_sleep_quest_tasks(
            list(store.values()), day="2026-08-26", incomplete_only=True
        )
        self.assertEqual(len(incompletes), 1)
        self.assertIn("81.7%", incompletes[0]["title"])
        self.assertIn("lift", store)
        self.assertIn("protein", store)
        self.assertIn("jot", store)
        self.assertEqual(store["jot"]["title"], "Text the vet")
        self.assertEqual(
            store["protein"]["title"],
            "Cover remaining protein (~40 g) from the meal plan",
        )
        self.assertFalse(
            any(
                looks_like_sleep_quest_title(t.get("title") or "")
                for t in created
            )
        )

    def test_completed_sleep_not_resurrected(self):
        store = {
            "s-done": {
                "id": "s-done",
                "title": "Protect bedtime — battery 60.3% after wake 18:28.",
                "notes": "[fitdash-quest:2026-08-26]",
                "status": "completed",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "jot": {
                "id": "jot",
                "title": "Call mom",
                "notes": "",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
        }
        created: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_ensure(store, created, tmp):
                result = ensure_daily_tasks(self._sleep_board(81.7), day="2026-08-26")
        self.assertTrue(result.get("ok"), result)
        self.assertIn("s-done", store)
        self.assertEqual(store["s-done"]["status"], "completed")
        self.assertIn("60.3%", store["s-done"]["title"])
        incompletes = collect_sleep_quest_tasks(
            list(store.values()), day="2026-08-26", incomplete_only=True
        )
        self.assertEqual(incompletes, [])
        self.assertFalse(
            any(
                looks_like_sleep_quest_title(t.get("title") or "")
                for t in created
            )
        )
        self.assertIn("jot", store)

    def test_protect_bedtime_kind_marks_collapse_third_refresh_upserts(self):
        store = {
            "sleep-h": {
                "id": "sleep-h",
                "title": "Sleep & recovery",
                "notes": "[fitdash-quest:2026-08-26]",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "s-a": {
                "id": "s-a",
                "title": "Protect bedtime — battery 60.3% after wake 18:28.",
                "notes": (
                    "[fitdash-quest:2026-08-26]\n"
                    "[fitdash-kind:sleep|act-aaa]"
                ),
                "parent": "sleep-h",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "s-b": {
                "id": "s-b",
                "title": "Protect bedtime — battery 65.5% after wake 19:05.",
                "notes": (
                    "[fitdash-quest:2026-08-26]\n"
                    "[fitdash-kind:sleep|act-bbb]"
                ),
                "parent": "sleep-h",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "low": {
                "id": "low",
                "title": "Sleep battery low (22%) — plan bedtime soon.",
                "notes": (
                    "[fitdash-quest:2026-08-26]\n"
                    "[fitdash-kind:sleep|protect-bedtime]"
                ),
                "parent": "sleep-h",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "jot": {
                "id": "jot",
                "title": "Text the vet",
                "notes": "",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
        }
        created: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_ensure(store, created, tmp):
                first = ensure_daily_tasks(
                    self._sleep_board(81.7, action_id="act-ccc"),
                    day="2026-08-26",
                )
                bedtime = collect_protect_bedtime_tasks(
                    list(store.values()), day="2026-08-26", incomplete_only=True
                )
                self.assertEqual(len(bedtime), 1)
                kept_id = bedtime[0]["id"]
                second = ensure_daily_tasks(
                    self._sleep_board(90.1, action_id="act-ddd", wake="20:10"),
                    day="2026-08-26",
                )
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        bedtime = collect_protect_bedtime_tasks(
            list(store.values()), day="2026-08-26", incomplete_only=True
        )
        self.assertEqual(len(bedtime), 1)
        self.assertEqual(bedtime[0]["id"], kept_id)
        self.assertIn("90.1%", bedtime[0]["title"])
        self.assertIn("20:10", bedtime[0]["title"])
        self.assertNotIn("60.3%", bedtime[0]["title"])
        self.assertNotIn("81.7%", bedtime[0]["title"])
        low = collect_sleep_battery_low_tasks(
            list(store.values()), day="2026-08-26", incomplete_only=True
        )
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0]["id"], "low")
        self.assertTrue(looks_like_sleep_battery_low_title(low[0]["title"]))
        self.assertFalse(is_protect_bedtime_owned_task(low[0], day="2026-08-26"))
        self.assertIn("jot", store)
        self.assertEqual(store["jot"]["title"], "Text the vet")
        parents = [
            t
            for t in store.values()
            if (t.get("title") or "").strip() == "Sleep & recovery"
        ]
        self.assertEqual(len(parents), 1)
        bedtime_created = [
            t
            for t in created
            if looks_like_protect_bedtime_title(t.get("title") or "")
        ]
        self.assertEqual(bedtime_created, [])

    def test_sleep_battery_low_not_deleted_as_bedtime_extra(self):
        store = {
            "sleep-h": {
                "id": "sleep-h",
                "title": "Sleep & recovery",
                "notes": "[fitdash-quest:2026-08-26]",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "s-a": {
                "id": "s-a",
                "title": "Protect bedtime — battery 60.3% after wake 18:28.",
                "notes": (
                    "[fitdash-quest:2026-08-26]\n"
                    "[fitdash-kind:sleep|act-aaa]"
                ),
                "parent": "sleep-h",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "s-b": {
                "id": "s-b",
                "title": "Protect bedtime — battery 70.9% after wake 18:28.",
                "notes": (
                    "[fitdash-quest:2026-08-26]\n"
                    "[fitdash-kind:sleep|act-bbb]"
                ),
                "parent": "sleep-h",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "low": {
                "id": "low",
                "title": "Sleep battery low (18%) — plan bedtime soon.",
                "notes": (
                    "[fitdash-quest:2026-08-26]\n"
                    "[fitdash-kind:sleep|protect-bedtime]"
                ),
                "parent": "sleep-h",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "lift": {
                "id": "lift",
                "title": "Complete today's PUSH session",
                "notes": "[fitdash-quest:2026-08-26]",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "jot": {
                "id": "jot",
                "title": "Call mom",
                "notes": "",
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
        }
        created: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_ensure(store, created, tmp):
                result = ensure_daily_tasks(
                    self._sleep_board(81.7, action_id="act-ccc"),
                    day="2026-08-26",
                )
        self.assertTrue(result.get("ok"), result)
        bedtime = collect_protect_bedtime_tasks(
            list(store.values()), day="2026-08-26", incomplete_only=True
        )
        self.assertEqual(len(bedtime), 1)
        self.assertIn("81.7%", bedtime[0]["title"])
        low = collect_sleep_battery_low_tasks(
            list(store.values()), day="2026-08-26", incomplete_only=True
        )
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0]["id"], "low")
        self.assertIn("18%", low[0]["title"])
        self.assertTrue(is_sleep_battery_low_owned_task(low[0], day="2026-08-26"))
        self.assertIn("lift", store)
        self.assertIn("jot", store)
        self.assertEqual(store["jot"]["title"], "Call mom")

    def test_sleep_battery_low_family_stays_one(self):
        def _low_board(pct, action_id):
            return {
                "date": "2026-08-26",
                "actions": [
                    {
                        "kind": "sleep",
                        "id": action_id,
                        "text": (
                            f"Sleep battery low ({pct}%) — plan bedtime soon "
                            "(empty ~23:10)."
                        ),
                    }
                ],
                "workout": {"is_rest_day": True, "exercises": []},
                "meal": {"meals": [], "items": []},
                "purchases": [],
            }

        store = {
            "low-a": {
                "id": "low-a",
                "title": "Sleep battery low (22%) — plan bedtime soon (empty ~22:00).",
                "notes": (
                    "[fitdash-quest:2026-08-26]\n"
                    "[fitdash-kind:sleep|act-old]"
                ),
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
            "low-b": {
                "id": "low-b",
                "title": "Sleep battery low (18%) — plan bedtime soon (empty ~21:40).",
                "notes": (
                    "[fitdash-quest:2026-08-26]\n"
                    "[fitdash-kind:sleep|protect-bedtime]"
                ),
                "status": "needsAction",
                "due": "2026-08-26T00:00:00.000Z",
            },
        }
        created: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_ensure(store, created, tmp):
                first = ensure_daily_tasks(_low_board(15, "act-new"), day="2026-08-26")
                low = collect_sleep_battery_low_tasks(
                    list(store.values()), day="2026-08-26", incomplete_only=True
                )
                self.assertEqual(len(low), 1)
                kept_id = low[0]["id"]
                second = ensure_daily_tasks(_low_board(12, "act-newer"), day="2026-08-26")
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        low = collect_sleep_battery_low_tasks(
            list(store.values()), day="2026-08-26", incomplete_only=True
        )
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0]["id"], kept_id)
        self.assertIn("12%", low[0]["title"])
        bedtime = collect_protect_bedtime_tasks(
            list(store.values()), day="2026-08-26", incomplete_only=True
        )
        self.assertEqual(bedtime, [])
        self.assertEqual(SLEEP_BATTERY_LOW_CACHE_KEY, "sleep|sleep-battery-low")

    def test_lift_weight_change_upserts_same_id(self):
        def _lift_board(weight):
            return {
                "date": "2026-08-26",
                "actions": [],
                "workout": {
                    "is_rest_day": False,
                    "exercises": [
                        {
                            "name": "DB Press",
                            "sets": 3,
                            "reps": 10,
                            "weight_lbs": weight,
                        }
                    ],
                },
                "meal": {"meals": [], "items": []},
                "purchases": [],
            }

        store: dict = {}
        created: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_ensure(store, created, tmp):
                first = ensure_daily_tasks(_lift_board(50), day="2026-08-26")
                lift_ids = [
                    t["id"]
                    for t in store.values()
                    if "DB Press" in (t.get("title") or "")
                    and t.get("status") != "completed"
                ]
                self.assertEqual(len(lift_ids), 1)
                first_id = lift_ids[0]
                second = ensure_daily_tasks(_lift_board(55), day="2026-08-26")
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        lifts = [
            t
            for t in store.values()
            if "DB Press" in (t.get("title") or "")
            and t.get("status") != "completed"
        ]
        self.assertEqual(len(lifts), 1)
        self.assertEqual(lifts[0]["id"], first_id)
        self.assertIn("55 lb", lifts[0]["title"])
        self.assertNotIn("50 lb", lifts[0]["title"])


if __name__ == "__main__":
    unittest.main()

