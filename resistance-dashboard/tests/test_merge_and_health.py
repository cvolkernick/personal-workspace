"""Tests for session merge (local+remote) and health metrics recovery inputs."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.github_client import GitHubLiftClient  # noqa: E402
from rt_dashboard.health_metrics_store import (  # noqa: E402
    parse_fitbit_report_markdown,
    resolve_health_snapshot,
)
from rt_dashboard.models import (  # noqa: E402
    ExerciseEntry,
    HealthSnapshot,
    Session,
    SetEntry,
    SleepSample,
    WeightSample,
)
from rt_dashboard.parse import parse_workout_markdown  # noqa: E402
from rt_dashboard.recovery import compute_recovery_status  # noqa: E402
from rt_dashboard.session_merge import merge_sessions  # noqa: E402


class TestSessionMerge(unittest.TestCase):
    def test_local_session_survives_merge_with_remote(self):
        remote = [
            Session(
                date="2026-05-26",
                session_type="push",
                exercises=[
                    ExerciseEntry(
                        name="DB Flat Press",
                        sets=[SetEntry(50, 3, 10)],
                    )
                ],
            )
        ]
        local = remote + [
            Session(
                date="2026-07-10",
                session_type="push",
                exercises=[
                    ExerciseEntry(
                        name="Local Only Press",
                        sets=[SetEntry(40, 3, 8)],
                    )
                ],
            )
        ]
        merged = merge_sessions(local, remote, prefer_first=True)
        names = {
            e.name
            for s in merged
            for e in s.exercises
            if s.date == "2026-07-10"
        }
        self.assertIn("Local Only Press", names)
        self.assertEqual(len(merged), 2)

    def test_local_write_then_merged_pull_includes_session(self):
        """Simulate the no-token path: write local, merge with remote-shaped history."""
        with tempfile.TemporaryDirectory() as td:
            seed = (
                "# Push Day\n\n"
                "## May 26, 2026 - Session Complete\n"
                "- DB Flat Press: 50 lbs x 3 x 10\n"
            )
            path = Path(td) / "fitness" / "workouts" / "push.md"
            path.parent.mkdir(parents=True)
            path.write_text(seed, encoding="utf-8")
            (Path(td) / "fitness" / "workouts" / "pull.md").write_text(
                "# Pull Day\n", encoding="utf-8"
            )
            (Path(td) / "fitness" / "workouts" / "legs.md").write_text(
                "# Legs Day\n", encoding="utf-8"
            )

            client = GitHubLiftClient(prefer_local=True, local_fallback_dir=td)
            session = Session(
                date="2026-07-10",
                session_type="push",
                exercises=[
                    ExerciseEntry(
                        name="Merge Probe Press",
                        sets=[SetEntry(33, 2, 5)],
                    )
                ],
            )
            result = client.append_workout_safe(session)
            self.assertTrue(
                result["verified_on_readback"],
                msg=result.get("readback_error"),
            )
            disk = (Path(td) / "fitness" / "workouts" / "push.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Merge Probe Press", disk)
            after_local = client.pull_sessions()
            remote = parse_workout_markdown(seed, session_type="push")
            merged = merge_sessions(after_local, remote, prefer_first=True)
            self.assertTrue(
                any(
                    e.name == "Merge Probe Press"
                    for s in merged
                    for e in s.exercises
                )
            )


class TestHealthAndRecovery(unittest.TestCase):
    def test_fitbit_report_parse_and_recovery_uses_weight_sleep(self):
        report = (
            Path(__file__).resolve().parents[2]
            / "fitness"
            / "data"
            / "fitbit-report-may2026.md"
        )
        if not report.exists():
            self.skipTest("fitbit report missing")
        weights, sleep = parse_fitbit_report_markdown(
            report.read_text(encoding="utf-8")
        )
        self.assertGreaterEqual(len(weights), 7)
        self.assertGreaterEqual(len(sleep), 1)
        # Report labeled kg as "lbs" (e.g. 83.1) — must store true pounds (~183)
        self.assertGreater(weights[-1].weight_lbs, 150.0)
        self.assertLess(weights[-1].weight_lbs, 220.0)
        self.assertAlmostEqual(weights[-1].weight_lbs, 83.1 * 2.2046226218, places=1)
        status = compute_recovery_status(
            weight=weights,
            sleep=sleep,
            sessions=[],
            as_of=weights[-1].date,
        )
        self.assertIsNotNone(status.inputs.get("avg_sleep_hours_7d"))
        self.assertIsNotNone(status.inputs.get("latest_weight_lbs"))
        self.assertAlmostEqual(
            status.inputs["latest_weight_lbs"], weights[-1].weight_lbs
        )
        self.assertTrue(status.reasons)
        # Recovery latest weight must not look like raw kg (~80s)
        self.assertGreater(status.inputs["latest_weight_lbs"], 150.0)

    def test_resolve_health_prefers_google_when_present(self):
        google = HealthSnapshot(
            weight=[
                WeightSample(
                    date="2026-07-01", weight_lbs=200.0, source="google_fit"
                )
            ],
            sleep=[
                SleepSample(
                    date="2026-07-01", sleep_hours=8.0, source="google_fit"
                )
            ],
        )
        resolved = resolve_health_snapshot(google, workspace_dir="")
        self.assertEqual(resolved.weight[0].source, "google_fit")
        self.assertEqual(resolved.sleep[0].sleep_hours, 8.0)

    def test_resolve_health_falls_back_to_local_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(__file__).resolve().parents[2]
            report = ws / "fitness" / "data" / "fitbit-report-may2026.md"
            if not report.exists():
                self.skipTest("no fitbit report")
            dest = Path(td) / "fitness" / "data"
            dest.mkdir(parents=True)
            (dest / "fitbit-report-may2026.md").write_text(
                report.read_text(encoding="utf-8"), encoding="utf-8"
            )
            empty_google = HealthSnapshot(
                error="Missing Google OAuth credentials"
            )
            resolved = resolve_health_snapshot(
                empty_google, workspace_dir=td
            )
            self.assertGreater(len(resolved.weight), 0)
            self.assertGreater(len(resolved.sleep), 0)
            status = compute_recovery_status(
                weight=resolved.weight,
                sleep=resolved.sleep,
                sessions=[],
            )
            self.assertIsNotNone(status.inputs.get("latest_weight_lbs"))
            self.assertIsNotNone(status.inputs.get("avg_sleep_hours_7d"))


if __name__ == "__main__":
    unittest.main()
