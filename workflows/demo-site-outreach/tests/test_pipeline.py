from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from adapters import FakePlaces, RecordingBland  # noqa: E402
from config import Config, LiveBlocked  # noqa: E402
from pipeline import Pipeline, PipelineError, make_pipeline  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "places_listings.json").read_text())


def _cfg(tmp: str, **kwargs: object) -> Config:
    base = dict(
        dry_run=True,
        store_path=Path(tmp) / "store.json",
        geo="Austin, TX",
        category="",
        batch_size=10,
        physical_address="123 Example St, Austin, TX 78701",
        from_email="alexandra@example.com",
        template_approved=False,
    )
    base.update(kwargs)
    return Config(**base)  # type: ignore[arg-type]


def _pipe(tmp: str, **kwargs: object) -> Pipeline:
    cfg = _cfg(tmp, **kwargs)
    return make_pipeline(cfg, places=FakePlaces([dict(row) for row in FIXTURE["listings"]]))


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.pipe = _pipe(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dry_run_e2e_no_http(self) -> None:
        self.pipe.cfg.simulate_replies = True
        self.pipe.cfg.simulate_interest = True
        with mock.patch("adapters._http_json", side_effect=AssertionError("HTTP during dry-run")):
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("urlopen during dry-run")):
                result = self.pipe.run()
        self.assertTrue(result["dry_run"])
        self.assertIn("place_oak_bakery", result["sourced"])
        self.assertNotIn("place_has_site", result["sourced"])
        oak = self.pipe.store.get("place_oak_bakery")
        assert oak is not None
        self.assertEqual(oak.state, "handed_off")
        self.assertTrue(oak.sms_consent)
        self.assertTrue(oak.demo_url.startswith("https://dry-run.invalid/"))
        self.assertIsNotNone(oak.handoff)
        self.assertIn("I'll have him reach out directly", result["handed_off"][0]["bow_out"])
        channels = {row["channel"] for row in self.pipe.store.outbox()}
        self.assertIn("email", channels)
        self.assertIn("sms", channels)
        self.assertTrue(all(row.get("dry_run") for row in self.pipe.store.outbox()))
        self.assertTrue(self.pipe.store.alerts())
        self.assertTrue(all(a.get("dry_run") for a in self.pipe.store.alerts()))

    def test_outreach_personalizes_and_rechecks_website(self) -> None:
        self.pipe.source()
        result = self.pipe.outreach_lead(self.pipe.store.get("place_oak_bakery"))  # type: ignore[arg-type]
        self.assertEqual(result["state"], "contacted")
        self.assertIn("Oak Street Bakery", result["email"]["subject"])
        self.assertTrue(result["email"]["dry_run"])
        river_row = next(
            row for row in self.pipe.adapters.places.listings if row["place_id"] == "place_river_lawn"
        )
        river_row["website"] = "https://now-has-a-site.example"
        river = self.pipe.store.get("place_river_lawn")
        assert river is not None
        skipped = self.pipe.outreach_lead(river)
        self.assertEqual(skipped["state"], "dead")
        self.assertIn("website", skipped["reason"])

    def test_sms_refused_without_consent(self) -> None:
        self.pipe.source()
        lead = self.pipe.store.get("place_oak_bakery")
        assert lead is not None
        lead.discovery = {
            "services": "x",
            "hours": "x",
            "contact": "x",
            "differentiator": "x",
            "photos": "x",
        }
        lead.sms_consent = False
        lead.sms_number = ""
        with self.assertRaises(PipelineError):
            self.pipe.build_and_deliver(lead)

    def test_opt_out_suppresses_and_blocks_later_sends(self) -> None:
        self.pipe.source()
        oak = self.pipe.store.get("place_oak_bakery")
        assert oak is not None
        self.pipe.outreach_lead(oak)
        oak = self.pipe.store.get("place_oak_bakery")
        assert oak is not None
        killed = self.pipe.ingest_text("STOP", lead=oak)
        self.assertEqual(killed["state"], "suppressed")
        oak = self.pipe.store.get("place_oak_bakery")
        assert oak is not None
        again = self.pipe.outreach_lead(oak)
        self.assertEqual(again["state"], "suppressed")
        self.assertTrue(self.pipe.store.is_suppressed("phone:5125550101"))

    def test_max_two_follow_ups_then_dead(self) -> None:
        self.pipe.source()
        oak = self.pipe.store.get("place_oak_bakery")
        assert oak is not None
        self.pipe.outreach_lead(oak)
        first = self.pipe.follow_up_batch()
        self.assertEqual(self.pipe.store.get("place_oak_bakery").follow_ups, 1)  # type: ignore[union-attr]
        second = self.pipe.follow_up_batch()
        self.assertEqual(self.pipe.store.get("place_oak_bakery").follow_ups, 2)  # type: ignore[union-attr]
        third = self.pipe.follow_up_batch()
        dead = [row for row in third if row["lead_id"] == "place_oak_bakery"][0]
        self.assertEqual(dead["state"], "dead")
        self.assertEqual(self.pipe.store.get("place_oak_bakery").state, "dead")  # type: ignore[union-attr]
        self.assertTrue(first and second)

    def test_live_blocked_without_template_approval(self) -> None:
        pipe = _pipe(self.tmp.name, dry_run=False, template_approved=False)
        pipe.source()
        oak = pipe.store.get("place_oak_bakery")
        assert oak is not None
        with self.assertRaises(LiveBlocked):
            pipe.outreach_lead(oak)

    def test_bland_adapter_refuses_unconsented_number(self) -> None:
        bland = RecordingBland(dry_run=True)
        with self.assertRaisesRegex(Exception, "consent"):
            bland.send_sms(to="5125550101", body="hi", lead_id="x", consent=False)
