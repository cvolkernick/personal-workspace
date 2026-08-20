"""Fleet payload assembly — roster units, no invented payoffs/bookings."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import dimo_client  # noqa: E402
import fleet  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROSTER = PKG / "data" / "roster.json"
NOTES = PKG / "data" / "notes.json"
EMPTY_INBOX = PKG / "data" / "turo_inbox.json"


class FleetAssemblyTests(unittest.TestCase):
    def tearDown(self) -> None:
        dimo_client._FETCH = None

    def _build(self, expenses: Path, inbox: Path | None = None):
        return fleet.build_fleet(
            roster_path=ROSTER,
            notes_path=NOTES,
            expenses_path=expenses,
            inbox_path=inbox or EMPTY_INBOX,
            dimo_env={},
            now="2026-08-17T16:00:00+00:00",
        )

    def test_units_from_roster(self) -> None:
        payload = self._build(FIXTURES / "expenses_no_fleet.json")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["unit_count"], 5)
        ids = [u["id"] for u in payload["units"]]
        self.assertEqual(
            ids,
            ["m3-2020", "r1s-2023", "m3-2022", "corolla-2022", "corolla-2024"],
        )
        by_id = {u["id"]: u for u in payload["units"]}
        self.assertEqual(by_id["m3-2020"]["identity"]["role"], "personal")
        self.assertEqual(by_id["m3-2022"]["identity"]["role"], "turo")
        self.assertEqual(
            by_id["m3-2022"]["identity"]["vin"], "5YJ3E1EA6NF289917"
        )
        self.assertIsNone(by_id["m3-2020"]["identity"]["vin"])
        r1s = by_id["r1s-2023"]
        self.assertEqual(r1s["identity"]["year"], 2023)
        self.assertEqual(r1s["identity"]["make"], "Rivian")
        self.assertEqual(r1s["identity"]["model"], "R1S")
        self.assertEqual(r1s["identity"]["role"], "personal")
        self.assertIsNone(r1s["identity"]["vin"])
        self.assertEqual(r1s["identity"]["lender"], "Vivek")
        self.assertEqual(by_id["m3-2020"]["identity"]["lender"], "Wells Fargo")
        self.assertNotIn("color", r1s["identity"])
        self.assertEqual(r1s["glance"]["title"], "2023 Rivian R1S")
        self.assertEqual(
            r1s["glance"]["photo"], "/static/fleet/rivian-r1s-2023.jpg"
        )
        self.assertEqual(
            by_id["m3-2020"]["glance"]["photo"],
            "/static/fleet/tesla-model-3-2020.jpg",
        )
        self.assertEqual(
            by_id["m3-2022"]["glance"]["photo"],
            "/static/fleet/tesla-model-3-2022.jpg",
        )
        self.assertEqual(
            by_id["corolla-2024"]["glance"]["photo"],
            "/static/fleet/toyota-corolla-2024.jpg",
        )
        self.assertEqual(
            by_id["corolla-2022"]["glance"]["photo"],
            "/static/fleet/toyota-corolla-2022.jpg",
        )

    def test_no_fleet_tab_is_stale_not_invented_payoff(self) -> None:
        payload = self._build(FIXTURES / "expenses_no_fleet.json")
        self.assertFalse(payload["sources"]["expenses"]["has_fleet_tab"])
        for unit in payload["units"]:
            fin = unit["finance"]
            self.assertTrue(fin["stale"])
            self.assertEqual(fin["sheet_lines"], [])
            self.assertNotIn("live_payoff", fin)
            self.assertIsNone(fin.get("live_payoff"))
            portal = fin.get("portal_override")
            if portal:
                self.assertTrue(portal["stale"])
                self.assertFalse(portal["live"])

    def test_fleet_tab_maps_lenders_and_dimo_lines(self) -> None:
        payload = self._build(FIXTURES / "expenses_with_fleet.json")
        self.assertTrue(payload["sources"]["expenses"]["has_fleet_tab"])
        by_id = {u["id"]: u for u in payload["units"]}
        gmf = by_id["m3-2022"]["finance"]
        self.assertFalse(gmf["stale"])
        self.assertEqual(gmf["source"], "expenses_sync.tabs.Fleet")
        names = {l["item"] for l in gmf["sheet_lines"]}
        self.assertIn("GM Financial (June / July / August)", names)
        self.assertNotIn("Fleet Insurance", names)

        c22 = by_id["corolla-2022"]["finance"]
        names22 = {l["item"] for l in c22["sheet_lines"]}
        self.assertIn("Capital One (June / July / August)", names22)
        self.assertIn("2022 Corolla DIMO", names22)
        self.assertAlmostEqual(c22["sheet_monthly"], 1130.55)

        c24 = by_id["corolla-2024"]["finance"]
        names24 = {l["item"] for l in c24["sheet_lines"]}
        self.assertIn("Santander (May / June / July)", names24)
        self.assertIn("2024 Corolla DIMO", names24)

        personal = by_id["m3-2020"]["finance"]
        self.assertEqual(personal["sheet_lines"], [])
        rivian = by_id["r1s-2023"]["finance"]
        self.assertEqual(rivian["sheet_lines"], [])

        shared_names = {l["item"] for l in payload["shared_finance"]["lines"]}
        self.assertIn("Fleet Insurance", shared_names)
        self.assertIn("Sud Stop Car Wash", shared_names)
        self.assertIn("Rivian R1S", shared_names)
        self.assertIn("Premium Connectivity", shared_names)

    def test_default_turo_and_dimo_are_empty_honest(self) -> None:
        payload = self._build(FIXTURES / "expenses_with_fleet.json")
        for unit in payload["units"]:
            self.assertEqual(unit["turo"]["bookings"], [])
            self.assertEqual(unit["dimo"]["status"], "unconfigured")
            self.assertIsNone(unit["dimo"]["odometer"])
        self.assertIn("empty", payload["sources"]["turo"]["inbox_status"].lower())

    def test_portal_override_is_not_live_payoff(self) -> None:
        payload = self._build(FIXTURES / "expenses_with_fleet.json")
        tesla = next(u for u in payload["units"] if u["id"] == "m3-2022")
        portal = tesla["finance"]["portal_override"]
        self.assertEqual(portal["principal_balance"], 21568.15)
        self.assertFalse(portal["principal_is_payoff_quote"])
        self.assertFalse(portal["live"])
        cap = next(u for u in payload["units"] if u["id"] == "corolla-2022")
        quote = cap["finance"]["portal_override"]["payoff_quote"]
        self.assertEqual(quote["amount"], 17996.47)
        self.assertFalse(cap["finance"]["portal_override"]["payoff_is_live"])

    def test_unit_cards_ignore_combined_monthly(self) -> None:
        """Fixture summary.combined_monthly is 8427 — must not leak onto cards."""
        payload = self._build(FIXTURES / "expenses_with_fleet.json")
        self.assertFalse(payload["sources"]["expenses"]["uses_combined_monthly"])
        self.assertNotIn("combined_monthly", payload)
        self.assertNotIn("combined_monthly", payload["shared_finance"])
        blob = json.dumps(payload["units"])
        self.assertNotIn("combined_monthly", blob)
        tesla = next(u for u in payload["units"] if u["id"] == "m3-2022")
        self.assertAlmostEqual(tesla["finance"]["sheet_monthly"], 1321.66)
        self.assertNotEqual(tesla["finance"]["sheet_monthly"], 8427.0)
        self.assertFalse(tesla["finance"]["stale"])

    def test_resolve_prefers_worktree_fleet_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            local = root / "local" / "expenses_latest.json"
            local.parent.mkdir()
            local.write_text(
                (FIXTURES / "expenses_no_fleet.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            wt = (
                root
                / "wt"
                / "treasury"
                / "treasury"
                / "snapshots"
                / "expenses_latest.json"
            )
            wt.parent.mkdir(parents=True)
            wt.write_text(
                (FIXTURES / "expenses_with_fleet.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            found = fleet.resolve_expenses_path(
                None,
                local_default=local,
                worktree_base=root / "wt",
                env={},
            )
            self.assertEqual(found, wt)
            payload = fleet.build_fleet(
                roster_path=ROSTER,
                notes_path=NOTES,
                expenses_path=found,
                inbox_path=EMPTY_INBOX,
                dimo_env={},
            )
            self.assertTrue(payload["sources"]["expenses"]["has_fleet_tab"])
            tesla = next(u for u in payload["units"] if u["id"] == "m3-2022")
            self.assertFalse(tesla["finance"]["stale"])
            self.assertEqual(tesla["finance"]["source"], "expenses_sync.tabs.Fleet")


if __name__ == "__main__":
    unittest.main()
