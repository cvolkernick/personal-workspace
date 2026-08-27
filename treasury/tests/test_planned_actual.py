"""Planned vs YNAB-actual flag strip — display-only AC (Naka PO)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.financial_coach import build_coach_plan  # noqa: E402
from treasury.interest_spectrum import build_interest_spectrum  # noqa: E402
from treasury.planned_actual import (  # noqa: E402
    COINBASE_USDC_LABEL,
    FLAG_CADENCE_LUMP,
    FLAG_NOT_YET,
    FLAG_OFF_BOOK,
    FLAG_ON,
    FLAG_PAYMENT_SHAPED,
    FLAG_TWO_CHARGE,
    FLAGS,
    build_planned_actual_strip,
    discover_planned_tabs,
    is_skipped_tx,
    names_join,
)
from treasury.ynab_category_map import MAP_PATH, load_category_map, validate_category_map  # noqa: E402

# Known fixtures (tests only — not hard-coded in UI).
THAIS_ID = "5cef00e5"
STUDENT_LOAN_ID = "ec45e5f7"
LEE_COUNTY_ID = "69f30960"
FILTEREASY_TX = "b9af9ed4"
YNAB_SUB_ID = "bf7c5065-838f-43bf-b227-419b97298a1c"
FLEET_INS_ID = "fleet-ins-cat"
FILTER_SUB_ID = "subs-cat"
ELECTRIC_ID = "electric-cat"
WATER_ID = "water-cat"
RENT_ID = "rent-cat"
CC_PAY_ID = "cc-pay-cat"

AS_OF = "2026-08-15"


def _cat(cid: str, name: str, *, group: str = "Bills", enabled: bool = True, **extra) -> dict:
    row = {
        "id": cid,
        "name": name,
        "group_id": "bills",
        "group_name": group,
        "hidden": False,
        "enabled": enabled,
    }
    row.update(extra)
    return row


def _map(*cats: dict) -> dict:
    return validate_category_map(
        {
            "schema_version": 0,
            "budget_id": "37502ae1-2484-4e3d-90a1-8985d775e86b",
            "budget_name": "Chris's Plan",
            "allow_approve": True,
            "allow_categorize": True,
            "forbid": ["move_money", "payment", "transfer"],
            "categories": list(cats),
            "payee_rules": [],
        }
    )


def _item(name: str, monthly: float, frm: str | None, *, tab: str = "Essential") -> dict:
    return {"item": name, "monthly": monthly, "from": frm, "tab": tab, "date": "8/1/2026"}


def _tx(
    *,
    payee: str,
    amount: float,
    category_id: str | None = None,
    category_name: str | None = None,
    date: str = "2026-08-10",
    tx_id: str | None = None,
    transfer_account_id: str | None = None,
    transfer_transaction_id: str | None = None,
) -> dict:
    return {
        "id": tx_id or f"tx-{payee}-{amount}",
        "date": date,
        "payee": payee,
        "amount": amount,
        "amount_display": amount,
        "category_id": category_id,
        "category_name": category_name,
        "transfer_account_id": transfer_account_id,
        "transfer_transaction_id": transfer_transaction_id,
    }


def _snaps(
    items: list[dict],
    txs: list[dict],
    *,
    extra_tabs: dict | None = None,
) -> dict:
    tabs = {"Essential": {"items": items, "role": "upcoming_expense_estimates"}}
    if extra_tabs:
        tabs.update(extra_tabs)
    return {
        "expenses": {
            "sheet_id": "15ZU7843pTSLSEI0U-taFZ4Qwk3bTQx6cWh2Ex0d7NJQ",
            "sheet_name": "Personal Expense Sheet",
            "tabs": tabs,
        },
        "x_money": {"transactions": txs, "source": "ynab"},
        "one_card": {"transactions": [], "source": "ynab"},
        "rh_checking": {"transactions": [], "source": "ynab"},
        "coinbase_usdc_sends": {"source": "fixture", "transactions": []},
    }


def _ac_map() -> dict:
    return _map(
        _cat(THAIS_ID, "Thaís", group="Bills"),
        _cat(STUDENT_LOAN_ID, "Student Loan", group="Bills"),
        _cat(LEE_COUNTY_ID, "Lee County Citation", group="Bills"),
        _cat(ELECTRIC_ID, "Electric", group="Bills"),
        _cat(WATER_ID, "Water", group="Bills"),
        _cat(RENT_ID, "Rent", group="Bills"),
        _cat(YNAB_SUB_ID, "🌳 YNAB subscription", group="Wants"),
        _cat(FLEET_INS_ID, "Fleet Insurance", group="Bills"),
        _cat(FILTER_SUB_ID, "Subscriptions", group="Bills", sheet_item="FilterEasy"),
        _cat(CC_PAY_ID, "Coinbase One Card – 5361", group="Credit Card Payments", enabled=False),
        _cat("rta", "Inflow: Ready to Assign", group="Internal Master Category", enabled=False),
        _cat("uncat", "Uncategorized", group="Internal Master Category", enabled=False),
    )


def _ac_items() -> list[dict]:
    return [
        _item("Rent", 2090.0, "Coinbase"),
        _item("Thaís", 900.0, "Coinbase"),
        _item("Student Loan", 83.33, "X Money"),
        _item("Lee County Citation", 161.0, "X Money"),
        _item("Electric", 426.0, "X Money"),
        _item("Water", 67.0, "X Money"),
        _item("YNAB", 14.99, "X Money"),
        _item("Fleet Insurance", 633.20, "X Money", tab="Fleet"),
        _item("FilterEasy", 10.65, "X Money"),
        _item("Coinbase One Card", 24.0, "Coinbase"),
        _item("Robinhood Gold", 4.17, "X Money"),
    ]


def _leftover_pile(n: int = 87) -> list[dict]:
    pile = []
    for i in range(n):
        kind = i % 5
        if kind == 0:
            pile.append(
                _tx(
                    payee="Rent",
                    amount=-100.0,
                    category_name="Uncategorized",
                    date="2026-08-05",
                    tx_id=f"leftover-rent-{i}",
                )
            )
        elif kind == 1:
            pile.append(
                _tx(
                    payee="Thaís",
                    amount=-50.0,
                    category_name="Inflow: Ready to Assign",
                    date="2026-08-06",
                    tx_id=f"leftover-thais-{i}",
                )
            )
        elif kind == 2:
            pile.append(
                _tx(
                    payee="Transfer : Coinbase One Card – 5361",
                    amount=-25.0,
                    category_name="Uncategorized",
                    date="2026-08-07",
                    tx_id=f"leftover-xfer-{i}",
                    transfer_account_id="acct-cc",
                )
            )
        elif kind == 3:
            pile.append(
                _tx(
                    payee="Coinbase One Card Payment",
                    amount=-80.0,
                    category_id=CC_PAY_ID,
                    category_name="Coinbase One Card – 5361",
                    date="2026-08-08",
                    tx_id=f"leftover-pay-{i}",
                )
            )
        else:
            pile.append(
                _tx(
                    payee="FilterEasy",
                    amount=-10.65,
                    category_name="Uncategorized",
                    date="2026-08-09",
                    tx_id=f"leftover-filter-{i}",
                )
            )
    return pile


def _row(strip: dict, name: str) -> dict:
    for r in strip.get("rows") or []:
        if r.get("item") == name:
            return r
    raise AssertionError(f"missing row {name!r} in {[r.get('item') for r in strip.get('rows') or []]}")


class TestFlagEnumAndJoin(unittest.TestCase):
    def test_fleet_insurance_and_ynab_subscription_two_charge_not_over(self) -> None:
        txs = [
            _tx(payee="Fleet Insurance", amount=-400.0, category_id=FLEET_INS_ID, category_name="Fleet Insurance"),
            _tx(
                payee="Fleet Insurance",
                amount=-350.00,
                category_id=FLEET_INS_ID,
                category_name="Fleet Insurance",
                date="2026-08-12",
            ),
            _tx(payee="YNAB", amount=-14.99, category_id=YNAB_SUB_ID, category_name="🌳 YNAB subscription"),
            _tx(
                payee="YNAB",
                amount=-5.00,
                category_id=YNAB_SUB_ID,
                category_name="🌳 YNAB subscription",
                date="2026-08-20",
            ),
        ]
        strip = build_planned_actual_strip(_snaps(_ac_items(), txs), _ac_map(), as_of=AS_OF)
        fleet = _row(strip, "Fleet Insurance")
        ynab = _row(strip, "YNAB")
        self.assertEqual(fleet["flag"], FLAG_TWO_CHARGE)
        self.assertEqual(ynab["flag"], FLAG_TWO_CHARGE)
        self.assertAlmostEqual(fleet["planned"], 633.20)
        self.assertAlmostEqual(ynab["planned"], 14.99)
        self.assertGreater(fleet["actual"], fleet["planned"])
        blob = (str(fleet) + str(ynab)).lower()
        self.assertNotIn("overspend", blob)
        self.assertNotEqual(fleet["flag"], "over")
        self.assertNotEqual(ynab["flag"], "over")
        self.assertIn("not lifestyle over", str(strip.get("notes") or "").lower())

    def test_filtereasy_cadence_lump_two_times_unit(self) -> None:
        txs = [
            _tx(
                payee="FilterEasy",
                amount=-10.65,
                category_id=FILTER_SUB_ID,
                category_name="Subscriptions",
                tx_id=FILTEREASY_TX,
            ),
            _tx(
                payee="FilterEasy",
                amount=-10.65,
                category_id=FILTER_SUB_ID,
                category_name="Subscriptions",
                date="2026-08-22",
                tx_id="filter-2",
            ),
        ]
        strip = build_planned_actual_strip(_snaps(_ac_items(), txs), _ac_map(), as_of=AS_OF)
        row = _row(strip, "FilterEasy")
        self.assertEqual(row["flag"], FLAG_CADENCE_LUMP)
        self.assertAlmostEqual(row["planned"], 10.65)
        self.assertAlmostEqual(row["actual"], 21.30)
        self.assertEqual(row["category_id"], FILTER_SUB_ID)
        self.assertNotEqual(row["flag"], "over")
        self.assertNotIn("overspend", str(row).lower())

    def test_rent_and_thais_off_book_from_coinbase_not_under(self) -> None:
        leftover = _leftover_pile(87)
        strip = build_planned_actual_strip(_snaps(_ac_items(), leftover), _ac_map(), as_of=AS_OF)
        rent = _row(strip, "Rent")
        thais = _row(strip, "Thaís")
        self.assertEqual(rent["flag"], FLAG_OFF_BOOK)
        self.assertEqual(thais["flag"], FLAG_OFF_BOOK)
        self.assertAlmostEqual(rent["planned"], 2090.0)
        self.assertAlmostEqual(thais["planned"], 900.0)
        self.assertEqual(rent["actual"], 0.0)
        self.assertEqual(thais["actual"], 0.0)
        self.assertEqual(rent["from_venue"], COINBASE_USDC_LABEL)
        self.assertEqual(thais["from_venue"], COINBASE_USDC_LABEL)
        self.assertEqual(rent["from"], COINBASE_USDC_LABEL)
        self.assertEqual(thais["from"], COINBASE_USDC_LABEL)
        self.assertEqual(rent["category_id"], RENT_ID)
        self.assertEqual(thais["category_id"], THAIS_ID)
        self.assertNotEqual(rent["flag"], "under")
        self.assertNotEqual(thais["flag"], "under")

    def test_rent_parenthetical_joins_rent_category(self) -> None:
        items = [_item("Rent (April / May / June / July / August)", 8400.0, "Coinbase")]
        strip = build_planned_actual_strip(_snaps(items, []), _ac_map(), as_of=AS_OF)
        rent = _row(strip, "Rent (April / May / June / July / August)")
        self.assertEqual(rent["flag"], FLAG_OFF_BOOK)
        self.assertEqual(rent["category_id"], RENT_ID)
        self.assertEqual(rent["actual"], 0.0)
        self.assertEqual(rent["from"], COINBASE_USDC_LABEL)

    def test_student_loan_citation_electric_water_not_yet(self) -> None:
        strip = build_planned_actual_strip(_snaps(_ac_items(), []), _ac_map(), as_of=AS_OF)
        for name, cid in (
            ("Student Loan", STUDENT_LOAN_ID),
            ("Lee County Citation", LEE_COUNTY_ID),
            ("Electric", ELECTRIC_ID),
            ("Water", WATER_ID),
        ):
            row = _row(strip, name)
            self.assertEqual(row["flag"], FLAG_NOT_YET, name)
            self.assertEqual(row["actual"], 0.0, name)
            self.assertEqual(row["category_id"], cid, name)

    def test_skipped_payment_transfer_never_become_actual(self) -> None:
        leftover = _leftover_pile(87)
        mapped = _ac_map()
        self.assertEqual(len(leftover), 87)
        for tx in leftover:
            self.assertTrue(is_skipped_tx(tx, mapped), tx.get("id"))
        in_map = [
            _tx(payee="Water", amount=-67.0, category_id=WATER_ID, category_name="Water"),
        ]
        strip = build_planned_actual_strip(
            _snaps(_ac_items(), leftover + in_map), mapped, as_of=AS_OF
        )
        self.assertGreaterEqual(strip["summary"]["skipped_leftover_txs"], 87)
        self.assertEqual(_row(strip, "Rent")["actual"], 0.0)
        self.assertEqual(_row(strip, "Thaís")["actual"], 0.0)
        self.assertEqual(_row(strip, "FilterEasy")["actual"], 0.0)
        water = _row(strip, "Water")
        self.assertEqual(water["flag"], FLAG_ON)
        self.assertAlmostEqual(water["actual"], 67.0)

    def test_three_tabs_render_and_discretionary_excluded(self) -> None:
        extra = {
            "Collateral": {
                "role": "collateral_investments",
                "items": [
                    _item("Agentic Fund Allocation", 108.33, "X Money"),
                    _item("ASIC Fleet OpEx", 437.2, "X Money"),
                ],
            },
            "Productive Discretionary": {
                "role": "productive_capital_outlay",
                "items": [_item("ASIC", 2500.0, None)],
            },
            "Consumer Discretionary": {
                "role": "consumer_wishlist",
                "items": [_item("Robot Vac", 0.0, None)],
            },
            "Fleet": {
                "role": "fleet_ops",
                "items": [
                    _item("Santander (June / July / August)", 1082.52, "X Money", tab="Fleet"),
                    _item("Capital One (June / July / August)", 1121.55, "X Money", tab="Fleet"),
                    _item("GM Financial (June / July / August)", 1321.66, "X Money", tab="Fleet"),
                    _item("Rivian R1S", 1350.0, "NFCU (Zelle)", tab="Fleet"),
                    _item("Fleet Insurance", 633.20, "X Money", tab="Fleet"),
                ],
            },
        }
        leftover = _leftover_pile(87) + [
            _tx(payee="Santander", amount=-1082.52, category_name="Uncategorized"),
            _tx(
                payee="Capital One",
                amount=-1121.55,
                transfer_account_id="xfer",
                category_name="Uncategorized",
            ),
            _tx(payee="GM Financial", amount=-1321.66, category_name="Uncategorized"),
        ]
        cmap = _map(
            *_ac_map()["categories"],
            _cat("sant", "Santander"),
            _cat("cap", "Capital One"),
            _cat("gm", "GM Financial"),
            _cat("riv", "Rivian R1S"),
            _cat("af", "Agentic Fund Allocation"),
            _cat("asic-opex", "ASIC Fleet OpEx"),
            _cat("asic-disc", "ASIC"),
            _cat("gold", "Robinhood Gold"),
            _cat("cbcard", "Coinbase One Card"),
        )
        strip = build_planned_actual_strip(
            _snaps(_ac_items(), leftover, extra_tabs=extra), cmap, as_of=AS_OF
        )
        names = {r["item"] for r in strip["rows"]}
        self.assertIn("Fleet Insurance", names)
        self.assertIn("Santander (June / July / August)", names)
        self.assertIn("Capital One (June / July / August)", names)
        self.assertIn("GM Financial (June / July / August)", names)
        self.assertIn("Rivian R1S", names)
        self.assertIn("Agentic Fund Allocation", names)
        self.assertIn("ASIC Fleet OpEx", names)
        self.assertNotIn("ASIC", names)
        self.assertNotIn("Robot Vac", names)
        self.assertNotIn("Coinbase One Card", names)
        self.assertNotIn("Robinhood Gold", names)
        tabs = {r["tab"] for r in strip["rows"]}
        self.assertTrue({"Essential", "Fleet", "Collateral"} <= tabs)
        self.assertNotIn("Productive Discretionary", tabs)
        self.assertNotIn("Consumer Discretionary", tabs)

        sant = _row(strip, "Santander (June / July / August)")
        cap = _row(strip, "Capital One (June / July / August)")
        gm = _row(strip, "GM Financial (June / July / August)")
        riv = _row(strip, "Rivian R1S")
        af = _row(strip, "Agentic Fund Allocation")
        opex = _row(strip, "ASIC Fleet OpEx")
        self.assertEqual(sant["flag"], FLAG_PAYMENT_SHAPED)
        self.assertEqual(cap["flag"], FLAG_PAYMENT_SHAPED)
        self.assertEqual(gm["flag"], FLAG_PAYMENT_SHAPED)
        self.assertEqual(riv["flag"], FLAG_OFF_BOOK)
        self.assertEqual(riv["from_venue"], "NFCU (Zelle)")
        self.assertEqual(sant["actual"], 0.0)
        self.assertEqual(cap["actual"], 0.0)
        self.assertEqual(gm["actual"], 0.0)
        self.assertEqual(riv["actual"], 0.0)
        self.assertAlmostEqual(sant["planned"], 1082.52)
        self.assertEqual(af["tab"], "Collateral")
        self.assertEqual(opex["tab"], "Collateral")
        self.assertEqual(opex["flag"], FLAG_NOT_YET)
        self.assertNotEqual(opex["category_id"], "asic-disc")
        self.assertEqual(opex["category_id"], "asic-opex")

    def test_asic_not_aliased_to_fleet_opex(self) -> None:
        self.assertFalse(names_join("ASIC", "ASIC Fleet OpEx"))
        self.assertTrue(names_join("ASIC Fleet OpEx", "ASIC Fleet OpEx"))

    def test_discover_planned_tabs_from_sheet_names_not_invented(self) -> None:
        """Sheet tab titles (probed): Essential/Personal, Fleet, Collateral."""
        found = [k for k, _ in discover_planned_tabs({
            "Essential": {"role": "upcoming_expense_estimates", "items": []},
            "Fleet": {"role": "fleet_ops", "items": []},
            "Collateral": {"role": "collateral_investments", "items": []},
            "Productive Discretionary": {"role": "productive_capital_outlay", "items": []},
            "Consumer Discretionary": {"role": "consumer_wishlist", "items": []},
        })]
        self.assertEqual(found, ["Essential", "Fleet", "Collateral"])

        personal_only = discover_planned_tabs({
            "Personal": {"role": "upcoming_expense_estimates", "items": []},
            "Discretionary": {"role": "excess_capital_targets", "items": []},
        })
        self.assertEqual([k for k, _ in personal_only], ["Personal"])

        no_collateral = [k for k, _ in discover_planned_tabs({
            "Essential": {"role": "upcoming_expense_estimates", "items": []},
            "Fleet": {"role": "fleet_ops", "items": []},
            "Productive Discretionary": {"role": "productive_capital_outlay", "items": []},
        })]
        self.assertEqual(no_collateral, ["Essential", "Fleet"])
        self.assertNotIn("Collateral", no_collateral)

    def test_personal_legacy_tab_still_renders_fleet_loans(self) -> None:
        """AC 2: fleet loans on planned tabs are rows, not 'never render'."""
        items = [
            _item("Rent", 2090.0, "Coinbase"),
            _item("Santander (June / July / August)", 1082.52, "X Money"),
            _item("Capital One (June / July / August)", 1121.55, "X Money"),
        ]
        leftover = [
            _tx(payee="Santander", amount=-1082.52, category_name="Uncategorized"),
            _tx(
                payee="Capital One",
                amount=-1121.55,
                transfer_account_id="xfer",
                category_name="Uncategorized",
            ),
        ]
        cmap = _map(
            *_ac_map()["categories"],
            _cat("sant", "Santander"),
            _cat("cap", "Capital One"),
        )
        snaps = {
            "expenses": {
                "sheet_id": "15ZU7843pTSLSEI0U-taFZ4Qwk3bTQx6cWh2Ex0d7NJQ",
                "sheet_name": "Personal Expense Sheet",
                "tabs": {
                    "Personal": {"items": items, "role": "upcoming_expense_estimates"},
                    "Discretionary": {
                        "role": "excess_capital_targets",
                        "items": [_item("ASIC", 2500.0, None), _item("Robot Vac", 0.0, None)],
                    },
                },
            },
            "x_money": {"transactions": leftover, "source": "ynab"},
            "one_card": {"transactions": [], "source": "ynab"},
            "rh_checking": {"transactions": [], "source": "ynab"},
        }
        strip = build_planned_actual_strip(snaps, cmap, as_of=AS_OF)
        names = {r["item"] for r in strip["rows"]}
        self.assertIn("Santander (June / July / August)", names)
        self.assertIn("Capital One (June / July / August)", names)
        self.assertNotIn("ASIC", names)
        self.assertNotIn("Robot Vac", names)
        sant = _row(strip, "Santander (June / July / August)")
        self.assertEqual(sant["flag"], FLAG_PAYMENT_SHAPED)
        self.assertEqual(sant["actual"], 0.0)
        self.assertAlmostEqual(sant["planned"], 1082.52)
        self.assertEqual(sant["tab"], "Personal")

    def test_rent_thais_ignore_one_card_and_x_money_txs(self) -> None:
        txs = [
            _tx(
                payee="Rent",
                amount=-100.0,
                category_id=RENT_ID,
                category_name="Rent",
                tx_id="one-card-rent",
            ),
            _tx(
                payee="Thaís",
                amount=-900.0,
                category_id=THAIS_ID,
                category_name="Thaís",
                tx_id="xm-thais",
            ),
        ]
        snaps = _snaps(_ac_items(), [])
        snaps["one_card"] = {"transactions": [txs[0]], "source": "ynab"}
        snaps["x_money"] = {"transactions": [txs[1]], "source": "ynab"}
        strip = build_planned_actual_strip(snaps, _ac_map(), as_of=AS_OF)
        rent = _row(strip, "Rent")
        thais = _row(strip, "Thaís")
        self.assertEqual(rent["flag"], FLAG_OFF_BOOK)
        self.assertEqual(thais["flag"], FLAG_OFF_BOOK)
        self.assertEqual(rent["actual"], 0.0)
        self.assertEqual(thais["actual"], 0.0)
        self.assertEqual(rent["from"], COINBASE_USDC_LABEL)
        self.assertEqual(thais["from"], COINBASE_USDC_LABEL)

    def test_rent_thais_locked_even_if_sheet_from_drifted(self) -> None:
        items = [
            _item("Rent", 2090.0, "X Money"),
            _item("Thaís", 900.0, "Coinbase One Card"),
        ]
        txs = [
            _tx(payee="Rent", amount=-100.0, category_id=RENT_ID, category_name="Rent"),
            _tx(payee="Thaís", amount=-900.0, category_id=THAIS_ID, category_name="Thaís"),
        ]
        snaps = _snaps(items, [])
        snaps["x_money"] = {"transactions": txs, "source": "ynab"}
        snaps["one_card"] = {
            "transactions": [
                _tx(
                    payee="August Rent",
                    amount=-100.0,
                    category_id=RENT_ID,
                    category_name="Rent",
                    tx_id="one-card-aug-rent",
                )
            ],
            "source": "ynab",
        }
        strip = build_planned_actual_strip(snaps, _ac_map(), as_of=AS_OF)
        for name in ("Rent", "Thaís"):
            row = _row(strip, name)
            self.assertEqual(row["flag"], FLAG_OFF_BOOK, name)
            self.assertEqual(row["actual"], 0.0, name)
            self.assertEqual(row["from"], COINBASE_USDC_LABEL, name)
            self.assertEqual(row["from_venue"], COINBASE_USDC_LABEL, name)
            self.assertNotEqual(row["flag"], "under", name)
        aug = build_planned_actual_strip(
            _snaps([_item("August Rent", 2090.0, "RH Checking")], txs),
            _ac_map(),
            as_of=AS_OF,
        )
        row = _row(aug, "August Rent")
        self.assertEqual(row["flag"], FLAG_OFF_BOOK)
        self.assertEqual(row["actual"], 0.0)
        self.assertEqual(row["from"], COINBASE_USDC_LABEL)

    def test_month_prefixed_rent_rows_are_not_collapsed(self) -> None:
        items = [
            _item("April Rent", 775.0, "Coinbase"),
            _item("August Rent", 2090.0, "Coinbase"),
            _item("Thaís", 900.0, "Coinbase"),
        ]
        strip = build_planned_actual_strip(_snaps(items, []), _ac_map(), as_of=AS_OF)
        april = _row(strip, "April Rent")
        august = _row(strip, "August Rent")
        thais = _row(strip, "Thaís")
        self.assertEqual(april["flag"], FLAG_OFF_BOOK)
        self.assertEqual(august["flag"], FLAG_OFF_BOOK)
        self.assertEqual(thais["flag"], FLAG_OFF_BOOK)
        self.assertEqual(april["from"], COINBASE_USDC_LABEL)
        self.assertEqual(august["from"], COINBASE_USDC_LABEL)
        self.assertEqual(thais["from"], COINBASE_USDC_LABEL)
        self.assertEqual(april["actual"], 0.0)
        self.assertEqual(august["actual"], 0.0)

    def test_flag_enum_only(self) -> None:
        txs = [
            _tx(payee="Water", amount=-67.0, category_id=WATER_ID, category_name="Water"),
            _tx(payee="FilterEasy", amount=-10.65, category_id=FILTER_SUB_ID, category_name="Subscriptions"),
            _tx(
                payee="FilterEasy",
                amount=-10.65,
                category_id=FILTER_SUB_ID,
                category_name="Subscriptions",
                date="2026-08-22",
            ),
            _tx(payee="YNAB", amount=-14.99, category_id=YNAB_SUB_ID, category_name="🌳 YNAB subscription"),
            _tx(
                payee="YNAB",
                amount=-3.00,
                category_id=YNAB_SUB_ID,
                category_name="🌳 YNAB subscription",
                date="2026-08-21",
            ),
        ]
        strip = build_planned_actual_strip(_snaps(_ac_items(), txs), _ac_map(), as_of=AS_OF)
        self.assertTrue(strip["display_only"])
        self.assertFalse(strip["coach_wired"])
        self.assertFalse(strip["spectrum_trigger"])
        for row in strip["rows"]:
            self.assertIn(row["flag"], FLAGS)
            self.assertNotIn(row["flag"], ("over", "under", "overspend"))


    def test_aug_thais_coinbase_send_900_895_on(self) -> None:
        snaps = _snaps(_ac_items(), [])
        snaps["coinbase_usdc_sends"] = {
            "source": "coinbase_v2_usdc",
            "transactions": [
                {
                    "id": "send-aug-thais",
                    "type": "send",
                    "status": "completed",
                    "created_at": "2026-08-10T14:00:00Z",
                    "amount": {"amount": "-895.00", "currency": "USDC"},
                    "to": {"resource": "address"},
                    "description": "Thaís",
                },
                {
                    "id": "send-jul-thais",
                    "type": "send",
                    "status": "completed",
                    "created_at": "2026-07-10T14:00:00Z",
                    "amount": {"amount": "-900.00", "currency": "USDC"},
                    "to": {"resource": "address"},
                    "description": "Thaís",
                },
                {
                    "id": "lend-withdraw",
                    "type": "retail_defi_lend_withdrawal",
                    "created_at": "2026-08-11T14:00:00Z",
                    "amount": {"amount": "895.00", "currency": "USDC"},
                },
                {
                    "id": "cc-lock",
                    "type": "credit_card_collateral_lock",
                    "created_at": "2026-08-12T14:00:00Z",
                    "amount": {"amount": "-895.00", "currency": "USDC"},
                },
                {
                    "id": "generic-lock",
                    "type": "lock",
                    "created_at": "2026-08-13T14:00:00Z",
                    "amount": {"amount": "-25.00", "currency": "USDC"},
                },
                {
                    "id": "aug10-unlabeled-5",
                    "type": "send",
                    "created_at": "2026-08-10T12:00:00Z",
                    "amount": {"amount": "-5.00", "currency": "USDC"},
                    "to": {"resource": "address"},
                    "description": "",
                },
                {
                    "id": "aug4-unlabeled-125",
                    "type": "send",
                    "created_at": "2026-08-04T12:00:00Z",
                    "amount": {"amount": "-125.00", "currency": "USDC"},
                    "to": {"resource": "address"},
                    "description": "",
                },
                {
                    "id": "jul-phone-20",
                    "type": "send",
                    "created_at": "2026-07-12T12:00:00Z",
                    "amount": {"amount": "-20.00", "currency": "USDC"},
                    "to": {"resource": "phone"},
                    "description": "",
                },
                {
                    "id": "jul-phone-40",
                    "type": "send",
                    "created_at": "2026-07-20T12:00:00Z",
                    "amount": {"amount": "-40.00", "currency": "USDC"},
                    "to": {"resource": "phone"},
                    "description": "",
                },
            ],
        }
        snaps["one_card"] = {
            "transactions": [
                _tx(payee="Thaís", amount=-900.0, category_id=THAIS_ID, category_name="Thaís"),
            ],
            "source": "ynab",
        }
        snaps["x_money"] = {
            "transactions": [
                _tx(payee="Rent", amount=-25.0, category_id=RENT_ID, category_name="Rent"),
                _tx(payee="Thaís", amount=-900.0, category_id=THAIS_ID, category_name="Thaís"),
            ],
            "source": "ynab",
        }
        strip = build_planned_actual_strip(snaps, _ac_map(), as_of=AS_OF)
        thais = _row(strip, "Thaís")
        rent = _row(strip, "Rent")
        self.assertAlmostEqual(thais["planned"], 900.0)
        self.assertAlmostEqual(thais["actual"], 895.0)
        self.assertEqual(thais["flag"], FLAG_ON)
        self.assertNotEqual(thais["flag"], "under")
        self.assertEqual(thais["from"], COINBASE_USDC_LABEL)
        self.assertEqual(thais["from_venue"], COINBASE_USDC_LABEL)
        self.assertEqual(rent["flag"], FLAG_OFF_BOOK)
        self.assertEqual(rent["actual"], 0.0)
        self.assertAlmostEqual(rent["planned"], 2090.0)
        self.assertEqual(rent["from"], COINBASE_USDC_LABEL)
        self.assertFalse(strip["coach_wired"])
        self.assertFalse(strip["spectrum_trigger"])

    def test_july_thais_send_not_in_aug_actual(self) -> None:
        snaps = _snaps(_ac_items(), [])
        snaps["coinbase_usdc_sends"] = {
            "source": "coinbase_v2_usdc",
            "transactions": [
                {
                    "id": "send-jul-only",
                    "type": "send",
                    "created_at": "2026-07-10T14:00:00Z",
                    "amount": {"amount": "-900.00", "currency": "USDC"},
                    "description": "Thaís",
                }
            ],
        }
        strip = build_planned_actual_strip(snaps, _ac_map(), as_of=AS_OF)
        thais = _row(strip, "Thaís")
        self.assertAlmostEqual(thais["planned"], 900.0)
        self.assertEqual(thais["actual"], 0.0)
        self.assertEqual(thais["flag"], FLAG_OFF_BOOK)
        self.assertNotEqual(thais["flag"], "under")

    def test_rent_planned_is_sheet_monthly_not_twenty_five_times_days(self) -> None:
        items = [
            _item("Rent", 2090.0, "Coinbase"),
            _item("Thaís", 900.0, "Coinbase"),
        ]
        items[0]["daily"] = 25.0
        snaps = _snaps(items, [])
        snaps["coinbase_usdc_sends"] = {
            "source": "coinbase_v2_usdc",
            "transactions": [
                {
                    "id": "future-daily-shape",
                    "type": "send",
                    "created_at": "2026-08-15T12:00:00Z",
                    "amount": {"amount": "-25.00", "currency": "USDC"},
                    "to": {"resource": "phone"},
                    "description": "Nicole Volkernick",
                },
                {
                    "id": "send-aug-thais",
                    "type": "send",
                    "created_at": "2026-08-10T14:00:00Z",
                    "amount": {"amount": "-895.00", "currency": "USDC"},
                    "to": {"resource": "address"},
                    "description": "Thaís",
                },
            ],
        }
        strip = build_planned_actual_strip(snaps, _ac_map(), as_of=AS_OF)
        rent = _row(strip, "Rent")
        thais = _row(strip, "Thaís")
        self.assertAlmostEqual(rent["planned"], 2090.0)
        self.assertNotAlmostEqual(rent["planned"], 25.0 * 31)
        self.assertNotAlmostEqual(rent["planned"], 25.0 * 30)
        self.assertEqual(rent["actual"], 0.0)
        self.assertEqual(rent["flag"], FLAG_OFF_BOOK)
        self.assertEqual(rent["from"], COINBASE_USDC_LABEL)
        self.assertAlmostEqual(thais["planned"], 900.0)
        self.assertAlmostEqual(thais["actual"], 895.0)
        self.assertEqual(thais["flag"], FLAG_ON)
        self.assertFalse(strip["coach_wired"])

    def test_rent_email_send_books_actual_planned_stays_sheet_monthly(self) -> None:
        items = [
            _item("Rent", 2090.0, "Coinbase"),
            _item("Thaís", 900.0, "Coinbase"),
        ]
        items[0]["daily"] = 25.0
        snaps = _snaps(items, [])
        snaps["coinbase_usdc_sends"] = {
            "source": "coinbase_v2_usdc",
            "transactions": [
                {
                    "id": "aug27-rent-email-25",
                    "type": "send",
                    "status": "completed",
                    "created_at": "2026-08-27T12:00:00Z",
                    "amount": {"amount": "-25.00", "currency": "USDC"},
                    "to": {"resource": "email", "email": "nvolkern@gmail.com"},
                    "description": "",
                },
                {
                    "id": "nicole-phone",
                    "type": "send",
                    "created_at": "2026-08-15T12:00:00Z",
                    "amount": {"amount": "-25.00", "currency": "USDC"},
                    "to": {"resource": "phone", "phone": "+15555550100"},
                    "description": "Nicole Volkernick",
                },
                {
                    "id": "aug10-unlabeled-5",
                    "type": "send",
                    "created_at": "2026-08-10T12:00:00Z",
                    "amount": {"amount": "-5.00", "currency": "USDC"},
                    "to": {"resource": "address"},
                    "description": "",
                },
                {
                    "id": "aug4-unlabeled-125",
                    "type": "send",
                    "created_at": "2026-08-04T12:00:00Z",
                    "amount": {"amount": "-125.00", "currency": "USDC"},
                    "to": {"resource": "address"},
                    "description": "",
                },
                {
                    "id": "send-aug-thais",
                    "type": "send",
                    "created_at": "2026-08-10T14:00:00Z",
                    "amount": {"amount": "-895.00", "currency": "USDC"},
                    "to": {"resource": "address"},
                    "description": "Thaís",
                },
            ],
        }
        strip = build_planned_actual_strip(snaps, _ac_map(), as_of="2026-08-27")
        rent = _row(strip, "Rent")
        thais = _row(strip, "Thaís")
        self.assertAlmostEqual(rent["planned"], 2090.0)
        self.assertNotAlmostEqual(rent["planned"], 25.0 * 31)
        self.assertAlmostEqual(rent["actual"], 25.0)
        self.assertEqual(rent["flag"], FLAG_ON)
        self.assertEqual(rent["from"], COINBASE_USDC_LABEL)
        self.assertAlmostEqual(thais["planned"], 900.0)
        self.assertAlmostEqual(thais["actual"], 895.0)
        self.assertEqual(thais["flag"], FLAG_ON)
        self.assertFalse(strip["coach_wired"])

        pending_only = dict(snaps)
        pending_only["coinbase_usdc_sends"] = {
            "source": "coinbase_v2_usdc",
            "transactions": [
                {
                    "id": "aug27-rent-still-pending",
                    "type": "send",
                    "status": "pending",
                    "created_at": "2026-08-27T12:00:00Z",
                    "amount": {"amount": "-25.00", "currency": "USDC"},
                    "to": {"resource": "email", "email": "nvolkern@gmail.com"},
                }
            ],
        }
        pending_strip = build_planned_actual_strip(pending_only, _ac_map(), as_of="2026-08-27")
        self.assertEqual(_row(pending_strip, "Rent")["actual"], 0.0)
        self.assertEqual(_row(pending_strip, "Rent")["flag"], FLAG_OFF_BOOK)

        snaps_aug = _snaps([_item("August Rent", 2090.0, "Coinbase")], [])
        snaps_aug["coinbase_usdc_sends"] = snaps["coinbase_usdc_sends"]
        august = build_planned_actual_strip(snaps_aug, _ac_map(), as_of="2026-08-27")
        row = _row(august, "August Rent")
        self.assertAlmostEqual(row["planned"], 2090.0)
        self.assertAlmostEqual(row["actual"], 25.0)
        self.assertEqual(row["flag"], FLAG_ON)

    def test_leftover_ynab_pile_does_not_backfill_thais_or_rent(self) -> None:
        snaps = _snaps(_ac_items(), _leftover_pile(87))
        snaps["coinbase_usdc_sends"] = {
            "source": "coinbase_v2_usdc",
            "transactions": [
                {
                    "id": "send-aug-thais",
                    "type": "send",
                    "created_at": "2026-08-10T14:00:00Z",
                    "amount": {"amount": "-895.00", "currency": "USDC"},
                    "to": {"resource": "address"},
                    "description": "Thaís",
                }
            ],
        }
        strip = build_planned_actual_strip(snaps, _ac_map(), as_of=AS_OF)
        thais = _row(strip, "Thaís")
        rent = _row(strip, "Rent")
        self.assertAlmostEqual(thais["actual"], 895.0)
        self.assertEqual(thais["flag"], FLAG_ON)
        self.assertEqual(rent["actual"], 0.0)
        self.assertEqual(rent["flag"], FLAG_OFF_BOOK)
        self.assertGreaterEqual(strip["summary"]["skipped_leftover_txs"], 87)
        self.assertFalse(strip["coach_wired"])
        self.assertFalse(strip["spectrum_trigger"])

    def test_live_map_joins_gym_pets_subs_loans_collateral(self) -> None:
        cmap = load_category_map(MAP_PATH)
        items = [
            _item("Gym", 20.0, "X Money"),
            _item("Pets", 40.0, "X Money"),
            _item("FilterEasy", 10.65, "X Money"),
            _item("Student Loan", 83.33, "X Money"),
            _item("Lee County Citation", 161.0, "X Money"),
            _item("Santander", 1082.52, "X Money"),
            _item("GM Financial", 900.0, "X Money"),
            _item("Capital One", 700.0, "X Money"),
            _item("Rivian R1S", 1100.0, "NFCU (Zelle)", tab="Fleet"),
            _item("ASIC Fleet OpEx", 2500.0, "X Money", tab="Collateral"),
            _item("Agentic Fund Allocation", 100.0, "X Money", tab="Collateral"),
            _item("Thaís", 900.0, "Coinbase"),
            _item("Rent", 2090.0, "Coinbase"),
        ]
        extra = {
            "Fleet": {"items": [], "role": "fleet_ops"},
            "Collateral": {"items": [], "role": "collateral"},
        }
        strip = build_planned_actual_strip(_snaps(items, [], extra_tabs=extra), cmap, as_of=AS_OF)
        by_item = {r["item"]: r for r in strip["rows"]}
        self.assertEqual(by_item["Gym"]["category_id"], "4b5886e5-a645-401e-a5a7-a24d52e9e044")
        self.assertEqual(by_item["Pets"]["category_id"], "cff7ed48-bffe-4bf6-8102-740fefff82b0")
        self.assertEqual(by_item["FilterEasy"]["category_id"], "77083d0b-3501-4639-9990-ced43c1a0435")
        self.assertEqual(by_item["Student Loan"]["category_id"], "ec45e5f7-2912-43a4-a23c-4fb1357571b7")
        self.assertEqual(by_item["Lee County Citation"]["category_id"], "69f30960-4655-4b94-97f0-875f1062ff45")
        self.assertEqual(by_item["ASIC Fleet OpEx"]["category_id"], "06e562d9-48d8-4ede-8295-af004741dca0")
        self.assertEqual(by_item["Agentic Fund Allocation"]["category_id"], "d178971c-6761-4e1a-b2d3-0e41607da87a")
        self.assertEqual(by_item["Thaís"]["flag"], FLAG_OFF_BOOK)
        self.assertEqual(by_item["Rent"]["flag"], FLAG_OFF_BOOK)
        self.assertEqual(by_item["Rent"]["actual"], 0.0)
        self.assertEqual(by_item["Santander"]["flag"], FLAG_PAYMENT_SHAPED)
        self.assertEqual(by_item["Rivian R1S"]["flag"], FLAG_OFF_BOOK)
        self.assertFalse(strip["coach_wired"])


class TestCoachAndSpectrumUnchanged(unittest.TestCase):
    def test_coach_plan_ignores_planned_actual_flags(self) -> None:
        snaps = _snaps(_ac_items(), [])
        plan = build_coach_plan(snaps, as_of=AS_OF)
        self.assertTrue(plan.get("ok"))
        blob = str(plan).lower()
        self.assertNotIn("cadence-lump", blob)
        self.assertNotIn("two-charge", blob)
        self.assertNotIn("off-book from", blob)
        items = {o.get("item") for o in plan.get("obligations") or []}
        self.assertIn("Thaís", items)
        self.assertIn("Student Loan", items)

    def test_spectrum_coach_wired_unchanged_by_strip_builder(self) -> None:
        treasury = {
            "as_of": "2026-08-27",
            "evaluation": {
                "inputs": {"next_free_dollar": 200, "ltv": 0.40},
                "cashflow_allocation": {"next_free_dollar": 200},
            },
            "snapshot": {
                "expenses": {
                    "tabs": {
                        "Essential": {
                            "items": [
                                {"item": "Rent", "date": "2026-09-01", "amount_due": 2090},
                            ]
                        }
                    }
                }
            },
        }
        before = build_interest_spectrum(treasury=treasury, config={}, x_money={}, solana={})
        strip = build_planned_actual_strip(_snaps(_ac_items(), []), _ac_map(), as_of=AS_OF)
        after = build_interest_spectrum(treasury=treasury, config={}, x_money={}, solana={})
        self.assertEqual(before.get("coach_wired"), after.get("coach_wired"))
        self.assertEqual(before.get("policy", {}).get("coach_wired"), after.get("policy", {}).get("coach_wired"))
        self.assertEqual(before.get("coach_nudge"), after.get("coach_nudge"))
        self.assertFalse(strip.get("coach_wired"))
        self.assertFalse(strip.get("spectrum_trigger"))
        self.assertNotIn("coach_nudge", strip)


if __name__ == "__main__":
    unittest.main()
