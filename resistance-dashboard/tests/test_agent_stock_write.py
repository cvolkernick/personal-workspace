"""Contract tests: POST /api/agent/inventory/stock (narrow agent pantry write).

House pantry-write model: Chris-bound FITDASH_INVENTORY_AGENT_TOKEN
(+ FITDASH_INVENTORY_AGENT_USER_ID). Read token / house token / loopback do
not write; the write token does not read. Only in_stock + qty on existing
items of Chris's own row. stock stays consistent with in_stock (low is kept).
Audit is recorded before / atomically with the save. The default load
SELECTs the user's row and does not seed an empty Turso pantry. Non-finite
qty is rejected.
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
from rt_dashboard.agent_write import Store, append_audit_jsonl, commit_file, commit_turso, respond_agent_stock_write  # noqa: E402
from rt_dashboard import turso_http  # noqa: E402

READ = "dummy-read-token-not-real"
WRITE = "dummy-write-token-not-real"
CHRIS = "chris-google-sub-dummy"
NOW = datetime(2026, 10, 19, 22, 0, 0, tzinfo=timezone.utc)
STAMP = "2026-10-19T22:00:00Z"


def _pantry():
    return {"ingredients": [
        {"id": "oats", "name": "Oats", "in_stock": True, "stock": "in", "qty": 2},
        {"id": "tilapia", "name": "Tilapia", "in_stock": False, "stock": "out"},
        {"id": "rice", "name": "Rice", "in_stock": True, "stock": "low", "qty": 1,
         "kcal": 130, "unit": "cup", "tags": ["carb"]},
    ]}


class _Mem:
    def __init__(self):
        self.inv = _pantry()
        self.audit = []
        self.persists = 0
        self.loads = []
        self.commits = []
        self.events = []

    def store(self):
        def load(uid):
            self.loads.append(uid)
            return deepcopy(self.inv)

        def commit(uid, inv, lines):
            self.commits.append(uid)
            self.audit.extend(lines)
            self.events.append("audit")
            self.persists += 1
            self.inv = deepcopy(inv)
            self.events.append("save")
            return deepcopy(inv)
        return Store(load=load, commit=commit)


def _w(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


GOOD = {"items": [{"id": "tilapia", "in_stock": True}], "updated_by": "nourish"}


class Base(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "FITDASH_AGENT_TOKEN": READ, "FITDASH_INVENTORY_AGENT_TOKEN": WRITE,
            "FITDASH_INVENTORY_AGENT_USER_ID": CHRIS,
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
        with mock.patch.dict(os.environ, {"FITDASH_INVENTORY_AGENT_TOKEN": ""}):
            self._assert_401(_w(WRITE), "unconfigured")

    def test_unbound_user_id_fails_closed(self):
        for bound in ("", "default"):
            with self.subTest(bound=bound), mock.patch.dict(os.environ, {"FITDASH_INVENTORY_AGENT_USER_ID": bound}):
                self._assert_401(_w(WRITE), "unconfigured")

    def test_write_token_equal_to_read_token_fails_closed(self):
        with mock.patch.dict(os.environ, {"FITDASH_INVENTORY_AGENT_TOKEN": READ}):
            self._assert_401(_w(READ), "unconfigured")

    def test_write_token_equal_to_house_token_fails_closed(self):
        with mock.patch.dict(os.environ, {"FITDASH_INVENTORY_AGENT_TOKEN": "house-dummy"}):
            self._assert_401(_w("house-dummy"), "unconfigured")

    def test_old_write_token_env_is_ignored(self):
        with mock.patch.dict(os.environ, {"FITDASH_AGENT_WRITE_TOKEN": "legacy-dummy"}):
            self._assert_401(_w("legacy-dummy"), "invalid")

    def test_writes_chris_row_not_default(self):
        status, _ = self.call(GOOD, _w(WRITE))
        self.assertEqual(status, 200)
        self.assertEqual(self.mem.loads, [CHRIS])
        self.assertEqual(self.mem.commits, [CHRIS])

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
        "qty inf": {**GOOD, "items": [{"id": "oats", "qty": float("inf")}]},
        "qty -inf": {**GOOD, "items": [{"id": "oats", "qty": float("-inf")}]},
        "qty nan": {**GOOD, "items": [{"id": "oats", "qty": float("nan")}]},
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

    def test_json_1e309_qty_is_rejected(self):
        body = json.loads('{"items":[{"id":"oats","qty":1e309}],"updated_by":"nourish"}')
        status, out = self.call(body, _w(WRITE))
        self.assertEqual(status, 400, out)
        self.assertIn("finite", out["message"])
        self.assertEqual(self.mem.persists, 0)
        self.assertEqual(self.mem.inv, _pantry())


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
        self.assertEqual(stored["tilapia"]["stock"], "in")
        self.assertEqual(stored["oats"]["stock"], "out")
        self.assertEqual(len(stored), 3)  # nothing created / deleted
        self.assertEqual(stored["oats"]["name"], "Oats")
        self.assertEqual(self.mem.audit, [
            {"ts": STAMP, "updated_by": "nourish", "id": "tilapia", "in_stock": {"old": False, "new": True},
             "stock": {"old": "out", "new": "in"}},
            {"ts": STAMP, "updated_by": "nourish", "id": "oats",
             "in_stock": {"old": True, "new": False}, "stock": {"old": "in", "new": "out"},
             "qty": {"old": 2, "new": 0.5}},
        ])
        self.assertEqual(self.mem.events, ["audit", "save"])

    def test_in_stock_true_keeps_low(self):
        status, out = self.call({"items": [{"id": "rice", "in_stock": True, "qty": 3}], "updated_by": "nourish"}, _w(WRITE))
        self.assertEqual(status, 200, out)
        rice = {i["id"]: i for i in self.mem.inv["ingredients"]}["rice"]
        self.assertEqual(rice["stock"], "low")  # not forced to "in"
        self.assertTrue(rice["in_stock"])
        self.assertNotIn("stock", self.mem.audit[0])  # stock did not change
        self.assertEqual(out["items"][0]["stock"], "low")

    def test_in_stock_false_sets_out_and_true_lifts_out(self):
        self.call({"items": [{"id": "rice", "in_stock": False}], "updated_by": "n"}, _w(WRITE))
        rice = {i["id"]: i for i in self.mem.inv["ingredients"]}["rice"]
        self.assertEqual((rice["stock"], rice["in_stock"]), ("out", False))
        self.call({"items": [{"id": "rice", "in_stock": True}], "updated_by": "n"}, _w(WRITE))
        rice = {i["id"]: i for i in self.mem.inv["ingredients"]}["rice"]
        self.assertEqual((rice["stock"], rice["in_stock"]), ("in", True))

    def test_only_in_stock_and_qty_are_writable(self):
        before = _pantry()
        for extra in ("stock", "name", "kcal", "unit", "tags", "updated_at", "updated_by", "user_id", "deleted"):
            with self.subTest(field=extra):
                st, _ = self.call({"items": [{"id": "rice", "qty": 2, extra: "x"}], "updated_by": "n"}, _w(WRITE))
                self.assertEqual(st, 400)
        for top in ("user_id", "userId", "ingredients", "row", "account_id"):
            with self.subTest(top=top):
                st, _ = self.call({**GOOD, top: CHRIS}, _w(WRITE))
                self.assertEqual(st, 400)
        self.assertEqual(self.mem.persists, 0)
        self.assertEqual(self.mem.inv, before)
        st, _ = self.call({"items": [{"id": "rice", "in_stock": False, "qty": 0}], "updated_by": "n"}, _w(WRITE))
        self.assertEqual(st, 200)
        after = {i["id"]: i for i in self.mem.inv["ingredients"]}
        allowed = {"in_stock", "stock", "qty", "updated_at", "updated_by"}
        for b in before["ingredients"]:
            a = after[b["id"]]
            changed = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
            if b["id"] == "rice":
                self.assertTrue(changed <= allowed, changed)
            else:
                self.assertEqual(changed, set(), b["id"])
        self.assertEqual(set(self.mem.inv) - {"updated_at"}, set(before) - {"updated_at"})

    def test_jsonl_append_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.jsonl"
            append_audit_jsonl([{"id": "a"}], p)
            append_audit_jsonl([{"id": "b"}], p)
            self.assertEqual([json.loads(x)["id"] for x in p.read_text().splitlines()], ["a", "b"])


LINES = [{"ts": STAMP, "updated_by": "n", "id": "rice", "in_stock": {"old": True, "new": False},
          "stock": {"old": "low", "new": "out"}}]


class AuditBeforeSave(unittest.TestCase):
    def test_file_audit_written_before_save(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.jsonl"
            seen = {}

            def persist(inv, uid, file_client=None, message=""):
                seen["audit_at_save"] = p.read_text().splitlines()
                seen["uid"] = uid
                return {"ok": True, "inventory": inv}

            with mock.patch("rt_dashboard.inventory_store.persist_inventory", side_effect=persist):
                commit_file(CHRIS, _pantry(), LINES, None, p)
            self.assertEqual(seen["uid"], CHRIS)
            self.assertEqual(len(seen["audit_at_save"]), 1)
            ln = json.loads(seen["audit_at_save"][0])
            self.assertEqual(ln["status"], "pending")
            self.assertEqual(ln["stock"], {"old": "low", "new": "out"})

    def test_file_save_failure_appends_abort(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.jsonl"
            with mock.patch("rt_dashboard.inventory_store.persist_inventory", return_value={"ok": False}):
                with self.assertRaises(RuntimeError):
                    commit_file(CHRIS, _pantry(), LINES, None, p)
            rows = [json.loads(x) for x in p.read_text().splitlines()]
            self.assertEqual([r["status"] for r in rows], ["pending", "aborted"])
            self.assertEqual(rows[0]["txn"], rows[1]["txn"])

    def test_turso_audit_and_save_in_one_transaction(self):
        calls = []

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def execute(self, sql, params=()):
                calls.append(("execute", sql.strip().split()[0]))

            def execute_transaction(self, stmts):
                calls.append(("tx", [s.split()[2] for s, _ in stmts], [p[0] for _, p in stmts][-1]))

        with mock.patch("rt_dashboard.turso_http.connect", return_value=Conn()), \
                mock.patch("rt_dashboard.inventory_store._turso_get_inventory", return_value=_pantry()):
            commit_turso(CHRIS, _pantry(), LINES)
        tx = [c for c in calls if c[0] == "tx"]
        self.assertEqual(len(tx), 1)
        tables, last_uid = tx[0][1], tx[0][2]
        self.assertEqual(tables, ["nutrition_inventory_audit(ts,", "nutrition_inventory_audit(ts,", "nutrition_inventory(user_id,"])
        self.assertEqual(last_uid, CHRIS)

    def test_turso_tx_failure_raises_and_nothing_else(self):
        with mock.patch("rt_dashboard.inventory_store._turso_get_inventory") as rb:
            class Conn:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return None

                def execute(self, *a, **k):
                    return None

                def execute_transaction(self, stmts):
                    raise RuntimeError("turso transaction rolled back")

            with mock.patch("rt_dashboard.turso_http.connect", return_value=Conn()):
                with self.assertRaises(RuntimeError):
                    commit_turso(CHRIS, _pantry(), LINES)
            rb.assert_not_called()

    def test_execute_transaction_batch_shape_and_rollback_detect(self):
        conn = turso_http.TursoConnection("https://x.turso.io", "dummy")
        sent = {}

        class Resp:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def read(self):
                return json.dumps(self.payload).encode()

        def ok_urlopen(req, timeout=None):
            sent["body"] = json.loads(req.data)
            n = len(sent["body"]["requests"][0]["batch"]["steps"])
            return Resp({"results": [{"type": "ok", "response": {"type": "batch", "result": {
                "step_results": [{}] * (n - 1) + [None], "step_errors": [None] * n}}}]})

        with mock.patch("urllib.request.urlopen", side_effect=ok_urlopen):
            conn.execute_transaction([("INSERT INTO a VALUES (?)", (1,)), ("INSERT INTO b VALUES (?)", ("x",))])
        steps = sent["body"]["requests"][0]["batch"]["steps"]
        self.assertEqual([s["stmt"]["sql"].split()[0] for s in steps], ["BEGIN", "INSERT", "INSERT", "COMMIT", "ROLLBACK"])
        self.assertEqual(steps[2]["condition"], {"type": "ok", "step": 1})

        def fail_urlopen(req, timeout=None):
            return Resp({"results": [{"type": "ok", "response": {"type": "batch", "result": {
                "step_results": [{}, None, None, None, {}], "step_errors": [None, {"message": "x"}, None, None, None]}}}]})

        with mock.patch("urllib.request.urlopen", side_effect=fail_urlopen):
            with self.assertRaises(RuntimeError):
                conn.execute_transaction([("INSERT INTO a VALUES (?)", (1,)), ("INSERT INTO b VALUES (?)", (2,))])


class SelectLoad(unittest.TestCase):
    """Default load is SELECT-only. An empty Turso row is not seeded."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "FITDASH_AGENT_TOKEN": READ, "FITDASH_INVENTORY_AGENT_TOKEN": WRITE,
            "FITDASH_INVENTORY_AGENT_USER_ID": CHRIS, "FITDASH_SERVICE_TOKEN": "house-dummy",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _conn(self, row):
        sqls = []

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=()):
                sqls.append((sql.strip(), params))

                class R:
                    def fetchone(self_inner):
                        return row
                return R()

        return sqls, Conn

    def _select(self, row, fn):
        sqls, Conn = self._conn(row)
        with mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=True), \
             mock.patch("rt_dashboard.turso_http.connect", return_value=Conn()), \
             mock.patch("rt_dashboard.inventory_store.load_preview_inventory", side_effect=AssertionError("seed")), \
             mock.patch("rt_dashboard.inventory_store._turso_put_inventory", side_effect=AssertionError("put")):
            result = fn()
        self.assertEqual(sqls, [("SELECT payload FROM nutrition_inventory WHERE user_id = ?", (CHRIS,))])
        return result

    def test_empty_turso_row_is_select_only(self):
        inv = self._select(None, lambda: agent_write._default_load(CHRIS))
        self.assertEqual(inv, {"ingredients": []})

    def test_invalid_payload_does_not_seed(self):
        inv = self._select({"payload": "not-json"}, lambda: agent_write._default_load(CHRIS))
        self.assertEqual(inv, {"ingredients": []})

    def test_existing_row_returns_payload(self):
        blob = json.dumps(_pantry())
        inv = self._select({"payload": blob}, lambda: agent_write._default_load(CHRIS))
        self.assertEqual([i["id"] for i in inv["ingredients"]], ["oats", "tilapia", "rice"])

    def test_tuple_row(self):
        inv = self._select((json.dumps(_pantry()),), lambda: agent_write._default_load(CHRIS))
        self.assertEqual(inv["ingredients"][0]["id"], "oats")

    def test_turso_dark_reads_file_and_does_not_connect(self):
        with mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False), \
             mock.patch("rt_dashboard.turso_http.connect", side_effect=AssertionError("connect")), \
             mock.patch("rt_dashboard.inventory_store.load_workspace_inventory", return_value=(deepcopy(_pantry()), "file")), \
             mock.patch("rt_dashboard.inventory_store.load_preview_inventory", side_effect=AssertionError("seed")):
            inv = agent_write._default_load(CHRIS)
        self.assertEqual(inv["ingredients"][0]["id"], "oats")

    def test_400_on_empty_row_does_not_commit(self):
        commits = []
        store = Store(commit=lambda uid, inv, lines: commits.append(uid) or inv)

        def run():
            return respond_agent_stock_write(_w(WRITE), GOOD, store, now=NOW)

        status, out = self._select(None, run)
        self.assertEqual(status, 400, out)
        self.assertIn("not a known item", out["message"])
        self.assertEqual(commits, [])

    def test_existing_row_write_commits_without_seed(self):
        commits = []
        store = Store(commit=lambda uid, inv, lines: commits.append(uid) or deepcopy(inv))
        body = {"items": [{"id": "oats", "qty": 4}], "updated_by": "nourish"}

        def run():
            return respond_agent_stock_write(_w(WRITE), body, store, now=NOW)

        status, out = self._select({"payload": json.dumps(_pantry())}, run)
        self.assertEqual(status, 200, out)
        self.assertEqual(commits, [CHRIS])
        self.assertEqual(out["items"][0]["qty"], 4)

    def test_inf_qty_on_existing_row_does_not_commit(self):
        commits = []
        store = Store(commit=lambda uid, inv, lines: commits.append(uid) or inv)
        body = {"items": [{"id": "oats", "qty": float("inf")}], "updated_by": "nourish"}

        def run():
            return respond_agent_stock_write(_w(WRITE), body, store, now=NOW)

        status, out = self._select({"payload": json.dumps(_pantry())}, run)
        self.assertEqual(status, 400, out)
        self.assertIn("finite", out["message"])
        self.assertEqual(commits, [])


class HttpRoute(unittest.TestCase):
    """End-to-end: write, then GET /api/agent/inventory reflects it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = Path(self.tmp.name) / "nutrition_inventory_audit.jsonl"
        self.mem = {"inv": _pantry()}
        mem = self.mem

        def commit(uid, inv, lines, _factory=None):
            self.commit_uids.append(uid)
            agent_write.append_audit_jsonl(lines, self.audit)
            mem["inv"] = deepcopy(inv)
            return deepcopy(inv)

        self.commit_uids = []

        self._patches = [
            mock.patch.dict(os.environ, {
                "FITDASH_AGENT_TOKEN": READ, "FITDASH_INVENTORY_AGENT_TOKEN": WRITE,
                "FITDASH_INVENTORY_AGENT_USER_ID": CHRIS, "FITDASH_SERVICE_LOOPBACK": "1", "FITDASH_INVENTORY_AUDIT_PATH": str(self.audit)}),
            mock.patch.object(fitdash_server, "load_dashboard_data", return_value={"meta": {}, "health": {}}),
            mock.patch.object(fitdash_server, "_auth_required", return_value=True),
            mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False),
            mock.patch("rt_dashboard.dashboard_cache.load_health_cache", return_value=(None, None, {})),
            mock.patch("rt_dashboard.inventory_store.load_workspace_inventory",
                       side_effect=lambda: (deepcopy(mem["inv"]), "fitness/nutrition/inventory.json")),
            mock.patch.object(agent_write, "_default_commit", side_effect=commit),
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
        self.assertEqual(self.commit_uids, [CHRIS])
        status, got = self._req("/api/agent/inventory", READ)
        self.assertEqual(status, 200, got)
        items = {i["id"]: i for i in got["data"]["items"]}
        self.assertTrue(items["tilapia"]["in_stock"])
        self.assertEqual(items["tilapia"]["updated_at"], out["as_of"])

    def test_write_token_rejected_on_get_routes(self):
        for s in SECTIONS:
            with self.subTest(section=s):
                self.assertEqual(self._req(f"/api/agent/{s}", WRITE)[0], 401)

    def test_read_token_rejected_on_session_stock_route(self):
        # The inventory-agent token is the house credential for /api/inventory/stock
        # (unchanged); the agent READ token must not write there either.
        status, _ = self._req("/api/inventory/stock", READ, {"id": "tilapia", "in_stock": True})
        self.assertEqual(status, 401)
        self.assertFalse(self.mem["inv"]["ingredients"][1]["in_stock"])


if __name__ == "__main__":
    unittest.main()
