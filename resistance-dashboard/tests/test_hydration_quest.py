"""Hydration daily quest: civil-day water vs the Today water target."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.coach import build_today_board
from rt_dashboard.daily_plan_tasks import (
    HYDRATION_CACHE_KEY,
    HYDRATION_SLUG,
    ensure_daily_tasks,
    item_kind_key,
    plan_from_today_board,
)
from rt_dashboard.hydration_bars import hydration_target_ml_from_lbs
from rt_dashboard.hydration_quest import (
    DEFAULT_HYDRATION_GOAL_ML,
    KIND_KEY,
    hydration_spec,
    hydration_title,
)
from rt_dashboard.models import RecoveryStatus
from rt_dashboard.quest_workout_log import apply_quest_to_session, looks_like_lift_quest


HTML = (
    Path(__file__).resolve().parents[1] / "static" / "index.html"
).read_text(encoding="utf-8")


def _board(**extra):
    board = {
        "date": "2026-08-31",
        "recommendation": "train",
        "recovery": {"label": "Ready", "score": 80},
        "workout": {
            "is_rest_day": False,
            "session_type": "push",
            "exercises": [
                {"name": "DB Press", "sets": 3, "reps": 10, "weight_lbs": 50}
            ],
        },
        "meal": {
            "meals": [
                {
                    "label": "Next meal",
                    "items": [{"name": "Chicken", "portion_g": 170}],
                }
            ]
        },
        "purchases": [{"name": "Oats", "action": "restock", "reason": "low"}],
        "actions": [],
    }
    board.update(extra)
    return board


class SpecAndTarget(unittest.TestCase):
    def test_missing_day_is_zero_against_default_target(self):
        spec = hydration_spec({"date": "2026-08-31"})
        self.assertEqual(spec["kind"], KIND_KEY)
        self.assertEqual(spec["water_ml"], 0.0)
        self.assertEqual(spec["target_ml"], DEFAULT_HYDRATION_GOAL_ML)
        self.assertEqual(spec["target_source"], "default")
        self.assertFalse(spec["hit"])
        self.assertEqual(spec["title"], "Hydration — 0 / 2500 ml")

    def test_target_matches_pacing_card_35ml_per_kg(self):
        target = hydration_target_ml_from_lbs(200)
        self.assertEqual(target, 3175)
        spec = hydration_spec(
            {"date": "2026-08-31"},
            hydration=[{"date": "2026-08-31", "water_ml": 1000, "source": "hidrate"}],
            weight=[{"date": "2026-08-30", "weight_lbs": 200}],
        )
        self.assertEqual(spec["target_ml"], 3175)
        self.assertEqual(spec["target_source"], "weight_35ml_kg")
        self.assertEqual(spec["water_ml"], 1000.0)
        self.assertEqual(spec["source"], "hidrate")
        self.assertFalse(spec["hit"])
        self.assertEqual(spec["title"], "Hydration — 1000 / 3175 ml")

    def test_same_date_rows_do_not_sum(self):
        spec = hydration_spec(
            {"date": "2026-08-31"},
            hydration=[
                {"date": "2026-08-31", "water_ml": 1800, "source": "hidrate"},
                {"date": "2026-08-31", "water_ml": 400, "source": "google_health"},
            ],
            weight=[{"date": "2026-08-31", "weight_lbs": 200}],
        )
        self.assertEqual(spec["water_ml"], 1800.0)
        self.assertEqual(spec["source"], "hidrate")

    def test_hit_when_logged_water_meets_target(self):
        spec = hydration_spec(
            {"date": "2026-08-31"},
            hydration=[{"date": "2026-08-31", "water_ml": 3175, "source": "hidrate"}],
            weight=[{"date": "2026-08-31", "weight_lbs": 200}],
        )
        self.assertTrue(spec["hit"])
        self.assertEqual(
            hydration_title(current_ml=3175, target_ml=3175),
            "Hydration — 3175 / 3175 ml",
        )


class PlanSeedsHydration(unittest.TestCase):
    def test_quest_sits_between_nutrition_and_shopping(self):
        groups = plan_from_today_board(
            _board(
                hydration_days=[
                    {"date": "2026-08-31", "water_ml": 500, "source": "hidrate"}
                ],
                body_weight=[{"date": "2026-08-31", "weight_lbs": 200}],
            ),
            day="2026-08-31",
        )
        order = [g.group for g in groups]
        self.assertLess(order.index("nutrition"), order.index("hydration"))
        self.assertLess(order.index("hydration"), order.index("shopping"))
        self.assertLess(order.index("cardio"), order.index("hydration"))
        self.assertLess(order.index("hydration"), order.index("sleep"))
        leaf = next(g for g in groups if g.group == "hydration").items
        self.assertEqual(len(leaf), 1)
        self.assertEqual(leaf[0].slug, HYDRATION_SLUG)
        self.assertEqual(item_kind_key(leaf[0]), HYDRATION_CACHE_KEY)
        self.assertIn("500 / 3175", leaf[0].title)

    def test_empty_board_still_shows_the_quest(self):
        groups = plan_from_today_board({"date": "2026-08-31"}, day="2026-08-31")
        hyd = next(g for g in groups if g.group == "hydration")
        self.assertEqual(hyd.items[0].title, "Hydration — 0 / 2500 ml")

    def test_action_title_does_not_fork_a_second_leaf(self):
        board = _board(
            actions=[
                {
                    "id": "water",
                    "kind": "hydration",
                    "text": "Hydration — 1 / 2500 ml",
                }
            ]
        )
        groups = plan_from_today_board(board, day="2026-08-31")
        hyd = next(g for g in groups if g.group == "hydration")
        self.assertEqual(len(hyd.items), 1)
        self.assertEqual(hyd.items[0].slug, HYDRATION_SLUG)


class CoachEmitsHydration(unittest.TestCase):
    def test_today_board_uses_live_series_and_keeps_one_action(self):
        rec = RecoveryStatus(label="Ready", score=80.0, reasons=["ok"])
        board = build_today_board(
            as_of="2026-08-31",
            recovery=rec,
            workout_plan={"is_rest_day": False, "session_type": "push", "exercises": []},
            meal_plan={},
            consumed={"calories": 0, "protein_g": 0},
            targets={"calories": 2100, "protein_g": 200},
            adherence={},
            hydration=[
                {"date": "2026-08-31", "water_ml": 900, "source": "hidrate"}
            ],
            weight=[{"date": "2026-08-31", "weight_lbs": 200}],
        )
        acts = [a for a in board["actions"] if a.get("kind") == "hydration"]
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["id"], "water")
        self.assertEqual(board["hydration"]["target_ml"], 3175)
        self.assertEqual(board["hydration"]["water_ml"], 900.0)
        self.assertFalse(board["hydration"]["hit"])
        self.assertIn("900 / 3175", board["hydration"]["title"])


class EnsureAutoComplete(unittest.TestCase):
    def _run(self, board, store=None):
        store = store if store is not None else {}
        created: list[dict] = []

        def fake_list(list_id, show_completed=True, show_hidden=True):
            return {"ok": True, "tasks": list(store.values())}

        def fake_create(list_id, title, notes="", due=None, parent=None):
            tid = f"h{len(created) + 1}"
            task = {
                "id": tid,
                "title": title,
                "notes": notes,
                "due": f"{due}T00:00:00.000Z" if due else due,
                "status": "needsAction",
                "parent": parent,
            }
            created.append(task)
            store[tid] = task
            return {"ok": True, "task": task}

        def fake_get(list_id, task_id):
            task = store.get(task_id)
            if not task:
                return {"ok": False}
            return {"ok": True, "task": task}

        def fake_update(list_id, task_id, title=None, notes=None, due=None, status=None, clear_due=False):
            task = store.get(task_id)
            if not task:
                return {"ok": False}
            if title is not None:
                task["title"] = title
            if notes is not None:
                task["notes"] = notes
            if status is not None:
                task["status"] = status
            return {"ok": True, "task": task}

        def fake_complete(list_id, task_id, completed=True):
            task = store.get(task_id)
            if not task:
                return {"ok": False}
            task["status"] = "completed" if completed else "needsAction"
            return {"ok": True, "task": task}

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
                side_effect=fake_list,
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.create_task",
                side_effect=fake_create,
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.get_task",
                side_effect=fake_get,
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.update_task",
                side_effect=fake_update,
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.complete_task",
                side_effect=fake_complete,
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.delete_task",
                return_value={"ok": True},
            ):
                result = ensure_daily_tasks(board, day="2026-08-31")
        return result, store, created

    def test_progress_upserts_and_hit_completes(self):
        low = _board(
            hydration_days=[
                {"date": "2026-08-31", "water_ml": 400, "source": "hidrate"}
            ],
            body_weight=[{"date": "2026-08-31", "weight_lbs": 200}],
        )
        first, store, created = self._run(low)
        hyd = next(g for g in first["groups"] if g["group"] == "hydration")
        leaf = hyd["items"][0]
        self.assertFalse(leaf["completed"])
        self.assertIn("400 / 3175", leaf["title"])
        leaf_id = leaf["task_id"]
        high = _board(
            hydration_days=[
                {"date": "2026-08-31", "water_ml": 3300, "source": "hidrate"}
            ],
            body_weight=[{"date": "2026-08-31", "weight_lbs": 200}],
        )
        second, store, created2 = self._run(high, store=store)
        hyd2 = next(g for g in second["groups"] if g["group"] == "hydration")
        leaf2 = hyd2["items"][0]
        self.assertEqual(leaf2["task_id"], leaf_id)
        self.assertTrue(leaf2["completed"])
        self.assertIn("3300 / 3175", leaf2["title"])
        extra = [
            t
            for t in created2
            if str(t.get("title") or "").startswith("Hydration —")
            and t["id"] != leaf_id
        ]
        self.assertFalse(extra)
        self.assertTrue(created)


class HydrationIsNotALift(unittest.TestCase):
    def test_complete_does_not_write_a_session(self):
        self.assertFalse(
            looks_like_lift_quest(
                group="hydration",
                title="Hydration — 1000 / 3175 ml",
                slug="water",
            )
        )
        session, info = apply_quest_to_session(
            completed=True,
            group="hydration",
            title="Hydration — 3175 / 3175 ml",
            slug="water",
            session_type="push",
            today_workout={
                "session_type": "push",
                "exercises": [
                    {"name": "DB Press", "weight_lbs": 50, "sets": 3, "reps": 10}
                ],
            },
            sessions=[],
            today="2026-08-31",
        )
        self.assertIsNone(session)
        self.assertFalse(info["wrote"])
        self.assertEqual(info["reason"], "not_lift")


class PacingCardStays(unittest.TestCase):
    def test_today_page_keeps_the_wake_window_card(self):
        self.assertIn('id="hydration-pacing-section"', HTML)
        self.assertIn("Hydration pacing · wake window", HTML)


if __name__ == "__main__":
    unittest.main()
