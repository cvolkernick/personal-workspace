"""Coach Pay-from · buffers rows stay single-line (#846)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "financial-command" / "index.html"

KPI_LABELS = (
    "Upcoming / mo",
    "Essential / mo",
    "Fleet / mo",
    "CB pay-from / mo",
    "RH Checking / mo",
    "X Money / mo",
    "Collateral / mo",
    "Productive / mo",
    "Consumer / mo",
)

ROW_SEL = "#expenses-by-source .metric,\n    #expense-kpis .metric"
VAL_SEL = "#expenses-by-source .metric .val,\n    #expense-kpis .metric .val"
ROW_SEL_720 = "#expenses-by-source .metric,\n      #expense-kpis .metric"
VAL_SEL_720 = "#expenses-by-source .metric .val,\n      #expense-kpis .metric .val"


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


class TestCoachPayFromCompact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        cls.css = _first_style(cls.html)
        cls.media_720 = _media_containing(
            cls.css, "@media (max-width: 720px)", "#expenses-by-source .metric"
        )
        cls.media_400 = _media_containing(
            cls.css, "@media (max-width: 400px)", "#expense-kpis.grid-2"
        )
        media_700 = cls.css.find("@media (max-width: 700px)")
        if media_700 < 0:
            raise AssertionError("mobile 700px media query missing")
        cls.desktop_css = cls.css[:media_700]

    def test_markup_keeps_bar_rows_and_kpis(self):
        self.assertIn('id="expenses-pay-from-bar"', self.html)
        self.assertIn('class="pay-from-bar"', self.html)
        self.assertIn('id="expenses-by-source"', self.html)
        self.assertIn('id="expense-kpis"', self.html)
        self.assertIn('id="coach-pay-from-col"', self.html)
        self.assertIn("Pay-from · buffers", self.html)

    def test_kpi_labels_and_order_unchanged(self):
        m = re.search(
            r'getElementById\("expense-kpis"\)\.innerHTML = \[(.*?)\].join\(""\)',
            self.html,
            re.S,
        )
        self.assertIsNotNone(m, "expense-kpis render block missing")
        labels = re.findall(r'metric\(\s*"([^"]+)"', m.group(1))
        self.assertEqual(tuple(labels), KPI_LABELS)

    def test_buffer_rows_keep_dot_label_value(self):
        self.assertIn('class="metric src-metric"', self.html)
        self.assertIn('<i class="dot ${sc}"></i>', self.html)
        self.assertIn('<span class="val">${money(v)}</span>', self.html)

    def test_desktop_rows_are_single_line(self):
        row = _decls(_rule(self.css, ROW_SEL))
        val = _decls(_rule(self.css, VAL_SEL))
        self.assertEqual(row.get("flex-wrap"), "nowrap")
        self.assertEqual(row.get("white-space"), "nowrap")
        self.assertEqual(val.get("width"), "auto")
        self.assertEqual(val.get("text-align"), "right")
        self.assertEqual(val.get("font-variant-numeric"), "tabular-nums")
        self.assertEqual(val.get("flex"), "0 0 auto")
        pad = row.get("padding", "")
        self.assertRegex(pad, r"^0\.\d+rem 0$")
        top = float(pad.split()[0].replace("rem", ""))
        self.assertLessEqual(top, 0.2)

    def test_desktop_kpi_grid_is_two_col(self):
        grid = _decls(_rule(self.css, "#expense-kpis.grid-2"))
        self.assertIn("repeat(2", grid.get("grid-template-columns", ""))

    def test_720_overrides_global_metric_wrap(self):
        wrapped = _decls(_rule(self.media_720, ".metric"))
        wrapped_val = _decls(_rule(self.media_720, ".metric .val"))
        self.assertEqual(wrapped.get("flex-wrap"), "wrap")
        self.assertEqual(wrapped_val.get("width"), "100%")
        row = _decls(_rule(self.media_720, ROW_SEL_720))
        val = _decls(_rule(self.media_720, VAL_SEL_720))
        self.assertEqual(row.get("flex-wrap"), "nowrap")
        self.assertEqual(val.get("width"), "auto")
        self.assertEqual(val.get("text-align"), "right")
        grid = _decls(_rule(self.media_720, "#expense-kpis.grid-2"))
        self.assertIn("repeat(2", grid.get("grid-template-columns", ""))

    def test_400_kpi_grid_stacks_one_col(self):
        grid = _decls(_rule(self.media_400, "#expense-kpis.grid-2"))
        self.assertEqual(grid.get("grid-template-columns"), "1fr")

    def test_does_not_reintroduce_pay_plan_zero_height(self):
        plan = _decls(_rule(self.desktop_css, ".coach-pay-plan"))
        self.assertNotEqual(plan.get("height"), "0")
        self.assertEqual(plan.get("height"), "auto")
        self.assertNotEqual(plan.get("min-height"), "100%")
        mobile_plan = _decls(
            _rule(
                _media_containing(self.css, "@media (max-width: 700px)", ".coach-pay-plan"),
                ".coach-pay-plan",
            )
        )
        self.assertNotEqual(mobile_plan.get("height"), "0")
        self.assertNotEqual(mobile_plan.get("min-height"), "100%")


if __name__ == "__main__":
    unittest.main()
