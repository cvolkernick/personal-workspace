"""Tests for the #584 unified CI runner (discovery + quarantine matching)."""

from __future__ import annotations

import importlib.util
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
