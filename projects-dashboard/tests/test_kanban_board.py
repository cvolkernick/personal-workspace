"""Column rules, worker parse, pagination, and the write flag for the eng board."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

import kanban_board as kb  # noqa: E402

NOW = datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc)
UI_ROOT = DASH / "eng-kanban"


def issue(**overrides):
    base = {
        "number": 1,
        "title": "Example",
        "body": "",
        "state": "open",
        "state_reason": None,
        "closed_at": None,
        "url": "https://github.com/cvolkernick/personal-workspace/issues/1",
        "labels": [],
        "pull_requests": [],
    }
    base.update(overrides)
    base["url"] = f"https://github.com/cvolkernick/personal-workspace/issues/{base['number']}"
    return base


class FakeGitHub:
    def __init__(self, issues, *, fail_set=False):
        self.issues = {item["number"]: dict(item) for item in issues}
        self.calls = []
        self.fail_set = fail_set

    def get_issue(self, number):
        self.calls.append(("get_issue", number))
        return self.issues[number]

    def set_labels(self, number, labels):
        self.calls.append(("set_labels", number, list(labels)))
        if self.fail_set:
            raise kb.GitHubError("GitHub 500")
        self.issues[number]["labels"] = list(labels)

    def forbidden(self, key):
        raise AssertionError(f"forwarded {key}")

    def list_issue_nodes(self):
        raise AssertionError("list_issue_nodes was not stubbed")


class ColumnRules(unittest.TestCase):
    def card(self, **overrides):
        found = kb.classify(issue(**overrides), now=NOW)
        self.assertIsNotNone(found)
        return found

    def test_open_without_status_is_backlog(self) -> None:
        card = self.card(labels=["bug", "spec"])
        self.assertEqual(card["column"], "backlog")
        self.assertFalse(card["conflict"])

    def test_each_status_column(self) -> None:
        self.assertEqual(self.card(labels=["status:ready"])["column"], "ready")
        self.assertEqual(self.card(labels=["status:in-progress"])["column"], "in_progress")
        self.assertEqual(
            self.card(labels=["status:pending-review"])["column"], "pending_review"
        )
        self.assertEqual(self.card(labels=["status:done"])["column"], "done")

    def test_precedence_and_conflict_badge(self) -> None:
        card = self.card(
            labels=["status:ready", "status:in-progress", "status:pending-review", "bug"]
        )
        self.assertEqual(card["column"], "pending_review")
        self.assertTrue(card["conflict"])
        self.assertIn("label conflict", card["badges"])
        chip_names = [chip["name"] for chip in card["chips"]]
        self.assertEqual(chip_names, ["bug"])

        ready_over_done = self.card(labels=["status:done", "status:ready"])
        self.assertEqual(ready_over_done["column"], "ready")
        self.assertTrue(ready_over_done["conflict"])

    def test_parked_idea_and_human_only_badges_stay_in_backlog(self) -> None:
        card = self.card(labels=["status:parked", "parked", "idea", "human-only", "bug"])
        self.assertEqual(card["column"], "backlog")
        self.assertFalse(card["conflict"])
        self.assertEqual(
            card["badges"],
            ["status:parked", "parked", "idea", "human-only"],
        )
        self.assertEqual([chip["name"] for chip in card["chips"]], ["bug"])

    def test_closed_completed_recent_is_done(self) -> None:
        card = self.card(
            state="closed",
            state_reason="completed",
            closed_at="2026-10-01T00:00:00Z",
            labels=["status:in-progress"],
        )
        self.assertEqual(card["column"], "done")

    def test_closed_completed_outside_14_days_is_hidden(self) -> None:
        old = (NOW - timedelta(days=14, seconds=1)).isoformat()
        self.assertIsNone(
            kb.classify(
                issue(
                    state="closed",
                    state_reason="completed",
                    closed_at=old,
                ),
                now=NOW,
            )
        )
        edge = (NOW - timedelta(days=14)).isoformat()
        kept = kb.classify(
            issue(state="closed", state_reason="completed", closed_at=edge),
            now=NOW,
        )
        self.assertEqual(kept["column"], "done")

    def test_closed_not_planned_is_hidden(self) -> None:
        self.assertIsNone(
            kb.classify(
                issue(
                    state="closed",
                    state_reason="not_planned",
                    closed_at="2026-10-05T00:00:00Z",
                    labels=["status:done", "status:ready"],
                ),
                now=NOW,
            )
        )

    def test_board_hides_not_planned_and_keeps_the_rest(self) -> None:
        board = kb.build_board(
            [
                issue(number=1, labels=["status:ready"]),
                issue(
                    number=2,
                    state="closed",
                    state_reason="not_planned",
                    closed_at="2026-10-05T00:00:00Z",
                ),
                issue(
                    number=3,
                    state="closed",
                    state_reason="completed",
                    closed_at="2026-10-04T00:00:00Z",
                ),
            ],
            now=NOW,
            write_enabled=False,
        )
        self.assertEqual([card["number"] for card in board["cards"]], [3, 1])
        self.assertFalse(board["writeEnabled"])
        self.assertFalse(board["columns"][-1]["droppable"])


class WorkerParse(unittest.TestCase):
    def test_worker_line_present(self) -> None:
        card = kb.classify(
            issue(
                body="**Worker:** Buzz/GrokBuild. Later sentences stay off the card.",
                labels=["owner:SomeoneElse"],
            ),
            now=NOW,
        )
        self.assertEqual(card["worker"], "Buzz/GrokBuild")

    def test_missing_worker_uses_owner_label(self) -> None:
        card = kb.classify(issue(body="No worker here", labels=["owner:Forge"]), now=NOW)
        self.assertEqual(card["worker"], "Forge")

    def test_malformed_worker_without_owner_is_empty(self) -> None:
        for body in ("", "**Worker:**", "**Worker:** ***", None):
            card = kb.classify(issue(body=body, labels=["bug"]), now=NOW)
            self.assertIsNone(card["worker"])

    def test_malformed_worker_falls_back_to_owner(self) -> None:
        card = kb.classify(issue(body="**Worker:**", labels=["owner:Forge"]), now=NOW)
        self.assertEqual(card["worker"], "Forge")


class LinkedPulls(unittest.TestCase):
    def test_fixture_extracts_number_and_state(self) -> None:
        node = {
            "number": 1074,
            "title": "quota day",
            "body": "**Worker:** Forge",
            "state": "OPEN",
            "stateReason": None,
            "closedAt": None,
            "url": "https://github.com/cvolkernick/personal-workspace/issues/1074",
            "labels": {"nodes": [{"name": "status:pending-review"}, {"name": "bug"}]},
            "closedByPullRequestsReferences": {
                "nodes": [
                    {
                        "number": 1047,
                        "state": "MERGED",
                        "url": "https://github.com/cvolkernick/personal-workspace/pull/1047",
                        "isDraft": False,
                    }
                ]
            },
            "timelineItems": {
                "nodes": [
                    {
                        "source": {
                            "__typename": "PullRequest",
                            "number": 1083,
                            "state": "OPEN",
                            "url": "https://github.com/cvolkernick/personal-workspace/pull/1083",
                            "isDraft": False,
                        }
                    },
                    {
                        "source": {
                            "__typename": "PullRequest",
                            "number": 1084,
                            "state": "OPEN",
                            "url": "https://github.com/cvolkernick/personal-workspace/pull/1084",
                            "isDraft": True,
                        }
                    },
                    {"source": {"__typename": "Issue", "number": 990}},
                    {
                        "source": {
                            "__typename": "PullRequest",
                            "number": 1083,
                            "state": "OPEN",
                            "url": "https://github.com/cvolkernick/personal-workspace/pull/1083",
                            "isDraft": False,
                        }
                    },
                ]
            },
        }
        parsed = kb.parse_issue_node(node)
        self.assertEqual(
            parsed["pull_requests"],
            [
                {
                    "number": 1047,
                    "state": "merged",
                    "url": "https://github.com/cvolkernick/personal-workspace/pull/1047",
                },
                {
                    "number": 1083,
                    "state": "open",
                    "url": "https://github.com/cvolkernick/personal-workspace/pull/1083",
                },
                {
                    "number": 1084,
                    "state": "draft",
                    "url": "https://github.com/cvolkernick/personal-workspace/pull/1084",
                },
            ],
        )
        card = kb.classify(parsed, now=NOW)
        self.assertEqual(card["pullRequests"][1]["state"], "open")


class Pagination(unittest.TestCase):
    def test_250_open_issues_all_become_cards(self) -> None:
        pages = {}
        cursor = None
        remaining = 250
        number = 1
        while remaining:
            size = min(100, remaining)
            nodes = [
                {
                    "number": number + offset,
                    "title": f"issue {number + offset}",
                    "body": "",
                    "state": "OPEN",
                    "stateReason": None,
                    "closedAt": None,
                    "url": f"https://github.com/cvolkernick/personal-workspace/issues/{number + offset}",
                    "labels": {"nodes": [{"name": "bug"}]},
                }
                for offset in range(size)
            ]
            remaining -= size
            number += size
            end = f"cursor-{number}" if remaining else None
            pages[cursor] = {
                "nodes": nodes,
                "has_next": remaining > 0,
                "end_cursor": end,
            }
            cursor = end

        seen = []

        def fetch(current):
            seen.append(current)
            return pages[current]

        nodes = kb.collect_pages(fetch)
        self.assertEqual(len(nodes), 250)
        self.assertGreater(len(seen), 1)
        board = kb.build_board(
            [kb.parse_issue_node(node) for node in nodes],
            now=NOW,
            write_enabled=False,
        )
        self.assertEqual(len(board["cards"]), 250)
        self.assertEqual({card["column"] for card in board["cards"]}, {"backlog"})

    def test_repeated_cursor_stops(self) -> None:
        def fetch(current):
            return {"nodes": [{"number": 1}], "has_next": True, "end_cursor": "same"}

        self.assertEqual(len(kb.collect_pages(fetch)), 2)


class WriteFlag(unittest.TestCase):
    def test_unset_write_flag_makes_no_calls_and_handles_stay_off(self) -> None:
        self.assertFalse(kb.write_enabled({}))
        self.assertFalse(kb.write_enabled({"KANBAN_WRITE": "0"}))
        client = FakeGitHub([issue(number=8, labels=["status:ready", "bug"])])
        result = kb.move_issue(8, "in_progress", client, write_enabled=kb.write_enabled({}))
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])
        board = kb.build_board(
            [issue(number=8, labels=["status:ready"])],
            now=NOW,
            write_enabled=False,
        )
        self.assertFalse(board["writeEnabled"])
        self.assertTrue(all(column["id"] != "done" or not column["droppable"] for column in board["columns"]))

    def test_ready_to_in_progress_is_one_label_update(self) -> None:
        client = FakeGitHub(
            [issue(number=8, labels=["bug", "status:ready", "owner:Forge", "spec"])]
        )
        result = kb.move_request(
            {
                "number": 8,
                "column": "in_progress",
                "priority": "P0",
                "sprint": "2026-W41",
                "rank": 1,
                "assignee": "nobody",
            },
            client,
            write_enabled=True,
        )
        self.assertTrue(result["ok"])
        mutations = [call for call in client.calls if call[0] == "set_labels"]
        self.assertEqual(len(mutations), 1)
        self.assertEqual(
            mutations[0][2],
            ["bug", "owner:Forge", "spec", "status:in-progress"],
        )
        self.assertNotIn("priority", json.dumps({"labels": mutations[0][2]}))

    def test_api_failure_reports_error_and_keeps_labels(self) -> None:
        client = FakeGitHub(
            [issue(number=8, labels=["status:ready", "bug"])],
            fail_set=True,
        )
        result = kb.move_issue(8, "in_progress", client, write_enabled=True)
        self.assertFalse(result["ok"])
        self.assertIn("GitHub 500", result["error"])
        self.assertEqual(client.issues[8]["labels"], ["status:ready", "bug"])
        self.assertNotIn("ghp_", result["error"])

    def test_done_drop_is_rejected_without_a_call(self) -> None:
        client = FakeGitHub([issue(number=8, labels=["status:ready"])])
        result = kb.move_issue(8, "done", client, write_enabled=True)
        self.assertFalse(result["ok"])
        self.assertEqual(client.calls, [])

    def test_backlog_drop_removes_status_labels_only(self) -> None:
        client = FakeGitHub(
            [issue(number=8, labels=["status:ready", "status:parked", "parked", "bug"])]
        )
        result = kb.move_issue(8, "backlog", client, write_enabled=True)
        self.assertEqual(result["labels"], ["parked", "bug"])

    def test_label_patch_body_has_no_rank_fields(self) -> None:
        captured = {}

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"{}"

        def opener(req, timeout=30):
            captured["url"] = req.full_url
            captured["method"] = req.method
            captured["body"] = json.loads(req.data.decode())
            captured["host"] = req.host
            return Resp()

        client = kb.GitHubClient("ghp_SUPERSECRET", opener=opener)
        client.set_labels(8, ["bug", "status:in-progress"])
        self.assertEqual(captured["host"], "api.github.com")
        self.assertEqual(captured["method"], "PATCH")
        self.assertEqual(
            captured["url"],
            "https://api.github.com/repos/cvolkernick/personal-workspace/issues/8",
        )
        self.assertEqual(set(captured["body"]), {"labels"})
        for key in kb.FORBIDDEN_WRITE_KEYS:
            self.assertNotIn(key, captured["body"])
        self.assertNotIn("ghp_SUPERSECRET", json.dumps(captured["body"]))

    def test_payload_does_not_contain_the_token(self) -> None:
        class Listing:
            def list_issue_nodes(self):
                return [
                    {
                        "number": 9,
                        "title": "token must stay server side",
                        "body": "GITHUB_TOKEN=ghp_SUPERSECRET",
                        "state": "OPEN",
                        "labels": {"nodes": [{"name": "bug"}]},
                    }
                ]

        board = kb.load_board(
            Listing(),
            now=NOW,
            env={"KANBAN_WRITE": "0", "GITHUB_TOKEN": "ghp_SUPERSECRET"},
        )
        blob = json.dumps(board)
        self.assertNotIn("ghp_SUPERSECRET", blob)
        self.assertNotIn("GITHUB_TOKEN", blob)
        self.assertEqual(board["cards"][0]["worker"], None)


class TokenAndHost(unittest.TestCase):
    def test_ui_source_has_no_token_or_github_host(self) -> None:
        self.assertTrue(UI_ROOT.is_dir(), "eng-kanban UI is part of this change")
        forbidden = ("GITHUB_TOKEN", "GH_TOKEN", "BUZZ_BOARD_GITHUB_TOKEN", "api.github.com", "ghp_")
        scanned = 0
        for path in UI_ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {"node_modules", "dist"} for part in path.parts):
                continue
            if path.suffix not in {".ts", ".tsx", ".js", ".css", ".html", ".json"}:
                continue
            if path.name == "package-lock.json":
                continue
            text = path.read_text(encoding="utf-8")
            scanned += 1
            for needle in forbidden:
                self.assertNotIn(needle, text, f"{path} contains {needle}")
        self.assertGreater(scanned, 0)

    def test_explicit_empty_env_does_not_invent_a_token(self) -> None:
        with self.assertRaises(kb.KanbanAuthError):
            kb.resolve_token({})

    def test_query_stays_on_the_issues_connection(self) -> None:
        self.assertIn("pageInfo", kb.ISSUES_QUERY)
        self.assertIn("hasNextPage", kb.ISSUES_QUERY)
        self.assertNotIn("api.github.com", kb.ISSUES_QUERY)


if __name__ == "__main__":
    unittest.main()
