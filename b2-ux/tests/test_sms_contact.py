"""Tests for allowlisted SMS → B2 ingest."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from b2_kb.sms_contact import (  # noqa: E402
    addresses_match,
    ingest_messages,
    normalize_address,
    redact_body,
)


class TestNormalize(unittest.TestCase):
    def test_us_10_digit(self):
        self.assertEqual(normalize_address("(239) 443-7563"), "+12394437563")
        self.assertEqual(normalize_address("2394437563"), "+12394437563")

    def test_e164(self):
        self.assertEqual(normalize_address("+1 239 443 7563"), "+12394437563")

    def test_match(self):
        self.assertTrue(addresses_match("(239) 443-7563", "+12394437563"))
        self.assertFalse(addresses_match("+12394437563", "+15551234567"))


class TestRedact(unittest.TestCase):
    def test_otp_phrase(self):
        t = redact_body("Your verification code is 482910")
        self.assertIn("[OTP redacted]", t)
        self.assertNotIn("482910", t)

    def test_normal_text_kept(self):
        t = redact_body("See you at 6 for dinner about project 42")
        self.assertEqual(t, "See you at 6 for dinner about project 42")


class TestIngest(unittest.TestCase):
    def test_allowlist_and_dedupe(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            meta = vault / "inbox" / "meta"
            meta.mkdir(parents=True)
            (meta / "sms_contacts.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "contacts": [
                            {
                                "id": "alex-djahankhah",
                                "display_name": "Alex Djahankhah",
                                "addresses": ["+12394437563"],
                                "enabled": True,
                                "redact_otp": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            batch = {
                "messages": [
                    {
                        "address": "(239) 443-7563",
                        "body": "Hello from Alex",
                        "direction": "in",
                        "ts": "2026-08-06T12:00:00Z",
                    },
                    {
                        "address": "+15550001111",
                        "body": "stranger",
                        "direction": "in",
                        "ts": "2026-08-06T12:01:00Z",
                    },
                    {
                        "address": "2394437563",
                        "body": "Your code is 998877",
                        "direction": "in",
                        "ts": "2026-08-06T12:02:00Z",
                    },
                ]
            }
            r1 = ingest_messages(batch, vault)
            self.assertTrue(r1["ok"])
            self.assertEqual(r1["accepted"], 2)
            self.assertEqual(r1["rejected"], 1)

            cap = vault / "inbox" / "captures" / "sms" / "alex-djahankhah.md"
            self.assertTrue(cap.is_file())
            body = cap.read_text(encoding="utf-8")
            self.assertIn("Hello from Alex", body)
            self.assertIn("[OTP redacted]", body)
            self.assertNotIn("998877", body)
            self.assertNotIn("stranger", body)

            r2 = ingest_messages(batch, vault)
            self.assertEqual(r2["accepted"], 0)
            self.assertEqual(r2["duplicates"], 2)
            self.assertEqual(r2["rejected"], 1)


if __name__ == "__main__":
    unittest.main()
