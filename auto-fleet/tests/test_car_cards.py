"""#265 car-centric cards — structure + no-invent."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import car_cards  # noqa: E402
import fleet  # noqa: E402
import glance  # noqa: E402
import turo_inbox  # noqa: E402
import turo_tasks  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROSTER = PKG / "data" / "roster.json"
NOTES = PKG / "data" / "notes.json"
EMPTY_INBOX = PKG / "data" / "turo_inbox.json"
BODY_YEAR = FIXTURES / "turo_mike_corolla_body_year.json"
NOW = "2026-08-23T12:00:00+00:00"

ROSTER_UNITS = [
    {
        "id": "m3-2020",
        "year": 2020,
        "make": "Tesla",
        "model": "Model 3",
        "role": "personal",
        "vin": None,
    },
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
        "plate": "24EWUH",
        "vin": "5YFVPMAE9NP362974",
    },
    {
        "id": "corolla-2024",
        "year": 2024,
        "make": "Toyota",
        "model": "Corolla",
        "role": "turo",
        "plate": "25EWUH",
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


class LockedFinanceTests(unittest.TestCase):
    def test_locked_lender_apr_no_invented_balances(self) -> None:
        payload = fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=FIXTURES / "expenses_no_fleet.json",
            inbox_path=EMPTY_INBOX,
            dimo_env={},
            now=NOW,
        )
        by_id = {u["id"]: u for u in payload["units"]}
        wells = by_id["m3-2020"]
        self.assertEqual(wells["identity"]["host_label"], None)
        self.assertIsNone(wells["identity"]["plate"])
        self.assertIsNone(wells["identity"]["vin"])
        self.assertEqual(wells["finance"]["locked"]["lender"], "Wells Fargo")
        self.assertEqual(wells["finance"]["locked"]["apr_pct"], 5.65)
        self.assertFalse(wells["finance"]["locked"]["show_balances"])
        self.assertNotIn("principal_balance", wells["finance"]["locked"])
        self.assertFalse(wells["glance"]["due"])

        self.assertIsNone(by_id["m3-2022"]["finance"]["locked"].get("apr_pct"))
        self.assertEqual(by_id["corolla-2022"]["finance"]["locked"]["apr_pct"], 11.14)
        self.assertEqual(by_id["corolla-2024"]["finance"]["locked"]["apr_pct"], 10.18)
        rivian = by_id["r1s-2023"]["finance"]["locked"]
        self.assertEqual(rivian["lender"], "Vivek")
        self.assertEqual(rivian["apr_pct"], 0)
        self.assertEqual(rivian["monthly"], 1350)
        self.assertFalse(rivian["show_balances"])
        self.assertEqual(by_id["corolla-2024"]["identity"]["host_label"], "Mike's")
        self.assertEqual(by_id["corolla-2022"]["identity"]["host_label"], "Mike's")
        self.assertEqual(by_id["m3-2022"]["identity"]["host_label"], "Mike's")
        self.assertEqual(by_id["r1s-2023"]["identity"]["host_label"], "Mike's")
        self.assertEqual(by_id["corolla-2022"]["identity"]["plate"], "24EWUH")
        self.assertEqual(by_id["corolla-2024"]["identity"]["plate"], "25EWUH")
        self.assertIsNone(by_id["m3-2022"]["identity"]["plate"])
        self.assertIsNone(by_id["r1s-2023"]["identity"]["plate"])
        self.assertEqual(by_id["r1s-2023"]["identity"]["vin"], "7PDSGABA3PN028624")


class HostIdentityTests(unittest.TestCase):
    """Mail-proven Mike Turo chip. Static. Not a host inbox. Helm stays locked."""

    STATIC = {
        "host_label": "Mike's",
        "driver_id": "27172979",
        "public_url": "https://turo.com/us/en/drivers/27172979",
    }

    def test_mail_proven_corollas_and_rivian(self) -> None:
        self.assertEqual(
            car_cards.host_identity_for({"id": "corolla-2022", "role": "turo"}),
            self.STATIC,
        )
        self.assertEqual(
            car_cards.host_identity_for({"id": "corolla-2024", "role": "turo"}),
            self.STATIC,
        )
        self.assertEqual(
            car_cards.host_identity_for({"id": "r1s-2023", "role": "turo"}),
            self.STATIC,
        )
        self.assertIsNone(
            car_cards.host_identity_for({"id": "m3-2022", "role": "turo"})
        )
        self.assertIsNone(
            car_cards.host_identity_for({"id": "m3-2020", "role": "personal"})
        )
        self.assertIsNone(
            car_cards.host_identity_for({"id": "r1s-2023", "role": "personal"})
        )
        self.assertIsNone(
            car_cards.host_identity_for({"id": "corolla-2022", "role": "personal"})
        )

    def test_fleet_payload_scopes_chip_to_listing_proven_units(self) -> None:
        payload = fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=FIXTURES / "expenses_no_fleet.json",
            inbox_path=EMPTY_INBOX,
            dimo_env={},
            now=NOW,
        )
        by_id = {u["id"]: u for u in payload["units"]}
        for uid in ("corolla-2022", "corolla-2024", "r1s-2023"):
            self.assertEqual(by_id[uid]["identity"]["host_identity"], self.STATIC)
        for uid in ("m3-2022", "m3-2020"):
            self.assertIsNone(by_id[uid]["identity"]["host_identity"])
        blob = json.dumps(payload)
        self.assertNotIn("rating", blob.lower())
        self.assertNotIn("response time", blob.lower())
        self.assertNotIn("listing inventory", blob.lower())

    def test_card_html_host_strip_on_corollas_only(self) -> None:
        payload = fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=FIXTURES / "expenses_no_fleet.json",
            inbox_path=EMPTY_INBOX,
            dimo_env={},
            now=NOW,
        )
        by_id = {u["id"]: u for u in payload["units"]}
        for uid in ("corolla-2022", "corolla-2024", "r1s-2023"):
            html = glance.render_unit_card_html(by_id[uid], now=NOW)
            host = html[: html.find("<h3>Vehicle</h3>")]
            self.assertIn('class="strip host-identity"', host)
            self.assertIn("<h3>Host</h3>", host)
            self.assertIn("Mike&#39;s", host)
            self.assertIn("27172979", host)
            self.assertIn("https://turo.com/us/en/drivers/27172979", host)
            self.assertIn("turo.com/us/en/drivers/27172979", host)
            self.assertIn('target="_blank"', host)
            self.assertIn("rel=\"noopener\"", host)
            self.assertNotIn("Schedule", host)
            self.assertNotIn("booking", host.lower())
            self.assertNotIn("inbox", host.lower())
        for uid in ("m3-2022", "m3-2020"):
            html = glance.render_unit_card_html(by_id[uid], now=NOW)
            self.assertNotIn("host-identity", html)
            self.assertNotIn("27172979", html)
            self.assertNotIn("turo.com/us/en/drivers", html)
        m3 = glance.render_unit_card_html(by_id["m3-2022"], now=NOW)
        self.assertIn("Mike&#39;s", m3)

    def test_dash_host_strip_is_not_a_second_inbox(self) -> None:
        index = (PKG / "index.html").read_text(encoding="utf-8")
        fn = index[
            index.find("function hostIdentityStrip") : index.find("function vehicleStrip")
        ]
        self.assertGreater(index.find("function hostIdentityStrip"), 0)
        self.assertIn("host-identity", fn)
        self.assertIn("host.public_url", fn)
        self.assertIn("target=\"_blank\"", fn)
        self.assertIn("rel=\"noopener\"", fn)
        self.assertNotIn("booking", fn.lower())
        self.assertNotIn("inbox", fn.lower())
        self.assertNotIn("rating", fn.lower())
        self.assertNotIn("scheduleStrip", fn)
        self.assertNotIn(":8796", index)
        render = index[index.find("function renderUnit") : index.find("function renderShared")]
        self.assertIn("hostIdentityStrip(idn)", render)
        self.assertLess(
            render.find("hostIdentityStrip(idn)"),
            render.find("vehicleStrip(u, g)"),
        )

    def test_no_turo_network_from_host_identity(self) -> None:
        src = (PKG / "car_cards.py").read_text(encoding="utf-8")
        self.assertIn(self.STATIC["public_url"], src)
        self.assertNotIn("urlopen", src)
        self.assertNotIn("requests", src)
        self.assertNotIn("urllib", src)
        server = (PKG / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("turo.com/us/en/drivers", server)
        self.assertNotIn("27172979", server)


class ScheduleCollapseTests(unittest.TestCase):
    def test_bookings_stay_on_matching_cars_unmatched_honest(self) -> None:
        payload = fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=FIXTURES / "expenses_no_fleet.json",
            inbox_path=BODY_YEAR,
            dimo_env={},
            now=NOW,
        )
        by_id = {u["id"]: u for u in payload["units"]}
        c24 = by_id["corolla-2024"]
        c22 = by_id["corolla-2022"]
        live24 = {t["trip_id"] for t in c24["turo"]["schedule"] if t["phase"] != "canceled"}
        self.assertIn("60463692", live24)  # Myles upcoming
        self.assertIn("60881200", live24)  # Matthew upcoming
        canceled24 = [t for t in c24["turo"]["schedule"] if t["phase"] == "canceled"]
        self.assertEqual({t["trip_id"] for t in canceled24}, {"60615645"})
        self.assertTrue(all(t["guest"] != "MEGAN" or t["phase"] == "canceled" for t in c24["turo"]["schedule"]))
        live22 = {t["trip_id"] for t in c22["turo"]["schedule"]}
        self.assertIn("60110022", live22)  # Marie upcoming at 8:00 AM ET (1:00 PM start)
        self.assertIn("60220022", live22)  # Nayive upcoming in fixture
        self.assertIn("60330022", live22)  # Jeffrey
        self.assertEqual(by_id["m3-2020"]["turo"]["schedule"], [])
        self.assertEqual(by_id["r1s-2023"]["turo"]["schedule"], [])
        unmatched = payload["turo_unmatched"]
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["guest"], "Pat Kim")
        self.assertIsNone(unmatched[0]["unit_id"])

    def test_past_trips_drop_off_the_card(self) -> None:
        later = fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=FIXTURES / "expenses_no_fleet.json",
            inbox_path=BODY_YEAR,
            dimo_env={},
            now="2026-09-10T12:00:00+00:00",
        )
        by_id = {u["id"]: u for u in later["units"]}
        self.assertEqual(by_id["corolla-2022"]["turo"]["schedule"], [])
        self.assertEqual(by_id["corolla-2024"]["turo"]["schedule"], [])
        self.assertTrue(by_id["corolla-2022"]["glance"]["available"])

    def test_raw_bookings_still_on_unit_for_dump_path(self) -> None:
        payload = fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=FIXTURES / "expenses_no_fleet.json",
            inbox_path=BODY_YEAR,
            dimo_env={},
            now=NOW,
        )
        c24 = next(u for u in payload["units"] if u["id"] == "corolla-2024")
        raw_ids = {b["trip_id"] for b in c24["turo"]["bookings"]}
        self.assertIn("60615645", raw_ids)
        self.assertIn("60463692", raw_ids)


class SchedulePhaseTests(unittest.TestCase):
    """Clock-aware phase: morning ≠ active when start is this afternoon."""

    def test_same_day_afternoon_start_is_upcoming_in_the_morning(self) -> None:
        trip = {
            "trip_id": "phase-pm",
            "status": "booked",
            "guest": "Pat",
            "start": "2026-08-23T15:00:00-04:00",
            "end": "2026-08-23T18:00:00-04:00",
        }
        morning = car_cards.schedule_for_bookings(
            [trip], now="2026-08-23T07:45:00-04:00"
        )
        self.assertEqual(len(morning), 1)
        self.assertEqual(morning[0]["phase"], "upcoming")

    def test_same_day_after_start_is_active(self) -> None:
        trip = {
            "trip_id": "phase-pm",
            "status": "booked",
            "guest": "Pat",
            "start": "2026-08-23T15:00:00-04:00",
            "end": "2026-08-23T18:00:00-04:00",
        }
        afternoon = car_cards.schedule_for_bookings(
            [trip], now="2026-08-23T15:30:00-04:00"
        )
        self.assertEqual(afternoon[0]["phase"], "active")

    def test_multi_day_middle_is_active(self) -> None:
        trip = {
            "trip_id": "phase-mid",
            "status": "booked",
            "guest": "Marie",
            "start": "2026-08-23T13:00:00-04:00",
            "end": "2026-08-25T13:00:00-04:00",
        }
        mid = car_cards.schedule_for_bookings(
            [trip], now="2026-08-24T07:45:00-04:00"
        )
        self.assertEqual(mid[0]["phase"], "active")

    def test_canceled_phase_unchanged(self) -> None:
        trip = {
            "trip_id": "phase-cx",
            "status": "canceled",
            "guest": "MEGAN",
            "start": "2026-08-23T15:00:00-04:00",
            "end": "2026-08-25T15:00:00-04:00",
        }
        rows = car_cards.schedule_for_bookings(
            [trip], now="2026-08-23T07:45:00-04:00"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phase"], "canceled")

    def test_date_only_same_day_upcoming_until_noon_then_active(self) -> None:
        trip = {
            "trip_id": "phase-date",
            "status": "booked",
            "guest": "Alex",
            "start": "2026-08-23",
            "end": "2026-08-23",
        }
        morning = car_cards.schedule_for_bookings(
            [trip], now="2026-08-23T07:45:00-04:00"
        )
        self.assertEqual(morning[0]["phase"], "upcoming")
        noon = car_cards.schedule_for_bookings(
            [trip], now="2026-08-23T12:00:00-04:00"
        )
        self.assertEqual(noon[0]["phase"], "active")

    def test_marie_fixture_active_after_one_pm_et(self) -> None:
        payload = fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=FIXTURES / "expenses_no_fleet.json",
            inbox_path=BODY_YEAR,
            dimo_env={},
            now="2026-08-23T15:30:00-04:00",
        )
        c22 = next(u for u in payload["units"] if u["id"] == "corolla-2022")
        marie = next(t for t in c22["turo"]["schedule"] if t["trip_id"] == "60110022")
        self.assertEqual(marie["phase"], "active")
        self.assertEqual(marie["start"], "2026-08-23T13:00:00-04:00")


class NextBadgeTests(unittest.TestCase):
    """NEXT is the soonest upcoming trip, never an in-progress active one."""

    def test_next_upcoming_index_skips_active(self) -> None:
        live, _ = glance.queue_bookings(
            [
                {"guest": "Marie", "phase": "active", "start": "2026-08-23T12:30:00-04:00"},
                {"guest": "Nayive", "phase": "upcoming", "start": "2026-08-26T11:00:00-04:00"},
            ]
        )
        self.assertEqual(live[0]["guest"], "Marie")
        self.assertEqual(glance.next_upcoming_index(live), 1)
        self.assertIsNone(
            glance.next_upcoming_index([{"guest": "Marie", "phase": "active"}])
        )

    def test_active_only_queue_has_no_next_badge(self) -> None:
        html = glance.schedule_queue_html(
            [
                {
                    "guest": "Marie",
                    "phase": "active",
                    "status": "booked",
                    "start": "2026-08-23T12:30:00-04:00",
                    "end": "2026-08-30T12:30:00-04:00",
                    "trip_id": "60604995",
                }
            ]
        )
        self.assertIn("Marie", html)
        self.assertIn('data-phase="active"', html)
        self.assertNotIn("NEXT", html)
        self.assertNotIn("booking active next", html)

    def test_active_marie_does_not_steal_next_from_nayive(self) -> None:
        payload = fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=FIXTURES / "expenses_no_fleet.json",
            inbox_path=BODY_YEAR,
            dimo_env={},
            now="2026-08-23T15:30:00-04:00",
        )
        c22 = next(u for u in payload["units"] if u["id"] == "corolla-2022")
        html = glance.schedule_queue_html(c22["turo"]["schedule"])
        articles = html.split("<article ")
        marie = next(a for a in articles if "Marie" in a)
        nayive = next(a for a in articles if "Nayive" in a)
        self.assertIn('data-phase="active"', marie)
        self.assertNotIn("NEXT", marie)
        self.assertIn("NEXT", nayive)
        self.assertIn('data-phase="upcoming"', nayive)

    def test_booking_row_refuses_next_on_active(self) -> None:
        html = glance.booking_row_html(
            {"guest": "Marie", "phase": "active", "trip_id": "60604995"},
            next_trip=True,
        )
        self.assertNotIn("NEXT", html)
        self.assertNotIn("booking active next", html)

    def test_index_next_badge_uses_upcoming_index(self) -> None:
        index = (PKG / "index.html").read_text(encoding="utf-8")
        self.assertIn("function nextUpcomingIndex", index)
        self.assertIn("next: i === nextIdx", index)
        self.assertNotIn("next: i === 0", index)


class OpsFieldTests(unittest.TestCase):
    def test_ops_flags_only_when_present_in_mail(self) -> None:
        parsed = turo_inbox.parse_message(
            {
                "subject": "(Mike's vehicle) - Pat's trip with your Toyota Corolla is booked!",
                "from": "Turo <noreply@mail.turo.com>",
                "date": "Tue, 19 Aug 2026 14:10:00 +0000",
                "body": (
                    "Toyota Corolla 2024\nbooked by Pat Kim\nReservation ID #60615645\n"
                    "Pickup: Punta Gorda Airport FBO\n"
                    "Drop-off: driveway after 6pm\n"
                    "Extra driver: Jane Doe (Turo-verified)\n"
                    "Phone: 941-555-0142\n"
                    "Phone tap: confirm FBO gate code\n"
                ),
            }
        )
        self.assertEqual(parsed["pickup"], "Punta Gorda Airport FBO")
        self.assertEqual(parsed["drop_off"], "driveway after 6pm")
        self.assertEqual(parsed["phone"], "941-555-0142")
        self.assertEqual(parsed["extra_drivers"][0]["name"], "Jane Doe")
        self.assertTrue(parsed["extra_drivers"][0]["turo_verified"])
        self.assertEqual(parsed["guest_asks"], ["confirm FBO gate code"])
        self.assertEqual(parsed["host_label"], "Mike's")
        self.assertEqual(
            turo_inbox.match_unit(parsed, ROSTER_UNITS),
            "corolla-2024",
        )

    def test_no_invented_ops_fields(self) -> None:
        parsed = turo_inbox.parse_message(
            {
                "subject": "(Mike's vehicle) - Myles's trip with your Toyota Corolla is booked!",
                "from": "Turo <noreply@mail.turo.com>",
                "date": "Wed, 20 Aug 2026 11:05:00 +0000",
                "body": (
                    "Toyota Corolla 2024\nbooked by Myles\n"
                    "Reservation ID #60463692\nPickup: driveway\n"
                ),
            }
        )
        self.assertEqual(parsed["pickup"], "driveway")
        self.assertNotIn("drop_off", parsed)
        self.assertNotIn("phone", parsed)
        self.assertNotIn("extra_drivers", parsed)
        self.assertNotIn("guest_asks", parsed)


class InvoiceMatchTests(unittest.TestCase):
    def test_gt_item_nests_under_year_car_not_guessed(self) -> None:
        item = {
            "id": "task-1",
            "title": "Rebill toll — 2024 Corolla #60615645",
            "notes": "SunPass. File on Turo.",
        }
        uid = car_cards.match_invoice_unit(item, ROSTER_UNITS)
        self.assertEqual(uid, "corolla-2024")
        yearless = {
            "id": "task-2",
            "title": "Rebill toll — Toyota Corolla",
            "notes": "No year in the task.",
        }
        self.assertIsNone(car_cards.match_invoice_unit(yearless, ROSTER_UNITS))
        plate_only = {
            "id": "task-3",
            "title": "Rebill toll — plate 24EWUH",
            "notes": "File on Turo.",
        }
        self.assertEqual(car_cards.match_invoice_unit(plate_only, ROSTER_UNITS), "corolla-2022")
        unknown_plate = {
            "id": "task-4",
            "title": "Rebill toll — plate ZZ9999",
            "notes": "",
        }
        self.assertIsNone(car_cards.match_invoice_unit(unknown_plate, ROSTER_UNITS))
        conflict = {
            "id": "task-5",
            "title": "2024 Corolla plate 24EWUH",
            "notes": "Year and plate name different cars.",
        }
        self.assertIsNone(car_cards.match_invoice_unit(conflict, ROSTER_UNITS))

    def test_gt_trip_id_uses_bookings_not_name_guess(self) -> None:
        books = {
            "corolla-2024": [{"trip_id": "60615645", "guest": "MEGAN"}],
            "corolla-2022": [{"trip_id": "60110022", "guest": "Marie"}],
        }
        item = {"title": "Invoice cleaning fee #60615645", "notes": ""}
        self.assertEqual(
            car_cards.match_invoice_unit(item, ROSTER_UNITS, books),
            "corolla-2024",
        )

    def test_attach_leaves_unmatched_at_fleet_level(self) -> None:
        items = [
            {"id": "a", "title": "Rebill 2022 Tesla Model 3", "notes": ""},
            {"id": "b", "title": "Garage insurance shared", "notes": ""},
            {"id": "done", "title": "2024 Corolla already billed", "notes": "", "status": "completed"},
        ]
        split = car_cards.attach_invoice_items(items, ROSTER_UNITS)
        self.assertEqual(len(split["by_unit"]["m3-2022"]), 1)
        self.assertEqual(split["unmatched"][0]["title"], "Garage insurance shared")
        self.assertEqual(split["by_unit"]["corolla-2024"], [])
        painted = [i["title"] for rows in split["by_unit"].values() for i in rows]
        painted += [i["title"] for i in split["unmatched"]]
        self.assertNotIn("2024 Corolla already billed", painted)


class CardHtmlTests(unittest.TestCase):
    def test_card_has_vehicle_schedule_money_not_inbox(self) -> None:
        payload = fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=FIXTURES / "expenses_no_fleet.json",
            inbox_path=BODY_YEAR,
            dimo_env={},
            now=NOW,
        )
        by_id = {u["id"]: u for u in payload["units"]}
        html = glance.render_unit_card_html(by_id["corolla-2024"], now=NOW)
        self.assertIn("<h3>Vehicle</h3>", html)
        self.assertIn("<h3>Schedule</h3>", html)
        self.assertIn("<h3>Money</h3>", html)
        self.assertIn("Myles", html)
        self.assertIn("#60463692", html)
        self.assertIn("driveway", html)
        self.assertIn("Santander", html)
        self.assertIn("10.18% APR", html)
        veh24 = html[html.find("<h3>Vehicle</h3>") : html.find("<h3>Schedule</h3>")]
        self.assertIn("25EWUH", veh24)
        self.assertIn('data-copy="25EWUH"', veh24)
        self.assertIn("Copy plate", veh24)
        self.assertIn("booking-res", veh24)
        self.assertNotIn("24EWUH", veh24)
        self.assertNotIn("inbox_status", html)
        self.assertNotIn("Kia", html)
        self.assertNotIn("Jessica", html)
        wells = glance.render_unit_card_html(by_id["m3-2020"], now=NOW)
        self.assertIn("Wells Fargo", wells)
        self.assertIn("5.65% APR", wells)
        self.assertNotIn("principal", wells)
        self.assertNotIn("amount_due", wells)
        self.assertNotIn("24EWUH", wells)
        self.assertNotIn("25EWUH", wells)
        self.assertNotIn("Copy plate", wells)
        rivian = glance.render_unit_card_html(by_id["r1s-2023"], now=NOW)
        self.assertIn("Vivek", rivian)
        self.assertIn("$1,350.00/mo", rivian)
        self.assertIn(
            "https://docs.google.com/spreadsheets/d/1H4hjK7hNOyUHAIekWwxuqf3NgZOpSdyezHA7rQ3Zafc/edit",
            rivian,
        )
        self.assertIn(">sheet</a>", rivian)
        self.assertNotIn("24EWUH", rivian)
        self.assertNotIn("25EWUH", rivian)
        c22 = glance.render_unit_card_html(by_id["corolla-2022"], now=NOW)
        veh22 = c22[c22.find("<h3>Vehicle</h3>") : c22.find("<h3>Schedule</h3>")]
        self.assertIn("24EWUH", veh22)
        self.assertIn('data-copy="24EWUH"', veh22)
        self.assertNotIn("25EWUH", veh22)
        m3turo = glance.render_unit_card_html(by_id["m3-2022"], now=NOW)
        self.assertNotIn("24EWUH", m3turo)
        self.assertNotIn("25EWUH", m3turo)
        self.assertNotIn("Copy plate", m3turo)

    def test_schedule_queue_is_structured_not_joined_prose(self) -> None:
        index = (PKG / "index.html").read_text(encoding="utf-8")
        booking_fn = index[index.find("function bookingRow") : index.find("function tripIdFromText")]
        self.assertGreater(index.find("function bookingRow"), 0)
        self.assertNotIn('bits.join(" · ")', booking_fn)
        self.assertIn("booking-when", booking_fn)
        self.assertIn("booking-who", booking_fn)
        self.assertIn("booking-res", booking_fn)
        self.assertIn("booking-pickup", booking_fn)
        self.assertIn("function pickupLabel", index)
        self.assertIn("function parseWhen", index)
        self.assertIn("function clockLabel", index)
        self.assertIn("function humanWhen", index)
        self.assertIn('timeZone: "America/New_York"', index)
        self.assertIn("coordinate / driveway", index)
        self.assertIn('class="queue"', index)
        self.assertIn("queue-cols", index)
        self.assertIn("No upcoming trips", index)
        self.assertIn(".booking.canceled", index)
        self.assertIn("queue-canceled", index)
        self.assertIn("function next30Strip", index)
        self.assertIn("function nextUpcomingIndex", index)
        self.assertIn('id="next30"', index)
        self.assertNotIn("CIC", index)
        self.assertNotIn(":8796", index)

        payload = fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=FIXTURES / "expenses_no_fleet.json",
            inbox_path=BODY_YEAR,
            dimo_env={},
            now=NOW,
        )
        by_id = {u["id"]: u for u in payload["units"]}

        c22 = glance.render_unit_card_html(by_id["corolla-2022"], now=NOW)
        sched22 = c22[c22.find("<h3>Schedule</h3>") : c22.find("<h3>Money</h3>")]
        self.assertIn('class="queue"', sched22)
        self.assertIn('class="booking-who">Marie</div>', sched22)
        self.assertIn("Nayive", sched22)
        self.assertIn("Jeffrey", sched22)
        self.assertLess(sched22.find("Marie"), sched22.find("Nayive"))
        self.assertLess(sched22.find("Nayive"), sched22.find("Jeffrey"))
        self.assertIn("Aug 23 1:00 PM → Aug 25 1:00 PM", sched22)
        self.assertIn("Aug 26 11:00 AM → Aug 27 11:00 AM", sched22)
        self.assertIn("Aug 29 4:00 PM → Aug 31 4:00 PM", sched22)
        self.assertIn(">ET<", sched22)
        self.assertIn("NEXT", sched22)
        self.assertIn('data-phase="upcoming"', sched22)
        self.assertNotIn('data-phase="active"', sched22)
        self.assertIn("upcoming", sched22)
        self.assertIn("#60110022", sched22)
        self.assertIn("coordinate / driveway", sched22)
        self.assertIn(">When<", sched22)
        self.assertIn(">Status<", sched22)
        self.assertIn(">Guest<", sched22)
        self.assertIn(">Pickup<", sched22)
        self.assertIn(">Res<", sched22)
        self.assertNotIn("drop-off", sched22)
        self.assertNotIn("phone", sched22.lower())
        self.assertNotIn("extra driver", sched22)
        self.assertNotIn("pay window", sched22)
        self.assertNotIn("invoice-ready", sched22)

        c24 = glance.render_unit_card_html(by_id["corolla-2024"], now=NOW)
        sched24 = c24[c24.find("<h3>Schedule</h3>") : c24.find("<h3>Money</h3>")]
        self.assertIn('class="booking-who">Myles</div>', sched24)
        self.assertIn("Matthew", sched24)
        self.assertIn("MEGAN", sched24)
        self.assertLess(sched24.find("Myles"), sched24.find("Matthew"))
        self.assertLess(sched24.find("Matthew"), sched24.find("MEGAN"))
        self.assertIn("Aug 25 9:00 AM → Aug 27 5:00 PM", sched24)
        self.assertIn("Aug 28 2:00 PM → Aug 30 2:00 PM", sched24)
        self.assertIn("#60463692", sched24)
        self.assertIn("coordinate / driveway", sched24)
        self.assertIn("queue-canceled", sched24)
        self.assertIn("Cancelled (", sched24)
        self.assertIn("booking canceled", sched24)
        self.assertIn('data-phase="canceled"', sched24)
        self.assertIn("cancelled", sched24)
        self.assertNotIn("$", sched24)
        self.assertNotIn("APR", sched24)
        self.assertNotIn("principal", sched24)

        wells = glance.render_unit_card_html(by_id["m3-2020"], now=NOW)
        sched_wells = wells[wells.find("<h3>Schedule</h3>") : wells.find("<h3>Money</h3>")]
        self.assertIn("No upcoming trips", sched_wells)
        self.assertNotIn("Jessica", wells)
        self.assertNotIn("Kia", wells)
        self.assertNotIn("class=\"booking ", sched_wells)

        self.assertEqual(glance.human_when("2026-08-28", "2026-08-30"), "Aug 28 → 30")
        self.assertEqual(glance.human_when("2026-08-28", "2026-09-02"), "Aug 28 → Sep 2")
        self.assertEqual(glance.human_when("2026-08-28", "2026-08-28"), "Aug 28")
        self.assertEqual(
            glance.human_when("2026-08-23T15:00:00-04:00", "2026-08-25T15:00:00-04:00"),
            "Aug 23 3:00 PM → Aug 25 3:00 PM",
        )
        self.assertEqual(
            glance.human_when("2026-08-23T15:00:00-04:00", "2026-08-23T18:00:00-04:00"),
            "Aug 23 3:00 PM → 6:00 PM",
        )
        self.assertEqual(
            glance.pickup_label({"pickup": "Punta Gorda Airport FBO"}),
            "Punta Gorda Airport FBO",
        )
        self.assertEqual(glance.pickup_label({"pickup": "driveway"}), "coordinate / driveway")
        self.assertEqual(glance.pickup_label({}), "coordinate / driveway")
        flagged = glance.booking_row_html(
            {
                "guest": "Pat",
                "start": "2026-08-28",
                "end": "2026-08-29",
                "trip_id": "60615645",
                "pickup": "Punta Gorda Airport FBO",
                "phone": "941-555-0142",
                "extra_drivers": [{"name": "Jane Doe"}],
                "guest_asks": ["confirm FBO gate code"],
                "phase": "upcoming",
            },
            invoice_items=[{"title": "Rebill #60615645", "notes": ""}],
        )
        self.assertIn("extra driver", flagged)
        self.assertIn("needs phone tap", flagged)
        self.assertIn("invoice-ready", flagged)
        self.assertIn("941-555-0142", flagged)
        self.assertNotIn("pay window", flagged)

    def test_index_is_car_first_no_invent_no_cic(self) -> None:
        html = (PKG / "index.html").read_text(encoding="utf-8")
        self.assertIn("<title>Auto Fleet</title>", html)
        self.assertIn("function vehicleStrip", html)
        self.assertIn("function hostIdentityStrip", html)
        self.assertIn("function scheduleStrip", html)
        self.assertIn("function moneyStrip", html)
        self.assertIn("function awaitingStrip", html)
        self.assertIn("function tripDetailStrip", html)
        self.assertIn("Unassigned Mike Turo", html)
        self.assertIn("Unassigned invoice-ready", html)
        self.assertIn("<h3>Awaiting</h3>", html)
        self.assertNotIn("CIC", html)
        self.assertNotIn("Orchestra", html)
        self.assertNotIn(":8796", html)
        self.assertNotIn("VIN unknown", html)
        self.assertNotIn("invent", html.lower())
        self.assertNotIn("parking-map", html)
        self.assertNotIn("driveway-diagram", html)
        self.assertNotIn("driveway map", html.lower())
        veh_fn = html[html.find("function vehicleStrip") : html.find("function scheduleStrip")]
        self.assertIn("idn.plate", veh_fn)
        self.assertIn("Copy plate", veh_fn)
        self.assertIn("booking-res", veh_fn)
        self.assertIn("data-copy", veh_fn)
        cards_fn = html[html.find("function renderUnit") : html.find("function renderShared")]
        self.assertNotIn("renderHostOps", cards_fn)

    def test_awaiting_strip_present_vs_honest_empty(self) -> None:
        unit = {
            "id": "corolla-2024",
            "identity": {"year": 2024, "make": "Toyota", "model": "Corolla", "role": "turo"},
            "finance": {"locked": {"lender": "Santander", "apr_pct": 10.18}},
            "dimo": {"status": "unconfigured"},
            "turo": {"bookings": [], "schedule": []},
            "invoice_ready": [
                {"id": "t1", "title": "Follow-up cleaning #60463692", "notes": "File on Turo.", "status": "needsAction"},
                {"id": "done", "title": "already billed", "notes": "", "status": "completed"},
            ],
        }
        html = glance.render_unit_card_html(unit, now=NOW)
        self.assertIn('<div class="strip awaiting">', html)
        self.assertIn("<h3>Awaiting</h3>", html)
        self.assertIn("Follow-up cleaning #60463692", html)
        self.assertNotIn("already billed", html)
        empty = dict(unit)
        empty["invoice_ready"] = []
        empty_html = glance.render_unit_card_html(empty, now=NOW)
        self.assertNotIn("<h3>Awaiting</h3>", empty_html)
        self.assertNotIn("class=\"strip awaiting\"", empty_html)
        self.assertNotIn("no invoice-ready", empty_html.lower())
        self.assertNotIn("nothing to do", empty_html.lower())

    def test_annotate_gt_items_optional(self) -> None:
        items = turo_tasks.annotate_unit_ids(
            [{"id": "t1", "title": "2024 Toyota Corolla toll", "notes": ""}],
            ROSTER_UNITS,
        )
        self.assertEqual(items[0]["unit_id"], "corolla-2024")


if __name__ == "__main__":
    unittest.main()
