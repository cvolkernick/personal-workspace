#!/usr/bin/env python3
"""Tests for tools/capture_to_initiative.py (real module entry)."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
MOD_PATH = TOOLS / "capture_to_initiative.py"


def _load():
    spec = importlib.util.spec_from_file_location("capture_to_initiative", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class CaptureToInitiativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load()

    def test_slugify(self) -> None:
        self.assertEqual(self.mod.slugify("Hello World!"), "hello-world")

    def test_extract_title_and_next_action(self) -> None:
        body = "Ship weekly memo\nTODO: draft outline from last week notes\nMore detail"
        self.assertEqual(self.mod.extract_title(body), "Ship weekly memo")
        self.assertIn("draft outline", self.mod.extract_next_action(body).lower())

    def test_build_markdown_structure(self) -> None:
        md = self.mod.build_initiative_markdown(
            title="Test Init",
            body="Do the thing",
            next_action="Write first paragraph",
            created_at="2026-07-22",
        )
        self.assertIn("# Test Init", md)
        self.assertIn("## Description", md)
        self.assertIn("Do the thing", md)
        self.assertIn("## Current Next Action", md)
        self.assertIn("Write first paragraph", md)
        self.assertIn("capture_to_initiative.py", md)

    def test_write_initiative_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            r = self.mod.write_initiative(
                title="Wealth research ritual",
                body="Wealth research ritual\nNext: define 3 tickers to review",
                out_dir=out,
            )
            self.assertTrue(r["ok"])
            path = Path(r["absolute_path"])
            self.assertTrue(path.is_file())
            text = path.read_text(encoding="utf-8")
            self.assertIn("Wealth research ritual", text)
            self.assertIn("3 tickers", text)

    def test_cli_dry_run(self) -> None:
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.mod.main(["--dry-run", "--title", "CLI Test", "CLI Test\nNext: run it"])
        self.assertEqual(code, 0)
        self.assertIn("# CLI Test", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
