from __future__ import annotations

import unittest

from phones import e164, extract_phones, first_phone, normalize_phone


class PhoneTests(unittest.TestCase):
    def test_normalize_us_forms(self) -> None:
        for raw in ("239-555-0101", "(239) 555-0101", "239.555.0101", "+1 239 555 0101", "12395550101"):
            self.assertEqual(normalize_phone(raw), "2395550101", raw)

    def test_rejects_short_and_invalid(self) -> None:
        self.assertEqual(normalize_phone("555-0101"), "")
        self.assertEqual(normalize_phone("023-555-0101"), "")
        self.assertEqual(normalize_phone(""), "")

    def test_extract_from_sign_text(self) -> None:
        text = "FOR SALE 2018 Toyota Corolla $8500 Call 239-555-0101 after 5"
        self.assertEqual(extract_phones(text), ["2395550101"])
        self.assertEqual(first_phone(text), "2395550101")
        self.assertEqual(e164("239-555-0101"), "+12395550101")
