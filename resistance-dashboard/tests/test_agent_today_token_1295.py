"""#1295: FITDASH_AGENT_TOKEN may GET /api/agent/today from the tailnet.

The agent read token is read-only. It does not SuperGrok-fill or persist.
No token off loopback stays 401. The house service token keeps the fill.
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
from api.workout._util import (  # noqa: E402
    _agent_today_from_stores,
    agent_today_body,
)
from rt_dashboard.agent_plan import fill_stamped_workout  # noqa: E402
from rt_dashboard.service_auth import agent_today_principal  # noqa: E402

TAILNET = "100.67.114.2"
AGENT = "agent-read-secret"
HOUSE = "house-service-secret"
SAVED = {
    "session_type": "pull",
    "is_rest_day": False,
    "exercises": [
        {"name": "Chest-Supported Row", "sets": 3, "reps": 8},
        {"name": "Lat Pulldown", "sets": 3, "reps": 10},
        {"name": "Hammer Curl", "sets": 2, "reps": 12},
    ],
}


class _Env(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "FITDASH_REQUIRE_AUTH",
                "FITDASH_SERVICE_TOKEN",
                "FITDASH_SERVICE_LOOPBACK",
                "FITDASH_AGENT_TOKEN",
            )
        }

    def tearDown(self) -> None:
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def _tokens(self, *, loopback: str = "1") -> None:
        os.environ["FITDASH_REQUIRE_AUTH"] = "1"
        os.environ["FITDASH_SERVICE_TOKEN"] = HOUSE
        os.environ["FITDASH_AGENT_TOKEN"] = AGENT
        os.environ["FITDASH_SERVICE_LOOPBACK"] = loopback


class PrincipalTests(_Env):
    def test_agent_token_is_read_only_on_the_tailnet(self) -> None:
        self._tokens()
        self.assertEqual(
            agent_today_principal(
                {"Authorization": f"Bearer {AGENT}"}, TAILNET
            ),
            "agent",
        )
        self.assertEqual(
            agent_today_principal(
                {"X-FitDash-Service-Token": AGENT}, TAILNET
            ),
            "agent",
        )

    def test_no_token_and_wrong_token_deny_off_loopback(self) -> None:
        self._tokens()
        self.assertEqual(agent_today_principal({}, TAILNET), "deny")
        self.assertEqual(
            agent_today_principal({"Authorization": "Bearer nope"}, TAILNET),
            "deny",
        )
        self.assertEqual(
            agent_today_principal({"Authorization": f"Bearer {HOUSE}"}, "10.0.0.8"),
            "service",
        )

    def test_loopback_without_agent_token_stays_service(self) -> None:
        self._tokens()
        self.assertEqual(agent_today_principal({}, "127.0.0.1"), "service")
        self.assertEqual(
            agent_today_principal({"Authorization": "Bearer nope"}, "::1"),
            "service",
        )

    def test_loopback_keeps_the_fill_with_the_agent_token(self) -> None:
        self._tokens()
        self.assertEqual(
            agent_today_principal(
                {"Authorization": f"Bearer {AGENT}"}, "127.0.0.1"
            ),
            "service",
        )

    def test_session_wins_and_identical_secrets_stay_service(self) -> None:
        self._tokens()
        self.assertEqual(
            agent_today_principal({}, TAILNET, session_user={"user_id": "u"}),
            "session",
        )
        os.environ["FITDASH_AGENT_TOKEN"] = HOUSE
        self.assertEqual(
            agent_today_principal({"Authorization": f"Bearer {HOUSE}"}, TAILNET),
            "service",
        )


class ReadOnlyFillTests(_Env):
    def test_read_only_does_not_generate_an_empty_letter(self) -> None:
        slot = {"session_type": "push", "is_rest_day": False, "exercises": []}
        with mock.patch(
            "rt_dashboard.agent_plan.load_last_good_workout_plan", return_value=None
        ), mock.patch(
            "rt_dashboard.agent_plan.ensure_today_grok_plan"
        ) as generate:
            out = fill_stamped_workout(
                "u", day="2026-10-20", workout=slot, read_only=True
            )
        generate.assert_not_called()
        self.assertEqual(out["session_type"], "push")
        self.assertEqual(out["exercises"], [])
        self.assertNotIn("generate_error", out)

    def test_read_only_returns_the_saved_plan(self) -> None:
        slot = {"session_type": "push", "is_rest_day": False, "exercises": []}
        with mock.patch(
            "rt_dashboard.agent_plan.load_last_good_workout_plan", return_value=SAVED
        ), mock.patch(
            "rt_dashboard.agent_plan.ensure_today_grok_plan"
        ) as generate:
            out = fill_stamped_workout(
                "u", day="2026-10-20", workout=slot, read_only=True
            )
        generate.assert_not_called()
        self.assertEqual(out["exercises"][0]["name"], "Chest-Supported Row")

    def test_read_only_does_not_persist(self) -> None:
        slot = {"session_type": "push", "is_rest_day": False, "exercises": []}
        with mock.patch(
            "rt_dashboard.agent_plan.load_last_good_workout_plan", return_value=SAVED
        ), mock.patch(
            "rt_dashboard.agent_plan.ensure_today_grok_plan"
        ) as generate, mock.patch(
            "rt_dashboard.agent_plan.save_last_good_workout_plan"
        ) as persist:
            fill_stamped_workout(
                "u", day="2026-10-20", workout=slot, read_only=True
            )
        generate.assert_not_called()
        persist.assert_not_called()

    def test_read_only_closed_day_does_not_roll(self) -> None:
        slot = {"session_type": "legs", "is_rest_day": False, "exercises": []}
        with mock.patch(
            "rt_dashboard.agent_plan.load_last_good_workout_plan", return_value=SAVED
        ), mock.patch(
            "rt_dashboard.agent_plan._session_closed", return_value=True
        ), mock.patch(
            "rt_dashboard.agent_plan.ensure_today_grok_plan"
        ) as generate:
            out = fill_stamped_workout(
                "u", day="2026-10-20", workout=slot, read_only=True
            )
        generate.assert_not_called()
        self.assertEqual(out["session_type"], "legs")
        self.assertEqual(out["exercises"], [])

    def test_service_path_still_generates(self) -> None:
        slot = {"session_type": "push", "is_rest_day": False, "exercises": []}
        with mock.patch(
            "rt_dashboard.agent_plan.load_last_good_workout_plan", return_value=None
        ), mock.patch(
            "rt_dashboard.agent_plan.ensure_today_grok_plan",
            return_value={"ok": False, "error": "stopped in test"},
        ) as generate:
            out = fill_stamped_workout(
                "u", day="2026-10-20", workout=slot, read_only=False
            )
        generate.assert_called_once()
        self.assertEqual(out.get("generate_error"), "stopped in test")


class VercelPathTests(_Env):
    def test_agent_token_calls_stores_read_only(self) -> None:
        self._tokens(loopback="0")
        with mock.patch(
            "api.workout._util._agent_today_from_stores",
            return_value=(200, {"ok": True, "today": {"date": "2026-10-20"}, "week": {}}),
        ) as load:
            status, body = agent_today_body(
                {"Authorization": f"Bearer {AGENT}"},
                "",
                client_host=TAILNET,
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertNotIn(AGENT, json.dumps(body))
        self.assertTrue(load.call_args.kwargs["read_only"])

    def test_no_token_on_tailnet_is_401(self) -> None:
        self._tokens(loopback="0")
        status, body = agent_today_body({}, "", client_host=TAILNET)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("today", body)
        self.assertNotIn(AGENT, json.dumps(body))
        self.assertNotIn(HOUSE, json.dumps(body))

    def test_service_token_still_fills(self) -> None:
        self._tokens(loopback="0")
        with mock.patch(
            "api.workout._util._agent_today_from_stores",
            return_value=(200, {"ok": True}),
        ) as load:
            status, _body = agent_today_body(
                {"Authorization": f"Bearer {HOUSE}"},
                "",
                client_host=TAILNET,
            )
        self.assertEqual(status, 200)
        self.assertFalse(load.call_args.kwargs["read_only"])

    def test_store_path_forwards_read_only_to_stamp(self) -> None:
        with mock.patch.dict(
            os.environ, {"TZ": "UTC", "FITDASH_USER_ID": "user-1"}, clear=False
        ), mock.patch(
            "api.dashboard._load_sessions", return_value=([], [], "turso")
        ), mock.patch(
            "rt_dashboard.google_health.GoogleHealthClient"
        ) as health, mock.patch(
            "rt_dashboard.hidrate_client.hidrate_hydration_samples", return_value=[]
        ), mock.patch(
            "rt_dashboard.hidrate_client.hidrate_bottle_charge",
            return_value={"available": False},
        ), mock.patch(
            "rt_dashboard.hydration_bars.build_hydration_bars_payload",
            return_value={"pacing": None},
        ), mock.patch(
            "rt_dashboard.workout_store.load_workspace_goals", return_value=({}, "mem")
        ), mock.patch(
            "rt_dashboard.agent_plan.stamp_and_fill_workout",
            return_value={"session_type": "push", "is_rest_day": False, "exercises": []},
        ) as stamp, mock.patch(
            "rt_dashboard.nutrition_store.load_preview_targets", return_value=({}, "mem")
        ), mock.patch(
            "api.dashboard._today_consumed", return_value=None
        ), mock.patch(
            "rt_dashboard.meal_plan_store.load_last_good_meal_plan", return_value={}
        ):
            health.return_value.credentials_present.return_value = False
            status, body = _agent_today_from_stores({}, "", read_only=True)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(stamp.call_args.kwargs["read_only"])


class _TailnetServer(ThreadingHTTPServer):
    """Present the peer as a tailnet host. urlopen still dials 127.0.0.1."""

    def finish_request(self, request, client_address) -> None:  # noqa: N802
        self.RequestHandlerClass(request, (TAILNET, client_address[1]), self)


class PiTailnetHttpTests(_Env):
    def setUp(self) -> None:
        super().setUp()
        self._tokens()
        self._load_patch = mock.patch.object(
            fitdash_server,
            "load_dashboard_data",
            return_value={
                "meta": {"local_today": "2026-10-20"},
                "coach": {"today": {"date": "2026-10-20"}},
                "sessions": [],
                "workout_store": {"goals": {}, "plan": {}},
                "recovery": {},
            },
        )
        self.load_dashboard = self._load_patch.start()
        self._fill_patch = mock.patch(
            "rt_dashboard.agent_plan.stamp_and_fill_workout",
            return_value={
                "session_type": "push",
                "is_rest_day": False,
                "exercises": [],
            },
        )
        self.fill = self._fill_patch.start()
        self.httpd = _TailnetServer(("127.0.0.1", 0), fitdash_server.DashboardHandler)
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._fill_patch.stop()
        self._load_patch.stop()
        super().tearDown()

    def _call(
        self,
        path: str,
        headers: Optional[dict] = None,
        *,
        data: Optional[bytes] = None,
        method: Optional[str] = None,
    ) -> tuple:
        req = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers=headers or {},
            method=method,
        )
        try:
            with urlopen(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8")
                body = json.loads(raw) if raw else {}
                return int(resp.status), body
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"raw": raw}
            return int(exc.code), body

    def test_agent_token_is_200_and_read_only(self) -> None:
        status, body = self._call(
            "/api/agent/today",
            {"Authorization": f"Bearer {AGENT}"},
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["today"]["date"], "2026-10-20")
        self.assertEqual(body["today"]["workout"]["session_type"], "push")
        self.assertNotIn(AGENT, json.dumps(body))
        self.assertNotIn(HOUSE, json.dumps(body))
        self.fill.assert_called()
        self.assertTrue(self.fill.call_args.kwargs["read_only"])

    def test_no_token_is_401_and_does_not_fill(self) -> None:
        status, body = self._call("/api/agent/today")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "auth_required")
        self.assertNotIn("today", body)
        self.assertNotIn(AGENT, json.dumps(body))
        self.load_dashboard.assert_not_called()
        self.fill.assert_not_called()

    def test_generate_plan_rejects_the_agent_token(self) -> None:
        status, body = self._call(
            "/api/agent/generate-plan",
            {"Authorization": f"Bearer {AGENT}", "Content-Type": "application/json"},
            data=b"{}",
            method="POST",
        )
        self.assertEqual(status, 401, body)
        self.assertEqual(body.get("error"), "auth_required")
        self.assertNotIn(AGENT, json.dumps(body))


class PiTailnetOverlayTests(_Env):
    """The tailnet brief must carry the stored workout, and must not write."""

    def test_saved_plan_is_on_the_body_and_nothing_is_generated(self) -> None:
        self._tokens()
        dash = {
            "meta": {"local_today": "2026-10-20"},
            "coach": {"today": {"date": "2026-10-20"}},
            "sessions": [],
            "workout_store": {"goals": {}, "plan": {}},
            "recovery": {},
        }
        stamp = {
            "session_type": "pull",
            "is_rest_day": False,
            "exercises": [],
            "empty": True,
        }
        httpd = _TailnetServer(("127.0.0.1", 0), fitdash_server.DashboardHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        patches = [
            mock.patch.object(fitdash_server, "load_dashboard_data", return_value=dash),
            mock.patch(
                "rt_dashboard.grok_planner.dashboard_plan_slots",
                return_value=({}, dict(stamp)),
            ),
            mock.patch(
                "rt_dashboard.agent_plan.load_last_good_workout_plan",
                return_value=dict(SAVED),
            ),
            mock.patch("rt_dashboard.agent_plan.ensure_today_grok_plan"),
            mock.patch("rt_dashboard.agent_plan.save_last_good_workout_plan"),
        ]
        started = [p.start() for p in patches]
        generate, persist = started[-2], started[-1]
        thread.start()
        try:
            port = int(httpd.server_address[1])
            req = Request(
                f"http://127.0.0.1:{port}/api/agent/today",
                headers={"Authorization": f"Bearer {AGENT}"},
            )
            with urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                status = int(resp.status)
        finally:
            httpd.shutdown()
            httpd.server_close()
            for p in patches:
                p.stop()
        self.assertEqual(status, 200, body)
        names = [
            row.get("name")
            for row in body["today"]["workout"].get("plan_exercises") or []
        ]
        self.assertEqual(names, [e["name"] for e in SAVED["exercises"]])
        self.assertNotIn(AGENT, json.dumps(body))
        generate.assert_not_called()
        persist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
