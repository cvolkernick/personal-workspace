"""Unit tests for daily plan task planning (no live Google Tasks calls)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.daily_plan_tasks import (
    REMAINING_PROTEIN_SLUG,
    _delete_order,
    cache_key,
    collect_fitdash_quest_ids,
    collect_meal_plan_task_ids,
    collect_remaining_protein_tasks,
    ensure_daily_tasks,
    is_meal_plan_owned_task,
    is_remaining_protein_task,
    looks_like_remaining_protein_title,
    meal_quest_notes,
    plan_from_today_board,
    plan_preview,
    purge_meal_plan_tasks,
    purge_stale_quest_tasks,
    quest_mark_day,
    quest_notes,
    upsert_remaining_protein_tasks,
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

    def test_refresh_resync_recreates_meal_tasks_same_fp(self):
        """Refresh / remaining-day title shift: same foods fp, meal GT ids rotate.

        Assay #326: never triggered=false + empty purged while ids change.
        """
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
        self.assertTrue(regen.get("triggered"), regen)
        self.assertEqual(regen.get("reason"), "refresh_resync")
        self.assertEqual(regen.get("fingerprint"), fp)
        self.assertEqual(regen.get("prior_fingerprint"), fp)
        self.assertIn("old-meal", regen.get("purged") or [])
        self.assertNotIn("old-meal", store)
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
        self.assertTrue(after_meal)
        self.assertTrue(
            all("Later meal" in (t.get("title") or "") for t in after_meal)
        )
        self.assertTrue(
            all("[fitdash-meal:2026-08-24]" in (t.get("notes") or "") for t in after_meal)
        )
        self.assertTrue(
            all(f"[fitdash-foods:{fp}]" in (t.get("notes") or "") for t in after_meal)
        )
        self.assertTrue(regen.get("created"))
        self.assertEqual(before_ids, {"old-meal"})
        self.assertTrue(set(regen["created"]).isdisjoint(before_ids))
        self.assertFalse(regen.get("silent"))

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

    def test_remaining_protein_slug_stable_without_action_id(self):
        board = {
            "date": "2026-08-24",
            "actions": [
                {
                    "kind": "nutrition",
                    "text": "Cover remaining protein (~189 g) from the meal plan "
                    "or a high-protein stocked staple.",
                },
                {
                    "kind": "nutrition",
                    "text": "Calorie pace is ahead of the waking window — slow intake.",
                    "id": "calorie-pace",
                },
            ],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {"meals": [], "items": []},
            "purchases": [],
        }
        groups = plan_from_today_board(board, day="2026-08-24")
        nut = next(g for g in groups if g.group == "nutrition")
        protein = next(it for it in nut.items if looks_like_remaining_protein_title(it.title))
        self.assertEqual(protein.slug, REMAINING_PROTEIN_SLUG)
        self.assertFalse(is_meal_plan_owned_task(
            {"title": protein.title, "notes": quest_notes("", "2026-08-24")},
            day="2026-08-24",
        ))

    def test_remaining_protein_identity_ignores_grams(self):
        day = "2026-08-24"
        a = {
            "id": "p189",
            "title": "Cover remaining protein (~189 g) from the meal plan or a h",
            "notes": "[fitdash-quest:2026-08-24]",
            "status": "needsAction",
            "due": "2026-08-24T00:00:00.000Z",
        }
        b = {
            "id": "p210",
            "title": "Cover remaining protein (~210 g) from the meal plan or a h",
            "notes": "[fitdash-quest:2026-08-24]",
            "status": "needsAction",
            "due": "2026-08-24T00:00:00.000Z",
        }
        jot = {
            "id": "jot",
            "title": "Call FGUA to check if auto pay update needs to be delivere",
            "notes": "",
            "status": "needsAction",
            "due": "2026-08-24T00:00:00.000Z",
        }
        self.assertTrue(looks_like_remaining_protein_title(a["title"]))
        self.assertTrue(is_remaining_protein_task(a, day=day))
        self.assertTrue(is_remaining_protein_task(b, day=day))
        self.assertFalse(is_remaining_protein_task(jot, day=day))
        ids = {t["id"] for t in collect_remaining_protein_tasks([a, b, jot], day=day)}
        self.assertEqual(ids, {"p189", "p210"})

    def test_upsert_collapses_gram_variants_and_duplicates(self):
        """MON leak: one ~189 g leftover + many identical ~210 g copies → one."""
        tasks = [
            {
                "id": "p189",
                "title": "Cover remaining protein (~189 g) from the meal plan",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
            },
            {
                "id": "done-old",
                "title": "Cover remaining protein (~40 g) from the meal plan",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "completed",
            },
            {
                "id": "jot",
                "title": "Text the vet",
                "notes": "",
                "status": "needsAction",
            },
        ]
        for i in range(11):
            tasks.append(
                {
                    "id": f"p210-{i}",
                    "title": "Cover remaining protein (~210 g) from the meal plan",
                    "notes": "[fitdash-quest:2026-08-24]",
                    "status": "needsAction",
                }
            )
        deleted: list[str] = []
        updated: list[tuple[str, str]] = []

        def fake_update(list_id, task_id, **kwargs):
            updated.append((task_id, kwargs.get("title") or ""))
            return {"ok": True, "task": {"id": task_id, "title": kwargs.get("title")}}

        def fake_delete(list_id, task_id):
            deleted.append(task_id)
            return {"ok": True}

        with mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.update_task", side_effect=fake_update
        ), mock.patch(
            "rt_dashboard.daily_plan_tasks.gtb.delete_task", side_effect=fake_delete
        ):
            stats = upsert_remaining_protein_tasks(
                list_id="L1",
                day="2026-08-24",
                title="Cover remaining protein (~210 g) from the meal plan "
                "or a high-protein stocked staple.",
                notes=quest_notes("", "2026-08-24"),
                listed_tasks=tasks,
            )

        self.assertTrue(stats["ok"])
        self.assertTrue(stats["keeper_id"])
        self.assertTrue(stats["updated"])
        self.assertEqual(len(stats["purged"]), 11)
        self.assertNotIn("done-old", stats["purged"])
        self.assertNotIn("jot", deleted)
        self.assertNotIn("done-old", deleted)
        self.assertEqual(len(deleted), 11)
        incompletes = [
            t
            for t in tasks
            if is_remaining_protein_task(t, day="2026-08-24")
            and t.get("status") != "completed"
            and t["id"] not in set(stats["purged"])
        ]
        self.assertEqual(len(incompletes), 1)

    def test_ensure_protein_seed_is_idempotent_and_updates_grams(self):
        """Double ensure + gram change: still exactly one incomplete protein quest."""
        store = {
            "nut-h": {
                "id": "nut-h",
                "title": "Nutrition",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "p189": {
                "id": "p189",
                "title": "Cover remaining protein (~189 g) from the meal plan "
                "or a high-protein stocked staple.",
                "notes": "[fitdash-quest:2026-08-24]",
                "parent": "nut-h",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "lift": {
                "id": "lift",
                "title": "Complete today's PUSH session (3 lifts as prescribed).",
                "notes": "[fitdash-quest:2026-08-24]",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
            "jot": {
                "id": "jot",
                "title": "Call FGUA to check if auto pay update needs to be delivere",
                "notes": "",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            },
        }
        for i in range(5):
            store[f"p210-{i}"] = {
                "id": f"p210-{i}",
                "title": "Cover remaining protein (~210 g) from the meal plan "
                "or a high-protein stocked staple.",
                "notes": "[fitdash-quest:2026-08-24]",
                "parent": "nut-h",
                "status": "needsAction",
                "due": "2026-08-24T00:00:00.000Z",
            }
        created: list[str] = []

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
            created.append(title)
            store[tid] = task
            return {"ok": True, "task": task}

        def fake_get(list_id, task_id):
            task = store.get(task_id)
            if not task:
                return {"ok": False, "error": "missing"}
            return {"ok": True, "task": task}

        def fake_update(list_id, task_id, **kwargs):
            task = store.get(task_id)
            if not task:
                return {"ok": False, "error": "missing"}
            if kwargs.get("title") is not None:
                task["title"] = kwargs["title"]
            if kwargs.get("notes") is not None:
                task["notes"] = kwargs["notes"]
            return {"ok": True, "task": task}

        board_210 = {
            "date": "2026-08-24",
            "actions": [
                {
                    "kind": "training",
                    "text": "Complete today's PUSH session (3 lifts as prescribed).",
                    "id": "train-session",
                },
                {
                    "kind": "nutrition",
                    "text": "Cover remaining protein (~210 g) from the meal plan "
                    "or a high-protein stocked staple.",
                    "id": "remaining-protein",
                },
            ],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {"meals": [], "items": []},
            "nutrition": {"food_log_count": 0, "food_logs_fp": ""},
            "purchases": [],
        }

        def _run():
            return ensure_daily_tasks(board_210, day="2026-08-24")

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
                "rt_dashboard.daily_plan_tasks._load_cache", return_value={}
            ):
                first = _run()
                second = _run()

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        protein_creates = [
            t for t in created if looks_like_remaining_protein_title(t)
        ]
        self.assertEqual(protein_creates, [])
        incompletes = collect_remaining_protein_tasks(
            list(store.values()), day="2026-08-24", incomplete_only=True
        )
        self.assertEqual(len(incompletes), 1)
        self.assertIn("~210 g", incompletes[0]["title"])
        self.assertIn("jot", store)
        self.assertIn("lift", store)
        self.assertEqual(len(first.get("protein_upsert", {}).get("purged") or []), 5)
        self.assertEqual(second.get("protein_upsert", {}).get("purged") or [], [])
        self.assertFalse(is_meal_plan_owned_task(incompletes[0], day="2026-08-24"))


if __name__ == "__main__":
    unittest.main()

