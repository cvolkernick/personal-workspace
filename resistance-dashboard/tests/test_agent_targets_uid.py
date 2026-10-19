"""Agent targets read Chris's Coach row, not the legacy ``default`` row.

Fixture mirrors prod Turso ``nutrition_targets`` on 2026-10-19: Coach applied
2150 / 170P / 230C / 60F, fiber 30, sugar 55, phase cut under Chris's id;
the stale ``default`` row is 2100 / 210P / 180C / 55F from 2026-09-11.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rt_dashboard import agent_read  # noqa: E402
from rt_dashboard.agent_today import _targets  # noqa: E402

CHRIS = "116947754611402469275"
ROWS = {
    CHRIS: {
        "payload": {"calories": 2150.0, "protein_g": 170.0, "carbs_g": 230.0, "fat_g": 60.0,
                    "fiber_g": 30.0, "sugar_g": 55.0, "sodium_mg": 2300.0, "phase": "cut",
                    "updated_at": "2026-10-19"},
        "updated_at": "2026-10-19T18:22:54Z",
    },
    "default": {
        "payload": {"calories": 2100.0, "protein_g": 210.0, "carbs_g": 180.0, "fat_g": 55.0,
                    "fiber_g": 30.0, "sugar_g": 50.0, "phase": None},
        "updated_at": "2026-09-11T11:49:16Z",
    },
}


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        assert sql.strip().upper().startswith("SELECT"), sql
        row = ROWS.get(params[0])

        class R:
            def fetchone(self_inner):
                if not row:
                    return None
                return {"payload": json.dumps(row["payload"]), "updated_at": row["updated_at"], "updated_by": None}
        return R()


def _patched(env):
    return (
        mock.patch.dict(os.environ, env, clear=False),
        mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=True),
        mock.patch("rt_dashboard.turso_http.connect", side_effect=lambda: _Conn()),
    )


class AgentTargetsUid(unittest.TestCase):
    def _read(self, env):
        clean = {k: "" for k in ("FITDASH_USER_ID", "FITDASH_INVENTORY_AGENT_USER_ID")}
        clean.update(env)
        a, b, c = _patched(clean)
        with a, b, c:
            return agent_read.build_targets(agent_read.Loaders(dashboard=dict).targets())[0]

    def test_reads_coach_row_not_default(self):
        data = self._read({"FITDASH_USER_ID": CHRIS})
        self.assertEqual(data["kcal"], 2150.0)
        self.assertEqual(
            {k: data["macros"][k] for k in ("protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g")},
            {"protein_g": 170.0, "carbs_g": 230.0, "fat_g": 60.0, "fiber_g": 30.0, "sugar_g": 55.0},
        )
        self.assertEqual(data["phase"], "cut")
        self.assertEqual(data["updated_at"], "2026-10-19T18:22:54Z")
        self.assertEqual(data["source"], "turso")

    def test_inventory_agent_id_is_fallback(self):
        data = self._read({"FITDASH_USER_ID": "default", "FITDASH_INVENTORY_AGENT_USER_ID": CHRIS})
        self.assertEqual(data["kcal"], 2150.0)

    def test_legacy_default_only_when_unbound(self):
        data = self._read({})
        self.assertEqual(data["kcal"], 2100.0)


class TodayTargetsFull(unittest.TestCase):
    def test_today_carries_full_applied_targets(self):
        t = _targets({"nutrition_store": {"targets": ROWS[CHRIS]["payload"]}})
        self.assertEqual(t["calories"], 2150.0)
        self.assertEqual(t["protein_g"], 170.0)
        self.assertEqual(t["carbs_g"], 230.0)
        self.assertEqual(t["fat_g"], 60.0)
        self.assertEqual(t["fiber_g"], 30.0)
        self.assertEqual(t["sugar_g"], 55.0)
        self.assertEqual(t["phase"], "cut")
        self.assertEqual(t["updated_at"], "2026-10-19")

    def test_today_server_binds_agent_uid(self):
        src = (ROOT / "server.py").read_text()
        i = src.index('if parsed.path == "/api/agent/today":')
        self.assertIn("agent_targets_uid()", src[i:i + 2500])


if __name__ == "__main__":
    unittest.main()
