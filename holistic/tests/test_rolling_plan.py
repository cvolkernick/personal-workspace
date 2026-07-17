"""Tests for personal targets + rolling 24h plan."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.domain import (  # noqa: E402
    PERSONAL_TARGETS,
    add_item,
    add_log,
    apply_plan,
    build_rolling_plan,
    empty_state,
    kpi_status,
    seed_starter,
)


class RollingPlanTests(unittest.TestCase):
    def test_personal_seed_has_core_targets(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        ids = {t["id"] for t in state["targets"]}
        self.assertEqual(
            ids,
            {"sleep", "duchess-walk", "workout", "lyft"},
        )
        self.assertEqual(len(PERSONAL_TARGETS), 4)

    def test_plan_reserves_sleep_duchess_and_fills_lyft(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        # No workout logs → behind min → session included
        now = datetime(2026, 7, 17, 8, 0, 0)
        plan = build_rolling_plan(state, now=now, as_of=date(2026, 7, 17))
        by_id = {b["id"]: b for b in plan["blocks"]}
        self.assertEqual(plan["window_minutes"], 1440)
        self.assertEqual(plan["sleep_reserve_minutes"], 480)
        self.assertEqual(plan["active_minutes"], 960)
        self.assertEqual(by_id["sleep"]["minutes"], 480)
        self.assertEqual(by_id["duchess-walk"]["minutes"], 130)
        self.assertIn("workout", by_id)
        self.assertEqual(by_id["workout"]["minutes"], 60)
        # Remaining active: 960 - 130 - 60 = 770 → Lyft
        self.assertEqual(by_id["lyft"]["minutes"], 770)
        self.assertEqual(by_id["lyft"]["role"], "fill")
        total = sum(b["minutes"] for b in plan["blocks"])
        self.assertEqual(total, 1440)

    def test_adhoc_reduces_lyft_fill(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        state = add_item(state, "Errand", priority=6, minutes=90, item_id="errand")
        # Log enough workouts so no forced session
        for i in range(3):
            state = add_log(state, "workout", 1, on=date(2026, 7, 14 + i))
        plan = build_rolling_plan(state, now=datetime(2026, 7, 17, 9, 0, 0), as_of=date(2026, 7, 17))
        by_id = {b["id"]: b for b in plan["blocks"]}
        self.assertNotIn("workout", by_id)  # on track, no forced session
        self.assertEqual(by_id["errand"]["minutes"], 90)
        # 960 - 130 duchess - 90 errand = 740 lyft
        self.assertEqual(by_id["lyft"]["minutes"], 740)

    def test_sleep_kpi_rolling_avg(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        state = add_log(state, "sleep", 7.0, on=date(2026, 7, 15))
        state = add_log(state, "sleep", 8.0, on=date(2026, 7, 16))
        state = add_log(state, "sleep", 9.0, on=date(2026, 7, 17))
        kpis = {k["id"]: k for k in kpi_status(state, as_of=date(2026, 7, 17))}
        self.assertAlmostEqual(kpis["sleep"]["detail"]["average"], 8.0)
        self.assertTrue(kpis["sleep"]["on_track"])

    def test_apply_plan_persists(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        state = apply_plan(state, now=datetime(2026, 7, 17, 8, 0, 0), as_of=date(2026, 7, 17))
        self.assertIsNotNone(state.get("plan"))
        self.assertGreaterEqual(len(state["plan"]["blocks"]), 3)


if __name__ == "__main__":
    unittest.main()
