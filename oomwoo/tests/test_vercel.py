"""Vercel adapter — routing, one hobby function, no live GitHub."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.index import STATUS_CACHE_CONTROL, handle, route_from  # noqa: E402


class VercelConfigTests(unittest.TestCase):
    def test_single_dispatcher_under_hobby_cap(self) -> None:
        api = PKG / "api"
        handlers = [
            p
            for p in api.rglob("*.py")
            if p.name != "__init__.py" and not p.name.startswith("_")
        ]
        self.assertEqual([p.name for p in handlers], ["index.py"])
        vercel = json.loads((PKG / "vercel.json").read_text(encoding="utf-8"))
        fns = vercel.get("functions") or {}
        self.assertEqual(list(fns.keys()), ["api/index.py"])
        self.assertLessEqual(len(fns), 12)
        sources = {r["source"] for r in vercel.get("rewrites") or []}
        self.assertIn("/api/status", sources)
        self.assertIn("/api/health", sources)


class VercelHandleTests(unittest.TestCase):
    def test_route_from_query_and_path(self) -> None:
        self.assertEqual(route_from("/api?_r=health"), "health")
        self.assertEqual(route_from("/api?_r=status"), "status")
        self.assertEqual(route_from("/api/health"), "health")
        self.assertEqual(route_from("/api/status?refresh=1"), "status")
        self.assertEqual(route_from("/api"), "status")

    def test_health(self) -> None:
        status, body, cache = handle("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("service"), "oomwoo")
        self.assertEqual(body.get("host"), "vercel")
        self.assertEqual(cache, "no-store")

    def test_options(self) -> None:
        status, body, cache = handle("OPTIONS", "/api/status")
        self.assertEqual(status, 204)
        self.assertEqual(body, {})
        self.assertEqual(cache, "no-store")

    def test_status_uses_build_status_and_caches(self) -> None:
        fake = {"ok": True, "service": "oomwoo", "hub": {"stars": 10039}}
        with patch("api.index.build_status", return_value=fake) as mocked:
            status, body, cache = handle("GET", "/api/status?refresh=1")
            mocked.assert_called_once_with(refresh=False)
        self.assertEqual(status, 200)
        self.assertEqual(body["hub"]["stars"], 10039)
        self.assertEqual(body.get("host"), "vercel")
        self.assertEqual(cache, STATUS_CACHE_CONTROL)

    def test_status_error_is_json_500(self) -> None:
        with patch("api.index.build_status", side_effect=RuntimeError("boom")):
            status, body, cache = handle("GET", "/api?_r=status")
        self.assertEqual(status, 500)
        self.assertFalse(body.get("ok"))
        self.assertIn("boom", body.get("error", ""))
        self.assertEqual(cache, "no-store")


if __name__ == "__main__":
    unittest.main()
