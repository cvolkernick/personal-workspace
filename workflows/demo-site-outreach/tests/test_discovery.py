from __future__ import annotations

import sys
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from discovery import apply_discovery, parse_inbound, render_discovery_email  # noqa: E402
from models import TEMPLATE_SLOTS, Lead  # noqa: E402


def _lead() -> Lead:
    return Lead(
        id="place_oak_bakery",
        place_id="place_oak_bakery",
        name="Oak Street Bakery",
        address="100 Oak St, Austin, TX 78701",
        phone="512-555-0101",
        email="oak@example.com",
        hours="Tue-Sun 7am-3pm",
        category="bakery",
        rating=4.6,
        neighborhood="Austin",
    )


class DiscoveryTests(unittest.TestCase):
    def test_email_fills_personalization_tokens(self) -> None:
        email = render_discovery_email(
            _lead(), physical_address="123 Example St, Austin, TX 78701"
        )
        self.assertEqual(email["to"], "oak@example.com")
        self.assertIn("Oak Street Bakery", email["subject"])
        self.assertIn("Oak Street Bakery", email["body"])
        self.assertIn("Austin", email["body"])
        self.assertIn("bakery", email["body"])
        self.assertIn("4.6", email["body"])
        self.assertIn("best number to text the preview link", email["body"].lower())
        self.assertIn("reply STOP", email["body"])
        self.assertIn("123 Example St, Austin, TX 78701", email["body"])
        self.assertIn("Alexandra", email["body"])

    def test_numbered_reply_fills_slots_and_consent(self) -> None:
        text = (
            "1. Croissants and coffee\n"
            "2. Tue-Sun 7-3\n"
            "3. Call 512-555-0101\n"
            "4. Baked same morning\n"
            "5. No logo yet\n"
            "Best number to text the preview: 512-555-0188\n"
        )
        parsed = parse_inbound(text)
        lead = apply_discovery(_lead(), parsed)
        for slot in TEMPLATE_SLOTS:
            self.assertTrue(lead.discovery.get(slot), slot)
        self.assertEqual(lead.discovery["services"], "Croissants and coffee")
        self.assertEqual(lead.sms_number, "5125550188")
        self.assertTrue(lead.sms_consent)
        self.assertTrue(lead.discovery_complete())

    def test_opt_out_and_interest_flags(self) -> None:
        self.assertTrue(parse_inbound("Please STOP emailing me")["opt_out"])
        self.assertTrue(parse_inbound("Not interested, thanks")["not_interested"])
        self.assertTrue(parse_inbound("Looks great, how much to go live?")["interest"])
