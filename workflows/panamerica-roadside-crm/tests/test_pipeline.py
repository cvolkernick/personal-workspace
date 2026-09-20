from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from adapters import FakeDrive, FakeOcr, RecordingBland  # noqa: E402
from config import Config, LiveBlocked  # noqa: E402
from models import SMS_MAX_CHARS  # noqa: E402
from outreach_copy import render_sms, render_voice_task  # noqa: E402
from pipeline import Pipeline, make_pipeline, shift_clock  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "photo_sets.json").read_text())
NOW = datetime(2026, 9, 17, 20, 0, 0, tzinfo=timezone.utc)


def _cfg(tmp: str, **kwargs: object) -> Config:
    base = dict(
        dry_run=True,
        copy_approved=False,
        store_path=Path(tmp) / "store.json",
        fixture_path=Path(__file__).parent / "fixtures" / "photo_sets.json",
        now=lambda: NOW,
    )
    base.update(kwargs)
    return Config(**base)  # type: ignore[arg-type]


def _pipe(tmp: str, **kwargs: object) -> Pipeline:
    cfg = _cfg(tmp, **kwargs)
    drive = FakeDrive([dict(row) for row in FIXTURE["sets"]])
    ocr = FakeOcr({k: v for row in FIXTURE["sets"] for k, v in (row.get("ocr") or {}).items()})
    return make_pipeline(cfg, drive=drive, ocr=ocr)


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.pipe = _pipe(self.tmp.name)
        self.pipe.weekly_pass()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_sms_copy_matches_approved_no_stop_footer(self) -> None:
        lead = self.pipe.store.get("lead-set-corolla")
        assert lead is not None
        body = render_sms(lead)
        approved = (
            "Hi, this is Alexandra with Panamerica Auto in Cape Coral. "
            f"I saw your {lead.car_label()} for sale on {lead.location} — have you considered renting it out "
            "instead of selling? We manage cars for owners and handle everything: "
            "listing, guests, cleaning, maintenance. We also have an owner-exit option "
            "where we take over the payments if you'd rather move on. You can find details "
            "on our available options at https://www.panamericafleet.com/plans. Worth a 10-min chat?"
        )
        self.assertEqual(body, approved)
        self.assertNotIn("STOP", body)
        self.assertNotIn("Turo", body)
        self.assertIn("owner-exit", body)
        self.assertIn("https://www.panamericafleet.com/plans", body)
        self.assertLessEqual(len(body), SMS_MAX_CHARS)

    def test_voice_task_and_prompt_include_owner_exit_no_turo_pitch(self) -> None:
        lead = self.pipe.store.get("lead-set-corolla")
        assert lead is not None
        task = render_voice_task(lead)
        self.assertIn("owner-exit", task)
        self.assertIn("www.panamericafleet.com/plans", task)
        self.assertIn("Do not mention Turo", task)
        self.assertEqual(task.lower().count("turo"), 1)
        prompt = (PKG / "prompts" / "alexandra.roadside.v1.md").read_text()
        self.assertIn("owner-exit", prompt)
        self.assertIn("www.panamericafleet.com/plans", prompt)
        self.assertIn("Do not mention Turo", prompt)
        self.assertEqual(prompt.lower().count("turo"), 1)

    def test_phase1_sms_dry_run_no_http(self) -> None:
        with mock.patch("adapters._http_json", side_effect=AssertionError("HTTP during dry-run")):
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("urlopen during dry-run")):
                result = self.pipe.send_sms(self.pipe.store.get("lead-set-corolla"))  # type: ignore[arg-type]
        self.assertEqual(result["state"], "sms_sent")
        self.assertTrue(result["sms"]["dry_run"])
        self.assertNotIn("STOP", result["sms"]["body"])
        self.assertIn("https://www.panamericafleet.com/plans", result["sms"]["body"])
        lead = self.pipe.store.get("lead-set-corolla")
        assert lead is not None
        self.assertEqual(lead.state, "sms_sent")
        self.assertTrue(lead.sms_sent_at)

    def test_needs_info_is_not_texted(self) -> None:
        unread = self.pipe.store.get("lead-set-unreadable")
        assert unread is not None
        skipped = self.pipe.send_sms(unread)
        self.assertTrue(skipped.get("skipped"))
        self.assertEqual(self.pipe.store.get("lead-set-unreadable").state, "needs-info")  # type: ignore[union-attr]

    def test_stop_honored_and_blocks_later_sends(self) -> None:
        corolla = self.pipe.store.get("lead-set-corolla")
        assert corolla is not None
        self.pipe.send_sms(corolla)
        killed = self.pipe.ingest_text("STOP", lead=self.pipe.store.get("lead-set-corolla"))
        self.assertEqual(killed["state"], "declined")
        self.assertEqual(killed["reason"], "opt-out")
        again = self.pipe.send_sms(self.pipe.store.get("lead-set-corolla"))  # type: ignore[arg-type]
        self.assertEqual(again["state"], "declined")
        self.assertTrue(self.pipe.store.is_suppressed("phone:2395550101"))
        call = self.pipe.start_call(self.pipe.store.get("lead-set-corolla"))  # type: ignore[arg-type]
        self.assertTrue(call.get("skipped"))

    def test_declined_never_recontacted(self) -> None:
        honda = self.pipe.store.get("lead-set-honda")
        assert honda is not None
        self.pipe.send_sms(honda)
        self.pipe.ingest_text("not interested", lead=self.pipe.store.get("lead-set-honda"))
        self.assertEqual(self.pipe.store.get("lead-set-honda").state, "declined")  # type: ignore[union-attr]
        self.pipe.cfg.now = lambda: shift_clock(NOW, 10)
        due = self.pipe.call_batch()
        self.assertFalse(any(row.get("lead_id") == "lead-set-honda" and not row.get("skipped") for row in due))

    def test_phase2_call_only_after_five_days_non_responders(self) -> None:
        corolla = self.pipe.store.get("lead-set-corolla")
        honda = self.pipe.store.get("lead-set-honda")
        assert corolla is not None and honda is not None
        self.pipe.send_sms(corolla)
        self.pipe.send_sms(honda)
        too_soon = self.pipe.call_batch()
        self.assertEqual(too_soon, [])
        self.pipe.ingest_text("maybe later", lead=self.pipe.store.get("lead-set-honda"))
        self.pipe.cfg.now = lambda: shift_clock(NOW, 6)
        due = self.pipe.call_batch()
        ids = [row["lead_id"] for row in due if not row.get("skipped")]
        self.assertEqual(ids, ["lead-set-corolla"])
        self.assertEqual(self.pipe.store.get("lead-set-corolla").state, "call_attempted")  # type: ignore[union-attr]
        self.assertEqual(self.pipe.store.get("lead-set-honda").state, "responded")  # type: ignore[union-attr]
        second = self.pipe.start_call(self.pipe.store.get("lead-set-corolla"))  # type: ignore[arg-type]
        self.assertTrue(second.get("skipped"))

    def test_interested_schedules_chris_callback(self) -> None:
        corolla = self.pipe.store.get("lead-set-corolla")
        assert corolla is not None
        self.pipe.send_sms(corolla)
        result = self.pipe.ingest_text("yes interested, call me", lead=self.pipe.store.get("lead-set-corolla"))
        self.assertEqual(result["state"], "interested")
        self.assertEqual(result["callback"]["with"], "Chris")
        self.assertEqual(result["callback"]["status"], "requested")
        self.assertTrue(self.pipe.store.alerts())
        self.assertTrue(all(a.get("dry_run") for a in self.pipe.store.alerts()))

    def test_live_blocked_without_copy_approval(self) -> None:
        pipe = _pipe(self.tmp.name + "-live", dry_run=False, copy_approved=False)
        pipe.weekly_pass()
        corolla = pipe.store.get("lead-set-corolla")
        assert corolla is not None
        with self.assertRaises(LiveBlocked):
            pipe.send_sms(corolla)

    def test_dry_run_e2e_no_http(self) -> None:
        self.pipe.cfg.simulate_replies = True
        self.pipe.cfg.simulate_interest = True
        with mock.patch("adapters._http_json", side_effect=AssertionError("HTTP during dry-run")):
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("urlopen during dry-run")):
                result = self.pipe.run()
        self.assertTrue(result["dry_run"])
        self.assertGreaterEqual(result["counts"].get("interested", 0), 1)
        self.assertTrue(all(row.get("dry_run") for row in self.pipe.store.outbox()))

    def test_recording_bland_records_sms_and_call(self) -> None:
        bland = RecordingBland(dry_run=True)
        sms = bland.send_sms(to="2395550101", body="hi STOP", lead_id="x")
        call = bland.start_call(to="2395550101", task="pitch", lead_id="x")
        self.assertEqual(sms["channel"], "sms")
        self.assertEqual(call["channel"], "voice")
        self.assertTrue(sms["dry_run"] and call["dry_run"])
