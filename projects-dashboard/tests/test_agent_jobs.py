"""Tests for agent job helpers (scaffold vs implementation PR rules)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

import agent_jobs as aj  # noqa: E402


class TestAgentJobs(unittest.TestCase):
    def test_scaffold_paths(self) -> None:
        self.assertTrue(aj.is_scaffold_path("ops/backlog/seeds/foo.prompt.txt"))
        self.assertTrue(aj.is_scaffold_path("ops/backlog/items.json"))
        self.assertTrue(aj.is_scaffold_path("ops/backlog/reports/rpt-1.json"))
        self.assertFalse(aj.is_scaffold_path("business/panamerica/index.html"))
        self.assertFalse(aj.is_scaffold_path("holistic/time_allocator/cli.py"))

    def test_split_dirty(self) -> None:
        impl, sc = aj.split_dirty_paths(
            [
                "ops/backlog/seeds/x.md",
                "ops/backlog/items.json",
                "sites/panamerica/index.html",
                "sites/panamerica/README.md",
            ]
        )
        self.assertEqual(impl, ["sites/panamerica/index.html", "sites/panamerica/README.md"])
        self.assertEqual(sc, ["ops/backlog/seeds/x.md", "ops/backlog/items.json"])

    def test_build_prompt_requires_mvp(self) -> None:
        p = aj.build_agent_prompt(
            {
                "title": "Demo site",
                "mvp_scope": "one page",
                "area": "business",
                "id": "abc",
            },
            job_id="job-1",
        )
        self.assertIn("implemented MVP", p)
        self.assertIn("one page", p)
        self.assertNotIn("/goal", p)


if __name__ == "__main__":
    unittest.main()
