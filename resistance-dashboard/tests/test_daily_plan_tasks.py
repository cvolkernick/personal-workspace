"""Unit tests for daily plan task planning (no live Google Tasks calls)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.daily_plan_tasks import (
    _delete_order,
    cache_key,
    ensure_daily_tasks,
    plan_from_today_board,
    plan_preview,
    purge_stale_quest_tasks,
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

    def test_purge_orphan_incomplete_past_due(self):
        deleted: list[str] = []

        def fake_delete(list_id: str, task_id: str):
            deleted.append(task_id)
            return {"ok": True}

        cache: dict = {}
        orphan = {
            "id": "orphan-1",
            "title": "Cover remaining protein",
            "status": "needsAction",
            "due": "2026-08-08T00:00:00.000Z",
        }
        keep_today = {
            "id": "today-1",
            "title": "Train",
            "status": "needsAction",
            "due": "2026-08-09T00:00:00.000Z",
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
                "tasks": [orphan, keep_today, keep_no_due],
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


if __name__ == "__main__":
    unittest.main()

