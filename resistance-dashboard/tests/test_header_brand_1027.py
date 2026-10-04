"""#1027: two-tone app header. Fit stays text white. Dash is accent blue.

The sign-in gate title and the document title stay plain FitDash.
No script reads or writes the header h1.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
JS_FILES = sorted((ROOT / "static").glob("*.js"))

HEADER_H1 = '<h1>💪 Fit<span class="brand-accent">Dash</span></h1>'
JS_NEEDLES = (
    "header h1",
    ".app-header h1",
    'querySelector("h1")',
    "querySelector('h1')",
    'querySelectorAll("h1")',
    "querySelectorAll('h1')",
)


def _slice(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find(end, i + len(start))
    if j < 0:
        return ""
    return text[i:j]


class HeaderBrand1027(unittest.TestCase):
    def test_app_header_splits_fit_and_dash(self):
        header = _slice(HTML, '<header class="app-header">', "</header>")
        self.assertIn(HEADER_H1, header)
        self.assertEqual(header.count("<h1>"), 1)
        self.assertNotIn("💪 FitDash", header)
        self.assertIn("💪", header)

    def test_sign_in_gate_and_title_stay_plain(self):
        gate = _slice(HTML, '<div class="auth-gate-brand">', '<p class="auth-gate-tagline">')
        self.assertIn("<h1>FitDash</h1>", gate)
        self.assertNotIn("brand-accent", gate)
        self.assertIn("<title>FitDash</title>", HTML)
        self.assertEqual(HTML.count("brand-accent"), 1)

    def test_dash_uses_accent_and_fit_inherits_text(self):
        self.assertIn("--text: #e7ecf3;", CSS)
        self.assertIn("--accent: #3d9cf0;", CSS)
        self.assertIn("color: var(--text);", CSS[CSS.find("body {") : CSS.find("body {") + 200])
        rule = CSS[CSS.find("header h1 .brand-accent") : CSS.find("header h1 .brand-accent") + 120]
        self.assertIn("color: var(--accent);", rule)
        desktop = CSS[CSS.find("header h1 {") : CSS.find("header h1 {") + 80]
        self.assertIn("font-size: 1.35rem;", desktop)
        self.assertNotIn("color:", desktop)
        mobile = CSS.rfind("header h1 {")
        phone = CSS[mobile : mobile + 60]
        self.assertIn("font-size: 1.2rem;", phone)

    def test_no_script_reads_header_h1(self):
        for path in JS_FILES:
            text = path.read_text(encoding="utf-8")
            for needle in JS_NEEDLES:
                self.assertNotIn(needle, text, f"{path.name} contains {needle}")

    def test_cache_busts_stylesheet_only(self):
        self.assertIn("/styles.css?v=brand-1027-1", HTML)
        self.assertIn("/styles.css?v=brand-1027-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v134"', SW)
        self.assertIn("/app.js?v=recipe-dishes-1069-1", HTML)
        self.assertIn("/app.js?v=recipe-dishes-1069-1", SW)
        self.assertIn("/history-sets.js?v=tonnage-1025-1", HTML)
        self.assertIn("/history-sets.js?v=tonnage-1025-1", SW)
        self.assertNotIn("styles.css?v=weekly-review-1033-1", HTML)
        self.assertNotIn("styles.css?v=weekly-review-1033-1", SW)
        self.assertNotIn("fitdash-shell-v130", SW)
