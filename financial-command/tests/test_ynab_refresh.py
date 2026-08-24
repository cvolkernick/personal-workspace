"""FCC Refresh must flag X Money soft-preserve — never bare ynab=ok."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FCC = ROOT / "financial-command"


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server_ynab_refresh", FCC / "server.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestFccYnabSoftPreserve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_fcc_server()

    def test_fcc_flags_x_money_soft_preserve(self):
        report = {
            "one_card": {
                "as_of": "2026-08-24T05:00:00+00:00",
                "token_source": "/home/prism-agent/.config/ynab/token",
            },
            "rh_checking": {
                "as_of": "2026-08-24T05:00:00+00:00",
                "token_source": "/home/prism-agent/.config/ynab/token",
            },
            "x_money": {
                "as_of": "2026-08-20T12:00:00+00:00",
                "token_source": "/Users/chris/.config/ynab/token",
                "live_error": "YNAB account Checking – 2201 is closed",
                "preserved": "YNAB account Checking – 2201 is closed",
            },
        }
        self.assertFalse(self.mod._ynab_refresh_ok(report))
        self.assertTrue(self.mod._ynab_soft_preserved(report, "x_money"))
        self.assertFalse(self.mod._ynab_soft_preserved(report, "one_card"))
        self.assertNotEqual(report, "ok")

    def test_bare_ok_string_is_not_used_for_object_report(self):
        clean = {
            "one_card": {"as_of": "2026-08-24T05:00:00+00:00", "token_source": "prism"},
            "rh_checking": {"as_of": "2026-08-24T05:00:00+00:00", "token_source": "prism"},
            "x_money": {"as_of": "2026-08-24T05:00:00+00:00", "token_source": "prism"},
        }
        self.assertTrue(self.mod._ynab_refresh_ok(clean))
        self.assertFalse(self.mod._ynab_soft_preserved(clean, "x_money"))

    def test_index_toast_warns_on_preserved_or_live_error(self):
        html = (FCC / "index.html").read_text(encoding="utf-8")
        self.assertIn("ynabSoft", html)
        self.assertIn("f.live_error || f.preserved", html)
        self.assertIn('"x_money"', html)
        # Must not treat a per-feed object as the legacy bare string.
        self.assertIn('r.ynab === "ok" || (ynabObj && !ynabSoft)', html)
