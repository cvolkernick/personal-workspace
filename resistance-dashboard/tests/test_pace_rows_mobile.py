"""#650 Today so far pace rows stack label above values on mobile."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def _media_blocks(css: str, query: str) -> list[str]:
    blocks = []
    idx = 0
    needle = f"@media {query}"
    while True:
        m = css.find(needle, idx)
        if m < 0:
            break
        start = css.find("{", m)
        if start < 0:
            break
        depth = 0
        end = start
        for i, ch in enumerate(css[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        blocks.append(css[m : end + 1])
        idx = end + 1
    return blocks


def _first_rule(css: str, selector: str) -> str:
    i = css.find(selector)
    if i < 0:
        raise AssertionError(f"missing {selector}")
    start = css.find("{", i)
    end = css.find("}", start)
    if start < 0 or end < 0:
        raise AssertionError(f"unclosed {selector}")
    return css[start + 1 : end]


class PaceRowsMobileStack(unittest.TestCase):
    def test_desktop_meta_stays_side_by_side(self):
        rule = _first_rule(CSS, ".macro-progress-meta")
        self.assertIn("display: flex", rule)
        self.assertIn("justify-content: space-between", rule)
        self.assertIn("font-size: 0.8rem", rule)
        self.assertNotIn("flex-direction: column", rule)

    def test_mobile_520_stacks_label_above_values(self):
        hits = [
            b
            for b in _media_blocks(CSS, "(max-width: 520px)")
            if ".macro-progress-meta" in b
        ]
        self.assertEqual(len(hits), 1, "exactly one 520px rule for pace-row meta")
        block = hits[0]
        meta = _first_rule(block, ".macro-progress-meta")
        self.assertIn("flex-direction: column", meta)
        self.assertIn("align-items: flex-start", meta)
        self.assertIn("font-size: 0.72rem", meta)


class PaceRowsCache(unittest.TestCase):
    def test_cache_bumped(self):
        self.assertIn("/styles.css?v=pace-rows-650-1", HTML)
        self.assertIn("/styles.css?v=pace-rows-650-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v107"', SW)
        self.assertNotIn("/styles.css?v=kitchen-collapse-1", HTML)
        self.assertNotIn("/styles.css?v=kitchen-collapse-1", SW)
        self.assertNotIn("fitdash-shell-v106", SW)
        self.assertNotIn("fitdash-shell-v105", SW)


if __name__ == "__main__":
    unittest.main()
