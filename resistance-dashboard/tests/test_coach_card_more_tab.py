"""Coach card lives in a collapsed More section, not on Today."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def _section(html: str, sid: str) -> str:
    start = html.index(f'id="{sid}"')
    start = html.rindex("<section", 0, start)
    end = html.index("\n      </section>", start)
    return html[start:end]


def _today_sections(html: str) -> list[str]:
    out = []
    for m in re.finditer(r"<section\b[^>]*data-m-panel=\"today\"[^>]*>", html):
        end = html.find("\n      </section>", m.start())
        out.append(html[m.start():end])
    return out


class CoachCardMoreTab(unittest.TestCase):
    def test_today_has_no_coach_card(self):
        for sec in _today_sections(HTML):
            for sid in (
                "coach-phase-card",
                "coach-phase-pill",
                "coach-sat-fat-chip",
                "coach-staged-panel",
            ):
                self.assertNotIn(f'id="{sid}"', sec)

    def test_more_coach_section_collapsed(self):
        sec = _section(HTML, "more-coach-section")
        self.assertIn('data-m-panel="more"', sec)
        self.assertIn('data-collapse="more-coach"', sec)
        self.assertIn('aria-expanded="false"', sec)
        self.assertIn("is-collapsed", sec)
        self.assertRegex(sec, r'id="more-coach-body"[^>]*\bhidden\b')
        body = sec[sec.index('id="more-coach-body"'):]
        for sid in ("coach-phase-card", "coach-phase-pill", "coach-staged-panel"):
            self.assertIn(f'id="{sid}"', body)

    def test_coach_card_ids_unique(self):
        for sid in ("coach-phase-card", "coach-staged-panel"):
            self.assertEqual(HTML.count(f'id="{sid}"'), 1)

    def test_more_collapse_key_registered(self):
        self.assertIn('"more-coach"', JS)


if __name__ == "__main__":
    unittest.main()
