#!/usr/bin/env python3
"""Unit tests for eng_gate_post_merge (no network)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "eng_gate_post_merge.py"


def _load():
    spec = importlib.util.spec_from_file_location("eng_gate_post_merge", MOD)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["eng_gate_post_merge"] = m
    spec.loader.exec_module(m)
    return m


M = _load()


class TestParseIssueRefs(unittest.TestCase):
    def test_fixes(self):
        self.assertEqual(M.parse_issue_refs("Fixes #58"), [58])

    def test_closes_multiple(self):
        self.assertEqual(
            M.parse_issue_refs("Closes #1\nResolves #2\nFixes #1"),
            [1, 2],
        )

    def test_title_and_body(self):
        self.assertEqual(
            M.parse_issue_refs("feat: board done (#58)", "Also closes #58"),
            [58],
        )


class TestMarkIssue(unittest.TestCase):
    def test_open_issue_rejected(self):
        with mock.patch.object(M, "rest", return_value={"state": "open", "html_url": "u", "node_id": "I_1"}):
            with mock.patch.object(M, "find_issue_item", return_value={"item_id": "PVTI_1", "status": "Pending Review"}):
                out = M.mark_issue(58)
        self.assertFalse(out["ok"])
        self.assertIn("still open", out["error"])

    def test_closed_sets_done(self):
        with mock.patch.object(M, "rest", return_value={"state": "closed", "html_url": "u", "node_id": "I_1"}):
            with mock.patch.object(
                M,
                "find_issue_item",
                return_value={"item_id": "PVTI_1", "status": "Pending Review"},
            ):
                with mock.patch.object(M, "set_status") as ss:
                    out = M.mark_issue(58)
        self.assertTrue(out["ok"])
        self.assertEqual(out["board_status"], "Done")
        ss.assert_called_once_with("PVTI_1", "Done")

    def test_residual_in_progress(self):
        with mock.patch.object(M, "rest", return_value={"state": "closed", "html_url": "u", "node_id": "I_1"}):
            with mock.patch.object(
                M,
                "find_issue_item",
                return_value={"item_id": "PVTI_1", "status": "Pending Review"},
            ):
                with mock.patch.object(M, "set_status") as ss:
                    out = M.mark_issue(58, residual="await Pi health")
        self.assertTrue(out["ok"])
        self.assertEqual(out["board_status"], "In Progress")
        ss.assert_called_once_with("PVTI_1", "In Progress")

    def test_dry_run_no_mutate(self):
        with mock.patch.object(M, "rest", return_value={"state": "closed", "html_url": "u", "node_id": "I_1"}):
            with mock.patch.object(
                M,
                "find_issue_item",
                return_value={"item_id": "PVTI_1", "status": "Pending Review"},
            ):
                with mock.patch.object(M, "set_status") as ss:
                    out = M.mark_issue(58, dry_run=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["dry_run"])
        ss.assert_not_called()


class TestFromPr(unittest.TestCase):
    def test_not_merged(self):
        with mock.patch.object(M, "rest", return_value={"merged": False, "state": "open"}):
            out = M.from_pr(47)
        self.assertFalse(out["ok"])

    def test_merged_with_fixes(self):
        pr = {
            "merged": True,
            "body": "Closes #58\n\n## Summary",
            "title": "feat(board): done after merge",
        }
        with mock.patch.object(M, "rest", return_value=pr):
            with mock.patch.object(
                M,
                "mark_issue",
                return_value={"ok": True, "number": 58, "board_status": "Done"},
            ) as mi:
                out = M.from_pr(47)
        self.assertTrue(out["ok"])
        mi.assert_called_once()


class TestSweep(unittest.TestCase):
    def test_finds_pending_review_closed(self):
        items = [
            {
                "kind": "Issue",
                "number": 9,
                "title": "stale",
                "state": "CLOSED",
                "status": "Pending Review",
                "repo": "cvolkernick/personal-workspace",
                "closed_at": "2020-01-01T00:00:00Z",
                "item_id": "PVTI_9",
                "url": "https://example/9",
            },
            {
                "kind": "Issue",
                "number": 10,
                "title": "ok",
                "state": "CLOSED",
                "status": "Done",
                "repo": "cvolkernick/personal-workspace",
                "closed_at": "2020-01-01T00:00:00Z",
                "item_id": "PVTI_10",
                "url": "https://example/10",
            },
        ]
        with mock.patch.object(M, "fetch_board_items", return_value=items):
            out = M.sweep(max_hours=24)
        self.assertEqual(out["stuck_count"], 1)
        self.assertEqual(out["stuck"][0]["number"], 9)
        self.assertTrue(out["stuck"][0]["over_sla"])


if __name__ == "__main__":
    unittest.main()
