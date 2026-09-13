"""#469 per-car finance block — Helm SoT 2026-09-03 seed + no phones."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import agent_fleet  # noqa: E402
import car_cards  # noqa: E402
import fleet  # noqa: E402
import glance  # noqa: E402
import server  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROSTER = PKG / "data" / "roster.json"
NOTES = PKG / "data" / "notes.json"
EMPTY_INBOX = PKG / "data" / "turo_inbox.json"
NOW = "2026-09-03T16:00:00+00:00"

# North-American phone / tel: — finance seed and Money strip must stay clean.
PHONE_RE = re.compile(
    r"(?:tel:|\+1[\s.-]?\d{3}|(?<!\d)\d{3}[-.\s]\d{3}[-.\s]\d{4}(?!\d))",
    re.I,
)
FULL_ACCOUNTS = ("111088614673", "6201049298207", "28312877")


def _build() -> dict:
    return fleet.build_fleet(
        roster_path=ROSTER,
        notes_path=NOTES,
        expenses_path=FIXTURES / "expenses_no_fleet.json",
        inbox_path=EMPTY_INBOX,
        dimo_env={},
        now=NOW,
    )


class HelmSotSeedTests(unittest.TestCase):
    def test_notes_are_helm_sot_2026_09_03(self) -> None:
        data = json.loads(NOTES.read_text(encoding="utf-8"))
        self.assertEqual(data["as_of"], "2026-09-03")
        self.assertEqual(data["source"], "helm_sot_2026-09-03")
        self.assertFalse(data.get("live"))
        self.assertEqual(
            set(data["units"]),
            {"corolla-2022", "corolla-2024", "m3-2022", "m3-2020", "r1s-2023"},
        )
        blob = NOTES.read_text(encoding="utf-8")
        self.assertIsNone(PHONE_RE.search(blob), blob)
        for acct in FULL_ACCOUNTS:
            self.assertNotIn(acct, blob)

    def test_seed_loan_fields_on_each_car(self) -> None:
        payload = _build()
        by_id = {u["id"]: u["finance"]["loan"] for u in payload["units"]}

        c22 = by_id["corolla-2022"]
        self.assertEqual(c22["lender"], "Capital One")
        self.assertEqual(c22["account_last4"], "8207")
        self.assertEqual(c22["amount_due_now"], 962.98)
        self.assertTrue(c22["past_due"])
        self.assertEqual(c22["past_due_days"], 74)
        self.assertEqual(c22["monthly_payment"], 373.85)
        self.assertEqual(c22["payoff"], 17974.92)
        self.assertEqual(c22["apr_pct"], 11.14)
        self.assertFalse(c22["live"])
        self.assertFalse(c22["payoff_is_live"])
        self.assertNotIn("arrangement", c22)
        self.assertNotIn("next_due_date", c22)
        self.assertNotIn("past_due_amount", c22)

        c24 = by_id["corolla-2024"]
        self.assertEqual(c24["lender"], "Santander")
        self.assertEqual(c24["account_last4"], "2877")
        self.assertEqual(c24["past_due_amount"], 788.99)
        self.assertEqual(c24["arrangement"]["amount"], 307.15)
        self.assertEqual(c24["arrangement"]["due"], "2026-09-14")
        self.assertEqual(c24["next_due_date"], "2026-09-21")
        self.assertEqual(c24["next_scheduled_amount"], 307.34)
        self.assertEqual(c24["payoff"], 14158.38)
        self.assertEqual(c24["apr_pct"], 10.18)
        self.assertNotIn("amount_due_now", c24)
        self.assertNotIn("past_due_days", c24)

        tesla22 = by_id["m3-2022"]
        self.assertEqual(tesla22["lender"], "GM Financial")
        self.assertEqual(tesla22["past_due_amount"], 1326.37)
        self.assertEqual(tesla22["past_due_days"], 41)
        self.assertEqual(tesla22["next_due_date"], "2026-09-23")
        self.assertEqual(tesla22["next_scheduled_amount"], 501.08)
        self.assertNotIn("apr_pct", tesla22)
        self.assertNotIn("account_last4", tesla22)
        self.assertNotIn("payoff", tesla22)
        self.assertNotIn("monthly_payment", tesla22)

        wells = by_id["m3-2020"]
        self.assertEqual(wells["lender"], "Wells Fargo")
        self.assertEqual(wells["apr_pct"], 5.65)
        self.assertEqual(wells["paid_by"], "Mike")
        self.assertTrue(wells["off_fcc"])
        self.assertFalse(wells["show_balances"])
        self.assertNotIn("amount_due_now", wells)
        self.assertNotIn("past_due_amount", wells)
        self.assertNotIn("payoff", wells)
        self.assertNotIn("account_last4", wells)

        rivian = by_id["r1s-2023"]
        self.assertEqual(rivian["lender"], "Vivek")
        self.assertEqual(rivian["apr_pct"], 0)
        self.assertEqual(rivian["monthly_payment"], 1350)
        self.assertEqual(rivian["payment_method"], "NFCU Zelle")
        self.assertTrue(rivian["no_portal"])
        self.assertFalse(rivian["show_balances"])
        self.assertNotIn("payoff", rivian)
        self.assertNotIn("past_due_amount", rivian)
        self.assertNotIn("arrangement", rivian)

    def test_seed_renders_on_existing_money_strip(self) -> None:
        payload = _build()
        by_id = {u["id"]: u for u in payload["units"]}
        rendered = {
            uid: glance.money_strip_inner_html(u["finance"])
            for uid, u in by_id.items()
        }
        html = glance.render_unit_card_html(by_id["corolla-2022"], now=NOW)
        self.assertIn("<h3>Money</h3>", html)
        self.assertNotIn("<h3>Finance", html)
        self.assertIn('class="finance-block"', html)
        self.assertIn("<h3>Vehicle</h3>", html)
        self.assertIn("<h3>Schedule</h3>", html)

        c22 = rendered["corolla-2022"]
        self.assertIn("Capital One", c22)
        self.assertIn("…8207", c22)
        self.assertIn("Due now $962.98", c22)
        self.assertIn("Past due 74 days", c22)
        self.assertIn("$373.85/mo", c22)
        self.assertIn("Payoff $17,974.92 — not live", c22)
        self.assertIn("11.14% APR", c22)

        c24 = rendered["corolla-2024"]
        self.assertIn("Santander", c24)
        self.assertIn("…2877", c24)
        self.assertIn("Past due $788.99", c24)
        self.assertIn("Arrangement $307.15 by 2026-09-14", c24)
        self.assertIn("Next $307.34 on 2026-09-21", c24)
        self.assertIn("Payoff $14,158.38 — not live", c24)
        self.assertIn("10.18% APR", c24)
        self.assertNotIn("28312877", c24)

        tesla22 = rendered["m3-2022"]
        self.assertIn("GM Financial", tesla22)
        self.assertIn("Past due $1,326.37 · 41 days", tesla22)
        self.assertIn("Next $501.08 on 2026-09-23", tesla22)
        self.assertNotIn("18.15", tesla22)
        self.assertNotIn("APR", tesla22)

        wells = rendered["m3-2020"]
        self.assertIn("Wells Fargo", wells)
        self.assertIn("5.65% APR", wells)
        self.assertIn("Mike pays", wells)
        self.assertIn("off FCC", wells)
        self.assertNotIn("Due now", wells)
        self.assertNotIn("Past due", wells)
        self.assertNotIn("Payoff", wells)

        rivian = rendered["r1s-2023"]
        self.assertIn("Vivek", rivian)
        self.assertIn("$1,350.00/mo", rivian)
        self.assertIn("0% APR", rivian)
        self.assertIn("NFCU Zelle", rivian)
        self.assertIn("No portal", rivian)
        self.assertNotIn("Due now", rivian)
        self.assertNotIn("Past due", rivian)

        for uid, block in rendered.items():
            self.assertIsNone(PHONE_RE.search(block), (uid, block))
            for acct in FULL_ACCOUNTS:
                self.assertNotIn(acct, block)

        self.assertTrue(by_id["corolla-2022"]["glance"]["due"])
        self.assertTrue(by_id["corolla-2024"]["glance"]["due"])
        self.assertTrue(by_id["m3-2022"]["glance"]["due"])
        self.assertFalse(by_id["m3-2020"]["glance"]["due"])
        self.assertFalse(by_id["r1s-2023"]["glance"]["due"])

    def test_index_keeps_money_strip_no_new_nav(self) -> None:
        html = (PKG / "index.html").read_text(encoding="utf-8")
        self.assertIn("function moneyStrip", html)
        self.assertIn("function loanOf", html)
        self.assertIn("class=\"finance-block\"", html)
        self.assertIn("<h3>Money</h3>", html)
        self.assertNotIn("<h3>Finance", html)
        self.assertIn("Due now", html)
        self.assertIn("Arrangement", html)
        nav = html[html.find("<header") : html.find("</header>")]
        self.assertIn('id="nav-fcc"', html)
        self.assertIn('id="nav-turo"', html)
        self.assertNotIn("finance-inbox", html.lower())
        self.assertNotIn("id=\"nav-finance\"", html)
        self.assertNotIn("Finance inbox", nav)

    def test_no_phones_in_finance_ui_or_seed_files(self) -> None:
        paths = [
            NOTES,
            PKG / "car_cards.py",
            PKG / "index.html",
        ]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            if path.name == "index.html":
                money_fn = text[text.find("function moneyStrip") : text.find("function photoHref")]
                self.assertIsNone(PHONE_RE.search(money_fn), money_fn)
                self.assertNotIn("tel:", money_fn)
                continue
            if path.name == "car_cards.py":
                helper = text[text.find("def loan_display_for") : text.find("def _as_et")]
                self.assertIsNone(PHONE_RE.search(helper), helper)
                continue
            self.assertIsNone(PHONE_RE.search(text), path)

    def test_mask_account_last4(self) -> None:
        self.assertEqual(car_cards.mask_account_last4("6201049298207"), "8207")
        self.assertEqual(car_cards.mask_account_last4("28312877"), "2877")
        self.assertEqual(car_cards.mask_account_last4("…8207"), "8207")
        self.assertIsNone(car_cards.mask_account_last4(""))
        self.assertIsNone(car_cards.mask_account_last4("12"))


class AgentFleetStaysDumpOnlyTests(unittest.TestCase):
    def test_agent_fleet_has_no_finance_and_no_writer(self) -> None:
        packet = agent_fleet.export_agent_fleet(inbox_path=EMPTY_INBOX)
        self.assertTrue(packet["read_only"])
        blob = json.dumps(packet)
        self.assertNotIn("finance", blob)
        self.assertNotIn("loan", blob)
        self.assertNotIn("962.98", blob)
        self.assertNotIn("17974.92", blob)
        for acct in FULL_ACCOUNTS:
            self.assertNotIn(acct, blob)
        src = (PKG / "server.py").read_text(encoding="utf-8")
        self.assertIn('if path == "/api/agent/fleet":', src)
        post = src[src.find("def do_POST") : src.find("def main")]
        self.assertNotIn('path == "/api/agent/fleet"', post)
        self.assertIn("/api/turo-tasks/complete", post)
        self.assertIn('self._json(404, {"ok": False, "error": "not found"})', post)
        vercel = (PKG / "api" / "agent" / "fleet.py").read_text(encoding="utf-8")
        self.assertIn("def do_GET", vercel)
        self.assertNotIn("def do_POST", vercel)
        self.assertIn("No write surfaces", vercel)

    def test_server_post_only_completes_tasks(self) -> None:
        src = Path(server.__file__).read_text(encoding="utf-8")
        self.assertIn('if path != "/api/turo-tasks/complete":', src)


if __name__ == "__main__":
    unittest.main()
