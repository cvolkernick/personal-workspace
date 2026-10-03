"""More tab sections collapse per device and start closed (#986)."""

from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")

# Top-level More cards, in page order. The form (or control) must stay in the body.
SECTIONS = (
    ("ask-card", "today-hub", "more-ask", "ask-form", "Ask Grok about your data"),
    ("targets-config-section", "hsa-section", "more-targets", "targets-form", "Daily Targets"),
    ("hsa-section", "labs-section", "more-hsa", "hsa-settings-form", "HSA"),
    ("labs-section", "training-settings-section", "more-labs", "labs-upload-form", "Labs"),
    (
        "training-settings-section",
        "suggestions-section",
        "more-training",
        "workout-goals-form",
        "Training settings",
    ),
    (
        "suggestions-section",
        "equipment-inventory-section",
        "more-suggestions",
        "suggestions-list",
        "Suggested additions",
    ),
    (
        "equipment-inventory-section",
        "exercise-catalog-section",
        "more-equipment",
        "equipment-form",
        "Equipment inventory",
    ),
    (
        "exercise-catalog-section",
        "log-card",
        "more-catalog",
        "library-add-form",
        "Exercise library",
    ),
    ("connections-card", "app-footer", "more-connections", "btn-google-auth", "Connections"),
)


def _section(html: str, start_id: str, end_id: str) -> str:
    a = html.find(f'id="{start_id}"')
    b = html.find(end_id if end_id.startswith("class=") or end_id.startswith("<") else f'id="{end_id}"')
    if end_id == "app-footer":
        b = html.find('class="app-footer"')
    assert a >= 0 and b > a, (start_id, end_id, a, b)
    return html[a:b]


class _Balance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if "id" in attr:
            self.ids.append(attr["id"])
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}:
            if not self.stack or self.stack[-1] != tag:
                self.errors.append((tag, self.stack[-5:]))
                return
            self.stack.pop()


class MoreTabCollapse(unittest.TestCase):
    def test_each_more_section_starts_collapsed(self):
        for start, end, key, control, title in SECTIONS:
            block = _section(HTML, start, end)
            head = block.find(f'data-collapse="{key}"')
            body = block.find(f'data-collapse-body="{key}"')
            control_at = block.find(f'id="{control}"')
            self.assertGreater(head, -1, key)
            self.assertLess(head, body, key)
            self.assertLess(body, control_at, key)
            self.assertIn('type="button"', block[head - 120 : head])
            self.assertIn("is-collapsed", block[head - 80 : head])
            self.assertIn('aria-expanded="false"', block[head : head + 80])
            self.assertNotIn('aria-expanded="true"', block[head - 40 : head + 80])
            self.assertIn(f'aria-controls="{key}-body"', block)
            self.assertIn(f'id="{key}-body"', block)
            self.assertIn("collapse-chevron", block[:body])
            self.assertIn(title, block[:body])
            open_at = block.find(f'<div class="collapsible-body" id="{key}-body"')
            open_tag = block[open_at : block.find(">", open_at) + 1]
            self.assertIn(" hidden", open_tag, key)
            self.assertNotIn('tabindex="-1"', block[:body])

    def test_markup_stays_balanced(self):
        parser = _Balance()
        parser.feed(HTML)
        self.assertEqual(parser.errors, [])
        self.assertEqual(parser.stack, [])
        for _, _, key, _, _ in SECTIONS:
            self.assertEqual(parser.ids.count(f"{key}-body"), 1, key)

    def test_state_is_local_and_defaults_closed(self):
        defaults = JS.split("const COLLAPSE_DEFAULTS = {", 1)[1].split("};", 1)[0]
        self.assertNotIn("more-ask", defaults)
        self.assertIn('const MORE_COLLAPSE_STORAGE_KEY = "fitdash-more-collapse-v1"', JS)
        self.assertIn("localStorage.getItem(MORE_COLLAPSE_STORAGE_KEY)", JS)
        reader = JS.split("function readMoreCollapse", 1)[1].split("function applyMoreSection", 1)[0]
        self.assertIn("open[key] = false", reader)
        self.assertNotIn("open[key] = true", reader)
        apply_more = JS.split("function applyMoreCollapseState", 1)[1].split(
            "function openMoreTarget", 1
        )[0]
        self.assertIn("moreCollapseOpen[key] === true", apply_more)
        self.assertIn("function readMoreCollapse", JS)
        self.assertIn("function commitMoreToggle", JS)
        handler = JS.split("function onCollapsibleHeadClick", 1)[1].split(
            "function applyStaticCollapseState", 1
        )[0]
        more_at = handler.find('key.indexOf("more-") === 0')
        session_at = handler.find("collapseOpen[key] = nextOpen")
        self.assertGreater(more_at, -1)
        self.assertLess(more_at, session_at)
        self.assertIn("persistMoreCollapse()", handler)
        apply = JS.split("function applyStaticCollapseState", 1)[1].split(
            "function bindCollapsibles", 1
        )[0]
        self.assertIn('key.indexOf("more-") === 0', apply)
        init = JS.split("function init()", 1)[1][:500]
        self.assertIn("bindCollapsibles(document)", init)

    def test_hide_keeps_the_body_and_the_tap_target(self):
        rule = CSS.split(".more-collapse-head {", 1)[1].split("}", 1)[0]
        self.assertIn("min-height: 44px", rule)
        self.assertIn(".collapsible-body[hidden]", CSS)
        self.assertIn("display: none !important", CSS)
        # Phone dock stays fixed. Collapsing a card must not restyle the tab bar.
        phone_at = CSS.find("FCC-style BOTTOM tabs")
        phone = CSS[phone_at : phone_at + 1200]
        self.assertGreater(phone_at, 0)
        self.assertIn("position: fixed", phone)
        self.assertIn("bottom: 0", phone)
        self.assertIn("body.m-shell .mobile-tabbar", phone)
        self.assertNotIn("more-collapse", phone)
        more_css = CSS.split("More tab (#955)", 1)[1].split("Re-assert quest header", 1)[0]
        self.assertNotIn("mobile-tabbar", more_css)

    def test_cache_bumped(self):
        self.assertIn("/app.js?v=recovery-1040-1", HTML)
        self.assertIn("/styles.css?v=brand-1027-1", HTML)
        self.assertIn("/app.js?v=recovery-1040-1", SW)
        self.assertIn("/styles.css?v=brand-1027-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v133"', SW)
        self.assertNotIn("/app.js?v=hydration-quest-994-1", HTML)
        self.assertNotIn("/app.js?v=hydration-quest-994-1", SW)
        self.assertNotIn("fitdash-shell-v123", SW)
        self.assertNotIn("/app.js?v=more-collapse-986-1", HTML)
        self.assertNotIn("/app.js?v=more-collapse-986-1", SW)
        self.assertNotIn("fitdash-shell-v122", SW)
        self.assertNotIn("/app.js?v=second-set-963-1", HTML)
        self.assertNotIn("/app.js?v=second-set-963-1", SW)
        self.assertNotIn("fitdash-shell-v121", SW)
        self.assertNotIn("/app.js?v=more-collapse-955-1", HTML)
        self.assertNotIn("fitdash-shell-v118", SW)
        self.assertNotIn("/app.js?v=empty-log-949-1", HTML)
        self.assertNotIn("/app.js?v=empty-log-949-1", SW)
        self.assertNotIn("fitdash-shell-v117", SW)


if __name__ == "__main__":
    unittest.main()
