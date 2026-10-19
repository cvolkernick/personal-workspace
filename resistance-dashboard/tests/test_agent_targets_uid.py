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


class _Store:
    """Stateful fake Turso for nutrition_targets (SELECT / INSERT upsert)."""

    def __init__(self):
        self.rows = {"default": dict(ROWS["default"], payload=json.dumps(ROWS["default"]["payload"]))}

    def __call__(self):
        store = self

        class C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=()):
                up = sql.strip().upper()
                res = None
                if up.startswith("INSERT"):
                    uid, blob, now, by = params
                    store.rows[uid] = {"payload": blob, "updated_at": now, "updated_by": by}
                elif up.startswith("SELECT"):
                    r = store.rows.get(params[0])
                    res = dict(r) if r else None

                class R:
                    def fetchone(self_inner):
                        return res
                return R()
        return C()


class CoachApplyEqualsAgent(unittest.TestCase):
    APPLIED = {"calories": 2150, "protein_g": 170, "carbs_g": 230, "fat_g": 60,
               "fiber_g": 30, "sugar_g": 55, "sodium_mg": 2300, "phase": "cut"}
    RECOMMENDED = {"calories": 2200, "protein_g": 170, "carbs_g": 245, "fat_g": 60}

    def test_agent_targets_equal_dashboard_targets_after_coach_apply(self):
        from rt_dashboard.agent_today import _targets as today_targets
        from rt_dashboard.nutrition_store import applied_targets_write, load_preview_targets

        store = _Store()
        env = {"FITDASH_USER_ID": CHRIS, "FITDASH_INVENTORY_AGENT_USER_ID": CHRIS}
        with mock.patch.dict(os.environ, env), \
             mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=True), \
             mock.patch("rt_dashboard.turso_http.connect", side_effect=store):
            # Coach apply under Chris's session uid.
            stuck, err, _w, _p = applied_targets_write(dict(self.APPLIED), CHRIS, message="coach apply")
            self.assertTrue(stuck, err)
            dash_targets, src = load_preview_targets(CHRIS)  # what the dashboard renders
            dash = {"nutrition_store": {"targets": dash_targets},
                    "coach": {"nutrition_targets": {"phase": "cut", "recommended": self.RECOMMENDED}}}
            rec = agent_read.Loaders(dashboard=lambda: dash).targets()
            data, _ = agent_read.build_targets(rec, agent_read._recommended(dash))
            today = today_targets(dash)
            # cookie-less read is redirected to the Coach row (no default drift)
            redirected, _ = load_preview_targets("")
        self.assertEqual(src, "turso")
        self.assertEqual(data["kcal"], dash_targets["calories"])
        for k in ("protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g", "sodium_mg"):
            self.assertEqual(data["macros"][k], float(dash_targets[k]), k)
            self.assertEqual(today[k], float(dash_targets[k]), k)
        self.assertEqual((data["kcal"], data["macros"]["protein_g"], data["macros"]["carbs_g"],
                          data["macros"]["fat_g"], data["macros"]["fiber_g"], data["macros"]["sugar_g"],
                          data["macros"]["sodium_mg"]),
                         (2150.0, 170.0, 230.0, 60.0, 30.0, 55.0, 2300.0))
        self.assertEqual(data["phase"], "cut")
        self.assertEqual(today["phase"], "cut")
        applied_at = store.rows[CHRIS]["updated_at"]
        self.assertRegex(applied_at, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(data["updated_at"], applied_at)
        self.assertEqual(data["applied_at"], applied_at)
        self.assertEqual(today["updated_at"], applied_at)
        self.assertEqual(data["recommended"]["calories"], 2200.0)
        self.assertEqual(data["recommended"]["carbs_g"], 245.0)
        self.assertEqual(data["recommended"]["phase"], "cut")
        self.assertEqual(today["recommended"]["calories"], 2200.0)
        self.assertEqual(redirected["calories"], 2150.0)
        # stale default row untouched and unread
        self.assertEqual(json.loads(store.rows["default"]["payload"])["calories"], 2100.0)
