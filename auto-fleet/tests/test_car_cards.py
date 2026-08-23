"""#265 car-centric cards — structure + no-invent."""

from __future__ import annotations

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
        "vin": "5YFVPMAE9NP362974",
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
        "role": "personal",
        "vin": None,
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

        self.assertEqual(by_id["m3-2022"]["finance"]["locked"]["apr_pct"], 18.15)
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
        self.assertIsNone(by_id["r1s-2023"]["identity"]["plate"])
        self.assertIsNone(by_id["r1s-2023"]["identity"]["vin"])


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
        self.assertIn("60110022", live22)  # Marie active 8/23–8/25
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
        ]
        split = car_cards.attach_invoice_items(items, ROSTER_UNITS)
        self.assertEqual(len(split["by_unit"]["m3-2022"]), 1)
        self.assertEqual(split["unmatched"][0]["title"], "Garage insurance shared")
        self.assertEqual(split["by_unit"]["corolla-2024"], [])


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
        self.assertNotIn("inbox_status", html)
        self.assertNotIn("Kia", html)
        self.assertNotIn("Jessica", html)
        wells = glance.render_unit_card_html(by_id["m3-2020"], now=NOW)
        self.assertIn("Wells Fargo", wells)
        self.assertIn("5.65% APR", wells)
        self.assertNotIn("principal", wells)
        self.assertNotIn("amount_due", wells)
        rivian = glance.render_unit_card_html(by_id["r1s-2023"], now=NOW)
        self.assertIn("Vivek", rivian)
        self.assertIn("$1,350.00/mo", rivian)

    def test_schedule_queue_is_structured_not_joined_prose(self) -> None:
        index = (PKG / "index.html").read_text(encoding="utf-8")
        booking_fn = index[index.find("function bookingRow") : index.find("function tripIdFromText")]
        self.assertGreater(index.find("function bookingRow"), 0)
        self.assertNotIn('bits.join(" · ")', booking_fn)
        self.assertIn("booking-when", booking_fn)
        self.assertIn("booking-who", booking_fn)
        self.assertIn("booking-res", booking_fn)
        self.assertIn("booking-pickup", booking_fn)
        self.assertIn('class="queue"', index)
        self.assertIn("No upcoming trips", index)
        self.assertIn(".booking.canceled", index)
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
        self.assertIn("Aug 23–25", sched22)
        self.assertIn("Aug 26–27", sched22)
        self.assertIn("Aug 29–31", sched22)
        self.assertIn("NEXT", sched22)
        self.assertIn('data-phase="active"', sched22)
        self.assertIn("#60110022", sched22)
        self.assertNotIn(" · ", sched22)
        self.assertNotIn("drop-off", sched22)
        self.assertNotIn("phone", sched22.lower())

        c24 = glance.render_unit_card_html(by_id["corolla-2024"], now=NOW)
        sched24 = c24[c24.find("<h3>Schedule</h3>") : c24.find("<h3>Money</h3>")]
        self.assertIn('class="booking-who">Myles</div>', sched24)
        self.assertIn("Matthew", sched24)
        self.assertIn("MEGAN", sched24)
        self.assertLess(sched24.find("Myles"), sched24.find("Matthew"))
        self.assertLess(sched24.find("Matthew"), sched24.find("MEGAN"))
        self.assertIn("Aug 25–27", sched24)
        self.assertIn("Aug 28–30", sched24)
        self.assertIn("#60463692", sched24)
        self.assertIn("driveway", sched24)
        self.assertIn("booking canceled", sched24)
        self.assertIn('data-phase="canceled"', sched24)
        self.assertNotIn(" · ", sched24)
        self.assertNotIn("$", sched24)
        self.assertNotIn("APR", sched24)

        wells = glance.render_unit_card_html(by_id["m3-2020"], now=NOW)
        sched_wells = wells[wells.find("<h3>Schedule</h3>") : wells.find("<h3>Money</h3>")]
        self.assertIn("No upcoming trips", sched_wells)
        self.assertNotIn("Jessica", wells)
        self.assertNotIn("Kia", wells)
        self.assertNotIn("class=\"booking ", sched_wells)

        self.assertEqual(glance.human_when("2026-08-28", "2026-08-30"), "Aug 28–30")
        self.assertEqual(glance.human_when("2026-08-28", "2026-09-02"), "Aug 28–Sep 2")
        self.assertEqual(glance.human_when("2026-08-28", "2026-08-28"), "Aug 28")

    def test_index_is_car_first_no_invent_no_cic(self) -> None:
        html = (PKG / "index.html").read_text(encoding="utf-8")
        self.assertIn("<title>Auto Fleet</title>", html)
        self.assertIn("function vehicleStrip", html)
        self.assertIn("function scheduleStrip", html)
        self.assertIn("function moneyStrip", html)
        self.assertIn("function tripDetailStrip", html)
        self.assertIn("Unassigned Mike Turo", html)
        self.assertIn("Unassigned invoice-ready", html)
        self.assertNotIn("CIC", html)
        self.assertNotIn("Orchestra", html)
        self.assertNotIn(":8796", html)
        self.assertNotIn("VIN unknown", html)
        self.assertNotIn("invent", html.lower())
        cards_fn = html[html.find("function renderUnit") : html.find("function renderShared")]
        self.assertNotIn("renderHostOps", cards_fn)

    def test_annotate_gt_items_optional(self) -> None:
        items = turo_tasks.annotate_unit_ids(
            [{"id": "t1", "title": "2024 Toyota Corolla toll", "notes": ""}],
            ROSTER_UNITS,
        )
        self.assertEqual(items[0]["unit_id"], "corolla-2024")


if __name__ == "__main__":
    unittest.main()
