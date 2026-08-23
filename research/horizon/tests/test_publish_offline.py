#!/usr/bin/env python3
"""Tests for nest/GH offline publish helper (#301)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon.publish_offline import (  # noqa: E402
    PUBLISH_PATH,
    PUBLISH_SOT_RELATIVE,
    VERSION_ID_RE,
    artifact_has_secret_leak,
    is_valid_version_id,
    publish_offline,
    publish_sot_paths,
    require_version_id,
    stamp_version_id,
)
from research.horizon.store import (  # noqa: E402
    DEFAULT_DATA_DIR,
    save_json,
    world_state_latest_path,
)
from research.horizon.world_state import empty_world_state  # noqa: E402


PACKAGE = ROOT / "research" / "horizon"
SHIPPED_LATEST = PACKAGE / "data" / "world_state_latest.json"


def _node_facts(state: dict) -> list[tuple[str, tuple[str, ...]]]:
    rows = []
    for bucket in (state.get("domains") or {}).values():
        for node in bucket.get("nodes") or []:
            rows.append((node.get("id"), tuple(node.get("facts") or [])))
    return sorted(rows)


class TestVersionIdStamp(unittest.TestCase):
    def test_stamp_matches_existing_schema(self):
        vid = stamp_version_id()
        self.assertTrue(is_valid_version_id(vid))
        self.assertRegex(vid, r"^\d{8}T\d{6}Z$")
        self.assertEqual(VERSION_ID_RE.pattern, r"^\d{8}T\d{6}Z$")

    def test_require_version_id_rejects_invented_shapes(self):
        with self.assertRaises(ValueError):
            require_version_id("v1-not-iso")
        with self.assertRaises(ValueError):
            require_version_id("2026-08-23")
        self.assertEqual(require_version_id("20260823T221500Z"), "20260823T221500Z")


class TestPublishPathsAndGitignore(unittest.TestCase):
    def test_documented_sot_paths_exist_on_shipped_tree(self):
        paths = publish_sot_paths(DEFAULT_DATA_DIR)
        self.assertEqual(paths["data_dir"], DEFAULT_DATA_DIR)
        self.assertTrue(paths["world_state_latest"].is_file())
        self.assertTrue(paths["brief_latest_json"].is_file())
        self.assertTrue(paths["brief_latest_md"].is_file())
        for rel in PUBLISH_SOT_RELATIVE:
            self.assertTrue((DEFAULT_DATA_DIR / rel).is_file(), rel)

    def test_helper_module_and_docs_exist(self):
        self.assertTrue((PACKAGE / "publish_offline.py").is_file())
        self.assertTrue((PACKAGE / "docs" / "OFFLINE_PUBLISH.md").is_file())
        self.assertTrue((PACKAGE / "run_horizon.py").is_file())

    def test_gitignore_keeps_publish_sot_pointers(self):
        gi = (PACKAGE / "data" / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("!briefs/brief_latest.json", gi)
        self.assertIn("!briefs/brief_latest.md", gi)
        ignore_lines = [
            ln.strip()
            for ln in gi.splitlines()
            if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith("!")
        ]
        self.assertFalse(
            any("world_state_latest" in ln for ln in ignore_lines),
            "world_state_latest.json must remain committable",
        )
        self.assertTrue(any(ln.startswith("history/") for ln in ignore_lines))


class TestPublishOffline(unittest.TestCase):
    def test_held_when_no_latest_and_fixtures_not_requested(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            result = publish_offline(
                workspace=ROOT,
                data_dir=data_dir,
                version_id="20260823T221500Z",
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["held"])
            self.assertEqual(result["publish_path"], PUBLISH_PATH)
            self.assertFalse((data_dir / "world_state_latest.json").exists())
            self.assertEqual(list((data_dir / "history").glob("world_state_*.json")), [])

    def test_restamp_writes_paths_preserves_facts_no_secret_leak(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            source = json.loads(SHIPPED_LATEST.read_text(encoding="utf-8"))
            prior = source["version_id"]
            regime_primary = ((source.get("regime") or {}).get("primary") or {}).get("id")
            facts_before = _node_facts(source)
            self.assertGreater(len(facts_before), 0)
            save_json(world_state_latest_path(data_dir), source)

            vid = "20260823T221501Z"
            result = publish_offline(
                workspace=ROOT,
                data_dir=data_dir,
                version_id=vid,
            )
            self.assertTrue(result["ok"])
            self.assertFalse(result["held"])
            self.assertEqual(result["version_id"], vid)
            self.assertEqual(result["prior_version_id"], prior)
            self.assertFalse(result["from_fixtures"])

            latest = Path(result["paths"]["world_state_latest"])
            hist = Path(result["paths"]["history"])
            brief_json = Path(result["paths"]["brief_latest_json"])
            brief_md = Path(result["paths"]["brief_latest_md"])
            for path in (latest, hist, brief_json, brief_md):
                self.assertTrue(path.is_file(), path)
                text = path.read_text(encoding="utf-8")
                self.assertFalse(artifact_has_secret_leak(text), path)

            written = json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(written["version_id"], vid)
            self.assertEqual((written.get("meta") or {}).get("publish_path"), PUBLISH_PATH)
            self.assertEqual(_node_facts(written), facts_before)
            self.assertEqual(
                ((written.get("regime") or {}).get("primary") or {}).get("id"),
                regime_primary,
            )
            brief = json.loads(brief_json.read_text(encoding="utf-8"))
            self.assertEqual(brief["version_id"], vid)
            self.assertIn(vid, brief_md.read_text(encoding="utf-8"))

    def test_from_fixtures_writes_sot_from_shipped_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            fixture = PACKAGE / "fixtures" / "sample_events.json"
            self.assertTrue(fixture.is_file())
            result = publish_offline(
                workspace=ROOT,
                data_dir=data_dir,
                from_fixtures=True,
                fixture_path=fixture,
                version_id="20260823T221502Z",
            )
            self.assertTrue(result["ok"])
            self.assertFalse(result["held"])
            self.assertTrue(result["from_fixtures"])
            self.assertEqual(result["version_id"], "20260823T221502Z")
            state = json.loads(Path(result["paths"]["world_state_latest"]).read_text())
            self.assertEqual(state["version_id"], "20260823T221502Z")
            self.assertGreaterEqual((state.get("meta") or {}).get("node_total") or 0, 1)
            for path in (
                result["paths"]["world_state_latest"],
                result["paths"]["brief_latest_json"],
                result["paths"]["brief_latest_md"],
            ):
                self.assertFalse(
                    artifact_has_secret_leak(Path(path).read_text(encoding="utf-8")),
                    path,
                )

    def test_cli_publish_offline_held(self):
        from research.horizon.run_horizon import main

        with tempfile.TemporaryDirectory() as td:
            code = main(
                [
                    "--publish-offline",
                    "--data-dir",
                    td,
                    "--workspace",
                    str(ROOT),
                    "--version-id",
                    "20260823T221503Z",
                ]
            )
            self.assertEqual(code, 0)
            self.assertFalse((Path(td) / "world_state_latest.json").exists())


class TestNoSecretLeakHelper(unittest.TestCase):
    def test_secret_scan_catches_private_key_and_allows_normal_brief(self):
        self.assertTrue(
            artifact_has_secret_leak("-----BEGIN RSA PRIVATE KEY-----\nMIIB")
        )
        self.assertTrue(artifact_has_secret_leak("token ghp_" + ("a" * 24)))
        shipped = (PACKAGE / "data" / "briefs" / "brief_latest.md").read_text(
            encoding="utf-8"
        )
        self.assertFalse(artifact_has_secret_leak(shipped))


if __name__ == "__main__":
    unittest.main()
