from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from adapters import FakeDrive, FakeOcr  # noqa: E402
from config import Config  # noqa: E402
from intake import extract_vehicle, parse_set_name  # noqa: E402
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
