#!/usr/bin/env python3
"""Structural checks for the Panamerica Auto website MVP."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
CSS = ROOT / "static" / "styles.css"
JS = ROOT / "static" / "app.js"
SERVER = ROOT / "server.py"
README = ROOT / "README.md"

# High-level services expected on the MVP page (HTML may escape &)
REQUIRED_SERVICES = [
    "Vehicle sales",
    "Service &amp; maintenance",
    "Parts supply",
    "Fleet support",
    "Financing guidance",
    "Inspections &amp; prep",
]

REQUIRED_SECTIONS = ["#services", "#why", "#process", "#contact"]


class PanamericaAutoSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")

    def test_project_files_exist(self) -> None:
        for path in (INDEX, CSS, JS, SERVER, README):
            self.assertTrue(path.is_file(), f"missing {path.relative_to(ROOT)}")

    def test_title_and_brand(self) -> None:
        self.assertIn("Panamerica Auto", self.html)
        self.assertRegex(self.html, r"<title>[^<]*Panamerica Auto")

    def test_service_cards_present(self) -> None:
        for name in REQUIRED_SERVICES:
            self.assertIn(name, self.html, f"service missing: {name}")
        # data-service markers for the six cards
        markers = re.findall(r'data-service="([^"]+)"', self.html)
        self.assertEqual(len(markers), 6, f"expected 6 service cards, got {markers}")

    def test_nav_and_section_anchors(self) -> None:
        for href in REQUIRED_SECTIONS:
            self.assertIn(f'href="{href}"', self.html)
            section_id = href.lstrip("#")
            self.assertIn(f'id="{section_id}"', self.html)

    def test_contact_form_fields(self) -> None:
        for field_id in ("name", "email", "interest", "message"):
            self.assertIn(f'id="{field_id}"', self.html)
        self.assertIn('id="contact-form"', self.html)

    def test_assets_linked(self) -> None:
        self.assertIn('href="static/styles.css"', self.html)
        self.assertIn('src="static/app.js"', self.html)
        self.assertGreater(CSS.stat().st_size, 500)
        self.assertGreater(JS.stat().st_size, 200)

    def test_server_is_runnable_module(self) -> None:
        text = SERVER.read_text(encoding="utf-8")
        self.assertIn("def main", text)
        self.assertIn("SimpleHTTPRequestHandler", text)

    def test_readme_has_verify_steps(self) -> None:
        readme = README.read_text(encoding="utf-8")
        self.assertIn("## Run", readme)
        self.assertIn("## Verify", readme)
        self.assertIn("server.py", readme)


if __name__ == "__main__":
    unittest.main()
