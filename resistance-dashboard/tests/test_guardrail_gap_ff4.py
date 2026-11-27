"""Equal-width Guardrails tiles on tablet/desktop; phone layout stays v178 (CSS only)."""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
SW = (STATIC / "sw.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
SCALE = '.guardrail-grid > .guardrail-tile[data-guardrail="scale"]'


class GuardrailEqualWidthTest(unittest.TestCase):
    def test_default_grid_is_three_equal_columns(self):
        m = re.search(
            r"\.guardrail-grid \{\s*display: grid;\s*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\);",
            CSS,
        )
        self.assertIsNotNone(m)
        self.assertNotRegex(
            CSS,
            r"\.guardrail-grid \{\s*display: grid;\s*grid-template-columns: repeat\(4, minmax\(0, 1fr\)\);",
        )

    def test_scale_has_no_desktop_span_two(self):
        self.assertNotIn("grid-column: span 2", CSS)
        # The only Scale column rule is inside the 560px query (full row).
        outside = re.sub(
            r"@media \(max-width: 560px\) \{.*?\n\}",
            "",
            CSS,
            flags=re.S,
        )
        self.assertNotIn(SCALE, outside)

    def test_scale_full_row_and_two_columns_on_mobile(self):
        m = re.search(
            r"@media \(max-width: 560px\) \{\s*"
            + re.escape(SCALE)
            + r"\s*\{\s*grid-column:\s*1 / -1;",
            CSS,
        )
        self.assertIsNotNone(m)
        m2 = re.search(
            r"@media \(max-width: 560px\) \{\s*\.guardrail-grid \{\s*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);",
            CSS,
        )
        self.assertIsNotNone(m2)

    def test_phase_check_still_full_row(self):
        self.assertRegex(CSS, r"\.guardrail-tile\.phase-check-tile \{\s*grid-column: 1 / -1;")

    def test_pins(self):
        self.assertIn('const CACHE = "fitdash-shell-v180"', SW)
        self.assertIn("/styles.css?v=guardrail-eq-1", HTML)
        self.assertIn("/styles.css?v=guardrail-eq-1", SW)


if __name__ == "__main__":
    unittest.main()
