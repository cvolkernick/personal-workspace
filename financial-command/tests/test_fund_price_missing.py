"""Fund views label a null market value or sleeve weight as price missing."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FCC = ROOT / "financial-command"


class TestFundPriceMissingCopy(unittest.TestCase):
    def test_money_tab_and_fund_views_say_price_missing(self):
        index = (FCC / "index.html").read_text(encoding="utf-8")
        position = (FCC / "position.html").read_text(encoding="utf-8")
        bias = (FCC / "bias-spectrum.html").read_text(encoding="utf-8")
        self.assertIn(': "price missing"', index)
        self.assertIn("Last marks carried forward. Not valued at cost.", index)
        self.assertIn("Stale as of ", index)
        self.assertIn("stale as of ", index)
        self.assertIn("Sleeve weights price missing. Not valued at cost.", index)
        self.assertIn('fmHealthLabel = "price missing"', index)
        self.assertIn("price missing", position)
        self.assertIn('st.value_label === "price missing"', position)
        self.assertIn('data.price_missing ? "price missing"', bias)
        self.assertIn('? " · price missing"', bias)
        for page in (index, position, bias):
            self.assertNotIn("average_buy_price", page)


if __name__ == "__main__":
    unittest.main()
