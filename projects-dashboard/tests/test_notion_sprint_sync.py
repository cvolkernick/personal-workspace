"""Worker parse, status map, and Notion field preservation for #1016."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

import notion_sprint_sync as sync  # noqa: E402

ET = ZoneInfo("America/New_York")

SCHEMA = {
    "Name": {"type": "title"},
    "Issue #": {"type": "number"},
    "GitHub Issue URL": {"type": "url"},
    "Owner": {
        "type": "select",
        "select": {"options": [{"name": "Buzz/GrokBuild"}, {"name": "Forge"}]},
    },
    "Status": {
        "type": "status",
        "status": {
            "options": [
                {"name": "Backlog"},
                {"name": "Sprint"},
                {"name": "In Progress"},
                {"name": "In Review"},
                {"name": "Done"},
                {"name": "Blocked"},
            ]
        },
    },
    "Sprint": {"type": "select", "select": {"options": [{"name": "2026-W41"}]}},
    "Priority": {"type": "select", "select": {"options": [{"name": "P1"}]}},
    "Rank": {"type": "number"},
    "Chris gate": {"type": "checkbox"},
    "Notes": {"type": "rich_text"},
}


def issue(**overrides) -> sync.GhIssue:
    base = dict(
        number=1016,
        title="Auto-sync GitHub issues",
        body="**Worker:** Buzz/GrokBuild\n",
        state="open",
        state_reason=None,
        labels={"spec", "status:ready"},
        url="https://github.com/cvolkernick/personal-workspace/issues/1016",
        open_pr=False,
        buzz_assignee=False,
    )
    base.update(overrides)
    return sync.GhIssue(**base)


def row(page_id: str, properties: dict) -> sync.BoardRow:
    return sync.BoardRow(page_id, properties)


def stored_status(name: str) -> dict:
    return {"type": "status", "status": {"name": name}}


def stored_title(text: str) -> dict:
    return {"type": "title", "title": [{"plain_text": text}]}


class ParseWorkerTest(unittest.TestCase):
    def test_bold_worker_line(self) -> None:
        self.assertEqual(sync.parse_worker("**Worker:** Buzz/GrokBuild\n"), "Buzz/GrokBuild")

    def test_trailing_sentence_is_not_the_owner(self) -> None:
        body = "**Worker:** Buzz/GrokBuild. Pi dispatcher/ops steps may hand off to Forge."
        self.assertEqual(sync.parse_worker(body), "Buzz/GrokBuild")

    def test_trailing_period_only(self) -> None:
        self.assertEqual(sync.parse_worker("**Worker:** Forge."), "Forge")

    def test_missing_or_blank(self) -> None:
        self.assertIsNone(sync.parse_worker(""))
        self.assertIsNone(sync.parse_worker("No worker here"))
        self.assertIsNone(sync.parse_worker("**Worker:**"))
        self.assertIsNone(sync.parse_worker(None))


class StatusMapTest(unittest.TestCase):
    def test_precedence(self) -> None:
        completed = issue(
            state="closed",
            state_reason="completed",
            labels={"blocked", "status:in-progress"},
            open_pr=True,
            buzz_assignee=True,
        )
        self.assertEqual(sync.desired_status(completed), "Done")
        blocked = issue(labels={"blocked"}, open_pr=True, buzz_assignee=True)
        self.assertEqual(sync.desired_status(blocked), "Blocked")
        review = issue(open_pr=True, labels={"status:in-progress"}, buzz_assignee=True)
        self.assertEqual(sync.desired_status(review), "In Review")
        progress = issue(labels={"status:in-progress"})
        self.assertEqual(sync.desired_status(progress), "In Progress")
        assigned = issue(labels=set(), buzz_assignee=True)
        self.assertEqual(sync.desired_status(assigned), "In Progress")

    def test_closed_not_planned_is_not_done(self) -> None:
        item = issue(state="closed", state_reason="not_planned", labels={"spec"})
        self.assertIsNone(sync.desired_status(item))

    def test_sprint_and_backlog_only_leave_for_mapped_statuses(self) -> None:
        self.assertEqual(sync.next_status("Sprint", None, creating=False), None)
        self.assertEqual(sync.next_status("Backlog", None, creating=False), None)
        self.assertEqual(sync.next_status("Sprint", "In Review", creating=False), "In Review")
        self.assertEqual(sync.next_status("Backlog", "Done", creating=False), "Done")
        self.assertEqual(sync.next_status("Backlog", "Blocked", creating=False), "Blocked")
        self.assertEqual(sync.next_status("In Review", "Done", creating=False), "Done")
        self.assertIsNone(sync.next_status("In Progress", "In Progress", creating=False))

    def test_create_uses_backlog_unless_a_mapped_status_applies(self) -> None:
        self.assertEqual(sync.next_status(None, None, creating=True), "Backlog")
        self.assertEqual(sync.next_status(None, "In Progress", creating=True), "In Progress")

    def test_empty_existing_status_fills_backlog(self) -> None:
        self.assertEqual(sync.next_status(None, None, creating=False), "Backlog")
        self.assertEqual(sync.next_status("", "Done", creating=False), "Done")


class FieldPreservationTest(unittest.TestCase):
    def _human_row(self) -> sync.BoardRow:
        return row(
            "page-1",
            {
                "Name": stored_title("old name"),
                "Issue #": {"type": "number", "number": 1016},
                "GitHub Issue URL": {"type": "url", "url": "https://github.com/cvolkernick/personal-workspace/issues/1016"},
                "Owner": {"type": "select", "select": {"name": "Forge"}},
                "Status": stored_status("Sprint"),
                "Sprint": {"type": "select", "select": {"name": "2026-W41"}},
                "Priority": {"type": "select", "select": {"name": "P1"}},
                "Rank": {"type": "number", "number": 3},
                "Chris gate": {"type": "checkbox", "checkbox": True},
                "Notes": {"type": "rich_text", "rich_text": [{"plain_text": "keep me"}]},
            },
        )

    def test_manual_sprint_fields_survive_and_name_owner_refresh(self) -> None:
        plan = sync.plan_sync([issue()], [self._human_row()], SCHEMA)
        self.assertEqual(len(plan.actions), 1)
        action = plan.actions[0]
        self.assertEqual(action.op, "update")
        keys = set(action.properties)
        self.assertTrue(keys.isdisjoint({"Sprint", "Priority", "Rank", "Chris gate", "Notes"}))
        self.assertEqual(
            action.properties["Name"]["title"][0]["text"]["content"],
            "Auto-sync GitHub issues",
        )
        self.assertEqual(action.properties["Owner"]["select"]["name"], "Buzz/GrokBuild")
        self.assertNotIn("Status", action.properties)
        self.assertNotIn("Issue #", action.properties)
        self.assertNotIn("GitHub Issue URL", action.properties)

    def test_unknown_owner_option_does_not_overwrite(self) -> None:
        item = issue(body="**Worker:** Somebody Else\n")
        plan = sync.plan_sync([item], [self._human_row()], SCHEMA)
        self.assertNotIn("Owner", plan.actions[0].properties)
        self.assertTrue(any("owner" in warning for warning in plan.warnings))

    def test_empty_url_and_issue_number_are_filled(self) -> None:
        existing = row(
            "page-2",
            {
                "Name": stored_title("#953"),
                "Status": stored_status("Backlog"),
            },
        )
        plan = sync.plan_sync([issue(number=953, title="Named later", labels={"spec"})], [existing], SCHEMA)
        props = plan.actions[0].properties
        self.assertEqual(props["Issue #"]["number"], 953)
        self.assertEqual(
            props["GitHub Issue URL"]["url"],
            "https://github.com/cvolkernick/personal-workspace/issues/1016",
        )
        self.assertNotIn("Sprint", props)

    def test_closing_moves_backlog_to_done(self) -> None:
        existing = row(
            "page-3",
            {
                "Name": stored_title("Auto-sync GitHub issues"),
                "Issue #": {"type": "number", "number": 1016},
                "Status": stored_status("Backlog"),
                "Sprint": {"type": "select", "select": {"name": "2026-W41"}},
                "Rank": {"type": "number", "number": 1},
            },
        )
        closed = issue(state="closed", state_reason="completed", labels=set())
        plan = sync.plan_sync([closed], [existing], SCHEMA)
        self.assertEqual(plan.actions[0].properties["Status"]["status"]["name"], "Done")
        self.assertNotIn("Sprint", plan.actions[0].properties)
        self.assertNotIn("Rank", plan.actions[0].properties)

    def test_create_ready_issue_and_rerun_is_idempotent(self) -> None:
        item = issue()
        first = sync.plan_sync([item], [], SCHEMA)
        self.assertEqual([action.op for action in first.actions], ["create"])
        self.assertEqual(first.actions[0].properties["Status"]["status"]["name"], "Backlog")
        self.assertEqual(first.actions[0].properties["Owner"]["select"]["name"], "Buzz/GrokBuild")
        stored = sync.materialize_properties(SCHEMA, first.actions[0].properties)
        second = sync.plan_sync([item], [row("page-new", stored)], SCHEMA)
        self.assertEqual(second.actions, [])
        self.assertEqual(second.unchanged, 1)

    def test_in_progress_create_and_no_duplicate_for_existing_number(self) -> None:
        item = issue(labels={"spec", "status:in-progress"})
        created = sync.plan_sync([item], [], SCHEMA)
        self.assertEqual(created.actions[0].properties["Status"]["status"]["name"], "In Progress")
        stored = sync.materialize_properties(SCHEMA, created.actions[0].properties)
        again = sync.plan_sync([item], [row("page-a", stored), row("page-b", stored)], SCHEMA)
        self.assertTrue(all(action.op == "update" or not action.properties for action in again.actions))
        self.assertFalse(any(action.op == "create" for action in again.actions))
        self.assertTrue(any("2 board rows" in warning for warning in again.warnings))

    def test_closed_spec_issue_is_not_backfilled(self) -> None:
        closed = issue(state="closed", state_reason="completed")
        plan = sync.plan_sync([closed], [], SCHEMA)
        self.assertEqual(plan.actions, [])

    def test_url_match_does_not_create_a_second_row(self) -> None:
        existing = row(
            "page-url",
            {
                "Name": stored_title("untitled"),
                "GitHub Issue URL": {
                    "type": "url",
                    "url": "https://github.com/cvolkernick/personal-workspace/issues/1016",
                },
                "Status": stored_status("Backlog"),
            },
        )
        plan = sync.plan_sync([issue()], [existing], SCHEMA)
        self.assertFalse(any(action.op == "create" for action in plan.actions))
        self.assertIn("Issue #", plan.actions[0].properties)


class FccWindowTest(unittest.TestCase):
    def test_hours_ending_1_or_6_at_minute_18(self) -> None:
        for hour in (1, 6, 11, 16, 21):
            moment = datetime(2026, 10, 3, hour, 18, tzinfo=ET)
            self.assertTrue(sync.in_fcc_restart_window(moment), hour)
        self.assertFalse(sync.in_fcc_restart_window(datetime(2026, 10, 3, 16, 15, tzinfo=ET)))
        self.assertFalse(sync.in_fcc_restart_window(datetime(2026, 10, 3, 2, 18, tzinfo=ET)))
        # 20:18 UTC is 16:18 EDT on this date.
        self.assertTrue(
            sync.in_fcc_restart_window(datetime(2026, 10, 3, 20, 18, tzinfo=timezone.utc))
        )


class RunSyncTest(unittest.TestCase):
    def test_missing_token_does_not_call_notion(self) -> None:
        called = {"notion": 0}

        class Notion:
            def schema(self):
                called["notion"] += 1
                return SCHEMA

        code, summary = sync.run_sync(env={}, notion=Notion(), now=datetime(2026, 10, 3, 12, 0, tzinfo=ET))
        self.assertEqual(code, 0)
        self.assertEqual(summary["skipped"], "not_configured")
        self.assertEqual(called["notion"], 0)

    def test_fcc_window_skips_before_notion(self) -> None:
        called = {"notion": 0}

        class Notion:
            def schema(self):
                called["notion"] += 1
                return SCHEMA

        code, summary = sync.run_sync(
            env={"NOTION_SPRINT_TOKEN": "secret"},
            notion=Notion(),
            now=datetime(2026, 10, 3, 16, 18, tzinfo=ET),
        )
        self.assertEqual(code, 0)
        self.assertEqual(summary["skipped"], "fcc_restart_window")
        self.assertEqual(called["notion"], 0)

    def test_auth_error_notifies_and_does_not_write(self) -> None:
        notes: list[str] = []

        class Notion:
            def schema(self):
                raise sync.AuthError("notion", 401, "unauthorized")

            def create(self, properties):
                raise AssertionError("create")

        code, summary = sync.run_sync(
            env={"NOTION_SPRINT_TOKEN": "secret"},
            notion=Notion(),
            now=datetime(2026, 10, 3, 12, 0, tzinfo=ET),
            notify=notes.append,
        )
        self.assertEqual(code, 2)
        self.assertEqual(summary["error"], "auth")
        self.assertEqual(summary["source"], "notion")
        self.assertEqual(notes, ["NOTION_SPRINT_AUTH_ERROR source=notion status=401"])

    def test_dry_run_plans_a_create_without_writing(self) -> None:
        writes: list[str] = []

        class Notion:
            def schema(self):
                return SCHEMA

            def query_rows(self):
                return []

            def create(self, properties):
                writes.append("create")
                return "page"

            def update(self, page_id, properties):
                writes.append("update")

        class Github:
            def fetch(self, numbers):
                return [issue()]

        code, summary = sync.run_sync(
            env={"NOTION_SPRINT_TOKEN": "secret"},
            notion=Notion(),
            github=Github(),
            now=datetime(2026, 10, 3, 12, 0, tzinfo=ET),
            dry_run=True,
        )
        self.assertEqual(code, 0)
        self.assertEqual(summary["created"], 1)
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
