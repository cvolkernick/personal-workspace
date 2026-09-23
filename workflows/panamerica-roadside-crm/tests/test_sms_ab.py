from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from adapters import ExternalSendError, RecordingBland  # noqa: E402
from models import Lead  # noqa: E402
from outreach_copy import assign_sms_variant, render_sms  # noqa: E402
from pipeline import shift_clock  # noqa: E402
from test_pipeline import NOW, _pipe  # noqa: E402


def _lead(lead_id: str, phone: str) -> Lead:
    return Lead(
        id=lead_id,
        folder_id=lead_id,
        phone=phone,
        year="2018",
        make="Toyota",
        model="Corolla",
        location="Burnt Store Rd",
        state="new",
    )


class SmsAbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.pipe = _pipe(self.tmp.name)
        self.pipe.weekly_pass()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_legacy_lead_roundtrip_tolerates_missing_variant_fields(self) -> None:
        legacy = Lead.from_dict({"id": "old", "folder_id": "f", "phone": "2395550101", "mystery": 1})
        self.assertEqual(legacy.sms_variant_id, "")
        self.assertIsNone(legacy.sms_variant_assigned_at)
        self.assertEqual(legacy.reply_quality, "")
        blank = Lead.from_dict({"id": "blank", "folder_id": "f", "sms_variant_id": None, "reply_quality": None})
        self.assertEqual(blank.sms_variant_id, "")
        self.assertEqual(blank.reply_quality, "")
        legacy.sms_variant_id = "A"
        legacy.sms_variant_assigned_at = "2026-09-23T18:00:00Z"
        legacy.reply_quality = "interested"
        again = Lead.from_dict(legacy.to_dict())
        self.assertEqual(again.sms_variant_id, "A")
        self.assertEqual(again.sms_variant_assigned_at, "2026-09-23T18:00:00Z")
        self.assertEqual(again.reply_quality, "interested")

    def test_champion_only_assigns_a_once_and_reports_zeros_for_b(self) -> None:
        before = self.pipe.daily_pass()["variant_report"]
        self.assertEqual(before["ab_mode"], "champion_only")
        self.assertFalse(before["variant_b_approved"])
        self.assertEqual(before["sends_today"], {"A": 0, "B": 0})
        self.assertIsNone(before["cumulative"]["A"]["reply_rate_72h"])
        self.assertIsNone(before["cumulative"]["B"]["reply_rate_72h"])

        results = self.pipe.sms_batch()
        sent = [row for row in results if not row.get("skipped")]
        self.assertGreaterEqual(len(sent), 1)
        self.assertTrue(all(row["sms_variant_id"] == "A" for row in sent))
        self.assertTrue(all(row["ab_mode"] == "champion_only" for row in sent))
        self.assertTrue(all("CHALLENGER_UNAPPROVED" not in row["sms"]["body"] for row in sent))

        lead = self.pipe.store.get("lead-set-corolla")
        assert lead is not None
        assigned = [event for event in lead.events if event.get("op") == "sms_variant_assigned"]
        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned[0]["variant"], "A")
        self.assertEqual(assigned[0]["ab_mode"], "champion_only")
        stamp = lead.sms_variant_assigned_at

        again = self.pipe.send_sms(lead)
        self.assertTrue(again.get("skipped"))
        stored = self.pipe.store.get("lead-set-corolla")
        assert stored is not None
        self.assertEqual(stored.sms_variant_id, "A")
        self.assertEqual(stored.sms_variant_assigned_at, stamp)
        self.assertEqual(sum(1 for event in stored.events if event.get("op") == "sms_variant_assigned"), 1)

        report = self.pipe.variant_report()
        self.assertEqual(report["ab_mode"], "champion_only")
        self.assertGreater(report["sends_today"]["A"], 0)
        self.assertEqual(report["sends_today"]["B"], 0)
        self.assertEqual(report["cumulative"]["B"]["sends"], 0)
        self.assertEqual(report["cumulative"]["A"]["sends"], len(sent))

    def test_failed_send_keeps_the_assignment(self) -> None:
        class Boom:
            def send_sms(self, **kwargs: object) -> dict[str, object]:
                raise ExternalSendError("boom")

        self.pipe.adapters.bland = Boom()
        lead = self.pipe.store.get("lead-set-corolla")
        assert lead is not None
        with self.assertRaises(ExternalSendError):
            self.pipe.send_sms(lead)
        stored = self.pipe.store.get("lead-set-corolla")
        assert stored is not None
        self.assertEqual(stored.state, "new")
        self.assertEqual(stored.sms_variant_id, "A")
        stamp = stored.sms_variant_assigned_at
        self.assertTrue(stamp)

        self.pipe.adapters.bland = RecordingBland(dry_run=True)
        result = self.pipe.send_sms(self.pipe.store.get("lead-set-corolla"))  # type: ignore[arg-type]
        stored = self.pipe.store.get("lead-set-corolla")
        assert stored is not None
        self.assertEqual(result["state"], "sms_sent")
        self.assertEqual(stored.sms_variant_id, "A")
        self.assertEqual(stored.sms_variant_assigned_at, stamp)
        self.assertEqual(sum(1 for event in stored.events if event.get("op") == "sms_variant_assigned"), 1)
        self.assertNotIn("CHALLENGER_UNAPPROVED", result["sms"]["body"])

    def test_unapproved_b_is_not_sent_or_reassigned(self) -> None:
        lead = self.pipe.store.get("lead-set-honda")
        assert lead is not None
        lead.sms_variant_id = "B"
        lead.sms_variant_assigned_at = "2026-09-01T00:00:00Z"
        self.pipe.store.put(lead)
        result = self.pipe.send_sms(self.pipe.store.get("lead-set-honda"))  # type: ignore[arg-type]
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result["reason"], "variant_not_approved")
        stored = self.pipe.store.get("lead-set-honda")
        assert stored is not None
        self.assertEqual(stored.state, "new")
        self.assertEqual(stored.sms_variant_id, "B")
        self.assertEqual(stored.sms_variant_assigned_at, "2026-09-01T00:00:00Z")
        self.assertFalse(any("CHALLENGER_UNAPPROVED" in str(row.get("body") or "") for row in self.pipe.store.outbox()))

    def test_same_phone_does_not_get_a_second_script(self) -> None:
        self.pipe.store.put(_lead("dup-a", "2395550198"))
        self.pipe.store.put(_lead("dup-b", "239-555-0198"))
        first = self.pipe.send_sms(self.pipe.store.get("dup-a"))  # type: ignore[arg-type]
        second = self.pipe.send_sms(self.pipe.store.get("dup-b"))  # type: ignore[arg-type]
        self.assertEqual(first["sms_variant_id"], "A")
        self.assertEqual(second.get("reason"), "duplicate-phone")
        self.assertEqual(self.pipe.store.get("dup-a").state, "sms_sent")  # type: ignore[union-attr]
        skipped = self.pipe.store.get("dup-b")
        assert skipped is not None
        self.assertEqual(skipped.state, "new")
        self.assertEqual(skipped.sms_variant_id, "")

    def test_split_sends_the_assigned_body_and_does_not_reassign(self) -> None:
        with mock.patch("outreach_copy.SMS_VARIANT_B_APPROVED", True):
            arms: dict[str, str] = {}
            n = 0
            while set(arms) != {"A", "B"} and n < 80:
                lead_id = f"ab-{n}"
                arm, mode = assign_sms_variant(lead_id)
                self.assertEqual(mode, "split")
                arms.setdefault(arm, lead_id)
                n += 1
            self.assertEqual(set(arms), {"A", "B"})
            for offset, (arm, lead_id) in enumerate(arms.items()):
                self.pipe.store.put(_lead(lead_id, f"239555{2000 + offset:04d}"))
                result = self.pipe.send_sms(self.pipe.store.get(lead_id))  # type: ignore[arg-type]
                self.assertEqual(result["ab_mode"], "split")
                self.assertEqual(result["sms_variant_id"], arm)
                stored = self.pipe.store.get(lead_id)
                assert stored is not None
                self.assertEqual(result["sms"]["body"], render_sms(stored))
                if arm == "B":
                    self.assertIn("CHALLENGER_UNAPPROVED", result["sms"]["body"])
                else:
                    self.assertNotIn("CHALLENGER_UNAPPROVED", result["sms"]["body"])
                retry = self.pipe.send_sms(stored)
                self.assertTrue(retry.get("skipped"))
                self.assertEqual(self.pipe.store.get(lead_id).sms_variant_id, arm)  # type: ignore[union-attr]

            natural, _ = assign_sms_variant("lead-set-corolla")
            opposite = "B" if natural == "A" else "A"
            pinned = self.pipe.store.get("lead-set-corolla")
            assert pinned is not None
            pinned.sms_variant_id = opposite
            pinned.sms_variant_assigned_at = "2026-09-01T00:00:00Z"
            self.pipe.store.put(pinned)
            sent = self.pipe.send_sms(self.pipe.store.get("lead-set-corolla"))  # type: ignore[arg-type]
            kept = self.pipe.store.get("lead-set-corolla")
            assert kept is not None
            self.assertEqual(sent["sms_variant_id"], opposite)
            self.assertEqual(kept.sms_variant_assigned_at, "2026-09-01T00:00:00Z")
            self.assertEqual(sent["sms"]["body"], render_sms(kept))

    def test_stop_not_now_and_interested_break_out_by_variant(self) -> None:
        corolla = self.pipe.store.get("lead-set-corolla")
        honda = self.pipe.store.get("lead-set-honda")
        assert corolla is not None and honda is not None
        self.pipe.send_sms(corolla)
        self.pipe.send_sms(honda)
        stopped = self.pipe.ingest_text("STOP", lead=self.pipe.store.get("lead-set-corolla"))
        self.assertEqual(stopped["state"], "declined")
        self.assertEqual(stopped["reason"], "opt-out")
        stopped_lead = self.pipe.store.get("lead-set-corolla")
        assert stopped_lead is not None
        self.assertEqual(stopped_lead.reply_quality, "stop_angry")
        self.assertTrue(self.pipe.store.is_suppressed("phone:2395550101"))
        quality_events = [event for event in stopped_lead.events if event.get("reply_quality") == "stop_angry"]
        self.assertTrue(quality_events)
        self.assertEqual(quality_events[-1]["sms_variant_id"], "A")

        declined = self.pipe.ingest_text("not interested", lead=self.pipe.store.get("lead-set-honda"))
        self.assertEqual(declined["reason"], "declined")
        declined_lead = self.pipe.store.get("lead-set-honda")
        assert declined_lead is not None
        self.assertEqual(declined_lead.reply_quality, "not_now")

        fresh = _lead("interest-1", "2395550177")
        self.pipe.store.put(fresh)
        self.pipe.send_sms(self.pipe.store.get("interest-1"))  # type: ignore[arg-type]
        interested = self.pipe.ingest_text(
            "yes interested, call me",
            lead=self.pipe.store.get("interest-1"),
        )
        self.assertEqual(interested["state"], "interested")
        interested_lead = self.pipe.store.get("interest-1")
        assert interested_lead is not None
        self.assertEqual(interested_lead.reply_quality, "interested")

        open_window = self.pipe.variant_report()["cumulative"]["A"]
        self.assertEqual(open_window["stop_angry"], 1)
        self.assertEqual(open_window["not_now"], 1)
        self.assertEqual(open_window["interested"], 1)
        self.assertEqual(open_window["replies"], 3)
        self.assertEqual(open_window["no_reply"], 0)
        self.assertIsNone(open_window["reply_rate_72h"])

        self.pipe.cfg.now = lambda: shift_clock(NOW, 4)
        matured = self.pipe.variant_report()["cumulative"]["A"]
        self.assertEqual(matured["replies_within_72h"], 3)
        self.assertEqual(matured["no_reply"], 0)
        self.assertEqual(matured["reply_rate_72h"], 1.0)
        self.assertEqual(self.pipe.store.get("lead-set-corolla").state, "declined")  # type: ignore[union-attr]

    def test_no_reply_is_derived_after_72h_without_a_state_change(self) -> None:
        lead = self.pipe.store.get("lead-set-corolla")
        assert lead is not None
        self.pipe.send_sms(lead)
        fresh = self.pipe.variant_report()["cumulative"]["A"]
        self.assertEqual(fresh["no_reply"], 0)
        self.assertIsNone(fresh["reply_rate_72h"])
        self.pipe.cfg.now = lambda: shift_clock(NOW, 3)
        matured = self.pipe.variant_report()["cumulative"]["A"]
        self.assertEqual(matured["sends"], 1)
        self.assertEqual(matured["no_reply"], 1)
        self.assertEqual(matured["replies"], 0)
        self.assertEqual(matured["reply_rate_72h"], 0.0)
        stored = self.pipe.store.get("lead-set-corolla")
        assert stored is not None
        self.assertEqual(stored.state, "sms_sent")
        self.assertEqual(stored.reply_quality, "")
