"""#660 resting heart rate → recovery/readiness signal.

Parse daily-resting-heart-rate only. Silent skip when missing. 7/14d median
baseline. Under-recovered (+5) → easy intensity; +8 → rest_gate even if sparse.
HRV / VO2 / SpO2 / respiratory rate are out of scope.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from rt_dashboard.coach import compute_weekly_review
from rt_dashboard.google_health import (
    GoogleHealthClient,
    GoogleHealthError,
    parse_daily_resting_heart_rate_points,
)
from rt_dashboard.models import (
    RecoveryStatus,
    RestingHeartRateDay,
    SleepSample,
    WeightSample,
)
from rt_dashboard.recovery import (
    RHR_REST_BPM,
    RHR_UNDER_RECOVERED_BPM,
    compute_recovery_status,
    rhr_readiness,
)
from rt_dashboard.workout_planner import prescribe
from rt_dashboard.workout_store import rest_gate

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AS_OF = "2026-09-16"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _series(as_of: str, prior_bpm, today_bpm=None, n_prior=14):
    end = datetime.strptime(as_of, "%Y-%m-%d")
    rows = []
    if isinstance(prior_bpm, (int, float)):
        vals = [float(prior_bpm)] * n_prior
    else:
        vals = [float(v) for v in prior_bpm]
        n_prior = len(vals)
    for i, bpm in enumerate(vals, start=1):
        d = (end - timedelta(days=n_prior - i + 1)).strftime("%Y-%m-%d")
        rows.append(RestingHeartRateDay(date=d, bpm=bpm))
    if today_bpm is not None:
        rows.append(RestingHeartRateDay(date=as_of, bpm=float(today_bpm)))
    return rows


class TestParseDailyRestingHeartRate(unittest.TestCase):
    def test_present_list_parses_days(self):
        days = parse_daily_resting_heart_rate_points(
            _load("rhr_list_present.json"), days=0
        )
        self.assertEqual([d.date for d in days], ["2026-09-10", "2026-09-11", "2026-09-12"])
        self.assertEqual(days[0].bpm, 62.0)
        self.assertEqual(days[1].bpm, 63.0)
        self.assertEqual(days[2].bpm, 64.4)
        self.assertEqual(days[0].source, "google_health")

    def test_empty_list_is_honest_empty(self):
        self.assertEqual(parse_daily_resting_heart_rate_points(_load("rhr_list_empty.json")), [])

    def test_missing_payload_is_honest_empty(self):
        self.assertEqual(parse_daily_resting_heart_rate_points({}), [])
        self.assertEqual(parse_daily_resting_heart_rate_points(None), [])  # type: ignore[arg-type]
        self.assertEqual(parse_daily_resting_heart_rate_points([]), [])  # type: ignore[arg-type]

    def test_does_not_read_hrv_vo2_spo2_or_intraday_hr(self):
        payload = {
            "dataPoints": [
                {
                    "heartRate": {"beatsPerMinute": "88", "sampleTime": {}},
                    "dailyHeartRateVariability": {
                        "date": {"year": 2026, "month": 9, "day": 10},
                        "averageHeartRateVariabilityMilliseconds": 40,
                    },
                    "dailyVo2Max": {
                        "date": {"year": 2026, "month": 9, "day": 10},
                        "vo2Max": 45,
                    },
                    "dailyOxygenSaturation": {
                        "date": {"year": 2026, "month": 9, "day": 10},
                        "percentage": 98,
                    },
                    "dailyRespiratoryRate": {
                        "date": {"year": 2026, "month": 9, "day": 10},
                        "breathsPerMinute": 14,
                    },
                }
            ]
        }
        self.assertEqual(parse_daily_resting_heart_rate_points(payload), [])

    def test_zero_or_missing_bpm_skipped(self):
        payload = {
            "dataPoints": [
                {
                    "dailyRestingHeartRate": {
                        "date": {"year": 2026, "month": 9, "day": 10},
                        "beatsPerMinute": "0",
                    }
                },
                {
                    "dailyRestingHeartRate": {
                        "date": {"year": 2026, "month": 9, "day": 11},
                    }
                },
            ]
        }
        self.assertEqual(parse_daily_resting_heart_rate_points(payload), [])


class TestFetchRestingHeartRate(unittest.TestCase):
    def test_lists_daily_resting_heart_rate_type(self):
        client = GoogleHealthClient(access_token="x")
        captured: dict = {}

        def fake_pages(data_type, max_pages=10, *, until_date=None):
            captured["data_type"] = data_type
            captured["until_date"] = until_date
            today = datetime.now().astimezone().date()
            return {
                "dataPoints": [
                    {
                        "dailyRestingHeartRate": {
                            "date": {
                                "year": today.year,
                                "month": today.month,
                                "day": today.day,
                            },
                            "beatsPerMinute": "61",
                        }
                    }
                ]
            }

        client._paginate_data_points = fake_pages  # type: ignore[method-assign]
        days = client.fetch_resting_heart_rate(days=90)
        self.assertEqual(captured["data_type"], "daily-resting-heart-rate")
        self.assertGreaterEqual(len(days), 1)

    def test_google_error_is_silent_empty(self):
        client = GoogleHealthClient(access_token="x")

        def boom(data_type, max_pages=10, *, until_date=None):
            raise GoogleHealthError("nope", status=403)

        client._paginate_data_points = boom  # type: ignore[method-assign]
        self.assertEqual(client.fetch_resting_heart_rate(days=14), [])


class TestRhrReadiness(unittest.TestCase):
    def test_empty_skips(self):
        sig = rhr_readiness([], AS_OF)
        self.assertTrue(sig["skipped"])
        self.assertFalse(sig["under_recovered"])
        self.assertIsNone(sig["today_bpm"])

    def test_missing_today_skips(self):
        rows = _series(AS_OF, 60, today_bpm=None, n_prior=14)
        sig = rhr_readiness(rows, AS_OF)
        self.assertTrue(sig["skipped"])
        self.assertIsNone(sig["today_bpm"])

    def test_thin_window_skips_without_inventing(self):
        rows = _series(AS_OF, 60, today_bpm=70, n_prior=3)
        sig = rhr_readiness(rows, AS_OF)
        self.assertTrue(sig["skipped"])
        self.assertEqual(sig["today_bpm"], 70.0)
        self.assertFalse(sig["under_recovered"])

    def test_14d_median_when_enough_samples(self):
        rows = _series(AS_OF, 60, today_bpm=60, n_prior=14)
        sig = rhr_readiness(rows, AS_OF)
        self.assertFalse(sig["skipped"])
        self.assertEqual(sig["baseline_days"], 14)
        self.assertEqual(sig["baseline_bpm"], 60.0)
        self.assertFalse(sig["under_recovered"])

    def test_7d_fallback_when_14d_thin(self):
        rows = _series(AS_OF, 58, today_bpm=58, n_prior=6)
        sig = rhr_readiness(rows, AS_OF)
        self.assertFalse(sig["skipped"])
        self.assertEqual(sig["baseline_days"], 7)
        self.assertEqual(sig["baseline_bpm"], 58.0)

    def test_plus_5_is_under_recovered(self):
        rows = _series(AS_OF, 60, today_bpm=60 + RHR_UNDER_RECOVERED_BPM, n_prior=14)
        sig = rhr_readiness(rows, AS_OF)
        self.assertTrue(sig["under_recovered"])
        self.assertEqual(sig["delta_bpm"], 5.0)

    def test_plus_4_is_not_under_recovered(self):
        rows = _series(AS_OF, 60, today_bpm=64, n_prior=14)
        sig = rhr_readiness(rows, AS_OF)
        self.assertFalse(sig["under_recovered"])
        self.assertEqual(sig["delta_bpm"], 4.0)


class TestRecoveryUsesRhr(unittest.TestCase):
    def test_silent_skip_does_not_mention_rhr(self):
        status = compute_recovery_status(
            weight=[],
            sleep=[SleepSample(date=AS_OF, sleep_hours=8.0)],
            sessions=[],
            as_of=AS_OF,
            rhr=[],
        )
        self.assertTrue(status.inputs["rhr_skipped"])
        self.assertFalse(status.inputs["rhr_under_recovered"])
        self.assertFalse(any("RHR" in r for r in status.reasons))

    def test_under_recovered_drops_score_and_reasons(self):
        good = compute_recovery_status(
            weight=[
                WeightSample(date="2026-09-09", weight_lbs=200.0),
                WeightSample(date=AS_OF, weight_lbs=200.1),
            ],
            sleep=[SleepSample(date=AS_OF, sleep_hours=8.0)],
            sessions=[],
            as_of=AS_OF,
            rhr=_series(AS_OF, 60, today_bpm=60, n_prior=14),
        )
        flagged = compute_recovery_status(
            weight=[
                WeightSample(date="2026-09-09", weight_lbs=200.0),
                WeightSample(date=AS_OF, weight_lbs=200.1),
            ],
            sleep=[SleepSample(date=AS_OF, sleep_hours=8.0)],
            sessions=[],
            as_of=AS_OF,
            rhr=_series(AS_OF, 60, today_bpm=66, n_prior=14),
        )
        self.assertTrue(flagged.inputs["rhr_under_recovered"])
        self.assertLess(flagged.score, good.score)
        self.assertTrue(any("under-recovered" in r for r in flagged.reasons))
        self.assertFalse(any("RHR" in r for r in good.reasons))


class TestRestGateRhr(unittest.TestCase):
    def test_plus_5_does_not_force_rest_when_score_ok(self):
        gate = rest_gate(
            {"rest_if_recovery_below": 40},
            {
                "score": 70,
                "sparse": False,
                "inputs": {
                    "rhr_under_recovered": True,
                    "rhr_delta_bpm": 5.0,
                    "rhr_today_bpm": 65,
                    "rhr_baseline_bpm": 60,
                    "rhr_baseline_days": 14,
                },
            },
        )
        self.assertFalse(gate["force_rest"])
        self.assertTrue(gate["rhr_under_recovered"])

    def test_plus_8_forces_rest_even_when_sparse(self):
        gate = rest_gate(
            {"rest_if_recovery_below": 40},
            {
                "score": 70,
                "sparse": True,
                "inputs": {
                    "rhr_under_recovered": True,
                    "rhr_delta_bpm": RHR_REST_BPM,
                    "rhr_today_bpm": 68,
                    "rhr_baseline_bpm": 60,
                    "rhr_baseline_days": 14,
                },
            },
        )
        self.assertTrue(gate["force_rest"])
        self.assertIn("under-recovered", gate["reason"])


class TestPrescribeRhrIntensity(unittest.TestCase):
    def test_rhr_under_deloads_even_when_score_high(self):
        ex = {"default_sets": 3, "default_reps": 10, "rep_range": [8, 12]}
        last = {"weight_lbs": 100, "sets": 3, "reps": 10, "date": "2026-09-14"}
        hold = prescribe(ex, last, recovery_score=80)
        cut = prescribe(ex, last, recovery_score=80, rhr_under_recovered=True)
        self.assertEqual(hold["weight_lbs"], 100)
        self.assertEqual(cut["weight_lbs"], 90.0)
        self.assertIn("RHR under-recovered", cut["rationale"])


class TestWeeklyReviewRhr(unittest.TestCase):
    def test_skipped_rhr_omits_bullet(self):
        rec = RecoveryStatus(
            label="Ready",
            score=80,
            reasons=["Strong sleep"],
            inputs={"rhr_skipped": True, "rhr_today_bpm": None},
        )
        review = compute_weekly_review(
            sessions=[],
            recovery=rec,
            weight=[],
            sleep=[],
            nutrition=[],
            targets={},
            adherence={},
            as_of=AS_OF,
        )
        self.assertFalse(any(b.startswith("RHR:") for b in review["bullets"]))

    def test_flagged_rhr_adds_bullet(self):
        rec = RecoveryStatus(
            label="Caution",
            score=55,
            reasons=["RHR high"],
            inputs={
                "rhr_skipped": False,
                "rhr_under_recovered": True,
                "rhr_today_bpm": 68,
                "rhr_baseline_bpm": 60,
                "rhr_baseline_days": 14,
                "rhr_delta_bpm": 8,
            },
        )
        review = compute_weekly_review(
            sessions=[],
            recovery=rec,
            weight=[],
            sleep=[],
            nutrition=[],
            targets={},
            adherence={},
            as_of=AS_OF,
        )
        self.assertTrue(any("under-recovered" in b and b.startswith("RHR:") for b in review["bullets"]))


if __name__ == "__main__":
    unittest.main()
