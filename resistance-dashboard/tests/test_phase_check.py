"""FF-4 Phase check: the counter is body_fat_phase.evaluate(); display only."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import body_fat_phase as bf
from rt_dashboard import phase_check as pcm
from rt_dashboard.coach_steps import clear_rules_cache
from rt_dashboard.cut_coach import agent_coach_card_body, agent_phase_body, coach_card_for_payload

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CUT_LIVE = {"phase": "cut", "calories": 1875, "protein_g": 160, "carbs_g": 180, "fat_g": 55}
TODAY = "2026-11-26"


def _days(start: str, n: int) -> list:
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _row(day: str, value: float, i: int = 0) -> dict:
    return {"date": day, "percentage": value, "recorded_at": f"{day}T12:00:{i:02d}Z"}


def today_fixture() -> list:
    """One reading a day. 7-day medians miss on 11/13–11/18 and are <=10% on 11/19–11/26."""
    rows = []
    for day in _days("2026-10-25", 33):
        rows.append(_row(day, 10.3 if day <= "2026-11-15" else 9.6))
    return rows


def bulk_fixture() -> list:
    rows = []
    for day in _days("2026-10-25", 33):
        rows.append(_row(day, 14.0 if day <= "2026-11-15" else 15.4))
    return rows


def _payload(rows: list, phase: str = "cut") -> dict:
    targets = dict(CUT_LIVE)
    targets["phase"] = phase
    return {
        "meta": {"local_today": TODAY, "user_id": "u"},
        "health": {"body_fat": rows},
        "nutrition_store": {"targets": targets},
    }


class _Env(unittest.TestCase):
    def setUp(self):
        clear_rules_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ,
            {
                "FITDASH_COACH_STATE_DIR": self.tmp.name,
                "LOCAL_WORKSPACE_DIR": self.tmp.name,
                "TURSO_DATABASE_URL": "",
                "TURSO_AUTH_TOKEN": "",
            },
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
        clear_rules_cache()


class CounterIsTheRule(_Env):
    def _assert_parity(self, rows, phase):
        payload = _payload(rows, phase)
        card = coach_card_for_payload(payload, as_of=TODAY)
        pc = card["phase_check"]
        ev = bf.evaluate(rows, as_of=TODAY, phase=phase)
        self.assertEqual(pc["hits"], ev["hits"])
        self.assertEqual(pc["counted"], ev["counted"])
        self.assertEqual(pc["median_14d"], ev["median_14d"])
        self.assertEqual(pc["median_7d"], ev["median_7d"])
        self.assertEqual(pc["hits_needed"], ev["hits_needed"])
        self.assertEqual(pc["counted_needed"], ev["counted_needed"])
        # Same numbers as the coach card's own bf_phase block.
        for key in ("hits", "counted", "median_14d", "median_7d"):
            self.assertEqual(pc[key], card["bf_phase"][key], key)
        return pc, ev

    def test_cut_today_is_8_of_12_earliest_monday_11_30(self):
        pc, ev = self._assert_parity(today_fixture(), "cut")
        self.assertEqual(ev["hits"], 8)
        self.assertEqual(ev["counted"], 14)
        self.assertEqual(pc["earliest_switch_date"], "2026-11-30")
        self.assertEqual(pc["earliest_switch_label"], "Mon 11/30")
        self.assertEqual(pc["state"], "holding")
        self.assertEqual(pc["endpoint"], bf.CUT_AT)
        self.assertEqual(pc["direction"], "<=")
        misses = [r["date"] for r in ev["counted_days"] if not r["past"]]
        self.assertEqual(misses, _days("2026-11-13", 6))

    def test_bulk_mirror(self):
        pc, ev = self._assert_parity(bulk_fixture(), "slow_bulk")
        self.assertEqual(pc["phase"], "bulk")
        self.assertEqual(pc["endpoint"], bf.BULK_AT)
        self.assertEqual(pc["direction"], ">=")
        self.assertEqual(ev["hits"], 8)
        self.assertEqual(pc["earliest_switch_date"], "2026-11-30")
        self.assertIsNone(pc["ramp"])

    def test_phase_endpoint_and_agent_bodies_carry_the_same_block(self):
        payload = _payload(today_fixture())
        phase = agent_phase_body(payload, as_of=TODAY)
        card = agent_coach_card_body(payload, as_of=TODAY)
        self.assertEqual(phase["phase_check"], card["coach_card"]["phase_check"])
        self.assertTrue(phase["phase_check"]["applies_automatically"])
        # Existing phase_report keys are untouched.
        self.assertEqual(phase["hits"], 8)
        self.assertEqual(phase["phase"], "cut")


class EarliestSwitch(unittest.TestCase):
    def test_already_reached_is_today(self):
        rows = [_row(d, 9.5) for d in _days("2026-10-20", 38)]
        ev = bf.evaluate(rows, as_of=TODAY, phase="cut")
        self.assertGreaterEqual(ev["hits"], 12)
        self.assertEqual(pcm.earliest_switch_date(ev), TODAY)

    def test_unreachable_is_null(self):
        rows = today_fixture()
        ev = bf.evaluate(rows, as_of=TODAY, phase="maintain")
        self.assertIsNone(pcm.earliest_switch_date(ev))
        self.assertIsNone(pcm.earliest_switch_date({"phase": "cut", "as_of": ""}))

    def test_thin_today_counts_from_today(self):
        rows = [r for r in today_fixture() if r["date"] <= "2026-11-19"]
        ev = bf.evaluate(rows, as_of=TODAY, phase="cut")
        self.assertTrue(ev["thin"])
        day = pcm.earliest_switch_date(ev)
        self.assertIsNotNone(day)
        self.assertGreaterEqual(day, TODAY)


class NotEnoughData(_Env):
    def _pc(self, rows):
        card = coach_card_for_payload(_payload(rows), as_of=TODAY)
        return card["phase_check"]

    def test_zero_readings(self):
        pc = self._pc([])
        self.assertEqual(pc["state"], "not_enough_data")
        self.assertEqual(pc["counted"], 0)
        self.assertIsNone(pc["median_14d"])
        self.assertIsNone(pc["bf_avg_14d"])

    def test_three_readings_in_seven_days_is_thin(self):
        rows = [_row("2026-11-24", 9.5), _row("2026-11-25", 9.5), _row("2026-11-26", 9.5)]
        pc = self._pc(rows)
        self.assertTrue(pc["thin"])
        self.assertEqual(pc["state"], "not_enough_data")

    def test_fewer_than_14_counted_days(self):
        rows = [_row(d, 9.5) for d in _days("2026-11-12", 15)]
        ev = bf.evaluate(rows, as_of=TODAY, phase="cut")
        self.assertLess(ev["counted"], 14)
        pc = self._pc(rows)
        self.assertEqual(pc["counted"], ev["counted"])
        self.assertEqual(pc["state"], "not_enough_data")

    def test_thin_days_skip_without_resetting(self):
        # A 5-day hole (11/15–11/19) makes 11/18–11/22 thin. They are skipped,
        # not reset: the 14 counted days reach back before 11/13, and the
        # early misses (10.3) still count as misses next to the later hits.
        rows = [
            _row(d, 10.3 if d <= "2026-11-10" else 9.5)
            for d in _days("2026-10-25", 33)
            if not ("2026-11-15" <= d <= "2026-11-19")
        ]
        ev = bf.evaluate(rows, as_of=TODAY, phase="cut")
        pc = self._pc(rows)
        dates = [r["date"] for r in ev["counted_days"]]
        for thin_day in _days("2026-11-18", 5):
            self.assertNotIn(thin_day, dates)
        self.assertEqual(pc["counted"], 14)
        self.assertTrue(any(d < "2026-11-13" for d in dates))
        self.assertEqual(pc["hits"], ev["hits"])
        self.assertEqual(pc["hits"], sum(1 for r in ev["counted_days"] if r["past"]))
        self.assertGreater(14 - pc["hits"], 0)


class DisplayFields(unittest.TestCase):
    def _ev(self):
        return bf.evaluate(today_fixture(), as_of=TODAY, phase="cut")

    def test_on_switch_is_maintenance_targets_and_ramp(self):
        card = {"weekly": {"tdee": {"tdee": 2344}}, "rate": {"two_week": 0.108, "band_pct": [0.25, 0.5], "position": "below"}}
        pc = pcm.build_phase_check({"health": {"body_fat": today_fixture()}}, card, self._ev())
        m = bf.maintenance_targets(2344)
        self.assertEqual(pc["on_switch"]["kcal"], m["calories"])
        self.assertEqual(pc["on_switch"]["protein_g"], m["protein_g"])
        self.assertEqual(pc["on_switch"]["fat_g"], m["fat_g"])
        self.assertEqual(pc["on_switch"]["carbs_g"], m["carbs_g"])
        self.assertEqual(pc["ramp"]["kcal"], bf.ramp_targets(2344)["calories"])
        self.assertEqual(pc["ramp"]["after_days"], bf.RAMP_AFTER_DAYS)
        self.assertEqual(pc["ramp"]["plus_kcal"], bf.ramp_targets(0)["calories"])
        self.assertEqual(pc["weight_rate"]["two_week_pct"], 0.11)
        self.assertEqual(pc["weight_rate"]["position"], "below")

    def test_missing_tdee_is_null(self):
        pc = pcm.build_phase_check({}, {"weekly": {}}, self._ev())
        self.assertIsNone(pc["on_switch"])
        self.assertIsNone(pc["ramp"]["kcal"])

    def test_applies_automatically_follows_bf_auto_flip(self):
        with mock.patch.object(bf, "auto_flip_enabled", return_value=False):
            pc = pcm.build_phase_check({}, {}, self._ev())
        self.assertFalse(pc["applies_automatically"])
        self.assertFalse(pc["auto_switch"])
        with mock.patch.object(bf, "auto_flip_enabled", return_value=True):
            pc = pcm.build_phase_check({}, {}, self._ev())
        self.assertTrue(pc["applies_automatically"])

    def test_building_never_calls_a_target_writer(self):
        import rt_dashboard.nutrition_store as ns

        boom = mock.Mock(side_effect=AssertionError("writer called"))
        patches = [
            mock.patch.object(bf, "commit_targets", boom),
            mock.patch.object(ns, "applied_targets_write", boom),
            mock.patch.object(ns, "persist_targets", boom),
        ]
        if hasattr(ns, "update_targets"):
            patches.append(mock.patch.object(ns, "update_targets", boom))
        for p in patches:
            p.start()
        try:
            card = {"weekly": {"tdee": {"tdee": 2344}}}
            pcm.build_phase_check({"health": {"body_fat": today_fixture()}}, card, self._ev())
            bulk = bf.evaluate(bulk_fixture(), as_of=TODAY, phase="bulk")
            pcm.build_phase_check({"health": {"body_fat": bulk_fixture()}}, card, bulk)
        finally:
            for p in patches:
                p.stop()
        boom.assert_not_called()
        src = (ROOT / "rt_dashboard" / "phase_check.py").read_text(encoding="utf-8")
        for name in ("commit_targets(", "update_targets(", "persist_targets(", "applied_targets_write("):
            self.assertNotIn(name, src)

    def test_bf_avg_14d_matches_js_trailing_average(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        rows = today_fixture() + [
            {"date": "2026-11-20", "percentage": 9.6, "recorded_at": "2026-11-20T12:00:00.500Z"},  # twin
            {"date": "2026-11-21", "percentage": 11.37, "recorded_at": "2026-11-21T19:00:00Z"},
        ]
        for as_of in ("2026-11-26", "2026-11-21", "2026-11-05"):
            py = pcm.bf_avg_14d(rows, as_of)
            script = (
                "const bf=require(process.argv[1]);"
                "const rows=JSON.parse(process.argv[2]);"
                "process.stdout.write(JSON.stringify(bf.trailingAverage(rows,[process.argv[3]],14)[0]));"
            )
            out = subprocess.run(
                [node, "-e", script, str(STATIC / "body-fat-series.js"), json.dumps(rows), py["date"]],
                capture_output=True, text=True, check=True,
            ).stdout
            self.assertAlmostEqual(py["value"], json.loads(out), delta=0.01)
        self.assertEqual(pcm.bf_avg_14d([], TODAY), {"value": None, "date": None})
        old = [_row("2026-10-01", 10.0)]
        self.assertIsNone(pcm.bf_avg_14d(old, TODAY)["value"])


class AgentTodayBody(unittest.TestCase):
    def test_nullable_fields_when_empty(self):
        from rt_dashboard.agent_today import _body_today

        out = _body_today({}, TODAY)
        for key in ("bodyweight_lbs", "body_fat_pct", "body_fat_date", "body_fat_14d_avg"):
            self.assertIn(key, out)
            self.assertIsNone(out[key])

    def test_latest_values(self):
        from rt_dashboard.agent_today import _body_today

        data = {
            "health": {
                "weight": [
                    {"date": "2026-11-25", "weight_lbs": 171.2},
                    {"date": "2026-11-26", "weight_lbs": 170.84},
                    {"date": "2026-11-27", "weight_lbs": 999},
                ],
                "body_fat": today_fixture(),
            }
        }
        out = _body_today(data, TODAY)
        self.assertEqual(out["bodyweight_lbs"], 170.84)
        self.assertEqual(out["bodyweight_date"], TODAY)
        self.assertEqual(out["body_fat_pct"], 9.6)
        self.assertEqual(out["body_fat_date"], TODAY)
        self.assertAlmostEqual(out["body_fat_14d_avg"], pcm.bf_avg_14d(today_fixture(), TODAY)["value"], places=2)

    def test_export_carries_fields(self):
        from rt_dashboard.agent_today import export_agent_today

        body = export_agent_today({"meta": {"local_today": TODAY}, "coach": {"today": {"date": TODAY}}})
        for key in ("bodyweight_lbs", "body_fat_pct", "body_fat_date", "body_fat_14d_avg"):
            self.assertIn(key, body["today"])
            self.assertIsNone(body["today"][key])


class GuardrailsGridPins(unittest.TestCase):
    def test_client_skips_progression_and_appends_phase_check(self):
        start = APP.index("function renderGuardrails(data)")
        block = APP[start:APP.index("function guardrailTileHtml", start)]
        self.assertIn('tile.id === "flag"', block)
        self.assertIn("filter(", block)
        self.assertIn("guardrailPhaseCheckHtml(data && data.coach_card && data.coach_card.phase_check)", block)
        self.assertIn('if (!api || !pc) return "";', block)

    def test_server_tiles_still_include_progression(self):
        from rt_dashboard.guardrails import build_guardrails

        out = build_guardrails({"meta": {"local_today": TODAY}}, as_of=TODAY)
        self.assertEqual([t["id"] for t in out["tiles"]], ["tonnage", "lifts", "scale", "flag"])
        self.assertIn("flag", out)

    def test_phase_check_script_loads_before_app(self):
        self.assertLess(HTML.index("/phase-check.js?v="), HTML.index("/app.js?v="))
        sw = (STATIC / "sw.js").read_text(encoding="utf-8")
        self.assertIn('"/phase-check.js?v=phase-check-1"', sw)
        css = (STATIC / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".guardrail-tile.phase-check-tile {\n  grid-column: 1 / -1;", css)

    def test_coach_line_replaces_bf_lines_today_unchanged(self):
        self.assertIn('id="coach-bf-line"', HTML)
        for gone in ("coach-bf-avg", "coach-bf-threshold", "coach-bf-streak", "coach-bf-median14", "coach-bf-thin"):
            self.assertNotIn(f'id="{gone}"', HTML)
        self.assertIn('id="coach-bf-last"', HTML)
        self.assertIn('id="coach-rate-chip"', HTML)
        self.assertIn('id="bf-phase-banner"', HTML)


if __name__ == "__main__":
    unittest.main()
