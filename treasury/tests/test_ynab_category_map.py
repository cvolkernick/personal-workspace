"""Tests for YNAB category map SoT, bootstrap, and write helpers (#340)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.ynab_category_map import (  # noqa: E402
    DEFAULT_BUDGET_NAME,
    HARD_FORBID,
    MAP_PATH,
    SCHEMA_VERSION,
    build_draft_map,
    categories_from_ynab_payload,
    empty_map,
    enabled_category_ids,
    load_category_map,
    validate_category_map,
)
from treasury.ynab_category_map_bootstrap import (  # noqa: E402
    bootstrap_category_map,
    draft_map_from_fetch,
    pick_budget_strict,
    write_draft_map,
)
from treasury.ynab_write import (  # noqa: E402
    assert_patch_allowed,
    categorize_and_approve,
)


ENABLED_CAT = "cat-enabled-1"
DISABLED_CAT = "cat-disabled-1"
UNKNOWN_CAT = "cat-not-in-map"


def _sot_map(**overrides):
    base = {
        "schema_version": 0,
        "budget_id": "bud-chris-1",
        "budget_name": "Chris's Plan",
        "allow_approve": True,
        "allow_categorize": True,
        "forbid": ["transfer", "payment", "move_money"],
        "categories": [
            {
                "id": ENABLED_CAT,
                "name": "Groceries",
                "group_id": "g-food",
                "group_name": "Food",
                "hidden": False,
                "enabled": True,
            },
            {
                "id": DISABLED_CAT,
                "name": "Vacation",
                "group_id": "g-fun",
                "group_name": "Fun",
                "hidden": False,
                "enabled": False,
            },
        ],
        "payee_rules": [],
    }
    base.update(overrides)
    return validate_category_map(base)


def _ynab_categories_payload():
    return {
        "data": {
            "category_groups": [
                {
                    "id": "g-food",
                    "name": "Food",
                    "hidden": False,
                    "deleted": False,
                    "categories": [
                        {
                            "id": "c-real-groceries",
                            "name": "Groceries",
                            "hidden": False,
                            "deleted": False,
                        },
                        {
                            "id": "c-real-restaurants",
                            "name": "Restaurants",
                            "hidden": False,
                            "deleted": False,
                        },
                        {
                            "id": "",
                            "name": "Should Be Skipped — no id",
                            "hidden": False,
                            "deleted": False,
                        },
                        {
                            "id": "c-deleted",
                            "name": "Old",
                            "hidden": False,
                            "deleted": True,
                        },
                    ],
                },
                {
                    "id": "g-cc",
                    "name": "Credit Card Payments",
                    "hidden": False,
                    "deleted": False,
                    "categories": [
                        {
                            "id": "c-real-one-card-payment",
                            "name": "Coinbase One Card Payment",
                            "hidden": False,
                            "deleted": False,
                        }
                    ],
                },
                {
                    "id": "g-internal",
                    "name": "Internal Master Category",
                    "hidden": False,
                    "deleted": False,
                    "categories": [
                        {
                            "id": "c-real-uncategorized",
                            "name": "Uncategorized",
                            "hidden": False,
                            "deleted": False,
                        }
                    ],
                },
            ]
        }
    }


class TestSoTSchema(unittest.TestCase):
    def test_checked_in_map_is_pinned_sot(self):
        data = load_category_map(MAP_PATH)
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertEqual(data["budget_name"], DEFAULT_BUDGET_NAME)
        self.assertEqual(data["budget_id"], "37502ae1-2484-4e3d-90a1-8985d775e86b")
        self.assertTrue(data["allow_approve"])
        self.assertTrue(data["allow_categorize"])
        self.assertTrue(HARD_FORBID.issubset(set(data["forbid"])))
        self.assertEqual(data["payee_rules"], [])
        cats = data["categories"]
        self.assertEqual(len(cats), 15)
        enabled = [c for c in cats if c.get("enabled") is True]
        disabled = [c for c in cats if c.get("enabled") is not True]
        self.assertEqual(len(enabled), 12)
        self.assertEqual(len(disabled), 3)
        self.assertEqual(len(enabled_category_ids(data)), 12)
        self.assertEqual(
            {c["name"] for c in disabled},
            {
                "Inflow: Ready to Assign",
                "Uncategorized",
                "Coinbase One Card – 5361",
            },
        )
        for row in cats:
            cid = str(row.get("id") or "")
            self.assertRegex(cid, r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

    def test_empty_map_does_not_invent_ids(self):
        m = empty_map()
        self.assertEqual(m["categories"], [])
        self.assertEqual(enabled_category_ids(m), set())
        self.assertIn("bootstrap", m["notes"])


class TestOutOfMapRefuse(unittest.TestCase):
    def test_unknown_category_refused(self):
        result = categorize_and_approve(
            "tx-1",
            UNKNOWN_CAT,
            category_map=_sot_map(),
            dry_run=True,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["refused"])
        self.assertEqual(result["reason"], "out_of_map")
        self.assertEqual(result["category_id"], UNKNOWN_CAT)

    def test_disabled_category_refused(self):
        result = categorize_and_approve(
            "tx-1",
            DISABLED_CAT,
            category_map=_sot_map(),
            dry_run=True,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "out_of_map")
        self.assertIn(ENABLED_CAT, result["enabled_ids"])
        self.assertNotIn(DISABLED_CAT, result["enabled_ids"])


class TestForbidTransfer(unittest.TestCase):
    def test_action_transfer_refused(self):
        result = categorize_and_approve(
            "tx-1",
            ENABLED_CAT,
            category_map=_sot_map(),
            action="transfer",
            dry_run=True,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "forbidden_action")
        self.assertEqual(result["action"], "transfer")

    def test_payment_and_move_money_refused(self):
        for action in ("payment", "move_money"):
            result = categorize_and_approve(
                "tx-1",
                ENABLED_CAT,
                category_map=_sot_map(),
                action=action,
                dry_run=False,
                token="t",
            )
            self.assertFalse(result["ok"], action)
            self.assertEqual(result["reason"], "forbidden_action", action)

    def test_transfer_account_on_existing_tx_refused(self):
        result = categorize_and_approve(
            "tx-1",
            ENABLED_CAT,
            category_map=_sot_map(),
            transaction={"id": "tx-1", "transfer_account_id": "acct-other"},
            dry_run=True,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "forbidden_action")
        self.assertEqual(result["action"], "transfer")

    def test_patch_path_transfer_refused(self):
        blocked = assert_patch_allowed(
            "/budgets/bud-1/transactions/tx-1/transfer",
            {"transaction": {"category_id": ENABLED_CAT, "approved": True}},
        )
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked["reason"], "forbidden_path")

    def test_patch_cannot_smuggle_transfer_account(self):
        blocked = assert_patch_allowed(
            "/budgets/bud-1/transactions/tx-1",
            {
                "transaction": {
                    "category_id": ENABLED_CAT,
                    "approved": True,
                    "transfer_account_id": "acct-other",
                }
            },
        )
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked["reason"], "forbidden_payload")

    def test_move_money_month_path_refused(self):
        blocked = assert_patch_allowed(
            "/budgets/bud-1/months/2026-08-01/categories/cat-1",
            {"category": {"budgeted": 1000}},
        )
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked["reason"], "forbidden_path")


class TestHappyPathMocked(unittest.TestCase):
    def test_dry_run_default_does_not_patch(self):
        calls = []

        def _patch(path, token, payload):
            calls.append((path, token, payload))
            return {"data": {"transaction": {"id": "tx-1"}}}

        result = categorize_and_approve(
            "tx-1",
            ENABLED_CAT,
            category_map=_sot_map(),
            token="secret",
            patch_fn=_patch,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(calls, [])
        self.assertEqual(result["would_patch"]["method"], "PATCH")
        self.assertEqual(
            result["would_patch"]["path"],
            "/budgets/bud-chris-1/transactions/tx-1",
        )
        self.assertEqual(
            result["would_patch"]["transaction"],
            {"category_id": ENABLED_CAT, "approved": True},
        )

    def test_live_categorize_and_approve_mocked(self):
        calls = []

        def _patch(path, token, payload):
            calls.append((path, token, payload))
            return {
                "data": {
                    "transaction": {
                        "id": "tx-1",
                        "category_id": ENABLED_CAT,
                        "approved": True,
                    }
                }
            }

        result = categorize_and_approve(
            "tx-1",
            ENABLED_CAT,
            category_map=_sot_map(),
            dry_run=False,
            token="secret",
            patch_fn=_patch,
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["dry_run"])
        self.assertEqual(len(calls), 1)
        path, token, payload = calls[0]
        self.assertEqual(path, "/budgets/bud-chris-1/transactions/tx-1")
        self.assertEqual(token, "secret")
        self.assertEqual(
            payload,
            {"transaction": {"category_id": ENABLED_CAT, "approved": True}},
        )
        self.assertNotIn("transfer_account_id", payload["transaction"])
        self.assertEqual(result["patched"]["data"]["transaction"]["approved"], True)

    def test_live_without_budget_id_refused(self):
        result = categorize_and_approve(
            "tx-1",
            ENABLED_CAT,
            category_map=_sot_map(budget_id=""),
            dry_run=False,
            token="secret",
            patch_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("should not patch")),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "budget_id_unpinned")


class TestBootstrapNoInvent(unittest.TestCase):
    def test_flatten_copies_only_payload_ids(self):
        cats = categories_from_ynab_payload(_ynab_categories_payload())
        ids = [c["id"] for c in cats]
        self.assertEqual(
            set(ids),
            {
                "c-real-groceries",
                "c-real-restaurants",
                "c-real-one-card-payment",
                "c-real-uncategorized",
            },
        )
        self.assertNotIn("c-deleted", ids)
        self.assertTrue(all(i.startswith("c-real-") for i in ids))
        by_id = {c["id"]: c for c in cats}
        self.assertTrue(by_id["c-real-groceries"]["enabled"])
        self.assertFalse(by_id["c-real-one-card-payment"]["enabled"])
        self.assertFalse(by_id["c-real-uncategorized"]["enabled"])

    def test_empty_or_malformed_payload_does_not_invent(self):
        self.assertEqual(categories_from_ynab_payload({}), [])
        self.assertEqual(categories_from_ynab_payload({"data": {}}), [])
        self.assertEqual(
            categories_from_ynab_payload(
                {"data": {"category_groups": [{"name": "Food", "categories": [{"name": "X"}]}]}}
            ),
            [],
        )

    def test_build_draft_does_not_invent_or_dedupes(self):
        draft = build_draft_map(
            budget_id="bud-from-api",
            budget_name="Chris's Plan",
            categories=[
                {"id": "c-real-a", "name": "A", "enabled": True},
                {"id": "c-real-a", "name": "A dup", "enabled": True},
                {"id": "", "name": "no id", "enabled": True},
            ],
        )
        ids = [c["id"] for c in draft["categories"]]
        self.assertEqual(ids, ["c-real-a"])
        self.assertEqual(draft["budget_id"], "bud-from-api")

    def test_pick_budget_strict_does_not_fall_back(self):
        budgets = [{"id": "other", "name": "Someone Else"}]
        with self.assertRaises(RuntimeError) as ctx:
            pick_budget_strict(budgets, "Chris's Plan")
        self.assertIn("not found", str(ctx.exception))

    def test_writes_draft_not_sot_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            draft_path = Path(td) / "ynab_category_map.draft.json"
            sot_path = Path(td) / "ynab_category_map.json"
            sot_path.write_text(json.dumps(empty_map()), encoding="utf-8")
            draft = build_draft_map(
                budget_id="bud-from-api",
                budget_name="Chris's Plan",
                categories=[{"id": "c-real-a", "name": "A", "enabled": True}],
            )
            result = write_draft_map(
                draft,
                draft_path=draft_path,
                sot_path=sot_path,
                overwrite_sot=False,
            )
            self.assertTrue(result["ok"])
            self.assertFalse(result["sot_written"])
            self.assertTrue(draft_path.is_file())
            sot = json.loads(sot_path.read_text(encoding="utf-8"))
            self.assertEqual(sot["categories"], [])
            self.assertEqual(sot.get("budget_id") or "", "")
            written = json.loads(draft_path.read_text(encoding="utf-8"))
            self.assertEqual(written["budget_id"], "bud-from-api")
            self.assertEqual([c["id"] for c in written["categories"]], ["c-real-a"])

    def test_bootstrap_mocked_get_does_not_invent_ids(self):
        budgets = {
            "data": {
                "budgets": [
                    {"id": "bud-from-api", "name": "Chris's Plan"},
                    {"id": "bud-other", "name": "Other"},
                ]
            }
        }
        cats = _ynab_categories_payload()

        def _get(path, token, params=None):
            self.assertEqual(token, "sealed-pat")
            if path == "/budgets":
                return budgets
            if path == "/budgets/bud-from-api/categories":
                return cats
            self.fail(f"unexpected GET {path}")

        with tempfile.TemporaryDirectory() as td:
            draft_path = Path(td) / "ynab_category_map.draft.json"
            sot_path = Path(td) / "ynab_category_map.json"
            sot_path.write_text(json.dumps(empty_map()), encoding="utf-8")
            with patch(
                "treasury.ynab_category_map_bootstrap.ynab_get",
                side_effect=_get,
            ):
                result = bootstrap_category_map(
                    token="sealed-pat",
                    draft_path=draft_path,
                    sot_path=sot_path,
                    overwrite_sot=False,
                )
            self.assertTrue(result["ok"])
            self.assertFalse(result["sot_written"])
            self.assertEqual(result["budget_id"], "bud-from-api")
            self.assertEqual(
                set(result["category_ids"]),
                {
                    "c-real-groceries",
                    "c-real-restaurants",
                    "c-real-one-card-payment",
                    "c-real-uncategorized",
                },
            )
            for invented in ("c-invented", "placeholder", "TODO"):
                self.assertNotIn(invented, result["category_ids"])
            sot = json.loads(sot_path.read_text(encoding="utf-8"))
            self.assertEqual(sot["categories"], [])

    def test_draft_from_fetch_uses_budget_id_from_get(self):
        fetched = {
            "budget": {"id": "bud-from-api", "name": "Chris's Plan"},
            "categories_payload": _ynab_categories_payload(),
        }
        draft = draft_map_from_fetch(fetched)
        self.assertEqual(draft["budget_id"], "bud-from-api")
        self.assertEqual(draft["budget_name"], "Chris's Plan")
        self.assertTrue(all(c["id"].startswith("c-real-") for c in draft["categories"]))


if __name__ == "__main__":
    unittest.main()
