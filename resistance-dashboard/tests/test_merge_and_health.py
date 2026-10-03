"""Tests for session merge (local+remote) and health metrics recovery inputs."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.github_client import GitHubLiftClient  # noqa: E402
from rt_dashboard.health_metrics_store import (  # noqa: E402
    backfill_weights_from_fitbit_report,
    parse_fitbit_report_markdown,
    reading_to_lbs,
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


def _resolve_without_github(google, workspace_dir=""):
    """Resolve health from the temp workspace only.

    ``resolve_health_snapshot`` prefers the live GitHub copy of
    ``fitness/data/health-metrics.json``. Tests must not depend on master.
    """
    with mock.patch(
        "rt_dashboard.health_metrics_store.fetch_metrics_from_github",
        return_value=None,
    ):
        return resolve_health_snapshot(google, workspace_dir=workspace_dir)


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
        # Report cells are kilograms (83.1 kg) — stored as true pounds (~183)
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
        resolved = _resolve_without_github(google, workspace_dir="")
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
            resolved = _resolve_without_github(
                empty_google, workspace_dir=td
            )
            self.assertGreater(len(resolved.weight), 0)
            self.assertGreater(len(resolved.sleep), 0)
            self.assertTrue(
                all(sample.source == "fitbit_report" for sample in resolved.weight)
            )
            self.assertTrue(
                all(sample.source == "fitbit_report" for sample in resolved.sleep)
            )
            status = compute_recovery_status(
                weight=resolved.weight,
                sleep=resolved.sleep,
                sessions=[],
                as_of=resolved.weight[-1].date,
            )
            self.assertIsNotNone(status.inputs.get("latest_weight_lbs"))
            self.assertIsNotNone(status.inputs.get("avg_sleep_hours_7d"))

    def test_fitbit_report_kg_converts_and_lbs_stays(self):
        kg = parse_fitbit_report_markdown("| 06-01 | 80.0 kg |\n")[0]
        lbs = parse_fitbit_report_markdown("| 06-01 | 180.5 lbs |\n")[0]
        self.assertEqual(len(kg), 1)
        self.assertEqual(len(lbs), 1)
        self.assertAlmostEqual(kg[0].weight_lbs, reading_to_lbs(80.0, "kg"))
        self.assertGreater(kg[0].weight_lbs, 150.0)
        self.assertAlmostEqual(lbs[0].weight_lbs, 180.5)
        self.assertEqual(lbs[0].weight_lbs, reading_to_lbs(180.5, "lbs"))

    def test_backfill_fills_holes_without_overwrite_or_duplicates(self):
        existing = [
            WeightSample(date="2026-05-19", weight_lbs=183.2, source="google_health")
        ]
        report = "| 05-19 | 83.1 kg |\n| 05-20 | 84.0 kg |\n"
        once = backfill_weights_from_fitbit_report(existing, report)
        twice = backfill_weights_from_fitbit_report(once, report)
        self.assertEqual(
            [(w.date, w.weight_lbs, w.source) for w in once],
            [(w.date, w.weight_lbs, w.source) for w in twice],
        )
        self.assertEqual(len(twice), 2)
        may19 = next(w for w in twice if w.date == "2026-05-19")
        may20 = next(w for w in twice if w.date == "2026-05-20")
        self.assertEqual(may19.source, "google_health")
        self.assertEqual(may19.weight_lbs, 183.2)
        self.assertAlmostEqual(may20.weight_lbs, reading_to_lbs(84.0, "kg"))

    def test_repo_weight_history_covers_may19_through_jul7(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "fitness"
            / "data"
            / "health-metrics.json"
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["weight"]
        dates = [row["date"] for row in rows]
        self.assertEqual(len(dates), len(set(dates)))
        by = {row["date"]: row for row in rows}
        for day in ("2026-05-19", "2026-06-15", "2026-07-07"):
            self.assertIn(day, by)
            self.assertGreater(by[day]["weight_lbs"], 150.0)
            self.assertLess(by[day]["weight_lbs"], 220.0)
        self.assertEqual(by["2026-07-07"]["weight_lbs"], 179.0)
        self.assertEqual(by["2026-07-07"]["source"], "google_health")

    def test_resolve_keeps_google_date_and_fills_repo_hole(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "fitness" / "data"
            dest.mkdir(parents=True)
            payload = {
                "source_note": "test",
                "weight": [
                    {
                        "date": "2026-05-20",
                        "weight_lbs": 185.4,
                        "source": "google_health",
                    }
                ],
                "sleep": [],
            }
            (dest / "health-metrics.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
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
            resolved = _resolve_without_github(google, workspace_dir=td)
            by = {w.date: w for w in resolved.weight}
            self.assertEqual(by["2026-07-01"].weight_lbs, 200.0)
            self.assertEqual(by["2026-07-01"].source, "google_fit")
            self.assertEqual(by["2026-05-20"].weight_lbs, 185.4)
            again = _resolve_without_github(resolved, workspace_dir=td)
            self.assertEqual(
                sorted(w.date for w in again.weight),
                sorted(w.date for w in resolved.weight),
            )


if __name__ == "__main__":
    unittest.main()
