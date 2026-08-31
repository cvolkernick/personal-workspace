"""Coinbase v2 USDC send book — type=send only, fixtures only."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.coinbase_usdc_sends import (  # noqa: E402
    IGNORE_TYPES,
    JR_DEST_TEST_ID_PREFIX,
    JR_SELF_SEND_DEST,
    PAY_FRIDAY_FIRST,
    PRISM_KEY_PATH,
    RENT_DEST_EMAIL,
    RENT_DEST_FINGERPRINTS,
    THAIS_DEST,
    THAIS_EXCLUDED_SEND_IDS,
    THAIS_PRE_STANDING_AMOUNT,
    THAIS_PROOF_ID_PREFIX,
    actual_for_item,
    collect_usdc_sends,
    is_excluded_send_id,
    is_usdc_send,
    load_send_book,
    matches_jr_self_send,
    matches_rent,
    matches_thais,
    next_pay_friday,
    next_standing_sends,
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

    def test_rent_email_only_counts_completed_send(self) -> None:
        self.assertEqual(RENT_DEST_FINGERPRINTS, frozenset({RENT_DEST_EMAIL}))
        self.assertEqual(RENT_DEST_EMAIL, "nvolkern@gmail.com")
        email_send = _send(
            id="aug27-rent-email-25",
            status="completed",
            created_at="2026-08-27T12:00:00Z",
            description="",
            to={"resource": "email", "email": "Nvolkern@Gmail.com"},
            amount={"amount": "-25.00", "currency": "USDC"},
        )
        pending = _send(
            id="aug27-rent-pending",
            status="pending",
            created_at="2026-08-27T11:00:00Z",
            description="",
            to={"resource": "email", "email": "nvolkern@gmail.com"},
            amount={"amount": "-25.00", "currency": "USDC"},
        )
        failed = _send(
            id="aug27-rent-failed",
            status="failed",
            created_at="2026-08-27T10:00:00Z",
            description="",
            to={"resource": "email", "email": "nvolkern@gmail.com"},
            amount={"amount": "-25.00", "currency": "USDC"},
        )
        canceled = _send(
            id="aug27-rent-canceled",
            status="canceled",
            created_at="2026-08-27T09:00:00Z",
            description="",
            to={"resource": "email", "email": "nvolkern@gmail.com"},
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
        self.assertTrue(is_usdc_send(email_send))
        self.assertFalse(is_usdc_send(pending))
        self.assertFalse(is_usdc_send(failed))
        self.assertFalse(is_usdc_send(canceled))
        self.assertTrue(matches_rent(email_send))
        self.assertFalse(matches_thais(email_send))
        self.assertFalse(matches_rent(pending))
        for tx in (pending, failed, canceled, phone, unlabeled_5, unlabeled_125):
            self.assertFalse(matches_rent(tx), tx["id"])
            self.assertFalse(matches_thais(tx), tx["id"])
        book = collect_usdc_sends(
            {
                "transactions": [
                    email_send,
                    pending,
                    failed,
                    canceled,
                    phone,
                    unlabeled_5,
                    unlabeled_125,
                    _send(),
                ]
            }
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Rent", month=date(2026, 8, 27)),
            25.0,
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="August Rent", month=date(2026, 8, 27)),
            25.0,
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="April Rent", month=date(2026, 8, 27)),
            0.0,
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 8, 27)),
            895.0,
        )
        pending_only = collect_usdc_sends({"transactions": [pending]})
        self.assertEqual(pending_only, [])
        self.assertAlmostEqual(
            actual_for_item([pending], item_name="August Rent", month=date(2026, 8, 27)),
            0.0,
        )

    def test_smoke_proof_id_is_not_thais_actual(self) -> None:
        smoke_id = "baa3976e-3304-53f7-b168-e35f16325653"
        self.assertIn(smoke_id, THAIS_EXCLUDED_SEND_IDS)
        smoke = _send(
            id=smoke_id,
            created_at="2026-08-27T16:00:00Z",
            amount={"amount": "-1.239144", "currency": "USDC"},
            to={"resource": "address"},
            description="Thais proof",
        )
        monthly = _send(id="aug10-thais-895")
        self.assertFalse(matches_thais(smoke))
        self.assertTrue(matches_thais(monthly))
        book = collect_usdc_sends(
            {
                "transactions": [
                    smoke,
                    monthly,
                    _send(
                        id="aug27-rent-email-25",
                        created_at="2026-08-27T12:00:00Z",
                        description="",
                        to={"resource": "email", "email": "nvolkern@gmail.com"},
                        amount={"amount": "-25.00", "currency": "USDC"},
                    ),
                ]
            }
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 8, 27)),
            895.0,
        )
        self.assertNotAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 8, 27)),
            896.0,
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="August Rent", month=date(2026, 8, 27)),
            25.0,
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="April Rent", month=date(2026, 8, 27)),
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
            self.assertEqual(len(book.get("standing") or []), 3)
            kinds = {r["kind"] for r in book["standing"]}
            self.assertEqual(kinds, {"thais", "rent", "jr_self_send"})


class TestStandingSendJoin427(unittest.TestCase):
    """#427 dest/cadence rewrite. Fixtures only. No live-money."""

    def test_excluded_proof_and_dest_test_ids(self) -> None:
        self.assertTrue(is_excluded_send_id("baa3976e-3304-53f7-b168-e35f16325653"))
        self.assertTrue(is_excluded_send_id("baa3976e"))
        self.assertTrue(is_excluded_send_id("7b8bf83b-aaaa-bbbb-cccc-ddddeeeeffff"))
        self.assertTrue(is_excluded_send_id({"id": "7b8bf83b"}))
        self.assertIn(THAIS_PROOF_ID_PREFIX, "baa3976e-3304-53f7-b168-e35f16325653")
        self.assertEqual(JR_DEST_TEST_ID_PREFIX, "7b8bf83b")
        self.assertIn("baa3976e-3304-53f7-b168-e35f16325653", THAIS_EXCLUDED_SEND_IDS)

    def test_thais_standing_dest_415_from_sep11(self) -> None:
        standing = _send(
            id="thais-415-sep11",
            created_at="2026-09-11T17:00:00Z",
            amount={"amount": "-415.00", "currency": "USDC"},
            to={"resource": "address", "address": THAIS_DEST, "network": "solana"},
            description="",
        )
        self.assertTrue(matches_thais(standing))
        self.assertFalse(matches_rent(standing))
        self.assertFalse(matches_jr_self_send(standing))
        book = collect_usdc_sends({"transactions": [standing]})
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 9, 11)),
            415.0,
        )

    def test_weekly_208_never_paints(self) -> None:
        weekly = _send(
            id="thais-weekly-208",
            created_at="2026-09-11T17:00:00Z",
            amount={"amount": "-208.00", "currency": "USDC"},
            to={"resource": "address", "address": THAIS_DEST, "network": "solana"},
            description="Thaís",
        )
        earlier = _send(
            id="thais-weekly-208-aug",
            created_at="2026-08-28T17:00:00Z",
            amount={"amount": "-208.00", "currency": "USDC"},
            to={"resource": "address", "address": THAIS_DEST},
            description="Thaís",
        )
        self.assertFalse(matches_thais(weekly))
        self.assertFalse(matches_thais(earlier))
        book = collect_usdc_sends({"transactions": [weekly, earlier]})
        self.assertEqual(actual_for_item(book, item_name="Thaís", month=date(2026, 9, 11)), 0.0)
        self.assertEqual(actual_for_item(book, item_name="Thaís", month=date(2026, 8, 15)), 0.0)

    def test_sep04_never_paints(self) -> None:
        cancelled = _send(
            id="thais-sep04-cancelled",
            created_at="2026-09-04T17:00:00Z",
            amount={"amount": "-415.00", "currency": "USDC"},
            to={"resource": "address", "address": THAIS_DEST, "network": "solana"},
            description="Thaís",
        )
        self.assertFalse(matches_thais(cancelled))
        book = collect_usdc_sends({"transactions": [cancelled]})
        self.assertEqual(actual_for_item(book, item_name="Thaís", month=date(2026, 9, 4)), 0.0)

    def test_nicole_daily_25_dead_after_aug30(self) -> None:
        last_daily = _send(
            id="rent-25-aug30",
            created_at="2026-08-30T16:00:00Z",
            amount={"amount": "-25.00", "currency": "USDC"},
            to={"resource": "email", "email": "nvolkern@gmail.com"},
            description="",
        )
        gap = _send(
            id="rent-25-sep01",
            created_at="2026-09-01T16:00:00Z",
            amount={"amount": "-25.00", "currency": "USDC"},
            to={"resource": "email", "email": "nvolkern@gmail.com"},
            description="",
        )
        self.assertTrue(matches_rent(last_daily))
        self.assertFalse(matches_rent(gap))
        book = collect_usdc_sends({"transactions": [last_daily, gap]})
        self.assertAlmostEqual(actual_for_item(book, item_name="Rent", month=date(2026, 8, 30)), 25.0)
        self.assertEqual(actual_for_item(book, item_name="Rent", month=date(2026, 9, 1)), 0.0)

    def test_nicole_350_one_row_not_fourteen_times_25(self) -> None:
        lump = _send(
            id="rent-350-sep11",
            created_at="2026-09-11T17:00:00Z",
            amount={"amount": "-350.00", "currency": "USDC"},
            to={"resource": "email", "email": "nvolkern@gmail.com"},
            description="",
        )
        dailies = [
            _send(
                id=f"rent-25-catchup-{i}",
                created_at="2026-09-0{}T16:00:00Z".format(i) if i < 10 else "2026-09-10T16:00:00Z",
                amount={"amount": "-25.00", "currency": "USDC"},
                to={"resource": "email", "email": "nvolkern@gmail.com"},
            )
            for i in range(1, 11)
        ]
        self.assertTrue(matches_rent(lump))
        for tx in dailies:
            self.assertFalse(matches_rent(tx), tx["id"])
        book = collect_usdc_sends({"transactions": [lump, *dailies]})
        hits = [t for t in book if matches_rent(t)]
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(send_spend_amount(hits[0]), 350.0)
        self.assertAlmostEqual(actual_for_item(book, item_name="Rent", month=date(2026, 9, 11)), 350.0)
        self.assertEqual(sum(1 for t in dailies if matches_rent(t)), 0)

    def test_jr_self_send_70_not_rent_or_thais(self) -> None:
        jr = _send(
            id="jr-70-sep11",
            created_at="2026-09-11T17:00:00Z",
            amount={"amount": "-70.00", "currency": "USDC"},
            to={"resource": "address", "address": JR_SELF_SEND_DEST, "network": "solana"},
            description="",
        )
        dest_test = _send(
            id="7b8bf83b-dest-test",
            created_at="2026-08-30T16:00:00Z",
            amount={"amount": "-5.00", "currency": "USDC"},
            to={"resource": "address", "address": JR_SELF_SEND_DEST, "network": "solana"},
            description="JR dest test",
        )
        self.assertTrue(matches_jr_self_send(jr))
        self.assertFalse(matches_thais(jr))
        self.assertFalse(matches_rent(jr))
        self.assertFalse(matches_jr_self_send(dest_test))
        self.assertFalse(matches_thais(dest_test))
        self.assertFalse(matches_rent(dest_test))
        book = collect_usdc_sends({"transactions": [jr, dest_test]})
        self.assertAlmostEqual(
            actual_for_item(book, item_name="JR self-send", month=date(2026, 9, 11)),
            70.0,
        )
        self.assertEqual(actual_for_item(book, item_name="Rent", month=date(2026, 9, 11)), 0.0)
        self.assertEqual(actual_for_item(book, item_name="Thaís", month=date(2026, 9, 11)), 0.0)

    def test_next_standing_before_sep11_is_first_pay_friday(self) -> None:
        nxt = next_pay_friday("2026-08-31")
        self.assertEqual(nxt, PAY_FRIDAY_FIRST)
        self.assertEqual(nxt.hour, 13)
        rows = next_standing_sends("2026-08-31")
        by_kind = {r["kind"]: r for r in rows}
        self.assertEqual(by_kind["thais"]["amount"], 415.0)
        self.assertEqual(by_kind["thais"]["dest"], THAIS_DEST)
        self.assertEqual(by_kind["thais"]["network"], "solana")
        self.assertEqual(by_kind["rent"]["amount"], 350.0)
        self.assertEqual(by_kind["rent"]["dest"], RENT_DEST_EMAIL)
        self.assertEqual(by_kind["jr_self_send"]["amount"], 70.0)
        self.assertEqual(by_kind["jr_self_send"]["dest"], JR_SELF_SEND_DEST)
        self.assertEqual(by_kind["jr_self_send"]["label"], "self-send")
        self.assertEqual(by_kind["thais"]["next_date"], "2026-09-11")
        later = next_pay_friday(datetime(2026, 9, 11, 14, 0, tzinfo=PAY_FRIDAY_FIRST.tzinfo))
        self.assertEqual(later.date(), date(2026, 9, 25))


class TestAugustThaisDestMatcher432(unittest.TestCase):
    """#432: dest rewrite must not blank August dest-only 895."""

    def test_aug10_dest_only_895_joins(self) -> None:
        dest_only = _send(
            id="aug10-thais-895-dest",
            created_at="2026-08-10T14:00:00Z",
            amount={"amount": "-895.00", "currency": "USDC"},
            to={"resource": "address", "address": THAIS_DEST, "network": "solana"},
            description="",
        )
        named = _send(id="aug10-thais-895-named")
        self.assertAlmostEqual(THAIS_PRE_STANDING_AMOUNT, 895.0)
        self.assertTrue(matches_thais(dest_only))
        self.assertTrue(matches_thais(named))
        self.assertFalse(matches_rent(dest_only))
        self.assertFalse(matches_jr_self_send(dest_only))
        book = collect_usdc_sends({"transactions": [dest_only]})
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 8, 10)),
            895.0,
        )

    def test_dest_415_isolated_to_sep11(self) -> None:
        early_415 = _send(
            id="thais-415-aug10",
            created_at="2026-08-10T17:00:00Z",
            amount={"amount": "-415.00", "currency": "USDC"},
            to={"resource": "address", "address": THAIS_DEST, "network": "solana"},
            description="",
        )
        standing = _send(
            id="thais-415-sep11",
            created_at="2026-09-11T17:00:00Z",
            amount={"amount": "-415.00", "currency": "USDC"},
            to={"resource": "address", "address": THAIS_DEST, "network": "solana"},
            description="",
        )
        dest_895 = _send(
            id="aug10-thais-895-dest",
            created_at="2026-08-10T14:00:00Z",
            amount={"amount": "-895.00", "currency": "USDC"},
            to={"resource": "address", "address": THAIS_DEST},
            description="",
        )
        self.assertFalse(matches_thais(early_415))
        self.assertTrue(matches_thais(standing))
        self.assertTrue(matches_thais(dest_895))
        book = collect_usdc_sends({"transactions": [early_415, standing, dest_895]})
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 8, 10)),
            895.0,
        )
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 9, 11)),
            415.0,
        )

    def test_baa3976e_excluded_on_standing_dest(self) -> None:
        proof = _send(
            id="baa3976e-3304-53f7-b168-e35f16325653",
            created_at="2026-08-27T16:00:00Z",
            amount={"amount": "-1.239144", "currency": "USDC"},
            to={"resource": "address", "address": THAIS_DEST, "network": "solana"},
            description="Thais proof",
        )
        dest_895 = _send(
            id="aug10-thais-895-dest",
            created_at="2026-08-10T14:00:00Z",
            amount={"amount": "-895.00", "currency": "USDC"},
            to={"resource": "address", "address": THAIS_DEST},
            description="",
        )
        self.assertTrue(is_excluded_send_id(proof))
        self.assertFalse(matches_thais(proof))
        book = collect_usdc_sends({"transactions": [proof, dest_895]})
        self.assertAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 8, 27)),
            895.0,
        )
        self.assertNotAlmostEqual(
            actual_for_item(book, item_name="Thaís", month=date(2026, 8, 27)),
            896.24,
        )


if __name__ == "__main__":
    unittest.main()
