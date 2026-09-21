from __future__ import annotations

import unittest

from models import SMS_MAX_CHARS, Lead
from outreach_copy import render_sms, render_voice_task, sale_location_phrase

KEVIN_LOCATION = "Near Burnt Store, Cape Coral, FL 33991"


def _lead(**kwargs: object) -> Lead:
    base = dict(
        id="lead-kevin",
        folder_id="set-kevin",
        year="2015",
        make="Jeep",
        location=KEVIN_LOCATION,
        phone="2395550101",
        state="new",
    )
    base.update(kwargs)
    return Lead(**base)  # type: ignore[arg-type]


class SaleLocationPhraseTests(unittest.TestCase):
    def test_proximity_drops_on_state_and_zip(self) -> None:
        self.assertEqual(
            sale_location_phrase(KEVIN_LOCATION),
            "near Burnt Store in Cape Coral",
        )
        self.assertEqual(
            sale_location_phrase("Near Burnt Store, Cape Coral, Florida 33991"),
            "near Burnt Store in Cape Coral",
        )

    def test_road_style_keeps_on(self) -> None:
        self.assertEqual(sale_location_phrase("Burnt Store Rd"), "on Burnt Store Rd")
        self.assertEqual(sale_location_phrase("del prado"), "on del prado")

    def test_road_with_city_state_zip_keeps_road_only(self) -> None:
        self.assertEqual(
            sale_location_phrase("Burnt Store Rd, Cape Coral, FL 33991"),
            "on Burnt Store Rd",
        )

    def test_coords_and_empty_fallback(self) -> None:
        self.assertEqual(sale_location_phrase(""), "on the roadside")
        self.assertEqual(sale_location_phrase("26.639011,-82.039046"), "on the roadside")
        self.assertEqual(sale_location_phrase("the roadside"), "on the roadside")


class OutboundCopyTests(unittest.TestCase):
    def test_sms_proximity_and_855_rules(self) -> None:
        lead = _lead()
        body = render_sms(lead)
        self.assertIn("for sale near Burnt Store in Cape Coral", body)
        self.assertNotIn("on near", body.lower())
        self.assertNotIn("33991", body)
        self.assertNotIn("Florida", body)
        self.assertNotRegex(body, r"\bFL\b")
        self.assertEqual(lead.location, KEVIN_LOCATION)
        self.assertTrue(body.startswith("Hi, this is Alexandra with Panamerica Auto in Cape Coral."))
        self.assertNotIn("STOP", body)
        self.assertNotIn("Turo", body)
        self.assertIn("owner-exit", body)
        self.assertIn("https://www.panamericafleet.com/plans", body)
        self.assertLessEqual(len(body), SMS_MAX_CHARS)

    def test_sms_road_style(self) -> None:
        lead = _lead(location="Burnt Store Rd")
        body = render_sms(lead)
        self.assertIn("for sale on Burnt Store Rd", body)
        self.assertNotIn("on near", body.lower())
        self.assertEqual(lead.location, "Burnt Store Rd")

    def test_voice_proximity_equivalent(self) -> None:
        lead = _lead()
        task = render_voice_task(lead)
        self.assertIn("listed for sale near Burnt Store in Cape Coral", task)
        self.assertNotIn("on near", task.lower())
        self.assertNotIn("33991", task)
        self.assertEqual(lead.location, KEVIN_LOCATION)
        self.assertIn("owner-exit", task)
        self.assertIn("www.panamericafleet.com/plans", task)
        self.assertIn("Do not mention Turo", task)
        self.assertEqual(task.lower().count("turo"), 1)

    def test_voice_road_style(self) -> None:
        lead = _lead(location="Burnt Store Rd")
        task = render_voice_task(lead)
        self.assertIn("listed for sale on Burnt Store Rd", task)
        self.assertEqual(lead.location, "Burnt Store Rd")
