"""POST /api/agent/inventory/items (agent pantry create) + richer GET inventory.

Same Chris-bound FITDASH_INVENTORY_AGENT_TOKEN principal and row as the stock
write. Create-only: existing id/name -> 409, no edits or deletes. Audit lines
(field "created") land with the save. No schema change: the pantry is the JSON
payload in nutrition_inventory and the audit table's `field` is free TEXT.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rt_dashboard.agent_read import build_inventory  # noqa: E402
from rt_dashboard.agent_write import (  # noqa: E402
    Store,
    audit_sql_rows,
    respond_agent_create_write,
)

READ = "dummy-read-token-not-real"
WRITE = "dummy-write-token-not-real"
CHRIS = "chris-google-sub-dummy"
NOW = datetime(2026, 10, 31, 22, 0, 0, tzinfo=timezone.utc)
STAMP = "2026-10-31T22:00:00Z"


def _pantry():
    return {"ingredients": [
        {"id": "oats", "name": "Oats", "in_stock": True, "stock": "in", "qty": 2,
         "serving_g": 40, "calories": 150, "protein_g": 5, "carbs_g": 27, "fat_g": 3},
        {"id": "tilapia", "name": "Tilapia", "in_stock": False, "stock": "out"},
    ]}


GOOD_ITEM = {"name": "Greek Yogurt", "category": "protein", "serving_g": 170,
             "calories": 100, "protein_g": 17, "carbs_g": 6, "fat_g": 0, "qty": 4}


class Base(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "FITDASH_AGENT_TOKEN": READ, "FITDASH_INVENTORY_AGENT_TOKEN": WRITE,
            "FITDASH_INVENTORY_AGENT_USER_ID": CHRIS, "FITDASH_SERVICE_TOKEN": "house-dummy"})
        self.env.start()
        self.inv = _pantry()
        self.audit, self.commits = [], []

    def tearDown(self):
        self.env.stop()

    def store(self):
        def load(uid):
            return deepcopy(self.inv)

        def commit(uid, inv, lines):
            self.commits.append(uid)
            self.audit.extend(lines)
            self.inv = deepcopy(inv)
            return deepcopy(inv)
        return Store(load=load, commit=commit)

    def call(self, body, token=WRITE):
        h = {"Authorization": f"Bearer {token}"} if token else {}
        return respond_agent_create_write(h, body, self.store(), now=NOW)


class Auth(Base):
    def test_only_inventory_agent_token(self):
        body = {"items": [GOOD_ITEM], "updated_by": "nourish"}
        for tok, reason in ((None, "missing"), ("nope", "invalid"), (READ, "invalid"), ("house-dummy", "invalid")):
            with self.subTest(tok=tok):
                status, out = self.call(body, tok)
                self.assertEqual(status, 401)
                self.assertEqual(out["reason"], reason)
                for t in (READ, WRITE, "house-dummy"):
                    self.assertNotIn(t, json.dumps(out))
        self.assertEqual(self.commits, [])

    def test_unbound_user_fails_closed(self):
        with mock.patch.dict(os.environ, {"FITDASH_INVENTORY_AGENT_USER_ID": "default"}):
            status, out = self.call({"items": [GOOD_ITEM], "updated_by": "n"})
        self.assertEqual((status, out["reason"]), (401, "unconfigured"))


class Create(Base):
    def test_adds_item_with_audit(self):
        status, out = self.call({"items": [GOOD_ITEM], "updated_by": "nourish"})
        self.assertEqual(status, 200, out)
        self.assertEqual(self.commits, [CHRIS])
        new = self.inv["ingredients"][-1]
        self.assertEqual(new["id"], "greek-yogurt")
        self.assertEqual((new["stock"], new["in_stock"], new["qty"]), ("in", True, 4))
        self.assertEqual((new["protein_g"], new["serving_g"]), (17.0, 170.0))
        self.assertEqual((new["updated_by"], new["updated_at"]), ("nourish", STAMP))
        self.assertEqual(len(self.inv["ingredients"]), 3)
        self.assertTrue(out["items"][0]["saved"])
        self.assertEqual(self.audit[0]["created"]["old"], None)
        rows = audit_sql_rows(self.audit)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1][3], "created")

    def test_out_of_stock_on_create(self):
        status, _ = self.call({"items": [dict(GOOD_ITEM, in_stock=False)], "updated_by": "n"})
        self.assertEqual(status, 200)
        self.assertEqual(self.inv["ingredients"][-1]["stock"], "out")

    def test_existing_id_or_name_is_409(self):
        for item in ({"id": "oats", "name": "New Oats", "serving_g": 40},
                     {"name": "oats", "serving_g": 40}):
            with self.subTest(item=item):
                status, out = self.call({"items": [item], "updated_by": "n"})
                self.assertEqual(status, 409)
                self.assertIn("restock", out["message"])
        self.assertEqual(self.commits, [])

    def test_duplicate_in_request_is_409(self):
        status, _ = self.call({"items": [GOOD_ITEM, GOOD_ITEM], "updated_by": "n"})
        self.assertEqual(status, 409)
        self.assertEqual(self.commits, [])

    def test_validation(self):
        bad = [
            {"items": [], "updated_by": "n"},
            {"items": [GOOD_ITEM]},
            {"items": [GOOD_ITEM], "updated_by": "n", "user_id": "x"},
            {"items": [dict(GOOD_ITEM, stock="in")], "updated_by": "n"},
            {"items": [dict(GOOD_ITEM, delete=True)], "updated_by": "n"},
            {"items": [{"name": ""}], "updated_by": "n"},
            {"items": [dict(GOOD_ITEM, calories=-1)], "updated_by": "n"},
            {"items": [dict(GOOD_ITEM, qty=float("inf"))], "updated_by": "n"},
            {"items": [dict(GOOD_ITEM, protein_g=True)], "updated_by": "n"},
            {"items": [dict(GOOD_ITEM, in_stock="yes")], "updated_by": "n"},
            {"items": [dict(GOOD_ITEM, name="x" * 81)], "updated_by": "n"},
            {"items": [dict(GOOD_ITEM, name=f"i{n}") for n in range(26)], "updated_by": "n"},
            [GOOD_ITEM],
        ]
        for body in bad:
            with self.subTest(body=str(body)[:60]):
                status, out = self.call(body)
                self.assertEqual(status, 400, out)
        self.assertEqual(self.commits, [])

    def test_serving_grams_optional_but_logged_serving_label_needs_grams(self):
        item = {k: v for k, v in GOOD_ITEM.items() if k != "serving_g"}
        status, out = self.call({"items": [dict(item, serving_label="1 cup")], "updated_by": "n"})
        self.assertEqual(status, 200, out)
        status, out = self.call({"items": [dict(item, name="Skyr", serving_label="1 logged serving")], "updated_by": "n"})
        self.assertEqual(status, 400, out)


class ReadShape(unittest.TestCase):
    def test_inventory_has_stock_and_per_serving_macros(self):
        data, _ = build_inventory({"payload": _pantry(), "updated_at": STAMP, "source": "turso"}, [])
        oats, tilapia = data["items"]
        self.assertEqual(oats["stock"], "in")
        self.assertEqual(oats["serving_g"], 40.0)
        self.assertEqual(oats["per_serving"]["protein_g"], 5.0)
        self.assertEqual(oats["per_serving"]["calories"], 150.0)
        self.assertEqual(tilapia["stock"], "out")
        self.assertIsNone(tilapia["per_serving"])
        self.assertEqual((data["in_stock_count"], data["out_of_stock_count"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
