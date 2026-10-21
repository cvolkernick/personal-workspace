"""#1304: yesterday Legs, today Push -> quest rows must show Push lifts."""

from __future__ import annotations

import unittest

from rt_dashboard.daily_plan_tasks import (
    PlannedGroup,
    PlannedItem,
    _objective_session_type,
    apply_training_objective,
)

LEGS = [
    ("ex-rdl", "RDL (50 lb 1×5)"),
    ("ex-leg-press", "Leg Press (190 lb 1×8)"),
    ("ex-seated-leg-curls", "Seated Leg Curls (90 lb 1×9)"),
]
PUSH = [
    ("ex-bench-press", "Bench Press (135 lb 3×8)"),
    ("ex-overhead-press", "Overhead Press (75 lb 3×8)"),
    ("ex-tricep-pushdown", "Tricep Pushdown (50 lb 3×12)"),
]


def _plan(lifts):
    items = [PlannedItem(group="training", slug="train-session", title="Complete today's PUSH session")]
    items += [PlannedItem(group="training", slug=s, title=t) for s, t in lifts]
    return [PlannedGroup(group="training", title="Training", emoji="x", items=items)]


def _titles(planned):
    return [it.title for g in planned for it in g.items if it.slug.startswith("ex-")]


class StaleLegsOnPushDay(unittest.TestCase):
    def _saved(self, **extra):
        leaves = [{"slug": s, "title": t, "notes_extra": ""} for s, t in LEGS]
        return {"frozen": True, "target_n": 3, "leaves": leaves, **extra}

    def test_legacy_legs_objective_rebuilt_for_push(self):
        st = _objective_session_type({"session_type": "push"})
        planned, obj = apply_training_objective(_plan(PUSH), self._saved(), session_type=st)
        self.assertEqual(_titles(planned), [t for _, t in PUSH])
        self.assertEqual(obj["session_type"], "push")
        self.assertEqual(len(obj["leaves"]), 3)

    def test_stamped_legs_objective_rebuilt_for_push(self):
        planned, obj = apply_training_objective(
            _plan(PUSH), self._saved(session_type="legs"), session_type="push"
        )
        self.assertEqual(_titles(planned), [t for _, t in PUSH])
        self.assertEqual(len(_titles(planned)), len(set(_titles(planned))))

    def test_same_letter_objective_still_frozen(self):
        saved = {"frozen": True, "target_n": 3, "session_type": "push",
                 "leaves": [{"slug": s, "title": t} for s, t in PUSH]}
        planned, _ = apply_training_objective(_plan(PUSH[:1]), saved, session_type="push")
        self.assertEqual(len(_titles(planned)), 3)

    def test_trained_day_keeps_objective(self):
        st = _objective_session_type({"session_type": "pull", "already_trained_today": True})
        self.assertEqual(st, "")
        planned, _ = apply_training_objective(_plan(PUSH), self._saved(), session_type=st)
        self.assertEqual(_titles(planned), [t for _, t in LEGS])

    def test_progressed_objective_is_kept(self):
        planned, _ = apply_training_objective(
            _plan(PUSH), self._saved(), session_type="push", done_slugs={"ex-rdl"}
        )
        self.assertEqual(_titles(planned), [t for _, t in LEGS])

    def test_ensure_daily_tasks_rebuilds_push_rows(self):
        """End to end via plan_preview: cached Legs leaves on a Push day."""
        import json, tempfile
        from pathlib import Path
        from unittest import mock
        from rt_dashboard.daily_plan_tasks import plan_preview

        day = "2026-10-20"
        board = {
            "date": day,
            "actions": [{"id": "train-session", "kind": "training", "priority": 1,
                         "text": "Complete today's PUSH session (3 lifts as prescribed)."}],
            "workout": {"session_type": "push", "exercises": [
                {"name": "Bench Press", "sets": 3, "reps": 8, "weight_lbs": 135},
                {"name": "Overhead Press", "sets": 3, "reps": 8, "weight_lbs": 75},
                {"name": "Tricep Pushdown", "sets": 3, "reps": 12, "weight_lbs": 50},
            ]},
        }
        cache = {day: {"list_id": None, "ids": {}, "local_completed": {},
                       "training_objective": self._saved()}}
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "daily_quest_cache.json").write_text(json.dumps(cache), encoding="utf-8")
            with mock.patch.dict("os.environ", {"RESISTANCE_DASHBOARD_CONFIG_DIR": tmp}):
                prev = plan_preview(board, day=day)
        train = [g for g in prev.get("groups") or [] if g.get("group") == "training"][0]
        titles = [it["title"] for it in train["items"] if str(it["slug"]).startswith("ex-")]
        self.assertEqual(len(titles), 3, titles)
        self.assertTrue(all(t.split(" (")[0] in ("Bench Press", "Overhead Press", "Tricep Pushdown") for t in titles), titles)
        self.assertFalse(any("RDL" in t or "Leg" in t for t in titles), titles)


if __name__ == "__main__":
    unittest.main()
