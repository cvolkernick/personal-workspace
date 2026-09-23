"""#299 PROOF-FIRST: parse + fetch Google Health active-zone-minutes.

Present rollup → dated days. Missing/empty rollup → honest [].
Never invent AZM or substitute heart_minutes / active_minutes / steps / kcal.
"""

from __future__ import annotations

import inspect
import io
import json
import re
import socket
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from rt_dashboard.dashboard_cache import health_from_dict
from rt_dashboard.google_health import (
    GoogleHealthClient,
    GoogleHealthError,
    parse_active_zone_minutes_rollup,
)
from rt_dashboard.models import (
    ActiveZoneMinutesDay,
    HealthSnapshot,
    WeightSample,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _NoLiveNetwork(unittest.TestCase):
    """urlopen fails immediately. A blackholed socket must not decide the test."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(
            urllib.request,
            "urlopen",
            side_effect=AssertionError("network I/O in test_active_zone_minutes"),
        )
        self._opened = patcher.start()
        self.addCleanup(patcher.stop)


class TestParseActiveZoneMinutesRollup(_NoLiveNetwork):
    def test_present_rollup_parses_days(self):
        payload = _load_fixture("azm_rollup_present.json")
        days = parse_active_zone_minutes_rollup(payload)
        self.assertEqual(len(days), 3)
        self.assertEqual(days[0].date, "2026-08-16")
        self.assertEqual(days[0].fat_burn_minutes, 12.0)
        self.assertEqual(days[0].cardio_minutes, 8.0)
        self.assertEqual(days[0].peak_minutes, 2.0)
        self.assertEqual(days[0].total_minutes, 22.0)
        self.assertEqual(days[0].source, "google_health")
        # snake_case union field + sum_in_* aliases
        self.assertEqual(days[1].date, "2026-08-17")
        self.assertEqual(days[1].fat_burn_minutes, 20.0)
        self.assertEqual(days[1].cardio_minutes, 6.0)
        self.assertEqual(days[1].peak_minutes, 1.0)
        self.assertEqual(days[1].total_minutes, 27.0)
        # Partial zone sums: total is only the fields that were present
        self.assertEqual(days[2].date, "2026-08-18")
        self.assertEqual(days[2].fat_burn_minutes, 15.0)
        self.assertIsNone(days[2].cardio_minutes)
        self.assertIsNone(days[2].peak_minutes)
        self.assertEqual(days[2].total_minutes, 15.0)

    def test_empty_rollup_is_honest_empty(self):
        payload = _load_fixture("azm_rollup_empty.json")
        self.assertEqual(parse_active_zone_minutes_rollup(payload), [])

    def test_missing_rollup_key_is_honest_empty(self):
        self.assertEqual(parse_active_zone_minutes_rollup({}), [])
        self.assertEqual(parse_active_zone_minutes_rollup({"dataPoints": []}), [])

    def test_non_dict_payload_is_honest_empty(self):
        self.assertEqual(parse_active_zone_minutes_rollup(None), [])  # type: ignore[arg-type]
        self.assertEqual(parse_active_zone_minutes_rollup([]), [])  # type: ignore[arg-type]

    def test_point_without_zone_sums_is_skipped(self):
        payload = {
            "rollupDataPoints": [
                {
                    "civilStartTime": {"date": {"year": 2026, "month": 8, "day": 16}},
                    "activeZoneMinutes": {},
                },
                {
                    "civilStartTime": {"date": {"year": 2026, "month": 8, "day": 17}},
                    "totalCalories": {"kcalSum": 2100},
                },
            ]
        }
        self.assertEqual(parse_active_zone_minutes_rollup(payload), [])

    def test_does_not_read_substitutes_as_azm(self):
        payload = {
            "rollupDataPoints": [
                {
                    "civilStartTime": {"date": {"year": 2026, "month": 8, "day": 16}},
                    "activeMinutes": {"minutesSum": 40},
                    "heartMinutes": {"minutesSum": 22},
                    "steps": {"countSum": 8000},
                    "totalCalories": {"kcalSum": 2100},
                }
            ]
        }
        self.assertEqual(parse_active_zone_minutes_rollup(payload), [])


class TestFetchActiveZoneMinutes(_NoLiveNetwork):
    def test_daily_rollup_uses_active_zone_minutes_type(self):
        client = GoogleHealthClient(access_token="x")
        captured: dict = {}

        def fake_rollup(data_type, days=14, end_date=None):
            captured["data_type"] = data_type
            captured["days"] = days
            return _load_fixture("azm_rollup_present.json")

        client.daily_rollup = fake_rollup  # type: ignore[method-assign]
        days = client.fetch_active_zone_minutes(days=14)
        self.assertEqual(captured["data_type"], "active-zone-minutes")
        self.assertEqual(captured["days"], 14)
        self.assertEqual(len(days), 3)
        self.assertEqual(days[0].total_minutes, 22.0)

    def test_caps_at_90_days(self):
        client = GoogleHealthClient(access_token="x")
        calls: list[int] = []

        def fake_rollup(data_type, days=14, end_date=None):
            calls.append(days)
            return {"rollupDataPoints": []}

        client.daily_rollup = fake_rollup  # type: ignore[method-assign]
        self.assertEqual(client.fetch_active_zone_minutes(days=180), [])
        self.assertEqual(sum(calls), 90)
        self.assertTrue(all(d <= 14 for d in calls))

    def test_chunks_90_day_window_like_calories(self):
        client = GoogleHealthClient(access_token="x")
        calls: list[dict] = []

        def fake_rollup(data_type, days=14, end_date=None):
            calls.append({"data_type": data_type, "days": days})
            return {"rollupDataPoints": []}

        client.daily_rollup = fake_rollup  # type: ignore[method-assign]
        self.assertEqual(client.fetch_active_zone_minutes(days=90), [])
        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(all(c["data_type"] == "active-zone-minutes" for c in calls))
        self.assertEqual(sum(c["days"] for c in calls), 90)
        self.assertTrue(all(c["days"] <= 14 for c in calls))

    def test_missing_rollup_returns_empty(self):
        client = GoogleHealthClient(access_token="x")
        client.daily_rollup = lambda *a, **k: {}  # type: ignore[method-assign]
        self.assertEqual(client.fetch_active_zone_minutes(days=7), [])


def _fetch_health_stream_names() -> set[str]:
    """Names of self.fetch_*() calls inside fetch_health.

    Derived from source so a new snapshot stream is stubbed here until a test
    overrides it. A hand-maintained list is how steps and RHR reached the network.
    """
    src = inspect.getsource(GoogleHealthClient.fetch_health)
    return set(re.findall(r"\bself\.(fetch_\w+)\s*\(", src))


def _stub_fetch_health_streams(client: GoogleHealthClient) -> None:
    """Empty stand-in for every stream fetch_health calls. No network."""
    for name in _fetch_health_stream_names():
        if name.endswith("_bundle"):
            setattr(client, name, lambda days=30, **_kwargs: ([], []))
        else:
            setattr(client, name, lambda days=30, **_kwargs: [])


class TestFetchHealthIncludesAzm(_NoLiveNetwork):
    def _client(self) -> GoogleHealthClient:
        client = GoogleHealthClient(access_token="x")
        client.ensure_access_token = lambda: "x"  # type: ignore[method-assign]
        _stub_fetch_health_streams(client)
        return client

    def test_stub_covers_steps_and_resting_heart_rate(self) -> None:
        names = _fetch_health_stream_names()
        self.assertIn("fetch_steps", names)
        self.assertIn("fetch_resting_heart_rate", names)
        self.assertGreaterEqual(len(names), 8)
        client = self._client()
        snap = client.fetch_health(days=30)
        self.assertEqual(self._opened.call_count, 0)
        self.assertEqual(snap.steps, [])
        self.assertEqual(snap.resting_heart_rate, [])
        self.assertIsNone(snap.error)

    def test_snapshot_includes_azm_list(self):
        client = self._client()
        client.fetch_active_zone_minutes = lambda days=14: [  # type: ignore[method-assign]
            ActiveZoneMinutesDay(
                date="2026-08-16",
                fat_burn_minutes=12,
                cardio_minutes=8,
                peak_minutes=2,
                total_minutes=22,
            )
        ]
        snap = client.fetch_health(days=30)
        self.assertEqual(len(snap.active_zone_minutes), 1)
        payload = snap.to_dict()
        self.assertIn("active_zone_minutes", payload)
        self.assertEqual(payload["active_zone_minutes"][0]["date"], "2026-08-16")
        self.assertEqual(payload["active_zone_minutes"][0]["total_minutes"], 22)
        self.assertIsNone(payload["error"])

    def test_snapshot_requests_azm_for_full_window(self):
        client = self._client()
        captured: dict = {}

        def _azm(days=14):
            captured["days"] = days
            return []

        client.fetch_active_zone_minutes = _azm  # type: ignore[method-assign]
        snap = client.fetch_health(days=90)
        self.assertEqual(captured["days"], 90)
        self.assertEqual(snap.active_zone_minutes, [])

    def test_azm_error_folds_into_snapshot_errors(self):
        client = self._client()
        client.fetch_weight = lambda days=30: [  # type: ignore[method-assign]
            WeightSample(date="2026-08-16", weight_lbs=180.0)
        ]

        def _boom(days=14):
            raise GoogleHealthError("azm failed")

        client.fetch_active_zone_minutes = _boom  # type: ignore[method-assign]
        snap = client.fetch_health(days=14)
        self.assertEqual(snap.active_zone_minutes, [])
        self.assertIn("active_zone_minutes", snap.error or "")
        self.assertEqual(len(snap.weight), 1)

    def test_azm_error_alone_is_honest_empty_error_snapshot(self):
        client = self._client()

        def _boom(days=14):
            raise GoogleHealthError("azm failed")

        client.fetch_active_zone_minutes = _boom  # type: ignore[method-assign]
        snap = client.fetch_health(days=14)
        self.assertEqual(snap.active_zone_minutes, [])
        self.assertIn("active_zone_minutes", snap.error or "")
        self.assertEqual(snap.to_dict()["active_zone_minutes"], [])


class TestRequestWrapsTransportErrors(_NoLiveNetwork):
    def _client(self) -> GoogleHealthClient:
        # Warm token so urlopen is the data call. A refresh-token in the
        # environment would otherwise hit ensure_access_token first, and that
        # path must stay loud on transport failure.
        client = GoogleHealthClient(access_token="tok")
        client._token_expiry = 9_000_000_000.0
        return client

    def test_socket_timeout_inside_request_is_google_health_error(self) -> None:
        client = self._client()
        timeout = socket.timeout("The read operation timed out")
        with mock.patch.object(urllib.request, "urlopen", side_effect=timeout):
            with self.assertRaises(GoogleHealthError) as ctx:
                client._request("GET", "https://health.googleapis.com/v4/example")
        self.assertIsNone(ctx.exception.status)
        self.assertIn("The read operation timed out", str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, timeout)

    def test_urlerror_and_oserror_wrap(self) -> None:
        client = self._client()
        cases = [
            TimeoutError("timed out"),
            urllib.error.URLError("nodename nor servname"),
            OSError("network down"),
        ]
        for exc in cases:
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(urllib.request, "urlopen", side_effect=exc):
                    with self.assertRaises(GoogleHealthError) as ctx:
                        client._request("GET", "https://health.googleapis.com/v4/example")
                self.assertIn("transport error", str(ctx.exception))
                self.assertIs(ctx.exception.__cause__, exc)

    def test_http_error_keeps_status(self) -> None:
        client = self._client()
        http_err = urllib.error.HTTPError(
            "https://health.googleapis.com/v4/example",
            403,
            "Forbidden",
            None,
            io.BytesIO(b'{"error":"no"}'),
        )
        with mock.patch.object(urllib.request, "urlopen", side_effect=http_err):
            with self.assertRaises(GoogleHealthError) as ctx:
                client._request("GET", "https://health.googleapis.com/v4/example")
        self.assertEqual(ctx.exception.status, 403)
        self.assertIn("Google Health/Fit API error HTTP 403", str(ctx.exception))
        self.assertIn("no", ctx.exception.body)

    def test_steps_and_rhr_return_empty_on_socket_timeout(self) -> None:
        client = self._client()
        with mock.patch.object(
            urllib.request,
            "urlopen",
            side_effect=socket.timeout("The read operation timed out"),
        ):
            self.assertEqual(client.fetch_steps(days=7), [])
            self.assertEqual(client.fetch_resting_heart_rate(days=7), [])


class TestHealthSnapshotAzmSerialization(_NoLiveNetwork):
    def test_to_dict_always_exposes_field(self):
        empty = HealthSnapshot()
        self.assertEqual(empty.to_dict()["active_zone_minutes"], [])
        filled = HealthSnapshot(
            active_zone_minutes=[
                ActiveZoneMinutesDay(
                    date="2026-08-16",
                    fat_burn_minutes=10,
                    cardio_minutes=4,
                    peak_minutes=1,
                    total_minutes=15,
                )
            ]
        )
        row = filled.to_dict()["active_zone_minutes"][0]
        self.assertEqual(row["fat_burn_minutes"], 10)
        self.assertEqual(row["cardio_minutes"], 4)
        self.assertEqual(row["peak_minutes"], 1)
        self.assertEqual(row["total_minutes"], 15)

    def test_health_from_dict_roundtrip(self):
        snap = health_from_dict(
            {
                "active_zone_minutes": [
                    {
                        "date": "2026-08-16",
                        "fat_burn_minutes": 10,
                        "cardio_minutes": 4,
                        "peak_minutes": 1,
                        "total_minutes": 15,
                        "source": "google_health",
                    }
                ]
            }
        )
        self.assertEqual(len(snap.active_zone_minutes), 1)
        self.assertEqual(snap.active_zone_minutes[0].total_minutes, 15.0)
        self.assertEqual(snap.to_dict()["active_zone_minutes"][0]["date"], "2026-08-16")


class TestNoNewHobbyFunction(_NoLiveNetwork):
    def test_no_new_serverless_function(self):
        root = Path(__file__).resolve().parents[1]
        api = root / "api"
        fns = []
        for path in api.rglob("*.py"):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            src = path.read_text(encoding="utf-8")
            if "class handler" in src or "\ndef app(" in src or "\napp = " in src:
                fns.append(path)
        self.assertEqual(len(fns), 12, [str(x.relative_to(root)) for x in fns])

    def test_ignore_build_unchanged(self):
        root = Path(__file__).resolve().parents[1]
        vercel = (root / "vercel.json").read_text(encoding="utf-8")
        self.assertIn(
            '"ignoreCommand": "python3 scripts/vercel_ignore.py || exit 1"', vercel
        )


if __name__ == "__main__":
    unittest.main()
