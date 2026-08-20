#!/usr/bin/env python3
"""MiKrafts v1: card copy, honest empty catalog, ingest stub, isolation."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from ingest import (  # noqa: E402
    ingest_print,
    parse_email_body,
    render_catalog_cards,
    subject_is_new_print,
)

INDEX = ROOT / "index.html"
CATALOG_PAGE = ROOT / "catalog.html"
CATALOG_JS = ROOT / "static" / "catalog.js"
STYLES = ROOT / "static" / "styles.css"
ITEMS = ROOT / "catalog" / "items.json"
IMAGES = ROOT / "catalog" / "images"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "example-print.jpg"
VERCEL_JSON = ROOT / "vercel.json"
FITDASH_IGNORE = REPO / "resistance-dashboard" / "vercel-ignore-paths.txt"
FITDASH_VERCEL = REPO / "resistance-dashboard" / "vercel.json"

CARD_STRINGS = (
    "MiKrafts LLC",
    "3D Printing Services",
    "2526 NW 11th Street",
    "Cape Coral, FL 33993",
    "Mike Volkernick",
    "Owner",
    "MiKraftsLLC@gmail.com",
    "239-989-4878",
)

# Customer-facing surfaces only. Developer docs may mention local servers.
VISIBLE_FILES = (INDEX, CATALOG_PAGE, CATALOG_JS, STYLES, ROOT / "static" / "logo.svg")

HOST_RE = re.compile(r"localhost|127\.0\.0\.1|0\.0\.0\.0|192\.168\.\d+\.\d+")
DASHBOARD_PORTS = (":8790", ":8000", ":8765", ":8770", ":8780", ":8787", ":8796", ":8795")

DUMMY_PRINT_RE = re.compile(
    r"\b(benchy|dragon|dummy print|sample print|placeholder print|fake print)\b",
    re.I,
)


def _visible_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in VISIBLE_FILES)


class TestLandingCardCopy(unittest.TestCase):
    def test_card_contact_strings_on_landing(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        for text in CARD_STRINGS:
            self.assertIn(text, html, text)
        self.assertIn("Catalog", html)
        self.assertIn("catalog.html", html)
        self.assertIn("Custom", html)
        self.assertIn("Proofs of concept", html)
        self.assertIn("Products", html)

    def test_logo_and_split_palette(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        css = STYLES.read_text(encoding="utf-8")
        logo = (ROOT / "static" / "logo.svg").read_text(encoding="utf-8")
        self.assertIn("static/logo.svg", html)
        self.assertIn("card-hero-top", html)
        self.assertIn("card-hero-bottom", html)
        self.assertIn("--plum:", css)
        self.assertIn("--gold:", css)
        self.assertIn("--charcoal:", css)
        self.assertIn("MiKrafts", logo)
        self.assertIn("nozzle", logo.lower())


class TestHonestEmptyCatalog(unittest.TestCase):
    def test_live_items_json_is_empty_array(self) -> None:
        data = json.loads(ITEMS.read_text(encoding="utf-8"))
        self.assertEqual(data, [])

    def test_no_live_catalog_images(self) -> None:
        live = [
            p
            for p in IMAGES.iterdir()
            if p.is_file() and p.name != ".gitkeep" and not p.name.startswith(".")
        ]
        self.assertEqual(live, [])

    def test_catalog_page_ships_honest_empty(self) -> None:
        html = CATALOG_PAGE.read_text(encoding="utf-8")
        js = CATALOG_JS.read_text(encoding="utf-8")
        self.assertIn("No prints in the catalog yet.", html)
        self.assertIn('data-state="empty"', html)
        self.assertIn("catalog/items.json", js)
        self.assertIn("No prints in the catalog yet.", js)
        self.assertNotIn("catalog-card", html)
        self.assertIsNone(DUMMY_PRINT_RE.search(html))
        self.assertIsNone(DUMMY_PRINT_RE.search(js))
        self.assertNotIn("Example print", html)

    def test_empty_render_is_not_a_dummy_card(self) -> None:
        html = render_catalog_cards([])
        self.assertEqual(html, '<p class="catalog-empty">No prints in the catalog yet.</p>')
        self.assertNotIn("catalog-card", html)


class TestIngestStub(unittest.TestCase):
    def test_email_contract_helpers(self) -> None:
        self.assertTrue(subject_is_new_print("New print"))
        self.assertTrue(subject_is_new_print("RE: NEW PRINT — clip"))
        self.assertFalse(subject_is_new_print("invoice"))
        title, note = parse_email_body("Bracket\nBlack PETG, 0.2mm")
        self.assertEqual(title, "Bracket")
        self.assertEqual(note, "Black PETG, 0.2mm")

    def test_ingest_fixture_produces_one_catalog_card(self) -> None:
        self.assertTrue(FIXTURE.is_file(), FIXTURE)
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "catalog" / "images").mkdir(parents=True)
            (site / "catalog" / "items.json").write_text("[]\n", encoding="utf-8")
            row = ingest_print(
                FIXTURE,
                "Example print",
                "EXAMPLE — tests only. Not a live catalog row.",
                site_root=site,
                added="2026-08-20",
            )
            items = json.loads((site / "catalog" / "items.json").read_text(encoding="utf-8"))
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["title"], "Example print")
            self.assertEqual(items[0]["id"], row["id"])
            image_path = site / items[0]["image"]
            self.assertTrue(image_path.is_file(), image_path)
            self.assertEqual(image_path.suffix.lower(), ".jpg")
            self.assertGreater(image_path.stat().st_size, 100)
            html = render_catalog_cards(items)
            self.assertIn('class="catalog-card"', html)
            self.assertEqual(html.count("catalog-card"), 1)
            self.assertIn("Example print", html)
            self.assertIn(items[0]["image"], html)
            self.assertIn(FIXTURE.name.split(".")[0] or "example", FIXTURE.name)

        live = json.loads(ITEMS.read_text(encoding="utf-8"))
        self.assertEqual(live, [], "ingest must not write the live catalog")

    def test_example_lives_only_in_docs_and_tests(self) -> None:
        live = ITEMS.read_text(encoding="utf-8")
        self.assertNotIn("Example print", live)
        design = (ROOT / "docs" / "design.md").read_text(encoding="utf-8")
        self.assertIn("NOT live", design)
        self.assertIn("Example print", design)
        self.assertTrue(FIXTURE.is_file())


class TestNoRequiredPortsInVisibleCopy(unittest.TestCase):
    def test_no_port_numbers_in_user_visible_copy(self) -> None:
        text = _visible_text()
        self.assertIsNone(HOST_RE.search(text), HOST_RE.search(text))
        for port in DASHBOARD_PORTS:
            self.assertNotIn(port, text, port)


class TestIsolation(unittest.TestCase):
    def test_own_vercel_json_is_static(self) -> None:
        cfg = json.loads(VERCEL_JSON.read_text(encoding="utf-8"))
        self.assertIsNone(cfg.get("framework"))
        self.assertTrue(cfg.get("cleanUrls"))
        self.assertNotIn("ignoreCommand", cfg)
        self.assertNotIn("functions", cfg)

    def test_fitdash_ignore_build_file_unchanged(self) -> None:
        text = FITDASH_IGNORE.read_text(encoding="utf-8")
        prefixes = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertEqual(prefixes, ["resistance-dashboard/"])
        self.assertNotIn("mikrafts", text)
        fitdash_vercel = json.loads(FITDASH_VERCEL.read_text(encoding="utf-8"))
        self.assertEqual(
            fitdash_vercel.get("ignoreCommand"),
            "python3 scripts/vercel_ignore.py || exit 1",
        )

    def test_not_wired_into_personal_stack_nav(self) -> None:
        surfaces = [
            REPO / "orchestra" / "domains.py",
            REPO / "orchestra" / "index.html",
            REPO / "financial-command" / "index.html",
            REPO / "research" / "horizon" / "start.command",
            REPO / "resistance-dashboard" / "vercel.json",
            REPO / "deploy" / "path_unit_map.json",
        ]
        for path in surfaces:
            blob = path.read_text(encoding="utf-8")
            self.assertNotIn("mikrafts", blob.lower(), path)


if __name__ == "__main__":
    unittest.main()
