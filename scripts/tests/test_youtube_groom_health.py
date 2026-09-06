#!/usr/bin/env python3
"""Health/alert tests for youtube-groom (no network, no OAuth, no writer)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "youtube_groom_health.py"
POLICY = ROOT / "youtube_groom.py"
CAPS_MD = ROOT.parent / "ops" / "YOUTUBE_GROOM_CAPS.md"
HEALTH_MD = ROOT.parent / "ops" / "YOUTUBE_GROOM_HEALTH.md"


def _load():
    spec = importlib.util.spec_from_file_location("youtube_groom_health", MOD)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["youtube_groom_health"] = m
    spec.loader.exec_module(m)
    return m


H = _load()
NOW = datetime(2026, 9, 6, 14, 0, tzinfo=timezone.utc)


def _log(*lines: str) -> str:
    return "\n".join(lines) + "\n"


SUCCESS = (
    "2026-09-06T12:00:10+00:00 hour0=False dry=False listed=41 "
    "del=0 {} remain=41 add=0 skip={} quota=200"
)
SUCCESS_INFO = (
    "2026-09-06 12:00:10,123 INFO 2026-09-06T12:00:10+00:00 "
    "hour0=False dry=False listed=41 del=0"
)
GRANT = (
    "2026-09-06 13:00:05,001 ERROR groom failed\n"
    "Traceback (most recent call last):\n"
    "  File \"youtube_groom.py\", line 1, in main\n"
    "google.auth.exceptions.RefreshError: ('invalid_grant: Token has been "
    "expired or revoked.', {'error': 'invalid_grant'})\n"
)


class RecordingPoster:
    def __init__(self, *, accepted: bool = True) -> None:
        self.calls: list[tuple[str, list[list[str]]]] = []
        self.accepted = accepted

    def __call__(self, content: str, tags: list[list[str]]) -> dict:
        self.calls.append((content, tags))
        return {"accepted": self.accepted, "id": "evt-test" if self.accepted else None, "error": None if self.accepted else "boom"}


class TestScanLog(unittest.TestCase):
    def test_success_listed(self):
        scan = H.scan_log(_log(SUCCESS))
        self.assertEqual(scan.last_success_at, datetime(2026, 9, 6, 12, 0, 10, tzinfo=timezone.utc))
        self.assertIsNone(scan.last_failure_kind)

    def test_info_listed_counts(self):
        scan = H.scan_log(_log(SUCCESS_INFO))
        self.assertIsNotNone(scan.last_success_at)
        self.assertIn("listed=41", scan.last_success_line)

    def test_error_line_with_listed_is_not_success(self):
        scan = H.scan_log(_log("2026-09-06T12:00:10Z ERROR listed=0 failed"))
        self.assertIsNone(scan.last_success_at)

    def test_invalid_grant_and_refresh_error(self):
        scan = H.scan_log(_log(SUCCESS, GRANT))
        self.assertEqual(scan.last_failure_kind, "invalid_grant")
        self.assertIsNotNone(scan.last_failure_at)
        self.assertGreaterEqual(scan.last_failure_at, scan.last_success_at)

    def test_traceback_inherits_error_timestamp(self):
        scan = H.scan_log(GRANT)
        self.assertEqual(scan.last_failure_kind, "invalid_grant")
        self.assertEqual(scan.last_failure_at, datetime(2026, 9, 6, 13, 0, 5, tzinfo=timezone.utc))

    def test_uncaught_groom_failed(self):
        scan = H.scan_log(
            _log(
                SUCCESS,
                "2026-09-06 13:05:00,000 ERROR groom failed",
            )
        )
        self.assertEqual(scan.last_failure_kind, "uncaught")


class TestClassify(unittest.TestCase):
    def test_recent_success_healthy(self):
        scan = H.scan_log(_log(SUCCESS))
        status, reason = H.classify_status(scan, now=NOW, writer_present=True)
        self.assertEqual(status, "healthy")
        self.assertEqual(reason, "ok")

    def test_stale_success_broken(self):
        old = "2026-09-06T10:00:00+00:00 hour0=False dry=False listed=41 del=0"
        scan = H.scan_log(_log(old))
        status, reason = H.classify_status(scan, now=NOW, writer_present=True)
        self.assertEqual(status, "broken")
        self.assertEqual(reason, "stale_success")

    def test_last_tick_grant_beats_recent_success(self):
        scan = H.scan_log(_log(SUCCESS, GRANT))
        status, reason = H.classify_status(scan, now=NOW, writer_present=True)
        self.assertEqual(status, "broken")
        self.assertEqual(reason, "invalid_grant")

    def test_missing_log_skipped_off_prod(self):
        scan = H.LogScan(missing_log=True)
        status, reason = H.classify_status(scan, now=NOW, writer_present=False)
        self.assertEqual(status, "skipped")
        self.assertEqual(reason, "no_prod_log")

    def test_missing_log_broken_on_prod(self):
        scan = H.LogScan(missing_log=True)
        status, reason = H.classify_status(scan, now=NOW, writer_present=True)
        self.assertEqual(status, "broken")
        self.assertEqual(reason, "missing_log")


class TestDedupAlerts(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory(prefix="yg-health-")
        self.dir = Path(self.td.name)
        self.log = self.dir / "groom.log"
        self.health = self.dir / "health.json"

    def tearDown(self) -> None:
        self.td.cleanup()

    def _run(self, text: str, poster: RecordingPoster, *, now: datetime = NOW, dry: bool = False):
        self.log.write_text(text, encoding="utf-8")
        return H.run_check(
            log_path=self.log,
            health_path=self.health,
            now=now,
            dry_run=dry,
            poster=poster,
            writer_present=True,
        )

    def test_healthy_no_alert(self):
        poster = RecordingPoster()
        result = self._run(_log(SUCCESS), poster)
        self.assertEqual(result["status"], "healthy")
        self.assertIsNone(result["would_alert"])
        self.assertEqual(poster.calls, [])
        self.assertTrue(self.health.is_file())

    def test_broken_transition_one_alert(self):
        poster = RecordingPoster()
        first = self._run(_log(SUCCESS, GRANT), poster)
        self.assertEqual(first["status"], "broken")
        self.assertEqual(first["would_alert"], "broken")
        self.assertEqual(len(poster.calls), 1)
        content, tags = poster.calls[0]
        self.assertIn("@Grok", content)
        self.assertIn("BROKEN", content)
        self.assertNotIn(H.CHRIS_PUBKEY, content)
        self.assertIn(["p", H.GROK_PUBKEY], tags)
        self.assertFalse(any(t[0] == "p" and t[1] == H.CHRIS_PUBKEY for t in tags))

        poster2 = RecordingPoster()
        second = self._run(_log(SUCCESS, GRANT), poster2, now=NOW + timedelta(minutes=15))
        self.assertEqual(second["status"], "broken")
        self.assertIsNone(second["would_alert"])
        self.assertEqual(poster2.calls, [])

    def test_failed_post_retries_same_transition(self):
        fail = RecordingPoster(accepted=False)
        first = self._run(_log(GRANT), fail)
        self.assertEqual(first["status"], "broken")
        self.assertEqual(first["would_alert"], "broken")
        self.assertFalse(first["posted"])
        self.assertFalse(self.health.is_file())

        ok = RecordingPoster()
        second = self._run(_log(GRANT), ok)
        self.assertEqual(len(ok.calls), 1)
        self.assertTrue(second["posted"])
        self.assertTrue(self.health.is_file())

    def test_recovery_one_alert(self):
        poster = RecordingPoster()
        self._run(_log(GRANT), poster)
        rec = RecordingPoster()
        later = NOW + timedelta(hours=1)
        success = (
            "2026-09-06T15:00:10+00:00 hour0=False dry=False listed=28 "
            "del=13 remain=28 add=9 skip={} quota=400"
        )
        result = self._run(_log(GRANT, success), rec, now=later)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["would_alert"], "recovery")
        self.assertEqual(len(rec.calls), 1)
        self.assertIn("recovered", rec.calls[0][0])
        self.assertIn("@Grok", rec.calls[0][0])

        quiet = RecordingPoster()
        third = self._run(_log(GRANT, success), quiet, now=later + timedelta(minutes=15))
        self.assertEqual(third["status"], "healthy")
        self.assertIsNone(third["would_alert"])
        self.assertEqual(quiet.calls, [])

    def test_daily_reminder_after_24h(self):
        poster = RecordingPoster()
        self._run(_log(GRANT), poster)
        quiet = RecordingPoster()
        self._run(_log(GRANT), quiet, now=NOW + timedelta(hours=12))
        self.assertEqual(quiet.calls, [])
        remind = RecordingPoster()
        result = self._run(_log(GRANT), remind, now=NOW + timedelta(hours=25))
        self.assertEqual(result["would_alert"], "reminder")
        self.assertEqual(len(remind.calls), 1)
        self.assertIn("still **BROKEN**", remind.calls[0][0])

    def test_off_prod_does_not_post(self):
        poster = RecordingPoster()
        self.log.write_text(_log(GRANT), encoding="utf-8")
        result = H.run_check(
            log_path=self.log,
            health_path=self.health,
            now=NOW,
            dry_run=False,
            poster=poster,
            writer_present=False,
        )
        self.assertEqual(result["status"], "broken")
        self.assertEqual(result["would_alert"], "broken")
        self.assertFalse(result["posted"])
        self.assertEqual(poster.calls, [])

    def test_dry_run_json_no_persist_no_post(self):
        poster = RecordingPoster()
        result = self._run(_log(GRANT), poster, dry=True)
        self.assertEqual(result["status"], "broken")
        self.assertEqual(result["would_alert"], "broken")
        self.assertEqual(poster.calls, [])
        self.assertFalse(self.health.is_file())
        self.assertTrue(result["dry_run"])

    def test_main_json_dry_run_exit_broken(self):
        import io
        from contextlib import redirect_stdout

        self.log.write_text(_log(GRANT), encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = H.main(["--dry-run", "--json", "--log", str(self.log), "--health", str(self.health)])
        self.assertEqual(rc, 1)
        self.assertIn('"status": "broken"', buf.getvalue())


class TestNoChrisAndNoWriterClobber(unittest.TestCase):
    def test_mention_tags_exclude_chris(self):
        tags = H.grok_mention_tags()
        pubkeys = [t[1] for t in tags if t[0] == "p"]
        self.assertEqual(pubkeys, [H.GROK_PUBKEY])
        self.assertNotIn(H.CHRIS_PUBKEY, pubkeys)

    def test_post_via_clock_refuses_chris_tag(self):
        with self.assertRaises(RuntimeError):
            H.post_via_clock("hi", [["p", H.CHRIS_PUBKEY]])

    def test_health_module_is_not_a_writer(self):
        src = MOD.read_text(encoding="utf-8")
        self.assertNotIn("googleapiclient", src)
        self.assertNotIn("InstalledAppFlow", src)
        self.assertNotIn("build(", src)
        self.assertIn("Not a second playlist writer", src)

    def test_policy_module_still_not_a_writer(self):
        src = POLICY.read_text(encoding="utf-8")
        self.assertIn("DO NOT copy this module over the Pi binary", src)

    def test_health_doc_exists(self):
        text = HEALTH_MD.read_text(encoding="utf-8")
        self.assertIn("health.json", text)
        self.assertIn("#workflow", text)
        self.assertIn("ops/board/youtube_groom_health.json", text)
        self.assertNotIn(H.CHRIS_PUBKEY, text)

    def test_caps_doc_points_at_health_without_changing_caps(self):
        text = CAPS_MD.read_text(encoding="utf-8")
        self.assertIn("YOUTUBE_GROOM_HEALTH.md", text)
        self.assertIn("FRESH_HOURS          = 168", text)
        self.assertIn("CAP                  = 200", text)
        self.assertIn("STALE_HARD_DAYS      = 7", text)


if __name__ == "__main__":
    unittest.main()
