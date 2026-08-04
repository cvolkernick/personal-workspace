"""Unit tests for groups, solar times, and schedule evaluation."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from iot.control import (  # noqa: E402
    build_control_intent,
    expand_target_members,
    list_groups,
    load_groups,
)
from iot.schedule import (  # noqa: E402
    due_routines,
    fire_key,
    load_schedule,
    mark_fired,
    run_due,
    run_routine_now,
    schedule_status,
    upcoming_for_day,
)
from iot.solar import sun_times_local  # noqa: E402
from iot.wiz_adapter import FakeTransport, execute_control, run_async  # noqa: E402

SAMPLE = {
    "entryway1": {"ip": "192.168.100.106", "mac": "6c2990089296"},
    "entryway2": {"ip": "192.168.100.118", "mac": "6c2990d5075a"},
    "livingroom1": {"ip": "192.168.100.42", "mac": "6c29904cfc9c"},
    "livingroom2": {"ip": "192.168.100.92", "mac": "6c29904ca8b6"},
}
GROUPS = {
    "entryway": {
        "id": "entryway",
        "label": "Entryway",
        "members": ["entryway1", "entryway2"],
    },
    "livingroom": {
        "id": "livingroom",
        "label": "Living room",
        "members": ["livingroom1", "livingroom2"],
    },
}


class GroupTests(unittest.TestCase):
    def test_load_real_groups(self) -> None:
        g = load_groups(ROOT / "iot" / "groups.json")
        self.assertIn("entryway", g)
        self.assertIn("livingroom", g)
        self.assertEqual(len(g["entryway"]["members"]), 4)
        self.assertEqual(len(g["livingroom"]["members"]), 2)

    def test_expand_group_targets(self) -> None:
        self.assertEqual(
            expand_target_members("entryway", registry=SAMPLE, groups=GROUPS),
            ["entryway1", "entryway2"],
        )
        self.assertEqual(
            expand_target_members("group:livingroom", registry=SAMPLE, groups=GROUPS),
            ["livingroom1", "livingroom2"],
        )
        self.assertEqual(
            len(expand_target_members("all", registry=SAMPLE, groups=GROUPS)),
            4,
        )

    def test_control_intent_group(self) -> None:
        intent = build_control_intent(
            "livingroom", "warm", 160, registry=SAMPLE, groups=GROUPS
        )
        self.assertTrue(intent["ok"])
        self.assertEqual(intent["action"], "on")
        self.assertEqual(len(intent["targets"]), 2)
        names = {t["name"] for t in intent["targets"]}
        self.assertEqual(names, {"livingroom1", "livingroom2"})

    def test_control_intent_entryway_off(self) -> None:
        intent = build_control_intent(
            "entryway", "off", registry=SAMPLE, groups=GROUPS
        )
        self.assertTrue(intent["ok"])
        self.assertEqual(intent["action"], "off")
        self.assertEqual(len(intent["targets"]), 2)

    def test_execute_group_via_fake(self) -> None:
        t = FakeTransport()
        result = run_async(
            execute_control(
                "entryway",
                "cyan",
                100,
                registry=SAMPLE,
                groups=GROUPS,
                transport=t,
            )
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["results"]), 2)
        ips = {c["ip"] for c in t.calls if c["op"] == "on"}
        self.assertEqual(ips, {"192.168.100.106", "192.168.100.118"})

    def test_list_groups_filters_missing(self) -> None:
        listed = list_groups(GROUPS, SAMPLE)
        ew = next(g for g in listed if g["id"] == "entryway")
        self.assertEqual(ew["count"], 2)
        self.assertEqual(ew["missing"], [])


class SolarTests(unittest.TestCase):
    def test_denver_equinox_has_sunrise_sunset(self) -> None:
        # Approximate Denver
        times = sun_times_local(
            date(2026, 3, 20), 39.7392, -104.9903, "America/Denver"
        )
        self.assertTrue(times["ok"])
        self.assertIsNotNone(times["sunrise_hhmm"])
        self.assertIsNotNone(times["sunset_hhmm"])
        # sunrise morning-ish, sunset evening-ish (local)
        rise_h = int(times["sunrise_hhmm"].split(":")[0])
        set_h = int(times["sunset_hhmm"].split(":")[0])
        self.assertLess(rise_h, 12)
        self.assertGreaterEqual(set_h, 12)


class ScheduleTests(unittest.TestCase):
    def _sched(self) -> dict:
        return {
            "location": {
                "latitude": 39.7392,
                "longitude": -104.9903,
                "timezone": "America/Denver",
            },
            "routines": [
                {
                    "id": "sunset_all_on",
                    "enabled": True,
                    "trigger": "sunset",
                    "offset_minutes": 0,
                    "target": "all",
                    "color": "magenta",
                    "brightness": 180,
                },
                {
                    "id": "sunrise_all_off",
                    "enabled": True,
                    "trigger": "sunrise",
                    "offset_minutes": 0,
                    "target": "all",
                    "color": "off",
                },
            ],
        }

    def test_shipped_sunset_routine_is_magenta(self) -> None:
        sched = load_schedule(ROOT / "iot" / "schedule.json")
        sunset = next(
            r for r in sched["routines"] if r["id"] in ("sunset_lights_on", "sunset_all_on")
        )
        self.assertEqual(sunset["color"], "magenta")
        # Plant lights day cycle + office plug must stay in shipped schedule.
        ids = {r["id"] for r in sched["routines"]}
        self.assertIn("sunrise_plants_on", ids)
        self.assertIn("sunrise_office_off", ids)
        self.assertIn("sunset_plants_off", ids)

    def test_upcoming_has_both_triggers(self) -> None:
        items = upcoming_for_day(self._sched(), date(2026, 6, 21))
        triggers = {i["trigger"] for i in items}
        self.assertEqual(triggers, {"sunrise", "sunset"})
        for i in items:
            self.assertIsNotNone(i["fire_at"])

    def test_due_within_window_not_before(self) -> None:
        sched = self._sched()
        tz = ZoneInfo("America/Denver")
        d = date(2026, 6, 21)
        items = upcoming_for_day(sched, d)
        sunset = next(i for i in items if i["trigger"] == "sunset")
        when = datetime.fromisoformat(sunset["fire_at"])
        # 2 minutes after sunset → due
        now = when.replace(tzinfo=when.tzinfo) + __import__("datetime").timedelta(minutes=2)
        due = due_routines(sched, {"fired": {}}, now=now, window_minutes=15)
        self.assertTrue(any(x["id"] == "sunset_all_on" for x in due))
        # before sunset → not due
        due_before = due_routines(
            sched, {"fired": {}}, now=when - __import__("datetime").timedelta(minutes=5)
        )
        self.assertFalse(any(x["id"] == "sunset_all_on" for x in due_before))
        # already fired → not due
        state = mark_fired({"fired": {}}, fire_key("sunset_all_on", d), now=now)
        due2 = due_routines(sched, state, now=now)
        self.assertFalse(any(x["id"] == "sunset_all_on" for x in due2))

    def test_run_due_calls_control(self) -> None:
        sched = self._sched()
        with tempfile.TemporaryDirectory() as td:
            sp = Path(td) / "schedule.json"
            st = Path(td) / "state.json"
            sp.write_text(json.dumps(sched), encoding="utf-8")
            st.write_text(json.dumps({"fired": {}}), encoding="utf-8")
            items = upcoming_for_day(sched, date(2026, 6, 21))
            sunset = next(i for i in items if i["trigger"] == "sunset")
            when = datetime.fromisoformat(sunset["fire_at"])
            now = when + __import__("datetime").timedelta(minutes=1)
            calls = []

            def control(target, color, brightness):
                calls.append((target, color, brightness))
                return {"ok": True, "target": target, "color": color}

            results = run_due(
                control=control, schedule_path=sp, state_path=st, now=now
            )
            self.assertEqual(len(results), 1)
            self.assertEqual(calls[0][0], "all")
            self.assertEqual(calls[0][1], "magenta")
            # second run same day should no-op
            results2 = run_due(
                control=control, schedule_path=sp, state_path=st, now=now
            )
            self.assertEqual(len(results2), 0)

    def test_run_routine_now(self) -> None:
        sched = self._sched()
        with tempfile.TemporaryDirectory() as td:
            sp = Path(td) / "schedule.json"
            st = Path(td) / "state.json"
            sp.write_text(json.dumps(sched), encoding="utf-8")
            st.write_text(json.dumps({"fired": {}}), encoding="utf-8")
            calls = []

            def control(target, color, brightness):
                calls.append((target, color, brightness))
                return {"ok": True}

            out = run_routine_now(
                "sunset_all_on",
                control=control,
                schedule_path=sp,
                state_path=st,
                mark=False,
            )
            self.assertTrue(out["ok"])
            self.assertEqual(calls[0][1], "magenta")
            # status includes next_event when location set
            status = schedule_status(sched, {"fired": {}})
            self.assertIn("next_event", status)

    def test_schedule_status_requires_location(self) -> None:
        status = schedule_status(
            {"location": {}, "routines": []},
            {"fired": {}},
        )
        self.assertTrue(status["ok"])
        self.assertFalse(status["location_configured"])
        self.assertIsNotNone(status["note"])


if __name__ == "__main__":
    unittest.main()
