#!/usr/bin/env python3
"""#1074 quota-day tests. No network, no OAuth, no live writer."""

from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "youtube_groom_quota.py"
WRITER = Path("/tmp/youtube_groom_pi.py")

LA = ZoneInfo("America/Los_Angeles")


def _load():
    spec = importlib.util.spec_from_file_location("youtube_groom_quota", MOD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["youtube_groom_quota"] = module
    spec.loader.exec_module(module)
    return module


Q = _load()


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, msg: str, *args: object) -> None:
        self.lines.append(msg % args if args else msg)

    def exception(self, *args: object) -> None:
        raise AssertionError(f"traceback logger used: {args}")


class TestQuotaDay(unittest.TestCase):
    def test_pdt_keeps_at_0659z_and_resets_at_0701z(self):
        early = _at("2026-10-05T06:59:00Z")
        state = {"quota": {"day": "2026-10-04", "units": 100, "tz": Q.QUOTA_TZ}}
        quota = Q.Quota(state, 8000, now=early)
        self.assertEqual(quota.used, 100)
        self.assertEqual(state["quota"]["day"], "2026-10-04")
        self.assertEqual(early.astimezone(LA).date().isoformat(), "2026-10-04")

        late = _at("2026-10-05T07:01:00Z")
        state = {"quota": {"day": "2026-10-04", "units": 100, "tz": Q.QUOTA_TZ}}
        quota = Q.Quota(state, 8000, now=late)
        self.assertEqual(quota.used, 0)
        self.assertEqual(state["quota"]["day"], "2026-10-05")
        self.assertEqual(late.astimezone(LA).strftime("%H:%M"), "00:01")

    def test_pst_keeps_at_0759z_and_resets_at_0801z(self):
        early = _at("2026-12-15T07:59:00Z")
        state = {"quota": {"day": "2026-12-14", "units": 40, "tz": Q.QUOTA_TZ}}
        quota = Q.Quota(state, 8000, now=early)
        self.assertEqual(quota.used, 40)
        self.assertEqual(state["quota"]["day"], "2026-12-14")

        late = _at("2026-12-15T08:01:00Z")
        state = {"quota": {"day": "2026-12-14", "units": 40, "tz": Q.QUOTA_TZ}}
        quota = Q.Quota(state, 8000, now=late)
        self.assertEqual(quota.used, 0)
        self.assertEqual(state["quota"]["day"], "2026-12-15")

    def test_utc_midnight_does_not_reset_the_pacific_day(self):
        now = _at("2026-10-05T00:01:00Z")
        state = {"quota": {"day": "2026-10-04", "units": 2552, "tz": Q.QUOTA_TZ}}
        quota = Q.Quota(state, 8000, now=now)
        self.assertEqual(quota.used, 2552)
        self.assertEqual(state["quota"]["day"], "2026-10-04")
        self.assertEqual(now.astimezone(LA).date().isoformat(), "2026-10-04")

    def test_legacy_utc_record_keeps_units_inside_the_open_pacific_day(self):
        now = _at("2026-10-05T02:00:00Z")
        state = {"quota": {"day": "2026-10-05", "units": 2552}}
        quota = Q.Quota(state, 8000, now=now)
        self.assertEqual(quota.used, 2552)
        self.assertEqual(state["quota"]["tz"], Q.QUOTA_TZ)
        self.assertEqual(state["quota"]["day"], "2026-10-04")


class TestGroomGate(unittest.TestCase):
    def test_skip_makes_zero_api_calls_and_exits_0(self):
        now = _at("2026-10-05T18:00:00Z")
        state = {"quota": {"day": "2026-10-05", "units": 7950, "tz": Q.QUOTA_TZ}}
        calls: list[str] = []
        saved: list[dict] = []
        log = _Log()

        def run_body(quota: Q.Quota) -> dict:
            calls.append("api")
            return {"ok": True}

        result = Q.groom(
            SimpleNamespace(quota_budget=8000, dry_run=False),
            state,
            now=now,
            run_body=run_body,
            save_state=saved.append,
            log=log,
        )
        self.assertEqual(calls, [])
        self.assertEqual(result["skipped"], "quota_cap")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(saved[0]["last_result"]["skipped"], "quota_cap")
        self.assertEqual(state["quota"]["units"], 7950)
        self.assertTrue(any(line.startswith("quota_skip used=7950 cap=8000") for line in log.lines))
        self.assertFalse(any("groom failed" in line for line in log.lines))

    def test_cap_mid_tick_saves_partial_without_traceback(self):
        now = _at("2026-10-05T16:00:00Z")
        state = {"quota": {"day": "2026-10-05", "units": 100, "tz": Q.QUOTA_TZ}}
        saved: list[dict] = []
        log = _Log()

        def run_body(quota: Q.Quota) -> dict:
            quota.charge("list", 1)
            state["last_result"] = {"added": 2, "deleted": 1, "at": "t"}
            state["_tick_partial"] = {"added": 2, "deleted": 1}
            raise Q.QuotaCapReached("list", 1, quota.used, quota.soft_cap)

        result = Q.groom(
            SimpleNamespace(quota_budget=8000, dry_run=False),
            state,
            now=now,
            run_body=run_body,
            save_state=saved.append,
            log=log,
        )
        self.assertEqual(result["skipped"], "quota_cap")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["added"], 2)
        self.assertEqual(result["deleted"], 1)
        self.assertTrue(result["partial"])
        self.assertEqual(saved[-1]["quota"]["units"], 101)
        self.assertEqual(saved[-1]["last_result"]["added"], 2)
        blob = "\n".join(log.lines)
        self.assertNotIn("groom failed", blob)
        self.assertNotIn("Traceback", blob)
        self.assertIn("quota soft-cap would exceed", blob)


class TestPacingAndQuarantine(unittest.TestCase):
    def test_open_day_does_not_front_load_four_searches(self):
        now = _at("2026-10-05T07:05:00Z")
        self.assertLessEqual(Q.paced_max_searches(8000, now), 1)

    def test_three_404s_quarantine_the_seed(self):
        state: dict = {}
        self.assertFalse(Q.seed_quarantined(state, "UCdead"))
        self.assertEqual(Q.note_seed_404(state, "UCdead"), 1)
        self.assertEqual(Q.note_seed_404(state, "UCdead"), 2)
        self.assertFalse(Q.seed_quarantined(state, "UCdead"))
        self.assertEqual(Q.note_seed_404(state, "UCdead"), 3)
        self.assertTrue(Q.seed_quarantined(state, "UCdead"))
        Q.clear_seed_404(state, "UCdead")
        self.assertFalse(Q.seed_quarantined(state, "UCdead"))


class TestWriterPatch(unittest.TestCase):
    def test_live_writer_patch_parses_and_is_idempotent(self):
        if not WRITER.is_file():
            self.skipTest("live writer snapshot is not on this machine")
        original = WRITER.read_text(encoding="utf-8")
        patched = Q.patch_writer_source(original)
        ast.parse(patched)
        self.assertIn("quota_groom(", patched)
        self.assertIn("self.quota.require(kind, n)", patched)
        self.assertNotIn("class Quota:", patched)
        self.assertLess(patched.index("quota_groom("), patched.index("def _groom_after_gate"))
        self.assertLess(patched.index("def _groom_after_gate"), patched.index("yt = YT("))
        self.assertIn('result.get("skipped") == "quota_cap"', patched)
        self.assertLess(
            patched.index('result.get("skipped") == "quota_cap"'),
            patched.index('"hour0": result["hour0"]'),
        )
        again = Q.patch_writer_source(patched)
        self.assertEqual(again, patched)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "youtube_groom.py"
            path.write_text(original, encoding="utf-8")
            result = Q.patch_writer_file(path, today="20261006")
            self.assertTrue(result["changed"])
            backup = Path(tmp) / "youtube_groom.py.bak-20261006-1074"
            self.assertTrue(backup.is_file())
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
