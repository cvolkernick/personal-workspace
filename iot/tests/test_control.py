"""Unit tests for pure IoT control helpers and fake-transport adapter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from iot.control import (  # noqa: E402
    COLORS,
    build_control_intent,
    device_key,
    list_color_presets,
    list_configured_devices,
    load_bulbs,
    merge_devices,
    resolve_rgb,
    summarize_registry,
)
from iot.wiz_adapter import (  # noqa: E402
    FakeTransport,
    discover_and_merge,
    execute_control,
    fetch_device_statuses,
    run_async,
)


SAMPLE = {
    "entryway1": {"ip": "192.168.100.106", "mac": "6c2990089296"},
    "entryway2": {"ip": "192.168.100.118", "mac": "6c2990d5075a"},
}


class LoadBulbsTests(unittest.TestCase):
    def test_load_real_bulbs_json(self) -> None:
        path = ROOT / "iot" / "wiz-lights" / "bulbs.json"
        bulbs = load_bulbs(path)
        self.assertIn("entryway1", bulbs)
        self.assertEqual(bulbs["entryway1"]["ip"], "192.168.100.106")
        self.assertGreaterEqual(len(bulbs), 4)

    def test_load_from_temp_file(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(SAMPLE, f)
            path = f.name
        bulbs = load_bulbs(path)
        self.assertEqual(set(bulbs), {"entryway1", "entryway2"})
        self.assertEqual(bulbs["entryway1"]["mac"], "6c2990089296")


class IntentTests(unittest.TestCase):
    def test_presets_include_off_and_cyan(self) -> None:
        presets = list_color_presets()
        self.assertIn("off", presets)
        self.assertIn("cyan", presets)
        self.assertIsNone(COLORS["off"])
        self.assertEqual(resolve_rgb("cyan"), (0, 255, 255))
        self.assertIsNone(resolve_rgb("off"))

    def test_control_intent_all_on(self) -> None:
        intent = build_control_intent("all", "red", 150, registry=SAMPLE)
        self.assertTrue(intent["ok"])
        self.assertEqual(intent["action"], "on")
        self.assertEqual(intent["rgb"], [255, 0, 0])
        self.assertEqual(intent["brightness"], 150)
        self.assertEqual(len(intent["targets"]), 2)
        names = {t["name"] for t in intent["targets"]}
        self.assertEqual(names, {"entryway1", "entryway2"})

    def test_control_intent_single_off(self) -> None:
        intent = build_control_intent("entryway1", "off", registry=SAMPLE)
        self.assertTrue(intent["ok"])
        self.assertEqual(intent["action"], "off")
        self.assertIsNone(intent["rgb"])
        self.assertEqual(len(intent["targets"]), 1)
        self.assertEqual(intent["targets"][0]["ip"], "192.168.100.106")

    def test_control_intent_unknown(self) -> None:
        intent = build_control_intent("nope", "blue", registry=SAMPLE)
        self.assertFalse(intent["ok"])
        self.assertEqual(intent["targets"], [])
        self.assertIn("unknown", intent["error"] or "")

    def test_control_intent_by_ip(self) -> None:
        intent = build_control_intent("10.0.0.5", "white", registry=SAMPLE)
        self.assertTrue(intent["ok"])
        self.assertEqual(intent["targets"][0]["ip"], "10.0.0.5")


class MergeTests(unittest.TestCase):
    def test_merge_enriches_and_adds_new(self) -> None:
        configured = list_configured_devices(SAMPLE)
        discovered = [
            {
                "ip": "192.168.100.106",
                "mac": "6c2990089296",
                "type": "wiz",
            },
            {
                "ip": "192.168.100.200",
                "mac": "aabbccddeeff",
                "type": "wiz",
                "name": "wiz-new",
            },
            {
                "name": "Nest-Audio",
                "type": "mdns",
                "service": "_googlecast._tcp",
                "controllable": False,
            },
        ]
        merged = merge_devices(configured, discovered)
        ids = {d["id"] for d in merged}
        self.assertIn("entryway1", ids)
        self.assertIn("entryway2", ids)
        # new wiz
        new_ones = [d for d in merged if d.get("mac") == "aabbccddeeff"]
        self.assertEqual(len(new_ones), 1)
        self.assertEqual(new_ones[0]["source"], "discovery")
        # config device marked seen
        e1 = next(d for d in merged if d["id"] == "entryway1")
        self.assertTrue(e1.get("seen_on_network"))
        # mdns note present
        mdns = [d for d in merged if d.get("type") == "mdns"]
        self.assertEqual(len(mdns), 1)
        self.assertFalse(mdns[0]["controllable"])

    def test_ip_mismatch_flag(self) -> None:
        configured = list_configured_devices(
            {"entryway3": {"ip": "192.168.100.185", "mac": "6c29904e244e"}}
        )
        discovered = [
            {"ip": "192.168.100.184", "mac": "6c29904e244e", "type": "wiz"}
        ]
        merged = merge_devices(configured, discovered)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].get("ip_mismatch"))
        self.assertEqual(merged[0]["discovered_ip"], "192.168.100.184")

    def test_device_key_prefers_mac(self) -> None:
        self.assertEqual(
            device_key({"mac": "AA:BB:CC", "ip": "1.2.3.4"}),
            "mac:aabbcc",
        )


class FakeTransportTests(unittest.TestCase):
    def test_execute_control_on_and_off(self) -> None:
        t = FakeTransport()
        result = run_async(
            execute_control("entryway1", "cyan", 120, registry=SAMPLE, transport=t)
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "on")
        self.assertEqual(result["rgb"], [0, 255, 255])
        self.assertEqual(len(result["results"]), 1)
        self.assertTrue(result["results"][0]["ok"])
        self.assertEqual(t.calls[0]["op"], "on")
        self.assertEqual(t.calls[0]["rgb"], [0, 255, 255])
        self.assertEqual(t.calls[0]["brightness"], 120)

        off = run_async(
            execute_control("all", "off", registry=SAMPLE, transport=t)
        )
        self.assertTrue(off["ok"])
        self.assertEqual(off["action"], "off")
        self.assertEqual(len(off["results"]), 2)
        off_calls = [c for c in t.calls if c["op"] == "off"]
        self.assertEqual(len(off_calls), 2)

    def test_execute_control_partial_failure(self) -> None:
        t = FakeTransport()
        t.fail_ips.add("192.168.100.106")
        result = run_async(
            execute_control("all", "white", registry=SAMPLE, transport=t)
        )
        self.assertFalse(result["ok"])
        oks = [r["ok"] for r in result["results"]]
        self.assertIn(True, oks)
        self.assertIn(False, oks)

    def test_fetch_statuses_via_fake(self) -> None:
        t = FakeTransport()
        run_async(execute_control("entryway2", "blue", registry=SAMPLE, transport=t))
        devices = run_async(
            fetch_device_statuses(registry=SAMPLE, transport=t)
        )
        self.assertEqual(len(devices), 2)
        e2 = next(d for d in devices if d["id"] == "entryway2")
        self.assertTrue(e2["status"]["ok"])
        self.assertTrue(e2["status"]["on"])
        self.assertEqual(e2["status"]["rgb"], [0, 0, 255])

    def test_discover_and_merge_via_fake(self) -> None:
        t = FakeTransport()
        t.discover_result = [
            {
                "ip": "192.168.100.200",
                "mac": "112233445566",
                "type": "wiz",
                "name": "wiz-200",
                "id": "wiz-200",
                "controllable": True,
            }
        ]
        out = run_async(
            discover_and_merge(registry=SAMPLE, transport=t, wait_time=0.1)
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["discovered_count"], 1)
        ids = {d["id"] for d in out["devices"]}
        self.assertIn("entryway1", ids)
        self.assertTrue(any("200" in str(d.get("ip")) or "wiz" in str(d.get("id")) for d in out["devices"]))

    def test_summarize_registry(self) -> None:
        s = summarize_registry(SAMPLE)
        self.assertEqual(s["count"], 2)
        self.assertEqual(set(s["names"]), {"entryway1", "entryway2"})


if __name__ == "__main__":
    unittest.main()
