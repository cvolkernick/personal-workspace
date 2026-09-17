from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from adapters import FakeDrive, FakeOcr  # noqa: E402
from config import Config  # noqa: E402
from pipeline import make_pipeline  # noqa: E402
from webhook import check_secret, extract_inbound, handle_payload, make_handler  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "photo_sets.json").read_text())


class WebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        cfg = Config(
            dry_run=True,
            store_path=Path(self.tmp.name) / "store.json",
            webhook_secret="s3cret",
        )
        drive = FakeDrive(FIXTURE["sets"])
        ocr = FakeOcr({k: v for row in FIXTURE["sets"] for k, v in (row.get("ocr") or {}).items()})
        self.pipe = make_pipeline(cfg, drive=drive, ocr=ocr)
        self.pipe.weekly_pass()
        corolla = self.pipe.store.get("lead-set-corolla")
        assert corolla is not None
        self.pipe.send_sms(corolla)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_stop_via_webhook(self) -> None:
        payload = {"from": "239-555-0101", "text": "STOP", "metadata": {"lead_id": "lead-set-corolla"}}
        inbound = extract_inbound(payload)
        self.assertEqual(inbound["lead_id"], "lead-set-corolla")
        result = handle_payload(self.pipe, payload)
        self.assertEqual(result["state"], "declined")
        self.assertTrue(self.pipe.store.is_suppressed("phone:2395550101"))

    def test_secret_compare(self) -> None:
        self.assertTrue(check_secret("s3cret", "s3cret"))
        self.assertFalse(check_secret("nope", "s3cret"))
        self.assertTrue(check_secret("ignored", ""))

    def test_http_unauthorized(self) -> None:
        handler_cls = make_handler(self.pipe)

        class Fake(handler_cls):  # type: ignore[misc, valid-type]
            def __init__(self) -> None:
                self.headers = {"Content-Length": "2", "X-Webhook-Secret": "wrong"}
                self.rfile = BytesIO(b"{}")
                self.wfile = BytesIO()
                self._code = 0

            def send_response(self, code: int) -> None:
                self._code = code

            def send_header(self, *args: object) -> None:
                return None

            def end_headers(self) -> None:
                return None

        fake = Fake()
        fake.do_POST()
        self.assertEqual(fake._code, 401)
