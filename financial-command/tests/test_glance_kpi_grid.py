"""Glance #kpi-grid drops the BP chip (#847)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "financial-command" / "index.html"

GLANCE_LABELS = ("USDC", "Card", "LTV", "NAV")
DEAD = ("rhBpTone", "tBp", "rhBp", "bpFloor")


def _first_style(html: str) -> str:
    m = re.search(r"<style>(.*?)</style>", html, re.S)
    if not m:
        raise AssertionError("no <style> block in index.html")
    return m.group(1)


def _rule(css: str, selector: str) -> str:
    """Return the first `{...}` body for selector (brace-matched)."""
    pat = re.compile(re.escape(selector) + r"\s*\{")
    m = pat.search(css)
    if not m:
        raise AssertionError(f"selector {selector!r} not found")
    start = m.end() - 1
    depth = 0
    for i, ch in enumerate(css[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return css[start : i + 1]
    raise AssertionError(f"selector {selector!r} unclosed")


def _decls(block: str) -> dict[str, str]:
    inner = block.strip()
    if inner.startswith("{"):
        inner = inner[1:]
    if inner.endswith("}"):
        inner = inner[:-1]
    out: dict[str, str] = {}
    for raw in inner.split(";"):
        line = re.sub(r"/\*.*?\*/", "", raw, flags=re.S).strip()
        if not line or ":" not in line:
            continue
        prop, _, val = line.partition(":")
        out[prop.strip()] = val.strip()
    return out


def _media_containing(css: str, query: str, needle: str) -> str:
    start = 0
    while True:
        i = css.find(query, start)
        if i < 0:
            raise AssertionError(f"{query!r} containing {needle!r} not found")
        block = _rule(css[i:], query)
        if needle in block:
            return block
        start = i + len(query)


class TestGlanceKpiGridNoBp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        cls.css = _first_style(cls.html)
        cls.media_820 = _media_containing(
            cls.css, "@media (max-width: 820px)", ".kpi-grid"
        )
        cls.media_560 = _media_containing(
            cls.css, "@media (max-width: 560px)", ".kpi-grid"
        )
        cls.media_720 = _media_containing(
            cls.css, "@media (max-width: 720px)", ".kpi-grid"
        )

    def test_glance_chip_labels_and_order(self):
        m = re.search(
            r'const kpis = \[(.*?)\];\s*document\.getElementById\("kpi-grid"\)',
            self.html,
            re.S,
        )
        self.assertIsNotNone(m, "Glance kpis array missing")
        labels = tuple(re.findall(r'\bk:\s*"([^"]+)"', m.group(1)))
        self.assertEqual(labels, GLANCE_LABELS)

    def test_no_bp_chip_or_empty_slot(self):
        m = re.search(
            r'const kpis = \[(.*?)\];\s*document\.getElementById\("kpi-grid"\)',
            self.html,
            re.S,
        )
        self.assertIsNotNone(m)
        self.assertNotIn('k: "BP"', m.group(1))
        self.assertEqual(len(re.findall(r"\bk:\s*\"", m.group(1))), 4)

    def test_dead_bp_chip_helpers_removed(self):
        for name in DEAD:
            self.assertIsNone(
                re.search(r"\b" + name + r"\b", self.html),
                f"dead identifier {name} still present",
            )

    def test_out_of_scope_bp_surfaces_remain(self):
        self.assertIn('reason: "BP healthy"', self.html)
        self.assertIn("rh_buying_power", self.html)
        self.assertIn("rh_bp_floor", self.html)

    def test_desktop_grid_is_four_col(self):
        grid = _decls(_rule(self.css, ".kpi-grid"))
        self.assertEqual(grid.get("grid-template-columns"), "repeat(4, minmax(0, 1fr))")

    def test_820_stays_four_col(self):
        grid = _decls(_rule(self.media_820, ".kpi-grid"))
        self.assertEqual(grid.get("grid-template-columns"), "repeat(4, minmax(0, 1fr))")

    def test_560_and_phone_are_two_by_two(self):
        grid_560 = _decls(_rule(self.media_560, ".kpi-grid"))
        self.assertEqual(
            grid_560.get("grid-template-columns"), "repeat(2, minmax(0, 1fr))"
        )
        cols = [
            _decls(block).get("grid-template-columns")
            for block in re.findall(
                r"\.kpi-grid\s*\{([^}]*)\}", self.media_720, re.S
            )
        ]
        self.assertTrue(
            any(c and "repeat(2" in c or c == "1fr 1fr" for c in cols),
            f"720px .kpi-grid not 2-col: {cols}",
        )


if __name__ == "__main__":
    unittest.main()
