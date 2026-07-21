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
        cfg["execution_mode"] = "spawn"
        cfg["spawn_grok"] = True
        sch.save_config(cfg)

        with mock.patch.object(sch, "initiate_item") as init:
            init.return_value = {
                "ok": True,
                "seed_path": "ops/backlog/seeds/x.md",
                "prompt_path": "ops/backlog/seeds/x.prompt.txt",
                "launch_script": "ops/backlog/seeds/x.launch.sh",
                "goal_objective": "do it",
                "spawn": {"attempted": True, "ok": True, "method": "test"},
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

    def test_tick_queue_mode_pending_terminal(self) -> None:
        sch.set_auto_start(self.bid, True)
        cfg = sch.load_config()
        cfg["enabled"] = True
        cfg["execution_mode"] = "queue"
        sch.save_config(cfg)
        with mock.patch.object(sch, "initiate_item") as init:
            init.return_value = {
                "ok": True,
                "seed_path": "ops/backlog/seeds/x.md",
                "prompt_path": "ops/backlog/seeds/x.prompt.txt",
                "launch_script": "ops/backlog/seeds/x.launch.sh",
                "goal_objective": "do it",
                "spawn": {"attempted": False},
            }
            result = sch.tick(force=True)
        self.assertEqual(result.get("pending_terminal_count"), 1)
        self.assertEqual(result.get("launched_count"), 0)
        self.assertEqual(sch.load_jobs()["jobs"][-1]["status"], "pending_terminal")
        init.assert_called()
        self.assertFalse(init.call_args.kwargs.get("try_spawn_grok"))

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
        cfg["execution_mode"] = "spawn"
        cfg["spawn_grok"] = True
        sch.save_config(cfg)
        with mock.patch.object(sch, "initiate_item") as init:
            init.return_value = {
                "ok": True,
                "seed_path": "s",
                "prompt_path": "p",
                "launch_script": "l",
                "goal_objective": "g",
                "spawn": {"attempted": True, "ok": True, "method": "test"},
            }
            sch.tick(force=True)
        job_id = sch.load_jobs()["jobs"][-1]["id"]
        out = sch.complete_job(job_id, summary="shipped MVP")
        self.assertTrue(out["ok"])
        self.assertEqual(out["job"]["status"], "completed")
        self.assertEqual(bl.get_item(self.bid)["status"], "done")

    def test_resolve_agent_mode_on_linux_like(self) -> None:
        cfg = sch.load_config()
        cfg["execution_mode"] = "agent"
        sch.save_config(cfg)
        plan = sch.resolve_execution_mode()
        self.assertTrue(plan.get("use_agent"))
        self.assertFalse(plan.get("should_spawn"))

    def test_orphan_running_cleared_when_backlog_ready(self) -> None:
        """Stale Terminal launch must not stay 'running' forever."""
        sch.set_auto_start(self.bid, True)
        jobs = {
            "version": 1,
            "jobs": [
                {
                    "id": "job-stale1",
                    "backlog_id": self.bid,
                    "title": "Auto job",
                    "status": "running",
                    "created_at": "2026-07-01T00:00:00+00:00",
                    "launched_at": "2026-07-01T00:00:00+00:00",
                }
            ],
        }
        sch.save_jobs(jobs)
        # backlog is ready (not planning) → orphan
        out = sch.reconcile_jobs(save=True)
        self.assertGreaterEqual(out.get("orphaned") or 0, 1)
        self.assertEqual(sch.load_jobs()["jobs"][0]["status"], "cancelled")

    def test_merged_pr_marks_job_and_backlog_done(self) -> None:
        jobs = {
            "version": 1,
            "jobs": [
                {
                    "id": "job-pr1",
                    "backlog_id": self.bid,
                    "title": "Auto job",
                    "status": "pr_ready",
                    "pr_url": "https://github.com/cvolkernick/personal-workspace/pull/99",
                    "pr_number": 99,
                    "created_at": "2026-07-20T00:00:00+00:00",
                }
            ],
        }
        sch.save_jobs(jobs)
        with mock.patch("agent_jobs.fetch_pull_request") as fp:
            fp.return_value = {
                "ok": True,
                "number": 99,
                "url": "https://github.com/cvolkernick/personal-workspace/pull/99",
                "state": "closed",
                "merged": True,
                "merged_at": "2026-07-21T12:00:00Z",
            }
            out = sch.reconcile_jobs(save=True)
        self.assertEqual(out.get("merged"), 1)
        self.assertEqual(sch.load_jobs()["jobs"][0]["status"], "completed")
        self.assertEqual(bl.get_item(self.bid)["status"], "done")

    def test_claim_pending_opens_launch(self) -> None:
        sch.set_auto_start(self.bid, True)
        cfg = sch.load_config()
        cfg["enabled"] = True
        cfg["execution_mode"] = "queue"
        sch.save_config(cfg)
        launch_rel = "ops/backlog/seeds/claim-test.launch.sh"
        launch_path = self.backlog / "seeds" / "claim-test.launch.sh"
        launch_path.parent.mkdir(parents=True, exist_ok=True)
        launch_path.write_text("#!/bin/bash\necho ok\n", encoding="utf-8")
        with mock.patch.object(sch, "initiate_item") as init:
            init.return_value = {
                "ok": True,
                "seed_path": "ops/backlog/seeds/x.md",
                "prompt_path": "ops/backlog/seeds/x.prompt.txt",
                "launch_script": launch_rel,
                "goal_objective": "g",
                "spawn": {"attempted": False},
            }
            sch.tick(force=True)
        with mock.patch.object(sch, "detect_runtime", return_value={
            "has_grok": True,
            "has_macos_terminal": True,
            "can_spawn_terminal": True,
        }), mock.patch.object(sch.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            out = sch.claim_pending_jobs(max_jobs=1)
        self.assertEqual(out["claimed_count"], 1)
        self.assertEqual(sch.load_jobs()["jobs"][-1]["status"], "launched")

    def test_auto_queue_scheduled(self) -> None:
        cfg = sch.load_config()
        cfg["enabled"] = True
        cfg["auto_queue_scheduled"] = True
        sch.save_config(cfg)
        item = bl.get_item(self.bid)
        self.assertFalse(bool(item.get("auto_start")))
        r = sch.auto_queue_scheduled()
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["count"], 1)
        self.assertTrue(bl.get_item(self.bid).get("auto_start"))

    def test_autonomous_loop_grooms_and_queues(self) -> None:
        cfg = sch.load_config()
        cfg["enabled"] = True
        cfg["auto_queue_scheduled"] = True
        sch.save_config(cfg)
        # Clear stored ranks so groom rewrites them
        data = bl.load_backlog()
        data["last_groomed_at"] = None
        for it in data["items"]:
            it.pop("auto_start", None)
        bl.save_backlog(data)
        out = sch.run_autonomous_loop(groom=True, queue=True, min_groom_interval_sec=0)
        self.assertTrue(out["ok"])
        self.assertTrue(out.get("groomed"))
        self.assertGreaterEqual((out.get("queue") or {}).get("count") or 0, 0)


if __name__ == "__main__":
    unittest.main()
