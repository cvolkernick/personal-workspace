"""Swarm ROI telemetry (#773) — never silently estimates."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

import swarm_roi as sr  # noqa: E402

GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env=GIT_ENV,
    )
    return (proc.stdout or "").strip()


def _pr(**extra):
    base = {
        "number": 12,
        "title": "feat: thing",
        "body": "Fixes #99",
        "html_url": "https://github.com/cvolkernick/personal-workspace/pull/12",
        "created_at": "2026-09-16T10:00:00Z",
        "merged_at": "2026-09-16T12:00:00Z",
        "merge_commit_sha": "abc123",
        "labels": [],
        "base": {"repo": {"full_name": "cvolkernick/personal-workspace"}},
    }
    base.update(extra)
    return base


class TestClassifyAndSpend(unittest.TestCase):
    def test_spend_without_payload_is_unavailable(self) -> None:
        m = sr.spend_metric(None)
        self.assertEqual(m["status"], "unavailable")
        self.assertIn("does not expose", m["reason"])
        self.assertNotIn("tokens", m)

    def test_spend_missing_tokens_not_estimated(self) -> None:
        m = sr.spend_metric({"source": "grok", "reason": "session has no usage field"})
        self.assertEqual(m["status"], "unavailable")
        self.assertIn("session has no usage field", m["reason"])

    def test_spend_uses_payload_tokens(self) -> None:
        m = sr.spend_metric({"tokens": 42000, "source": "grok-build"})
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["tokens"], 42000)

    def test_parallel_from_two_agent_trailers(self) -> None:
        msgs = [
            "feat\n\nCo-authored-by: Forge <a@x>\nCo-authored-by: Frankenfit <b@x>\n"
        ]
        c = sr.classify_parallelism(
            labels=[], commit_messages=msgs, known_agents=sr.DEFAULT_KNOWN_AGENTS
        )
        self.assertEqual(c["kind"], "parallel")
        self.assertEqual(c["evidence"], "coauthored_by")
        self.assertEqual(c["agents"], ["Forge", "Frankenfit"])

    def test_single_agent_from_one_trailer(self) -> None:
        msgs = ["feat\n\nCo-authored-by: Forge <a@x>\n"]
        c = sr.classify_parallelism(
            labels=[], commit_messages=msgs, known_agents=sr.DEFAULT_KNOWN_AGENTS
        )
        self.assertEqual(c["kind"], "single_agent")
        self.assertEqual(c["agents"], ["Forge"])

    def test_no_evidence_is_unavailable_not_human(self) -> None:
        c = sr.classify_parallelism(
            labels=[], commit_messages=["feat: no trailers"], known_agents=sr.DEFAULT_KNOWN_AGENTS
        )
        self.assertEqual(c["kind"], "unavailable")
        self.assertIn("Not classified as human", c["reason"])

    def test_label_overrides(self) -> None:
        c = sr.classify_parallelism(
            labels=[{"name": "parallel-agent"}],
            commit_messages=["feat"],
            known_agents=sr.DEFAULT_KNOWN_AGENTS,
        )
        self.assertEqual(c["kind"], "parallel")
        self.assertEqual(c["evidence"], "label")


class TestReviewAndOutcome(unittest.TestCase):
    def test_review_rounds_count_approve_and_changes(self) -> None:
        pr = _pr()
        reviews = [
            {"state": "COMMENTED"},
            {"state": "CHANGES_REQUESTED"},
            {"state": "APPROVED"},
        ]
        comments = [
            {"user": {"login": "alice"}},
            {"user": {"login": "swarm-roi[bot]"}},
        ]
        m = sr.review_burden_metric(pr, reviews, comments)
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["review_rounds"], 2)
        self.assertEqual(m["review_comment_count"], 1)
        self.assertEqual(m["open_to_merge_seconds"], 2 * 3600)

    def test_outcome_closed_as_done(self) -> None:
        m = sr.outcome_metric(
            [{"number": 99, "state": "closed", "state_reason": "completed"}]
        )
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["kind"], "closed_as_done")

    def test_outcome_no_linked_issue_unavailable(self) -> None:
        m = sr.outcome_metric([])
        self.assertEqual(m["status"], "unavailable")
        self.assertIn("no linked", m["reason"])

    def test_issue_refs_from_body(self) -> None:
        self.assertEqual(sr.parse_issue_refs("Fixes #773", "also Closes #12"), [773, 12])


class TestReworkGit(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="roi-")
        self.repo = Path(self._td.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "master")
        (self.repo / "app.py").write_text("a\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "init")
        self.t0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_window_open_before_seven_days(self) -> None:
        m = sr.rework_metric(
            self.repo,
            merge_at=self.t0,
            now=self.t0 + timedelta(days=3),
            pr_paths=["app.py"],
        )
        self.assertEqual(m["status"], "window_open")
        self.assertIsNone(m["followup_commits"])

    def test_followup_on_same_path_counts(self) -> None:
        (self.repo / "app.py").write_text("b\n", encoding="utf-8")
        env = {**GIT_ENV, "GIT_AUTHOR_DATE": "2026-09-03T12:00:00", "GIT_COMMITTER_DATE": "2026-09-03T12:00:00"}
        subprocess.run(
            ["git", "add", "app.py"],
            cwd=str(self.repo),
            check=True,
            env=env,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "follow-up"],
            cwd=str(self.repo),
            check=True,
            env=env,
            capture_output=True,
        )
        m = sr.rework_metric(
            self.repo,
            merge_at=self.t0,
            now=self.t0 + timedelta(days=8),
            pr_paths=["app.py"],
        )
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["followup_commits"], 1)
        self.assertEqual(m["reverts"], 0)

    def test_swarm_roi_only_commit_ignored(self) -> None:
        ledger = self.repo / "ops" / "swarm-roi" / "records"
        ledger.mkdir(parents=True)
        (ledger / "pr-1.json").write_text("{}\n", encoding="utf-8")
        env = {**GIT_ENV, "GIT_AUTHOR_DATE": "2026-09-03T12:00:00", "GIT_COMMITTER_DATE": "2026-09-03T12:00:00"}
        subprocess.run(
            ["git", "add", "ops/swarm-roi/records/pr-1.json"],
            cwd=str(self.repo),
            check=True,
            env=env,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "chore(swarm-roi): record"],
            cwd=str(self.repo),
            check=True,
            env=env,
            capture_output=True,
        )
        m = sr.rework_metric(
            self.repo,
            merge_at=self.t0,
            now=self.t0 + timedelta(days=8),
            pr_paths=["app.py"],
        )
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["followup_commits"], 0)


class TestBuildAndRollup(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="roi-b-")
        self.repo = Path(self._td.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "master")
        (self.repo / "app.py").write_text("a\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "init")

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_build_record_four_metrics_or_unavailable(self) -> None:
        now = datetime(2026, 9, 16, 13, 0, tzinfo=timezone.utc)
        rec = sr.build_record(
            pr=_pr(),
            reviews=[{"state": "APPROVED"}],
            comments=[],
            commit_messages=["feat\n"],
            pr_paths=["app.py"],
            issues=[{"number": 99, "state": "closed", "state_reason": "completed"}],
            repo_path=self.repo,
            now=now,
        )
        metrics = rec["metrics"]
        self.assertEqual(set(metrics), {"spend", "rework", "review_burden", "outcome"})
        self.assertEqual(metrics["spend"]["status"], "unavailable")
        self.assertTrue(metrics["spend"]["reason"])
        self.assertEqual(metrics["rework"]["status"], "window_open")
        self.assertEqual(metrics["review_burden"]["status"], "ok")
        self.assertEqual(metrics["outcome"]["kind"], "closed_as_done")
        self.assertEqual(rec["parallelism"]["kind"], "unavailable")
        self.assertEqual(rec["pr_paths"], ["app.py"])
        self.assertEqual(rec["linked_issues"], [99])

    def test_weekly_rollup_splits_and_skips_unavailable_spend(self) -> None:
        week = datetime(2026, 9, 14, tzinfo=timezone.utc)  # Monday
        recs = [
            {
                "merged_at": "2026-09-16T12:00:00Z",
                "parallelism": {"kind": "parallel"},
                "metrics": {
                    "spend": {"status": "unavailable", "reason": "no api"},
                    "rework": {"status": "ok", "followup_commits": 1, "reverts": 0, "reopened_issues": 0},
                    "review_burden": {"status": "ok", "review_rounds": 2},
                    "outcome": {"status": "ok", "kind": "closed_as_done"},
                },
            },
            {
                "merged_at": "2026-09-16T13:00:00Z",
                "parallelism": {"kind": "single_agent"},
                "metrics": {
                    "spend": {"status": "ok", "tokens": 1000},
                    "rework": {"status": "ok", "followup_commits": 0, "reverts": 0, "reopened_issues": 0},
                    "review_burden": {"status": "ok", "review_rounds": 1},
                    "outcome": {"status": "ok", "kind": "closed_as_done"},
                },
            },
            {
                "merged_at": "2026-09-01T00:00:00Z",
                "parallelism": {"kind": "human"},
                "metrics": {
                    "spend": {"status": "ok", "tokens": 9},
                    "rework": {"status": "ok", "followup_commits": 0, "reverts": 0, "reopened_issues": 0},
                    "review_burden": {"status": "ok", "review_rounds": 9},
                    "outcome": {"status": "ok", "kind": "closed_as_done"},
                },
            },
        ]
        roll = sr.weekly_rollup(
            recs, week_start=week, week_end=week + timedelta(days=7)
        )
        self.assertEqual(roll["pr_count"], 2)
        self.assertEqual(roll["by_parallelism"]["parallel"]["count"], 1)
        self.assertEqual(roll["by_parallelism"]["single_agent"]["count"], 1)
        self.assertEqual(roll["by_parallelism"]["human"]["count"], 0)
        self.assertEqual(
            roll["by_parallelism"]["parallel"]["token_spend"]["status"], "unavailable"
        )
        self.assertEqual(
            roll["by_parallelism"]["single_agent"]["token_spend"]["median_tokens"], 1000
        )
        self.assertEqual(roll["overall"]["median_review_rounds"]["value"], 1.5)
        self.assertEqual(roll["overall"]["rework_rate"]["n_with_followup"], 1)

    def test_missing_merged_prs_after_cutoff(self) -> None:
        since = datetime(2026, 9, 16, tzinfo=timezone.utc)
        pulls = [
            {"number": 1, "merged_at": "2026-09-15T23:00:00Z"},
            {"number": 2, "merged_at": "2026-09-16T01:00:00Z"},
            {"number": 3, "merged_at": None},
            {"number": 4, "merged_at": "2026-09-17T00:00:00Z"},
        ]
        missing = sr.missing_merged_prs(pulls, since=since, have=[2])
        self.assertEqual(missing, [4])

    def test_write_and_load_roundtrip(self) -> None:
        store = Path(self._td.name) / "records"
        rec = sr.build_record(
            pr=_pr(),
            reviews=[],
            comments=[],
            commit_messages=["feat\n\nCo-authored-by: Forge <a@x>\n"],
            pr_paths=["app.py"],
            issues=[{"number": 99, "state": "closed", "state_reason": "completed"}],
            repo_path=self.repo,
            now=datetime(2026, 9, 16, 13, 0, tzinfo=timezone.utc),
        )
        path = sr.write_record(store, rec)
        self.assertTrue(path.is_file())
        loaded = sr.load_records(store)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["parallelism"]["kind"], "single_agent")


if __name__ == "__main__":
    unittest.main()
