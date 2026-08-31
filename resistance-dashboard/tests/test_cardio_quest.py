"""Cardio daily quest: AZM target, recovery-aware easy day, no PPL write."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.cardio_quest import (
    DEFAULT_AZM_TARGET,
    KIND_KEY,
    cardio_spec,
    cardio_target_minutes,
    cardio_title,
    is_easy_cardio,
    recent_azm_minutes,
    today_azm_minutes,
)
from rt_dashboard.coach import build_today_board
from rt_dashboard.daily_plan_tasks import (
    CARDIO_AZM_CACHE_KEY,
    CARDIO_AZM_SLUG,
    ensure_daily_tasks,
    item_kind_key,
    plan_from_today_board,
)
from rt_dashboard.models import RecoveryStatus
from rt_dashboard.quest_workout_log import apply_quest_to_session, looks_like_lift_quest


AZM_14D = [
    {"date": "2026-08-16", "total_minutes": 10},
    {"date": "2026-08-17", "total_minutes": 22},
    {"date": "2026-08-18", "total_minutes": 18},
    {"date": "2026-08-19", "total_minutes": 24},
    {"date": "2026-08-20", "total_minutes": 12},
    {"date": "2026-08-21", "total_minutes": 30},
    {"date": "2026-08-22", "total_minutes": 16},
    {"date": "2026-08-23", "total_minutes": 20},
    {"date": "2026-08-24", "total_minutes": 28},
    {"date": "2026-08-25", "total_minutes": 14},
    {"date": "2026-08-26", "total_minutes": 26},
    {"date": "2026-08-27", "total_minutes": 19},
    {"date": "2026-08-28", "total_minutes": 21},
    {"date": "2026-08-29", "total_minutes": 17},
    {"date": "2026-08-30", "total_minutes": 23},
]


def _board(*, rest=False, azm=None, rec="Ready", score=80.0, already=False):
    return {
        "date": "2026-08-31",
        "recommendation": "rest" if rest else ("done" if already else "train"),
        "recovery": {"label": rec, "score": score},
        "workout": {
            "is_rest_day": rest,
            "already_trained_today": already,
            "session_type": "rest" if rest else "push",
            "exercises": []
            if rest or already
            else [{"name": "DB Press", "sets": 3, "reps": 10, "weight_lbs": 50}],
        },
        "meal": {"meals": [], "items": []},
        "purchases": [],
        "actions": [],
        "active_zone_minutes": list(azm or []),
    }


class TargetFromRecentAzm(unittest.TestCase):
    def test_missing_history_uses_default(self):
        self.assertEqual(cardio_target_minutes([], easy=False), DEFAULT_AZM_TARGET)

    def test_median_not_mean_and_outlier_capped(self):
        hiked = recent_azm_minutes(
            AZM_14D + [{"date": "2026-08-18", "total_minutes": 413}],
            as_of="2026-08-31",
        )
        self.assertIn(413.0, hiked)
        target = cardio_target_minutes(hiked, easy=False)
        self.assertLessEqual(target, 45)
        self.assertGreaterEqual(target, 10)
        unhiked = cardio_target_minutes(
            recent_azm_minutes(AZM_14D, as_of="2026-08-31"), easy=False
        )
        self.assertLessEqual(abs(target - unhiked), 2)

    def test_today_excluded_from_median(self):
        days = AZM_14D + [{"date": "2026-08-31", "total_minutes": 400}]
        recent = recent_azm_minutes(days, as_of="2026-08-31")
        self.assertNotIn(400.0, recent)
        self.assertEqual(today_azm_minutes(days, "2026-08-31"), 400.0)

    def test_missing_day_not_zero(self):
        recent = recent_azm_minutes(
            [{"date": "2026-08-30", "total_minutes": 20}],
            as_of="2026-08-31",
        )
        self.assertEqual(recent, [20.0])

    def test_easy_is_half_capped_not_skip(self):
        target = cardio_target_minutes([40, 40, 40], easy=True)
        self.assertEqual(target, 20)
        floor = cardio_target_minutes([10, 10, 10], easy=True)
        self.assertEqual(floor, 10)


class EasyCardio(unittest.TestCase):
    def test_rest_and_needs_rest_are_easy(self):
        self.assertTrue(is_easy_cardio(_board(rest=True)))
        self.assertTrue(
            is_easy_cardio(_board(rec="Needs Rest", score=20.0))
        )
        self.assertTrue(is_easy_cardio(_board(rec="Caution", score=35.0)))

    def test_ready_train_is_standard(self):
        self.assertFalse(is_easy_cardio(_board()))

    def test_deload_volume_scale_is_easy(self):
        board = _board()
        board["workout"]["training_continuity"] = {
            "phase": "return",
            "volume_band_scale": 0.78,
        }
        self.assertTrue(is_easy_cardio(board))


class SpecAndTitle(unittest.TestCase):
    def test_progress_title_and_kind(self):
        spec = cardio_spec(
            _board(azm=AZM_14D + [{"date": "2026-08-31", "total_minutes": 12}])
        )
        self.assertEqual(spec["kind"], KIND_KEY)
        self.assertIn("12 /", spec["title"])
        self.assertTrue(spec["title"].startswith("Cardio"))
        self.assertFalse(spec["hit"])

    def test_rest_title_is_walk_zone2(self):
        spec = cardio_spec(_board(rest=True, azm=AZM_14D))
        self.assertTrue(spec["easy"])
        self.assertTrue(spec["title"].startswith("Walk · Zone 2"))

    def test_hit_when_current_meets_target(self):
        spec = cardio_spec(
            _board(azm=AZM_14D + [{"date": "2026-08-31", "total_minutes": 90}])
        )
        self.assertGreaterEqual(spec["current_minutes"], spec["target_minutes"])
        self.assertTrue(spec["hit"])
        self.assertEqual(
            cardio_title(current=30, target=30, easy=False),
            "Cardio — 30 / 30 AZM",
        )


class PlanSeedsCardioEveryDay(unittest.TestCase):
    def test_board_without_action_still_seeds_cardio(self):
        groups = plan_from_today_board(_board(), day="2026-08-31")
        by = {g.group: g for g in groups}
        self.assertIn("cardio", by)
        leaves = by["cardio"].items
        self.assertEqual(len(leaves), 1)
        self.assertEqual(leaves[0].slug, CARDIO_AZM_SLUG)
        self.assertEqual(item_kind_key(leaves[0]), CARDIO_AZM_CACHE_KEY)
        self.assertIn("AZM", leaves[0].title)

    def test_rest_day_does_not_drop_cardio(self):
        groups = plan_from_today_board(_board(rest=True), day="2026-08-31")
        cardio = next(g for g in groups if g.group == "cardio")
        self.assertTrue(cardio.items[0].title.startswith("Walk · Zone 2"))

    def test_already_trained_does_not_seed_ex_leaves_but_keeps_cardio(self):
        groups = plan_from_today_board(_board(already=True, azm=AZM_14D), day="2026-08-31")
        by = {g.group: g for g in groups}
        self.assertIn("cardio", by)
        train = by.get("training")
        if train:
            self.assertFalse(any(it.slug.startswith("ex-") for it in train.items))


class EnsureAutoComplete(unittest.TestCase):
    def _run(self, board, store=None):
        store = store if store is not None else {}
        created: list[dict] = []
        complete_calls: list[tuple[str, bool]] = []

        def fake_list(list_id, show_completed=True, show_hidden=True):
            return {"ok": True, "tasks": list(store.values())}

        def fake_delete(list_id, task_id):
            store.pop(task_id, None)
            return {"ok": True}

        def fake_create(list_id, title, notes="", due=None, parent=None):
            tid = f"c{len(created) + 1}"
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
            return {"ok": True, "task": task}

        def fake_complete(list_id, task_id, completed=True):
            complete_calls.append((task_id, completed))
            task = store.get(task_id)
            if task:
                task["status"] = "completed" if completed else "needsAction"
            return {"ok": True, "task": task}

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                "os.environ", {"RESISTANCE_DASHBOARD_CONFIG_DIR": str(Path(tmp))}
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks.gtb.credentials_status",
                return_value={"ok": True},
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
                side_effect=fake_complete,
            ), mock.patch(
                "rt_dashboard.daily_plan_tasks._load_cache", return_value={}
            ), mock.patch("rt_dashboard.daily_plan_tasks._save_cache"):
                result = ensure_daily_tasks(board, day="2026-08-31")
        return result, store, created, complete_calls

    def test_seeds_kind_and_progress_title(self):
        result, store, created, _calls = self._run(_board(azm=AZM_14D))
        self.assertTrue(result.get("ok"), result)
        cardio = next(g for g in result["groups"] if g["group"] == "cardio")
        self.assertEqual(len(cardio["items"]), 1)
        leaf = cardio["items"][0]
        self.assertEqual(leaf["slug"], "azm")
        self.assertIn("AZM", leaf["title"])
        self.assertFalse(leaf["completed"])
        notes = [t.get("notes") or "" for t in created]
        self.assertTrue(any("[fitdash-kind:cardio|azm]" in n for n in notes), created)

    def test_auto_completes_when_azm_hits_target(self):
        board = _board(azm=AZM_14D + [{"date": "2026-08-31", "total_minutes": 90}])
        result, store, created, calls = self._run(board)
        self.assertTrue(result.get("ok"), result)
        cardio = next(g for g in result["groups"] if g["group"] == "cardio")
        leaf = cardio["items"][0]
        self.assertTrue(leaf["completed"])
        leaf_ids = {t["id"] for t in created if "AZM" in (t.get("title") or "")}
        self.assertTrue(any(tid in leaf_ids and done for tid, done in calls))

    def test_below_target_stays_open(self):
        result, _store, _created, calls = self._run(
            _board(azm=AZM_14D + [{"date": "2026-08-31", "total_minutes": 1}])
        )
        cardio = next(g for g in result["groups"] if g["group"] == "cardio")
        self.assertFalse(cardio["items"][0]["completed"])
        azm_ids = {
            row["task_id"]
            for g in result["groups"]
            if g["group"] == "cardio"
            for row in g["items"]
        }
        self.assertFalse(any(tid in azm_ids and done for tid, done in calls))

    def test_title_upserts_progress_same_kind(self):
        store = {}
        first, store, created, _ = self._run(_board(azm=AZM_14D), store=store)
        leaf_id = next(
            t["id"] for t in created if "AZM" in (t.get("title") or "")
        )
        n_created = len(created)
        second, store, created2, _ = self._run(
            _board(azm=AZM_14D + [{"date": "2026-08-31", "total_minutes": 8}]),
            store=store,
        )
        cardio = next(g for g in second["groups"] if g["group"] == "cardio")
        self.assertEqual(cardio["items"][0]["task_id"], leaf_id)
        self.assertIn("8 /", cardio["items"][0]["title"])
        extra_azm = [
            t for t in created2 if "AZM" in (t.get("title") or "") and t["id"] != leaf_id
        ]
        self.assertFalse(extra_azm)
        self.assertGreaterEqual(n_created, 1)


class CoachEmitsCardio(unittest.TestCase):
    def test_today_board_always_has_cardio_action(self):
        rec = RecoveryStatus(label="Ready", score=80.0, reasons=["ok"])
        board = build_today_board(
            as_of="2026-08-31",
            recovery=rec,
            workout_plan={"is_rest_day": False, "session_type": "push", "exercises": []},
            meal_plan={},
            consumed={"calories": 0, "protein_g": 0},
            targets={"calories": 2100, "protein_g": 200},
            adherence={},
            active_zone_minutes=AZM_14D,
        )
        acts = [a for a in board["actions"] if a.get("kind") == "cardio"]
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["id"], "azm")
        self.assertEqual(board["cardio"]["kind"], KIND_KEY)
        self.assertFalse(board["cardio"]["easy"])

    def test_rest_day_is_walk_zone2(self):
        rec = RecoveryStatus(label="Needs Rest", score=20.0, reasons=["low sleep"])
        board = build_today_board(
            as_of="2026-08-31",
            recovery=rec,
            workout_plan={"is_rest_day": True, "session_type": "rest", "exercises": []},
            meal_plan={},
            consumed={"calories": 0, "protein_g": 0},
            targets={"calories": 2100, "protein_g": 200},
            adherence={},
            active_zone_minutes=AZM_14D,
        )
        self.assertTrue(board["cardio"]["easy"])
        self.assertTrue(board["cardio"]["title"].startswith("Walk · Zone 2"))


class CardioIsNotALift(unittest.TestCase):
    def test_looks_like_lift_false(self):
        self.assertFalse(
            looks_like_lift_quest(
                group="cardio",
                title="Cardio — 12 / 20 AZM",
                slug="azm",
            )
        )
        self.assertFalse(
            looks_like_lift_quest(
                group="cardio",
                title="Walk · Zone 2 — 8 / 10 AZM",
                slug="azm",
            )
        )

    def test_complete_does_not_write_ppl_session(self):
        session, info = apply_quest_to_session(
            completed=True,
            group="cardio",
            title="Cardio — 30 / 20 AZM",
            slug="azm",
            session_type="push",
            today_workout={
                "session_type": "push",
                "exercises": [{"name": "DB Press", "weight_lbs": 50, "sets": 3, "reps": 10}],
            },
            sessions=[],
            today="2026-08-31",
        )
        self.assertIsNone(session)
        self.assertFalse(info["wrote"])
        self.assertEqual(info["reason"], "not_lift")
        self.assertNotEqual(info.get("session_type"), "push")


if __name__ == "__main__":
    unittest.main()
