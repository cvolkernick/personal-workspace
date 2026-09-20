"""Coach tab pay-plan list is visible on desktop, not zero-height (#844)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "financial-command" / "index.html"


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


class TestCoachPayPlanDesktopVisible(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        cls.css = _first_style(cls.html)
        media_at = cls.css.find("@media (max-width: 700px)")
        if media_at < 0:
            raise AssertionError("mobile 700px media query missing")
        cls.desktop_css = cls.css[:media_at]
        cls.mobile_css = _rule(cls.css[media_at:], "@media (max-width: 700px)")

    def test_markup_still_has_bills_list(self):
        self.assertIn('id="expenses-upcoming"', self.html)
        self.assertIn('class="coach-pay-plan"', self.html)
        self.assertIn('class="coach-pay-from"', self.html)
        self.assertIn('id="coach-pay-from-col"', self.html)
        self.assertNotIn('id="coach-pay-from"', self.html)
        self.assertIn('getElementById("expenses-upcoming")', self.html)
        self.assertNotIn('getElementById("coach-pay-from")', self.html)

    def test_desktop_pay_plan_is_not_zero_height(self):
        plan = _decls(_rule(self.desktop_css, ".coach-pay-plan"))
        self.assertNotEqual(plan.get("height"), "0")
        self.assertEqual(plan.get("height"), "auto")
        self.assertRegex(plan.get("max-height", ""), r"^\d+(\.\d+)?rem$")
        self.assertEqual(plan.get("overflow"), "hidden")
        self.assertNotEqual(plan.get("min-height"), "100%")

    def test_desktop_bill_list_has_internal_scroll(self):
        scroll = _decls(_rule(self.desktop_css, ".coach-pay-plan .bill-list-scroll"))
        self.assertEqual(scroll.get("overflow-y"), "auto")
        self.assertRegex(scroll.get("max-height", ""), r"^\d+(\.\d+)?rem$")

    def test_mobile_override_stays_bounded(self):
        plan = _decls(_rule(self.mobile_css, ".coach-pay-plan"))
        scroll = _decls(_rule(self.mobile_css, ".coach-pay-plan .bill-list-scroll"))
        self.assertEqual(plan.get("height"), "auto")
        self.assertEqual(plan.get("max-height"), "22rem")
        self.assertEqual(scroll.get("max-height"), "20rem")

    def test_no_zero_height_trick_on_desktop_or_mobile(self):
        for css in (self.desktop_css, self.mobile_css):
            plan = _decls(_rule(css, ".coach-pay-plan"))
            self.assertNotEqual(plan.get("height"), "0")
            self.assertNotEqual(plan.get("min-height"), "100%")


if __name__ == "__main__":
    unittest.main()
