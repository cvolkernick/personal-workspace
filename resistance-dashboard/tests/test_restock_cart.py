"""#554 restock → Walmart/Costco cart. No Google Tasks. Keep is fallback only."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from api.workout._util import restock_cart_body, restock_list_body
from rt_dashboard.nutrition_planner import suggest_inventory_staples
from rt_dashboard.restock_venues import tag_items
from rt_dashboard.restock_cart import (
    add_costco_list,
    add_walmart_cart,
    confirm_received,
    keep_line,
    push_items,
    restock_list,
    retry_held,
    session_status,
    write_keep_hold,
)
from rt_dashboard.restock_venues import venue_for_item


class VenueTags(unittest.TestCase):
    def test_named_exceptions_and_default_walmart(self):
        self.assertEqual(venue_for_item({"id": "broccoli", "name": "Broccoli"}), "walmart")
        self.assertEqual(venue_for_item({"id": "chicken-breast", "name": "Chicken breast"}), "costco")
        self.assertEqual(venue_for_item({"id": "greek-yogurt", "name": "Greek yogurt"}), "costco")
        self.assertEqual(
            venue_for_item({"id": "chicken-burrito-bowl", "name": "Chicken Burrito Bowl"}),
            "other",
        )
        self.assertEqual(
            venue_for_item({"name": "High-protein staples"}),
            "other",
        )
        self.assertEqual(venue_for_item({"id": "broccoli", "venue": "costco"}), "costco")

    def test_suggestions_and_today_purchases_are_venue_tagged(self):
        inv = {
            "ingredients": [
                {
                    "id": "broccoli",
                    "name": "Broccoli",
                    "category": "veg",
                    "in_stock": False,
                    "calories": 30,
                    "protein_g": 2,
                    "carbs_g": 6,
                    "fat_g": 0,
                },
                {
                    "id": "chicken-breast",
                    "name": "Chicken breast",
                    "category": "protein",
                    "in_stock": False,
                    "calories": 280,
                    "protein_g": 52,
                    "carbs_g": 0,
                    "fat_g": 6,
                },
            ]
        }
        out = suggest_inventory_staples(inv, max_suggestions=8)
        venues = {s.get("name"): s.get("venue") for s in out["suggestions"]}
        self.assertEqual(venues.get("Broccoli"), "walmart")
        self.assertEqual(venues.get("Chicken breast"), "costco")
        tagged = tag_items(out["suggestions"])
        self.assertTrue(tagged)
        self.assertTrue(all(p.get("venue") in ("walmart", "costco", "other") for p in tagged))


class CartWriters(unittest.TestCase):
    def test_walmart_without_session_is_auth_blocked(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = add_walmart_cart({"name": "Broccoli", "id": "broccoli"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "auth_session")
        self.assertFalse(result["checkout"])
        self.assertEqual(result["venue"], "walmart")

    def test_walmart_session_adds_cheapest_offer_no_checkout(self):
        calls = []

        def fake_get(url, headers):
            calls.append(("GET", url))
            return {
                "ok": True,
                "status": 200,
                "body": {
                    "items": [
                        {"offerId": "dear", "name": "Brand A Broccoli", "price": 3.5},
                        {"offerId": "cheap", "name": "Broccoli", "price": 1.2},
                    ]
                },
            }

        def fake_post(url, headers, body):
            calls.append(("POST", url, body))
            self.assertEqual(body["offerId"], "cheap")
            self.assertNotIn("checkout", url.lower())
            return {"ok": True, "status": 200, "body": {"ok": True}}

        env = {"WALMART_SESSION_COOKIE": "session=abc"}
        with mock.patch.dict(os.environ, env, clear=True):
            result = add_walmart_cart(
                {"name": "Broccoli", "id": "broccoli"},
                http_get=fake_get,
                http_post=fake_post,
            )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["checkout"])
        self.assertEqual(result["offer_id"], "cheap")
        self.assertTrue(any(c[0] == "POST" and "/cart/items" in c[1] for c in calls))

    def test_costco_without_session_is_auth_blocked(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = add_costco_list({"name": "Chicken breast", "id": "chicken-breast"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "auth_session")
        self.assertEqual(result["venue"], "costco")

    def test_costco_session_writes_list_no_checkout(self):
        def fake_post(url, headers, body):
            self.assertIn("costco.com", url)
            self.assertNotIn("checkout", url.lower())
            return {"ok": True, "status": 200, "body": {"ok": True}}

        env = {"COSTCO_SESSION_COOKIE": "session=xyz"}
        with mock.patch.dict(os.environ, env, clear=True):
            result = add_costco_list(
                {"name": "Chicken breast", "id": "chicken-breast"},
                http_post=fake_post,
            )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["checkout"])


class PushAndKeepFallback(unittest.TestCase):
    def test_push_routes_venues_and_does_not_create_tasks(self):
        added = []

        def wm(item):
            added.append(("walmart", item["name"]))
            return {"ok": True, "venue": "walmart", "item": item}

        def cc(item):
            added.append(("costco", item["name"]))
            return {"ok": True, "venue": "costco", "item": item}

        items = [
            {"name": "Broccoli", "id": "broccoli"},
            {"name": "Chicken breast", "id": "chicken-breast"},
            {"name": "High-protein staples"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RESISTANCE_DASHBOARD_CONFIG_DIR": tmp}, clear=True):
                result = push_items(items, walmart_add=wm, costco_add=cc, keep_write=lambda rows: {"ok": True})
        self.assertFalse(result["google_tasks"])
        self.assertFalse(result["checkout"])
        self.assertEqual(added, [("walmart", "Broccoli"), ("costco", "Chicken breast")])
        self.assertEqual(len(result["parked"]), 1)
        self.assertEqual(result["parked"][0]["venue"], "other")

    def test_auth_block_lands_on_keep_hold_with_venue_tags_and_retry(self):
        items = [{"name": "Broccoli", "id": "broccoli", "action": "restock"}]
        keep_writes = []

        def keep(rows):
            keep_writes.append([keep_line(x) for x in rows])
            return {"ok": True, "blocked": False, "lines": keep_writes[-1]}

        with tempfile.TemporaryDirectory() as tmp:
            env = {"RESISTANCE_DASHBOARD_CONFIG_DIR": tmp}
            with mock.patch.dict(os.environ, env, clear=True):
                first = push_items(
                    items,
                    walmart_add=lambda item: {
                        "ok": False,
                        "blocked": True,
                        "reason": "auth_session",
                        "venue": "walmart",
                        "item": item,
                    },
                    keep_write=keep,
                )
                self.assertTrue(first["retry"])
                self.assertTrue(first["blocked"])
                self.assertIn("[walmart] Restock: Broccoli", keep_writes[0])
                retried = []

                def wm_ok(item):
                    retried.append(item["name"])
                    return {"ok": True, "venue": "walmart", "item": item}

                second = retry_held(walmart_add=wm_ok, keep_write=keep)
        self.assertEqual(retried, ["Broccoli"])
        self.assertEqual(second["added"][0]["name"], "Broccoli")
        self.assertFalse(second["google_tasks"])

    def test_keep_without_creds_is_honest_and_hold_still_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"RESISTANCE_DASHBOARD_CONFIG_DIR": tmp}, clear=True):
                keep = write_keep_hold([{"name": "Broccoli", "venue": "walmart"}])
                self.assertFalse(keep["ok"])
                self.assertTrue(keep["blocked"])
                self.assertEqual(keep["reason"], "auth_session")
                listed = restock_list([{"name": "Broccoli", "id": "broccoli"}])
        self.assertFalse(listed["google_tasks"])
        self.assertEqual(listed["by_venue"]["walmart"][0]["name"], "Broccoli")
        self.assertFalse(session_status("walmart")["ok"])

    def test_confirm_received_marks_stock_without_gt(self):
        inv = {
            "ingredients": [
                {
                    "id": "broccoli",
                    "name": "Broccoli",
                    "in_stock": False,
                    "calories": 30,
                    "protein_g": 2,
                    "carbs_g": 6,
                    "fat_g": 0,
                }
            ]
        }
        result = confirm_received(
            [{"name": "Broccoli", "id": "broccoli"}],
            inventory=inv,
        )
        self.assertTrue(result["wrote"])
        self.assertFalse(result["google_tasks"])
        self.assertFalse(result["checkout"])
        row = next(i for i in result["inventory"]["ingredients"] if i["id"] == "broccoli")
        self.assertTrue(row.get("in_stock"))


class RestockApi(unittest.TestCase):
    def test_service_list_and_cart_do_not_create_gts(self):
        env = {"FITDASH_SERVICE_TOKEN": "svc", "FITDASH_SERVICE_LOOPBACK": "0"}
        headers = {"Authorization": "Bearer svc"}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {**env, "RESISTANCE_DASHBOARD_CONFIG_DIR": tmp}, clear=True):
                status, listed = restock_list_body(headers, client_host="10.0.0.8")
                self.assertEqual(status, 200)
                self.assertFalse(listed["google_tasks"])
                self.assertIn("by_venue", listed)
                status, pushed = restock_cart_body(
                    headers,
                    {
                        "items": [
                            {"name": "Broccoli", "id": "broccoli"},
                            {"name": "Chicken breast", "id": "chicken-breast"},
                        ]
                    },
                    "POST",
                    client_host="10.0.0.8",
                )
        self.assertEqual(status, 200)
        self.assertFalse(pushed["google_tasks"])
        self.assertFalse(pushed["checkout"])
        self.assertTrue(pushed["retry"] or pushed["blocked"] or pushed["added"])
        venues = {r.get("venue") for r in pushed.get("results") or []}
        self.assertIn("walmart", venues)
        self.assertIn("costco", venues)
