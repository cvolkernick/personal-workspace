"""Turo fixture + maildir parser. No network."""

from __future__ import annotations

import hashlib
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
        "role": "turo",
        "vin": "5YJ3E1EA6NF289917",
    },
    {
        "id": "corolla-2022",
        "year": 2022,
        "make": "Toyota",
        "model": "Corolla",
        "role": "turo",
        "vin": "5YFVPMAE9NP362974",
    },
    {
        "id": "m3-2020",
        "year": 2020,
        "make": "Tesla",
        "model": "Model 3",
        "role": "personal",
        "vin": None,
    },
    {
        "id": "corolla-2024",
        "year": 2024,
        "make": "Toyota",
        "model": "Corolla",
        "role": "turo",
        "vin": "5YFB4MDE9RP121896",
    },
    {
        "id": "r1s-2023",
        "year": 2023,
        "make": "Rivian",
        "model": "R1S",
        "role": "turo",
        "vin": "7PDSGABA3PN028624",
    },
]


def _with_roles(units: list[dict]) -> list[dict]:
    """Copy of the shipped roster roles used by host-scope tests."""
    roles = {
        "m3-2020": "personal",
        "m3-2022": "turo",
        "corolla-2022": "turo",
        "corolla-2024": "turo",
        "r1s-2023": "turo",
    }
    out = []
    for u in units:
        rec = dict(u)
        rec["role"] = roles.get(str(u["id"]), rec.get("role"))
        out.append(rec)
    return out


class TuroInboxTests(unittest.TestCase):
    def test_labeled_inbox_is_panamerica_not_personal(self) -> None:
        self.assertEqual(turo_inbox.GMAIL_INBOX_ADDR, "panamerica.cars@gmail.com")
        self.assertNotEqual(turo_inbox.GMAIL_INBOX_ADDR, "cvolkern@gmail.com")
        self.assertIn("after:2026/08/18", turo_inbox.GMAIL_QUERY)
        self.assertNotIn("label:Turo", turo_inbox.GMAIL_QUERY)

    def test_empty_default_fixture_invents_nothing(self) -> None:
        empty = PKG / "data" / "turo_inbox.json"
        payload = turo_inbox.turo_payload(inbox_path=empty, units=ROSTER_UNITS)
        self.assertEqual(payload["bookings"], [])
        self.assertEqual(payload["unmatched"], [])
        self.assertEqual(payload["photo_messages"], [])
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
        self.assertIn("panamerica.cars@gmail.com", payload["inbox_status"])
        self.assertNotIn("cvolkern@gmail.com", payload["inbox_status"])

    def test_json_fixture_parses_booked_and_canceled(self) -> None:
        path = FIXTURES / "turo_messages.json"
        payload = turo_inbox.turo_payload(
            inbox_path=path, units=ROSTER_UNITS, since=False
        )
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
            inbox_path=FIXTURES / "turo_payout.json",
            units=ROSTER_UNITS,
            since=False,
        )
        self.assertEqual(len(payload["bookings"]), 1)
        self.assertEqual(payload["bookings"][0]["status"], "payout")
        self.assertEqual(payload["bookings"][0]["payout"], 214.5)
        self.assertEqual(payload["bookings"][0]["unit_id"], "m3-2022")
        self.assertEqual(payload["payout_destination"], "X Money")
        self.assertIn("X Money", payload["inbox_status"])
        self.assertNotIn("Mercury", payload["inbox_status"])

    def test_inbox_status_names_x_money_no_mercury(self) -> None:
        empty = PKG / "data" / "turo_inbox.json"
        payload = turo_inbox.turo_payload(inbox_path=empty, units=ROSTER_UNITS)
        self.assertEqual(payload["payout_destination"], "X Money")
        self.assertIn("X Money", payload["inbox_status"])
        self.assertNotIn("Mercury", payload["inbox_status"])
        self.assertNotIn("ACH", payload["inbox_status"])
        self.assertIn("every 15m", payload["inbox_status"])
        self.assertIn("2026-08-18", payload["inbox_status"])
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
        self.assertIn("panamerica.cars@gmail.com", payload["inbox_status"])
        self.assertNotIn("cvolkern@gmail.com", payload["inbox_status"])
        self.assertIn("0 trip events", payload["inbox_status"])
        self.assertNotIn("host mail is not forwarded", payload["inbox_status"].lower())

    def test_real_turo_booked_shape_maps_corolla(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_real_booked_corolla.json",
            units=ROSTER_UNITS,
            since=False,
        )
        self.assertEqual(len(payload["bookings"]), 1)
        rec = payload["bookings"][0]
        self.assertEqual(rec["status"], "booked")
        self.assertEqual(rec["unit_id"], "corolla-2022")
        self.assertEqual(rec["guest"], "Alex Rivera")
        self.assertEqual(rec["trip_id"], "88421001")
        self.assertEqual(rec["start"], "2026-09-01T18:00:00-04:00")
        self.assertEqual(rec["end"], "2026-09-04T18:00:00-04:00")

    def test_old_fleet_kia_stays_unmatched_guest_chat_skipped(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_old_fleet_kia.json",
            units=ROSTER_UNITS,
            since=False,
        )
        kinds = {b["status"] for b in payload["bookings"]}
        self.assertNotIn("booked", kinds)
        self.assertIn("payout", kinds)
        self.assertNotIn("other", kinds)
        payout = next(b for b in payload["bookings"] if b["status"] == "payout")
        self.assertEqual(payout["payout"], 151.47)
        self.assertEqual(payout["unit_id"], "m3-2022")
        # Jessica host listing is always dropped, even with cutoff off.
        subjects = {b["subject"] for b in payload["bookings"]}
        self.assertTrue(all("jessica" not in s.lower() for s in subjects))
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
            path = turo_gmail.write_dump([], dest)
            self.assertEqual(path, dest)
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(data["messages"], [])
            self.assertEqual(data["inbox"], "panamerica.cars@gmail.com")
            self.assertEqual(data["source"], "gmail_dump")
            payload = turo_inbox.turo_payload(inbox_path=dest, units=ROSTER_UNITS)
            self.assertEqual(payload["bookings"], [])
            self.assertIn("panamerica.cars@gmail.com", payload["inbox_status"])
            self.assertNotIn("cvolkern@gmail.com", payload["inbox_status"])

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
            payload = turo_inbox.turo_payload(
                inbox_path=root, units=ROSTER_UNITS, since=False
            )
            self.assertEqual(payload["inbox_kind"], "maildir")
            self.assertEqual(len(payload["bookings"]), 1)
            self.assertEqual(payload["bookings"][0]["unit_id"], "corolla-2022")
            self.assertEqual(payload["bookings"][0]["guest"], "Pat Kim")

    def test_default_cutoff_drops_historical_kia(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_old_fleet_kia.json", units=ROSTER_UNITS
        )
        self.assertEqual(payload["bookings"], [])
        self.assertEqual(payload["unmatched"], [])
        self.assertIn("0 trip events", payload["inbox_status"])
        self.assertIn("historical dropped", payload["inbox_status"])

    def test_pre_cutoff_fixture_dates_are_dropped(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_messages.json", units=ROSTER_UNITS
        )
        self.assertEqual(payload["bookings"], [])

    def test_mikes_vehicle_after_cutoff_maps_m3(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_mikes_vehicle.json", units=ROSTER_UNITS
        )
        self.assertEqual(len(payload["bookings"]), 1)
        rec = payload["bookings"][0]
        self.assertEqual(rec["status"], "booked")
        self.assertEqual(rec["unit_id"], "m3-2022")
        self.assertEqual(rec["trip_id"], "99112233")
        self.assertEqual(rec["guest"], "Alex Rivera")
        self.assertTrue(turo_inbox.is_current_host_subject(rec["subject"]))
        self.assertEqual(payload["unmatched"], [])

    def test_gmail_writer_records_forward_window(self) -> None:
        import turo_gmail

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "dump.json"
            turo_gmail.write_dump([], dest)
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(data["inbox"], "panamerica.cars@gmail.com")
            self.assertEqual(data["forward_since"], turo_inbox.FORWARD_SINCE_ISO)
            self.assertEqual(data["poll_interval_s"], 900)
            self.assertIn("after:2026/08/18", data["query"])
            self.assertNotIn("label:Turo", data["query"])

    def test_fetch_without_creds_writes_honest_empty(self) -> None:
        import turo_gmail

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "dump.json"
            missing = Path(td) / "no-token.json"
            env_file = Path(td) / "empty.env"
            env_file.write_text("# no gmail keys\n", encoding="utf-8")
            path = turo_gmail.fetch_and_write(
                dest,
                token_path=missing,
                env_file=env_file,
                env={},
            )
            self.assertEqual(path, dest)
            rc = turo_gmail.main(
                [
                    "--fetch",
                    "--out",
                    str(dest),
                    "--token",
                    str(missing),
                    "--env-file",
                    str(env_file),
                ]
            )
            self.assertEqual(rc, 0)
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(data["messages"], [])
            self.assertEqual(data["source"], "gmail_unconfigured")
            self.assertIn("after:2026/08/18", data["query"])
            self.assertNotIn("label:Turo", data["query"])
            self.assertIn("no Gmail refresh token", data["note"])
            payload = turo_inbox.turo_payload(inbox_path=dest, units=ROSTER_UNITS)
            self.assertEqual(payload["bookings"], [])
            self.assertEqual(data["inbox"], "panamerica.cars@gmail.com")
            self.assertIn("panamerica.cars@gmail.com", payload["inbox_status"])
            self.assertNotIn("cvolkern@gmail.com", payload["inbox_status"])
            self.assertIn("0 trip events", payload["inbox_status"])

    def test_fetch_with_mocked_gmail_writes_api_source(self) -> None:
        import turo_gmail

        calls: list[str] = []

        def fake_http(url: str, data, headers):
            calls.append(url)
            if "oauth2.googleapis.com/token" in url:
                return {"access_token": "tok-test"}
            if url.startswith(turo_gmail.GMAIL_API + "/messages?") and "q=" in url:
                return {"messages": [{"id": "m1"}]}
            if "/messages/m1" in url:
                return {
                    "id": "m1",
                    "snippet": "booked",
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "From", "value": "Turo <noreply@mail.turo.com>"},
                            {
                                "name": "Subject",
                                "value": "(Mike's vehicle) - Pat's trip is booked!",
                            },
                            {"name": "Date", "value": "Tue, 18 Aug 2026 14:10:00 +0000"},
                        ],
                        "body": {
                            "data": turo_gmail.base64.urlsafe_b64encode(
                                b"Reservation ID #42\n2022 Tesla Model 3\n"
                            ).decode("ascii")
                        },
                    },
                }
            raise AssertionError(f"unexpected url {url}")

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "dump.json"
            token = Path(td) / "gmail-token.json"
            token.write_text(
                json.dumps(
                    {
                        "refresh_token": "r",
                        "client_id": "cid",
                        "client_secret": "sec",
                    }
                ),
                encoding="utf-8",
            )
            path = turo_gmail.fetch_and_write(
                dest,
                token_path=token,
                env_file=Path(td) / "missing.env",
                env={},
                http=fake_http,
            )
            self.assertEqual(path, dest)
            data = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(data["source"], "gmail_api")
            self.assertEqual(len(data["messages"]), 1)
            self.assertEqual(data["messages"][0]["id"], "m1")
            self.assertIn("Mike's vehicle", data["messages"][0]["subject"])
            self.assertTrue(any("oauth2.googleapis.com/token" in u for u in calls))

    def test_fetch_http_error_writes_gmail_error_not_bookings(self) -> None:
        import turo_gmail

        def boom(url: str, data, headers):
            raise RuntimeError("HTTP 401 token endpoint")

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "dump.json"
            path = turo_gmail.fetch_and_write(
                dest,
                env={
                    "GMAIL_REFRESH_TOKEN": "r",
                    "GMAIL_CLIENT_ID": "cid",
                    "GMAIL_CLIENT_SECRET": "sec",
                },
                token_path=Path(td) / "missing.json",
                env_file=Path(td) / "missing.env",
                http=boom,
            )
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["source"], "gmail_error")
            self.assertEqual(data["messages"], [])
            self.assertIn("401", data["error"])
            payload = turo_inbox.turo_payload(inbox_path=path, units=ROSTER_UNITS)
            self.assertEqual(payload["bookings"], [])

    def test_body_year_maps_2024_and_2022_corollas(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_mike_corolla_body_year.json",
            units=_with_roles(ROSTER_UNITS),
        )
        matched = [b for b in payload["bookings"] if b.get("unit_id")]
        unmatched = payload["unmatched"]
        by_guest = {b.get("guest"): b for b in matched}
        self.assertEqual(by_guest["MEGAN"]["unit_id"], "corolla-2024")
        self.assertEqual(by_guest["MEGAN"]["trip_id"], "60615645")
        self.assertEqual(by_guest["MEGAN"]["status"], "canceled")
        self.assertEqual(by_guest["Myles"]["unit_id"], "corolla-2024")
        self.assertEqual(by_guest["Myles"]["trip_id"], "60463692")
        self.assertEqual(by_guest["Myles"]["pickup"], "driveway")
        self.assertEqual(by_guest["Matthew"]["unit_id"], "corolla-2024")
        self.assertEqual(by_guest["Matthew"]["status"], "modified")
        self.assertEqual(by_guest["Marie"]["unit_id"], "corolla-2022")
        self.assertEqual(by_guest["Marie"]["start"], "2026-08-23T13:00:00-04:00")
        self.assertEqual(by_guest["Marie"]["end"], "2026-08-25T13:00:00-04:00")
        self.assertEqual(by_guest["Nayive"]["unit_id"], "corolla-2022")
        self.assertEqual(by_guest["Nayive"]["trip_id"], "60220022")
        self.assertEqual(by_guest["Jeffrey"]["unit_id"], "corolla-2022")
        c24 = turo_inbox.turo_for_unit("corolla-2024", payload)
        c22 = turo_inbox.turo_for_unit("corolla-2022", payload)
        self.assertEqual({b["trip_id"] for b in c24["bookings"]}, {"60615645", "60463692", "60881200"})
        self.assertEqual({b["trip_id"] for b in c22["bookings"]}, {"60110022", "60220022", "60330022"})
        self.assertTrue(all(b.get("unit_id") != "m3-2020" for b in payload["bookings"]))
        self.assertTrue(all(b.get("unit_id") != "r1s-2023" for b in payload["bookings"]))
        self.assertEqual(len(unmatched), 1)
        self.assertIsNone(unmatched[0]["unit_id"])
        self.assertEqual(unmatched[0]["guest"], "Pat Kim")
        self.assertEqual(unmatched[0]["trip_id"], "60990000")
        self.assertNotIn("body", unmatched[0])
        megan_trips = [b for b in c24["bookings"] if b["trip_id"] == "60615645"]
        self.assertEqual({b["status"] for b in megan_trips}, {"booked", "canceled"})

    def test_trip_start_end_keep_clock_in_et(self) -> None:
        parsed = turo_inbox.parse_message(
            {
                "subject": "(Mike's vehicle) - Pat's trip with your Toyota Corolla is booked!",
                "from": "Turo <noreply@mail.turo.com>",
                "date": "Sun, 23 Aug 2026 11:45:00 +0000",
                "body": (
                    "Toyota Corolla 2024\nbooked by Pat Kim\nReservation ID #60619999\n"
                    "Trip start: 8/23/26 3:00 pm\nTrip end: 8/25/26 3:00 pm\n"
                ),
            }
        )
        self.assertEqual(parsed["start"], "2026-08-23T15:00:00-04:00")
        self.assertEqual(parsed["end"], "2026-08-25T15:00:00-04:00")

    def test_long_range_keeps_clock_not_date_only(self) -> None:
        parsed = turo_inbox.parse_message(
            {
                "subject": "Alex’s trip with your 2022 Toyota Corolla is booked!",
                "from": "Turo <noreply@mail.turo.com>",
                "date": "Mon, 17 Aug 2026 13:26:19 +0000",
                "body": (
                    "Alex’s trip with your 2022 Toyota Corolla is booked from "
                    "Monday, September 1, 2026 6:00 PM to Thursday, September 4, "
                    "2026 6:00 PM.\n2022 Toyota Corolla\nbooked by Alex Rivera\n"
                    "Reservation ID #88421001\n"
                ),
            }
        )
        self.assertEqual(parsed["start"], "2026-09-01T18:00:00-04:00")
        self.assertEqual(parsed["end"], "2026-09-04T18:00:00-04:00")

    def test_date_only_mail_stays_date_only(self) -> None:
        parsed = turo_inbox.parse_message(
            {
                "subject": "New trip booked",
                "from": "noreply@transactional.turo.com",
                "date": "2026-08-10T14:00:00+00:00",
                "body": (
                    "Guest: Alex Rivera\nTrip ID: TR-88421\n"
                    "2022 Toyota Corolla\n2026-09-01 to 2026-09-04\n"
                ),
            }
        )
        self.assertEqual(parsed["start"], "2026-09-01")
        self.assertEqual(parsed["end"], "2026-09-04")

    def test_yearless_corolla_stays_unmatched(self) -> None:
        rec = {
            "subject": "(Mike's vehicle) - Pat's trip with your Toyota Corolla is booked!",
            "from": "Turo <noreply@mail.turo.com>",
            "date": "Tue, 18 Aug 2026 14:10:00 +0000",
            "body": "Toyota Corolla\nbooked by Pat Kim\nReservation ID #60990000\n",
        }
        parsed = turo_inbox.parse_message(rec)
        self.assertIsNotNone(parsed)
        self.assertIsNone(turo_inbox.match_unit(parsed, _with_roles(ROSTER_UNITS)))

    def test_plate_is_display_only_not_a_match_key(self) -> None:
        """Year-in-mail-body stays the match key. Plate is ops metadata only."""
        plated = []
        for u in _with_roles(ROSTER_UNITS):
            rec = dict(u)
            if rec["id"] == "corolla-2022":
                rec["plate"] = "24EWUH"
            elif rec["id"] == "corolla-2024":
                rec["plate"] = "25EWUH"
            plated.append(rec)
        yearless = turo_inbox.parse_message(
            {
                "subject": "(Mike's vehicle) - Pat's trip with your Toyota Corolla is booked!",
                "from": "Turo <noreply@mail.turo.com>",
                "date": "Tue, 18 Aug 2026 14:10:00 +0000",
                "body": (
                    "Toyota Corolla\nplate 24EWUH\nbooked by Pat Kim\n"
                    "Reservation ID #60990000\n"
                ),
            }
        )
        self.assertIsNone(turo_inbox.match_unit(yearless, plated))
        year_wins = turo_inbox.parse_message(
            {
                "subject": "(Mike's vehicle) - Pat's trip with your Toyota Corolla is booked!",
                "from": "Turo <noreply@mail.turo.com>",
                "date": "Tue, 18 Aug 2026 14:10:00 +0000",
                "body": (
                    "Toyota Corolla 2024\nplate 24EWUH\nbooked by Pat Kim\n"
                    "Reservation ID #60990001\n"
                ),
            }
        )
        self.assertEqual(turo_inbox.match_unit(year_wins, plated), "corolla-2024")

    def test_guest_name_is_not_a_unit_map(self) -> None:
        """Ops hint guests still require a body year — never guest→year alone."""
        rec = {
            "subject": "(Mike's vehicle) - MEGAN's trip with your Toyota Corolla is booked!",
            "from": "Turo <noreply@mail.turo.com>",
            "date": "Tue, 18 Aug 2026 14:10:00 +0000",
            "body": "Toyota Corolla\nbooked by MEGAN\nReservation ID #60615645\n",
        }
        parsed = turo_inbox.parse_message(rec)
        self.assertEqual(parsed["guest"], "MEGAN")
        self.assertEqual(parsed["trip_id"], "60615645")
        self.assertIsNone(turo_inbox.match_unit(parsed, _with_roles(ROSTER_UNITS)))

    def test_mike_host_mail_does_not_attach_to_chris_personal(self) -> None:
        rec = {
            "subject": "(Mike's vehicle) - Alex's trip with your Tesla Model 3 is booked!",
            "from": "Turo <noreply@mail.turo.com>",
            "date": "Tue, 18 Aug 2026 14:10:00 +0000",
            "body": "2020 Tesla Model 3\nbooked by Alex Rivera\nReservation ID #77001122\n",
        }
        parsed = turo_inbox.parse_message(rec)
        self.assertIsNone(turo_inbox.match_unit(parsed, _with_roles(ROSTER_UNITS)))

    def test_other_host_mail_stays_unmatched_no_invented_unit(self) -> None:
        rec = {
            "subject": "(Alex's vehicle) - Sam's trip with your 2023 Honda Civic is booked!",
            "from": "Turo <noreply@mail.turo.com>",
            "date": "Tue, 18 Aug 2026 14:10:00 +0000",
            "body": (
                "2023 Honda Civic\nbooked by Sam Lee\nReservation ID #77889900\n"
            ),
        }
        parsed = turo_inbox.parse_message(rec)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["guest"], "Sam Lee")
        self.assertFalse(turo_inbox.is_current_host_subject(parsed["subject"]))
        units = _with_roles(ROSTER_UNITS)
        self.assertIsNone(turo_inbox.match_unit(parsed, units))
        self.assertEqual({u["id"] for u in units}, {u["id"] for u in ROSTER_UNITS})
        self.assertNotIn("civic-2023", {u["id"] for u in units})

    def test_html_body_year_and_reservation_flatten(self) -> None:
        html = (
            "<html><body><p>MEGAN&rsquo;s trip with your "
            "<b>Toyota Corolla 2024</b> is booked.</p>"
            "<p>Reservation ID #60615645</p>"
            "<p>Pickup: Punta Gorda Airport FBO</p></body></html>"
        )
        rec = {
            "subject": "(Mike's vehicle) - MEGAN's trip with your Toyota Corolla is booked!",
            "from": "Turo <noreply@mail.turo.com>",
            "date": "Tue, 18 Aug 2026 14:10:00 +0000",
            "body": html,
        }
        parsed = turo_inbox.parse_message(rec)
        self.assertEqual(parsed["trip_id"], "60615645")
        self.assertEqual(parsed["pickup"], "Punta Gorda Airport FBO")
        self.assertEqual(
            turo_inbox.match_unit(parsed, _with_roles(ROSTER_UNITS)),
            "corolla-2024",
        )

    def test_gmail_writer_keeps_html_body_year_and_snippet(self) -> None:
        import turo_gmail

        html = (
            "<p>Toyota Corolla 2024</p><p>Reservation ID #60615645</p>"
        ).encode("utf-8")

        def fake_http(url: str, data, headers):
            if "oauth2.googleapis.com/token" in url:
                return {"access_token": "tok-test"}
            if url.startswith(turo_gmail.GMAIL_API + "/messages?") and "q=" in url:
                return {"messages": [{"id": "m-html"}]}
            if "/messages/m-html" in url:
                return {
                    "id": "m-html",
                    "snippet": "Toyota Corolla 2024 Reservation ID #60615645",
                    "payload": {
                        "mimeType": "text/html",
                        "headers": [
                            {"name": "From", "value": "Turo <noreply@mail.turo.com>"},
                            {
                                "name": "Subject",
                                "value": "(Mike's vehicle) - MEGAN's trip is booked!",
                            },
                            {"name": "Date", "value": "Tue, 19 Aug 2026 14:10:00 +0000"},
                        ],
                        "body": {
                            "data": turo_gmail.base64.urlsafe_b64encode(html).decode(
                                "ascii"
                            )
                        },
                    },
                }
            raise AssertionError(f"unexpected url {url}")

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "dump.json"
            token = Path(td) / "gmail-token.json"
            token.write_text(
                json.dumps(
                    {
                        "refresh_token": "r",
                        "client_id": "cid",
                        "client_secret": "sec",
                    }
                ),
                encoding="utf-8",
            )
            turo_gmail.fetch_and_write(
                dest,
                token_path=token,
                env_file=Path(td) / "missing.env",
                env={},
                http=fake_http,
            )
            data = json.loads(dest.read_text(encoding="utf-8"))
            msg = data["messages"][0]
            self.assertIn("Toyota Corolla 2024", msg["body"])
            self.assertIn("60615645", msg["body"])
            self.assertIn("60615645", msg["snippet"])
            payload = turo_inbox.turo_payload(
                inbox_path=dest, units=_with_roles(ROSTER_UNITS)
            )
            self.assertEqual(payload["bookings"][0]["unit_id"], "corolla-2024")
            self.assertEqual(payload["bookings"][0]["trip_id"], "60615645")
            self.assertNotIn("attachments", payload["bookings"][0])
            self.assertEqual(payload["photo_messages"], [])


def _fixture_jpeg(tag: bytes = b"turo-fixture") -> bytes:
    """Minimal JPEG bytes. Not a trip photo — just real image MIME for ingest."""
    comment = tag[:60]
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + b"\xff\xfe"
        + bytes([(len(comment) + 2) >> 8, (len(comment) + 2) & 0xFF])
        + comment
        + b"\xff\xd9"
    )


class TuroInboxPhotoTests(unittest.TestCase):
    def test_claim_without_parts_is_honest_missing(self) -> None:
        payload = turo_inbox.turo_payload(
            inbox_path=FIXTURES / "turo_photo_claim_only.json",
            units=_with_roles(ROSTER_UNITS),
        )
        booked = [b for b in payload["bookings"] if b.get("status") == "booked"]
        self.assertEqual(len(booked), 1)
        self.assertNotIn("attachments", booked[0])
        self.assertFalse(booked[0].get("claims_photos"))
        photos = payload["photo_messages"]
        self.assertEqual(len(photos), 1)
        rec = photos[0]
        self.assertEqual(rec["kind"], "guest_message")
        self.assertEqual(rec["trip_id"], "60615645")
        self.assertEqual(rec["attachments"], [])
        self.assertTrue(rec["photos_missing"])
        self.assertTrue(rec["claims_photos"])
        self.assertEqual(rec["unit_id"], "corolla-2024")
        subjects = {b["subject"] for b in payload["bookings"]}
        self.assertTrue(all("sent you a message" not in s.lower() for s in subjects))

    def test_json_attachments_pass_through_on_booking(self) -> None:
        jpeg = _fixture_jpeg(b"blocked-in")
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "turo_inbox.json"
            media = Path(td) / "turo_inbox_media" / "msg-photo-1"
            media.mkdir(parents=True)
            photo = media / "blocked-in.jpg"
            photo.write_bytes(jpeg)
            dump = {
                "as_of": "2026-08-19",
                "source": "test_fixture",
                "messages": [
                    {
                        "id": "msg-photo-1",
                        "from": "Turo <noreply@mail.turo.com>",
                        "subject": "(Mike's vehicle) - Pat's trip with your Toyota Corolla is booked!",
                        "date": "Tue, 19 Aug 2026 15:10:00 +0000",
                        "body": (
                            "Toyota Corolla 2024\nbooked by Pat Kim\n"
                            "Reservation ID #60619999\n"
                        ),
                        "attachments": [
                            {
                                "filename": "blocked-in.jpg",
                                "mime": "image/jpeg",
                                "size": len(jpeg),
                                "sha256": hashlib.sha256(jpeg).hexdigest(),
                                "path": str(photo),
                                "relpath": "msg-photo-1/blocked-in.jpg",
                            }
                        ],
                    }
                ],
            }
            dest.write_text(json.dumps(dump), encoding="utf-8")
            payload = turo_inbox.turo_payload(
                inbox_path=dest, units=_with_roles(ROSTER_UNITS)
            )
            rec = payload["bookings"][0]
            self.assertEqual(rec["unit_id"], "corolla-2024")
            self.assertEqual(len(rec["attachments"]), 1)
            self.assertEqual(rec["attachments"][0]["filename"], "blocked-in.jpg")
            self.assertEqual(rec["attachments"][0]["path"], str(photo))
            self.assertTrue(photo.is_file())
            self.assertEqual(photo.read_bytes(), jpeg)
            unit = turo_inbox.turo_for_unit("corolla-2024", payload)
            self.assertEqual(len(unit["photos"]), 1)
            self.assertEqual(unit["photos"][0]["attachments"][0]["relpath"], "msg-photo-1/blocked-in.jpg")

    def test_guest_mms_merges_onto_matching_trip(self) -> None:
        jpeg = _fixture_jpeg(b"fuel-gauge")
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "turo_inbox.json"
            photo = Path(td) / "turo_inbox_media" / "guest-1" / "fuel.jpg"
            photo.parent.mkdir(parents=True)
            photo.write_bytes(jpeg)
            dump = {
                "messages": [
                    {
                        "id": "booked-1",
                        "from": "Turo <noreply@mail.turo.com>",
                        "subject": "(Mike's vehicle) - Pat's trip with your Toyota Corolla is booked!",
                        "date": "Tue, 19 Aug 2026 15:10:00 +0000",
                        "body": (
                            "Toyota Corolla 2024\nbooked by Pat Kim\n"
                            "Reservation ID #60619999\n"
                        ),
                    },
                    {
                        "id": "guest-1",
                        "from": "Turo <noreply@mail.turo.com>",
                        "subject": "Pat has sent you a message about your Toyota Corolla 2024",
                        "date": "Wed, 20 Aug 2026 12:00:00 +0000",
                        "body": "Contains photo(s).\nReservation ID #60619999\nToyota Corolla 2024\n",
                        "attachments": [
                            {
                                "filename": "fuel.jpg",
                                "mime": "image/jpeg",
                                "size": len(jpeg),
                                "sha256": hashlib.sha256(jpeg).hexdigest(),
                                "path": str(photo),
                                "relpath": "guest-1/fuel.jpg",
                            }
                        ],
                    },
                ]
            }
            dest.write_text(json.dumps(dump), encoding="utf-8")
            payload = turo_inbox.turo_payload(
                inbox_path=dest, units=_with_roles(ROSTER_UNITS)
            )
            booked = [b for b in payload["bookings"] if b.get("status") == "booked"]
            self.assertEqual(len(booked), 1)
            self.assertEqual(booked[0]["attachments"][0]["filename"], "fuel.jpg")
            guest = payload["photo_messages"]
            self.assertEqual(len(guest), 1)
            self.assertEqual(guest[0]["kind"], "guest_message")
            self.assertFalse(guest[0].get("photos_missing"))
            subjects = {b["subject"] for b in payload["bookings"]}
            self.assertTrue(all("sent you a message" not in s.lower() for s in subjects))

    def test_eml_image_part_is_written(self) -> None:
        jpeg = _fixture_jpeg(b"damage")
        with tempfile.TemporaryDirectory() as td:
            eml = Path(td) / "guest.eml"
            msg = EmailMessage()
            msg["From"] = "Turo <noreply@mail.turo.com>"
            msg["Subject"] = "Pat has sent you a message about your Toyota Corolla 2024"
            msg["Date"] = "Wed, 20 Aug 2026 12:00:00 +0000"
            msg["Message-ID"] = "<eml-photo-1@turo.com>"
            msg.set_content(
                "Contains photo(s).\nReservation ID #60619999\nToyota Corolla 2024\n"
            )
            msg.add_attachment(
                jpeg, maintype="image", subtype="jpeg", filename="damage.jpg"
            )
            eml.write_bytes(msg.as_bytes())
            payload = turo_inbox.turo_payload(
                inbox_path=eml, units=_with_roles(ROSTER_UNITS)
            )
            self.assertEqual(payload["bookings"], [])
            self.assertEqual(len(payload["photo_messages"]), 1)
            att = payload["photo_messages"][0]["attachments"][0]
            self.assertEqual(att["filename"], "damage.jpg")
            self.assertEqual(att["mime"], "image/jpeg")
            stored = Path(att["path"])
            self.assertTrue(stored.is_file())
            self.assertEqual(stored.read_bytes(), jpeg)
            self.assertTrue(att["relpath"].endswith("damage.jpg"))

    def test_gmail_multipart_writes_inline_jpeg_skips_logo(self) -> None:
        import turo_gmail

        jpeg = _fixture_jpeg(b"mms-inline")
        logo = b"\x89PNG\r\n\x1a\n" + b"logo-bytes-not-a-trip-photo"

        def fake_http(url: str, data, headers):
            if "oauth2.googleapis.com/token" in url:
                return {"access_token": "tok-test"}
            if url.startswith(turo_gmail.GMAIL_API + "/messages?") and "q=" in url:
                return {"messages": [{"id": "m-mms"}]}
            if "/messages/m-mms" in url and "/attachments/" not in url:
                return {
                    "id": "m-mms",
                    "snippet": "Contains photo(s). Reservation ID #60619999",
                    "payload": {
                        "mimeType": "multipart/mixed",
                        "headers": [
                            {"name": "From", "value": "Turo <noreply@mail.turo.com>"},
                            {
                                "name": "Subject",
                                "value": "Pat has sent you a message about your Toyota Corolla 2024",
                            },
                            {"name": "Date", "value": "Wed, 20 Aug 2026 12:00:00 +0000"},
                        ],
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {
                                    "data": turo_gmail.base64.urlsafe_b64encode(
                                        b"Contains photo(s).\nReservation ID #60619999\n"
                                        b"Toyota Corolla 2024\n"
                                    ).decode("ascii")
                                },
                            },
                            {
                                "mimeType": "image/jpeg",
                                "filename": "IMG_blocked_in.jpg",
                                "body": {
                                    "data": turo_gmail.base64.urlsafe_b64encode(
                                        jpeg
                                    ).decode("ascii")
                                },
                            },
                            {
                                "mimeType": "image/png",
                                "filename": "turo-logo.png",
                                "body": {
                                    "data": turo_gmail.base64.urlsafe_b64encode(
                                        logo
                                    ).decode("ascii")
                                },
                            },
                        ],
                    },
                }
            raise AssertionError(f"unexpected url {url}")

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "dump.json"
            token = Path(td) / "gmail-token.json"
            token.write_text(
                json.dumps(
                    {"refresh_token": "r", "client_id": "cid", "client_secret": "sec"}
                ),
                encoding="utf-8",
            )
            turo_gmail.fetch_and_write(
                dest,
                token_path=token,
                env_file=Path(td) / "missing.env",
                env={},
                http=fake_http,
            )
            data = json.loads(dest.read_text(encoding="utf-8"))
            msg = data["messages"][0]
            self.assertIn("Contains photo(s)", msg["body"])
            self.assertNotIn("JFIF", msg["body"])
            self.assertEqual(len(msg["attachments"]), 1)
            att = msg["attachments"][0]
            self.assertEqual(att["filename"], "IMG_blocked_in.jpg")
            stored = Path(att["path"])
            self.assertEqual(stored.read_bytes(), jpeg)
            self.assertTrue(str(data["media_dir"]).endswith("_media"))
            self.assertTrue(str(att["path"]).startswith(str(data["media_dir"])))
            payload = turo_inbox.turo_payload(
                inbox_path=dest, units=_with_roles(ROSTER_UNITS)
            )
            self.assertEqual(payload["bookings"], [])
            self.assertEqual(len(payload["photo_messages"]), 1)
            self.assertEqual(
                payload["photo_messages"][0]["attachments"][0]["sha256"],
                att["sha256"],
            )

    def test_gmail_attachment_id_is_fetched(self) -> None:
        import turo_gmail

        jpeg = _fixture_jpeg(b"attachment-id")

        def fake_http(url: str, data, headers):
            if "oauth2.googleapis.com/token" in url:
                return {"access_token": "tok-test"}
            if url.startswith(turo_gmail.GMAIL_API + "/messages?") and "q=" in url:
                return {"messages": [{"id": "m-att"}]}
            if url.endswith("/attachments/ATT123") or "/attachments/ATT123" in url:
                return {
                    "size": len(jpeg),
                    "data": turo_gmail.base64.urlsafe_b64encode(jpeg).decode("ascii"),
                }
            if "/messages/m-att" in url:
                return {
                    "id": "m-att",
                    "snippet": "Contains photo(s).",
                    "payload": {
                        "mimeType": "multipart/mixed",
                        "headers": [
                            {"name": "From", "value": "Turo <noreply@mail.turo.com>"},
                            {
                                "name": "Subject",
                                "value": "Pat has sent you a message about your Toyota Corolla 2024",
                            },
                            {"name": "Date", "value": "Wed, 20 Aug 2026 12:00:00 +0000"},
                        ],
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {
                                    "data": turo_gmail.base64.urlsafe_b64encode(
                                        b"Contains photo(s).\nReservation ID #60619999\n"
                                        b"Toyota Corolla 2024\n"
                                    ).decode("ascii")
                                },
                            },
                            {
                                "mimeType": "image/jpeg",
                                "filename": "fuel.jpg",
                                "body": {"attachmentId": "ATT123", "size": len(jpeg)},
                            },
                        ],
                    },
                }
            raise AssertionError(f"unexpected url {url}")

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "dump.json"
            token = Path(td) / "gmail-token.json"
            token.write_text(
                json.dumps(
                    {"refresh_token": "r", "client_id": "cid", "client_secret": "sec"}
                ),
                encoding="utf-8",
            )
            turo_gmail.fetch_and_write(
                dest,
                token_path=token,
                env_file=Path(td) / "missing.env",
                env={},
                http=fake_http,
            )
            data = json.loads(dest.read_text(encoding="utf-8"))
            att = data["messages"][0]["attachments"][0]
            self.assertEqual(Path(att["path"]).read_bytes(), jpeg)
            self.assertEqual(att["filename"], "fuel.jpg")


if __name__ == "__main__":
    unittest.main()
