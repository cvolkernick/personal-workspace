#!/usr/bin/env python3
"""Unit tests for the Forge daily brief (no network)."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "forge_daily_brief.py"


def _load():
    spec = importlib.util.spec_from_file_location("forge_daily_brief", MOD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["forge_daily_brief"] = module
    spec.loader.exec_module(module)
    return module


M = _load()

LAST = datetime(2026, 9, 30, 12, 5, tzinfo=timezone.utc)
AS_OF = datetime(2026, 10, 1, 12, 10, tzinfo=timezone.utc)


def _issue(number, title, state, *labels):
    return {
        "number": number,
        "title": title,
        "state": state,
        "labels": [{"name": label} for label in labels],
    }


def _pr(number, title, merged_at, *closes):
    return {
        "number": number,
        "title": title,
        "mergedAt": merged_at,
        "closingIssuesReferences": [{"number": n} for n in closes],
    }


def _section(text, heading):
    body = text.split(f"## {heading}", 1)[1]
    body = body.split("\n## ", 1)[0]
    return body


class TestOvernightClose(unittest.TestCase):
    def test_closed_issue_is_not_ready_in_progress_or_blocker(self):
        # #948 and #949 closed overnight. A carried blocker list still names them.
        open_issues = [
            _issue(960, "stale bitcoin feeds", "open", "status:ready", "spec"),
            _issue(983, "stale brief", "open", "status:in-progress", "spec"),
            _issue(908, "ynab tap", "open", "status:pending-review"),
            _issue(948, "mining band", "closed", "status:in-progress", "spec"),
        ]
        by_number = {
            "948": _issue(948, "mining band", "closed", "spec"),
            "949": _issue(949, "manual log", "closed", "fitdash"),
            "962": _issue(962, "scheduler", "closed"),
        }
        brief = M.build_brief(
            open_issues=open_issues,
            merged_prs=[
                _pr(965, "mining band", "2026-10-01T07:45:00Z", 948),
                _pr(940, "older", "2026-09-29T20:30:22Z", 938),
            ],
            blocker_candidates=[948, 949, 962, 960],
            fetch_one=lambda number: by_number.get(str(number)),
            pi_facts=[{"text": "prism FitDash SHA unconfirmed", "as_of": "2026-09-27"}],
            last_brief=LAST,
            as_of=AS_OF,
        )
        for heading in ("Ready", "In progress", "Blockers"):
            section = _section(brief["text"], heading)
            self.assertNotIn("#948", section)
            self.assertNotIn("#949", section)
            self.assertNotIn("#962", section)
        self.assertEqual([item["number"] for item in brief["ready"]], [960])
        self.assertEqual([item["number"] for item in brief["in_progress"]], [983])
        self.assertEqual([item["number"] for item in brief["blockers"]], [960])
        self.assertIn("PR #965", _section(brief["text"], "Shipped since last brief"))
        self.assertNotIn("PR #940", brief["text"])
        self.assertIn("last known 2026-09-27", _section(brief["text"], "Pi"))


class TestLiveState(unittest.TestCase):
    def test_three_issue_numbers_match_live_labels(self):
        open_issues = [
            _issue(952, "about page", "open", "status:ready"),
            _issue(944, "card chip", "open", "status:pending-review"),
            _issue(983, "stale brief", "open", "status:in-progress"),
        ]
        brief = M.build_brief(
            open_issues=open_issues,
            merged_prs=[],
            blocker_candidates=[],
            fetch_one=lambda number: None,
            pi_facts=[],
            last_brief=LAST,
            as_of=AS_OF,
        )
        self.assertEqual(M.board_mismatches(brief, open_issues), [])
        checked = {
            952: "ready",
            944: "pending_review",
            983: "in_progress",
        }
        for number, section in checked.items():
            listed = [item["number"] for item in brief[section]]
            self.assertIn(number, listed)
            live = M.normalize_issue(next(i for i in open_issues if i["number"] == number))
            self.assertEqual(live["state"], "open")


class TestShippedWindow(unittest.TestCase):
    def test_shipped_matches_merged_at_or_after_last_brief(self):
        prs = [
            _pr(965, "in window", "2026-09-30T12:05:00Z", 948),
            _pr(966, "after", "2026-10-01T07:56:00Z", 949),
            _pr(925, "before", "2026-09-28T20:39:27Z", 916),
            _pr(100, "same day before", "2026-09-30T08:00:00Z"),
        ]
        shipped = M.shipped_since(prs, LAST)
        self.assertEqual([pr["number"] for pr in shipped], [965, 966])

    def test_snapshot_source_uses_the_same_window(self):
        snapshot = {
            "open_issues": [_issue(960, "feeds", "open", "status:ready")],
            "merged_prs": [
                _pr(966, "after", "2026-10-01T07:56:00Z", 949),
                _pr(925, "before", "2026-09-28T20:39:27Z", 916),
            ],
            "issues_by_number": {"949": _issue(949, "manual log", "closed")},
        }
        brief = M.build_from_sources(
            repo="cvolkernick/personal-workspace",
            last_brief=LAST,
            as_of=AS_OF,
            blocker_candidates=[949],
            pi_facts=[],
            snapshot=snapshot,
        )
        self.assertEqual([pr["number"] for pr in brief["shipped"]], [966])
        self.assertEqual(brief["blockers"], [])
        self.assertNotIn("#949", _section(brief["text"], "Blockers"))


class TestCliSnapshot(unittest.TestCase):
    def test_main_prints_live_sections_from_snapshot(self):
        snapshot = {
            "open_issues": [
                _issue(960, "feeds", "open", "status:ready"),
                _issue(948, "closed overnight", "closed", "status:in-progress"),
            ],
            "merged_prs": [_pr(965, "mining band", "2026-10-01T07:45:00Z", 948)],
            "issues_by_number": {"948": _issue(948, "mining band", "closed", "spec")},
        }
        path = ROOT / "tests" / "fixtures" / "forge_daily_brief_cli.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot))
        self.addCleanup(path.unlink, missing_ok=True)
        buffer = StringIO()
        with redirect_stdout(buffer):
            code = M.main(
                [
                    "--last-brief",
                    "2026-09-30T12:05:00Z",
                    "--as-of",
                    "2026-10-01T12:10:00Z",
                    "--blocker",
                    "948",
                    "--pi",
                    "prism SHA unconfirmed|2026-09-27",
                    "--snapshot",
                    str(path),
                ]
            )
        self.assertEqual(code, 0)
        text = buffer.getvalue()
        self.assertNotIn("#948", _section(text, "In progress"))
        self.assertNotIn("#948", _section(text, "Blockers"))
        self.assertIn("PR #965", _section(text, "Shipped since last brief"))
        self.assertIn("last known 2026-09-27", text)
        self.assertIn("#960", _section(text, "Ready"))


class TestGhJson(unittest.TestCase):
    def test_strips_forced_tty_color(self):
        payload = [{"number": 960, "state": "OPEN"}]

        class Proc:
            returncode = 0
            stdout = "\x1b[1;37m" + json.dumps(payload) + "\x1b[m"
            stderr = ""

        with mock.patch.object(M.subprocess, "run", return_value=Proc()) as run:
            parsed = M.gh_json(["issue", "list"])
        self.assertEqual(parsed, payload)
        env = run.call_args.kwargs["env"]
        self.assertNotIn("GH_FORCE_TTY", env)
        self.assertEqual(env["NO_COLOR"], "1")


class TestPiFacts(unittest.TestCase):
    def test_undated_pi_fact_is_rejected(self):
        with self.assertRaises(ValueError):
            M.build_brief(
                open_issues=[],
                merged_prs=[],
                blocker_candidates=[],
                fetch_one=lambda number: None,
                pi_facts=[{"text": "sha unknown", "as_of": ""}],
                last_brief=LAST,
                as_of=AS_OF,
            )


if __name__ == "__main__":
    unittest.main()
