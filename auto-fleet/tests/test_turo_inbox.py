"""Turo fixture + maildir parser. No network."""

from __future__ import annotations

import mailbox
import sys
import tempfile
import unittest
import json
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
        self.assertEqual(payload["payout_destination"], "X Money")
        self.assertIn("X Money", payload["inbox_status"])
        self.assertIn("historical", payload["inbox_status"])

    def test_inbox_status_names_x_money_not_live_mercury(self) -> None:
        empty = PKG / "data" / "turo_inbox.json"
        payload = turo_inbox.turo_payload(inbox_path=empty, units=ROSTER_UNITS)
        self.assertEqual(payload["payout_destination"], "X Money")
        self.assertIn("X Money", payload["inbox_status"])
        self.assertIn("historical", payload["inbox_status"].lower())
        self.assertIn("Mercury ACH is historical", payload["inbox_status"])
        unit = turo_inbox.turo_for_unit("m3-2022", payload)
        self.assertEqual(unit["payout_destination"], "X Money")
        self.assertIn("X Money", unit["inbox_status"])

    def test_empty_list_fixture_invents_nothing(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_empty.json", units=ROSTER_UNITS
        )
        self.assertEqual(payload["bookings"], [])
        self.assertIn("empty", payload["inbox_status"].lower())

    def test_gmail_empty_dump_watches_inbox_invents_nothing(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_gmail_empty.json", units=ROSTER_UNITS
        )
        self.assertEqual(payload["bookings"], [])
        self.assertEqual(payload["inbox_state"], "empty")
        self.assertIn("cvolkern@gmail.com", payload["inbox_status"])
        self.assertIn("0 trip events", payload["inbox_status"])
        self.assertNotIn("host mail is not forwarded", payload["inbox_status"].lower())

    def test_real_turo_booked_shape_maps_corolla(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_real_booked_corolla.json",
            units=ROSTER_UNITS,
        )
        self.assertEqual(len(payload["bookings"]), 1)
        rec = payload["bookings"][0]
        self.assertEqual(rec["status"], "booked")
        self.assertEqual(rec["unit_id"], "corolla-2022")
        self.assertEqual(rec["guest"], "Alex Rivera")
        self.assertEqual(rec["trip_id"], "88421001")
        self.assertEqual(rec["start"], "2026-09-01")
        self.assertEqual(rec["end"], "2026-09-04")

    def test_old_fleet_kia_stays_unmatched_guest_chat_skipped(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_old_fleet_kia.json", units=ROSTER_UNITS
        )
        kinds = {b["status"] for b in payload["bookings"]}
        self.assertIn("booked", kinds)
        self.assertIn("payout", kinds)
        self.assertNotIn("other", kinds)
        booked = next(b for b in payload["bookings"] if b["status"] == "booked")
        self.assertIsNone(booked.get("unit_id"))
        self.assertIn(booked, payload["unmatched"])
        self.assertEqual(booked["trip_id"], "32786339")
        self.assertEqual(booked["start"], "2024-07-17")
        self.assertEqual(booked["end"], "2024-07-21")
        payout = next(b for b in payload["bookings"] if b["status"] == "payout")
        self.assertEqual(payout["payout"], 151.47)
        self.assertEqual(payout["unit_id"], "m3-2022")
        # Guest-chat subject must not become a booking even if body says Booked trip
        subjects = {b["subject"] for b in payload["bookings"]}
        self.assertTrue(all("sent you a message" not in s.lower() for s in subjects))

    def test_resolve_prefers_env_then_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            data.mkdir()
            shipped = data / "turo_inbox.json"
            shipped.write_text("[]", encoding="utf-8")
            env_file = Path(td) / "env-inbox.json"
            env_file.write_text("[]", encoding="utf-8")
            found = turo_inbox.resolve_inbox_path(
                None, data, env={"AUTO_FLEET_TURO_INBOX": str(env_file)}
            )
            self.assertEqual(found, env_file)
            explicit = Path(td) / "explicit.json"
            found2 = turo_inbox.resolve_inbox_path(
                explicit, data, env={"AUTO_FLEET_TURO_INBOX": str(env_file)}
            )
            self.assertEqual(found2, explicit)

    def test_gmail_writer_roundtrip(self) -> None:
        import turo_gmail

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "dump.json"
            path = turo_gmail.write_dump([], dest, inbox="cvolkern@gmail.com")
            self.assertEqual(path, dest)
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(data["messages"], [])
            self.assertEqual(data["inbox"], "cvolkern@gmail.com")
            self.assertEqual(data["source"], "gmail_dump")
            payload = turo_inbox.turo_payload(inbox_path=dest, units=ROSTER_UNITS)
            self.assertEqual(payload["bookings"], [])
            self.assertIn("cvolkern@gmail.com", payload["inbox_status"])

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
