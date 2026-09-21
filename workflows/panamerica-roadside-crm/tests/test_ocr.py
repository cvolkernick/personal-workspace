from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adapters import (
    FakeDrive,
    NullOcr,
    OCR_UNKNOWN,
    VisionOcr,
    build_adapters,
)
from config import Config, LiveBlocked
from pipeline import make_pipeline

SIGN_TEXT = "FOR SALE 2018 Toyota Corolla $8500 Call 239-555-0101"


def _vision_ok(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {
        "responses": [
            {
                "fullTextAnnotation": {
                    "text": SIGN_TEXT,
                    "pages": [{"confidence": 0.95}],
                }
            }
        ]
    }


def _vision_low(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {
        "responses": [
            {
                "fullTextAnnotation": {
                    "text": SIGN_TEXT,
                    "pages": [{"confidence": 0.1}],
                }
            }
        ]
    }


class _BlobDrive:
    def __init__(self, blob: bytes) -> None:
        self.blob = blob

    def download_bytes(self, file_id: str) -> bytes:
        return self.blob


class VisionOcrUnitTests(unittest.TestCase):
    def test_sign_text_from_bytes(self) -> None:
        ocr = VisionOcr(api_key="vk", downloader=_BlobDrive(b"\xff\xd8fake"), post=_vision_ok)
        text = ocr.read_text({"id": "p-sign", "name": "sign.jpg"})
        self.assertEqual(text, SIGN_TEXT)
        self.assertNotEqual(text, "")
        self.assertNotEqual(text, OCR_UNKNOWN)

    def test_failed_http_is_unknown_not_empty(self) -> None:
        def boom(*_a: object, **_k: object) -> dict[str, object]:
            raise RuntimeError("vision down")

        ocr = VisionOcr(api_key="vk", downloader=_BlobDrive(b"xx"), post=boom)
        self.assertEqual(ocr.read_text({"id": "p-sign"}), OCR_UNKNOWN)

    def test_low_confidence_is_unknown_not_empty(self) -> None:
        ocr = VisionOcr(api_key="vk", downloader=_BlobDrive(b"xx"), post=_vision_low)
        self.assertEqual(ocr.read_text({"id": "p-sign"}), OCR_UNKNOWN)

    def test_missing_bytes_or_key_is_unknown(self) -> None:
        ocr = VisionOcr(api_key="vk", downloader=_BlobDrive(b""), post=_vision_ok)
        self.assertEqual(ocr.read_text({"id": "p-sign"}), OCR_UNKNOWN)
        ocr2 = VisionOcr(api_key="", downloader=_BlobDrive(b"xx"), post=_vision_ok)
        self.assertEqual(ocr2.read_text({"id": "p-sign"}), OCR_UNKNOWN)


class LiveAdapterDefaultTests(unittest.TestCase):
    def test_live_build_adapters_defaults_to_vision_not_null(self) -> None:
        cfg = Config(
            dry_run=False,
            copy_approved=False,
            google_drive_token="tok",
            vision_api_key="vk",
        )
        adapters = build_adapters(cfg, drive=FakeDrive())
        self.assertIsInstance(adapters.ocr, VisionOcr)
        self.assertNotIsInstance(adapters.ocr, NullOcr)

    def test_dry_run_still_uses_fake_ocr(self) -> None:
        cfg = Config(dry_run=True, vision_api_key="vk")
        adapters = build_adapters(cfg, drive=FakeDrive())
        self.assertNotIsInstance(adapters.ocr, VisionOcr)
        self.assertNotIsInstance(adapters.ocr, NullOcr)


class ProductionPathOcrTests(unittest.TestCase):
    def test_photos_only_live_path_extracts_car_price_phone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(
                dry_run=False,
                copy_approved=False,
                google_drive_token="tok",
                vision_api_key="vk",
                store_path=Path(tmp) / "store.json",
            )
            drive = FakeDrive(
                [
                    {
                        "id": "set-fresh",
                        "name": "2026-09-20-burnt-store",
                        "photos": [
                            {
                                "id": "p-sign",
                                "name": "sign.jpg",
                                "jpeg_bytes": b"\xff\xd8fake-sign",
                            }
                        ],
                        "ocr": {},
                        "sidecar_text": "",
                    }
                ]
            )
            with mock.patch("adapters._http_json", side_effect=_vision_ok):
                pipe = make_pipeline(cfg, drive=drive)
                self.assertIsInstance(pipe.adapters.ocr, VisionOcr)
                result = pipe.daily_pass()
            lead = pipe.store.get("lead-set-fresh")
            assert lead is not None
            self.assertEqual(lead.phone, "2395550101")
            self.assertEqual(lead.year, "2018")
            self.assertEqual(lead.make, "Toyota")
            self.assertEqual(lead.model, "Corolla")
            self.assertEqual(lead.asking_price, "8500")
            self.assertEqual(lead.state, "new")
            self.assertTrue(any(row.get("lead_id") == "lead-set-fresh" for row in result["created"]))
            with self.assertRaises(LiveBlocked):
                pipe.send_sms(lead)
