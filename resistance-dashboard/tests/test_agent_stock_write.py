"""Contract tests: POST /api/agent/inventory/stock (narrow agent pantry write).

Own token (FITDASH_AGENT_WRITE_TOKEN). Read token / house token / loopback do
not write; the write token does not read. Only in_stock + qty on existing
items. Each change stamps updated_at + updated_by and appends an audit line.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server as fitdash_server  # noqa: E402
from rt_dashboard import agent_write  # noqa: E402
from rt_dashboard.agent_read import SECTIONS  # noqa: E402
from rt_dashboard.agent_write import Store, append_audit_jsonl, respond_agent_stock_write  # noqa: E402

READ = "dummy-read-token-not-real"
WRITE = "dummy-write-token-not-real"
NOW = datetime(2026, 10, 19, 22, 0, 0, tzinfo=timezone.utc)
STAMP = "2026-10-19T22:00:00Z"


def _pantry():
    return {"ingredients": [
        {"id": "oats", "name": "Oats", "in_stock": True, "stock": "in", "qty": 2},
        {"id": "tilapia", "name": "Tilapia", "in_stock": False, "stock": "out"},
    ]}


class _Mem:
    def __init__(self):
        self.inv = _pantry()
        self.audit = []
        self.persists = 0

    def store(self):
        def persist(inv):
            self.persists += 1
            self.inv = deepcopy(inv)
            return deepcopy(inv)
        return Store(load=lambda: deepcopy(self.inv), persist=persist, append_audit=self.audit.extend)


def _w(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


GOOD = {"items": [{"id": "tilapia", "in_stock": True}], "updated_by": "nourish"}


class Base(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "FITDASH_AGENT_TOKEN": READ, "FITDASH_AGENT_WRITE_TOKEN": WRITE,
            "FITDASH_SERVICE_TOKEN": "house-dummy", "FITDASH_SERVICE_LOOPBACK": "1"})
        self.env.start()
        self.mem = _Mem()

    def tearDown(self):
        self.env.stop()

    def call(self, body, headers):
        return respond_agent_stock_write(headers, body, self.mem.store(), now=NOW)


class Auth(Base):
    def _assert_401(self, headers, reason):
        status, body = self.call(GOOD, headers)
        self.assertEqual(status, 401)
        self.assertEqual(body["reason"], reason)
        dumped = json.dumps(body)
        for t in (READ, WRITE, "house-dummy"):
            self.assertNotIn(t, dumped)
        self.assertEqual(self.mem.persists, 0)
        self.assertEqual(self.mem.audit, [])

    def test_no_token(self):
        self._assert_401({}, "missing")

    def test_wrong_token(self):
        self._assert_401(_w("nope"), "invalid")

    def test_read_token_cannot_write(self):
        self._assert_401(_w(READ), "invalid")

    def test_house_token_cannot_write(self):
        self._assert_401(_w("house-dummy"), "invalid")

    def test_unset_env(self):
        with mock.patch.dict(os.environ, {"FITDASH_AGENT_WRITE_TOKEN": ""}):
            self._assert_401(_w(WRITE), "unconfigured")

    def test_write_token_equal_to_read_token_fails_closed(self):
        with mock.patch.dict(os.environ, {"FITDASH_AGENT_WRITE_TOKEN": READ}):
            self._assert_401(_w(READ), "unconfigured")

    def test_x_header_ok(self):
        status, _ = self.call(GOOD, {"X-FitDash-Service-Token": WRITE})
        self.assertEqual(status, 200)


class Validation(Base):
    CASES = {
        "not object": [],
        "unknown top field": {**GOOD, "user_id": "x"},
        "missing updated_by": {"items": GOOD["items"]},
        "blank updated_by": {**GOOD, "updated_by": "  "},
        "updated_by not str": {**GOOD, "updated_by": 5},
        "items not list": {**GOOD, "items": {"id": "oats"}},
        "empty list": {**GOOD, "items": []},
        "too many": {**GOOD, "items": [{"id": "oats", "qty": 1}] * 101},
        "unknown item field": {**GOOD, "items": [{"id": "oats", "name": "Evil"}]},
        "unknown id": {**GOOD, "items": [{"id": "caviar", "in_stock": True}]},
        "id not str": {**GOOD, "items": [{"id": 3, "in_stock": True}]},
        "in_stock not bool": {**GOOD, "items": [{"id": "oats", "in_stock": "yes"}]},
        "qty bool": {**GOOD, "items": [{"id": "oats", "qty": True}]},
        "qty str": {**GOOD, "items": [{"id": "oats", "qty": "3"}]},
        "qty negative": {**GOOD, "items": [{"id": "oats", "qty": -1}]},
        "no change fields": {**GOOD, "items": [{"id": "oats"}]},
        "duplicate id": {**GOOD, "items": [{"id": "oats", "qty": 1}, {"id": "OATS", "qty": 2}]},
        "item not object": {**GOOD, "items": ["oats"]},
    }

    def test_400s_write_nothing(self):
        for name, body in self.CASES.items():
            with self.subTest(case=name):
                status, out = self.call(body, _w(WRITE))
                self.assertEqual(status, 400, out)
                self.assertEqual(out["error"], "bad_request")
        self.assertEqual(self.mem.persists, 0)
        self.assertEqual(self.mem.audit, [])
        self.assertEqual(self.mem.inv, _pantry())

    def test_100_items_ok_and_null_qty_ok(self):
        status, _ = self.call({**GOOD, "items": [{"id": "oats", "qty": None}]}, _w(WRITE))
        self.assertEqual(status, 200)


class Success(Base):
    def test_toggle_stamps_and_audits(self):
        status, out = self.call(
            {"items": [{"id": "tilapia", "in_stock": True}, {"id": "oats", "qty": 0.5, "in_stock": False}],
             "updated_by": "nourish"}, _w(WRITE))
        self.assertEqual(status, 200, out)
        self.assertEqual(out["generated_at"], STAMP)
        self.assertEqual(out["as_of"], STAMP)
        by = {i["id"]: i for i in out["items"]}
        self.assertTrue(by["tilapia"]["in_stock"])
        self.assertEqual(by["oats"]["qty"], 0.5)
        self.assertFalse(by["oats"]["in_stock"])
        stored = {i["id"]: i for i in self.mem.inv["ingredients"]}
        for iid in ("tilapia", "oats"):
            self.assertEqual(stored[iid]["updated_at"], STAMP)
            self.assertEqual(stored[iid]["updated_by"], "nourish")
        self.assertEqual(stored["tilapia"]["stock"], "in")  # same set_in_stock path
        self.assertEqual(stored["oats"]["stock"], "out")
        self.assertEqual(len(stored), 2)  # nothing created / deleted
        self.assertEqual(stored["oats"]["name"], "Oats")
        self.assertEqual(self.mem.audit, [
            {"ts": STAMP, "updated_by": "nourish", "id": "tilapia", "in_stock": {"old": False, "new": True}},
            {"ts": STAMP, "updated_by": "nourish", "id": "oats",
             "in_stock": {"old": True, "new": False}, "qty": {"old": 2, "new": 0.5}},
        ])

    def test_jsonl_append_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.jsonl"
            append_audit_jsonl([{"id": "a"}], p)
            append_audit_jsonl([{"id": "b"}], p)
            self.assertEqual([json.loads(x)["id"] for x in p.read_text().splitlines()], ["a", "b"])


class HttpRoute(unittest.TestCase):
    """End-to-end: write, then GET /api/agent/inventory reflects it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = Path(self.tmp.name) / "nutrition_inventory_audit.jsonl"
        self.mem = {"inv": _pantry()}
        mem = self.mem

        def persist(inv, _factory=None):
            mem["inv"] = deepcopy(inv)
            return deepcopy(inv)

        self._patches = [
            mock.patch.dict(os.environ, {
                "FITDASH_AGENT_TOKEN": READ, "FITDASH_AGENT_WRITE_TOKEN": WRITE,
                "FITDASH_SERVICE_LOOPBACK": "1", "FITDASH_INVENTORY_AUDIT_PATH": str(self.audit)}),
            mock.patch.object(fitdash_server, "load_dashboard_data", return_value={"meta": {}, "health": {}}),
            mock.patch.object(fitdash_server, "_auth_required", return_value=True),
            mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False),
            mock.patch("rt_dashboard.dashboard_cache.load_health_cache", return_value=(None, None, {})),
            mock.patch("rt_dashboard.inventory_store.load_workspace_inventory",
                       side_effect=lambda: (deepcopy(mem["inv"]), "fitness/nutrition/inventory.json")),
            mock.patch.object(agent_write, "_default_persist", side_effect=persist),
        ]
        for p in self._patches:
            p.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), fitdash_server.DashboardHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()

    def _req(self, path, token=None, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data else "GET")
        if data:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_write_then_read(self):
        path = "/api/agent/inventory/stock"
        self.assertEqual(self._req(path, None, GOOD)[0], 401)  # loopback, no token
        self.assertEqual(self._req(path, READ, GOOD)[0], 401)
        self.assertEqual(self._req(path, WRITE, {**GOOD, "extra": 1})[0], 400)
        self.assertFalse(self.audit.exists())
        status, out = self._req(path, WRITE, GOOD)
        self.assertEqual(status, 200, out)
        self.assertEqual(out["items"][0]["id"], "tilapia")
        lines = [json.loads(x) for x in self.audit.read_text().splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["updated_by"], "nourish")
        self.assertEqual(lines[0]["in_stock"], {"old": False, "new": True})
        status, got = self._req("/api/agent/inventory", READ)
        self.assertEqual(status, 200, got)
        items = {i["id"]: i for i in got["data"]["items"]}
        self.assertTrue(items["tilapia"]["in_stock"])
        self.assertEqual(items["tilapia"]["updated_at"], out["as_of"])

    def test_write_token_rejected_on_get_routes(self):
        for s in SECTIONS:
            with self.subTest(section=s):
                self.assertEqual(self._req(f"/api/agent/{s}", WRITE)[0], 401)

    def test_write_token_rejected_on_session_stock_route(self):
        status, _ = self._req("/api/inventory/stock", WRITE, {"id": "tilapia", "in_stock": True})
        self.assertEqual(status, 401)
        self.assertFalse(self.mem["inv"]["ingredients"][1]["in_stock"])


if __name__ == "__main__":
    unittest.main()
