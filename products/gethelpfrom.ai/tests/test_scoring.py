#!/usr/bin/env python3
"""Worked personas for GetHelpFrom.ai scoring (#621)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from score import score_session  # noqa: E402


MAYA = {
    "eob_still_cant_tell_what_we_owe": "thats_me",
    "hospital_bill_months_later": "thats_me",
    "mom_copay_then_second_bill": "sort_of",
    "kids_urgent_care_drawer": "thats_me",
    "mystery_streaming_on_joint_card": "thats_me",
    "spouse_handles_it_until_they_dont": "thats_me",
    "p_msg_never_zero_01": "not_me",
}

LUIS = {
    "facebook_lead_sat_6h": "thats_me",
    "google_lsa_silenced_on_install": "thats_me",
    "condenser_quote_never_chased": "thats_me",
    "p_msg_reply_later_01": "not_me",
}

PRIYA = {
    "p_msg_never_zero_01": "thats_me",
    "p_msg_reply_later_01": "thats_me",
    "p_msg_reply_later_03": "thats_me",
    "p_msg_followup_01": "thats_me",
    "p_time_junk_meeting_02": "sort_of",
    "eob_still_cant_tell_what_we_owe": "not_me",
}


def _ids(result) -> list[str]:
    return [r["solution_id"] for r in result["shortlist"]]


class TestPersonas(unittest.TestCase):
    def test_maya_medical_admin(self) -> None:
        out = score_session(MAYA, hours="6-10", urgency="weekly_pain", trust="3")
        top = set(_ids(out)[:3])
        self.assertTrue(
            top & {"eob_explainer", "bill_pay_agent", "subscription_audit", "document_inbox"},
            out["shortlist"],
        )
        self.assertTrue(any(r["why"] for r in out["shortlist"]))

    def test_luis_speed_to_lead(self) -> None:
        out = score_session(
            LUIS,
            hours="10+",
            urgency="losing_sleep_or_money",
            trust="4",
            tools=["jobber"],
        )
        top = set(_ids(out)[:3])
        self.assertTrue(top & {"speed_to_lead", "quote_followup"}, out["shortlist"])

    def test_priya_inbox(self) -> None:
        out = score_session(PRIYA, hours="6-10", urgency="weekly_pain", trust="2")
        top = set(_ids(out)[:3])
        self.assertTrue(top & {"inbox_triage", "followup_nudge"}, out["shortlist"])

    def test_not_me_does_not_promote(self) -> None:
        out = score_session({"eob_still_cant_tell_what_we_owe": "not_me"})
        self.assertEqual(out["shortlist"], [])

    def test_example_digest_validates_required(self) -> None:
        path = ROOT / "examples" / "digest-maya.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads((ROOT / "digest.schema.json").read_text(encoding="utf-8"))
        for key in schema["required"]:
            self.assertIn(key, data)


if __name__ == "__main__":
    unittest.main()
