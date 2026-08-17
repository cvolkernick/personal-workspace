"""Turo fixture + maildir parser. No network."""

from __future__ import annotations

import mailbox
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import turo_inbox  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROSTER_UNITS = [
    {
        "id": "m3-2022",
        "year": 2022,
        "make": "Tesla",
        "model": "Model 3",
        "vin": "5YJ3E1EA6NF289917",
    },
    {
        "id": "corolla-2022",
        "year": 2022,
        "make": "Toyota",
        "model": "Corolla",
        "vin": "5YFVPMAE9NP362974",
    },
    {
        "id": "m3-2020",
        "year": 2020,
        "make": "Tesla",
        "model": "Model 3",
        "vin": None,
    },
]


class TuroInboxTests(unittest.TestCase):
    def test_empty_default_fixture_invents_nothing(self) -> None:
        empty = PKG / "data" / "turo_inbox.json"
        payload = turo_inbox.turo_payload(inbox_path=empty, units=ROSTER_UNITS)
        self.assertEqual(payload["bookings"], [])
        self.assertEqual(payload["unmatched"], [])
        self.assertIn("empty", payload["inbox_status"].lower())
        for uid in ("m3-2022", "corolla-2022", "m3-2020"):
            unit = turo_inbox.turo_for_unit(uid, payload)
            self.assertEqual(unit["bookings"], [])
            self.assertEqual(unit["inbox_status"], payload["inbox_status"])

    def test_missing_path_explains_empty(self) -> None:
        missing = Path("/tmp/auto-fleet-does-not-exist-inbox.json")
        payload = turo_inbox.turo_payload(inbox_path=missing, units=ROSTER_UNITS)
        self.assertEqual(payload["bookings"], [])
        self.assertIn("no host inbox", payload["inbox_status"].lower())

    def test_json_fixture_parses_booked_and_canceled(self) -> None:
        path = FIXTURES / "turo_messages.json"
        payload = turo_inbox.turo_payload(inbox_path=path, units=ROSTER_UNITS)
        self.assertEqual(len(payload["bookings"]), 2)
        by_id = {b["unit_id"]: b for b in payload["bookings"]}
        self.assertEqual(by_id["corolla-2022"]["status"], "booked")
        self.assertEqual(by_id["corolla-2022"]["guest"], "Alex Rivera")
        self.assertEqual(by_id["corolla-2022"]["trip_id"], "TR-88421")
        self.assertEqual(by_id["corolla-2022"]["start"], "2026-09-01")
        self.assertEqual(by_id["m3-2022"]["status"], "canceled")
        # Marketing blast is not a booking
        self.assertTrue(all(b["status"] != "other" for b in payload["bookings"]))
        corolla = turo_inbox.turo_for_unit("corolla-2022", payload)
        self.assertEqual(len(corolla["bookings"]), 1)
        personal = turo_inbox.turo_for_unit("m3-2020", payload)
        self.assertEqual(personal["bookings"], [])

    def test_bad_json_is_parse_error_not_bookings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "broken.json"
            bad.write_text("{not json", encoding="utf-8")
            payload = turo_inbox.turo_payload(inbox_path=bad, units=ROSTER_UNITS)
            self.assertEqual(payload["bookings"], [])
            self.assertTrue(payload["inbox_status"].startswith("parse error"))

    def test_payout_fixture(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_payout.json", units=ROSTER_UNITS
        )
        self.assertEqual(len(payload["bookings"]), 1)
        self.assertEqual(payload["bookings"][0]["status"], "payout")
        self.assertEqual(payload["bookings"][0]["payout"], 214.5)
        self.assertEqual(payload["bookings"][0]["unit_id"], "m3-2022")

    def test_empty_list_fixture_invents_nothing(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_empty.json", units=ROSTER_UNITS
        )
        self.assertEqual(payload["bookings"], [])
        self.assertIn("empty", payload["inbox_status"].lower())

    def test_maildir_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "maildir"
            box = mailbox.Maildir(str(root), create=True)
            msg = EmailMessage()
            msg["From"] = "noreply@transactional.turo.com"
            msg["Subject"] = "New trip booked"
            msg["Message-ID"] = "<maildir-1@test>"
            msg.set_content(
                "Guest: Pat Kim\nTrip ID: TR-55\n"
                "2022 Toyota Corolla VIN 5YFVPMAE9NP362974\n"
                "2026-10-01 to 2026-10-03\nPickup: driveway"
            )
            box.add(msg)
            box.close()
            payload = turo_inbox.turo_payload(inbox_path=root, units=ROSTER_UNITS)
            self.assertEqual(payload["inbox_kind"], "maildir")
            self.assertEqual(len(payload["bookings"]), 1)
            self.assertEqual(payload["bookings"][0]["unit_id"], "corolla-2022")
            self.assertEqual(payload["bookings"][0]["guest"], "Pat Kim")


if __name__ == "__main__":
    unittest.main()
