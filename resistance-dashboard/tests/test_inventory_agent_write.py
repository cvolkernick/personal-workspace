"""#479 B: Chris-bound inventory agent token. House service token is not enough."""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.auth.session_util import SESSION_COOKIE, make_session  # noqa: E402
from api.workout._util import dispatch_client_route, inventory_write  # noqa: E402
from rt_dashboard.inventory_store import (  # noqa: E402
    INVENTORY_ROW_DEFAULT,
    _inventory_uid,
    inventory_principal_uid,
)
from rt_dashboard.service_auth import (  # noqa: E402
    inventory_agent_principal,
    service_auth_ok,
)

CHRIS = "chris-google-sub"
OTHER = "other-google-sub"
AGENT_TOKEN = "chris-inventory-agent-secret"
HOUSE_TOKEN = "house-service-secret"


def _pantry(*, in_stock: bool = False, iid: str = "broccoli") -> dict:
    return {
        "ingredients": [
            {
                "id": iid,
                "name": iid.replace("-", " ").title(),
                "in_stock": in_stock,
                "calories": 30,
                "protein_g": 2,
                "carbs_g": 6,
                "fat_g": 0,
            }
        ]
    }


class InventoryUidFailClosed(unittest.TestCase):
    def test_human_path_still_defaults(self):
        self.assertEqual(_inventory_uid(""), INVENTORY_ROW_DEFAULT)
        self.assertEqual(_inventory_uid("  "), INVENTORY_ROW_DEFAULT)

    def test_agent_path_rejects_default(self):
        with self.assertRaises(ValueError) as ctx:
            inventory_principal_uid("")
        self.assertEqual(str(ctx.exception), "inventory user_id required")
        self.assertEqual(inventory_principal_uid(CHRIS), CHRIS)


class InventoryAgentPrincipal(unittest.TestCase):
    def test_requires_token_and_bound_user(self):
        headers = {"Authorization": f"Bearer {AGENT_TOKEN}"}
        with mock.patch.dict(
            os.environ,
            {
                "FITDASH_INVENTORY_AGENT_TOKEN": AGENT_TOKEN,
                "FITDASH_INVENTORY_AGENT_USER_ID": "",
                "FITDASH_SERVICE_TOKEN": HOUSE_TOKEN,
            },
            clear=True,
        ):
            self.assertIsNone(inventory_agent_principal(headers))
        with mock.patch.dict(
            os.environ,
            {
                "FITDASH_INVENTORY_AGENT_TOKEN": AGENT_TOKEN,
                "FITDASH_INVENTORY_AGENT_USER_ID": CHRIS,
                "FITDASH_SERVICE_TOKEN": HOUSE_TOKEN,
            },
            clear=True,
        ):
            principal = inventory_agent_principal(headers)
        self.assertEqual(principal["id"], CHRIS)
        self.assertTrue(principal["agent_inventory"])

    def test_house_token_is_not_inventory_principal(self):
        headers = {"X-FitDash-Service-Token": HOUSE_TOKEN}
        env = {
            "FITDASH_INVENTORY_AGENT_TOKEN": AGENT_TOKEN,
            "FITDASH_INVENTORY_AGENT_USER_ID": CHRIS,
            "FITDASH_SERVICE_TOKEN": HOUSE_TOKEN,
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(inventory_agent_principal(headers))
            self.assertTrue(service_auth_ok(headers, "192.168.100.5"))

    def test_rejects_token_equal_to_house_token(self):
        env = {
            "FITDASH_INVENTORY_AGENT_TOKEN": HOUSE_TOKEN,
            "FITDASH_INVENTORY_AGENT_USER_ID": CHRIS,
            "FITDASH_SERVICE_TOKEN": HOUSE_TOKEN,
        }
        headers = {"Authorization": f"Bearer {HOUSE_TOKEN}"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(inventory_agent_principal(headers))


class AgentStockWrite(unittest.TestCase):
    def setUp(self):
        self.store = {
            CHRIS: _pantry(in_stock=False),
            OTHER: _pantry(in_stock=False),
        }
        self.puts: list[str] = []

    def _env(self, **extra) -> dict:
        env = {
            "FITDASH_INVENTORY_AGENT_TOKEN": AGENT_TOKEN,
            "FITDASH_INVENTORY_AGENT_USER_ID": CHRIS,
            "FITDASH_SERVICE_TOKEN": HOUSE_TOKEN,
            "GOOGLE_CLIENT_SECRET": "test-secret",
        }
        env.update(extra)
        return {k: v for k, v in env.items() if v is not None}

    def _agent_headers(self, token: str = AGENT_TOKEN, *, bearer: bool = True) -> dict:
        if bearer:
            return {"Authorization": f"Bearer {token}"}
        return {"X-FitDash-Service-Token": token}

    def _session_headers(self, uid: str) -> dict:
        token = make_session(
            {"id": uid, "email": f"{uid}@example.com", "display_name": uid}
        )
        return {"Cookie": f"{SESSION_COOKIE}={token}"}

    def _run(self, headers, payload, route="inv_stock", env=None, session_uid=None):
        def get(uid):
            return deepcopy(self.store.get(uid))

        def put(uid, inv):
            self.puts.append(uid)
            self.store[uid] = deepcopy(inv)

        with mock.patch.dict(os.environ, env or self._env(), clear=True):
            if session_uid:
                headers = self._session_headers(session_uid)
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_get_inventory",
                side_effect=get,
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_put_inventory",
                side_effect=put,
            ):
                return inventory_write(headers, route, payload)

    def test_agent_stock_true_writes_chris_only(self):
        status, body = self._run(
            self._agent_headers(),
            {"id": "broccoli", "in_stock": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["user_id"], CHRIS)
        broccoli = next(i for i in body["inventory"]["ingredients"] if i["id"] == "broccoli")
        self.assertTrue(broccoli["in_stock"])
        self.assertTrue(self.store[CHRIS]["ingredients"][0]["in_stock"])
        self.assertFalse(self.store[OTHER]["ingredients"][0]["in_stock"])
        self.assertEqual(self.puts, [CHRIS])
        self.assertNotIn(INVENTORY_ROW_DEFAULT, self.puts)

    def test_x_header_same_as_bearer(self):
        status, body = self._run(
            self._agent_headers(bearer=False),
            {"id": "broccoli", "in_stock": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["user_id"], CHRIS)

    def test_idempotent_stock_true(self):
        self.store[CHRIS] = _pantry(in_stock=True)
        status, body = self._run(
            self._agent_headers(),
            {"id": "broccoli", "in_stock": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["inventory"]["ingredients"][0]["in_stock"])

    def test_unknown_id_is_404_no_create(self):
        status, body = self._run(
            self._agent_headers(),
            {"id": "unicorn-steak", "in_stock": True},
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")
        self.assertEqual(self.puts, [])
        ids = [i["id"] for i in self.store[CHRIS]["ingredients"]]
        self.assertNotIn("unicorn-steak", ids)

    def test_invalid_and_missing_cred_are_401(self):
        status, body = self._run({}, {"id": "broccoli", "in_stock": True})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotEqual(status, 500)

        status, body = self._run(
            self._agent_headers("nope"),
            {"id": "broccoli", "in_stock": True},
        )
        self.assertEqual(status, 401)
        self.assertEqual(self.puts, [])

        status, body = self._run(
            self._agent_headers(HOUSE_TOKEN),
            {"id": "broccoli", "in_stock": True},
        )
        self.assertEqual(status, 401)
        self.assertEqual(self.puts, [])

    def test_token_without_bound_user_does_not_write_default(self):
        env = self._env()
        env["FITDASH_INVENTORY_AGENT_USER_ID"] = ""
        status, body = self._run(
            self._agent_headers(),
            {"id": "broccoli", "in_stock": True},
            env=env,
        )
        self.assertEqual(status, 401)
        self.assertEqual(self.puts, [])
        self.assertNotIn(INVENTORY_ROW_DEFAULT, self.store)

    def test_spoofed_account_hint_cannot_escalate(self):
        status, body = self._run(
            self._agent_headers(),
            {"id": "broccoli", "in_stock": True, "userId": OTHER},
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "forbidden")
        self.assertFalse(self.store[OTHER]["ingredients"][0]["in_stock"])
        self.assertFalse(self.store[CHRIS]["ingredients"][0]["in_stock"])
        self.assertEqual(self.puts, [])

    def test_agent_cannot_add(self):
        status, body = self._run(
            self._agent_headers(),
            {"id": "unicorn-steak", "name": "Unicorn steak", "serving_g": 100},
            route="inv_add",
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.puts, [])

    def test_kitchen_session_still_writes_own_tenant(self):
        status, body = self._run(
            {},
            {"id": "broccoli", "in_stock": True},
            session_uid=CHRIS,
        )
        self.assertEqual(status, 200)
        self.assertTrue(self.store[CHRIS]["ingredients"][0]["in_stock"])
        self.assertFalse(self.store[OTHER]["ingredients"][0]["in_stock"])

        status, body = self._run(
            {},
            {"id": "broccoli", "in_stock": True, "userId": CHRIS},
            session_uid=OTHER,
        )
        self.assertEqual(status, 403)
        self.assertTrue(self.store[CHRIS]["ingredients"][0]["in_stock"])
        self.assertFalse(self.store[OTHER]["ingredients"][0]["in_stock"])

    def test_dispatch_agent_stock_path(self):
        def get(uid):
            return deepcopy(self.store.get(uid))

        def put(uid, inv):
            self.puts.append(uid)
            self.store[uid] = deepcopy(inv)

        with mock.patch.dict(os.environ, self._env(), clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_get_inventory",
                side_effect=get,
            ), mock.patch(
                "rt_dashboard.inventory_store._turso_put_inventory",
                side_effect=put,
            ):
                status, body = dispatch_client_route(
                    self._agent_headers(),
                    "",
                    "POST",
                    payload={"id": "broccoli", "in_stock": True},
                    path="/api/inventory/stock",
                )
        self.assertEqual(status, 200)
        self.assertEqual(body["user_id"], CHRIS)


class PiStockHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "FITDASH_REQUIRE_AUTH",
                "FITDASH_SERVICE_TOKEN",
                "FITDASH_SERVICE_LOOPBACK",
                "FITDASH_INVENTORY_AGENT_TOKEN",
                "FITDASH_INVENTORY_AGENT_USER_ID",
            )
        }
        os.environ["FITDASH_REQUIRE_AUTH"] = "1"
        os.environ["FITDASH_SERVICE_TOKEN"] = HOUSE_TOKEN
        os.environ["FITDASH_SERVICE_LOOPBACK"] = "1"
        os.environ["FITDASH_INVENTORY_AGENT_TOKEN"] = AGENT_TOKEN
        os.environ["FITDASH_INVENTORY_AGENT_USER_ID"] = CHRIS
        import server as fitdash_server

        self.server_mod = fitdash_server
        self.store = {
            CHRIS: _pantry(in_stock=False),
            OTHER: _pantry(in_stock=False),
        }

        def load(uid, *args, **kwargs):
            key = str(uid or "")
            if key not in self.store:
                raise AssertionError(f"unexpected tenant load {key!r}")
            return deepcopy(self.store[key]), "turso"

        def persist(inv, uid, **kwargs):
            key = str(uid or "")
            self.store[key] = deepcopy(inv)
            return {"ok": True, "inventory": inv, "source": "turso"}

        self._load = mock.patch.object(
            fitdash_server, "load_preview_inventory", side_effect=load
        )
        self._persist = mock.patch.object(
            fitdash_server, "persist_inventory", side_effect=persist
        )
        self._gh = mock.patch.object(
            fitdash_server, "build_github_client", return_value=None
        )
        self._load.start()
        self._persist.start()
        self._gh.start()
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), fitdash_server.DashboardHandler
        )
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._load.stop()
        self._persist.stop()
        self._gh.stop()
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def _post(
        self, path: str, payload: dict, headers: Optional[Dict[str, str]] = None
    ) -> tuple[int, dict]:
        raw = json.dumps(payload).encode("utf-8")
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        req = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=raw,
            headers=hdrs,
            method="POST",
        )
        try:
            with urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return int(resp.status), body
        except HTTPError as exc:
            raw_body = exc.read().decode("utf-8")
            try:
                body = json.loads(raw_body) if raw_body else {}
            except json.JSONDecodeError:
                body = {"raw": raw_body}
            return int(exc.code), body

    def test_loopback_without_agent_token_is_401(self):
        status, body = self._post(
            "/api/inventory/stock", {"id": "broccoli", "in_stock": True}
        )
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "auth_required")
        self.assertFalse(self.store[CHRIS]["ingredients"][0]["in_stock"])

    def test_house_service_token_is_401(self):
        status, body = self._post(
            "/api/inventory/stock",
            {"id": "broccoli", "in_stock": True},
            headers={"X-FitDash-Service-Token": HOUSE_TOKEN},
        )
        self.assertEqual(status, 401)
        self.assertFalse(self.store[CHRIS]["ingredients"][0]["in_stock"])

    def test_agent_token_writes_bound_tenant(self):
        status, body = self._post(
            "/api/inventory/stock",
            {"id": "broccoli", "in_stock": True},
            headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body.get("user_id"), CHRIS)
        self.assertTrue(self.store[CHRIS]["ingredients"][0]["in_stock"])
        self.assertFalse(self.store[OTHER]["ingredients"][0]["in_stock"])

    def test_spoofed_user_id_is_403(self):
        status, body = self._post(
            "/api/inventory/stock",
            {"id": "broccoli", "in_stock": True, "user_id": OTHER},
            headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
        )
        self.assertEqual(status, 403)
        self.assertFalse(self.store[OTHER]["ingredients"][0]["in_stock"])
        self.assertFalse(self.store[CHRIS]["ingredients"][0]["in_stock"])


if __name__ == "__main__":
    unittest.main()
