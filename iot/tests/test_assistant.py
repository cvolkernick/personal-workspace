"""Unit tests for natural-language IoT assistant (local parser)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from iot.assistant import execute_plan, parse_local, plan_command

GROUPS = {
    "entryway": {"label": "Entryway", "members": ["entryway1"]},
    "livingroom": {"label": "Living room", "members": ["livingroom1"]},
    "masterbedroom": {"label": "Master Bedroom", "members": ["masterbedroom1"]},
    "masterbathroom": {"label": "Master Bathroom", "members": ["masterbathroom1"]},
}
DEVICES = [
    "entryway1",
    "livingroom1",
    "masterbedroom1",
    "masterbathroom1",
]


class ParseLocalTests(unittest.TestCase):
    def _p(self, msg: str):
        return parse_local(
            msg, groups=GROUPS, devices=DEVICES, default_brightness=180
        )

    def test_all_off(self) -> None:
        r = self._p("all off")
        self.assertTrue(r["ok"])
        self.assertEqual(r["actions"][0]["op"], "control")
        self.assertEqual(r["actions"][0]["target"], "all")
        self.assertEqual(r["actions"][0]["color"], "off")

    def test_entryway_magenta(self) -> None:
        r = self._p("turn entryway magenta")
        self.assertTrue(r["ok"])
        a = r["actions"][0]
        self.assertEqual(a["target"], "entryway")
        self.assertEqual(a["color"], "magenta")

    def test_living_room_warm_percent(self) -> None:
        r = self._p("living room warm 50%")
        self.assertTrue(r["ok"])
        a = r["actions"][0]
        self.assertEqual(a["target"], "livingroom")
        self.assertEqual(a["color"], "warm")
        self.assertEqual(a["brightness"], 128)  # 50% of 255 ≈ 128

    def test_bedroom_brightness_only_keep(self) -> None:
        r = self._p("bedroom 40%")
        self.assertTrue(r["ok"])
        a = r["actions"][0]
        self.assertEqual(a["target"], "masterbedroom")
        self.assertEqual(a["color"], "keep")
        self.assertEqual(a["brightness"], 102)  # 40% of 255

    def test_dim_bedroom_to_percent(self) -> None:
        r = self._p("dim master bedroom to 20%")
        self.assertTrue(r["ok"])
        a = r["actions"][0]
        self.assertEqual(a["target"], "masterbedroom")
        self.assertEqual(a["color"], "keep")
        self.assertEqual(a["brightness"], 51)

    def test_master_bedroom_on(self) -> None:
        r = self._p("master bedroom on")
        self.assertTrue(r["ok"])
        a = r["actions"][0]
        self.assertEqual(a["target"], "masterbedroom")
        self.assertEqual(a["color"], "warm")

    def test_bathroom_alias(self) -> None:
        r = self._p("bathroom cyan")
        self.assertTrue(r["ok"])
        self.assertEqual(r["actions"][0]["target"], "masterbathroom")
        self.assertEqual(r["actions"][0]["color"], "cyan")

    def test_run_sunset(self) -> None:
        r = self._p("run sunset")
        self.assertTrue(r["ok"])
        self.assertEqual(r["actions"][0], {"op": "run_routine", "id": "sunset"})

    def test_status(self) -> None:
        r = self._p("status")
        self.assertTrue(r["ok"])
        self.assertEqual(r["actions"][0]["op"], "status")

    def test_help(self) -> None:
        r = self._p("help")
        self.assertTrue(r["ok"])
        self.assertEqual(r["actions"][0]["op"], "help")

    def test_device_id(self) -> None:
        r = self._p("entryway1 blue")
        self.assertTrue(r["ok"])
        self.assertEqual(r["actions"][0]["target"], "entryway1")
        self.assertEqual(r["actions"][0]["color"], "blue")

    def test_empty(self) -> None:
        r = self._p("  ")
        self.assertFalse(r["ok"])

    def test_unparsed(self) -> None:
        r = self._p("what's for dinner")
        self.assertFalse(r["ok"])
        self.assertEqual(r.get("error"), "unparsed")


class ResolveCredentialsTests(unittest.TestCase):
    def test_env_key_preferred(self) -> None:
        from iot import assistant as asst

        with mock.patch.dict(os.environ, {"XAI_API_KEY": "test-key-xyz"}, clear=False):
            c = asst.resolve_xai_credentials()
        self.assertEqual(c["source"], "xai_api_key")
        self.assertEqual(c["token"], "test-key-xyz")

    def test_supergrok_auth_file(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from iot import assistant as asst

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "auth.json"
            p.write_text(
                json.dumps(
                    {
                        "https://auth.x.ai::test": {
                            "key": "session-token-abc",
                            "email": "t@example.com",
                            "expires_at": "2099-01-01T00:00:00Z",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"XAI_API_KEY": ""}, clear=False):
                with mock.patch.object(asst, "GROK_AUTH_PATH", p):
                    c = asst.resolve_xai_credentials()
            self.assertEqual(c["source"], "supergrok_session")
            self.assertEqual(c["token"], "session-token-abc")
            self.assertFalse(c["expired"])


class ExecutePlanTests(unittest.TestCase):
    def test_dry_run_no_calls(self) -> None:
        plan = parse_local(
            "all off", groups=GROUPS, devices=DEVICES, default_brightness=180
        )
        called = []

        def ctrl(t, c, b):
            called.append((t, c, b))
            return {"ok": True}

        out = execute_plan(plan, control_fn=ctrl, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertEqual(called, [])

    def test_executes_control(self) -> None:
        plan = plan_command(
            "entryway red",
            groups=GROUPS,
            devices=DEVICES,
            default_brightness=200,
            use_grok=False,
        )
        got = []

        def ctrl(t, c, b):
            got.append((t, c, b))
            return {"ok": True, "results": [{"ok": True}]}

        out = execute_plan(plan, control_fn=ctrl, dry_run=False)
        self.assertTrue(out["ok"])
        self.assertEqual(got, [("entryway", "red", 200)])


if __name__ == "__main__":
    unittest.main()
