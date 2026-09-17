from __future__ import annotations

import sys
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from models import Lead  # noqa: E402
from sitegen import generate_site  # noqa: E402


class SitegenTests(unittest.TestCase):
    def test_generates_required_sections_from_discovery(self) -> None:
        lead = Lead(
            id="place_oak_bakery",
            place_id="place_oak_bakery",
            name="Oak Street Bakery",
            address="100 Oak St, Austin, TX 78701",
            phone="512-555-0101",
            email="oak@example.com",
            hours="listed hours ignored",
            category="bakery",
            rating=4.6,
            reviews=["Best croissants in town."],
            neighborhood="Austin",
            template_version="v1",
            sms_number="5125550188",
            sms_consent=True,
            discovery={
                "services": "Croissants, coffee, cakes",
                "hours": "Tue-Sun 7am-3pm",
                "contact": "Text 512-555-0188",
                "differentiator": "Baked same morning",
                "photos": "No logo yet",
            },
        )
        files = generate_site(lead)
        html = files["index.html"]
        self.assertIn("styles.css", files)
        self.assertIn("Oak Street Bakery", html)
        self.assertIn('id="services"', html)
        self.assertIn("Croissants", html)
        self.assertIn("Tue-Sun 7am-3pm", html)
        self.assertIn("Best croissants in town.", html)
        self.assertIn("maps.google.com/maps", html)
        self.assertIn('href="tel:+15125550188"', html)
        self.assertIn("Baked same morning", html)
        self.assertIn('data-template="v1"', html)
        self.assertGreater(len(html), 2000)
        self.assertNotIn("listed hours ignored", html)
