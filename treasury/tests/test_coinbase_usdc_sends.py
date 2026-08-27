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
    RENT_DEST_EMAIL,
    RENT_DEST_FINGERPRINTS,
    actual_for_item,
    collect_usdc_sends,
    is_usdc_send,
    load_send_book,
    matches_rent,
    matches_thais,
    send_spend_amount,
    write_send_book,
)


def _send(**overrides):
    row = {
        "id": "s1",
        "type": "send",
        "status": "completed",
        "created_at": "2026-08-10T14:00:00Z",
        "amount": {"amount": "-895.00", "currency": "USDC"},
        "to": {"resource": "address"},
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

    def test_unlabeled_aug_and_july_phone_sends_not_assigned(self) -> None:
        unlabeled = [
            _send(
                id="aug10-unlabeled-5",
                created_at="2026-08-10T12:00:00Z",
                description="",
                to={"resource": "address"},
                amount={"amount": "-5.00", "currency": "USDC"},
            ),
            _send(
                id="aug4-unlabeled-125",
                created_at="2026-08-04T12:00:00Z",
                description="",
                to={"resource": "address"},
                amount={"amount": "-125.00", "currency": "USDC"},
            ),
            _send(
                id="jul-phone-20",
                created_at="2026-07-12T12:00:00Z",
                description="",
                to={"resource": "phone"},
                amount={"amount": "-20.00", "currency": "USDC"},
            ),
            _send(
                id="jul-phone-40",
                created_at="2026-07-20T12:00:00Z",
                description="",
                to={"resource": "phone"},
                amount={"amount": "-40.00", "currency": "USDC"},
            ),
        ]
        book = collect_usdc_sends({"transactions": unlabeled + [_send()]})
        for tx in unlabeled:
            self.assertFalse(matches_thais(tx), tx["id"])
            self.assertFalse(matches_rent(tx), tx["id"])
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 8, 15)),
            895.0,
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Rent", month=date(2026, 8, 15)),
            0.0,
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Rent", month=date(2026, 7, 15)),
            0.0,
        )
        self.assertFalse(any(abs(send_spend_amount(t) - 2090.0) < 1 for t in book))

    def test_rent_email_only_counts_pending_send(self) -> None:
        self.assertEqual(RENT_DEST_FINGERPRINTS, frozenset({RENT_DEST_EMAIL}))
        self.assertEqual(RENT_DEST_EMAIL, "nvolkern@gmail.com")
        email_send = _send(
            id="aug27-rent-email-25",
            status="pending",
            created_at="2026-08-27T12:00:00Z",
            description="",
            to={"resource": "email", "email": "Nvolkern@Gmail.com"},
            amount={"amount": "-25.00", "currency": "USDC"},
        )
        phone = _send(
            id="nicole-phone",
            created_at="2026-08-15T12:00:00Z",
            description="Nicole Volkernick",
            to={"resource": "phone", "phone": "+15555550100"},
            amount={"amount": "-25.00", "currency": "USDC"},
        )
        unlabeled_5 = _send(
            id="aug10-unlabeled-5",
            created_at="2026-08-10T12:00:00Z",
            description="",
            to={"resource": "address"},
            amount={"amount": "-5.00", "currency": "USDC"},
        )
        unlabeled_125 = _send(
            id="aug4-unlabeled-125",
            created_at="2026-08-04T12:00:00Z",
            description="",
            to={"resource": "address"},
            amount={"amount": "-125.00", "currency": "USDC"},
        )
        self.assertTrue(matches_rent(email_send))
        self.assertFalse(matches_thais(email_send))
        for tx in (phone, unlabeled_5, unlabeled_125):
            self.assertFalse(matches_rent(tx), tx["id"])
            self.assertFalse(matches_thais(tx), tx["id"])
        book = collect_usdc_sends(
            {"transactions": [email_send, phone, unlabeled_5, unlabeled_125, _send()]}
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Rent", month=date(2026, 8, 27)),
            25.0,
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 8, 27)),
            895.0,
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
