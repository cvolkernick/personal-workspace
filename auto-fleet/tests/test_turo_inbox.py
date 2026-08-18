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
        self.assertIn("cvolkern@gmail.com", payload["inbox_status"])
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
        self.assertEqual(rec["start"], "2026-09-01")
        self.assertEqual(rec["end"], "2026-09-04")

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
            self.assertIn("cvolkern@gmail.com", payload["inbox_status"])
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


if __name__ == "__main__":
    unittest.main()
