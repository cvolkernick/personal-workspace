"""Manifest icons must match their declared pixel sizes."""

import json
import struct
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


class IconSizes(unittest.TestCase):
    def test_declared_sizes_match(self):
        m = json.loads((STATIC / "manifest.webmanifest").read_text())
        for icon in m["icons"]:
            w, h = (int(x) for x in icon["sizes"].split("x"))
            self.assertEqual(png_size(STATIC / icon["src"].lstrip("/")), (w, h), icon["src"])


if __name__ == "__main__":
    unittest.main()
