"""Incremental warmer vs Refresh-data full pull."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from rt_dashboard import background_refresh as br


class IncrementalWarmTests(unittest.TestCase):
    def setUp(self) -> None:
        br._last_scheduled = 0.0
        br._warm_loop_started = False
        self._saved = {
            k: os.environ.get(k)
            for k in ("DASHBOARD_WARM_INTERVAL_SEC", "DASHBOARD_BG_REFRESH_MIN_SEC")
        }

    def tearDown(self) -> None:
        br._last_scheduled = 0.0
        br._warm_loop_started = False
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_warm_is_always_incremental(self) -> None:
        spawned: list = []

        def capture(**kwargs):
            spawned.append(kwargs)

        with mock.patch.object(br, "_spawn_refresh", side_effect=capture):
            ok = br.schedule_incremental_warm(
                local_dir="/tmp",
                token="gh",
                health_refresh_token="1//rt",
                force_schedule=True,
            )
        self.assertTrue(ok)
        self.assertEqual(len(spawned), 1)
        self.assertTrue(spawned[0]["incremental"])
        self.assertEqual(spawned[0]["health_refresh_token"], "1//rt")

    def test_refresh_data_force_is_full_90d_pull(self) -> None:
        spawned: list = []
        with mock.patch.object(br, "_spawn_refresh", side_effect=lambda **k: spawned.append(k)):
            ok = br.maybe_schedule_background_refresh(force=True, health_refresh_token="1//rt")
        self.assertTrue(ok)
        self.assertFalse(spawned[0]["incremental"])

    def test_warm_skips_inside_interval_unless_forced(self) -> None:
        os.environ["DASHBOARD_WARM_INTERVAL_SEC"] = "300"
        spawned: list = []
        with mock.patch.object(br, "_spawn_refresh", side_effect=lambda **k: spawned.append(k)):
            self.assertTrue(br.schedule_incremental_warm(force_schedule=False))
            self.assertFalse(br.schedule_incremental_warm(force_schedule=False))
            self.assertTrue(br.schedule_incremental_warm(force_schedule=True))
        self.assertEqual(len(spawned), 2)
        self.assertTrue(all(c["incremental"] for c in spawned))

    def test_warm_interval_zero_disables_loop(self) -> None:
        os.environ["DASHBOARD_WARM_INTERVAL_SEC"] = "0"
        self.assertEqual(br.warm_interval_sec(), 0.0)
        self.assertFalse(br.start_warm_loop())
        self.assertFalse(br._warm_loop_started)

    def test_refresh_health_incremental_uses_14_days(self) -> None:
        captured = {}

        class FakeClient:
            def credentials_present(self):
                return True

            def fetch_health(self, days=90):
                captured["days"] = days
                from rt_dashboard.models import HealthSnapshot

                return HealthSnapshot()

        with mock.patch.object(br, "GoogleHealthClient", return_value=FakeClient()):
            with mock.patch.object(br, "load_health_cache", return_value=(None, None, {})):
                with mock.patch.object(br, "resolve_health_snapshot", side_effect=lambda snap, **k: snap):
                    with mock.patch.object(
                        br, "overlay_hidrate_hydration", side_effect=lambda snap, **k: (snap, {})
                    ):
                        with mock.patch.object(br, "save_health_cache"):
                            br._refresh_health(
                                local_dir="",
                                token="",
                                incremental=True,
                                health_refresh_token="1//rt",
                            )
        self.assertEqual(captured["days"], 14)

    def test_refresh_health_full_uses_90_days(self) -> None:
        captured = {}

        class FakeClient:
            def credentials_present(self):
                return True

            def fetch_health(self, days=90):
                captured["days"] = days
                from rt_dashboard.models import HealthSnapshot

                return HealthSnapshot()

        with mock.patch.object(br, "GoogleHealthClient", return_value=FakeClient()):
            with mock.patch.object(br, "load_health_cache", return_value=(None, None, {})):
                with mock.patch.object(br, "resolve_health_snapshot", side_effect=lambda snap, **k: snap):
                    with mock.patch.object(
                        br, "overlay_hidrate_hydration", side_effect=lambda snap, **k: (snap, {})
                    ):
                        with mock.patch.object(br, "save_health_cache"):
                            br._refresh_health(
                                local_dir="",
                                token="",
                                incremental=False,
                            )
        self.assertEqual(captured["days"], 90)


if __name__ == "__main__":
    unittest.main()
