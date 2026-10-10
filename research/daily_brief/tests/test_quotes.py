"""Quote of the day: curated list and a deterministic pick."""

from __future__ import annotations

import re
import unittest

from research.daily_brief import quotes

# Whole words only. A name like "Seneca" is not a ticker.
_TICKER_RE = re.compile(
    r"\b(?:AAPL|NVDA|TSLA|MSFT|AMZN|GOOG|META|BTC|ETH|SOL|USDC|USDT|USD)\b",
    re.I,
)
_MONEY_RE = re.compile(r"[$€£¥]|\b\d")


class TestCuratedQuotes(unittest.TestCase):
    def test_about_sixty_short_lines(self):
        self.assertGreaterEqual(len(quotes.QUOTES), 55)
        self.assertLessEqual(len(quotes.QUOTES), 65)
        for q in quotes.QUOTES:
            self.assertEqual(set(q), {"text", "by"})
            self.assertGreaterEqual(len(q["text"].split()), 3)
            self.assertLessEqual(len(q["text"]), 180)
            self.assertTrue(q["by"].strip())

    def test_no_money_amounts_or_tickers(self):
        for q in quotes.QUOTES:
            for part in (q["text"], q["by"]):
                self.assertIsNone(_MONEY_RE.search(part), part)
                self.assertIsNone(_TICKER_RE.search(part), part)

    def test_attribution_prefers_by_then_author(self):
        self.assertEqual(quotes.quote_attribution({"text": "Line", "by": "Ada"}), "Ada")
        self.assertEqual(quotes.quote_attribution({"text": "Line", "author": "Ada"}), "Ada")
        self.assertEqual(quotes.quote_attribution({"text": "Line", "by": "Ada", "author": "Other"}), "Ada")
        self.assertEqual(quotes.quote_attribution("A plain line"), "")

    def test_pick_is_stable_and_differs_by_edition(self):
        am = quotes.quote_for("2026-10-09", "am")
        again = quotes.quote_for("2026-10-09", "am")
        pm = quotes.quote_for("2026-10-09", "pm")
        self.assertEqual(am, again)
        self.assertNotEqual(am["text"], pm["text"])
        self.assertNotEqual(quotes.index_for("2026-10-09", "am"), quotes.index_for("2026-10-09", "pm"))
        self.assertEqual(quotes.quote_for("2026-10-08", "pm"), quotes.quote_for("2026-10-08", "pm"))

    def test_quote_for_returns_text_and_by(self):
        picked = quotes.quote_for("2026-10-09", "am")
        self.assertEqual(set(picked), {"text", "by"})
        self.assertEqual(picked, quotes.QUOTES[quotes.index_for("2026-10-09", "am")])
