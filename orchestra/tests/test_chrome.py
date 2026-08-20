"""Orchestra v1 chrome: WORLD / WEEK / GATES / HELD + Mode SoT."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "orchestra"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chrome import (  # noqa: E402
    MODE_CONFIG_PATH,
    build_chrome,
    build_dock,
    build_gates,
    build_held,
    build_week,
    build_world,
    load_mode_config,
    resolve_mode,
)
from payload import build_orchestra_payload  # noqa: E402

NY = ZoneInfo("America/New_York")
WED = datetime(2026, 8, 19, 12, 0, tzinfo=NY)  # Hinge A
SUN = datetime(2026, 8, 23, 21, 0, tzinfo=NY)  # Hinge B (evening still civil Sunday)
MON = datetime(2026, 8, 24, 8, 0, tzinfo=NY)  # Slow; Sunday-into-Monday note stays Hinge B
THU = datetime(2026, 8, 20, 10, 0, tzinfo=NY)
TUE_SERVICE = datetime(2026, 8, 25, 9, 0, tzinfo=NY)


def _gate_map(gates: dict) -> dict:
    return {g["id"]: g for g in gates.get("items") or []}


class ModeSoTTests(unittest.TestCase):
    def test_config_is_new_york(self) -> None:
        cfg = load_mode_config()
        self.assertEqual(cfg["timezone"], "America/New_York")
        self.assertTrue(MODE_CONFIG_PATH.is_file())
        self.assertEqual(cfg["weekday_modes"]["wed"], "hinge_a")
        self.assertEqual(cfg["weekday_modes"]["sun"], "hinge_b")
        self.assertEqual(cfg["weekday_modes"]["mon"], "slow")
        self.assertEqual(cfg["weekday_modes"]["thu"], "busy")

    def test_wednesday_is_hinge_a(self) -> None:
        mode = resolve_mode(WED)
        self.assertEqual(mode["id"], "hinge_a")
        self.assertEqual(mode["label"], "Hinge A")
        self.assertTrue(mode["is_hinge"])
        self.assertTrue(mode["success_empty"])
        self.assertEqual(mode["timezone"], "America/New_York")
        self.assertIn("success", mode["success_note"].lower())

    def test_sunday_is_hinge_b_not_clock_band(self) -> None:
        mode = resolve_mode(SUN)
        self.assertEqual(mode["id"], "hinge_b")
        self.assertEqual(mode["sunday_into_monday"], "hinge_b")
        self.assertNotIn("start_hour", mode)
        self.assertNotIn("hour_grid", mode)
        self.assertNotIn("bands", mode)

    def test_monday_is_slow_sunday_into_monday_still_hinge_b(self) -> None:
        mode = resolve_mode(MON)
        self.assertEqual(mode["id"], "slow")
        self.assertFalse(mode["is_hinge"])
        self.assertEqual(mode["sunday_into_monday"], "hinge_b")
        self.assertIn("Sunday-into-Monday", mode["sunday_into_monday_note"])


class WeekTests(unittest.TestCase):
    def test_week_modes_and_today_chip(self) -> None:
        week = build_week(WED)
        by = {d["weekday"]: d for d in week["days"]}
        self.assertEqual(len(week["days"]), 7)
        self.assertEqual(by["mon"]["mode"], "slow")
        self.assertEqual(by["tue"]["mode"], "slow")
        self.assertEqual(by["wed"]["mode"], "hinge_a")
        self.assertTrue(by["wed"]["today"])
        self.assertEqual(by["thu"]["mode"], "busy")
        self.assertEqual(by["fri"]["mode"], "busy")
        self.assertEqual(by["sat"]["mode"], "busy")
        self.assertEqual(by["sun"]["mode"], "hinge_b")
        self.assertTrue(week["hinges_offset"])
        self.assertEqual(week["sunday_into_monday"], "hinge_b")
        self.assertTrue(by["wed"]["is_hinge"])
        self.assertTrue(by["sun"]["is_hinge"])
        self.assertFalse(by["mon"]["is_hinge"])

    def test_personal_chip_only_service_center_on_2026_08_25(self) -> None:
        week = build_week(TUE_SERVICE)
        tue = next(d for d in week["days"] if d["weekday"] == "tue")
        self.assertEqual(tue["date"], "2026-08-25")
        self.assertEqual(tue["chips"], [{"date": "2026-08-25", "label": "Service center"}])
        # Current week (Wed 8/19) must not invent extra chips
        this_week = build_week(WED)
        chips = [c for d in this_week["days"] for c in d["chips"]]
        self.assertEqual(chips, [])


class GateTests(unittest.TestCase):
    def test_hinge_a_closes_drive_sleep_desk(self) -> None:
        gates = build_gates(now=WED)
        by = _gate_map(gates)
        self.assertTrue(gates["is_hinge"])
        self.assertFalse(by["drive"]["open"])
        self.assertFalse(by["sleep"]["open"])
        self.assertFalse(by["desk"]["open"])
        self.assertTrue(by["hinge_buffer"]["open"])
        for gid in ("drive", "sleep", "desk"):
            self.assertEqual(by[gid]["hint"], "not this window")

    def test_busy_day_opens_drive_sleep_desk(self) -> None:
        gates = build_gates(now=THU)
        by = _gate_map(gates)
        self.assertTrue(by["drive"]["open"])
        self.assertTrue(by["sleep"]["open"])
        self.assertTrue(by["desk"]["open"])
        self.assertFalse(by["hinge_buffer"]["open"])
        self.assertEqual(by["hinge_buffer"]["hint"], "not this window")

    def test_hinge_buffer_dashed_never_red(self) -> None:
        for when in (WED, THU, SUN):
            by = _gate_map(build_gates(now=when))
            buf = by["hinge_buffer"]
            self.assertEqual(buf["style"], "dashed")
            self.assertTrue(buf["never_red"])
            self.assertNotEqual(buf.get("severity"), "red")


class WorldHeldDockTests(unittest.TestCase):
    def test_world_placeholder_deep_links_horizon(self) -> None:
        world = build_world({"regime": {"available": False}})
        self.assertTrue(world["placeholder"])
        self.assertFalse(world["embed"])
        self.assertIn(":8795", world["url"])
        self.assertEqual(world["opens"], "horizon")

    def test_world_uses_regime_line_when_present(self) -> None:
        world = build_world(
            {
                "regime": {"available": True, "primary_label": "risk-off"},
                "implications": {
                    "top": [{"action": "Hold dry powder"}],
                },
            }
        )
        self.assertFalse(world["placeholder"])
        self.assertIn("risk-off", world["line"])
        self.assertIn("Hold dry powder", world["line"])

    def test_held_is_thin_bridge_not_board(self) -> None:
        held = build_held(
            {
                "note": "Macro backlog → day plan without merging UIs.",
                "candidates": [{"backlog_id": "bl-1", "title": "Ship chrome", "status": "ready"}],
                "linked": [{"id": "t1", "title": "On today"}],
                "workflow_url": "http://127.0.0.1:8765/",
                "allocator_url": "http://127.0.0.1:8770/",
            }
        )
        self.assertEqual(held["kind"], "workflow_to_holistic_today")
        self.assertEqual(len(held["items"]), 2)
        self.assertIn(":8765", held["workflow_url"])
        self.assertIn(":8770", held["allocator_url"])
        self.assertNotIn("sprint", json.dumps(held).lower())

    def test_dock_only_allocator_and_workflow(self) -> None:
        dock = build_dock(
            [
                {"id": "holistic", "url": "http://127.0.0.1:8770/", "live": True},
                {"id": "workflow", "url": "http://127.0.0.1:8765/", "live": False},
                {"id": "finance", "url": "http://127.0.0.1:8000/"},
                {"id": "fitness", "url": "http://127.0.0.1:8787/"},
            ]
        )
        ids = [d["id"] for d in dock]
        self.assertEqual(ids, ["holistic", "workflow"])
        self.assertEqual(dock[0]["port"], 8770)
        self.assertEqual(dock[1]["port"], 8765)


class PayloadAndUiTests(unittest.TestCase):
    def test_payload_includes_chrome(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategy").mkdir()
            (ws / "strategy" / "bets.md").write_text("# Bets\n- **AI**\n", encoding="utf-8")
            (ws / "strategy" / "today.md").write_text("# Today\n", encoding="utf-8")
            (ws / "initiatives").mkdir()
            (ws / "ops" / "backlog").mkdir(parents=True)
            (ws / "ops" / "backlog" / "items.json").write_text("[]\n", encoding="utf-8")
            payload = build_orchestra_payload(ws, probe_ports=False)
            self.assertTrue(payload["ok"])
            self.assertIn("day_plan", payload)
            chrome = payload["chrome"]
            self.assertIn("world", chrome)
            self.assertIn("week", chrome)
            self.assertIn("gates", chrome)
            self.assertIn("held", chrome)
            self.assertEqual([d["id"] for d in chrome["dock"]], ["holistic", "workflow"])
            self.assertFalse(chrome["world"]["embed"])
            self.assertIn(":8795", chrome["world"]["url"])

    def test_index_has_v1_chrome_without_horizon_embed(self) -> None:
        html = (ORCH / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="world-strip"', html)
        self.assertIn('id="sec-week"', html)
        self.assertIn('id="sec-gates"', html)
        self.assertIn('id="sec-held"', html)
        self.assertIn('id="sec-day-plan"', html)
        self.assertIn('id="domain-nav"', html)
        self.assertNotIn("<iframe", html.lower())
        self.assertNotIn("</iframe>", html.lower())
        # Dock children only — no 4th app / no Horizon tile
        self.assertIn("Time Allocator", html)
        self.assertIn("Workflow", html)
        self.assertIn(
            'chips: [\n          { id: "holistic", short: "Time Allocator", port: 8770, path: "/" },\n          { id: "workflow", short: "Workflow", port: 8765, path: "/" },',
            html,
        )
        self.assertNotIn('short: "FitDash"', html)
        self.assertNotIn('short: "FCC"', html)
        self.assertNotIn('short: "Horizon"', html)
        self.assertNotIn('short: "IoT"', html)
        self.assertNotIn('short: "Seasonal"', html)
        self.assertNotIn("port: 8000", html)
        self.assertNotIn("port: 8787", html)
        self.assertNotIn("port: 8780", html)
        self.assertNotIn("port: 8791", html)
        self.assertNotIn("port: 8792", html)
        self.assertIn("8795", html)


class AssembleTests(unittest.TestCase):
    def test_build_chrome_hinge_day_success(self) -> None:
        chrome = build_chrome(
            domains=[],
            bridge={"candidates": [], "linked": []},
            fan_in={"regime": {"available": False}, "implications": {"top": []}},
            now=WED,
        )
        self.assertTrue(chrome["mode"]["is_hinge"])
        self.assertTrue(chrome["gates"]["success_empty"])
        self.assertIn("success", chrome["mode"]["success_note"].lower())
        self.assertEqual(chrome["meta"]["timezone"], "America/New_York")


if __name__ == "__main__":
    unittest.main()
