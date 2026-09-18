from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapters import FakeDrive, FakeOcr  # noqa: E402
from config import Config  # noqa: E402
from exif_fixtures import build_jpeg_with_exif  # noqa: E402
from geocode import FakeGeocoder  # noqa: E402
from organize import cluster_photos, photo_meta_from_row, same_cluster  # noqa: E402
from pipeline import make_pipeline  # noqa: E402

TAKEN = datetime(2026, 9, 18, 8, 15, 0)
LAT = 26.5626
LON = -81.9495
LAT2 = 26.6400
LON2 = -82.0100
NOW = datetime(2026, 9, 18, 20, 0, 0, tzinfo=timezone.utc)


class ClusterTests(unittest.TestCase):
    def test_same_car_burst_stays_together(self) -> None:
        a = photo_meta_from_row(
            {
                "id": "a",
                "name": "car.jpg",
                "exif_datetime": "2026:09:18 08:15:00",
                "exif_gps": {"lat": LAT, "lon": LON},
            }
        )
        b = photo_meta_from_row(
            {
                "id": "b",
                "name": "sign.jpg",
                "exif_datetime": "2026:09:18 08:15:12",
                "exif_gps": {"lat": LAT + 0.0002, "lon": LON},
            }
        )
        self.assertTrue(same_cluster(a, b))
        groups = cluster_photos([a, b])
        self.assertEqual(len(groups), 1)
        self.assertEqual({p.id for p in groups[0]}, {"a", "b"})

    def test_two_cars_same_day_split_on_gps(self) -> None:
        a = photo_meta_from_row(
            {
                "id": "a",
                "name": "car1.jpg",
                "exif_datetime": "2026:09:18 08:15:00",
                "exif_gps": {"lat": LAT, "lon": LON},
            }
        )
        b = photo_meta_from_row(
            {
                "id": "b",
                "name": "car2.jpg",
                "exif_datetime": "2026:09:18 08:16:00",
                "exif_gps": {"lat": LAT2, "lon": LON2},
            }
        )
        self.assertFalse(same_cluster(a, b))
        self.assertEqual(len(cluster_photos([a, b])), 2)


class RootDumpTests(unittest.TestCase):
    def test_root_dump_files_exif_folder_and_geocodes(self) -> None:
        jpeg = build_jpeg_with_exif(taken=TAKEN, lat=LAT, lon=LON)
        sign = build_jpeg_with_exif(taken=datetime(2026, 9, 18, 8, 15, 8), lat=LAT, lon=LON)
        geo = FakeGeocoder({(LAT, LON): "Del Prado Blvd, Cape Coral"})
        drive = FakeDrive(
            root_photos=[
                {
                    "id": "p-car",
                    "name": "IMG_1001.HEIC",
                    "jpeg_bytes": jpeg,
                    "ocr_text": "FOR SALE 2018 Toyota Corolla $8500 Call 239-555-0101",
                },
                {
                    "id": "p-sign",
                    "name": "IMG_1002.jpg",
                    "jpeg_bytes": sign,
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(
                dry_run=True,
                store_path=Path(tmp) / "store.json",
                now=lambda: NOW,
            )
            ocr = FakeOcr({"p-car": "FOR SALE 2018 Toyota Corolla $8500 Call 239-555-0101"})
            pipe = make_pipeline(cfg, drive=drive, ocr=ocr, geocoder=geo)
            result = pipe.daily_pass()
            self.assertEqual(len(result["organized"]), 1)
            self.assertTrue(result["organized"][0]["folder_name"].startswith("2026-09-18"))
            self.assertIn("del-prado", result["organized"][0]["folder_name"])
            self.assertEqual(len(drive.moves), 2)
            created = {row["lead_id"] for row in result["created"]}
            self.assertEqual(len(created), 1)
            lead = pipe.store.get(next(iter(created)))
            assert lead is not None
            self.assertEqual(lead.spotted_at, "2026-09-18")
            self.assertEqual(lead.date_source, "exif")
            self.assertEqual(lead.location_source, "exif")
            self.assertIn("Del Prado", lead.location)
            self.assertEqual(lead.phone, "2395550101")
            self.assertEqual(lead.state, "new")
            self.assertTrue(lead.gps)

    def test_missing_exif_falls_back_visibly(self) -> None:
        drive = FakeDrive(
            root_photos=[
                {
                    "id": "p-shot",
                    "name": "screenshot.png",
                    "created_time": "2026-09-18T14:05:00Z",
                    "ocr_text": "FOR SALE cash only",
                }
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(dry_run=True, store_path=Path(tmp) / "store.json", now=lambda: NOW)
            pipe = make_pipeline(cfg, drive=drive, ocr=FakeOcr({"p-shot": "FOR SALE cash only"}))
            result = pipe.daily_pass()
            self.assertEqual(len(result["needs_info"]), 1)
            lead = pipe.store.get(result["needs_info"][0]["lead_id"])
            assert lead is not None
            self.assertEqual(lead.date_source, "upload")
            self.assertEqual(lead.location_source, "missing")
            self.assertEqual(lead.location, "")
            self.assertEqual(lead.spotted_at, "2026-09-18")
            notes = " ".join(lead.notes)
            self.assertIn("upload", notes.lower())
            self.assertIn("GPS", notes)

    def test_morning_drop_actioned_same_day(self) -> None:
        jpeg = build_jpeg_with_exif(taken=TAKEN, lat=LAT, lon=LON)
        drive = FakeDrive(
            root_photos=[
                {
                    "id": "p-am",
                    "name": "morning.jpg",
                    "jpeg_bytes": jpeg,
                    "ocr_text": "Honda Civic 2016 asking $6200 (239) 555-0199",
                }
            ]
        )
        geo = FakeGeocoder({(LAT, LON): "Del Prado Blvd"})
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(dry_run=True, store_path=Path(tmp) / "store.json", now=lambda: NOW)
            pipe = make_pipeline(
                cfg,
                drive=drive,
                ocr=FakeOcr({"p-am": "Honda Civic 2016 asking $6200 (239) 555-0199"}),
                geocoder=geo,
            )
            result = pipe.daily_pass()
            self.assertEqual(len(result["created"]), 1)
            lead = pipe.store.get(result["created"][0]["lead_id"])
            assert lead is not None
            self.assertEqual(lead.spotted_at, "2026-09-18")
            self.assertEqual(NOW.date().isoformat(), lead.spotted_at)
