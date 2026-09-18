from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from exif import parse_exif  # noqa: E402
from exif_fixtures import build_heic_with_exif, build_jpeg_with_exif  # noqa: E402

TAKEN = datetime(2026, 9, 18, 8, 15, 0)
LAT = 26.5626
LON = -81.9495


class ExifParseTests(unittest.TestCase):
    def test_jpeg_datetime_and_gps(self) -> None:
        blob = build_jpeg_with_exif(taken=TAKEN, lat=LAT, lon=LON)
        info = parse_exif(blob)
        self.assertEqual(info.datetime_original, TAKEN)
        self.assertEqual(info.date_source, "exif")
        self.assertIsNotNone(info.gps)
        assert info.gps is not None
        self.assertAlmostEqual(info.gps[0], LAT, places=3)
        self.assertAlmostEqual(info.gps[1], LON, places=3)
        self.assertEqual(info.date_iso, "2026-09-18")

    def test_heic_datetime_and_gps(self) -> None:
        blob = build_heic_with_exif(taken=TAKEN, lat=LAT, lon=LON)
        info = parse_exif(blob)
        self.assertEqual(info.datetime_original, TAKEN)
        self.assertIsNotNone(info.gps)
        assert info.gps is not None
        self.assertAlmostEqual(info.gps[0], LAT, places=3)
        self.assertAlmostEqual(info.gps[1], LON, places=3)

    def test_stripped_bytes_have_no_exif(self) -> None:
        info = parse_exif(b"\xff\xd8\xff\xd9")
        self.assertIsNone(info.datetime_original)
        self.assertIsNone(info.gps)
        self.assertEqual(info.date_source, "")
        self.assertEqual(info.date_iso, "")
