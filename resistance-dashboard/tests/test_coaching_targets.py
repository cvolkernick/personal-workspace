"""Coaching targets: settings override, otherwise the phase calendar.

No override leaves the coach card and Today on the calendar (9000 from
2026-11-03, #1369). A settings value replaces that number. The SQLite
migration is additive and the target columns are nullable.
"""

from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from rt_dashboard.agent_today import _targets
from rt_dashboard.coaching_targets import (
    BOUNDS,
    db_path,
    effective_targets,
    load_overrides,
    respond_get,
    respond_set,
    set_override,
    validate_assignment,
)
from rt_dashboard.cut_coach import decide, today_targets
from rt_dashboard.hydration_bars import build_hydration_bars_payload, rules_target_ml
from rt_dashboard.phase_calendar import steps_resolution

TOKEN = "coaching-test-token"
DAY = "2026-11-04"


def _state():
    return {
        "kcal_target": 2000,
        "step_target": 7000,
        "phase": "cut",
        "weight_lbs": 170,
        "last_step_up_date": None,
        "step_events": [],
    }


class CoachingTargets(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {
            "FITDASH_COACHING_TARGETS_BACKEND": "sqlite",
            "FITDASH_COACHING_TARGETS_DB": str(Path(self.tmp.name) / "targets.sqlite3"),
            "FITDASH_AGENT_TOKEN": TOKEN,
            "TURSO_DATABASE_URL": "",
            "TURSO_AUTH_TOKEN": "",
        }
        self._env = mock.patch.dict(os.environ, env, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def _headers(self, token=TOKEN):
        return {"Authorization": f"Bearer {token}"}

    def test_read_does_not_create_the_database(self):
        view = effective_targets(DAY)
        self.assertFalse(db_path().exists())
        self.assertEqual(view["targets"]["steps"]["source"], "phase_calendar")
        self.assertIsNone(view["targets"]["steps"]["override"])

    def test_calendar_fallback_matches_the_schedule(self):
        for day, steps in (("2026-11-02", 8000), ("2026-11-03", 9000), (DAY, 9000)):
            resolved = steps_resolution(day)["step_target"]
            self.assertEqual(resolved, steps, day)
            view = effective_targets(day)
            self.assertEqual(view["targets"]["steps"]["effective"], steps, day)
            self.assertEqual(view["targets"]["steps"]["calendar"], steps, day)
            self.assertEqual(view["targets"]["steps"]["source"], "phase_calendar", day)
            card = decide(as_of=day, state=_state())
            self.assertEqual(card["targets"]["step_target"], steps, day)
            self.assertEqual(today_targets(card)["step_target"], steps, day)
        self.assertIsNone(effective_targets(DAY)["targets"]["kcal"]["effective"])
        self.assertEqual(effective_targets(DAY)["targets"]["hydration_ml"]["effective"], 3000)
        self.assertEqual(effective_targets(DAY)["targets"]["hydration_ml"]["source"], "coach_rules")

    def test_settings_override_wins_on_the_card_and_today(self):
        set_override("steps", 12000)
        set_override("kcal", 1800)
        set_override("hydration_ml", 2500)
        view = effective_targets(DAY)
        self.assertEqual(view["targets"]["steps"]["effective"], 12000)
        self.assertEqual(view["targets"]["steps"]["source"], "settings")
        self.assertEqual(view["targets"]["steps"]["calendar"], 9000)
        self.assertEqual(view["targets"]["kcal"]["effective"], 1800)
        self.assertEqual(view["targets"]["hydration_ml"]["effective"], 2500)
        card = decide(as_of=DAY, state=_state())
        shown = today_targets(card)
        self.assertEqual(card["targets"]["step_target"], 12000)
        self.assertEqual(shown["step_target"], 12000)
        self.assertEqual(card["targets"]["kcal_target"], 1800)
        self.assertEqual(shown["kcal_target"], 1800)
        self.assertEqual(card["targets"]["hydration_ml"], 2500)
        self.assertEqual(shown["hydration_ml"], 2500)
        self.assertEqual(rules_target_ml(), 2500.0)
        bars = build_hydration_bars_payload(target_ml_override=rules_target_ml(), as_of=DAY)
        self.assertEqual(bars["target_ml"], 2500.0)
        books = _targets({"nutrition_store": {"targets": {"calories": 2100.0, "protein_g": 170.0}}})
        self.assertEqual(books["calories"], 1800.0)
        self.assertEqual(books["protein_g"], 170.0)

    def test_null_clears_one_target_and_leaves_the_others(self):
        set_override("steps", 11000)
        set_override("kcal", 1900)
        set_override("steps", None)
        view = effective_targets(DAY)
        self.assertIsNone(view["targets"]["steps"]["override"])
        self.assertEqual(view["targets"]["steps"]["effective"], 9000)
        self.assertEqual(view["targets"]["steps"]["source"], "phase_calendar")
        self.assertEqual(view["targets"]["kcal"]["override"], 1900)
        card = decide(as_of=DAY, state=_state())
        self.assertEqual(card["targets"]["step_target"], 9000)
        self.assertEqual(card["targets"]["kcal_target"], 1900)

    def test_migration_columns_are_nullable(self):
        set_override("hydration_ml", 3000)
        conn = sqlite3.connect(str(db_path()))
        try:
            info = list(conn.execute("PRAGMA table_info(coaching_target_settings)"))
            by_name = {row[1]: row for row in info}
            for name in ("steps", "kcal", "hydration_ml", "updated_at", "updated_by"):
                self.assertIn(name, by_name)
                self.assertEqual(by_name[name][3], 0, name)
            conn.execute(
                "INSERT INTO coaching_target_settings(user_id, steps, kcal, hydration_ml) VALUES (?, ?, ?, ?)",
                ("other", None, None, None),
            )
            conn.commit()
        finally:
            conn.close()
        stored = load_overrides("other")
        self.assertEqual(
            {key: stored[key] for key in ("steps", "kcal", "hydration_ml")},
            {"steps": None, "kcal": None, "hydration_ml": None},
        )

    def test_bounds(self):
        self.assertEqual(BOUNDS["steps"], (1000, 30000))
        self.assertEqual(validate_assignment("steps", 1000), ("steps", 1000))
        self.assertEqual(validate_assignment("steps", 30000), ("steps", 30000))
        self.assertEqual(validate_assignment("kcal", 1200), ("kcal", 1200))
        self.assertEqual(validate_assignment("kcal", 6000), ("kcal", 6000))
        self.assertEqual(validate_assignment("hydration_ml", 500), ("hydration_ml", 500))
        self.assertEqual(validate_assignment("hydration_ml", 8000), ("hydration_ml", 8000))
        self.assertEqual(validate_assignment("steps", None), ("steps", None))
        for target, value in (
            ("steps", 999),
            ("steps", 30001),
            ("steps", True),
            ("steps", 9000.5),
            ("steps", "9000"),
            ("kcal", 1199),
            ("kcal", 6001),
            ("hydration_ml", 499),
            ("hydration_ml", 8001),
            ("fiber", 35),
        ):
            with self.assertRaises(ValueError):
                validate_assignment(target, value)

    def test_agent_token_gates_get_and_set(self):
        status, body = respond_get({})
        self.assertEqual(status, 401)
        self.assertNotIn(TOKEN, str(body))
        status, body = respond_get(self._headers("nope"), f"as_of={DAY}")
        self.assertEqual(status, 401)
        status, body = respond_get(self._headers(), f"as_of={DAY}")
        self.assertEqual(status, 200)
        self.assertEqual(body["targets"]["steps"]["effective"], 9000)
        status, body = respond_set(self._headers(), {"target": "steps", "value": 500})
        self.assertEqual(status, 400)
        self.assertIn("1000", body["message"])
        status, body = respond_set(self._headers(), {"target": "steps", "value": 15000, "note": "x"})
        self.assertEqual(status, 400)
        status, body = respond_set({}, {"target": "steps", "value": 15000})
        self.assertEqual(status, 401)
        self.assertFalse(db_path().exists())
        status, body = respond_set(self._headers(), {"target": "steps", "value": 15000})
        self.assertEqual(status, 200)
        self.assertEqual(body["targets"]["steps"]["effective"], 15000)
        self.assertEqual(body["targets"]["steps"]["source"], "settings")
        status, body = respond_get(self._headers(), "as_of=not-a-date")
        self.assertEqual(status, 400)

    def test_route_is_wired_without_a_ui_change(self):
        server = Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8")
        self.assertEqual(server.count('"/api/agent/coaching_targets"'), 3)
        static = Path(__file__).resolve().parents[1].joinpath("static")
        html_path = static / "index.html"
        html = html_path.read_text(encoding="utf-8")
        mcp = html[html.index('id="mcp-section"') : html.index('id="connections-card"')]
        self.assertIn("get_coaching_targets", mcp)
        self.assertNotIn("<button", mcp[mcp.index('id="more-mcp-body"') :])
        self.assertNotIn("coaching_targets", html.replace(mcp, ""))
        for path in static.rglob("*"):
            if path.resolve() == html_path.resolve():
                continue
            if path.suffix in {".js", ".html", ".css"}:
                self.assertNotIn(
                    "coaching_targets",
                    path.read_text(encoding="utf-8", errors="ignore"),
                    str(path),
                )


if __name__ == "__main__":
    unittest.main()
