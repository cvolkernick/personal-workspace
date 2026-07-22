#!/usr/bin/env python3
"""Structural checks for the personal portfolio MVP."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
CSS = ROOT / "static" / "styles.css"
JS = ROOT / "static" / "app.js"
SERVER = ROOT / "server.py"
README = ROOT / "README.md"

REQUIRED_SECTIONS = ["#about", "#master-plan", "#work", "#interests", "#wins", "#targets"]


class PersonalPortfolioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")

    def test_project_files_exist(self) -> None:
        for path in (INDEX, CSS, JS, SERVER, README):
            self.assertTrue(path.is_file(), f"missing {path}")

    def test_title_and_identity(self) -> None:
        self.assertIn("Personal Portfolio", self.html)
        self.assertIn("Master plan", self.html)
        self.assertRegex(self.html, r"<title>[^<]*Portfolio")

    def test_section_anchors(self) -> None:
        for href in REQUIRED_SECTIONS:
            self.assertIn(f'href="{href}"', self.html)
            sid = href.lstrip("#")
            self.assertIn(f'id="{sid}"', self.html)

    def test_master_plan_outline(self) -> None:
        self.assertIn("master-plan", self.html)
        self.assertIn("Command center", self.html)
        self.assertIn("Wealth systems", self.html)

    def test_assets_linked(self) -> None:
        self.assertIn("static/styles.css", self.html)
        self.assertIn("static/app.js", self.html)

    def test_server_entrypoint(self) -> None:
        text = SERVER.read_text(encoding="utf-8")
        self.assertIn("TCPServer", text)
        self.assertIn("DEFAULT_PORT", text)

    def test_readme_verify(self) -> None:
        readme = README.read_text(encoding="utf-8")
        self.assertIn("unittest", readme)


if __name__ == "__main__":
    unittest.main()
