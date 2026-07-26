#!/usr/bin/env python3
"""Structural + API tests for Horizon dashboard server."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HORIZON = Path(__file__).resolve().parents[1]


class TestDashboardArtifacts(unittest.TestCase):
    def test_index_html_exists_with_key_sections(self):
        html = (HORIZON / "index.html").read_text(encoding="utf-8")
        for needle in (
            "Horizon",
            "domain-heat",
            "Executive brief",
            "Implications for my strategy",
            "Watchlist",
            "/api/dashboard",
            "/api/refresh",
            "fact-box",
            "interp-box",
        ):
            self.assertIn(needle, html)

    def test_build_dashboard_payload_shipped(self):
        from research.horizon.pipeline import run_pipeline
        from research.horizon.server import build_dashboard_payload
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            run_pipeline(workspace=ROOT, data_dir=data_dir, offline=True)
            # Point DEFAULT-style payload helper with explicit dirs
            payload = build_dashboard_payload(ROOT, data_dir)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["has_brief"])
            self.assertTrue(payload["has_world_state"])
            self.assertEqual(len(payload["domain_stats"]), 10)
            self.assertIn("executive_brief", payload["brief"])
            self.assertIn("watchlist", payload["brief"])


if __name__ == "__main__":
    unittest.main()
