"""Skip-logic for FitDash Vercel ignoreCommand — no Vercel API required."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from vercel_ignore import (  # noqa: E402
    decide_exit,
    is_fitdash_path,
    load_fitdash_prefixes,
    resolve_compare_base,
    should_skip_build,
)

SCRIPT = ROOT / "scripts" / "vercel_ignore.py"


def _run(*paths: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--changed", *paths],
        text=True,
        capture_output=True,
        check=False,
    )


class TestFitDashPrefixes(unittest.TestCase):
    def test_paths_file_lists_resistance_dashboard_only(self):
        prefixes = load_fitdash_prefixes()
        self.assertEqual(prefixes, ["resistance-dashboard/"])

    def test_fitdash_app_paths_count(self):
        for path in (
            "resistance-dashboard/server.py",
            "resistance-dashboard/vercel.json",
            "resistance-dashboard/scripts/vercel_ignore.py",
            "resistance-dashboard/static/app.js",
            "resistance-dashboard",
        ):
            self.assertTrue(is_fitdash_path(path), path)

    def test_other_apps_do_not_count(self):
        for path in (
            "orchestra/server.py",
            "auto-fleet/static/index.html",
            "treasury/config.json",
            "financial-command/vercel.json",
            "work/treasury/notes.md",
            "fitness/workouts/push.md",
            "deploy/path_unit_map.json",
            "README.md",
        ):
            self.assertFalse(is_fitdash_path(path), path)

    def test_skip_when_no_fitdash_paths(self):
        self.assertTrue(
            should_skip_build(["orchestra/server.py", "auto-fleet/glance.py"])
        )
        self.assertEqual(decide_exit(["orchestra/server.py", "feat/fcc.txt"]), 0)

    def test_build_when_any_fitdash_path(self):
        self.assertFalse(
            should_skip_build(["orchestra/server.py", "resistance-dashboard/README.md"])
        )
        self.assertEqual(decide_exit(["resistance-dashboard/api/healthz.py"]), 1)

    def test_cli_exit_codes_match_vercel_contract(self):
        skip = _run("orchestra/server.py", "work/treasury/foo.py")
        self.assertEqual(skip.returncode, 0, skip.stderr)
        build = _run("resistance-dashboard/server.py")
        self.assertEqual(build.returncode, 1, build.stderr)

    def test_vercel_json_ignore_command(self):
        cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        self.assertEqual(
            cfg.get("ignoreCommand"),
            "python3 scripts/vercel_ignore.py || exit 1",
        )
        self.assertEqual(list(cfg.keys()), ["$schema", "ignoreCommand"])


class TestGitCompare(unittest.TestCase):
    def test_missing_base_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            self.assertIsNone(resolve_compare_base(repo, previous_sha="deadbeef" * 5))
            self.assertEqual(decide_exit(repo_root=repo, previous_sha="missing"), 1)

    def test_git_diff_skip_and_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "FitDash Ignore Test"],
                cwd=repo,
                check=True,
            )
            (repo / "orchestra").mkdir()
            (repo / "orchestra" / "server.py").write_text("print(1)\n", encoding="utf-8")
            (repo / "resistance-dashboard").mkdir()
            (repo / "resistance-dashboard" / "server.py").write_text(
                "print(0)\n", encoding="utf-8"
            )
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "base"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            (repo / "orchestra" / "server.py").write_text("print(2)\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "orchestra/server.py"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "orchestra only"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            self.assertEqual(decide_exit(repo_root=repo), 0)

            (repo / "resistance-dashboard" / "server.py").write_text(
                "print(3)\n", encoding="utf-8"
            )
            subprocess.run(
                ["git", "add", "resistance-dashboard/server.py"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "fitdash"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            self.assertEqual(decide_exit(repo_root=repo), 1)


if __name__ == "__main__":
    unittest.main()
