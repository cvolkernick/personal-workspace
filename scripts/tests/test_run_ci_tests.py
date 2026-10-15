"""Tests for the #584 unified CI runner (discovery + quarantine matching)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = ROOT / "scripts" / "run_ci_tests.py"
CONFTEST_PATH = ROOT / "conftest.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


RUNNER = _load(MOD_PATH, "run_ci_tests")
HOOKS = _load(CONFTEST_PATH, "ci_conftest")


class DiscoverTests(unittest.TestCase):
    def test_discovers_known_suites(self) -> None:
        suites = {p.relative_to(ROOT).as_posix() for p in RUNNER.discover_python_suites(ROOT)}
        self.assertIn("tests", suites)
        self.assertIn("deploy/tests", suites)
        self.assertIn("projects-dashboard/tests", suites)
        self.assertTrue(any(s.startswith("archive/") for s in suites) is False)

    def test_extra_python_scripts_exist(self) -> None:
        extras = RUNNER.extra_python_files(ROOT)
        self.assertTrue(any(p.name == "test_coinbase_feasibility_doc.py" for p in extras))

    def test_gethelpfrom_node_suite_is_wired(self) -> None:
        found = RUNNER.discover_gethelpfrom_node_tests(ROOT)
        self.assertTrue(any(p.name == "web.test.js" for p in found))

    def test_js_tests_are_standalone_scripts(self) -> None:
        js = RUNNER.discover_js_tests(ROOT)
        self.assertTrue(js)
        self.assertTrue(all(p.suffix == ".js" for p in js))
        self.assertTrue(all(p.parent.name == "tests" for p in js))


class QuarantineTests(unittest.TestCase):
    def test_load_skips_comments(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "q.txt"
            path.write_text(
                "# comment\n"
                "auto-fleet/tests/test_glance.py::FormatTests::test_turo_line_does_not_invent_bookings  #123\n"
                "\n",
                encoding="utf-8",
            )
            rules = HOOKS.load_quarantine(path)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0][1], "#123")

    def test_match_full_node_and_file(self) -> None:
        rules = [
            (
                "auto-fleet/tests/test_glance.py::FormatTests::test_turo_line_does_not_invent_bookings",
                "#1",
            ),
            ("tests/test_dashboard_endpoints.py", "#2"),
        ]
        self.assertEqual(
            HOOKS.quarantine_issue(
                "auto-fleet/tests/test_glance.py::FormatTests::test_turo_line_does_not_invent_bookings",
                rules,
            ),
            "#1",
        )
        self.assertEqual(
            HOOKS.quarantine_issue(
                "tests/test_dashboard_endpoints.py::EndpointResolveTests::test_workspace_sync_script_pulls_master",
                rules,
            ),
            "#2",
        )
        self.assertIsNone(
            HOOKS.quarantine_issue(
                "auto-fleet/tests/test_glance.py::FormatTests::some_other_test",
                rules,
            )
        )


if __name__ == "__main__":
    unittest.main()


class CleanTreeGuardTests(unittest.TestCase):
    """Suites must not rewrite tracked files (a worker once committed regenerated snapshots with `commit -a`)."""

    def _repo(self, td: str) -> Path:
        root = Path(td)
        for cmd in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"]):
            subprocess.run(["git", *cmd], cwd=root, check=True)
        (root / "tracked.json").write_text("{}\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
        return root

    def test_modified_tracked_file_and_new_untracked_file_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td)
            before = RUNNER.tree_state(root)
            self.assertEqual(before, {})
            (root / "tracked.json").write_text('{"x": 1}\n', encoding="utf-8")
            (root / "new_latest.json").write_text("{}\n", encoding="utf-8")
            self.assertEqual(RUNNER.tree_changes(before, RUNNER.tree_state(root)), ["new_latest.json", "tracked.json"])

    def test_clean_run_and_preexisting_dirt_are_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = self._repo(td)
            (root / "tracked.json").write_text('{"dev": 1}\n', encoding="utf-8")  # developer's own edit
            before = RUNNER.tree_state(root)
            self.assertEqual(RUNNER.tree_changes(before, RUNNER.tree_state(root)), [])
            (root / "tracked.json").write_text('{"dev": 2}\n', encoding="utf-8")  # a test rewrote it again
            self.assertEqual(RUNNER.tree_changes(before, RUNNER.tree_state(root)), ["tracked.json"])

    def test_not_a_git_checkout_skips_the_guard(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(RUNNER.tree_state(Path(td)))
            self.assertEqual(RUNNER.tree_changes(None, {"a": "b"}), [])
