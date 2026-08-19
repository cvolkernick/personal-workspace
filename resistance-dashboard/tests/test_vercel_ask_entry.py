"""POST /api/ask entry must be the package, not a self-importing ask.py."""

from __future__ import annotations

import importlib
import importlib.util
import io
import json
import os
import sys
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class AskEntryLayout(unittest.TestCase):
    def test_no_sibling_ask_py_collision(self):
        self.assertFalse((ROOT / "api" / "ask.py").exists())
        self.assertTrue((ROOT / "api" / "ask" / "index.py").exists())
        self.assertTrue((ROOT / "api" / "ask" / "_post.py").exists())

    def test_vercel_json_max_duration_on_index(self):
        raw = (ROOT / "vercel.json").read_text(encoding="utf-8")
        self.assertIn("api/ask/index.py", raw)
        self.assertNotIn('"api/ask.py"', raw)


class AskEntryImport(unittest.TestCase):
    def test_api_ask_is_package_with_handler(self):
        pkg = importlib.import_module("api.ask")
        self.assertTrue(hasattr(pkg, "__path__"), "api.ask must be the package")
        post = importlib.import_module("api.ask._post")
        index = importlib.import_module("api.ask.index")
        self.assertTrue(callable(post.handler))
        self.assertTrue(callable(index.handler))

    def test_index_exposes_handler_even_if_named_api_ask(self):
        """Vercel may spec_from_file_location('api.ask', index.py). Must not crash."""
        path = ROOT / "api" / "ask" / "index.py"
        spec = importlib.util.spec_from_file_location(
            "_vercel_ask_index_as_api_ask", path
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        self.assertTrue(callable(mod.handler))
        self.assertTrue(callable(mod.ask_body))

    def test_post_import_does_not_load_dashboard(self):
        src = (ROOT / "api" / "ask" / "_post.py").read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("from api.dashboard") or stripped.startswith(
                "import api.dashboard"
            ):
                self.assertNotEqual(
                    line,
                    stripped,
                    f"module-level dashboard import at line {i}",
                )


class CookieLessAskPost(unittest.TestCase):
    def test_ask_body_401_json_without_dashboard(self):
        from api.ask._post import ask_body

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.dict(sys.modules, {"api.dashboard": None}):
                status, body = ask_body({}, {"question": "hi"})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("<html", json.dumps(body).lower())

    def test_handler_post_cookie_less_is_401_json(self):
        from api.ask.index import handler

        class _Req(handler):
            def __init__(self):
                self.headers = {}
                self.rfile = io.BytesIO(b"{}")
                self.wfile = io.BytesIO()
                self._status = None
                self._headers = {}

            def send_response(self, code, message=None):
                self._status = code

            def send_header(self, key, val):
                self._headers[key] = val

            def end_headers(self):
                return

        with mock.patch.dict(os.environ, {}, clear=True):
            req = _Req()
            req.do_POST()
        self.assertEqual(req._status, 401)
        self.assertEqual(req._headers.get("Content-Type"), "application/json")
        body = json.loads(req.wfile.getvalue().decode("utf-8"))
        self.assertEqual(body["error"], "auth_required")


class NoTerminalLoginCopy(unittest.TestCase):
    def test_product_copy_points_at_connect_supergrok(self):
        banned = ("run grok login", "run `grok login`", "in a terminal and retry")
        files = [
            ROOT / "static" / "app.js",
            ROOT / "static" / "supergrok.js",
            ROOT / "static" / "index.html",
            ROOT / "rt_dashboard" / "grok_ask.py",
            ROOT / "rt_dashboard" / "grok_oauth.py",
            ROOT / "rt_dashboard" / "grok_planner.py",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8")
            low = text.lower()
            for needle in banned:
                self.assertNotIn(needle, low, f"{path.name} still has {needle!r}")


if __name__ == "__main__":
    unittest.main()
