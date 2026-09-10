"""#583: POST /api/targets is JSON on Vercel (rewrite onto dashboard)."""

from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import dispatch_client_route, targets_write

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
VERCEL = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))


def _headers():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


class TargetsRouteParity(unittest.TestCase):
    def test_frontend_posts_targets(self):
        self.assertIn('fetch("/api/targets"', APP_JS)
        self.assertIn("apply_coach: true", APP_JS)

    def test_rewrite_onto_dashboard_not_new_function(self):
        sources = {r["source"]: r["destination"] for r in VERCEL.get("rewrites") or []}
        self.assertEqual(sources.get("/api/targets"), "/api/dashboard?_r=targets")
        self.assertFalse((ROOT / "api" / "targets.py").exists())

    def test_app_js_api_paths_have_handler_or_rewrite(self):
        """Drift: frontend /api/* calls must hit a rewrite or a real function.

        Pi-only Google Health OAuth stays on server.py.
        """
        fetches = set(re.findall(r'fetch\("/api/([^"?]+)', APP_JS))
        rewrites = {r["source"] for r in VERCEL.get("rewrites") or []}
        pi_only = {"google-health/auth/start", "google-health/auth/status"}
        missing = []
        for path in sorted(fetches):
            if path in pi_only:
                continue
            if f"/api/{path}" in rewrites:
                continue
            nested_py = ROOT / "api" / f"{path}.py"
            if nested_py.is_file():
                continue
            missing.append(path)
        self.assertEqual(missing, [], missing)


class TargetsWriteJson(unittest.TestCase):
    def test_cookie_less_is_401_json(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = targets_write({}, {"apply_coach": True})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertFalse(body.get("ok", True))
        dumped = json.dumps(body).lower()
        self.assertNotIn("<html", dumped)
        self.assertNotIn("the page c", dumped)

    def test_save_targets_writes_and_returns_json(self):
        writes = []

        def fake_write(client, rel, data, message=""):
            writes.append((rel, dict(data), message))
            return {"ok": True, "path": rel, "github": True, "local": False}

        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "rt_dashboard.nutrition_store.write_nutrition_file",
            side_effect=fake_write,
        ):
            status, body = targets_write(
                _headers(),
                {
                    "calories": 2000,
                    "protein_g": 180,
                    "carbs_g": 160,
                    "fat_g": 50,
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["targets"]["calories"], 2000)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][0], "fitness/nutrition/targets.json")
        self.assertIn("update daily macro targets", writes[0][2])

    def test_apply_coach_missing_rec_is_400_json(self):
        rec = {"abstain": True, "recommended": None, "reasons": ["not enough days"]}
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "api.dashboard.dashboard_body",
            return_value=(200, {"coach": {"nutrition_targets": rec}}),
        ), mock.patch(
            "rt_dashboard.nutrition_store.write_nutrition_file",
        ) as write:
            status, body = dispatch_client_route(
                _headers(),
                "_r=targets",
                "POST",
                payload={"apply_coach": True},
            )
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "Coach has no recommendation to apply")
        self.assertEqual(body["action"], "apply_coach_targets")
        self.assertNotIn("<html", json.dumps(body).lower())
        write.assert_not_called()

    def test_apply_coach_abstain_still_writes_micros(self):
        rec = {
            "abstain": True,
            "phase": "cut",
            "as_of": "2026-09-10",
            "recommended": {
                "calories": 2100,
                "protein_g": 210,
                "carbs_g": 180,
                "fat_g": 55,
                "fiber_g": 30,
                "sugar_g": 50,
                "sodium_mg": 2300,
            },
        }
        writes = []

        def fake_write(client, rel, data, message=""):
            writes.append(dict(data))
            return {"ok": True, "path": rel, "github": True}

        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "api.dashboard.dashboard_body",
            return_value=(200, {"coach": {"nutrition_targets": rec}}),
        ), mock.patch(
            "rt_dashboard.nutrition_store.write_nutrition_file",
            side_effect=fake_write,
        ):
            status, body = targets_write(_headers(), {"apply_coach": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["targets"]["fiber_g"], 30)
        self.assertEqual(body["targets"]["sugar_g"], 50)
        self.assertEqual(body["targets"]["sodium_mg"], 2300)
        self.assertEqual(writes[0]["fiber_g"], 30)

    def test_apply_coach_writes_merged_targets(self):
        rec = {
            "abstain": False,
            "phase": "cut",
            "as_of": "2026-09-10",
            "recommended": {
                "calories": 1900,
                "protein_g": 200,
                "carbs_g": 150,
                "fat_g": 45,
            },
        }
        writes = []

        def fake_write(client, rel, data, message=""):
            writes.append((rel, dict(data), message))
            return {"ok": True, "path": rel, "github": True}

        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "api.dashboard.dashboard_body",
            return_value=(200, {"coach": {"nutrition_targets": rec}}),
        ), mock.patch(
            "rt_dashboard.nutrition_store.write_nutrition_file",
            side_effect=fake_write,
        ):
            status, body = targets_write(_headers(), {"apply_coach": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["action"], "apply_coach_targets")
        self.assertEqual(body["targets"]["calories"], 1900)
        self.assertEqual(body["targets"]["protein_g"], 200)
        self.assertEqual(len(writes), 1)
        self.assertIn("apply coach targets", writes[0][2])


if __name__ == "__main__":
    unittest.main()
