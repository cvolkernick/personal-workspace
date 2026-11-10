"""Body-fat % on the existing Google Health weight poll (#1382).

Ingest uses dataTypes/body-fat inside the weight job. Readings persist with
timestamps on the health cache. Trends export and the weight chart read them.
"""

from __future__ import annotations

import inspect
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

from rt_dashboard.dashboard_cache import (  # noqa: E402
    health_from_dict,
    load_health_cache,
    merge_health_snapshots,
    save_health_cache,
)
from rt_dashboard.google_health import (  # noqa: E402
    GoogleHealthClient,
    GoogleHealthError,
    parse_health_api_body_fat,
)
from rt_dashboard.models import BodyFatSample, HealthSnapshot, WeightSample  # noqa: E402
from rt_dashboard.trends_export import export_trends_window  # noqa: E402

HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")

PAYLOAD = {
    "dataPoints": [
        {
            "bodyFat": {
                "sampleTime": {
                    "physicalTime": "2026-11-06T15:02:00Z",
                    "civilTime": {"date": {"year": 2026, "month": 11, "day": 6}},
                },
                "percentage": 18.4,
            }
        },
        {
            "bodyFat": {
                "sampleTime": {
                    "physicalTime": "2026-11-06T11:00:00Z",
                    "civilTime": {"date": {"year": 2026, "month": 11, "day": 6}},
                },
                "percentage": "18.1",
            }
        },
        {
            "bodyFat": {
                "sampleTime": {
                    "physicalTime": "2020-01-01T00:00:00Z",
                    "civilTime": {"date": {"year": 2020, "month": 1, "day": 1}},
                },
                "percentage": 22,
            }
        },
        {
            "bodyFat": {
                "sampleTime": {
                    "civilTime": {"date": {"year": 2026, "month": 11, "day": 5}}
                },
                "percentage": 140,
            }
        },
    ]
}


def _stub_streams(client: GoogleHealthClient) -> None:
    src = inspect.getsource(GoogleHealthClient.fetch_health)
    names = set(re.findall(r"\bself\.(fetch_\w+)\s*\(", src))
    for name in names:
        if name.endswith("_bundle"):
            setattr(client, name, lambda days=30, **_kwargs: ([], []))
        else:
            setattr(client, name, lambda days=30, **_kwargs: [])


class ParseBodyFat(unittest.TestCase):
    def test_keeps_same_day_timestamps_and_drops_bad_rows(self):
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        rows = parse_health_api_body_fat(PAYLOAD, start=start)
        self.assertEqual(
            [(r.date, r.percentage, r.recorded_at) for r in rows],
            [
                ("2026-11-06", 18.1, "2026-11-06T11:00:00Z"),
                ("2026-11-06", 18.4, "2026-11-06T15:02:00Z"),
            ],
        )
        self.assertTrue(all(r.source == "google_health" for r in rows))

    def test_fetch_uses_body_fat_data_type_on_the_weight_path(self):
        client = GoogleHealthClient(access_token="x")
        seen = {}

        def fake(data_type, max_pages=2, **_kwargs):
            seen["type"] = data_type
            seen["pages"] = max_pages
            return PAYLOAD

        client._paginate_data_points = fake  # type: ignore[method-assign]
        rows = client.fetch_body_fat(days=30)
        self.assertEqual(seen["type"], "body-fat")
        self.assertEqual(seen["pages"], 2)
        self.assertEqual(len(rows), 2)
        weight_fn = inspect.getsource(GoogleHealthClient.fetch_health)
        self.assertIn("self.fetch_body_fat", weight_fn)
        self.assertIn("self.fetch_weight", weight_fn)


class FetchHealthBodyFat(unittest.TestCase):
    def _client(self) -> GoogleHealthClient:
        client = GoogleHealthClient(access_token="x")
        client.ensure_access_token = lambda: "x"  # type: ignore[method-assign]
        _stub_streams(client)
        return client

    def test_snapshot_includes_body_fat_beside_weight(self):
        client = self._client()
        client.fetch_weight = lambda days=30: [  # type: ignore[method-assign]
            WeightSample(date="2026-11-06", weight_lbs=180.2, source="google_health")
        ]
        client.fetch_body_fat = lambda days=30: [  # type: ignore[method-assign]
            BodyFatSample(
                date="2026-11-06",
                percentage=18.4,
                recorded_at="2026-11-06T15:02:00Z",
            )
        ]
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            snap = client.fetch_health(days=14)
        self.assertEqual(snap.weight[0].weight_lbs, 180.2)
        self.assertEqual(snap.body_fat[0].percentage, 18.4)
        self.assertEqual(snap.body_fat[0].recorded_at, "2026-11-06T15:02:00Z")
        self.assertEqual(snap.to_dict()["body_fat"][0]["percentage"], 18.4)
        self.assertIsNone(snap.error)

    def test_body_fat_error_does_not_drop_weight(self):
        client = self._client()
        client.fetch_weight = lambda days=30: [  # type: ignore[method-assign]
            WeightSample(date="2026-11-06", weight_lbs=180.2, source="google_health")
        ]

        def _boom(days=30):
            raise GoogleHealthError("body fat down")

        client.fetch_body_fat = _boom  # type: ignore[method-assign]
        snap = client.fetch_health(days=14)
        self.assertEqual(len(snap.weight), 1)
        self.assertEqual(snap.body_fat, [])
        self.assertIn("body_fat", snap.error or "")


class StoreBodyFat(unittest.TestCase):
    def test_cache_roundtrip_and_merge_keep_timestamps(self):
        morning = BodyFatSample(
            date="2026-11-06",
            percentage=18.1,
            recorded_at="2026-11-06T11:00:00Z",
            source="google_health",
        )
        evening = BodyFatSample(
            date="2026-11-06",
            percentage=18.4,
            recorded_at="2026-11-06T15:02:00Z",
            source="google_health",
        )
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("rt_dashboard.dashboard_cache.CACHE_DIR", Path(td)):
                save_health_cache(
                    HealthSnapshot(
                        weight=[
                            WeightSample(
                                date="2026-11-06",
                                weight_lbs=180.2,
                                source="google_health",
                            )
                        ],
                        body_fat=[morning],
                    )
                )
                loaded, _, meta = load_health_cache()
                self.assertTrue(meta.get("hit"))
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.body_fat[0].percentage, 18.1)
                self.assertEqual(loaded.body_fat[0].recorded_at, "2026-11-06T11:00:00Z")
                self.assertEqual(loaded.weight[0].weight_lbs, 180.2)
                revised = BodyFatSample(
                    date="2026-11-06",
                    percentage=18.9,
                    recorded_at="2026-11-06T11:00:00Z",
                    source="google_health",
                )
                merged = merge_health_snapshots(
                    loaded, HealthSnapshot(body_fat=[revised, evening])
                )
                by_ts = {row.recorded_at: row.percentage for row in merged.body_fat}
                self.assertEqual(by_ts["2026-11-06T11:00:00Z"], 18.9)
                self.assertEqual(by_ts["2026-11-06T15:02:00Z"], 18.4)
                self.assertEqual(merged.weight[0].weight_lbs, 180.2)

    def test_cache_drops_percentage_outside_range(self):
        snap = health_from_dict(
            {
                "body_fat": [
                    {"date": "2026-11-06", "percentage": 250},
                    {
                        "date": "2026-11-06",
                        "percentage": 18.4,
                        "recorded_at": "2026-11-06T15:02:00Z",
                    },
                ]
            }
        )
        self.assertEqual(len(snap.body_fat), 1)
        self.assertEqual(snap.body_fat[0].percentage, 18.4)


class TrendsBodyFatRead(unittest.TestCase):
    def test_export_rows_carry_last_percentage_beside_weight(self):
        body = export_trends_window(
            {
                "weight": [{"date": "2026-11-06", "weight_lbs": 180.2}],
                "body_fat": [
                    {
                        "date": "2026-11-06",
                        "percentage": 18.1,
                        "recorded_at": "2026-11-06T11:00:00Z",
                    },
                    {
                        "date": "2026-11-06",
                        "percentage": 18.4,
                        "recorded_at": "2026-11-06T15:02:00Z",
                    },
                    {
                        "date": "2026-11-05",
                        "percentage": 18.6,
                        "recorded_at": "2026-11-05T12:00:00Z",
                    },
                    {"date": "2026-11-04", "percentage": 120},
                ],
            },
            days=3,
            end="2026-11-06",
            tz_name="America/New_York",
        )
        rows = {row["date"]: row for row in body["rows"]}
        self.assertEqual(rows["2026-11-06"]["weight_lb"], 180.2)
        self.assertEqual(rows["2026-11-06"]["body_fat_pct"], 18.4)
        self.assertEqual(rows["2026-11-05"]["body_fat_pct"], 18.6)
        self.assertIsNone(rows["2026-11-05"]["weight_lb"])
        self.assertIsNone(rows["2026-11-04"]["body_fat_pct"])
        self.assertIsNone(rows["2026-11-04"]["weight_lb"])


class BodyFatChart(unittest.TestCase):
    def test_weight_card_shows_body_fat_series(self):
        self.assertIn("Body weight · body fat · 90d", HTML)
        self.assertIn('id="chart-weight"', HTML)
        self.assertIn("/body-fat-series.js?v=bf-phase-14d-1", HTML)
        self.assertLess(
            HTML.find("/body-fat-series.js?v=bf-phase-14d-1"),
            HTML.find("/app.js?v=bf-auto-flip-1"),
        )
        self.assertIn('"/body-fat-series.js?v=bf-phase-14d-1"', SW)
        self.assertIn('const CACHE = "fitdash-shell-v164"', SW)
        self.assertIn("FitDashBodyFat", APP_JS)
        self.assertIn('label: "Body fat (%)"', APP_JS)
        self.assertIn('label: "Body fat 7-day avg"', APP_JS)
        self.assertIn("showLine: false", APP_JS)
        self.assertIn("spanGaps: false", APP_JS)
        self.assertIn("bfApi.measurements(bodyFatWindow)", APP_JS)
        self.assertIn("bfApi.trailingAverage(bodyFatForAvg, chartLabels)", APP_JS)
        self.assertIn("bfApi.axisDates(weightLabels, bodyFatWindow)", APP_JS)
        self.assertIn('yAxisID: "y1"', APP_JS)
        self.assertIn("7-day avg", APP_JS)
        self.assertIn("no body-fat readings in this window", APP_JS)
        self.assertIn("7-calendar-day average", HTML)
        self.assertNotIn('id="body-fat-home"', HTML)

    def test_node_aligns_last_reading_onto_weight_dates(self):
        script = ROOT / "tests" / "body_fat_series.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok body-fat-series", proc.stdout)


if __name__ == "__main__":
    unittest.main()
