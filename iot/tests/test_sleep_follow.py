"""Unit tests for post-sunset Sleep Battery bedroom follow."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from iot.control import build_control_intent  # noqa: E402
from iot.sleep_follow import (  # noqa: E402
    decide_action,
    extract_sleep_battery,
    pct_to_wiz_brightness,
    should_poll,
    tick_sleep_follow,
)


class BrightnessMapTests(unittest.TestCase):
    def test_pct_map(self) -> None:
        self.assertEqual(pct_to_wiz_brightness(100), 255)
        self.assertEqual(pct_to_wiz_brightness(10), 26)
        self.assertEqual(pct_to_wiz_brightness(50), 128)
        self.assertEqual(pct_to_wiz_brightness(0), 1)

    def test_decide_off_at_zero(self) -> None:
        a = decide_action(0)
        self.assertEqual(a["color"], "off")
        self.assertTrue(a["done"])

    def test_decide_keep_when_charged(self) -> None:
        a = decide_action(40)
        self.assertEqual(a["color"], "keep")
        self.assertEqual(a["brightness"], pct_to_wiz_brightness(40))
        self.assertFalse(a["done"])


class KeepColorTests(unittest.TestCase):
    def test_build_intent_brightness_only(self) -> None:
        reg = {"masterbedroom1": {"ip": "192.168.100.50", "mac": "aabbccddeeff"}}
        groups = {
            "masterbedroom": {
                "id": "masterbedroom",
                "label": "Master Bedroom",
                "members": ["masterbedroom1"],
            }
        }
        intent = build_control_intent(
            "masterbedroom", "keep", 26, registry=reg, groups=groups
        )
        self.assertTrue(intent["ok"])
        self.assertEqual(intent["action"], "on")
        self.assertIsNone(intent["rgb"])
        self.assertTrue(intent["brightness_only"])
        self.assertEqual(intent["brightness"], 26)


class ExtractBatteryTests(unittest.TestCase):
    def test_from_dashboard_shape(self) -> None:
        bat = extract_sleep_battery(
            {
                "recovery": {
                    "sleep_battery": {"pct_charged": 42.5, "empty_at": "2026-08-05T00:18:00"}
                }
            }
        )
        self.assertIsNotNone(bat)
        assert bat is not None
        self.assertEqual(bat["pct_charged"], 42.5)

    def test_bare(self) -> None:
        bat = extract_sleep_battery({"pct_charged": 12, "mode": "awake"})
        self.assertEqual(bat and bat["pct_charged"], 12)


class PollIntervalTests(unittest.TestCase):
    def test_first_poll(self) -> None:
        now = datetime(2026, 8, 4, 20, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertTrue(should_poll({}, now=now, poll_minutes=15))

    def test_too_soon(self) -> None:
        now = datetime(2026, 8, 4, 20, 10, tzinfo=ZoneInfo("America/New_York"))
        follow = {"last_poll_at": "2026-08-04T20:00:00-04:00"}
        self.assertFalse(should_poll(follow, now=now, poll_minutes=15))

    def test_due(self) -> None:
        now = datetime(2026, 8, 4, 20, 16, tzinfo=ZoneInfo("America/New_York"))
        follow = {"last_poll_at": "2026-08-04T20:00:00-04:00"}
        self.assertTrue(should_poll(follow, now=now, poll_minutes=15))


class TickIntegrationTests(unittest.TestCase):
    def test_tick_dims_then_off(self) -> None:
        tz = ZoneInfo("America/New_York")
        # After sunset in FL summer — use force=True to skip solar gate
        now = datetime(2026, 8, 4, 22, 0, tzinfo=tz)
        calls: list[tuple] = []

        def control(target: str, color: str, brightness):
            calls.append((target, color, brightness))
            return {"ok": True}

        def fetch(*, base_url, path, token):  # noqa: ARG001
            return {
                "ok": True,
                "pct_charged": 25.0,
                "empty_at": "2026-08-05T00:18:00-04:00",
            }

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            sched = {
                "location": {
                    "latitude": 26.6093,
                    "longitude": -81.60184,
                    "timezone": "America/New_York",
                },
                "sleep_battery_follow": {
                    "enabled": True,
                    "poll_minutes": 15,
                    "target": "masterbedroom",
                    "fitdash_url": "http://example.invalid",
                },
                "routines": [],
            }
            sp = td_path / "schedule.json"
            st = td_path / "state.json"
            sp.write_text(json.dumps(sched), encoding="utf-8")
            st.write_text(json.dumps({"fired": {}}), encoding="utf-8")

            r1 = tick_sleep_follow(
                control=control,
                schedule_path=sp,
                state_path=st,
                now=now,
                fetch_fn=fetch,
                force=True,
            )
            self.assertTrue(r1.get("ok"))
            self.assertEqual(calls[-1][0], "masterbedroom")
            self.assertEqual(calls[-1][1], "keep")
            self.assertEqual(calls[-1][2], pct_to_wiz_brightness(25))

            def fetch_empty(*, base_url, path, token):  # noqa: ARG001
                return {"ok": True, "pct_charged": 0.0, "empty_at": now.isoformat()}

            # Force past poll interval by clearing last_poll via force still polls
            r2 = tick_sleep_follow(
                control=control,
                schedule_path=sp,
                state_path=st,
                now=now.replace(minute=30),
                fetch_fn=fetch_empty,
                force=True,
            )
            self.assertTrue(r2.get("ok"))
            self.assertTrue((r2.get("follow") or {}).get("done"))
            self.assertEqual(calls[-1], ("masterbedroom", "off", None))


if __name__ == "__main__":
    unittest.main()
