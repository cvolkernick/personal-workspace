"""Tests for shipped time-allocator domain, store, and CLI entry."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.domain import (  # noqa: E402
    STARTER_ITEMS,
    add_item,
    allocate_total,
    empty_state,
    get_item,
    list_items,
    remove_item,
    seed_starter,
    set_minutes,
    set_priority,
)
from holistic.time_allocator.store import load_state, save_state  # noqa: E402
from holistic.time_allocator.cli import main as cli_main  # noqa: E402


class DomainTests(unittest.TestCase):
    def test_add_remove_list(self) -> None:
        state = empty_state()
        state = add_item(state, "Alpha", kind="task", priority=3, minutes=30)
        state = add_item(state, "Beta goal", kind="goal", priority=5, minutes=10)
        items = list_items(state)
        self.assertEqual(len(items), 2)
        # Higher priority first
        self.assertEqual(items[0]["title"], "Beta goal")
        self.assertEqual(items[1]["title"], "Alpha")
        state = remove_item(state, "Alpha")
        titles = [it["title"] for it in list_items(state)]
        self.assertEqual(titles, ["Beta goal"])
        with self.assertRaises(KeyError):
            remove_item(state, "missing")

    def test_allocate_weighted_sums_to_total(self) -> None:
        state = empty_state()
        state = add_item(state, "A", priority=1, item_id="a")
        state = add_item(state, "B", priority=3, item_id="b")
        state = add_item(state, "C", priority=0, item_id="c")  # zero weight
        state = allocate_total(state, 100)
        items = {it["id"]: it for it in state["items"]}
        total = sum(int(it["minutes"]) for it in items.values())
        self.assertEqual(total, 100)
        # B has 3x weight of A; C has 0 → 0 minutes
        self.assertEqual(items["c"]["minutes"], 0)
        self.assertGreater(items["b"]["minutes"], items["a"]["minutes"])
        # Exact: weights 1+3+0=4 → A=25, B=75, C=0 (or remainder on B)
        self.assertEqual(items["a"]["minutes"] + items["b"]["minutes"], 100)

    def test_seed_starter(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        self.assertEqual(len(list_items(state)), 0)
        self.assertEqual(len(state["targets"]), 4)
        state_g = seed_starter(empty_state(), personal=False)
        self.assertEqual(len(list_items(state_g)), len(STARTER_ITEMS))
        self.assertIsNotNone(get_item(state_g, "seed-deep-work"))

    def test_set_priority_and_minutes(self) -> None:
        state = add_item(empty_state(), "X", item_id="x", priority=1, minutes=0)
        state = set_priority(state, "x", 9)
        state = set_minutes(state, "X", 45)
        it = get_item(state, "x")
        assert it is not None
        self.assertEqual(it["priority"], 9)
        self.assertEqual(it["minutes"], 45)

    def test_add_item_with_backlog_link(self) -> None:
        from holistic.time_allocator.domain import get_item_by_backlog_id

        state = add_item(
            empty_state(),
            "From backlog",
            priority=7,
            minutes=45,
            notes="MVP slice",
            source="workflow-backlog",
            backlog_id="abc-123",
        )
        it = get_item_by_backlog_id(state, "abc-123")
        assert it is not None
        self.assertEqual(it["title"], "From backlog")
        self.assertEqual(it["source"], "workflow-backlog")
        with self.assertRaises(ValueError):
            add_item(
                state,
                "Dup link",
                backlog_id="abc-123",
            )


class StoreTests(unittest.TestCase):
    def test_roundtrip_persistence(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tasks.json"
            state = add_item(empty_state(), "Persist me", priority=4, minutes=15, item_id="p1")
            save_state(state, path)
            loaded = load_state(path)
            self.assertEqual(len(loaded["items"]), 1)
            self.assertEqual(loaded["items"][0]["title"], "Persist me")
            self.assertEqual(loaded["items"][0]["priority"], 4)
            # Second write after mutate
            loaded = remove_item(loaded, "p1")
            loaded = add_item(loaded, "Second", item_id="p2", priority=2)
            save_state(loaded, path)
            again = load_state(path)
            titles = [it["title"] for it in again["items"]]
            self.assertEqual(titles, ["Second"])


class CliTests(unittest.TestCase):
    def test_cli_main_seed_add_remove_allocate_list(self) -> None:
        import tempfile
        from io import StringIO
        from contextlib import redirect_stdout, redirect_stderr

        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "cli_tasks.json"
            # seed personal targets
            buf = StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cli_main(["--data", str(data), "seed"])
            self.assertEqual(rc, 0)
            self.assertTrue(data.is_file())
            state = load_state(data)
            self.assertGreaterEqual(len(state["targets"]), 4)
            self.assertEqual(len(state["items"]), 0)

            # add ad-hoc
            buf = StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cli_main(
                    [
                        "--data",
                        str(data),
                        "add",
                        "Ship MVP",
                        "--kind",
                        "task",
                        "--priority",
                        "6",
                        "--id",
                        "ship-mvp",
                        "--minutes",
                        "60",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertIsNotNone(get_item(load_state(data), "ship-mvp"))

            # second item then remove it
            buf = StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cli_main(
                    ["--data", str(data), "add", "Temp", "--id", "temp-x", "--minutes", "15"]
                )
            self.assertEqual(rc, 0)
            buf = StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cli_main(["--data", str(data), "remove", "temp-x"])
            self.assertEqual(rc, 0)
            self.assertIsNone(get_item(load_state(data), "temp-x"))

            # allocate across ad-hoc
            buf = StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cli_main(["--data", str(data), "allocate", "480"])
            self.assertEqual(rc, 0)
            state = load_state(data)
            total = sum(int(it["minutes"]) for it in state["items"])
            self.assertEqual(total, 480)

            # list --json reflects durable state
            buf = StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = cli_main(["--data", str(data), "list", "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            ids = {it["id"] for it in payload["items"]}
            self.assertIn("ship-mvp", ids)
            self.assertNotIn("temp-x", ids)
            self.assertEqual(sum(int(it["minutes"]) for it in payload["items"]), 480)
            self.assertIn("sleep", {t["id"] for t in payload["targets"]})

    def test_subprocess_entry_point_persists_across_runs(self) -> None:
        """Drive the real entry script twice; second run sees first-run state."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "e2e.json"
            entry = ROOT / "holistic" / "run_time_allocator.py"
            env = {**dict(**__import__("os").environ), "PYTHONPATH": str(ROOT)}

            def run(args: list[str]) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(entry), "--data", str(data), *args],
                    capture_output=True,
                    text=True,
                    cwd=str(ROOT),
                    env=env,
                    timeout=30,
                )

            r1 = run(["add", "First goal", "--kind", "goal", "--priority", "4", "--id", "g1"])
            self.assertEqual(r1.returncode, 0, r1.stderr + r1.stdout)
            r2 = run(["add", "Second task", "--kind", "task", "--priority", "2", "--id", "t2"])
            self.assertEqual(r2.returncode, 0, r2.stderr + r2.stdout)
            r3 = run(["remove", "t2"])
            self.assertEqual(r3.returncode, 0, r3.stderr + r3.stdout)
            r4 = run(["allocate", "120"])
            self.assertEqual(r4.returncode, 0, r4.stderr + r4.stdout)
            r5 = run(["list", "--json"])
            self.assertEqual(r5.returncode, 0, r5.stderr + r5.stdout)
            payload = json.loads(r5.stdout)
            ids = [it["id"] for it in payload["items"]]
            self.assertEqual(ids, ["g1"])
            self.assertEqual(payload["items"][0]["minutes"], 120)
            # Second process: list only — must match persisted data
            r6 = run(["list", "--json"])
            self.assertEqual(r6.returncode, 0, r6.stderr + r6.stdout)
            payload2 = json.loads(r6.stdout)
            self.assertEqual([it["id"] for it in payload2["items"]], ["g1"])
            self.assertEqual(payload2["items"][0]["minutes"], 120)


if __name__ == "__main__":
    unittest.main()
