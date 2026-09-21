"""#769 FCC top nav has no Robinhood button."""

from __future__ import annotations

import unittest
from pathlib import Path

FCC = Path(__file__).resolve().parents[1]
HTML = (FCC / "index.html").read_text(encoding="utf-8")
SW = (FCC / "sw.js").read_text(encoding="utf-8")


class TestNavRobinhoodRemoved(unittest.TestCase):
    def test_anchor_script_and_file_are_gone(self) -> None:
        self.assertNotIn("nav-robinhood", HTML)
        self.assertNotIn("Open Robinhood", HTML)
        self.assertNotIn(">Robinhood</a>", HTML)
        self.assertIn('id="nav-coinbase"', HTML)
        self.assertIn('id="nav-expenses"', HTML)
        self.assertIn('id="link-fleet-chip"', HTML)
        start = HTML.find('class="broker-links"')
        end = HTML.find("</div>", start)
        broker = HTML[start:end]
        self.assertNotIn("Robinhood", broker)
        self.assertIn("Coinbase", broker)
        self.assertIn("Expenses", broker)
        self.assertIn("Fleet", broker)
        self.assertFalse((FCC / "nav-robinhood.js").exists())

    def test_sw_does_not_precache_nav_robinhood(self) -> None:
        self.assertNotIn("nav-robinhood", SW)
        self.assertIn("fcc-shell-v7", SW)
        self.assertNotIn("fcc-shell-v6", SW)


if __name__ == "__main__":
    unittest.main()
