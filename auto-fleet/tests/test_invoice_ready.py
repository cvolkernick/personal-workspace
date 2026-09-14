"""Invoice-ready mail → Turso rows. No Google Tasks."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import invoice_ready  # noqa: E402
import turo_gmail  # noqa: E402
import turo_inbox  # noqa: E402


class ClassifyInvoiceReadyTests(unittest.TestCase):
    def test_time_to_invoice_subject(self) -> None:
        self.assertEqual(
            turo_inbox.classify_subject("Time to invoice Alex for their trip"),
            "invoice_ready",
        )

    def test_payout_is_not_invoice_ready(self) -> None:
        self.assertEqual(
            turo_inbox.classify_subject("Your Turo payout is on the way"),
            "payout",
        )

    def test_booked_is_not_invoice_ready(self) -> None:
        self.assertEqual(
            turo_inbox.classify_subject("New trip booked — Mike's vehicle"),
            "booked",
        )


class UpsertFromMailTests(unittest.TestCase):
    def test_upserts_invoice_mail_skips_bookings(self) -> None:
        store = invoice_ready.MemoryStore()
        messages = [
            {
                "id": "msg-inv",
                "subject": "Time to invoice Jordan for extra charges",
                "date": "Thu, 10 Sep 2026 12:00:00 -0400",
                "snippet": "Trip #60615645",
            },
            {
                "id": "msg-book",
                "subject": "New trip booked — Mike's vehicle",
                "date": "Thu, 10 Sep 2026 11:00:00 -0400",
                "snippet": "Toyota Corolla 2024",
            },
        ]
        result = invoice_ready.upsert_from_messages(messages, store=store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["upserted"], 1)
        listed = invoice_ready.list_open(store=store)
        self.assertEqual(len(listed["items"]), 1)
        row = listed["items"][0]
        self.assertEqual(row["id"], "msg-inv")
        self.assertEqual(row["subject"], "Time to invoice Jordan for extra charges")
        self.assertEqual(row["source_email_ref"], "msg-inv")
        self.assertEqual(row["status"], "open")
        self.assertTrue(row["received_at"])

    def test_complete_hides_row(self) -> None:
        store = invoice_ready.MemoryStore()
        invoice_ready.upsert_from_messages(
            [
                {
                    "id": "msg-inv",
                    "subject": "Invoice your guest for cleaning",
                    "date": "2026-09-10T16:00:00+00:00",
                }
            ],
            store=store,
        )
        done = invoice_ready.complete("msg-inv", store=store)
        self.assertTrue(done["ok"])
        listed = invoice_ready.list_open(store=store)
        self.assertEqual(listed["items"], [])

    def test_upsert_does_not_reopen_completed(self) -> None:
        store = invoice_ready.MemoryStore()
        msg = {
            "id": "msg-inv",
            "subject": "Time to invoice Sam",
            "date": "2026-09-10T16:00:00+00:00",
        }
        invoice_ready.upsert_from_messages([msg], store=store)
        invoice_ready.complete("msg-inv", store=store)
        invoice_ready.upsert_from_messages([msg], store=store)
        listed = invoice_ready.list_open(store=store)
        self.assertEqual(listed["items"], [])

    def test_gmail_writer_swallows_turso_errors(self) -> None:
        with mock.patch.object(
            turo_gmail.invoice_ready,
            "upsert_from_messages",
            side_effect=RuntimeError("turso down"),
        ):
            result = turo_gmail.sync_invoice_ready(
                [{"id": "x", "subject": "Time to invoice"}]
            )
        self.assertFalse(result["ok"])
        self.assertIn("turso down", result.get("error") or "")


if __name__ == "__main__":
    unittest.main()
