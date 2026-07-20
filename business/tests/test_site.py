"""Structural tests for the shipped Panamerica Auto website.

These tests load the real committed index.html from disk (the shipped artifact)
and assert presence of site identity + high-level service descriptions.
No re-rendering, no mocks of the HTML content.
"""

from __future__ import annotations

import unittest
from pathlib import Path


class PanamericaSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Resolve relative to this test file: business/tests/ -> business/
        cls.site_dir = Path(__file__).resolve().parent.parent
        cls.index_path = cls.site_dir / "index.html"
        cls.html = cls.index_path.read_text(encoding="utf-8")

    def test_index_exists_and_is_substantial(self) -> None:
        self.assertTrue(self.index_path.is_file(), "index.html must exist in business/")
        self.assertGreater(len(self.html), 2000, "index.html should be >2kB for a real site")

    def test_contains_site_name(self) -> None:
        self.assertIn("Panamerica Auto", self.html)
        self.assertIn("Panamerica <span>Auto</span>", self.html)  # brand in nav

    def test_features_high_level_services(self) -> None:
        # At least these high-level service descriptions must be present in the shipped HTML
        required = [
            "New &amp; Pre-Owned Sales",
            "Maintenance &amp; Repair",
            "Import &amp; Logistics",
            "Genuine Parts &amp; Accessories",
            "Fleet &amp; Commercial Solutions",
        ]
        for svc in required:
            self.assertIn(svc, self.html, f"Missing service description: {svc}")

    def test_has_core_sections(self) -> None:
        # Structural markers for hero/services/about/contact
        self.assertIn("id=\"services\"", self.html)
        self.assertIn("id=\"about\"", self.html)
        self.assertIn("id=\"contact\"", self.html)
        self.assertIn("<header>", self.html)
        self.assertIn("</footer>", self.html)

    def test_no_external_module_deps(self) -> None:
        # MVP is deliberately zero external script src or ESM/require bare imports.
        # Natural language ("import & logistics") and font stacks are fine.
        self.assertNotIn('src="', self.html)
        self.assertNotIn('<script src', self.html.lower())
        if '<script>' in self.html:
            # inspect only actual script content
            for chunk in self.html.split('<script>')[1:]:
                script_body = chunk.split('</script>')[0]
                self.assertNotIn(' import ', script_body)
                self.assertNotIn('require(', script_body)


if __name__ == "__main__":
    unittest.main()
