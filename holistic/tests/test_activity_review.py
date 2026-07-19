"""Duchess walk confirm/deny review flow."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.activity_review import (  # noqa: E402
    pending_walk_candidates,
    review_walk,
    sync_walk_candidates,
)
from holistic.time_allocator.domain import (  # noqa: E402
    build_rolling_plan,
    empty_state,
    seed_starter,
)


class ActivityReviewTests(unittest.TestCase):
    def test_confirm_logs_duchess_and_clears_pending(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        state["activity_reviews"] = [
            {
                "id": "ex-test1",
                "start": "2026-07-19T02:59:26-04:00",
                "end": "2026-07-19T03:18:26-04:00",
                "minutes": 19,
                "exercise_type": "WALKING",
                "display_name": "Walk",
                "status": "pending",
                "local_date": "2026-07-19",
                "target_hint": "duchess-walk",
            }
        ]
        pending = pending_walk_candidates(state, as_of=date(2026, 7, 19), days=2)
        self.assertEqual(len(pending), 1)

        state = review_walk(state, "ex-test1", decision="confirm")
        pending2 = pending_walk_candidates(state, as_of=date(2026, 7, 19), days=2)
        self.assertEqual(len(pending2), 0)
        rev = next(r for r in state["activity_reviews"] if r["id"] == "ex-test1")
        self.assertEqual(rev["status"], "confirmed_duchess")

        plan = build_rolling_plan(
            state,
            now=datetime(2026, 7, 19, 12, 0, tzinfo=timezone(timedelta(hours=-4))),
            as_of=date(2026, 7, 19),
        )
        # 45 plan - 19 logged = 26 remaining (or gone if min met differently)
        d = next((b for b in plan["blocks"] if b["id"] == "duchess-walk"), None)
        if d:
            self.assertEqual(d.get("done_today"), 19)
            self.assertEqual(d["minutes"], 26)

    def test_deny_keeps_out_of_pending(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        state["activity_reviews"] = [
            {
                "id": "ex-deny",
                "start": "2026-07-19T10:00:00-04:00",
                "end": "2026-07-19T10:20:00-04:00",
                "minutes": 20,
                "exercise_type": "WALKING",
                "status": "pending",
                "local_date": "2026-07-19",
            }
        ]
        state = review_walk(state, "ex-deny", decision="deny")
        self.assertEqual(len(pending_walk_candidates(state, as_of=date(2026, 7, 19))), 0)
        logs = [lg for lg in state.get("logs") or [] if lg.get("target_id") == "duchess-walk"]
        self.assertEqual(logs, [])

    def test_sync_merges_new_pending(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        fake = (
            [
                {
                    "id": "ex-new",
                    "start": "2026-07-19T12:00:00+00:00",
                    "end": "2026-07-19T12:25:00+00:00",
                    "minutes": 25,
                    "exercise_type": "WALKING",
                    "display_name": "Walk",
                    "recording_method": "PASSIVELY_MEASURED",
                    "source": "google_health",
                    "local_date": "2026-07-19",
                }
            ],
            "google_health",
        )
        with mock.patch(
            "holistic.time_allocator.activity_review.fetch_walking_sessions",
            return_value=fake,
        ):
            state, meta = sync_walk_candidates(state, days=3)
        self.assertEqual(meta["new_pending"], 1)
        self.assertEqual(meta["fetched"], 1)
        self.assertEqual(len(pending_walk_candidates(state, as_of=date(2026, 7, 19))), 1)


if __name__ == "__main__":
    unittest.main()
