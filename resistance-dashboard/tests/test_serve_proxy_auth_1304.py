"""#1304: Tailscale Serve traffic arrives from 127.0.0.1 but is remote.

A loopback request carrying Serve / reverse-proxy headers must not get the
loopback service bypass. /api/agent/today then needs the agent read token
(read-only), plan generate stays behind write auth, and a Google session
still works. Direct loopback with no proxy headers keeps the old behaviour.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server as fitdash_server  # noqa: E402
from rt_dashboard.service_auth import (  # noqa: E402
    agent_today_principal,
    is_proxied,
    service_auth_ok,
)

AGENT = "agent-read-secret"
HOUSE = "house-service-secret"
SERVE = {
    "Tailscale-User-Login": "someone@example.com",
    "X-Forwarded-For": "100.64.0.9",
    "X-Forwarded-Proto": "https",
}
ENV_KEYS = (
    "FITDASH_REQUIRE_AUTH",
    "FITDASH_SERVICE_TOKEN",
    "FITDASH_SERVICE_LOOPBACK",
    "FITDASH_AGENT_TOKEN",
)


class _Env(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["FITDASH_REQUIRE_AUTH"] = "1"
        os.environ["FITDASH_SERVICE_TOKEN"] = HOUSE
        os.environ["FITDASH_AGENT_TOKEN"] = AGENT
        os.environ["FITDASH_SERVICE_LOOPBACK"] = "1"

    def tearDown(self) -> None:
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


class GateTests(_Env):
    def test_each_proxy_header_marks_the_request_proxied(self) -> None:
        for name in ("Tailscale-User-Login", "X-Forwarded-For", "X-Forwarded-Proto"):
            self.assertTrue(is_proxied({name: "x"}), name)
        self.assertFalse(is_proxied({}))
        self.assertFalse(is_proxied({"X-Forwarded-For": ""}))

    def test_proxied_loopback_is_not_service(self) -> None:
        self.assertFalse(service_auth_ok(SERVE, "127.0.0.1"))
        self.assertEqual(agent_today_principal(SERVE, "127.0.0.1"), "deny")
        self.assertEqual(
            agent_today_principal(
                {**SERVE, "Authorization": f"Bearer {AGENT}"}, "127.0.0.1"
            ),
            "agent",
        )
        self.assertEqual(
            agent_today_principal(
                {**SERVE, "Authorization": f"Bearer {HOUSE}"}, "127.0.0.1"
            ),
            "service",
        )

    def test_direct_loopback_keeps_current_behaviour(self) -> None:
        self.assertTrue(service_auth_ok({}, "127.0.0.1"))
        self.assertTrue(service_auth_ok({}, "::1"))
        self.assertEqual(agent_today_principal({}, "127.0.0.1"), "service")


class ServeHttpTests(_Env):
    """Real loopback socket, Serve headers on the request."""

    def setUp(self) -> None:
        super().setUp()
        self._load = mock.patch.object(
            fitdash_server,
            "load_dashboard_data",
            return_value={
                "meta": {"local_today": "2026-10-21"},
                "coach": {"today": {"date": "2026-10-21"}},
                "sessions": [],
                "workout_store": {"goals": {}, "plan": {}},
                "recovery": {},
            },
        )
        self.load_dashboard = self._load.start()
        self._fill = mock.patch(
            "rt_dashboard.agent_plan.stamp_and_fill_workout",
            return_value={"session_type": "push", "is_rest_day": False, "exercises": []},
        )
        self.fill = self._fill.start()
        self._sess = mock.patch.object(
            fitdash_server,
            "_session_user_from_headers",
            side_effect=lambda h: (
                {"user_id": "chris", "email": "c@example.com"}
                if "fitdash_session=good" in (h.get("Cookie") or "")
                else None
            ),
        )
        self._sess.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), fitdash_server.DashboardHandler)
        self.port = int(self.httpd.server_address[1])
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._sess.stop()
        self._fill.stop()
        self._load.stop()
        super().tearDown()

    def _call(self, path: str, headers: Optional[dict] = None, *, data=None, method=None):
        req = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers=headers or {},
            method=method,
        )
        try:
            with urlopen(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8")
                return int(resp.status), (json.loads(raw) if raw else {})
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                return int(exc.code), (json.loads(raw) if raw else {})
            except json.JSONDecodeError:
                return int(exc.code), {"raw": raw}

    def test_serve_request_without_token_is_401(self) -> None:
        status, body = self._call("/api/agent/today", dict(SERVE))
        self.assertEqual(status, 401, body)
        self.assertEqual(body.get("error"), "auth_required")
        self.assertNotIn("today", body)
        self.load_dashboard.assert_not_called()
        self.fill.assert_not_called()

    def test_serve_request_with_read_token_is_200_read_only(self) -> None:
        status, body = self._call(
            "/api/agent/today", {**SERVE, "Authorization": f"Bearer {AGENT}"}
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertTrue(self.fill.call_args.kwargs["read_only"])
        self.assertNotIn(AGENT, json.dumps(body))

    def test_serve_read_token_cannot_generate_a_plan(self) -> None:
        for headers in (
            {**SERVE, "Authorization": f"Bearer {AGENT}"},
            dict(SERVE),
        ):
            status, body = self._call(
                "/api/agent/generate-plan",
                {**headers, "Content-Type": "application/json"},
                data=b"{}",
                method="POST",
            )
            self.assertEqual(status, 401, body)
            self.assertEqual(body.get("error"), "auth_required")

    def test_serve_session_cookie_still_works(self) -> None:
        status, body = self._call(
            "/api/agent/today", {**SERVE, "Cookie": "fitdash_session=good"}
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertFalse(self.fill.call_args.kwargs["read_only"])
        self.assertEqual(self.load_dashboard.call_args.kwargs["user_id"], "chris")

    def test_direct_loopback_without_headers_unchanged(self) -> None:
        status, body = self._call("/api/agent/today")
        self.assertEqual(status, 200, body)
        self.assertFalse(self.fill.call_args.kwargs["read_only"])


if __name__ == "__main__":
    unittest.main()
