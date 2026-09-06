"""Sleep/recovery daily quest: prior night vs 8h, nap recover, GH lag pending."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from rt_dashboard.coach import build_today_board
from rt_dashboard.daily_plan_tasks import (
    SLEEP_RECOVERY_CACHE_KEY,
    SLEEP_RECOVERY_SLUG,
    ensure_daily_tasks,
    item_kind_key,
    plan_from_today_board,
)
from rt_dashboard.models import RecoveryStatus
from rt_dashboard.quest_workout_log import looks_like_lift_quest
from rt_dashboard.sleep_quest import (
    KIND_KEY,
    score_sleep,
    sleep_spec,
    sleep_title,
)


def _board(*, sleep_battery=None, intervals=None, rest=False):
    return {
        "date": "2026-09-06",
        "now": "2026-09-06T16:00:00-04:00",
        "recommendation": "rest" if rest else "train",
        "recovery": {"label": "Ready", "score": 80.0},
        "workout": {
            "is_rest_day": rest,
            "already_trained_today": False,
            "session_type": "rest" if rest else "push",
            "exercises": []
            if rest
            else [{"name": "DB Press", "sets": 3, "reps": 10, "weight_lbs": 50}],
        },
        "meal": {"meals": [], "items": []},
        "purchases": [],
        "actions": [],
        "sleep_battery": sleep_battery,
        "sleep_intervals": list(intervals or []),
    }


OVERNIGHT_8H = [
    {
        "start": "2026-09-05T22:00:00-04:00",
        "end": "2026-09-06T06:00:00-04:00",
    }
]
OVERNIGHT_6H = [
    {
        "start": "2026-09-05T23:00:00-04:00",
        "end": "2026-09-06T05:00:00-04:00",
    }
]
NAP_2H = {
    "start": "2026-09-06T13:00:00-04:00",
    "end": "2026-09-06T15:00:00-04:00",
}
ET = timezone(timedelta(hours=-4))
NOW = datetime(2026, 9, 6, 16, 0, tzinfo=ET)


class ScorePriorNight(unittest.TestCase):
    def test_full_night_hits(self):
        scored = score_sleep(
            last_sleep_hours=8.2,
            intervals=OVERNIGHT_8H,
            now=datetime(2026, 9, 6, 12, 0, tzinfo=ET),
        )
        self.assertEqual(scored["status"], "hit")
        self.assertTrue(scored["hit"])
        self.assertAlmostEqual(scored["last_night_hours"], 8.0, places=1)

    def test_short_night_is_incomplete(self):
        scored = score_sleep(
            last_sleep_hours=6.0,
            intervals=OVERNIGHT_6H,
            now=datetime(2026, 9, 6, 12, 0, tzinfo=ET),
        )
        self.assertEqual(scored["status"], "short")
        self.assertFalse(scored["hit"])
        self.assertAlmostEqual(scored["last_night_hours"], 6.0, places=1)

    def test_nap_recovers_without_rewriting_last_night(self):
        scored = score_sleep(
            last_sleep_hours=2.0,
            last_wake_at="2026-09-06T15:00:00-04:00",
            intervals=OVERNIGHT_6H + [NAP_2H],
            now=NOW,
        )
        self.assertEqual(scored["status"], "recovered")
        self.assertTrue(scored["hit"])
        self.assertAlmostEqual(scored["last_night_hours"], 6.0, places=1)
        self.assertAlmostEqual(scored["extra_hours"], 2.0, places=1)
        title = sleep_title(
            status="recovered",
            last_night=6.0,
            extra=2.0,
            target=8.0,
        )
        self.assertIn("recovered", title.lower())
        self.assertIn("6.0h last night", title)
        self.assertNotIn("8.0h last night", title)

    def test_short_nap_still_short(self):
        scored = score_sleep(
            last_sleep_hours=6.0,
            intervals=OVERNIGHT_6H
            + [
                {
                    "start": "2026-09-06T13:00:00-04:00",
                    "end": "2026-09-06T13:40:00-04:00",
                }
            ],
            now=NOW,
        )
        self.assertEqual(scored["status"], "short")
        self.assertFalse(scored["hit"])

    def test_missing_interval_is_pending_not_zero(self):
        scored = score_sleep(last_sleep_hours=None, intervals=[], now=NOW)
        self.assertEqual(scored["status"], "pending")
        self.assertFalse(scored["hit"])
        self.assertIsNone(scored["last_night_hours"])
        title = sleep_title(
            status="pending", last_night=None, extra=0, target=8.0
        )
        self.assertIn("waiting", title.lower())
        self.assertNotIn("0.0h", title)

    def test_sleeping_mode_without_completed_overnight_is_pending(self):
        scored = score_sleep(
            last_sleep_hours=2.5,
            mode="sleeping",
            intervals=[],
            now=NOW,
        )
        self.assertEqual(scored["status"], "pending")


class SpecAndTitle(unittest.TestCase):
    def test_hit_title_shape(self):
        spec = sleep_spec(
            _board(
                sleep_battery={
                    "mode": "awake",
                    "last_sleep_hours": 8.2,
                    "sleep_target_hours": 8.0,
                    "pct_charged": 70,
                },
                intervals=OVERNIGHT_8H,
            )
        )
        self.assertEqual(spec["kind"], KIND_KEY)
        self.assertEqual(spec["slug"], "sleep-recovery")
        self.assertTrue(spec["hit"])
        self.assertEqual(spec["status"], "hit")
        self.assertIn("last night", spec["title"])
        self.assertNotIn("tonight", spec["title"].lower())
        self.assertNotIn("Protect bedtime", spec["title"])

    def test_short_copy_is_corrective_not_tonight(self):
        spec = sleep_spec(
            _board(
                sleep_battery={
                    "mode": "awake",
                    "last_sleep_hours": 6.0,
                    "sleep_target_hours": 8.0,
                    "pct_charged": 55,
                    "empty_at": "2026-09-06T21:40:00-04:00",
                },
                intervals=OVERNIGHT_6H,
            )
        )
        self.assertEqual(spec["status"], "short")
        self.assertFalse(spec["hit"])
        self.assertIn("6.0h / 8.0h last night", spec["title"])
        self.assertIn("Nap", spec["title"])
        self.assertIn("caffeine", spec["title"].lower())
        self.assertNotIn("sleep 8h tonight", spec["title"].lower())
        self.assertNotIn("Protect bedtime", spec["title"])

    def test_battery_empty_is_copy_on_same_leaf(self):
        spec = sleep_spec(
            _board(
                sleep_battery={
                    "mode": "awake",
                    "last_sleep_hours": 6.0,
                    "sleep_target_hours": 8.0,
                    "pct_charged": 12,
                    "empty_at": "2026-09-06T21:40:00-04:00",
                },
                intervals=OVERNIGHT_6H,
            )
        )
        self.assertTrue(spec["battery_critical"])
        self.assertIn("Battery empty", spec["title"])
        self.assertEqual(spec["slug"], "sleep-recovery")


class PlanSeedsSleepEveryDay(unittest.TestCase):
    def test_board_without_action_still_seeds_sleep(self):
        groups = plan_from_today_board(
            _board(
                sleep_battery={
                    "mode": "awake",
                    "last_sleep_hours": 8.0,
                    "sleep_target_hours": 8.0,
                },
                intervals=OVERNIGHT_8H,
            ),
            day="2026-09-06",
        )
        by = {g.group: g for g in groups}
        self.assertIn("sleep", by)
        leaves = by["sleep"].items
        self.assertEqual(len(leaves), 1)
        self.assertEqual(leaves[0].slug, SLEEP_RECOVERY_SLUG)
        self.assertEqual(item_kind_key(leaves[0]), SLEEP_RECOVERY_CACHE_KEY)
        self.assertIn("last night", leaves[0].title)

    def test_protect_bedtime_and_battery_low_collapse_to_one(self):
        board = _board(
            sleep_battery={
                "mode": "awake",
                "last_sleep_hours": 6.0,
                "sleep_target_hours": 8.0,
                "pct_charged": 22,
            },
            intervals=OVERNIGHT_6H,
        )
        board["actions"] = [
            {
                "kind": "sleep",
                "id": "protect-bedtime",
                "text": "Protect bedtime — battery 60.3% after wake 18:28.",
            },
            {
                "kind": "sleep",
                "id": "sleep-battery-low",
                "text": "Sleep battery low (22%) — plan bedtime soon.",
            },
        ]
        groups = plan_from_today_board(board, day="2026-09-06")
        sleep = next(g for g in groups if g.group == "sleep")
        self.assertEqual(len(sleep.items), 1)
        self.assertEqual(sleep.items[0].slug, SLEEP_RECOVERY_SLUG)
        self.assertIn("last night", sleep.items[0].title)
        self.assertNotIn("Protect bedtime", sleep.items[0].title)
        self.assertNotIn("Sleep battery low", sleep.items[0].title)


class EnsureAutoComplete(unittest.TestCase):
    def _run(self, board, store=None, day="2026-09-06"):
        store = store if store is not None else {}
        created: list[dict] = []
        complete_calls: list[tuple[str, bool]] = []

        def fake_list(list_id, show_completed=True, show_hidden=True):
            return {"ok": True, "tasks": list(store.values())}

        def fake_delete(list_id, task_id):
            store.pop(task_id, None)
            return {"ok": True}

        def fake_create(list_id, title, notes="", due=None, parent=None):
            tid = f"s{len(created) + 1}"
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

        def fake_update(
            list_id, task_id, title=None, notes=None, due=None, status=None, clear_due=False
        ):
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
                result = ensure_daily_tasks(board, day=day)
        return result, store, created, complete_calls

    def test_auto_completes_when_last_night_hits(self):
        board = _board(
            sleep_battery={
                "mode": "awake",
                "last_sleep_hours": 8.2,
                "sleep_target_hours": 8.0,
            },
            intervals=OVERNIGHT_8H,
        )
        result, store, created, calls = self._run(board)
        self.assertTrue(result.get("ok"), result)
        sleep = next(g for g in result["groups"] if g["group"] == "sleep")
        self.assertEqual(len(sleep["items"]), 1)
        leaf = sleep["items"][0]
        self.assertTrue(leaf["completed"])
        self.assertIn("last night", leaf["title"])
        notes = [t.get("notes") or "" for t in created]
        self.assertTrue(
            any("[fitdash-kind:sleep|sleep-recovery]" in n for n in notes), created
        )
        leaf_ids = {t["id"] for t in created if "last night" in (t.get("title") or "")}
        self.assertTrue(any(tid in leaf_ids and done for tid, done in calls))

    def test_short_stays_open(self):
        result, _store, _created, calls = self._run(
            _board(
                sleep_battery={
                    "mode": "awake",
                    "last_sleep_hours": 6.0,
                    "sleep_target_hours": 8.0,
                    "pct_charged": 50,
                },
                intervals=OVERNIGHT_6H,
            )
        )
        sleep = next(g for g in result["groups"] if g["group"] == "sleep")
        self.assertFalse(sleep["items"][0]["completed"])
        self.assertIn("Nap", sleep["items"][0]["title"])

    def test_pending_does_not_fail_as_zero(self):
        result, _store, created, calls = self._run(
            _board(sleep_battery={"mode": "no_data"}, intervals=[])
        )
        sleep = next(g for g in result["groups"] if g["group"] == "sleep")
        leaf = sleep["items"][0]
        self.assertFalse(leaf["completed"])
        self.assertIn("waiting", leaf["title"].lower())
        self.assertNotIn("0.0h", leaf["title"])
        sleep_ids = {row["task_id"] for row in sleep["items"]}
        self.assertFalse(any(tid in sleep_ids and done for tid, done in calls))

    def test_nap_recover_auto_completes(self):
        result, _store, _created, calls = self._run(
            _board(
                sleep_battery={
                    "mode": "awake",
                    "last_sleep_hours": 2.0,
                    "sleep_target_hours": 8.0,
                    "last_wake_at": "2026-09-06T15:00:00-04:00",
                },
                intervals=OVERNIGHT_6H + [NAP_2H],
            )
        )
        sleep = next(g for g in result["groups"] if g["group"] == "sleep")
        leaf = sleep["items"][0]
        self.assertTrue(leaf["completed"])
        self.assertIn("recovered", leaf["title"].lower())
        self.assertIn("6.0h last night", leaf["title"])

    def test_legacy_leaves_collapse_to_one(self):
        store = {
            "sleep-h": {
                "id": "sleep-h",
                "title": "Sleep & recovery",
                "notes": "[fitdash-quest:2026-09-06]",
                "status": "needsAction",
                "due": "2026-09-06T00:00:00.000Z",
            },
            "bed": {
                "id": "bed",
                "title": "Protect bedtime — battery 60.3% after wake 18:28.",
                "notes": (
                    "[fitdash-quest:2026-09-06]\n"
                    "[fitdash-kind:sleep|protect-bedtime]"
                ),
                "parent": "sleep-h",
                "status": "needsAction",
                "due": "2026-09-06T00:00:00.000Z",
            },
            "low": {
                "id": "low",
                "title": "Sleep battery low (22%) — plan bedtime soon.",
                "notes": (
                    "[fitdash-quest:2026-09-06]\n"
                    "[fitdash-kind:sleep|sleep-battery-low]"
                ),
                "parent": "sleep-h",
                "status": "needsAction",
                "due": "2026-09-06T00:00:00.000Z",
            },
            "jot": {
                "id": "jot",
                "title": "Text the vet",
                "notes": "",
                "status": "needsAction",
                "due": "2026-09-06T00:00:00.000Z",
            },
        }
        result, store, created, _calls = self._run(
            _board(
                sleep_battery={
                    "mode": "awake",
                    "last_sleep_hours": 8.2,
                    "sleep_target_hours": 8.0,
                },
                intervals=OVERNIGHT_8H,
            ),
            store=store,
        )
        self.assertTrue(result.get("ok"), result)
        sleep_leaves = [
            t
            for t in store.values()
            if t.get("id") not in ("sleep-h", "jot")
            and (
                "last night" in (t.get("title") or "")
                or "Protect bedtime" in (t.get("title") or "")
                or "Sleep battery low" in (t.get("title") or "")
            )
        ]
        incompletes = [t for t in sleep_leaves if t.get("status") != "completed"]
        self.assertEqual(len(incompletes), 0)
        self.assertIn("jot", store)
        self.assertEqual(store["jot"]["title"], "Text the vet")
        kept = [t for t in sleep_leaves if t.get("id") in ("bed", "low")]
        self.assertEqual(len(kept), 1)
        self.assertIn("last night", kept[0]["title"])
        self.assertFalse(
            any("Protect bedtime" in (t.get("title") or "") for t in created)
        )


class CoachEmitsSleep(unittest.TestCase):
    def test_today_board_always_has_one_sleep_action(self):
        rec = RecoveryStatus(label="Ready", score=80.0, reasons=["ok"])
        board = build_today_board(
            as_of="2026-09-06",
            recovery=rec,
            workout_plan={"is_rest_day": False, "session_type": "push", "exercises": []},
            meal_plan={},
            consumed={"calories": 0, "protein_g": 0},
            targets={"calories": 2100, "protein_g": 200},
            adherence={},
            sleep_battery={
                "mode": "awake",
                "last_sleep_hours": 8.2,
                "sleep_target_hours": 8.0,
                "pct_charged": 80,
            },
            sleep_intervals=OVERNIGHT_8H,
        )
        acts = [a for a in board["actions"] if a.get("kind") == "sleep"]
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["id"], "sleep-recovery")
        self.assertIn("last night", acts[0]["text"])
        self.assertTrue(board["sleep"]["hit"])
        self.assertEqual(board["sleep_battery"]["last_sleep_hours"], 8.2)

    def test_no_data_is_pending_action(self):
        rec = RecoveryStatus(label="Ready", score=80.0, reasons=["ok"])
        board = build_today_board(
            as_of="2026-09-06",
            recovery=rec,
            workout_plan={"is_rest_day": True, "session_type": "rest", "exercises": []},
            meal_plan={},
            consumed={"calories": 0, "protein_g": 0},
            targets={"calories": 2100, "protein_g": 200},
            adherence={},
            sleep_battery={"mode": "no_data"},
        )
        acts = [a for a in board["actions"] if a.get("kind") == "sleep"]
        self.assertEqual(len(acts), 1)
        self.assertIn("waiting", acts[0]["text"].lower())
        self.assertFalse(board["sleep"]["hit"])


class SleepIsNotALift(unittest.TestCase):
    def test_looks_like_lift_false(self):
        self.assertFalse(
            looks_like_lift_quest(
                group="sleep",
                title="Sleep — 8.2h / 8.0h last night",
                slug="sleep-recovery",
            )
        )
        self.assertFalse(
            looks_like_lift_quest(
                group="sleep",
                title="Protect bedtime — battery 60% after wake 07:00.",
                slug="protect-bedtime",
            )
        )


if __name__ == "__main__":
    unittest.main()
