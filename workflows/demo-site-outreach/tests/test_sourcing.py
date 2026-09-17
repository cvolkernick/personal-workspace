from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from adapters import FakePlaces  # noqa: E402
from sourcing import source_leads  # noqa: E402
from store import FileStore  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "places_listings.json").read_text())


class SourcingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = FileStore(Path(self.tmp.name) / "store.json")
        self.places = FakePlaces(FIXTURE["listings"])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_filters_website_and_suppression_and_keeps_required_fields(self) -> None:
        self.store.suppress(["phone:5125550111"], "opt-out")
        leads = source_leads(
            self.store, self.places, geo="Austin, TX", category="", limit=10
        )
        names = {lead.name for lead in leads}
        self.assertIn("Oak Street Bakery", names)
        self.assertIn("River Lawn Co", names)
        self.assertIn("Cash Only Auto", names)
        self.assertNotIn("Pixel Plumbing", names)
        self.assertNotIn("Silent Salon", names)
        oak = next(lead for lead in leads if lead.name == "Oak Street Bakery")
        self.assertEqual(oak.state, "sourced")
        self.assertEqual(oak.address, "100 Oak St, Austin, TX 78701")
        self.assertEqual(oak.phone, "512-555-0101")
        self.assertEqual(oak.hours, "Tue-Sun 7am-3pm")
        self.assertEqual(oak.category, "bakery")
        self.assertEqual(oak.website, "")

    def test_category_filter(self) -> None:
        leads = source_leads(
            self.store, self.places, geo="Austin, TX", category="bakery", limit=10
        )
        self.assertEqual([lead.name for lead in leads], ["Oak Street Bakery"])

    def test_dedupes_existing_place(self) -> None:
        first = source_leads(self.store, self.places, geo="Austin, TX", category="", limit=10)
        second = source_leads(self.store, self.places, geo="Austin, TX", category="", limit=10)
        self.assertGreater(len(first), 0)
        self.assertEqual(second, [])
