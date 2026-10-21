"""#1304 follow-up: a kept legacy Legs target must not be stamped as Push,
and Legs lifts done after midnight are not progress on a Push day."""

from __future__ import annotations

import unittest

from rt_dashboard.daily_plan_tasks import (
    PlannedGroup,
    PlannedItem,
    apply_training_objective,
)

LEGS = [
    ("ex-zz-hinge", "Zz Hinge (50 lb 1×5)"),
    ("ex-zz-press", "Zz Press (190 lb 1×8)"),
]
PUSH = [("ex-bench-press", "Bench Press (135 lb 3×8)")]


def _plan(lifts):
    items = [PlannedItem(group="training", slug="train-session", title="Session")]
    items += [PlannedItem(group="training", slug=s, title=t) for s, t in lifts]
    return [PlannedGroup(group="training", title="Training", emoji="x", items=items)]


def _saved(**extra):
    leaves = [{"slug": s, "title": t, "notes_extra": ""} for s, t in LEGS]
    return {"frozen": True, "target_n": 2, "leaves": leaves, **extra}


def _slugs(planned):
    return [it.slug for g in planned for it in g.items if it.slug.startswith("ex-")]


class ObjectiveLetter1304b(unittest.TestCase):
    def test_kept_legacy_target_is_not_stamped_with_todays_letter(self):
        # Planner echoed the legacy target back as today's plan (remainder).
        _, obj = apply_training_objective(_plan(LEGS), _saved(), session_type="push")
        self.assertNotEqual(obj.get("session_type"), "push")

    def test_cross_letter_target_without_progress_rebuilds(self):
        planned, obj = apply_training_objective(
            _plan(PUSH), _saved(session_type="legs"), session_type="push",
        )
        self.assertEqual(_slugs(planned), ["ex-bench-press"])
        self.assertEqual(obj.get("session_type"), "push")

    def test_same_letter_progress_still_keeps_target(self):
        planned, _ = apply_training_objective(
            _plan(PUSH), _saved(session_type="push"),
            session_type="push", done_slugs={"ex-zz-hinge"},
        )
        self.assertIn("ex-zz-hinge", _slugs(planned))


if __name__ == "__main__":
    unittest.main()
