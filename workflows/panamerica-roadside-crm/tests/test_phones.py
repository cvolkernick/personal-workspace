from __future__ import annotations

import unittest

from phones import (
    PHONE_UNKNOWN,
    e164,
    extract_phones,
    first_phone,
    normalize_phone,
    phone_or_unknown,
)

KEVIN_SIGN = "FOR SALE 2015 Jeep Wrangler $16,500 Call 239-464-8445"
KEVIN_GPS = "26.639011, -82.039046"


class PhoneTests(unittest.TestCase):
    def test_normalize_us_forms(self) -> None:
        for raw in ("239-555-0101", "(239) 555-0101", "239.555.0101", "+1 239 555 0101", "12395550101"):
            self.assertEqual(normalize_phone(raw), "2395550101", raw)

    def test_rejects_short_and_invalid(self) -> None:
        self.assertEqual(normalize_phone("555-0101"), "")
        self.assertEqual(normalize_phone("023-555-0101"), "")
        self.assertEqual(normalize_phone(""), "")
        self.assertEqual(normalize_phone(PHONE_UNKNOWN), "")

    def test_extract_from_sign_text(self) -> None:
        text = "FOR SALE 2018 Toyota Corolla $8500 Call 239-555-0101 after 5"
        self.assertEqual(extract_phones(text), ["2395550101"])
        self.assertEqual(first_phone(text), "2395550101")
        self.assertEqual(e164("239-555-0101"), "+12395550101")

    def test_dashed_and_parenthesized_preferred(self) -> None:
        self.assertEqual(first_phone("Call 239-464-8445"), "2394648445")
        self.assertEqual(first_phone("Call (239) 464-8445"), "2394648445")
        self.assertEqual(first_phone("Call 239.464.8445"), "2394648445")

    def test_decimals_and_latlong_are_not_phones(self) -> None:
        self.assertEqual(extract_phones("26.639011"), [])
        self.assertEqual(extract_phones("-82.039046"), [])
        self.assertEqual(extract_phones("26.639011, -82.039046"), [])
        self.assertEqual(extract_phones("26.639011,-82.039046"), [])
        self.assertEqual(phone_or_unknown(KEVIN_GPS), PHONE_UNKNOWN)
        self.assertEqual(phone_or_unknown("26.639011"), PHONE_UNKNOWN)
        self.assertNotRegex(phone_or_unknown(KEVIN_GPS), r"^\d{10}$")

    def test_kevin_sign_plus_coordinate_screenshot(self) -> None:
        blob = f"{KEVIN_SIGN}\n{KEVIN_GPS}"
        self.assertEqual(extract_phones(blob), ["2394648445"])
        self.assertEqual(first_phone(blob), "2394648445")
        self.assertEqual(phone_or_unknown(blob), "2394648445")
