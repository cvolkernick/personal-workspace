"""Coinbase v2 USDC send book — type=send only, fixtures only."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.coinbase_usdc_sends import (  # noqa: E402
    IGNORE_TYPES,
    PRISM_KEY_PATH,
    actual_for_item,
    collect_usdc_sends,
    is_usdc_send,
    load_send_book,
    matches_rent,
    matches_thais,
    write_send_book,
)


def _send(**overrides):
    row = {
        "id": "s1",
        "type": "send",
        "status": "completed",
        "created_at": "2026-08-10T14:00:00Z",
        "amount": {"amount": "-895.00", "currency": "USDC"},
        "to": {"resource": "address", "address": "0xthaisdestfixture"},
        "description": "Thaís",
    }
    row.update(overrides)
    return row


class TestSendFilter(unittest.TestCase):
    def test_type_send_usdc_kept(self) -> None:
        sends = collect_usdc_sends({"transactions": [_send()]})
        self.assertEqual(len(sends), 1)
        self.assertTrue(is_usdc_send(sends[0]))
        self.assertTrue(matches_thais(sends[0]))

    def test_lend_and_lock_ignored(self) -> None:
        raw = [
            _send(id="lend", type="retail_defi_lend_withdrawal", amount={"amount": "895.00", "currency": "USDC"}),
            _send(id="lock", type="lock", amount={"amount": "-25.00", "currency": "USDC"}),
            _send(id="cc", type="credit_card_collateral_lock", amount={"amount": "-895.00", "currency": "USDC"}),
            _send(),
        ]
        sends = collect_usdc_sends({"transactions": raw})
        self.assertEqual([t["id"] for t in sends], ["s1"])
        for ignored in IGNORE_TYPES:
            self.assertFalse(is_usdc_send(_send(type=ignored)))

    def test_july_not_in_august_actual(self) -> None:
        book = collect_usdc_sends(
            {
                "transactions": [
                    _send(id="jul", created_at="2026-07-10T14:00:00Z", amount={"amount": "-900.00", "currency": "USDC"}),
                    _send(),
                ]
            }
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 8, 15)),
            895.0,
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 7, 15)),
            900.0,
        )

    def test_rent_dest_hold_never_matches(self) -> None:
        tx = _send(
            id="maybe-rent",
            description="Nicole Volkernick",
            to={"resource": "phone", "phone": "+15555550100"},
            amount={"amount": "-25.00", "currency": "USDC"},
        )
        self.assertFalse(matches_rent(tx))
        self.assertAlmostEqual(
            actual_for_item([tx], item_name="Rent", month=date(2026, 8, 15)),
            0.0,
        )

    def test_load_send_book_from_snapshots_not_disk_key(self) -> None:
        snaps = {"coinbase_usdc_sends": {"transactions": [_send()]}}
        book = load_send_book(snaps)
        self.assertEqual(len(book), 1)
        self.assertFalse(str(PRISM_KEY_PATH).endswith("ynab_category_map.json"))
        self.assertIn("cdp-api-key.json", str(PRISM_KEY_PATH))

    def test_write_send_book_filters_payload(self) -> None:
        payload = {
            "data": {
                "transactions": [
                    _send(),
                    _send(id="lend", type="retail_defi_lend_withdrawal"),
                ]
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "coinbase_usdc_sends.json"
            book = write_send_book(payload, dest)
            self.assertEqual(len(book["transactions"]), 1)
            loaded = load_send_book(path=dest)
            self.assertEqual(len(loaded), 1)


if __name__ == "__main__":
    unittest.main()
