"""Log progress on next-actions updates rolling plan."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.domain import (  # noqa: E402
    add_item,
    apply_plan,
    build_rolling_plan,
    empty_state,
    log_action_progress,
    seed_starter,
)
from holistic.time_allocator.recommend import recommend_next  # noqa: E402


class ProgressTests(unittest.TestCase):
    def test_partial_duchess_then_done(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        now = datetime(2026, 7, 18, 10, 0, 0)
        as_of = date(2026, 7, 18)
        state = log_action_progress(state, "duchess-walk", minutes=20, on=as_of)
        plan = build_rolling_plan(state, now=now, as_of=as_of)
        d = next(b for b in plan["blocks"] if b["id"] == "duchess-walk")
        self.assertEqual(d["minutes"], 25)  # 45 plan - 20 done
        self.assertEqual(d["done_today"], 20)

        state = log_action_progress(state, "duchess-walk", complete=True, on=as_of)
        plan = build_rolling_plan(state, now=now, as_of=as_of)
        self.assertNotIn("duchess-walk", {b["id"] for b in plan["blocks"]})

    def test_lyft_duty_reduces_remaining_drive(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        from holistic.time_allocator.domain import add_log
        from holistic.time_allocator.lyft_duty import set_lyft_driven

        for i in range(3):
            state = add_log(state, "workout", 1, on=date(2026, 7, 15 + i))
        state = log_action_progress(state, "duchess-walk", complete=True, on=date(2026, 7, 18))
        plan = build_rolling_plan(
            state, now=datetime(2026, 7, 18, 12, 0, 0), as_of=date(2026, 7, 18)
        )
        lyft0 = next(b for b in plan["blocks"] if b["id"] == "lyft")
        self.assertEqual(lyft0["minutes"], 12 * 60)  # full cycle when driven=0
        state = set_lyft_driven(state, 2 * 60)  # 2h driven → 10h left
        plan = build_rolling_plan(
            state, now=datetime(2026, 7, 18, 12, 0, 0), as_of=date(2026, 7, 18)
        )
        lyft1 = next(b for b in plan["blocks"] if b["id"] == "lyft")
        self.assertEqual(lyft1["minutes"], 10 * 60)
        self.assertEqual(lyft1.get("remaining_drive_minutes"), 10 * 60)

    def test_adhoc_partial(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        state = add_item(state, "Deep work", minutes=90, priority=6, item_id="dw")
        state = log_action_progress(state, "dw", minutes=30, on=date(2026, 7, 18))
        item = next(i for i in state["items"] if i["id"] == "dw")
        self.assertEqual(item["minutes"], 60)
        self.assertEqual(item["done_minutes"], 30)
        recs = recommend_next(
            apply_plan(state, now=datetime(2026, 7, 18, 10, 0, 0), as_of=date(2026, 7, 18)),
            now=datetime(2026, 7, 18, 10, 0, 0),
        )
        dw = next(r for r in recs if r["id"] == "dw")
        self.assertTrue(dw.get("loggable"))
        self.assertEqual(dw["minutes"], 60)


if __name__ == "__main__":
    unittest.main()
