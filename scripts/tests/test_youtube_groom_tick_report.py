#!/usr/bin/env python3
"""Tick listed/add/skip/quota report tests (no network, no OAuth, no writer)."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "youtube_groom_tick_report.py"
POLICY = ROOT / "youtube_groom.py"
HEALTH = ROOT / "youtube_groom_health.py"
CAPS_MD = ROOT.parent / "ops" / "YOUTUBE_GROOM_CAPS.md"
QUEUE_MD = ROOT.parent / "ops" / "YOUTUBE_QUEUE.md"
REPORT_MD = ROOT.parent / "ops" / "YOUTUBE_GROOM_TICK_REPORT.md"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "groom_tick_report.txt"
SERVICE = ROOT / "youtube-groom.service"
EXPORT = ROOT.parent / "scripts" / "export-day-packets.sh"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


R = _load(MOD, "youtube_groom_tick_report")
P = _load(POLICY, "youtube_groom_policy_for_tick_report")
NOW = datetime(2026, 9, 19, 14, 30, tzinfo=timezone.utc)

SCORING = (
    "2026-09-19T14:00:00+00:00 hour0=False dry=False listed=90 del=0 {} "
    "remain=90 add=0 skip={'fit=0': 12, 'throttled-decay': 4} quota=1000 house=100"
)
FILLED = (
    "2026-09-19T14:00:00+00:00 hour0=False dry=False listed=100 del=2 "
    "{'dup': 1, 'stale_7d': 1} remain=100 add=2 skip={} quota=2000 house=100"
)


class TestParseTicks(unittest.TestCase):
    def test_fixture_dedupes_info_and_append(self):
        ticks = R.parse_ticks(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(ticks), 5)
        last = ticks[-1]
        self.assertEqual(last["listed"], 60)
        self.assertEqual(last["add"], 3)
        self.assertEqual(last["skip"], {})
        self.assertEqual(last["quota"], 3690)
        self.assertEqual(last["house"], 100)
        self.assertEqual(last["remain"], 60)
        self.assertEqual(last["source"], "append")
        self.assertEqual(last["delete_reasons"]["dislike"], 1)

    def test_scoring_skip_parse(self):
        tick = R.parse_tick_line(SCORING)
        assert tick is not None
        self.assertEqual(tick["skip"]["fit=0"], 12)
        self.assertEqual(tick["skip"]["throttled-decay"], 4)
        self.assertTrue(R.skip_is_scoring(tick["skip"]))

    def test_error_listed_is_not_a_tick(self):
        line = "2026-09-19T14:00:00Z ERROR listed=0 add=0 skip={} quota=0"
        self.assertIsNone(R.parse_tick_line(line))


class TestDiagnosis(unittest.TestCase):
    def test_fixture_is_supply(self):
        text = FIXTURE.read_text(encoding="utf-8")
        report = R.build_report(text, now=NOW)
        self.assertEqual(report["diagnosis"]["verdict"], "supply")
        self.assertEqual(report["last_tick"]["listed"], 60)
        self.assertEqual(report["last_tick"]["add"], 3)
        self.assertEqual(report["last_tick"]["skip"], {})
        self.assertEqual(report["rollup_24h"]["skip_reasons"], {})
        self.assertGreaterEqual(report["rollup_24h"]["tick_count"], 3)
        self.assertEqual(report["not_issue"], 759)
        self.assertEqual(report["issue"], 838)
        self.assertFalse(report["copy_over_pi"])

    def test_listed_high_skip_scoring(self):
        report = R.build_report(SCORING + "\n", now=NOW)
        self.assertEqual(report["diagnosis"]["verdict"], "scoring")

    def test_filled_house(self):
        report = R.build_report(FILLED + "\n", now=NOW)
        self.assertEqual(report["diagnosis"]["verdict"], "filled")

    def test_mixed_low_listed_and_scoring_skip(self):
        line = (
            "2026-09-19T14:00:00+00:00 hour0=False dry=False listed=50 del=0 {} "
            "remain=50 add=0 skip={'fit=0': 9} quota=500 house=100"
        )
        report = R.build_report(line + "\n", now=NOW)
        self.assertEqual(report["diagnosis"]["verdict"], "mixed")


class TestQuotaLevers(unittest.TestCase):
    def test_headroom_blocks_half_hour_ticks(self):
        text = FIXTURE.read_text(encoding="utf-8")
        report = R.build_report(text, now=NOW)
        quota = report["quota"]
        self.assertEqual(quota["daily_soft_cap"], 8000)
        self.assertEqual(quota["youtube_daily_units"], 10000)
        self.assertEqual(quota["quota_used_today"], 3690)
        self.assertEqual(quota["quota_remaining_soft"], 4310)
        self.assertFalse(quota["crank_without_quota"])
        by_lever = {p["lever"]: p for p in quota["proposals"]}
        self.assertTrue(by_lever["broaden_seed_channel_set"]["apply"])
        self.assertFalse(by_lever["more_ticks_per_day"]["apply"])
        self.assertFalse(by_lever["raise_per_tick_add_cap"]["apply"])
        self.assertFalse(quota["half_hour_fits_soft"])

    def test_filled_does_not_apply_seed_crank(self):
        report = R.build_report(FILLED + "\n", now=NOW)
        by_lever = {p["lever"]: p for p in report["quota"]["proposals"]}
        self.assertFalse(by_lever["broaden_seed_channel_set"]["apply"])


class TestPersist(unittest.TestCase):
    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            log = td / "groom.log"
            log.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            report = td / "tick_report.json"
            jsonl = td / "ticks.jsonl"
            payload = R.run_report(
                log_path=log,
                report_path=report,
                jsonl_path=jsonl,
                dry_run=True,
                now=NOW,
            )
            self.assertTrue(payload["ok"])
            self.assertFalse(report.exists())
            self.assertFalse(jsonl.exists())
            self.assertTrue(payload["jsonl_appended"])

    def test_persist_and_jsonl_dedupe(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            log = td / "groom.log"
            log.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            report = td / "tick_report.json"
            jsonl = td / "ticks.jsonl"
            first = R.run_report(
                log_path=log,
                report_path=report,
                jsonl_path=jsonl,
                dry_run=False,
                now=NOW,
            )
            second = R.run_report(
                log_path=log,
                report_path=report,
                jsonl_path=jsonl,
                dry_run=False,
                now=NOW,
            )
            self.assertTrue(first["jsonl_appended"])
            self.assertFalse(second["jsonl_appended"])
            lines = [ln for ln in jsonl.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1)
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["diagnosis"]["verdict"], "supply")
            self.assertEqual(json.loads(lines[0])["listed"], 60)

    def test_missing_log_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = R.main(
                    [
                        "--dry-run",
                        "--json",
                        "--log",
                        str(td / "missing.log"),
                        "--report",
                        str(td / "tick_report.json"),
                        "--jsonl",
                        str(td / "ticks.jsonl"),
                    ]
                )
            self.assertEqual(rc, 2)
            self.assertIn('"ok": false', buf.getvalue())


class TestNoWriterAndLanding(unittest.TestCase):
    def test_module_is_not_a_writer(self):
        src = MOD.read_text(encoding="utf-8")
        self.assertNotIn("googleapiclient", src)
        self.assertNotIn("InstalledAppFlow", src)
        self.assertNotIn("build(", src)
        self.assertIn("not a second playlist writer", src.lower())
        self.assertIn("never copies", src.lower())
        self.assertIn("759", src)

    def test_policy_and_health_untouched_as_writer(self):
        self.assertIn("DO NOT copy this module over the Pi binary", POLICY.read_text(encoding="utf-8"))
        self.assertIn("Not a second playlist writer", HEALTH.read_text(encoding="utf-8"))

    def test_house_and_quota_match_policy(self):
        self.assertEqual(R.HOUSE_TARGET, P.HOUSE_TARGET)
        self.assertEqual(R.DAILY_SOFT_CAP, 8000)

    def test_docs_and_service_wire_report(self):
        text = REPORT_MD.read_text(encoding="utf-8")
        self.assertIn("tick_report.json", text)
        self.assertIn("ticks.jsonl", text)
        self.assertIn("ops/board/youtube_groom_tick_report.json", text)
        self.assertIn("#838", text)
        self.assertIn("not #759", text.lower())
        self.assertIn("supply", text.lower())
        self.assertNotIn("do remint", text.lower())

        caps = CAPS_MD.read_text(encoding="utf-8")
        self.assertIn("YOUTUBE_GROOM_TICK_REPORT.md", caps)

        queue = QUEUE_MD.read_text(encoding="utf-8")
        self.assertIn("tick_report.json", queue)
        self.assertIn("test_youtube_groom_tick_report", queue)

        service = SERVICE.read_text(encoding="utf-8")
        self.assertIn("youtube_groom_tick_report.py", service)
        self.assertIn("youtube_groom_health.py", service)
        self.assertIn("youtube_groom.py", service)
        # ExecStart still the live writer; report is StopPost only.
        self.assertRegex(service, r"ExecStart=.*/youtube_groom\.py")

        export = EXPORT.read_text(encoding="utf-8")
        self.assertIn("youtube_groom_tick_report.py", export)
        self.assertIn("ops/board/youtube_groom_tick_report.json", export)

    def test_board_tick_report_is_durable_untracked(self):
        marker = "ops/board/youtube_groom_tick_report.json"
        gi = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(marker, gi)
        sync = (ROOT.parent / "deploy" / "workspace_sync.sh").read_text(encoding="utf-8")
        self.assertGreaterEqual(sync.count(marker), 2)
        self.assertIn("preserve_durable", sync)


if __name__ == "__main__":
    unittest.main()
