#!/usr/bin/env python3
"""Item 101 is past the first=100 window in scripts/buzz-board.

The fixture is 101 project nodes. The match sits at position 101.
On 920553f2, get reports on_board false and set-status/add call
addProjectV2ItemById. After the lookup change, get returns that item id,
set-status updates it, and add reports already on board.

No network. gql/rest/urlopen are patched.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
LIVE_PATH = SCRIPTS / "buzz-board"
TARGET_NUMBER = 4242
TARGET_ID = "PVTI_ITEM_101"
DUPLICATE_ID = "PVTI_DUPLICATE"
REPO_NAME = "cvolkernick/personal-workspace"


def _load(path: Path, name: str):
    # scripts/buzz-board has no .py suffix, so spec_from_file_location has no loader.
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


LIVE = _load(LIVE_PATH, "buzz_board_window_live")


def _item_101_nodes() -> list[dict]:
    """101 nodes. Positions 1–100 are other issues. Position 101 is the match."""
    nodes = []
    for n in range(1, 101):
        nodes.append(_issue_node(n, f"PVTI_{n:03d}", "Ready"))
    nodes.append(_issue_node(TARGET_NUMBER, TARGET_ID, "Parked"))
    assert len(nodes) == 101
    assert nodes[100]["id"] == TARGET_ID
    return nodes


def _issue_node(number: int, item_id: str, status: str) -> dict:
    return {
        "id": item_id,
        "fieldValueByName": {"name": status},
        "content": {
            "__typename": "Issue",
            "number": number,
            "title": f"issue {number}",
            "url": f"https://github.com/{REPO_NAME}/issues/{number}",
            "state": "OPEN",
            "body": "",
            "assignees": {"nodes": []},
            "labels": {"nodes": []},
            "repository": {"nameWithOwner": REPO_NAME},
        },
    }


def _issue_payload(number: int) -> dict:
    return {
        "number": number,
        "title": f"issue {number}",
        "state": "open",
        "html_url": f"https://github.com/{REPO_NAME}/issues/{number}",
        "body": "fixture",
        "labels": [],
        "node_id": f"ISSUE_NODE_{number}",
    }


class _Calls:
    def __init__(self) -> None:
        self.added: list[dict] = []
        self.updated: list[dict] = []
        self.queries: list[dict] = []
        self.rest: list[tuple] = []


@contextmanager
def board_fixture(mod, nodes: list[dict] | None = None):
    nodes = _item_101_nodes() if nodes is None else nodes
    calls = _Calls()

    def fake_gql(query: str, variables: dict | None = None):
        variables = variables or {}
        if len(calls.queries) + len(calls.added) + len(calls.updated) > 30:
            raise AssertionError("pagination did not stop")
        if "addProjectV2ItemById" in query:
            calls.added.append(variables)
            return {"addProjectV2ItemById": {"item": {"id": DUPLICATE_ID}}}
        if "updateProjectV2ItemFieldValue" in query:
            calls.updated.append(variables)
            return {
                "updateProjectV2ItemFieldValue": {
                    "projectV2Item": {"id": variables["itemId"]}
                }
            }
        first = int(variables["first"])
        after = variables.get("after")
        start = 0 if not after else int(after)
        chunk = nodes[start : start + first]
        end = start + len(chunk)
        has_next = end < len(nodes)
        calls.queries.append({"first": first, "after": after, "end": end})
        return {
            "user": {
                "projectV2": {
                    "title": "Buzz Board",
                    "items": {
                        "pageInfo": {
                            "hasNextPage": has_next,
                            "endCursor": str(end) if has_next else None,
                        },
                        "nodes": chunk,
                    },
                }
            }
        }

    def fake_rest(method: str, path: str, body: dict | None = None):
        calls.rest.append((method, path, body))
        number = int(path.rstrip("/").rsplit("/", 1)[-1])
        return _issue_payload(number)

    with mock.patch.object(mod, "gql", side_effect=fake_gql), mock.patch.object(
        mod, "rest", side_effect=fake_rest
    ), mock.patch.object(
        mod.urllib.request, "urlopen", side_effect=AssertionError("network")
    ), mock.patch.dict(
        os.environ, {"GITHUB_TOKEN": "fixture", "GH_TOKEN": "fixture"}
    ):
        yield calls


def _run(mod, fn, ns) -> str:
    buf = StringIO()
    with redirect_stdout(buf):
        fn(ns)
    return buf.getvalue()


def _blob_920553f2():
    try:
        blob = subprocess.check_output(
            ["git", "-C", str(REPO), "show", "920553f2:scripts/buzz-board"],
            stderr=subprocess.PIPE,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    directory = tempfile.mkdtemp(prefix="buzz-board-920553f2-")
    try:
        path = Path(directory) / "buzz-board"
        path.write_bytes(blob)
        return _load(path, "buzz_board_window_920553f2")
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class TestBuzzBoardItem101(unittest.TestCase):
    def test_get_set_status_add_see_item_101(self) -> None:
        with board_fixture(LIVE) as calls:
            got = json.loads(
                _run(LIVE, LIVE.cmd_get, Namespace(number=TARGET_NUMBER, json=True))
            )
            self.assertTrue(got["on_board"])
            self.assertEqual(got["board_item_id"], TARGET_ID)
            self.assertEqual(got["board_status"], "Parked")
            self.assertGreaterEqual(len(calls.queries), 3)

        with board_fixture(LIVE) as calls:
            raw = _run(
                LIVE,
                LIVE.cmd_set_status,
                Namespace(number=TARGET_NUMBER, status="In Progress", json=True),
            )
            body = json.loads(raw)
            self.assertEqual(body["item_id"], TARGET_ID)
            self.assertEqual(body["board_status"], "In Progress")
            self.assertEqual(calls.added, [])
            self.assertEqual(calls.rest, [])
            self.assertEqual(len(calls.updated), 1)
            self.assertEqual(calls.updated[0]["itemId"], TARGET_ID)
            self.assertEqual(
                calls.updated[0]["optionId"], LIVE.STATUS_OPTIONS["In Progress"]
            )

        with board_fixture(LIVE) as calls:
            text = _run(
                LIVE,
                LIVE.cmd_add,
                Namespace(number=TARGET_NUMBER, status=None, json=False),
            )
            self.assertIn("already on board", text)
            self.assertEqual(calls.added, [])

        with board_fixture(LIVE) as calls:
            body = json.loads(
                _run(
                    LIVE,
                    LIVE.cmd_add,
                    Namespace(number=TARGET_NUMBER, status=None, json=True),
                )
            )
            self.assertFalse(body["added"])
            self.assertEqual(body["item_id"], TARGET_ID)
            self.assertEqual(calls.added, [])

    def test_get_still_finds_item_100(self) -> None:
        with board_fixture(LIVE) as calls:
            got = json.loads(_run(LIVE, LIVE.cmd_get, Namespace(number=100, json=True)))
            self.assertTrue(got["on_board"])
            self.assertEqual(got["board_item_id"], "PVTI_100")
            self.assertLessEqual(len(calls.queries), 2)

    def test_capped_fetch_still_stops_at_100(self) -> None:
        with board_fixture(LIVE) as calls:
            items = LIVE.fetch_items(first=100)
        numbers = [item["number"] for item in items]
        self.assertEqual(len(items), 100)
        self.assertNotIn(TARGET_NUMBER, numbers)
        self.assertEqual(len(calls.queries), 2)

    def test_set_status_still_adds_when_issue_is_absent(self) -> None:
        with board_fixture(LIVE) as calls:
            body = json.loads(
                _run(
                    LIVE,
                    LIVE.cmd_set_status,
                    Namespace(number=9999, status="Ready", json=True),
                )
            )
        self.assertEqual(len(calls.added), 1)
        self.assertEqual(body["item_id"], DUPLICATE_ID)
        self.assertEqual(calls.updated[0]["itemId"], DUPLICATE_ID)
        self.assertEqual(calls.updated[0]["optionId"], LIVE.STATUS_OPTIONS["Ready"])

    def test_item_101_fixture_fails_on_920553f2(self) -> None:
        old = _blob_920553f2()
        if old is None:
            self.skipTest("920553f2:scripts/buzz-board is not in this clone")

        with board_fixture(old) as calls:
            got = json.loads(
                _run(old, old.cmd_get, Namespace(number=TARGET_NUMBER, json=True))
            )
            self.assertFalse(got["on_board"])
            self.assertIsNone(got["board_item_id"])
            self.assertEqual(len(calls.queries), 2)

        with board_fixture(old) as calls:
            body = json.loads(
                _run(
                    old,
                    old.cmd_set_status,
                    Namespace(number=TARGET_NUMBER, status="In Progress", json=True),
                )
            )
            self.assertEqual(len(calls.added), 1)
            self.assertEqual(body["item_id"], DUPLICATE_ID)
            self.assertNotEqual(body["item_id"], TARGET_ID)
            self.assertEqual(calls.updated[0]["itemId"], DUPLICATE_ID)

        with board_fixture(old) as calls:
            text = _run(
                old,
                old.cmd_add,
                Namespace(number=TARGET_NUMBER, status=None, json=False),
            )
            self.assertNotIn("already on board", text)
            self.assertIn("added", text)
            self.assertEqual(len(calls.added), 1)


def _parked_past_window_nodes() -> list[dict]:
    """120 Ready rows, then 130 open Parked rows, then one closed Parked row.

    Every Parked row sits past item 100. The open Parked count is larger
    than the default ``--limit`` of 100.
    """
    nodes = [_issue_node(n, f"PVTI_R_{n:03d}", "Ready") for n in range(1, 121)]
    nodes.extend(
        _issue_node(n, f"PVTI_P_{n}", "Parked") for n in range(900, 1030)
    )
    closed = _issue_node(7000, "PVTI_CLOSED", "Parked")
    closed["content"]["state"] = "CLOSED"
    nodes.append(closed)
    assert len(nodes) == 251
    assert all(node["fieldValueByName"]["name"] != "Parked" for node in nodes[:100])
    return nodes


class TestBuzzBoardListStatusWindow(unittest.TestCase):
    def test_list_status_parked_returns_the_row_past_item_100(self) -> None:
        with board_fixture(LIVE) as calls:
            listed = json.loads(
                _run(
                    LIVE,
                    LIVE.cmd_list,
                    Namespace(status="Parked", all=False, limit=100, json=True),
                )
            )
        self.assertEqual([item["number"] for item in listed], [TARGET_NUMBER])
        self.assertEqual(listed[0]["status"], "Parked")
        self.assertGreaterEqual(calls.queries[-1]["end"], 101)
        self.assertGreaterEqual(len(calls.queries), 3)

    def test_list_status_parked_matches_a_full_paginate(self) -> None:
        nodes = _parked_past_window_nodes()
        parked_open = list(range(900, 1030))
        with board_fixture(LIVE, nodes) as calls:
            at_default = json.loads(
                _run(
                    LIVE,
                    LIVE.cmd_list,
                    Namespace(status="Parked", all=False, limit=100, json=True),
                )
            )
        self.assertEqual([item["number"] for item in at_default], parked_open)
        self.assertGreater(calls.queries[-1]["end"], 100)
        self.assertEqual(calls.added, [])
        self.assertEqual(calls.updated, [])

        with board_fixture(LIVE, nodes) as calls:
            at_wide = json.loads(
                _run(
                    LIVE,
                    LIVE.cmd_list,
                    Namespace(status="Parked", all=False, limit=800, json=True),
                )
            )
        self.assertEqual(at_default, at_wide)

        with board_fixture(LIVE, nodes):
            including_closed = json.loads(
                _run(
                    LIVE,
                    LIVE.cmd_list,
                    Namespace(status="Parked", all=True, limit=100, json=True),
                )
            )
        self.assertEqual(
            [item["number"] for item in including_closed],
            parked_open + [7000],
        )

    def test_list_without_status_still_stops_at_limit(self) -> None:
        with board_fixture(LIVE) as calls:
            listed = json.loads(
                _run(
                    LIVE,
                    LIVE.cmd_list,
                    Namespace(status=None, all=True, limit=100, json=True),
                )
            )
        self.assertEqual(len(listed), 100)
        self.assertNotIn(TARGET_NUMBER, [item["number"] for item in listed])
        self.assertEqual(len(calls.queries), 2)
        self.assertEqual(calls.added, [])
        self.assertEqual(calls.updated, [])

    def test_fetch_all_pages_when_first_is_none(self) -> None:
        with board_fixture(LIVE) as calls:
            items = LIVE.fetch_items(first=None)
        self.assertEqual(len(items), 101)
        self.assertEqual(items[-1]["number"], TARGET_NUMBER)
        self.assertGreaterEqual(len(calls.queries), 3)


if __name__ == "__main__":
    unittest.main()
