"""Image MIME helpers. No network. No invented trip photos."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import turo_media  # noqa: E402


class TuroMediaTests(unittest.TestCase):
    def test_keep_jpeg_drop_logo_gif_svg(self) -> None:
        self.assertTrue(turo_media.keep_image_part("image/jpeg", filename="fuel.jpg"))
        self.assertTrue(turo_media.keep_image_part("image/png", filename="blocked-in.png"))
        self.assertFalse(turo_media.keep_image_part("image/png", filename="turo-logo.png"))
        self.assertFalse(turo_media.keep_image_part("image/gif", filename="pixel.gif"))
        self.assertFalse(turo_media.keep_image_part("image/svg+xml", filename="mark.svg"))
        self.assertFalse(turo_media.keep_image_part("text/plain", filename="note.txt"))
        self.assertFalse(
            turo_media.keep_image_part("image/jpeg", filename="x.jpg", size=4)
        )

    def test_claims_photos_phrase(self) -> None:
        self.assertTrue(turo_media.claims_photos("Contains photo(s)."))
        self.assertTrue(turo_media.claims_photos("message contains photos from guest"))
        self.assertFalse(turo_media.claims_photos("Trip booked. No images here."))

    def test_write_and_resolve_rejects_traversal(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20 + b"\xff\xd9"
        with tempfile.TemporaryDirectory() as td:
            media = Path(td) / "turo_inbox_media"
            rec = turo_media.write_image_bytes(
                media, "m1", jpeg, filename="fuel.jpg", mime="image/jpeg"
            )
            self.assertIsNotNone(rec)
            self.assertEqual(rec["relpath"], "m1/fuel.jpg")
            found = turo_media.resolve_media_file(media, rec["relpath"])
            self.assertEqual(found, Path(rec["path"]))
            self.assertIsNone(turo_media.resolve_media_file(media, "../secret.jpg"))
            self.assertIsNone(turo_media.resolve_media_file(media, "m1/../../etc/passwd"))
            self.assertIsNone(turo_media.resolve_media_file(media, "missing/nope.jpg"))
            skipped = turo_media.write_image_bytes(
                media, "m1", jpeg, filename="header-logo.png", mime="image/png"
            )
            self.assertIsNone(skipped)


if __name__ == "__main__":
    unittest.main()
