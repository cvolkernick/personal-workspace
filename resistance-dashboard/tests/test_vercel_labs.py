"""Cookie-less Labs POST is 401 JSON, not a missing Vercel route."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import dispatch_client_route, labs_body, labs_write

ROOT = Path(__file__).resolve().parents[1]
VERCEL_JSON = ROOT / "vercel.json"


def _headers():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _twenty_marker_panel():
    markers = {}
    order = []
    for i in range(20):
        key = f"marker_{i}_ng_ml"
        markers[key] = {
            "id": f"marker_{i}",
            "name": f"Marker {i}",
            "value": 10.0 + i,
            "value_text": str(10 + i),
            "comparator": "",
            "unit": "ng/mL",
            "clinical_low": 0,
            "clinical_high": 100,
            "performance_low": 20,
            "performance_high": 80,
        }
        order.append(key)
    return {
        "date": "2026-06-01",
        "collected": "2026-05-30",
        "lab": "Rythm Health",
        "fasting": True,
        "markers": markers,
        "marker_order": order,
    }


class LabsRewrites(unittest.TestCase):
    def test_vercel_json_rewrites_onto_dashboard(self):
        raw = VERCEL_JSON.read_text(encoding="utf-8")
        self.assertIn("/api/labs/upload", raw)
        self.assertIn("/api/dashboard?_r=labs_upload", raw)
        self.assertIn("/api/labs/delete", raw)
        self.assertIn("/api/dashboard?_r=labs_delete", raw)
        self.assertIn('"/api/labs"', raw)
        self.assertIn("/api/dashboard?_r=labs", raw)
        self.assertNotIn("api/labs.py", raw)
        self.assertNotIn("api/labs/upload.py", raw)
        self.assertFalse((ROOT / "api" / "labs.py").exists())
        self.assertFalse((ROOT / "api" / "labs").is_dir())

    def test_hobby_function_count_stays_at_12(self):
        api = ROOT / "api"
        fns = []
        for path in api.rglob("*.py"):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            src = path.read_text(encoding="utf-8")
            if "class handler" in src or "\ndef app(" in src or "\napp = " in src:
                fns.append(path)
        self.assertEqual(len(fns), 12, [str(x.relative_to(ROOT)) for x in fns])


class CookieLessLabsRoutes(unittest.TestCase):
    def test_upload_401_json(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = labs_write({}, "labs_upload", {"content_base64": "QQ=="})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertFalse(body.get("ok", True))
        self.assertNotIn("<html", json.dumps(body).lower())
        self.assertNotIn("panel", body)
        self.assertNotIn("labs", body)

    def test_delete_401_json(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = labs_write({}, "labs_delete", {"date": "2026-06-01"})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertFalse(body.get("ok", True))
        self.assertNotIn("<html", json.dumps(body).lower())

    def test_get_labs_401_json(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = labs_body({})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("<html", json.dumps(body).lower())
        self.assertNotIn("labs", body)

    def test_dispatch_cookie_less_401_on_labs_paths(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            pairs = (
                ("/api/labs/upload", "labs_upload", "POST"),
                ("/api/labs/delete", "labs_delete", "POST"),
                ("/api/labs", "labs", "GET"),
            )
            for path, route, method in pairs:
                status, body = dispatch_client_route(
                    {}, "", method, payload={}, path=path
                )
                self.assertIsNotNone(status, route)
                self.assertEqual(status, 401, route)
                self.assertEqual(body["error"], "auth_required")
                status, body = dispatch_client_route(
                    {}, f"_r={route}", method, payload={}
                )
                self.assertEqual(status, 401, route)
                self.assertEqual(body["error"], "auth_required")


class SignedInLabsRoutes(unittest.TestCase):
    def test_vercel_upload_parses_without_hobby_phi(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret", "VERCEL": "1"}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        panel = _twenty_marker_panel()
        with mock.patch.dict(
            os.environ,
            {**env, "RESISTANCE_DASHBOARD_CONFIG_DIR": tmp.name},
            clear=True,
        ):
            with mock.patch(
                "rt_dashboard.labs_parse.parse_lab_pdf",
                return_value=panel,
            ):
                status, body = labs_write(
                    _headers(),
                    "labs_upload",
                    {
                        "filename": "rythm.pdf",
                        "content_base64": base64.b64encode(b"%PDF-1.3 fake").decode(
                            "ascii"
                        ),
                    },
                )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(body["marker_count"], 20)
        self.assertEqual(body["panel"]["lab"], "Rythm Health")
        self.assertEqual((body.get("labs") or {}).get("storage"), "memory")
        self.assertEqual(list(Path(tmp.name).rglob("*.pdf")), [])
        self.assertEqual(list(Path(tmp.name).rglob("index.json")), [])

    def test_vercel_delete_is_ok_without_disk_write(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret", "VERCEL": "1"}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(
            os.environ,
            {**env, "RESISTANCE_DASHBOARD_CONFIG_DIR": tmp.name},
            clear=True,
        ):
            status, body = labs_write(
                _headers(),
                "labs_delete",
                {"date": "2026-06-01", "lab": "Rythm Health"},
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["labs"]["panels"], [])
        self.assertEqual(list(Path(tmp.name).rglob("*")), [])

    def test_non_vercel_upload_reuses_pi_store(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        panel = _twenty_marker_panel()
        with mock.patch.dict(
            os.environ,
            {**env, "RESISTANCE_DASHBOARD_CONFIG_DIR": tmp.name},
            clear=True,
        ):
            with mock.patch(
                "rt_dashboard.labs_parse.parse_lab_pdf",
                return_value=panel,
            ):
                status, body = labs_write(
                    _headers(),
                    "labs_upload",
                    {
                        "filename": "fake.pdf",
                        "content_base64": base64.b64encode(b"%PDF-1.3 fake").decode(
                            "ascii"
                        ),
                    },
                )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertGreater(body["marker_count"], 0)
        self.assertEqual((body.get("labs") or {}).get("storage"), "config")
        self.assertTrue(list(Path(tmp.name).rglob("index.json")))


if __name__ == "__main__":
    unittest.main()
