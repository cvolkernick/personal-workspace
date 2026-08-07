"""Unit tests for Meet Recordings → B2 (no network)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from b2_kb.meet_recordings import (
    format_channel_summary,
    ingest,
    mime_policy,
    plan,
    status,
)


class TestMimePolicy(unittest.TestCase):
    def test_docs_ingest(self):
        self.assertEqual(
            mime_policy("application/vnd.google-apps.document"), "ingest"
        )

    def test_video_skip(self):
        self.assertEqual(mime_policy("video/mp4"), "skip")

    def test_pdf_ingest(self):
        self.assertEqual(mime_policy("application/pdf"), "ingest")


class TestPlanIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "inbox" / "meta").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_to_fetch_and_skip_video(self):
        payload = [
            {
                "file_id": "doc1",
                "name": "Zoom call - Notes by Gemini",
                "modified_time": "2026-08-06T01:00:00Z",
                "mime_type": "application/vnd.google-apps.document",
            },
            {
                "file_id": "vid1",
                "name": "recording.mp4",
                "modified_time": "2026-08-06T01:00:00Z",
                "mime_type": "video/mp4",
            },
        ]
        out = plan(payload, self.vault)
        self.assertTrue(out["ok"])
        self.assertEqual(out["to_fetch_count"], 1)
        self.assertEqual(out["to_fetch"][0]["file_id"], "doc1")
        self.assertTrue(out["to_fetch"][0]["prefer"])
        self.assertEqual(out["skip_count"], 1)
        self.assertTrue(out["notify"])

    def test_ingest_promote_and_dedupe(self):
        item = {
            "file_id": "doc1",
            "name": "Zoom call with Alex - Notes by Gemini",
            "modified_time": "2026-08-06T01:50:57Z",
            "mime_type": "application/vnd.google-apps.document",
            "web_view_link": "https://docs.google.com/document/d/doc1",
            "text": "## Summary\n\nTalked about Buzz and Turo hosts.\n\n## Details\n\nMore body.",
        }
        r1 = ingest([item], self.vault, auto_promote=True)
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["promoted_count"], 1)
        self.assertTrue(r1["notify"])
        cap = r1["promoted"][0]["capture"]
        self.assertTrue((self.vault / cap).is_file())
        body = (self.vault / cap).read_text(encoding="utf-8")
        self.assertIn("source_type: gdrive_meet", body)
        self.assertIn("Talked about Buzz", body)

        r2 = ingest([item], self.vault, auto_promote=True)
        self.assertEqual(r2["promoted_count"], 0)
        self.assertEqual(r2["unchanged_count"], 1)
        self.assertFalse(r2["notify"])
        self.assertEqual(format_channel_summary(r2), "")

    def test_empty_transcript_quiet(self):
        item = {
            "file_id": "empty1",
            "name": "Empty notes",
            "modified_time": "2026-08-07T00:00:00Z",
            "mime_type": "application/vnd.google-apps.document",
            "text": "   \n  ",
        }
        r = ingest([item], self.vault, auto_promote=True)
        self.assertEqual(r["skipped_count"], 1)
        self.assertEqual(r["skipped"][0]["reason"], "empty_transcript")
        self.assertFalse(r["notify"])
        self.assertEqual(r["channel_summary"], "")

    def test_plan_unchanged_after_ingest(self):
        item = {
            "file_id": "doc2",
            "name": "Notes by Gemini",
            "modified_time": "2026-08-01T12:00:00Z",
            "mime_type": "application/vnd.google-apps.document",
            "text": "Hello vault",
        }
        ingest([item], self.vault, auto_promote=True)
        meta_only = {
            "file_id": "doc2",
            "name": "Notes by Gemini",
            "modified_time": "2026-08-01T12:00:00Z",
            "mime_type": "application/vnd.google-apps.document",
        }
        p = plan([meta_only], self.vault)
        self.assertEqual(p["unchanged_count"], 1)
        self.assertEqual(p["to_fetch_count"], 0)
        self.assertFalse(p["notify"])

    def test_status_standing_order(self):
        st = status(self.vault)
        self.assertIn("standing_order", st)
        self.assertEqual(st["standing_order"]["cadence"], "daily")
        self.assertIn("never", st["standing_order"])


if __name__ == "__main__":
    unittest.main()
