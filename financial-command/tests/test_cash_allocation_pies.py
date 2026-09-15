"""Cash & credit allocation pies (#756): MECE partition + FM pie reuse."""

from __future__ import annotations

import math
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "financial-command" / "index.html"

METRIC_LABELS = (
    "LTV sleeve (spot+HY)",
    "One Card owed",
    "HY LTV Buffer",
    "Available credit",
    "Idle spot USDC",
    "USDC security deposit",
    "Liquid BTC",
    "RH Checking",
    "BTC ≈ USD",
    "X Money",
    "LTV",
    "Morpho var APR",
    "Liq. price (BTC)",
    "Bank cash (RH+X)",
    "Card source",
    "Bill-pay cash",
    "Checking 30d out",
    "X Money 30d out",
    "Solana book",
    "JR-strcUSX",
)

EXCLUDED_SLICE_LABELS = (
    "Bank cash",
    "Bill-pay cash",
    "Available credit",
    "One Card owed",
    "LTV sleeve",
    "Solana book",
    "working_usdc",
    "card_available_credit",
    "card_balance",
)


def _num(x, default=0.0) -> float:
    try:
        n = float(x)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _short_xm(name: str) -> str:
    cleaned = re.sub(r"\s+[–-]\s+\d{4}\s*$", "", str(name or "")).strip()
    return cleaned or str(name or "space")


def cash_allocation_pools(inp: dict, xm: dict, rhc: dict, sol: dict) -> dict:
    """Oracle matching cashAllocationPools() in financial-command/index.html."""

    def pool(symbol: str, value) -> dict:
        return {"symbol": symbol, "market_value": _num(value, float("nan"))}

    def pos(items: list[dict]) -> list[dict]:
        return [
            p
            for p in items
            if math.isfinite(p["market_value"]) and p["market_value"] > 0
        ]

    spaces = xm.get("spaces") if isinstance(xm.get("spaces"), list) else []
    if spaces:
        main_val = _num(xm.get("main_cash"))
        xm_pools = []
        if main_val > 0:
            xm_pools.append(
                pool("X Money · " + _short_xm(xm.get("account_name") or "Main"), main_val)
            )
        for sp in spaces:
            xm_pools.append(
                pool("X Money · " + _short_xm(sp.get("account_name")), sp.get("cash"))
            )
    else:
        xm_pools = [pool("X Money", inp.get("x_money_cash", xm.get("cash")))]
    cash = pos(
        [
            pool("RH Checking", inp.get("rh_checking_cash", rhc.get("cash"))),
            pool("Idle spot USDC", inp.get("liquid_usdc")),
            *xm_pools,
        ]
    )
    crypto = pos(
        [
            pool("Liquid BTC", inp.get("liquid_btc_usd")),
            pool("SOL", sol.get("sol_usd")),
            pool("On-chain USDC", inp.get("solana_usdc", sol.get("usdc"))),
            pool(
                "JR-strcUSX",
                inp.get("solana_jr_strcusx_usd", sol.get("jr_strcusx_usd")),
            ),
            pool("HY LTV Buffer", inp.get("vault_usdc")),
            pool("USDC security deposit", inp.get("card_security_deposit_usdc")),
        ]
    )
    return {
        "cashPools": cash,
        "cryptoPools": crypto,
        "allPools": cash + crypto,
    }


def pie_slices(positions: list[dict], max_slices: int = 8) -> list[dict]:
    valued = sorted(
        [p for p in positions if p.get("market_value") and p["market_value"] > 0],
        key=lambda p: p["market_value"],
        reverse=True,
    )
    if len(valued) <= max_slices:
        return valued
    head = valued[: max_slices - 1]
    rest = valued[max_slices - 1 :]
    head.append(
        {
            "symbol": "Other",
            "market_value": sum(p["market_value"] for p in rest),
        }
    )
    return head


class TestCashAllocationPiesMarkup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        m = re.search(
            r"const cashAllocationPools = \(\) => \{",
            cls.html,
        )
        if not m:
            raise AssertionError("cashAllocationPools not found")
        start = cls.html.find("{", m.end() - 1)
        depth = 0
        end = None
        for i, ch in enumerate(cls.html[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            raise AssertionError("cashAllocationPools unclosed")
        cls.pools_js = cls.html[m.start() : end]

    def test_sleeves_then_pies_then_metrics(self):
        sleeves = self.html.find('id="buffer-sleeves"')
        pies = self.html.find('id="cc-pies"')
        metrics = self.html.find('id="cb-metrics"')
        self.assertGreater(sleeves, 0)
        self.assertGreater(pies, sleeves)
        self.assertGreater(metrics, pies)
        self.assertIn('class="cc-pies"', self.html)
        self.assertIn('id="cc-pies-hint"', self.html)

    def test_three_named_pies_and_shared_helpers(self):
        self.assertEqual(self.html.count('pieCard("Capital allocation"'), 1)
        self.assertEqual(self.html.count('pieCard("Cash accounts"'), 1)
        self.assertEqual(self.html.count('pieCard("Crypto & deposits"'), 1)
        self.assertIn("const pieSVG = (slices) =>", self.html)
        self.assertIn("const pieCard = (title, positions, ramp, opts) =>", self.html)
        # Fund manager reuses the same helpers (no second pieSVG definition).
        self.assertEqual(self.html.count("const pieSVG ="), 1)
        self.assertEqual(self.html.count("const pieCard ="), 1)
        self.assertIn('pieCard("Agentic holdings allocation", apos)', self.html)

    def test_mobile_one_column_and_eight_slice_other(self):
        self.assertIn(".fm-pies, .cc-pies { grid-template-columns: 1fr; }", self.html)
        self.assertIn("grid-template-columns: repeat(3, 1fr)", self.html)
        self.assertIn("const MAX_SLICES = 8", self.html)
        self.assertIn('label: "Other"', self.html)
        self.assertIn(".sort((a, b) => b.value - a.value)", self.html)
        self.assertIn("opts && opts.breakdown", self.html)

    def test_mece_slice_labels_locked(self):
        js = self.pools_js
        for label in (
            "RH Checking",
            "Idle spot USDC",
            "Liquid BTC",
            "SOL",
            "On-chain USDC",
            "JR-strcUSX",
            "HY LTV Buffer",
            "USDC security deposit",
        ):
            self.assertIn(f'pool("{label}"', js)
        self.assertIn("mainVal > 0", js)
        self.assertIn("xm.spaces", js)
        self.assertIn("xm.main_cash", js)
        for label in EXCLUDED_SLICE_LABELS:
            self.assertNotIn(f'pool("{label}"', js)
        # Aggregates stay out of the pie function body.
        self.assertNotIn("working_usdc", js)
        self.assertNotIn("bank_cash", js)
        self.assertNotIn("bill_pay_cash", js)
        self.assertNotIn("solana_book_usd", js)
        self.assertNotIn("card_available_credit", js)
        self.assertNotIn("card_balance", js)
        self.assertNotIn("liquid_btc)", js)  # qty, not USD

    def test_no_metric_dropped(self):
        rows = self.html.split("const cashCreditRows = [", 1)[1].split(
            "const cashMid =", 1
        )[0]
        for label in METRIC_LABELS:
            self.assertIn(label, rows)


class TestCashAllocationMeceOracle(unittest.TestCase):
    def test_overdraft_main_excluded_spaces_sliced(self):
        inp = {
            "rh_checking_cash": 40.0,
            "liquid_usdc": 106.0,
            "liquid_btc_usd": 820.0,
            "vault_usdc": 100.0,
            "card_security_deposit_usdc": 500.0,
            "solana_usdc": 0.0,
            "solana_jr_strcusx_usd": 13.08,
            "working_usdc": 206.0,
            "bank_cash": 721.14,
            "bill_pay_cash": 40.0,
            "solana_book_usd": 14.20,
            "card_available_credit": 1.25,
            "card_balance": 498.75,
            "x_money_cash": 681.14,
        }
        xm = {
            "account_name": "Main – 2201",
            "main_cash": -1308.6,
            "cash": 681.14,
            "spaces": [
                {"account_name": "Auto Fleet – 0895", "cash": 1375.61},
                {"account_name": "Collateral – 3326", "cash": 483.78},
                {"account_name": "Utilities – 4867", "cash": 130.35},
            ],
        }
        pools = cash_allocation_pools(inp, xm, {"cash": 40.0}, {"sol_usd": 1.12})
        labels = [p["symbol"] for p in pools["allPools"]]
        self.assertNotIn("X Money · Main", labels)
        self.assertIn("X Money · Auto Fleet", labels)
        self.assertIn("X Money · Collateral", labels)
        self.assertIn("X Money · Utilities", labels)
        self.assertNotIn("Bank cash (RH+X)", labels)
        cash_total = sum(p["market_value"] for p in pools["cashPools"])
        crypto_total = sum(p["market_value"] for p in pools["cryptoPools"])
        all_total = sum(p["market_value"] for p in pools["allPools"])
        self.assertAlmostEqual(all_total, cash_total + crypto_total)
        # Each dollar once: union equals parts; overdraft not subtracted from assets.
        self.assertAlmostEqual(
            cash_total, 40.0 + 106.0 + 1375.61 + 483.78 + 130.35
        )
        self.assertAlmostEqual(crypto_total, 820.0 + 1.12 + 13.08 + 100.0 + 500.0)
        # Derived / capacity metrics are not in the pie total.
        self.assertNotAlmostEqual(all_total, inp["working_usdc"])
        self.assertGreater(all_total, inp["bank_cash"])

    def test_positive_main_is_a_slice(self):
        pools = cash_allocation_pools(
            {"rh_checking_cash": 10, "liquid_usdc": 0},
            {
                "account_name": "Main – 2201",
                "main_cash": 25.5,
                "spaces": [{"account_name": "Auto Fleet – 0895", "cash": 5}],
            },
            {},
            {},
        )
        labels = {p["symbol"]: p["market_value"] for p in pools["cashPools"]}
        self.assertAlmostEqual(labels["X Money · Main"], 25.5)
        self.assertAlmostEqual(labels["X Money · Auto Fleet"], 5)
        self.assertAlmostEqual(labels["RH Checking"], 10)

    def test_no_spaces_falls_back_to_aggregate_if_positive(self):
        pools = cash_allocation_pools(
            {"x_money_cash": 12.0},
            {"cash": 12.0, "spaces": []},
            {},
            {},
        )
        self.assertEqual([p["symbol"] for p in pools["cashPools"]], ["X Money"])

    def test_eight_slice_other_preserves_total(self):
        positions = [
            {"symbol": f"P{i}", "market_value": float(20 - i)} for i in range(12)
        ]
        slices = pie_slices(positions, 8)
        self.assertEqual(len(slices), 8)
        self.assertEqual(slices[-1]["symbol"], "Other")
        self.assertAlmostEqual(
            sum(s["market_value"] for s in slices),
            sum(p["market_value"] for p in positions),
        )
        self.assertEqual([s["symbol"] for s in slices[:7]], [f"P{i}" for i in range(7)])


if __name__ == "__main__":
    unittest.main()
