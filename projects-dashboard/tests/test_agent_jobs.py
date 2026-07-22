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

    def test_changed_paths_includes_committed(self) -> None:
        """Agent mid-session commits must still count as implementation."""
        import tempfile
        import subprocess
        from pathlib import Path
        from unittest import mock

        td = tempfile.TemporaryDirectory()
        repo = Path(td.name)
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True
        )
        (repo / "base.txt").write_text("b\n", encoding="utf-8")
        subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True
        )
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        (repo / "sites").mkdir()
        (repo / "sites" / "index.html").write_text("<h1>hi</h1>\n", encoding="utf-8")
        (repo / "ops").mkdir()
        (repo / "ops" / "backlog").mkdir(parents=True)
        (repo / "ops" / "backlog" / "seeds").mkdir(parents=True)
        (repo / "ops" / "backlog" / "seeds" / "x.md").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "agent work"], cwd=repo, check=True, capture_output=True
        )
        # clean tree — this is what fooled the old gate
        porcelain = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=repo, text=True
        ).strip()
        self.assertEqual(porcelain, "")
        changed = aj.changed_paths_since(repo, base)
        impl, sc = aj.split_dirty_paths(changed)
        self.assertIn("sites/index.html", impl)
        self.assertIn("ops/backlog/seeds/x.md", sc)
        self.assertGreaterEqual(aj.commits_ahead(repo, base), 1)
        td.cleanup()



if __name__ == "__main__":
    unittest.main()
