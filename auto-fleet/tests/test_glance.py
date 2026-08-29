"""Slice G format helpers — km→mi, SoC, stale/dead, one Turo inbox footer."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import glance  # noqa: E402
import fleet  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROSTER = PKG / "data" / "roster.json"
NOTES = PKG / "data" / "notes.json"
EMPTY_INBOX = PKG / "data" / "turo_inbox.json"
NOW = "2026-08-18T03:24:00+00:00"


class FormatTests(unittest.TestCase):
    def test_km_to_miles_odo_zero_decimals(self) -> None:
        self.assertEqual(glance.odo_miles(1.609344), 1)
        self.assertEqual(glance.odo_miles(16093.44), 10000)
        # 10-decimal dump from live DIMO must not print as-is
        self.assertEqual(glance.odo_miles(94725.3000000000), 58860)
        self.assertIsNone(glance.odo_miles(None))
        self.assertIsNone(glance.odo_miles("not-a-number"))

    def test_range_miles_zero_or_one_decimal(self) -> None:
        self.assertEqual(glance.range_miles(333.123456789), 207.0)
        self.assertEqual(glance.range_miles(160.9344), 100.0)
        self.assertEqual(glance.range_miles(241.5), 150.1)
        self.assertIsNone(glance.range_miles(None))

    def test_soc_integer_percent(self) -> None:
        self.assertEqual(glance.soc_pct(80), 80)
        self.assertEqual(glance.soc_pct(80.4), 80)
        self.assertEqual(glance.soc_pct(22.0), 22)
        self.assertEqual(glance.soc_pct("79.6"), 80)
        self.assertIsNone(glance.soc_pct(None))
        self.assertIsNone(glance.soc_pct(""))

    def test_stale_and_dead_thresholds(self) -> None:
        now = datetime(2026, 8, 18, 3, 24, tzinfo=timezone.utc)
        live = (now - timedelta(hours=23)).isoformat()
        stale = (now - timedelta(hours=25)).isoformat()
        dead = (now - timedelta(days=8)).isoformat()
        july = "2026-07-01T00:00:00Z"
        self.assertEqual(glance.freshness(live, now), "live")
        self.assertEqual(glance.freshness(stale, now), "stale")
        self.assertEqual(glance.freshness(dead, now), "dead")
        self.assertEqual(glance.freshness(july, now), "dead")
        self.assertEqual(glance.freshness(None, now), "unknown")
        self.assertEqual(glance.relative_age(july, now), "48d ago")
        self.assertEqual(glance.relative_age(now - timedelta(minutes=6), now), "6m ago")
        self.assertEqual(glance.relative_age(now - timedelta(seconds=10), now), "just now")
        # Live DIMO uses 4-digit fractions; 3.9 fromisoformat rejects them raw.
        four = glance.parse_ts("2026-08-18T03:30:10.7072Z")
        self.assertIsNotNone(four)
        self.assertEqual(glance.freshness("2026-08-18T03:30:10.7072Z", now), "live")

    def test_turo_line_does_not_invent_bookings(self) -> None:
        self.assertEqual(
            glance.turo_line({"bookings": []}, 900),
            "0 trips · watching 15m",
        )
        self.assertEqual(
            glance.turo_line(
                {
                    "bookings": [
                        {
                            "status": "booked",
                            "guest": "Alex Rivera",
                            "start": "2026-09-01",
                            "end": "2026-09-04",
                        }
                    ]
                },
                900,
            ),
            "booked · Alex Rivera · 2026-09-01 → 2026-09-04",
        )

    def test_card_html_lists_unit_bookings(self) -> None:
        unit = {
            "id": "corolla-2024",
            "identity": {
                "year": 2024,
                "make": "Toyota",
                "model": "Corolla",
                "role": "turo",
            },
            "dimo": {"status": "unconfigured"},
            "turo": {
                "bookings": [
                    {
                        "status": "booked",
                        "guest": "MEGAN",
                        "start": "2026-08-22",
                        "end": "2026-08-24",
                        "trip_id": "60615645",
                        "pickup": "Punta Gorda Airport FBO",
                    }
                ]
            },
            "finance": {},
        }
        html = glance.render_unit_card_html(unit, now=NOW)
        sched = html[html.find("<h3>Schedule</h3>") : html.find("<h3>Money</h3>")]
        self.assertIn('class="booking-who">MEGAN</div>', sched)
        self.assertIn("Aug 22 → 24", sched)
        self.assertIn("#60615645", sched)
        self.assertIn("Punta Gorda Airport FBO", sched)
        self.assertIn('class="queue"', sched)
        self.assertNotIn("booked · MEGAN", sched)
        self.assertNotIn("0 trips · watching", html)

    def test_due_from_2024_corolla_portal(self) -> None:
        payload = fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=FIXTURES / "expenses_no_fleet.json",
            inbox_path=EMPTY_INBOX,
            dimo_env={},
            now=NOW,
        )
        by_id = {u["id"]: u for u in payload["units"]}
        c24 = by_id["corolla-2024"]
        due = glance.due_from_finance(c24["finance"])
        self.assertTrue(due["due"])
        self.assertEqual(due["ptp"]["amount"], 460.6)
        self.assertEqual(due["ptp"]["due"], "2026-08-21")
        self.assertEqual(due["past_due"], 921.2)
        self.assertFalse(by_id["m3-2020"]["glance"]["due"])
        self.assertTrue(c24["glance"]["due"])
        self.assertTrue(c24["glance"]["available"])
        self.assertEqual(c24["glance"]["turo_line"], "0 trips · watching 15m")
        self.assertIsNone(c24["glance"]["soc"])

    def test_inbox_status_not_repeated_on_cards(self) -> None:
        status = (
            "Watching Gmail cvolkern@gmail.com after:2026/08/18 "
            "from:(turo.com OR mail.turo.com OR transactional.turo.com); "
            "0 trip events. Payout destination is X Money."
        )
        units = []
        for uid, year, model, role in (
            ("m3-2020", 2020, "Model 3", "personal"),
            ("m3-2022", 2022, "Model 3", "turo"),
            ("corolla-2022", 2022, "Corolla", "turo"),
            ("corolla-2024", 2024, "Corolla", "turo"),
        ):
            units.append(
                {
                    "id": uid,
                    "identity": {
                        "year": year,
                        "make": "Tesla" if "m3" in uid else "Toyota",
                        "model": model,
                        "role": role,
                        "vin": None,
                    },
                    "dimo": {
                        "status": "ok",
                        "soc": 80 if uid == "m3-2020" else None,
                        "range": 333 if uid == "m3-2020" else None,
                        "odometer": 10000,
                        "last_seen": (
                            "2026-07-01T00:00:00Z"
                            if uid == "m3-2022"
                            else "2026-08-18T03:18:00Z"
                        ),
                        "location": {"lat": 27.95, "lon": -82.46},
                    },
                    "turo": {"bookings": [], "inbox_status": status},
                    "finance": {},
                }
            )
        html = glance.render_cards_html(
            units, now=NOW, inbox_status=status, poll_interval_s=900
        )
        self.assertEqual(html.count(status), 1)
        self.assertEqual(html.count("No upcoming trips"), 4)
        self.assertNotIn("0 trips · watching 15m", html[html.find('<div class="grid">') :])
        self.assertIn('class="glance"', html)
        self.assertIn("available", html)
        self.assertIn('data-freshness="dead"', html)
        self.assertIn("48d ago", html)
        self.assertIn("https://maps.google.com/?q=27.95,-82.46", html)
        self.assertIn("27.95, -82.46", html)
        self.assertNotIn("27.95000000", html)
        self.assertNotIn("Kia", html)
        self.assertNotIn("Spark", html)
        self.assertNotIn("Jessica", html)
        self.assertNotIn("Mercury", html)
        self.assertNotIn("TREAD", html)
        self.assertNotIn("SafeWheels", html)
        cards = html[html.find('<div class="grid">') : html.find("turo-inbox")]
        self.assertNotIn(status, cards)

    def test_2022_m3_july_last_seen_is_dead(self) -> None:
        unit = {
            "id": "m3-2022",
            "identity": {
                "year": 2022,
                "make": "Tesla",
                "model": "Model 3",
                "role": "turo",
                "vin": "5YJ3E1EA6NF289917",
            },
            "dimo": {
                "status": "ok",
                "soc": 22.2,
                "range": 80.0,
                "odometer": 44120,
                "last_seen": "2026-07-01T12:00:00Z",
            },
            "turo": {"bookings": []},
            "finance": {},
        }
        g = glance.glance_for_unit(unit, now=NOW)
        self.assertEqual(g["freshness"], "dead")
        self.assertEqual(g["soc"], 22)
        self.assertEqual(g["hero"], "22%")
        self.assertTrue(g["available"])
        html = glance.render_unit_card_html(unit, now=NOW)
        self.assertIn('data-freshness="dead"', html)
        self.assertIn("class=\"chip err\">dead", html.replace("chip err", "chip err"))
        self.assertIn("SoC 22%", html)
        self.assertNotIn("44120", html)  # raw km must not appear

    def test_costs_lead_with_ptp_and_hide_empty_fleet_note(self) -> None:
        unit = {
            "id": "corolla-2024",
            "identity": {
                "year": 2024,
                "make": "Toyota",
                "model": "Corolla",
                "role": "turo",
                "vin": "5YFB4MDE9RP121896",
            },
            "dimo": {"status": "ok", "soc": None, "odometer": 12000, "last_seen": NOW},
            "turo": {"bookings": []},
            "finance": {
                "sheet_lines": [],
                "note": "Fleet tab not in expenses snapshot or no lines for this unit.",
                "portal": {
                    "stale": True,
                    "amount_due": 1095.88,
                    "past_due": 921.2,
                    "promise_to_pay": {"amount": 460.6, "due": "2026-08-21"},
                    "apr_pct": 10.18,
                },
            },
        }
        html = glance.render_unit_card_html(unit, now=NOW)
        ptp_at = html.find("PTP $460.60 due 2026-08-21")
        due_at = html.find("Due $1,095.88")
        past_at = html.find("Past due $921.20")
        self.assertGreater(ptp_at, 0)
        self.assertGreater(due_at, ptp_at)
        self.assertGreater(past_at, due_at)
        self.assertNotIn("Fleet tab not", html)
        self.assertNotIn("SoC", html)

    def test_coord_pair_and_speed_mph(self) -> None:
        self.assertEqual(
            glance.coord_pair({"lat": 27.95, "lon": -82.46}),
            "27.95, -82.46",
        )
        self.assertEqual(
            glance.coord_pair({"latitude": 26.672633, "longitude": -82.027344}),
            "26.67263, -82.02734",
        )
        self.assertIsNone(glance.coord_pair(None))
        self.assertEqual(glance.speed_mph(1.609344), 1.0)
        self.assertEqual(glance.speed_mph(0), 0.0)
        self.assertIsNone(glance.speed_mph(None))

    def test_index_html_glance_contract(self) -> None:
        html = (PKG / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="glance"', html)
        self.assertIn("function renderGlanceCell", html)
        turo_fn = html[html.find("function scheduleStrip") : html.find("function moneyStrip")]
        self.assertGreater(html.find("function scheduleStrip"), 0)
        self.assertGreater(html.find("function vehicleStrip"), 0)
        self.assertGreater(html.find("function moneyStrip"), 0)
        self.assertNotIn("inbox_status", turo_fn)
        self.assertIn("<details", html)
        self.assertIn("static/fleet/tesla-model-3-2020.jpg", html)
        self.assertIn("static/fleet/rivian-r1s-2023.jpg", html)
        self.assertIn("static/fleet/tesla-model-3-2022.jpg", html)
        self.assertIn("maps.google.com", html)
        self.assertIn('id="page-loader"', html)
        self.assertIn('id="fleet-map"', html)
        self.assertIn("setInterval(load, 30 * 1000)", html)
        self.assertIn("max-width: 390px", html)
        self.assertIn("grid-template-columns: repeat(5, minmax(0, 1fr))", html)
        self.assertIn("grid-template-columns: 1fr 1fr", html)
        self.assertIn("min-height: 44px", html)
        self.assertNotIn("SafeWheels", html)
        self.assertNotIn("Mercury", html)
        self.assertIn('id="nav-fcc"', html)
        self.assertIn('id="nav-turo"', html)
        self.assertIn('href="https://turo.com"', html)
        self.assertNotIn("<iframe", html.lower())
        self.assertNotIn("TREAD", html.replace("not TREAD", ""))

    def test_photos_are_chris_listing_stills(self) -> None:
        static = PKG / "static" / "fleet"
        for name in (
            "tesla-model-3.jpg",
            "tesla-model-3-2020.jpg",
            "tesla-model-3-2022.jpg",
            "rivian-r1s-2023.jpg",
            "toyota-corolla-2022.jpg",
            "toyota-corolla-2024.jpg",
        ):
            path = static / name
            self.assertTrue(path.is_file(), path)
            self.assertGreater(path.stat().st_size, 10_000)
        self.assertEqual(
            glance.photo_for({"id": "m3-2020"}),
            "/static/fleet/tesla-model-3-2020.jpg",
        )
        self.assertEqual(
            glance.photo_for({"id": "m3-2022"}),
            "/static/fleet/tesla-model-3-2022.jpg",
        )
        self.assertEqual(
            glance.photo_for({"id": "r1s-2023"}),
            "/static/fleet/rivian-r1s-2023.jpg",
        )
        self.assertEqual(
            glance.photo_for({"id": "corolla-2024"}),
            "/static/fleet/toyota-corolla-2024.jpg",
        )
        self.assertEqual(
            glance.photo_for({"id": "corolla-2022"}),
            "/static/fleet/toyota-corolla-2022.jpg",
        )
        self.assertNotEqual(
            glance.photo_for({"id": "m3-2020"}),
            glance.photo_for({"id": "m3-2022"}),
        )

    def test_card_html_follows_roster_make_pairs(self) -> None:
        payload = fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=FIXTURES / "expenses_no_fleet.json",
            inbox_path=EMPTY_INBOX,
            dimo_env={},
            now=NOW,
        )
        html = glance.render_cards_html(
            payload["units"], now=NOW, inbox_status=None
        )
        ids = []
        needle = 'data-unit="'
        start = 0
        while True:
            at = html.find(needle, start)
            if at < 0:
                break
            end = html.find('"', at + len(needle))
            ids.append(html[at + len(needle) : end])
            start = end
        self.assertEqual(
            ids,
            ["m3-2020", "m3-2022", "corolla-2022", "corolla-2024", "r1s-2023"],
        )
        index = (PKG / "index.html").read_text(encoding="utf-8")
        grid = index[index.find(".grid {") : index.find(".card {")]
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", grid)


if __name__ == "__main__":
    unittest.main()
