from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from adapters import FakePlaces  # noqa: E402
from config import Config  # noqa: E402
from pipeline import make_pipeline  # noqa: E402
from webhook import check_secret, extract_inbound, handle_payload, make_handler  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "places_listings.json").read_text())


class WebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        cfg = Config(
            dry_run=True,
            store_path=Path(self.tmp.name) / "store.json",
            geo="Austin, TX",
            webhook_secret="s3cret",
            physical_address="123 Example St",
            from_email="alexandra@example.com",
        )
        self.pipe = make_pipeline(cfg, places=FakePlaces(FIXTURE["listings"]))
        self.pipe.source()
        oak = self.pipe.store.get("place_oak_bakery")
        assert oak is not None
        self.pipe.outreach_lead(oak)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_extract_and_advance_discovery(self) -> None:
        payload = {
            "from": "512-555-0101",
            "text": (
                "1. Croissants\n2. 7-3\n3. Call us\n4. Fresh daily\n5. None\n"
                "Best number to text the preview: 512-555-0101"
            ),
            "metadata": {"lead_id": "place_oak_bakery"},
        }
        inbound = extract_inbound(payload)
        self.assertEqual(inbound["lead_id"], "place_oak_bakery")
        result = handle_payload(self.pipe, payload)
        self.assertIn(result["state"], {"discovering", "demo_sent"})
        oak = self.pipe.store.get("place_oak_bakery")
        assert oak is not None
        self.assertTrue(oak.sms_consent)
        self.assertTrue(oak.discovery_complete())
        self.assertEqual(oak.state, "demo_sent")
        self.assertTrue(oak.demo_url)

    def test_secret_compare(self) -> None:
        self.assertTrue(check_secret("s3cret", "s3cret"))
        self.assertFalse(check_secret("nope", "s3cret"))
        self.assertTrue(check_secret("ignored", ""))

    def test_http_unauthorized(self) -> None:
        handler_cls = make_handler(self.pipe)

        class Fake(handler_cls):  # type: ignore[misc, valid-type]
            def __init__(self) -> None:  # noqa: D107
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
