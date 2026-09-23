from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from adapters import FakeDrive, FakeOcr, PhotoSet  # noqa: E402
from config import Config  # noqa: E402
from intake import (  # noqa: E402
    distinct_year_make_pairs,
    extract_vehicle,
    harvest_intake,
    harvest_text,
    harvest_vehicle_text,
    lead_from_set,
    pairs_in_text,
    parse_set_name,
)
from pipeline import make_pipeline  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "photo_sets.json").read_text())


def _pipe(tmp: str):
    cfg = Config(
        dry_run=True,
        store_path=Path(tmp) / "store.json",
        fixture_path=Path(__file__).parent / "fixtures" / "photo_sets.json",
    )
    drive = FakeDrive(FIXTURE["sets"])
    ocr = FakeOcr({k: v for row in FIXTURE["sets"] for k, v in (row.get("ocr") or {}).items()})
    return make_pipeline(cfg, drive=drive, ocr=ocr)


class IntakeTests(unittest.TestCase):
    def test_parse_folder_convention(self) -> None:
        date, road = parse_set_name("2026-09-10-del-prado")
        self.assertEqual(date, "2026-09-10")
        self.assertEqual(road, "del prado")

    def test_extract_vehicle_from_sign(self) -> None:
        info = extract_vehicle("FOR SALE 2018 Toyota Corolla $8500 Call 239-555-0101")
        self.assertEqual(info["year"], "2018")
        self.assertEqual(info["make"], "Toyota")
        self.assertEqual(info["model"], "Corolla")
        self.assertEqual(info["asking_price"], "8500")

    def test_weekly_pass_creates_one_lead_per_car_skips_duplicate_phone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipe = _pipe(tmp)
            result = pipe.weekly_pass()
            created_ids = {row["lead_id"] for row in result["created"]}
            self.assertIn("lead-set-corolla", created_ids)
            self.assertIn("lead-set-honda", created_ids)
            self.assertNotIn("lead-set-corolla-respot", created_ids)
            dupes = [row for row in result["skipped"] if row["reason"] == "duplicate-phone"]
            self.assertEqual(len(dupes), 1)
            self.assertEqual(dupes[0]["existing_id"], "lead-set-corolla")
            needs = {row["lead_id"] for row in result["needs_info"]}
            self.assertIn("lead-set-unreadable", needs)
            corolla = pipe.store.get("lead-set-corolla")
            assert corolla is not None
            self.assertEqual(corolla.phone, "2395550101")
            self.assertEqual(corolla.location, "del prado")
            self.assertEqual(corolla.make, "Toyota")
            self.assertEqual(corolla.year, "2018")
            honda = pipe.store.get("lead-set-honda")
            assert honda is not None
            self.assertEqual(honda.year, "2016")
            self.assertGreaterEqual(len(corolla.photos), 2)
            unread = pipe.store.get("lead-set-unreadable")
            assert unread is not None
            self.assertEqual(unread.state, "needs-info")
            self.assertEqual(unread.phone, "")

    def test_second_weekly_pass_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipe = _pipe(tmp)
            first = pipe.weekly_pass()
            second = pipe.weekly_pass()
            self.assertEqual(len(first["created"]), 2)
            self.assertEqual(second["created"], [])
            self.assertEqual(pipe.store.counts().get("new"), 2)
            self.assertEqual(pipe.store.counts().get("needs-info"), 1)

    def test_daily_pass_is_weekly_alias_and_keeps_folder_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipe = _pipe(tmp)
            result = pipe.daily_pass()
            self.assertEqual(len(result["created"]), 2)
            corolla = pipe.store.get("lead-set-corolla")
            assert corolla is not None
            self.assertEqual(corolla.date_source, "folder")
            self.assertEqual(corolla.location_source, "folder")
            self.assertEqual(corolla.location, "del prado")
            self.assertEqual(corolla.spotted_at, "2026-09-10")
            alias = pipe.weekly_pass()
            self.assertEqual(alias["created"], [])

    def test_jeep_sign_year_not_folder_date(self) -> None:
        photo_set = PhotoSet(
            id="set-jeep",
            name="2026-09-20",
            photos=[{"id": "p-jeep", "name": "jeep.jpg"}],
            sidecar_text="",
            ocr={"p-jeep": "FOR SALE 2015 Jeep Wrangler $16,500 Call 239-464-8445"},
        )
        text = harvest_text(photo_set, None)
        vehicle_text = harvest_vehicle_text(photo_set, None)
        self.assertNotIn("2026-09-20", vehicle_text)
        lead = lead_from_set(photo_set, text, vehicle_text=vehicle_text)
        self.assertEqual(lead.year, "2015")
        self.assertEqual(lead.make, "Jeep")
        self.assertEqual(lead.model, "Wrangler")
        self.assertEqual(lead.asking_price, "16500")
        self.assertEqual(lead.phone, "2394648445")
        self.assertEqual(lead.spotted_at, "2026-09-20")

    def test_folder_route_digits_do_not_leak_into_vehicle(self) -> None:
        photo_set = PhotoSet(
            id="set-jeep-route",
            name="2026-09-20-route-66",
            photos=[{"id": "p-jeep2", "name": "jeep.jpg"}],
            sidecar_text="",
            ocr={"p-jeep2": "FOR SALE 2015 Jeep Wrangler $16,500 Call 239-464-8445"},
        )
        text = harvest_text(photo_set, None)
        vehicle_text = harvest_vehicle_text(photo_set, None)
        lead = lead_from_set(photo_set, text, vehicle_text=vehicle_text)
        self.assertEqual(lead.year, "2015")
        self.assertNotEqual(lead.year, "2026")
        self.assertEqual(lead.make, "Jeep")
        self.assertEqual(lead.model, "Wrangler")
        self.assertEqual(lead.asking_price, "16500")
        self.assertNotIn("66", (lead.year, lead.model, lead.asking_price))
        self.assertEqual(lead.location, "route 66")
        self.assertEqual(lead.spotted_at, "2026-09-20")

    def test_pipeline_jeep_folder_date_yields_sign_year(self) -> None:
        jeep = {
            "id": "set-jeep",
            "name": "2026-09-20-route-66",
            "photos": [{"id": "p-jeep", "name": "jeep.jpg"}],
            "sidecar_text": "",
            "ocr": {"p-jeep": "FOR SALE 2015 Jeep Wrangler $16,500 Call 239-464-8445"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(dry_run=True, store_path=Path(tmp) / "store.json")
            pipe = make_pipeline(
                cfg,
                drive=FakeDrive([jeep]),
                ocr=FakeOcr(jeep["ocr"]),
            )
            result = pipe.daily_pass()
            self.assertEqual(len(result["created"]), 1)
            lead = pipe.store.get("lead-set-jeep")
            assert lead is not None
            self.assertEqual(lead.year, "2015")
            self.assertEqual(lead.make, "Jeep")
            self.assertEqual(lead.model, "Wrangler")
            self.assertEqual(lead.asking_price, "16500")
            self.assertEqual(lead.location, "route 66")
            self.assertEqual(lead.spotted_at, "2026-09-20")

    def test_kevin_photo_set_ignores_gps_screenshot_phone(self) -> None:
        photo_set = PhotoSet(
            id="set-kevin",
            name="2026-09-20-burnt-store",
            photos=[
                {"id": "p-car", "name": "car.jpg"},
                {"id": "p-sign", "name": "sign.jpg"},
                {"id": "p-map", "name": "maps.jpg"},
            ],
            sidecar_text="",
            ocr={
                "p-car": "2015 Jeep Wrangler",
                "p-sign": "FOR SALE 2015 Jeep Wrangler $16,500 Call 239-464-8445",
                "p-map": "26.639011, -82.039046",
            },
        )
        text = harvest_text(photo_set, None)
        vehicle_text = harvest_vehicle_text(photo_set, None)
        lead = lead_from_set(photo_set, text, vehicle_text=vehicle_text)
        self.assertEqual(lead.phone, "2394648445")
        self.assertEqual(lead.year, "2015")
        self.assertEqual(lead.make, "Jeep")
        self.assertNotIn("2663901182", lead.phone)
        self.assertEqual(lead.state, "new")
        self.assertNotIn("same-corner", " ".join(lead.notes))

    def test_year_make_identity_ignores_model_and_make_alias(self) -> None:
        pairs = distinct_year_make_pairs(
            [
                "2018 Toyota Corolla",
                "2018 Toyota Camry $9000 Call 239-555-0101",
                "2018 Chevy Malibu",
                "2018 Chevrolet Malibu",
            ]
        )
        self.assertEqual(pairs, [("2018", "Toyota"), ("2018", "Chevrolet")])

    def test_one_blob_can_hold_two_year_make_pairs(self) -> None:
        pairs = pairs_in_text(
            "2018 Toyota Corolla $8500 and 2015 Jeep Wrangler $16500 Call 239-555-0101"
        )
        self.assertEqual(pairs, [("2018", "Toyota"), ("2015", "Jeep")])

    def test_two_vehicles_on_one_set_are_needs_info(self) -> None:
        photo_set = PhotoSet(
            id="set-corner",
            name="2026-09-18-del-prado",
            photos=[{"id": "p-a", "name": "a.jpg"}, {"id": "p-b", "name": "b.jpg"}],
            ocr={
                "p-a": "FOR SALE 2018 Toyota Corolla $8500 Call 239-555-0101",
                "p-b": "FOR SALE 2015 Jeep Wrangler $16500 Call 239-555-0199",
            },
            spotted_at="2026-09-18",
            location="Del Prado Blvd",
            date_source="exif",
            location_source="exif",
        )
        text, vehicle_text, bits = harvest_intake(photo_set, None)
        lead = lead_from_set(photo_set, text, vehicle_text=vehicle_text, vehicle_bits=bits)
        self.assertEqual(lead.state, "needs-info")
        self.assertEqual(lead.phone, "2395550101")
        self.assertIn("same-corner", " ".join(lead.notes))
        self.assertIn("2018 Toyota", " ".join(lead.notes))
        self.assertIn("2015 Jeep", " ".join(lead.notes))
