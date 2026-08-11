"""Unit tests for parse → volume → strength-trend → recovery (shipped code)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.analytics import (  # noqa: E402
    best_e1rm,
    best_working_weight,
    exercise_strength_slope_lbs_per_day,
    session_volume,
    strength_trend,
    volume_by_session,
)
from rt_dashboard.google_health import (  # noqa: E402
    parse_recorded_sleep_payload,
    parse_recorded_weight_payload,
)
from rt_dashboard.models import (  # noqa: E402
    ExerciseEntry,
    Session,
    SetEntry,
    SleepSample,
    WeightSample,
)
from rt_dashboard.parse import (  # noqa: E402
    append_session_to_markdown,
    format_session_block,
    parse_exercise_line,
    parse_workout_markdown,
)
from rt_dashboard.recovery import compute_recovery_status  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestParseAndVolume(unittest.TestCase):
    def test_parse_multi_set_line_volume(self):
        # 50*1*12 + 45*1*12 + 40*1*12 = 600+540+480 = 1620
        ex = parse_exercise_line(
            "- DB Flat Press: 50 lbs x 1 x 12, 45 lbs x 1 x 12, 40 lbs x 1 x 12"
        )
        self.assertIsNotNone(ex)
        assert ex is not None
        self.assertEqual(ex.name, "DB Flat Press")
        self.assertEqual(len(ex.sets), 3)
        self.assertEqual(ex.volume, 1620.0)

    def test_parse_pr_and_triple(self):
        ex = parse_exercise_line("- Tricep Pushdowns: 47.5 lbs x 3 x 12 (PR!)")
        self.assertIsNotNone(ex)
        assert ex is not None
        self.assertTrue(ex.is_pr)
        self.assertEqual(ex.volume, 47.5 * 3 * 12)
        self.assertAlmostEqual(ex.best_e1rm, 47.5 * (1 + 12 / 30.0))

    def test_fixture_file_sessions_and_known_volume(self):
        text = (FIXTURES / "sample_push.md").read_text(encoding="utf-8")
        sessions = parse_workout_markdown(
            text, session_type="push", source_file="fitness/workouts/push.md"
        )
        self.assertEqual(len(sessions), 2)
        # Newest first
        self.assertEqual(sessions[0].date, "2026-05-26")
        self.assertEqual(sessions[0].session_type, "push")

        # May 26 volume:
        # Flat: 1620
        # Tricep: 47.5*3*12 = 1710
        # Shoulder: 40*1*5 + 35*1*10 + 35*1*8 + 30*1*12 = 200+350+280+360 = 1190
        # Lateral: 15*2*8 + 15*1*6 = 240+90 = 330
        # Total = 1620+1710+1190+330 = 4850
        self.assertEqual(session_volume(sessions[0]), 4850.0)

        # May 20: shoulder 35*3*12=1260; flat 35*3*10=1050; tricep 47.5*3*10=1425 → 3735
        may20 = next(s for s in sessions if s.date == "2026-05-20")
        self.assertEqual(session_volume(may20), 3735.0)

    def test_strength_trend_and_slope(self):
        text = (FIXTURES / "sample_push.md").read_text(encoding="utf-8")
        sessions = parse_workout_markdown(text, session_type="push")
        trend = strength_trend(sessions, "DB Shoulder Press")
        self.assertEqual(len(trend), 2)
        # May 20 best weight 35; May 26 best weight 40
        by_date = {p["date"]: p for p in trend}
        self.assertEqual(by_date["2026-05-20"]["best_working_weight"], 35.0)
        self.assertEqual(by_date["2026-05-26"]["best_working_weight"], 40.0)
        # e1rm May 20: 35*(1+12/30)=35*1.4=49
        self.assertAlmostEqual(by_date["2026-05-20"]["best_e1rm"], 49.0)
        slope = exercise_strength_slope_lbs_per_day(sessions, "DB Shoulder Press")
        self.assertIsNotNone(slope)
        assert slope is not None
        # +5 lbs over 6 days ≈ 0.833 lb/day
        self.assertAlmostEqual(slope, 5.0 / 6.0, places=5)

    def test_serialize_round_trip(self):
        session = Session(
            date="2026-07-10",
            session_type="push",
            exercises=[
                ExerciseEntry(
                    name="DB Flat Press",
                    sets=[SetEntry(weight_lbs=50, sets=3, reps=10)],
                    is_pr=True,
                )
            ],
            notes="test log",
        )
        block = format_session_block(session)
        existing = "# Push Day\n\n"
        updated = append_session_to_markdown(existing, session)
        parsed = parse_workout_markdown(updated, session_type="push")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].date, "2026-07-10")
        self.assertEqual(parsed[0].exercises[0].name, "DB Flat Press")
        self.assertEqual(parsed[0].exercises[0].volume, 50 * 3 * 10)
        self.assertTrue(parsed[0].exercises[0].is_pr)
        self.assertIn("DB Flat Press", block)


class TestRecovery(unittest.TestCase):
    def test_ready_vs_needs_rest_changes_with_inputs(self):
        sessions = [
            Session(
                date="2026-05-26",
                session_type="push",
                exercises=[
                    ExerciseEntry(
                        name="X",
                        sets=[SetEntry(weight_lbs=100, sets=5, reps=10)],
                    )
                ],
            )
        ]
        # volume = 5000 — not extreme
        # Full 7 calendar nights (unlogged nights count as 0h)
        good_sleep = [
            SleepSample(date=f"2026-05-{d:02d}", sleep_hours=8.0 + (d % 3) * 0.1)
            for d in range(20, 27)
        ]
        good_weight = [
            WeightSample(date="2026-05-19", weight_lbs=200.0),
            WeightSample(date="2026-05-26", weight_lbs=200.2),
        ]
        ready = compute_recovery_status(
            weight=good_weight,
            sleep=good_sleep,
            sessions=sessions,
            as_of="2026-05-26",
        )
        self.assertEqual(ready.label, "Ready")
        self.assertGreaterEqual(ready.score, 75)

        bad_sleep = [
            SleepSample(date=f"2026-05-{d:02d}", sleep_hours=4.5)
            for d in range(20, 27)
        ]
        heavy = [
            Session(
                date="2026-05-25",
                session_type="legs",
                exercises=[
                    ExerciseEntry(
                        name="Leg Press",
                        sets=[SetEntry(weight_lbs=300, sets=10, reps=12)],
                    )
                ],
            ),
            Session(
                date="2026-05-26",
                session_type="push",
                exercises=[
                    ExerciseEntry(
                        name="Bench",
                        sets=[SetEntry(weight_lbs=200, sets=10, reps=10)],
                    )
                ],
            ),
        ]
        # volume huge + low sleep + weight drop
        drop_weight = [
            WeightSample(date="2026-05-19", weight_lbs=205.0),
            WeightSample(date="2026-05-26", weight_lbs=201.0),
        ]
        rest = compute_recovery_status(
            weight=drop_weight,
            sleep=bad_sleep,
            sessions=heavy,
            as_of="2026-05-26",
            high_volume_threshold=10000.0,
        )
        self.assertIn(rest.label, ("Needs Rest", "Caution"))
        self.assertLess(rest.score, ready.score)
        self.assertTrue(rest.reasons)
        self.assertNotEqual(rest.label, ready.label)

    def test_empty_health_still_returns_status(self):
        status = compute_recovery_status(weight=[], sleep=[], sessions=[])
        self.assertIn(status.label, ("Ready", "Moderate", "Caution", "Needs Rest"))
        self.assertTrue(status.reasons)

    def test_as_of_defaults_to_today_not_last_session_date(self):
        """Stale lift history must not inflate 'last 7d' volume when as_of is omitted."""
        from datetime import datetime

        # Dense May sessions with huge volume — if as_of wrongly used max(session.date),
        # recovery would report "Very high training volume last 7d".
        heavy = [
            Session(
                date="2026-05-25",
                session_type="legs",
                exercises=[
                    ExerciseEntry(
                        name="Leg Press",
                        sets=[SetEntry(weight_lbs=300, sets=10, reps=12)],
                    )
                ],
            ),
            Session(
                date="2026-05-26",
                session_type="push",
                exercises=[
                    ExerciseEntry(
                        name="Bench",
                        sets=[SetEntry(weight_lbs=200, sets=10, reps=10)],
                    )
                ],
            ),
            Session(
                date="2026-05-27",
                session_type="pull",
                exercises=[
                    ExerciseEntry(
                        name="Row",
                        sets=[SetEntry(weight_lbs=150, sets=10, reps=10)],
                    )
                ],
            ),
        ]
        weights = [
            WeightSample(date="2026-05-20", weight_lbs=200.0),
            WeightSample(date="2026-05-27", weight_lbs=200.2),
        ]
        sleep = [
            SleepSample(date="2026-05-26", sleep_hours=7.5),
            SleepSample(date="2026-05-27", sleep_hours=7.5),
        ]
        # Recovery as_of is local civil day (not UTC) — match that clock
        from rt_dashboard.timeutil import local_today_iso

        today = local_today_iso()
        # Only run the "stale history" assertion when today is outside the May window
        if today <= "2026-05-27":
            self.skipTest("today still within fixture window")

        status = compute_recovery_status(
            weight=weights, sleep=sleep, sessions=heavy
        )
        self.assertEqual(status.inputs["as_of"], today)
        self.assertEqual(status.inputs["training_volume_7d"], 0.0)
        self.assertTrue(
            any("No logged training volume" in r for r in status.reasons),
            msg=status.reasons,
        )
        self.assertFalse(
            any("Very high training volume" in r for r in status.reasons),
            msg=status.reasons,
        )

        # Explicit as_of still evaluates volume for that historical window
        hist = compute_recovery_status(
            weight=weights,
            sleep=sleep,
            sessions=heavy,
            as_of="2026-05-27",
            high_volume_threshold=10000.0,
        )
        self.assertEqual(hist.inputs["as_of"], "2026-05-27")
        self.assertGreater(hist.inputs["training_volume_7d"], 0)
        self.assertTrue(
            any("volume last 7d" in r for r in hist.reasons),
            msg=hist.reasons,
        )


class TestGoogleParsers(unittest.TestCase):
    def test_weight_aggregate_fixture(self):
        payload = json.loads(
            (FIXTURES / "google_weight_aggregate.json").read_text(encoding="utf-8")
        )
        samples = parse_recorded_weight_payload(payload)
        self.assertEqual(len(samples), 2)
        # 90.7185 kg * 2.2046226218 ≈ 200.0
        self.assertAlmostEqual(samples[0].weight_lbs, 90.7185 * 2.2046226218, places=1)
        self.assertAlmostEqual(samples[1].weight_lbs, 90.265 * 2.2046226218, places=1)

    def test_sleep_sessions_fixture(self):
        payload = json.loads(
            (FIXTURES / "google_sleep_sessions.json").read_text(encoding="utf-8")
        )
        samples = parse_recorded_sleep_payload(payload)
        self.assertEqual(len(samples), 2)
        # 1717227000000 - 1717200000000 = 27000000 ms = 7.5 h
        self.assertAlmostEqual(samples[0].sleep_hours, 7.5)
        # 1717311600000 - 1717286400000 = 25200000 ms = 7.0 h
        self.assertAlmostEqual(samples[1].sleep_hours, 7.0)


class TestRealWorkspaceParse(unittest.TestCase):
    def test_real_push_pull_legs_nonempty(self):
        ws = ROOT.parent
        for name in ("push", "pull", "legs"):
            path = ws / "fitness" / "workouts" / f"{name}.md"
            if not path.exists():
                self.skipTest(f"missing {path}")
            sessions = parse_workout_markdown(
                path.read_text(encoding="utf-8"),
                session_type=name,
                source_file=str(path),
            )
            self.assertGreater(len(sessions), 5, msg=name)
            self.assertGreater(session_volume(sessions[0]), 0)


if __name__ == "__main__":
    unittest.main()
