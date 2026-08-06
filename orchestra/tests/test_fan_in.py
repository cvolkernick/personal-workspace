"""Tests for Orchestra fan-in strip (#51)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "orchestra"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fan_in import (  # noqa: E402
    build_fan_in,
    build_host_slice,
    build_implications_slice,
    build_regime_slice,
    heartbeat_path,
    packet_path,
)
from payload import build_orchestra_payload  # noqa: E402


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


class HostSliceTests(unittest.TestCase):
    def test_missing_heartbeat(self) -> None:
        p = Path("/tmp/no-such-heartbeat-51.json")
        h = build_host_slice(None, path=p)
        self.assertFalse(h["available"])
        self.assertIsNone(h["ok"])

    def test_ok_true_with_yellow_degraded_not_flipped(self) -> None:
        """Producer ok:true + yellow degraded must stay ok (contract)."""
        doc = {
            "ok": True,
            "as_of": "2026-08-06T03:00:00+00:00",
            "host": "prism-gateway",
            "host_role": "prod",
            "degraded": [
                {"service": "iot-dashboard", "reason": "unit_inactive", "severity": "yellow"}
            ],
        }
        h = build_host_slice(doc, path=Path("x"), now=datetime(2026, 8, 6, 3, 1, tzinfo=timezone.utc))
        self.assertTrue(h["available"])
        self.assertTrue(h["ok"])
        self.assertEqual(len(h["degraded"]), 1)
        self.assertEqual(h["age_seconds"], 60.0)

    def test_ok_false_critical(self) -> None:
        doc = {
            "ok": False,
            "as_of": "2026-08-06T03:00:00+00:00",
            "host": "prism-gateway",
            "degraded": [{"service": "orchestra-dashboard", "severity": "red", "reason": "x"}],
        }
        h = build_host_slice(doc, path=Path("x"))
        self.assertFalse(h["ok"])


class RegimeImplicationsTests(unittest.TestCase):
    def test_stub_without_packet(self) -> None:
        r = build_regime_slice(None)
        i = build_implications_slice(None)
        self.assertFalse(r["available"])
        self.assertFalse(i["available"])
        self.assertEqual(i["top"], [])
        self.assertIn("stub", (r.get("note") or "").lower())

    def test_reads_packet_fields(self) -> None:
        packet = {
            "as_of": "2026-08-06T02:00:00+00:00",
            "regime_summary": {
                "primary_label": "risk-off",
                "primary_probability": 0.62,
                "confidence": 0.7,
                "note": "test",
            },
            "freshness": {"as_of": "2026-08-06T02:00:00+00:00", "stale": False, "confidence_overall": 0.7},
            "implications_for_l4": [
                {
                    "id": "a",
                    "action": "Watch liquidity",
                    "owner_domain": "capital",
                    "urgency": "watch",
                    "confidence": 0.5,
                },
                {
                    "id": "b",
                    "action": "Defer hire",
                    "owner_domain": "work",
                    "urgency": "this_week",
                    "confidence": 0.8,
                },
                {
                    "id": "c",
                    "action": "Cut spend",
                    "owner_domain": "capital",
                    "urgency": "immediate",
                    "confidence": 0.9,
                },
            ],
        }
        r = build_regime_slice(packet)
        self.assertTrue(r["available"])
        self.assertEqual(r["primary_label"], "risk-off")
        i = build_implications_slice(packet, top_n=2)
        self.assertTrue(i["available"])
        self.assertEqual(i["count"], 3)
        self.assertEqual(len(i["top"]), 2)
        # immediate before this_week before watch
        self.assertEqual(i["top"][0]["id"], "c")
        self.assertEqual(i["top"][1]["id"], "b")


class FanInIntegrationTests(unittest.TestCase):
    def test_build_fan_in_empty_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            fan = build_fan_in(ws)
            self.assertTrue(fan["ok"])
            self.assertFalse(fan["host"]["available"])
            self.assertFalse(fan["regime"]["available"])
            self.assertFalse(fan["implications"]["available"])
            self.assertEqual(
                Path(fan["sources"]["heartbeat_path"]).resolve(),
                heartbeat_path(ws).resolve(),
            )
            self.assertEqual(
                Path(fan["sources"]["packet_path"]).resolve(),
                packet_path(ws).resolve(),
            )

    def test_build_fan_in_with_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            _write(
                heartbeat_path(ws),
                {
                    "schema_version": 1,
                    "host": "prism-gateway",
                    "host_role": "prod",
                    "as_of": "2026-08-06T03:30:00+00:00",
                    "ok": True,
                    "degraded": [
                        {"service": "iot-dashboard", "reason": "unit_inactive", "severity": "yellow"}
                    ],
                },
            )
            _write(
                packet_path(ws),
                {
                    "schema_version": 1,
                    "as_of": "2026-08-06T03:00:00+00:00",
                    "regime_summary": {
                        "primary_label": "neutral",
                        "primary_probability": 0.5,
                        "confidence": 0.6,
                    },
                    "implications_for_l4": [
                        {
                            "id": "1",
                            "action": "Hold dry powder",
                            "owner_domain": "capital",
                            "urgency": "this_week",
                            "confidence": 0.7,
                        }
                    ],
                    "freshness": {"stale": False, "confidence_overall": 0.6},
                },
            )
            fan = build_fan_in(ws)
            self.assertTrue(fan["host"]["available"])
            self.assertTrue(fan["host"]["ok"])
            self.assertEqual(fan["host"]["host"], "prism-gateway")
            self.assertTrue(fan["regime"]["available"])
            self.assertEqual(fan["regime"]["primary_label"], "neutral")
            self.assertTrue(fan["implications"]["available"])
            self.assertEqual(fan["implications"]["top"][0]["action"], "Hold dry powder")

    def test_payload_includes_fan_in_on_fixture(self) -> None:
        """Offline fixture path still works (existing orchestra fixture pattern)."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            # minimal strategy so collectors don't explode
            (ws / "strategy").mkdir(parents=True)
            (ws / "strategy" / "bets.md").write_text("# Bets\n- **AI**\n", encoding="utf-8")
            (ws / "strategy" / "today.md").write_text("# Today\n", encoding="utf-8")
            (ws / "initiatives").mkdir(parents=True)
            (ws / "ops" / "backlog").mkdir(parents=True)
            (ws / "ops" / "backlog" / "items.json").write_text("[]\n", encoding="utf-8")
            payload = build_orchestra_payload(ws, probe_ports=False)
            self.assertTrue(payload.get("ok"))
            self.assertIn("fan_in", payload)
            self.assertFalse(payload["fan_in"]["host"]["available"])
            self.assertFalse(payload["fan_in"]["regime"]["available"])


if __name__ == "__main__":
    unittest.main()
