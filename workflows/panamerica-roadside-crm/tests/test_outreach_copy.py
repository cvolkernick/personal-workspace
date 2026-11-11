from __future__ import annotations

import unittest

from unittest import mock

from models import COPY_VERSION, SMS_MAX_CHARS, Lead
from outreach_copy import (
    SMS_DRAFT,
    SMS_VARIANT_A,
    SMS_VARIANT_B,
    SMS_VARIANT_B_APPROVED,
    _fit_sms,
    assign_sms_variant,
    render_sms,
    render_voice_task,
    sale_location_phrase,
)

# Approved 2026-11-10 8:44 PM ET. {location} maps onto {location_slot}.
APPROVED_VARIANT_A = (
    "Hi, this is Alexandra with Panamerica Auto in Cape Coral. "
    "I saw your {car} for sale {location_slot} — have you considered renting it out "
    "instead of selling? We manage cars for owners and handle everything: "
    "listing, guests, cleaning, maintenance, and you keep 80–90% of every trip. "
    "Details at https://www.panamericafleet.com/management. Worth a 10-min chat?"
)

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
        self.assertIn("80–90%", body)
        self.assertIn("https://www.panamericafleet.com/management", body)
        self.assertNotIn("owner-exit", body.lower())
        self.assertNotIn("take over the payments", body.lower())
        self.assertNotIn("/plans", body)
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
        self.assertIn("monthly income", task)
        self.assertNotIn("owner-exit", task.lower())
        self.assertNotIn("take over the payments", task.lower())
        self.assertNotIn("panamericafleet.com", task)
        self.assertNotIn("80–90%", task)
        self.assertIn("Do not mention Turo", task)
        self.assertEqual(task.lower().count("turo"), 1)

    def test_voice_road_style(self) -> None:
        lead = _lead(location="Burnt Store Rd")
        task = render_voice_task(lead)
        self.assertIn("listed for sale on Burnt Store Rd", task)
        self.assertEqual(lead.location, "Burnt Store Rd")

    def test_render_sms_switches_variant_and_defaults_to_champion(self) -> None:
        champion = render_sms(_lead(sms_variant_id="A"))
        unset = render_sms(_lead())
        challenger = render_sms(_lead(sms_variant_id="B"))
        self.assertEqual(unset, champion)
        self.assertTrue(champion.startswith("Hi, this is Alexandra with Panamerica Auto in Cape Coral."))
        self.assertNotIn("CHALLENGER_UNAPPROVED", champion)
        self.assertIn("CHALLENGER_UNAPPROVED", challenger)
        self.assertIn("2015 Jeep", challenger)
        self.assertIn("near Burnt Store in Cape Coral", challenger)
        self.assertNotEqual(champion, challenger)


class ApprovedVariantATests(unittest.TestCase):
    def test_draft_is_the_approved_text_on_existing_slots(self) -> None:
        self.assertIs(SMS_VARIANT_A, SMS_DRAFT)
        self.assertEqual(SMS_DRAFT, APPROVED_VARIANT_A)
        self.assertIn("\u2014", SMS_DRAFT)  # em dash, as approved
        self.assertIn("80\u201390%", SMS_DRAFT)  # en dash, as approved
        self.assertNotIn("{location}", SMS_DRAFT.replace("{location_slot}", ""))
        self.assertFalse(SMS_VARIANT_B_APPROVED)
        self.assertIn("CHALLENGER_UNAPPROVED", SMS_VARIANT_B)
        self.assertIn("{location_slot}", SMS_VARIANT_B)
        self.assertEqual(COPY_VERSION, "v2")

    def test_unknown_location_still_says_on_the_roadside(self) -> None:
        body = render_sms(_lead(location=""))
        self.assertIn("for sale on the roadside", body)
        self.assertIn("80–90%", body)
        self.assertIn("https://www.panamericafleet.com/management", body)

    def test_over_length_fallback_drops_owner_exit(self) -> None:
        long_car = "2015 Jeep Wrangler Unlimited Sahara"
        slot = "near Burnt Store in Cape Coral"
        with mock.patch("outreach_copy.SMS_MAX_CHARS", 300):
            body = _fit_sms(SMS_VARIANT_A, long_car, slot)
        self.assertLessEqual(len(body), 300)
        self.assertTrue(body.startswith("Hi, this is Alexandra with Panamerica Auto in Cape Coral."))
        self.assertIn("https://www.panamericafleet.com/management", body)
        self.assertNotIn("owner-exit", body.lower())
        self.assertNotIn("/plans", body)
        self.assertNotIn("80–90%", body)
        with mock.patch("outreach_copy.SMS_MAX_CHARS", 40):
            challenger = _fit_sms(SMS_VARIANT_B, long_car, slot)
        self.assertLessEqual(len(challenger), 40)
        self.assertNotIn("panamericafleet.com", challenger)


class AssignSmsVariantTests(unittest.TestCase):
    def test_unapproved_b_is_always_champion(self) -> None:
        arm, mode = assign_sms_variant("lead-set-corolla")
        self.assertEqual((arm, mode), ("A", "champion_only"))
        self.assertEqual(assign_sms_variant("lead-set-corolla"), ("A", "champion_only"))

    def test_split_is_deterministic_and_covers_both_arms(self) -> None:
        with mock.patch("outreach_copy.SMS_VARIANT_B_APPROVED", True):
            seen: set[str] = set()
            first, mode = assign_sms_variant("lead-7")
            self.assertEqual(mode, "split")
            self.assertEqual(assign_sms_variant("lead-7"), (first, "split"))
            for n in range(64):
                arm, _ = assign_sms_variant(f"lead-{n}")
                seen.add(arm)
            self.assertEqual(seen, {"A", "B"})
