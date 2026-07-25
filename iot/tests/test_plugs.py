"""Unit tests for plug helpers (no network)."""

from __future__ import annotations

import unittest

from iot.plugs import color_to_power, is_plug_type


class PlugHelpersTests(unittest.TestCase):
    def test_is_plug_type(self) -> None:
        self.assertTrue(is_plug_type("kasa"))
        self.assertTrue(is_plug_type("vesync"))
        self.assertFalse(is_plug_type("wiz"))
        self.assertFalse(is_plug_type(None))

    def test_color_to_power(self) -> None:
        self.assertEqual(color_to_power("off"), "off")
        self.assertEqual(color_to_power("magenta"), "on")
        self.assertEqual(color_to_power("warm"), "on")


if __name__ == "__main__":
    unittest.main()
