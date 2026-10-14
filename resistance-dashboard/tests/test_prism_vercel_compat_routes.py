"""Former Vercel-only /api functions are served by prism server.py.

Each route must be non-404 and delegate with the server.py session user
(not the Vercel signed cookie).
"""

from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server as fitdash_server

USER = {"user_id": "u-test-1", "email": "", "display_name": "t", "session_id": "s"}
DASH = {"nutrition_store": {"meal_plan": {"items": [1]}, "labs": {"panels": []}, "sources": {}}}


class CompatRoutes(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("FITDASH_REQUIRE_AUTH")
        os.environ["FITDASH_REQUIRE_AUTH"] = "1"
        self.patches = [
            mock.patch.object(fitdash_server, "_session_user_from_headers", return_value=dict(USER)),
            mock.patch.object(fitdash_server, "load_dashboard_data", return_value=DASH),
        ]
        for p in self.patches:
            p.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), fitdash_server.DashboardHandler)
        self.port = int(self.httpd.server_address[1])
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for p in self.patches:
            p.stop()
        if self._saved is None:
            os.environ.pop("FITDASH_REQUIRE_AUTH", None)
        else:
            os.environ["FITDASH_REQUIRE_AUTH"] = self._saved

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else (b"{}" if method == "POST" else None)
        req = Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                      headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}"), r.headers
        except HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers

    def _assert_delegates(self, method, path, target, ret):
        with mock.patch(target, return_value=ret) as fn:
            status, body, headers = self._call(method, path)
        self.assertNotEqual(status, 404, path)
        self.assertTrue(fn.called, f"{path} did not delegate to {target}")
        user = fn.call_args.kwargs.get("user")
        self.assertEqual(user["id"], USER["user_id"])
        return status, body, headers

    def test_grok_start(self):
        status, body, headers = self._assert_delegates(
            "POST", "/api/ask/grok/start", "api.ask.grok.start.grok_start_body",
            (200, {"ok": True, "user_code": "ABCD"}, [("Set-Cookie", "fitdash_grok_device=x; Path=/")]),
        )
        self.assertEqual(status, 200)
        self.assertIn("fitdash_grok_device", headers.get("Set-Cookie") or "")

    def test_grok_poll(self):
        self._assert_delegates("GET", "/api/ask/grok/poll", "api.ask.grok.poll.grok_poll_body",
                               (200, {"ok": True, "status": "pending"}, []))

    def test_grok_disconnect(self):
        self._assert_delegates("POST", "/api/ask/grok/disconnect",
                               "api.ask.grok.disconnect.grok_disconnect_body", (200, {"ok": True}))

    def test_ask_plan_uses_server_dashboard(self):
        with mock.patch("api.ask.plan.ask_plan_body", return_value=(200, {"ok": True})) as fn:
            status, _b, _h = self._call("POST", "/api/ask/plan")
        self.assertEqual(status, 200)
        self.assertEqual(fn.call_args.kwargs["user"]["id"], USER["user_id"])
        self.assertIs(fn.call_args.kwargs["dashboard"], DASH)

    def test_meal_plan_and_labs(self):
        s, b, _ = self._call("GET", "/api/meal-plan")
        self.assertEqual((s, b["plan"]), (200, {"items": [1]}))
        s, b, _ = self._call("POST", "/api/meal-plan")
        self.assertEqual(s, 200)
        s, b, _ = self._call("GET", "/api/labs")
        self.assertEqual((s, b["labs"]), (200, {"panels": []}))

    def test_wrong_method_is_405_not_404(self):
        s, _b, _h = self._call("GET", "/api/ask/grok/start")
        self.assertEqual(s, 405)

    def test_ask_status_scoped_to_user(self):
        with mock.patch.object(fitdash_server, "grok_auth_status", return_value={"ok": True}) as fn:
            s, _b, _h = self._call("GET", "/api/ask/status")
        self.assertEqual(s, 200)
        self.assertEqual(fn.call_args.kwargs.get("user_id"), USER["user_id"])

    def test_unauthenticated_is_401(self):
        with mock.patch.object(fitdash_server, "_session_user_from_headers", return_value=None):
            s, _b, _h = self._call("POST", "/api/ask/grok/start")
        self.assertEqual(s, 401)


class BodyFunctionsAcceptUser(unittest.TestCase):
    def test_disconnect_uses_supplied_user(self):
        from api.ask.grok.disconnect import grok_disconnect_body

        with mock.patch("rt_dashboard.grok_sessions.delete_grok_session") as d:
            status, _ = grok_disconnect_body({}, user={"user_id": "u-9"})
        self.assertEqual(status, 200)
        d.assert_called_once_with("u-9")

    def test_no_user_still_requires_vercel_cookie(self):
        from api.ask.grok.disconnect import grok_disconnect_body

        status, body = grok_disconnect_body({})
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
