"""Status assembler with a fake GitHub client."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import status as status_mod  # noqa: E402
from ghclient import GitHubError  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
README = (FIXTURES / "readme.md").read_text(encoding="utf-8")


def _hub(**overrides: object) -> dict:
    base = {
        "full_name": "makerspet/oomwoo",
        "description": "Open-source vacuum robot cleaner",
        "html_url": "https://github.com/makerspet/oomwoo",
        "homepage": "https://oomwoo.com",
        "stargazers_count": 10039,
        "forks_count": 598,
        "open_issues_count": 3,
        "pushed_at": "2026-08-31T06:53:23Z",
        "updated_at": "2026-09-01T18:45:21Z",
        "language": "Python",
        "default_branch": "main",
        "topics": ["ros2"],
        "license": {"spdx_id": "Apache-2.0"},
        "archived": False,
    }
    base.update(overrides)
    return base


class FakeGitHub:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_text(self, path: str) -> str:
        self.calls.append(path)
        if path.endswith("/README.md"):
            return README
        raise GitHubError(f"no text {path}", status=404)

    def get_json(self, path: str) -> object:
        self.calls.append(path)
        parsed = urlparse(path)
        p = parsed.path
        if p == "/repos/makerspet/oomwoo":
            return _hub()
        if p == "/repos/makerspet/oomwoo/issues":
            return [
                {
                    "number": 18,
                    "title": "Evaluate Rust",
                    "html_url": "https://github.com/makerspet/oomwoo/issues/18",
                    "user": {"login": "xbattlax"},
                    "updated_at": "2026-08-30T05:13:34Z",
                    "labels": [{"name": "Discussion"}],
                },
                {
                    "number": 60,
                    "title": "a PR listed as an issue",
                    "html_url": "https://github.com/makerspet/oomwoo/pull/60",
                    "user": {"login": "yueqin22"},
                    "updated_at": "2026-08-28T22:00:26Z",
                    "labels": [],
                    "pull_request": {"url": "https://api.github.com/repos/makerspet/oomwoo/pulls/60"},
                },
            ]
        if p == "/repos/makerspet/oomwoo/pulls":
            return [
                {
                    "number": 60,
                    "title": "feat(observability): Phase-0",
                    "html_url": "https://github.com/makerspet/oomwoo/pull/60",
                    "user": {"login": "yueqin22"},
                    "updated_at": "2026-08-28T22:00:26Z",
                    "draft": False,
                    "labels": [],
                }
            ]
        if p == "/repos/makerspet/oomwoo/commits":
            return [
                {
                    "sha": "dda5c48939",
                    "html_url": "https://github.com/makerspet/oomwoo/commit/dda5c48939",
                    "commit": {
                        "message": "chore: refresh star-history chart [skip ci]",
                        "author": {
                            "name": "github-actions[bot]",
                            "date": "2026-08-31T06:53:22Z",
                        },
                    },
                    "author": {"login": "github-actions[bot]"},
                },
                {
                    "sha": "fd9d9f1ff1",
                    "html_url": "https://github.com/makerspet/oomwoo/commit/fd9d9f1ff1",
                    "commit": {
                        "message": "Merge pull request #61 from IKsares/part-specs-drive-wheel\n\nbody",
                        "author": {"name": "Maker's Pet", "date": "2026-08-30T05:14:15Z"},
                    },
                    "author": {"login": "makers-pet"},
                },
            ]
        if p == "/repos/makerspet/oomwoo-pcb":
            return _hub(
                full_name="makerspet/oomwoo-pcb",
                description="I/O board",
                html_url="https://github.com/makerspet/oomwoo-pcb",
                stargazers_count=32,
                forks_count=11,
                open_issues_count=0,
            )
        if p.startswith("/repos/"):
            raise GitHubError("missing", status=404)
        raise GitHubError(f"unexpected {path}")


class StatusTests(unittest.TestCase):
    def setUp(self) -> None:
        status_mod._CACHE["payload"] = None
        status_mod._CACHE["at"] = 0.0

    def test_payload_shape_filters_prs_and_bots(self) -> None:
        fake = FakeGitHub()
        payload = status_mod.build_status(fake, ttl=0, refresh=True)  # type: ignore[arg-type]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["hub"]["stars"], 10039)
        self.assertEqual(payload["progress"]["modules_done"], 3)
        self.assertEqual(len(payload["issues"]), 1)
        self.assertEqual(payload["issues"][0]["number"], 18)
        self.assertEqual(len(payload["pulls"]), 1)
        self.assertEqual(payload["last_human_commit"]["author"], "makers-pet")
        self.assertTrue(payload["commits"][0]["bot"])
        names = {r["full_name"] for r in payload["related"]}
        self.assertIn("makerspet/oomwoo", names)
        self.assertIn("makerspet/oomwoo-pcb", names)
        self.assertNotIn("makerspet/oomwoo-install", names)
        self.assertEqual(payload["errors"], [])

    def test_readme_failure_still_returns_hub(self) -> None:
        class Boom(FakeGitHub):
            def get_text(self, path: str) -> str:
                raise GitHubError("nope", status=500)

        payload = status_mod.build_status(Boom(), ttl=0, refresh=True)  # type: ignore[arg-type]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["modules"], [])
        self.assertTrue(any(e.startswith("readme:") for e in payload["errors"]))
        self.assertEqual(payload["hub"]["full_name"], "makerspet/oomwoo")

    def test_load_fixture(self) -> None:
        data = status_mod.load_fixture(str(FIXTURES / "status.json"))
        self.assertEqual(data["hub"]["stars"], 10039)
        self.assertTrue(data["ok"])


if __name__ == "__main__":
    unittest.main()
