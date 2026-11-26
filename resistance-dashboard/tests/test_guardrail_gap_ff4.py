"""FF-4 gap fix: no empty cell next to Scale once Progression is off screen (CSS only)."""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
SW = (STATIC / "sw.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
SCALE = '.guardrail-grid > .guardrail-tile[data-guardrail="scale"]'


class GuardrailGapTest(unittest.TestCase):
    def test_scale_spans_two_on_desktop(self):
        self.assertRegex(CSS, re.escape(SCALE) + r"\s*\{\s*grid-column:\s*span 2;")

    def test_scale_full_row_on_mobile(self):
        m = re.search(r"@media \(max-width: 560px\) \{\s*" + re.escape(SCALE) + r"\s*\{\s*grid-column:\s*1 / -1;", CSS)
        self.assertIsNotNone(m)

    def test_phase_check_still_full_row(self):
        self.assertRegex(CSS, r"\.guardrail-tile\.phase-check-tile \{\s*grid-column: 1 / -1;")

    def test_pins(self):
        self.assertIn('const CACHE = "fitdash-shell-v178"', SW)
        self.assertIn("/styles.css?v=guardrail-gap-1", HTML)
        self.assertIn("/styles.css?v=guardrail-gap-1", SW)


if __name__ == "__main__":
    unittest.main()
