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


TOYOTA = "FOR SALE 2018 Toyota Corolla $8500 Call 239-555-0101"
JEEP = "FOR SALE 2015 Jeep Wrangler $16500 Call 239-555-0199"
# ~200 m north of LAT. Outside CLUSTER_METERS (150) and inside a same-day window.
LAT_200M = LAT + (200.0 / 111_320.0)


def _shot(photo_id: str, name: str, when: str, lat: float, lon: float, ocr: str) -> dict:
    return {
        "id": photo_id,
        "name": name,
        "exif_datetime": when,
        "exif_gps": {"lat": lat, "lon": lon},
        "ocr_text": ocr,
    }


class SameCornerTests(unittest.TestCase):
    def _pass(self, photos: list[dict]):
        drive = FakeDrive(root_photos=photos)
        geo = FakeGeocoder(
            {
                (LAT, LON): "Del Prado Blvd, Cape Coral",
                (LAT_200M, LON): "Del Prado Blvd, Cape Coral",
                (LAT2, LON2): "Santa Barbara Blvd, Cape Coral",
            }
        )
        ocr = FakeOcr({p["id"]: p["ocr_text"] for p in photos})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(dry_run=True, store_path=Path(tmp.name) / "store.json", now=lambda: NOW)
        pipe = make_pipeline(cfg, drive=drive, ocr=ocr, geocoder=geo)
        return pipe, pipe.daily_pass()

    def test_same_corner_two_vehicles_needs_info_and_does_not_sms(self) -> None:
        pipe, result = self._pass(
            [
                _shot("p-toyota", "toyota.jpg", "2026:09:18 08:15:00", LAT, LON, TOYOTA),
                _shot("p-jeep", "jeep.jpg", "2026:09:18 08:15:40", LAT, LON, JEEP),
            ]
        )
        self.assertEqual(len(result["organized"]), 1)
        self.assertEqual(result["created"], [])
        self.assertEqual(len(result["needs_info"]), 1)
        lead = pipe.store.get(result["needs_info"][0]["lead_id"])
        assert lead is not None
        self.assertEqual(lead.state, "needs-info")
        self.assertIn("same-corner", " ".join(lead.notes))
        self.assertIn("2018 Toyota", " ".join(lead.notes))
        self.assertIn("2015 Jeep", " ".join(lead.notes))
        sms = pipe.send_sms(lead)
        self.assertTrue(sms.get("skipped"))
        self.assertEqual(sms.get("reason"), "not sms-eligible")
        self.assertEqual(pipe.sms_batch(), [])

    def test_same_vehicle_car_and_sign_is_not_flagged(self) -> None:
        pipe, result = self._pass(
            [
                _shot("p-car", "car.jpg", "2026:09:18 08:15:00", LAT, LON, "2018 Toyota Corolla"),
                _shot("p-sign", "sign.jpg", "2026:09:18 08:15:12", LAT, LON, TOYOTA),
            ]
        )
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["needs_info"], [])
        lead = pipe.store.get(result["created"][0]["lead_id"])
        assert lead is not None
        self.assertEqual(lead.state, "new")
        self.assertEqual(lead.make, "Toyota")
        self.assertEqual(lead.year, "2018")
        self.assertNotIn("same-corner", " ".join(lead.notes))

    def test_outside_20_min_stays_two_leads(self) -> None:
        pipe, result = self._pass(
            [
                _shot("p-toyota", "toyota.jpg", "2026:09:18 08:15:00", LAT, LON, TOYOTA),
                _shot("p-jeep", "jeep.jpg", "2026:09:18 08:36:00", LAT, LON, JEEP),
            ]
        )
        self.assertEqual(len(result["organized"]), 2)
        self.assertEqual(len(result["created"]), 2)
        self.assertEqual(result["needs_info"], [])
        notes = []
        makes = set()
        for row in result["created"]:
            lead = pipe.store.get(row["lead_id"])
            assert lead is not None
            self.assertEqual(lead.state, "new")
            notes.append(" ".join(lead.notes))
            makes.add(lead.make)
        self.assertEqual(makes, {"Toyota", "Jeep"})
        self.assertFalse(any("same-corner" in note for note in notes))

    def test_outside_150_m_stays_two_leads(self) -> None:
        pipe, result = self._pass(
            [
                _shot("p-toyota", "toyota.jpg", "2026:09:18 08:15:00", LAT, LON, TOYOTA),
                _shot("p-jeep", "jeep.jpg", "2026:09:18 08:16:00", LAT_200M, LON, JEEP),
            ]
        )
        self.assertEqual(len(result["organized"]), 2)
        self.assertEqual(len(result["created"]), 2)
        self.assertEqual(result["needs_info"], [])
        for row in result["created"]:
            lead = pipe.store.get(row["lead_id"])
            assert lead is not None
            self.assertEqual(lead.state, "new")
            self.assertNotIn("same-corner", " ".join(lead.notes))
