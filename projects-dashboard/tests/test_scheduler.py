"""Tests for local auto-start scheduler."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

import backlog as bl  # noqa: E402
import scheduler as sch  # noqa: E402


class TestScheduler(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="sched-")
        self.ws = Path(self._td.name)
        self.backlog = self.ws / "ops" / "backlog"
        self.backlog.mkdir(parents=True)
        self._patches = [
            mock.patch.object(bl, "WORKSPACE_ROOT", self.ws),
            mock.patch.object(bl, "BACKLOG_DIR", self.backlog),
            mock.patch.object(bl, "ITEMS_PATH", self.backlog / "items.json"),
            mock.patch.object(bl, "SEEDS_DIR", self.backlog / "seeds"),
            mock.patch.object(sch, "WORKSPACE_ROOT", self.ws),
            mock.patch.object(sch, "BACKLOG_DIR", self.backlog),
            mock.patch.object(sch, "CONFIG_PATH", self.backlog / "scheduler.json"),
            mock.patch.object(sch, "JOBS_PATH", self.backlog / "jobs.json"),
            mock.patch.object(sch, "REPORTS_DIR", self.backlog / "reports"),
        ]
        for p in self._patches:
            p.start()
        r = bl.add_item(
            "Auto job",
            priority="high",
            status="ready",
            mvp_scope="one thing",
            notes="go",
            area="tools",
        )
        self.bid = r["item"]["id"]
        data = bl.load_backlog()
        for it in data["items"]:
            if it["id"] == self.bid:
                it["schedule_slot"] = "now"
                it["press_rank"] = 1
        bl.save_backlog(data)

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._td.cleanup()

    def test_auto_start_and_tick_launches(self) -> None:
        sch.set_auto_start(self.bid, True)
        item = bl.get_item(self.bid)
        self.assertTrue(item.get("auto_start"))
        # Don't open Terminal in tests
        cfg = sch.load_config()
        cfg["enabled"] = True
        cfg["spawn_grok"] = False
        sch.save_config(cfg)

        with mock.patch.object(sch, "initiate_item") as init:
            init.return_value = {
                "ok": True,
                "seed_path": "ops/backlog/seeds/x.md",
                "prompt_path": "ops/backlog/seeds/x.prompt.txt",
                "launch_script": "ops/backlog/seeds/x.launch.sh",
                "goal_objective": "do it",
                "spawn": {"ok": True, "method": "test"},
            }
            result = sch.tick(force=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["launched_count"], 1)
        self.assertTrue(result["reports"])
        jobs = sch.load_jobs()["jobs"]
        self.assertEqual(jobs[-1]["status"], "launched")
        # auto_start cleared
        item2 = bl.get_item(self.bid)
        self.assertFalse(item2.get("auto_start"))

    def test_disabled_skips_without_force(self) -> None:
        sch.set_auto_start(self.bid, True)
        cfg = sch.load_config()
        cfg["enabled"] = False
        sch.save_config(cfg)
        r = sch.tick(force=False)
        self.assertTrue(r.get("skipped"))

    def test_complete_job_marks_done(self) -> None:
        sch.set_auto_start(self.bid, True)
        cfg = sch.load_config()
        cfg["enabled"] = True
        cfg["spawn_grok"] = False
        sch.save_config(cfg)
        with mock.patch.object(sch, "initiate_item") as init:
            init.return_value = {
                "ok": True,
                "seed_path": "s",
                "prompt_path": "p",
                "launch_script": "l",
                "goal_objective": "g",
                "spawn": {},
            }
            sch.tick(force=True)
        job_id = sch.load_jobs()["jobs"][-1]["id"]
        out = sch.complete_job(job_id, summary="shipped MVP")
        self.assertTrue(out["ok"])
        self.assertEqual(out["job"]["status"], "completed")
        self.assertEqual(bl.get_item(self.bid)["status"], "done")


if __name__ == "__main__":
    unittest.main()
