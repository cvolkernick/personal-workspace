"""Unit tests for Hidrate client parsing + hydration overlay (no network)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.hidrate_client import (  # noqa: E402
    HidrateClient,
    HidrateError,
    _bottle_battery_percent,
    _day_total_ml,
    hidrate_bottle_charge,
    hidrate_hydration_samples,
    overlay_hidrate_hydration,
    parse_sip_samples,
    summarize_bottle_charge,
)
from rt_dashboard.models import HealthSnapshot, HydrationDay  # noqa: E402


class TestDayTotalMl(unittest.TestCase):
    def test_prefers_total_amount(self):
        self.assertEqual(
            _day_total_ml(
                {
                    "totalAmount": 100.5,
                    "totalBottleAmount": 90,
                    "totalVolumeAmount": 80,
                }
            ),
            100.5,
        )

    def test_falls_back(self):
        self.assertEqual(_day_total_ml({"totalBottleAmount": 42}), 42.0)
        self.assertIsNone(_day_total_ml({}))


class TestOverlayHidrate(unittest.TestCase):
    def test_no_credentials_leaves_gh(self):
        snap = HealthSnapshot(
            hydration=[HydrationDay(date="2026-08-05", water_ml=500, source="google_health")]
        )
        with patch(
            "rt_dashboard.hidrate_client.hidrate_credentials_present", return_value=False
        ):
            out, meta = overlay_hidrate_hydration(snap, days=14)
        self.assertFalse(meta["configured"])
        self.assertEqual(out.hydration[0].water_ml, 500)
        self.assertEqual(out.hydration[0].source, "google_health")

    def test_hidrate_wins_on_overlap_keeps_older_gh(self):
        snap = HealthSnapshot(
            hydration=[
                HydrationDay(date="2026-07-01", water_ml=2000, source="google_health"),
                HydrationDay(date="2026-08-05", water_ml=100, source="google_health"),
            ]
        )
        hidrate_series = [
            HydrationDay(date="2026-08-05", water_ml=2276, source="hidrate"),
            HydrationDay(date="2026-08-04", water_ml=1400, source="hidrate"),
        ]

        class FakeClient:
            def credentials_present(self) -> bool:
                return True

            def fetch_hydration_days(self, days: int = 90):
                return hidrate_series

        with patch(
            "rt_dashboard.hidrate_client.hidrate_credentials_present", return_value=True
        ):
            out, meta = overlay_hidrate_hydration(
                snap, days=14, client=FakeClient()  # type: ignore[arg-type]
            )
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["days"], 2)
        by = {h.date: h for h in out.hydration}
        self.assertEqual(by["2026-07-01"].water_ml, 2000)
        self.assertEqual(by["2026-07-01"].source, "google_health")
        self.assertEqual(by["2026-08-05"].water_ml, 2276)
        self.assertEqual(by["2026-08-05"].source, "hidrate")
        self.assertEqual(by["2026-08-04"].water_ml, 1400)
        self.assertEqual(by["2026-08-04"].source, "hidrate")


class TestFetchHydrationDaysParse(unittest.TestCase):
    def test_skips_future_and_dedupes(self):
        client = HidrateClient(email="x@y.z", password="secret")
        rows = [
            {"date": "2026-08-05", "totalAmount": 100},
            {"date": "2026-08-05", "totalAmount": 2276},  # later wins via by-date
            {"date": "2099-01-01", "totalAmount": 999},
            {"date": "2026-08-04", "totalAmount": 50},
        ]
        with patch.object(client, "ensure_session"), patch.object(
            client, "fetch_day_rows", return_value=rows
        ), patch(
            "rt_dashboard.hidrate_client.local_today_iso", return_value="2026-08-05"
        ), patch(
            "rt_dashboard.hidrate_client.local_tz"
        ) as tz_mock:
            # local_tz only used for start date; return UTC-ish
            from datetime import timezone

            tz_mock.return_value = timezone.utc
            series = client.fetch_hydration_days(days=14)
        by = {h.date: h.water_ml for h in series}
        self.assertNotIn("2099-01-01", by)
        self.assertEqual(by["2026-08-05"], 2276.0)
        self.assertEqual(by["2026-08-04"], 50.0)
        self.assertTrue(all(h.source == "hidrate" for h in series))


class TestBottleBatteryField(unittest.TestCase):
    def test_reads_battery_level(self):
        self.assertEqual(_bottle_battery_percent({"batteryLevel": 42}), (42.0, "batteryLevel"))

    def test_accepts_battery_alias(self):
        self.assertEqual(_bottle_battery_percent({"battery": 7}), (7.0, "battery"))

    def test_prefers_battery_level_over_alias(self):
        self.assertEqual(
            _bottle_battery_percent({"batteryLevel": 55, "battery": 9}),
            (55.0, "batteryLevel"),
        )

    def test_missing_and_unparseable_are_none(self):
        self.assertIsNone(_bottle_battery_percent({}))
        self.assertIsNone(_bottle_battery_percent({"name": "Spark"}))
        self.assertIsNone(_bottle_battery_percent({"batteryLevel": None}))
        self.assertIsNone(_bottle_battery_percent({"batteryLevel": "charged"}))
        self.assertIsNone(_bottle_battery_percent({"batteryLevel": 250}))

    def test_zero_is_a_real_reading(self):
        self.assertEqual(_bottle_battery_percent({"batteryLevel": 0}), (0.0, "batteryLevel"))


class TestSummarizeBottleCharge(unittest.TestCase):
    def test_battery_present_is_surfaced(self):
        got = summarize_bottle_charge(
            [
                {
                    "name": "Spark Steel",
                    "serialNumber": "ABC",
                    "batteryLevel": 68,
                    "updatedAt": "2026-08-22T11:00:00.000Z",
                }
            ]
        )
        self.assertTrue(got["available"])
        self.assertEqual(got["percent"], 68.0)
        self.assertEqual(got["field"], "batteryLevel")
        self.assertEqual(got["name"], "Spark Steel")
        self.assertEqual(got["status"], "ok")
        self.assertIsNone(got["error"])
        self.assertEqual(len(got["bottles"]), 1)
        self.assertEqual(got["bottles"][0]["percent"], 68.0)

    def test_prefers_newer_bottle(self):
        got = summarize_bottle_charge(
            [
                {"name": "Old", "batteryLevel": 10, "updatedAt": "2026-01-01T00:00:00.000Z"},
                {"name": "New", "batteryLevel": 90, "updatedAt": "2026-08-22T00:00:00.000Z"},
            ]
        )
        self.assertEqual(got["percent"], 90.0)
        self.assertEqual(got["name"], "New")
        self.assertEqual(len(got["bottles"]), 2)
        names = {b["name"] for b in got["bottles"]}
        self.assertEqual(names, {"Old", "New"})

    def test_surfaces_both_bottles_capacity_order(self):
        got = summarize_bottle_charge(
            [
                {
                    "name": "946ml PRO",
                    "serialNumber": "BIG",
                    "capacity": 946,
                    "batteryLevel": 40,
                    "updatedAt": "2026-08-22T00:00:00.000Z",
                },
                {
                    "name": "621ml PRO",
                    "serialNumber": "SMALL",
                    "capacity": 621,
                    "batteryLevel": 80,
                    "updatedAt": "2026-08-21T00:00:00.000Z",
                },
            ]
        )
        self.assertTrue(got["available"])
        self.assertEqual(got["percent"], 40.0)
        self.assertEqual(got["name"], "946ml PRO")
        self.assertEqual([b["name"] for b in got["bottles"]], ["621ml PRO", "946ml PRO"])
        self.assertEqual([b["percent"] for b in got["bottles"]], [80.0, 40.0])
        self.assertEqual(got["bottles"][0]["capacity_ml"], 621.0)
        self.assertEqual(got["bottles"][1]["capacity_ml"], 946.0)

    def test_includes_uncharged_bottle_alongside_charged(self):
        got = summarize_bottle_charge(
            [
                {
                    "name": "621ml PRO",
                    "serialNumber": "SMALL",
                    "capacity": 621,
                    "batteryLevel": 80,
                    "updatedAt": "2026-08-22T00:00:00.000Z",
                },
                {
                    "name": "946ml PRO",
                    "serialNumber": "BIG",
                    "capacity": 946,
                    "updatedAt": "2026-08-22T00:00:00.000Z",
                },
            ]
        )
        self.assertTrue(got["available"])
        self.assertEqual(len(got["bottles"]), 2)
        self.assertEqual(got["bottles"][1]["name"], "946ml PRO")
        self.assertFalse(got["bottles"][1]["available"])
        self.assertIsNone(got["bottles"][1]["percent"])
        self.assertEqual(got["bottles"][1]["status"], "missing_field")

    def test_dedupes_same_serial_keeps_newer(self):
        got = summarize_bottle_charge(
            [
                {
                    "name": "Old",
                    "serialNumber": "ABC",
                    "batteryLevel": 10,
                    "updatedAt": "2026-01-01T00:00:00.000Z",
                },
                {
                    "name": "New",
                    "serialNumber": "ABC",
                    "batteryLevel": 90,
                    "updatedAt": "2026-08-22T00:00:00.000Z",
                },
            ]
        )
        self.assertEqual(got["percent"], 90.0)
        self.assertEqual(got["name"], "New")
        self.assertEqual(len(got["bottles"]), 1)

    def test_missing_field_is_honest_empty(self):
        got = summarize_bottle_charge(
            [{"name": "Spark", "serialNumber": "ABC", "capacity": 600}]
        )
        self.assertFalse(got["available"])
        self.assertIsNone(got["percent"])
        self.assertEqual(got["status"], "missing_field")
        self.assertNotIn("%", str(got["percent"]))
        self.assertEqual(len(got["bottles"]), 1)
        self.assertEqual(got["bottles"][0]["name"], "Spark")
        self.assertIsNone(got["bottles"][0]["percent"])

    def test_empty_results_are_honest_empty(self):
        got = summarize_bottle_charge([])
        self.assertFalse(got["available"])
        self.assertIsNone(got["percent"])
        self.assertEqual(got["status"], "empty")
        self.assertEqual(got["bottles"], [])


class TestHidrateBottleChargeHelper(unittest.TestCase):
    def test_battery_present_via_client(self):
        class FakeClient:
            def credentials_present(self) -> bool:
                return True

            def fetch_bottle_charge(self, use_cache: bool = True):
                return summarize_bottle_charge([{"batteryLevel": 33, "name": "Puck"}])

        with patch(
            "rt_dashboard.hidrate_client.hidrate_credentials_present", return_value=True
        ):
            got = hidrate_bottle_charge(client=FakeClient())  # type: ignore[arg-type]
        self.assertTrue(got["available"])
        self.assertEqual(got["percent"], 33.0)
        self.assertEqual(got["field"], "batteryLevel")

    def test_missing_field_via_client(self):
        class FakeClient:
            def credentials_present(self) -> bool:
                return True

            def fetch_bottle_charge(self, use_cache: bool = True):
                return summarize_bottle_charge([{"name": "Puck"}])

        with patch(
            "rt_dashboard.hidrate_client.hidrate_credentials_present", return_value=True
        ):
            got = hidrate_bottle_charge(client=FakeClient())  # type: ignore[arg-type]
        self.assertFalse(got["available"])
        self.assertIsNone(got["percent"])
        self.assertEqual(got["status"], "missing_field")

    def test_auth_fail_is_unavailable_not_fake_percent(self):
        class FakeClient:
            def credentials_present(self) -> bool:
                return True

            def fetch_bottle_charge(self, use_cache: bool = True):
                raise HidrateError("Hidrate HTTP 401: unauthorized", status=401)

        with patch(
            "rt_dashboard.hidrate_client.hidrate_credentials_present", return_value=True
        ):
            got = hidrate_bottle_charge(client=FakeClient())  # type: ignore[arg-type]
        self.assertFalse(got["available"])
        self.assertIsNone(got["percent"])
        self.assertEqual(got["status"], "unavailable")
        self.assertIn("401", str(got["error"] or ""))
        self.assertNotEqual(got["percent"], 0)
        self.assertNotEqual(got["percent"], 100)

    def test_fetch_retries_session_then_surfaces_unavailable(self):
        client = HidrateClient(email="x@y.z", password="secret")
        with patch.object(
            client,
            "fetch_bottle_rows",
            side_effect=HidrateError("invalid session", status=401),
        ):
            with self.assertRaises(HidrateError):
                client.fetch_bottle_charge(use_cache=False)
        with patch(
            "rt_dashboard.hidrate_client.hidrate_credentials_present", return_value=True
        ):
            with patch.object(
                client,
                "fetch_bottle_charge",
                side_effect=HidrateError("invalid session", status=401),
            ):
                got = hidrate_bottle_charge(client=client)
        self.assertEqual(got["status"], "unavailable")
        self.assertIsNone(got["percent"])


class TestParseSipSamples(unittest.TestCase):
    def test_uses_time_not_created_at(self):
        rows = [
            {
                "time": {"__type": "Date", "iso": "2026-08-22T22:30:00.000Z"},
                "createdAt": "2026-08-23T08:00:00.000Z",
                "amount": 250,
            },
            {
                "createdAt": "2026-08-22T22:30:00.000Z",
                "amount": 999,
            },
            {
                "time": {"iso": "2026-08-22T23:00:00.000Z"},
            },
        ]
        got = parse_sip_samples(rows)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["logged_at"], "2026-08-22T22:30:00.000Z")
        self.assertEqual(got[0]["water_ml"], 250.0)
        self.assertEqual(got[0]["source"], "hidrate")

    def test_hidrate_hydration_samples_no_creds_is_empty(self):
        with patch(
            "rt_dashboard.hidrate_client.hidrate_credentials_present",
            return_value=False,
        ):
            self.assertEqual(hidrate_hydration_samples(), [])

    def test_sip_fetch_auth_fail_is_empty(self):
        class FakeClient:
            def credentials_present(self) -> bool:
                return True

            def fetch_hydration_samples(self, hours: int = 48, use_cache: bool = True):
                raise HidrateError("invalid session", status=401)

        with patch(
            "rt_dashboard.hidrate_client.hidrate_credentials_present",
            return_value=True,
        ):
            got = hidrate_hydration_samples(client=FakeClient())  # type: ignore[arg-type]
        self.assertEqual(got, [])

    def test_fetch_sip_rows_filters_on_time_not_created_at(self):
        client = HidrateClient(email="x@y.z", password="secret")
        captured = {}

        def fake_request(method, path, *, params=None, session=False):
            captured["path"] = path
            captured["params"] = params
            return {"results": []}

        start = __import__("datetime").datetime(2026, 8, 22, 12, 0, tzinfo=__import__("datetime").timezone.utc)
        with patch.object(client, "ensure_session"), patch.object(
            client, "_request", side_effect=fake_request
        ):
            client.fetch_sip_rows(start=start, limit=50)
        self.assertEqual(captured["path"], "/classes/Sip")
        where = json.loads(captured["params"]["where"])
        self.assertIn("time", where)
        self.assertNotIn("createdAt", where)
        self.assertNotIn("date", where)
        self.assertEqual(where["time"]["$gte"]["__type"], "Date")


class TestDashboardBottlePayload(unittest.TestCase):
    def test_dashboard_surfaces_bottle_charge(self):
        import os

        from api.auth.session_util import SESSION_COOKIE, make_session
        from api.dashboard import dashboard_body

        charge = summarize_bottle_charge([{"batteryLevel": 81, "name": "Spark"}])
        health = HealthSnapshot()
        env = {"TZ": "UTC", "GOOGLE_CLIENT_SECRET": "test-secret"}
        with patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            with patch(
                "api.dashboard._load_sessions", return_value=([], [], "turso")
            ), patch(
                "api.dashboard._load_health", return_value=(health, [])
            ), patch(
                "rt_dashboard.hidrate_client.hidrate_bottle_charge", return_value=charge
            ):
                status, body = dashboard_body(headers, "")
        self.assertEqual(status, 200)
        self.assertEqual(body["hidrate_bottle"]["percent"], 81.0)
        self.assertEqual(body["hidrate_bottle"]["field"], "batteryLevel")
        self.assertEqual((body.get("hydration_bars") or {}).get("bottle", {}).get("percent"), 81.0)

    def test_dashboard_auth_fail_is_honest_empty(self):
        import os

        from api.auth.session_util import SESSION_COOKIE, make_session
        from api.dashboard import dashboard_body

        empty = {
            "available": False,
            "percent": None,
            "field": None,
            "name": None,
            "serial": None,
            "status": "unavailable",
            "error": "Hidrate HTTP 403: forbidden",
        }
        health = HealthSnapshot()
        env = {"TZ": "UTC", "GOOGLE_CLIENT_SECRET": "test-secret"}
        with patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            with patch(
                "api.dashboard._load_sessions", return_value=([], [], "turso")
            ), patch(
                "api.dashboard._load_health", return_value=(health, [])
            ), patch(
                "rt_dashboard.hidrate_client.hidrate_bottle_charge", return_value=empty
            ):
                status, body = dashboard_body(headers, "")
        self.assertEqual(status, 200)
        bottle = body["hidrate_bottle"]
        self.assertFalse(bottle["available"])
        self.assertIsNone(bottle["percent"])
        self.assertEqual(bottle["status"], "unavailable")


class TestBottleChargeUiOverlay(unittest.TestCase):
    def test_thin_overlay_not_app_js_stub(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "static" / "index.html").read_text(encoding="utf-8")
        js = (root / "static" / "hidrate-bottle.js").read_text(encoding="utf-8")
        css = (root / "static" / "styles.css").read_text(encoding="utf-8")
        app_js = root / "static" / "app.js"
        self.assertGreater(app_js.stat().st_size, 180_000)
        self.assertIn("hidrate-bottle.js?v=bottle-charge-7", html)
        self.assertIn("styles.css?v=library-1", html)
        self.assertIn('id="hidrate-bottle-charge"', html)
        self.assertIn("hydration-pacing-header-end", html)
        header_end = html[
            html.find("hydration-pacing-header-end") : html.find(
                'id="hydration-pacing-track"'
            )
        ]
        self.assertIn('id="hidrate-bottle-charge"', header_end)
        self.assertNotIn("hydration-pacing-meta", header_end)
        self.assertLess(
            html.find('id="hidrate-bottle-charge"'),
            html.find('id="hydration-pacing-summary"'),
        )
        self.assertGreater(
            html.find('id="hydration-pacing-meta"'),
            html.find('id="hydration-pacing-summary"'),
        )
        self.assertIn("hydration-pacing-footer", html)
        self.assertIn(".hydration-pacing-footer", css)
        self.assertIn("hidrate_bottle", js)
        self.assertIn("bottlesFrom", js)
        self.assertIn("unavailable", js)
        self.assertIn("sb-shell", js)
        self.assertIn("sb-fill-wrap", js)
        self.assertIn("sb-fill", js)
        self.assertIn('" style="width:' , js)
        self.assertNotIn('" style="height:' , js)
        self.assertIn("sb-label", js)
        self.assertIn("critical", js)
        self.assertIn("full", js)
        self.assertIn(".hidrate-bottle-charge .sb-shell", css)
        start = css.find(".hidrate-bottle-charge .sb-shell {")
        self.assertGreaterEqual(start, 0)
        hidrate_block = css[start : css.find(".pace-track", start)]
        self.assertIn("width: 46px;", hidrate_block)
        self.assertIn("height: 22px;", hidrate_block)
        wrap_start = hidrate_block.find(".hidrate-bottle-charge .sb-fill-wrap {")
        self.assertGreaterEqual(wrap_start, 0)
        wrap_block = hidrate_block[wrap_start : hidrate_block.find("}", wrap_start) + 1]
        self.assertIn("display: block;", wrap_block)
        self.assertIn("width: 38px;", wrap_block)
        self.assertIn("height: 14px;", wrap_block)
        self.assertIn("right: -5px;", hidrate_block)
        self.assertIn("width: 4px;", hidrate_block)
        self.assertNotIn("width: 50px;", hidrate_block)
        self.assertNotIn("height: 28px;", hidrate_block)
        self.assertNotIn("width: 32px;", hidrate_block)
        self.assertNotIn("height: 56px;", hidrate_block)
        self.assertNotIn("width: 22px;", hidrate_block)
        self.assertNotIn("height: 38px;", hidrate_block)
        self.assertNotIn("width: 72px;", hidrate_block)
        self.assertNotIn("width: 64px;", hidrate_block)
        self.assertNotIn(".hidrate-bottle-charge .hbb-shell", hidrate_block)
        # Landscape body: wrap wider than tall (sleep-battery orientation).
        self.assertLess(
            hidrate_block.find("width: 38px;"),
            hidrate_block.find("height: 14px;"),
        )
        # Sleep battery shell stays full-width / 44px fill — not the mini sizes.
        sleep_shell = css[css.find(".sb-shell {") : css.find(".hidrate-bottle-charge .sb-shell")]
        self.assertIn("width: 100%;", sleep_shell)
        self.assertIn("height: 44px;", sleep_shell)
        self.assertIn(".sb-fill.critical", css)
        self.assertIn(".sb-fill.low", css)
        self.assertIn(".sb-fill.ok", css)
        self.assertIn(".sb-fill.full", css)
        self.assertNotIn("battery: 100", js)
        self.assertNotIn('"percent": 100', js)
        self.assertNotIn("linear-gradient(90deg, #ff00", css)

    def test_node_mini_battery_structure(self):
        import subprocess

        script = Path(__file__).resolve().parents[1] / "tests" / "hidrate_bottle_mini.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(Path(__file__).resolve().parents[1]),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok hidrate-bottle-mini", proc.stdout)


if __name__ == "__main__":
    unittest.main()
