"""Unit tests for daily plan task planning (no live Google Tasks calls)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.daily_plan_tasks import (
    _delete_order,
    cache_key,
    collect_fitdash_quest_ids,
    ensure_daily_tasks,
    plan_from_today_board,
    plan_preview,
    purge_stale_quest_tasks,
    quest_mark_day,
    quest_notes,
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


if __name__ == "__main__":
    unittest.main()

