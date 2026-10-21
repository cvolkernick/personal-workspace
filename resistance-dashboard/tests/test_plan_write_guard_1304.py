"""#1304: smoke / parity / dev runs may only write today's training-day plan."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rt_dashboard import workout_plan_store as wps  # noqa: E402

ET = ZoneInfo("America/New_York")
PLAN = {"session_type": "pull", "is_rest_day": False,
        "exercises": [{"name": "Machine Row", "sets": 3, "reps": 8}]}
ENVS = wps.PLAN_WRITE_GUARD_ENVS


class _Env(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in ENVS}
        for k in ENVS:
            os.environ.pop(k, None)
        wps.clear_memory_workout_plans()
        p = mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        wps.clear_memory_workout_plans()

    def _at(self, h, m, day=21):
        """Pin the clock: current training day as computed at 10/<day> h:m ET."""
        fixed = REAL_CTD(datetime(2026, 10, day, h, m, tzinfo=ET))
        return mock.patch.object(wps, "current_training_day", return_value=fixed)


REAL_CTD = wps.current_training_day


class GuardTests(_Env):
    def test_off_by_default_any_day_saves(self) -> None:
        self.assertFalse(wps.plan_write_guard_active())
        out = wps.save_last_good_workout_plan("u", "2026-10-19", PLAN)
        self.assertTrue(out["ok"])

    def test_each_env_name_turns_it_on(self) -> None:
        for name in ENVS:
            os.environ[name] = "1"
            self.assertTrue(wps.plan_write_guard_active(), name)
            os.environ.pop(name)

    def test_refuses_non_current_day_and_logs(self) -> None:
        os.environ["FITDASH_PARITY"] = "1"
        with self._at(10, 0), self.assertLogs("fitdash.plan_write_guard", "WARNING") as logs:
            out = wps.save_last_good_workout_plan("u", "2026-10-20", PLAN)
        self.assertFalse(out["ok"])
        self.assertEqual(out["store"], "guard_refused")
        self.assertIn("2026-10-20", logs.output[0])
        self.assertIsNone(wps.load_last_good_workout_plan("u", "2026-10-20"))

    def test_allows_current_training_day(self) -> None:
        os.environ["FITDASH_SMOKE"] = "1"
        with self._at(10, 0):
            out = wps.save_last_good_workout_plan("u", "2026-10-21", PLAN)
        self.assertTrue(out["ok"])

    def test_before_cutoff_current_day_is_yesterday(self) -> None:
        # 02:30 on 10/21: training day is 10/20; the civil 10/21 row is refused.
        os.environ["FITDASH_PARITY"] = "1"
        with self._at(2, 30):
            self.assertFalse(wps.save_last_good_workout_plan("u", "2026-10-21", PLAN)["ok"])
            self.assertTrue(wps.save_last_good_workout_plan("u", "2026-10-20", PLAN)["ok"])

    def test_boundary_0359_and_0400(self) -> None:
        os.environ["FITDASH_PARITY"] = "1"
        with self._at(3, 59):
            self.assertIsNone(wps.plan_write_refusal("2026-10-20"))
            self.assertIsNotNone(wps.plan_write_refusal("2026-10-21"))
        with self._at(4, 0):
            self.assertIsNone(wps.plan_write_refusal("2026-10-21"))
            self.assertIsNotNone(wps.plan_write_refusal("2026-10-20"))

    def test_turso_put_refuses_too(self) -> None:
        os.environ["FITDASH_PARITY"] = "1"
        with self._at(10, 0):
            with self.assertRaises(PermissionError):
                wps._turso_put_workout_plan("u", "2026-10-19", PLAN)

    def test_real_clock_helper_uses_cutoff(self) -> None:
        self.assertEqual(REAL_CTD(datetime(2026, 10, 21, 3, 59, tzinfo=ET)), "2026-10-20")
        self.assertEqual(REAL_CTD(datetime(2026, 10, 21, 4, 0, tzinfo=ET)), "2026-10-21")

    def test_context_manager_restores_env(self) -> None:
        with wps.plan_write_guard():
            self.assertTrue(wps.plan_write_guard_active())
        self.assertFalse(wps.plan_write_guard_active())

    def test_agent_plan_persist_goes_through_guard(self) -> None:
        from rt_dashboard import agent_plan

        os.environ["FITDASH_PARITY"] = "1"
        with self._at(10, 0):
            out = agent_plan.persist_grok_result("u", "2026-10-20", {"ok": True, "workout": PLAN})
        self.assertFalse(out["ok"])
        self.assertEqual(out["store"], "guard_refused")


class GuardedGenerateServesPlanTests(_Env):
    def test_refused_persist_still_serves_the_generated_plan(self) -> None:
        from rt_dashboard import agent_plan

        os.environ["FITDASH_PARITY"] = "1"
        ctx = {
            "catalog": {"exercises": [{"id": "machine-row", "name": "Machine Row"}]},
            "stamped": {"session_type": "pull", "is_rest_day": False, "exercises": []},
            "sessions": [],
            "goals": {},
            "recovery": {},
        }
        with self._at(10, 0), mock.patch.object(
            agent_plan, "generate_grok_plans",
            return_value={"ok": True, "workout": dict(PLAN)},
        ):
            out = agent_plan.ensure_today_grok_plan("u", day="2026-10-20", context=ctx)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["persist"]["store"], "guard_refused")
        self.assertEqual(out["workout"]["exercises"][0]["name"], "Machine Row")
        self.assertIsNone(wps.load_last_good_workout_plan("u", "2026-10-20"))


if __name__ == "__main__":
    unittest.main()
