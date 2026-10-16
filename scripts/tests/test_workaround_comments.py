"""Groom sidecars do not import the writer module."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = ROOT / "scripts" / "workaround_comments.py"


def _load():
    spec = importlib.util.spec_from_file_location("workaround_comments", MOD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["workaround_comments"] = mod
    spec.loader.exec_module(mod)
    return mod


GATE = _load()


class WriterImportTests(unittest.TestCase):
    def test_flags_import_forms(self) -> None:
        text = (
            "import youtube_groom\n"
            "import youtube_groom_shorts\n"
            "from youtube_groom import foo\n"
            "from youtube_groom_supply import widen_writer\n"
            "import importlib\n"
            "importlib.import_module('youtube_groom')\n"
            "importlib.import_module('youtube_groom_quota')\n"
        )
        lines = [line for line, _kind in GATE.writer_imports(text)]
        self.assertEqual(lines, [1, 3, 6])

    def test_writer_submodule_is_the_writer(self) -> None:
        text = (
            "import youtube_groom.client\n"
            "from youtube_groom.client import x\n"
            "import youtube_groom_shorts\n"
        )
        lines = [line for line, _kind in GATE.writer_imports(text)]
        self.assertEqual(lines, [1, 2])

    def test_relative_import_of_the_writer(self) -> None:
        text = "from . import youtube_groom\n"
        self.assertEqual(GATE.writer_imports(text), [(1, "from . import youtube_groom")])

    def test_sidecars_do_not_import_the_writer(self) -> None:
        hits = GATE.sidecar_import_hits(ROOT)
        self.assertEqual(hits, [], "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
