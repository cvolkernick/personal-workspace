#!/usr/bin/env python3
"""Unit tests for buzz_board_cli (mocked network)."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from buzz_board_cli import cmd_list, cmd_show, main


class TestBuzzBoardCli(unittest.TestCase):
    def test_list_filters_status(self) -> None:
        fake = {
            "ok": True,
            "board": {"title": "Buzz Board", "url": "https://example/projects/1"},
            "counts": {"Ready": 1, "In Progress": 0},
            "columns": {
                "Parked": [],
                "Validate ($0)": [],
                "Ready": [
                    {
                        "number": 20,
                        "title": "Horizon",
                        "status": "Ready",
                        "url": "https://github.com/x/y/issues/20",
                        "repo": "cvolkernick/personal-workspace",
                        "type": "Issue",
                        "item_id": "PVTI_1",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                ],
                "In Progress": [],
                "Done": [],
            },
            "uncategorized": [],
        }
        ns = mock.Mock(status="Ready", include_done=False, format="json")
        with mock.patch("buzz_board_cli.sprint_payload", return_value=fake):
            with mock.patch("sys.stdout") as out:
                # capture via real stdout
                pass
        with mock.patch("buzz_board_cli.sprint_payload", return_value=fake):
            import io
            import contextlib

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cmd_list(ns)
            self.assertEqual(code, 0)
            data = json.loads(buf.getvalue())
            self.assertTrue(data["ok"])
            self.assertEqual(data["item_count"], 1)
            self.assertEqual(data["items"][0]["number"], 20)
            self.assertEqual(data["decision"], "stay-on-github")

    def test_show_issue(self) -> None:
        issue = {
            "number": 21,
            "title": "Board access",
            "state": "open",
            "html_url": "https://github.com/cvolkernick/personal-workspace/issues/21",
            "body": "AC here",
            "labels": [{"name": "workflow"}],
        }
        board = {
            "ok": True,
            "columns": {
                "In Progress": [
                    {
                        "number": 21,
                        "status": "In Progress",
                        "item_id": "PVTI_21",
                        "repo": "cvolkernick/personal-workspace",
                    }
                ],
                "Ready": [],
                "Parked": [],
                "Validate ($0)": [],
                "Done": [],
            },
            "uncategorized": [],
        }
        ns = mock.Mock(number=21)
        with mock.patch(
            "buzz_board_cli._rest", return_value=(200, issue)
        ), mock.patch("buzz_board_cli.sprint_payload", return_value=board):
            import io
            import contextlib

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cmd_show(ns)
            self.assertEqual(code, 0)
            data = json.loads(buf.getvalue())
            self.assertEqual(data["number"], 21)
            self.assertEqual(data["board_status"], "In Progress")
            self.assertEqual(data["project_item_id"], "PVTI_21")

    def test_main_auth_missing_token(self) -> None:
        with mock.patch("buzz_board_cli.credentials_status", return_value={
            "ok": False,
            "token_present": False,
        }):
            # auth command still runs
            code = main(["auth"])
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
