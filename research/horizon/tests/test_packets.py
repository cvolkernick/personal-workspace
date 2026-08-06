#!/usr/bin/env python3
"""Implication packet v0 producer + validation tests (#49)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon.packets import (  # noqa: E402
    MAX_EDGES,
    MAX_IMPLICATIONS,
    MAX_NODES,
    PacketValidationError,
    assert_valid_packet,
    build_l0_down_packet,
    schema_path,
    validate_packet,
)
from research.horizon.pipeline import run_pipeline  # noqa: E402
from research.horizon.store import load_packet, packet_latest_path  # noqa: E402


class TestPacketSchemaArtifact(unittest.TestCase):
    def test_json_schema_file_exists(self):
        path = schema_path()
        self.assertTrue(path.is_file(), f"missing {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("title"), "Implication packet v0")
        self.assertIn("nodes", data.get("properties") or {})
        nodes = (data.get("properties") or {}).get("nodes") or {}
        self.assertEqual(nodes.get("maxItems"), MAX_NODES)

    def test_shipped_example_fixture_validates(self):
        fixture = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "implication_packet_v0_example.json"
        )
        self.assertTrue(fixture.is_file())
        packet = json.loads(fixture.read_text(encoding="utf-8"))
        errors = validate_packet(packet)
        self.assertEqual(errors, [], errors)


class TestPacketProducer(unittest.TestCase):
    def test_build_l0_down_from_offline_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            result = run_pipeline(workspace=ROOT, data_dir=data_dir, offline=True)
            self.assertTrue(result["ok"])
            packet = result["packet"]
            self.assertEqual(packet["level"], "L0")
            self.assertEqual(packet["direction"], "down")
            self.assertEqual(packet["schema_version"], 1)
            self.assertEqual(packet["as_of"], packet["freshness"]["as_of"])
            self.assertLessEqual(len(packet["nodes"]), MAX_NODES)
            self.assertLessEqual(len(packet["edges"]), MAX_EDGES)
            self.assertLessEqual(len(packet["implications_for_l4"]), MAX_IMPLICATIONS)
            self.assertGreaterEqual(len(packet["implications_for_l4"]), 1)
            self.assertEqual(packet["constraints_from_l4"], [])
            self.assertEqual(packet["producer"]["domain"], "horizon")
            self.assertIn("primary_label", packet["regime_summary"])
            # Fail-closed write path
            latest = packet_latest_path(data_dir)
            self.assertTrue(latest.is_file())
            loaded = load_packet(data_dir)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["packet_id"], packet["packet_id"])
            assert_valid_packet(loaded)

    def test_fail_closed_rejects_bad_packet(self):
        bad = {
            "schema_version": 1,
            "packet_id": "x",
            "level": "L0",
            "direction": "down",
            "as_of": "2026-08-06T00:00:00+00:00",
            "producer": {"domain": "horizon", "surface": "horizon-macro"},
            "freshness": {
                "as_of": "2026-08-06T00:00:00+00:00",
                "max_age_hours": 168,
                "stale": False,
                "confidence_overall": 0.5,
            },
            "nodes": [],
            "implications_for_l4": [],  # down requires ≥1
            "constraints_from_l4": [],
        }
        errors = validate_packet(bad)
        self.assertTrue(any("implications_for_l4" in e for e in errors))
        with self.assertRaises(PacketValidationError):
            assert_valid_packet(bad)

    def test_build_from_state_without_brief(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            result = run_pipeline(workspace=ROOT, data_dir=data_dir, offline=True)
            state = result["state"]
            packet = build_l0_down_packet(state, brief=None)
            self.assertEqual(packet["level"], "L0")
            self.assertGreaterEqual(len(packet["implications_for_l4"]), 1)


class TestPacketHTTP(unittest.TestCase):
    def test_build_packet_response(self):
        from research.horizon.server import build_packet_response

        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            run_pipeline(workspace=ROOT, data_dir=data_dir, offline=True)
            code, body = build_packet_response(data_dir, level="L0")
            self.assertEqual(code, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["level"], "L0")
            self.assertIn("packet", body)
            self.assertTrue(body["validation"]["valid"])

            code2, body2 = build_packet_response(data_dir, level="L4")
            self.assertEqual(code2, 400)
            self.assertFalse(body2["ok"])

            empty = Path(td) / "empty"
            empty.mkdir()
            code3, body3 = build_packet_response(empty, level="L0")
            self.assertEqual(code3, 404)


if __name__ == "__main__":
    unittest.main()
