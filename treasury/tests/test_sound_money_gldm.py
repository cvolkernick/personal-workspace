"""Sound Money sleeve: 60/40 kept, GLDM inside the 40, auto-executed since 2026-11-24 (underweight_first)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury import fund_manager as fm
from treasury import fund_manager_broker as broker

NOW = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)


def _policy():
    return json.loads((ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8"))


class TestSoundMoneyConfig(unittest.TestCase):
    def test_whole_fund_weights_sum_to_100_and_sleeve_is_35_plus_5(self):
        policy = _policy()
        sleeve = policy["sleeves"]["btc_digital_credit"]
        self.assertEqual(sleeve["display_name"], "Sound Money & Digital Credit")
        self.assertEqual(policy["targets"]["btc_digital_credit_pct"], 0.4)
        self.assertEqual(policy["targets"]["stocks_growth_pct"], 0.6)
        comp = sleeve["composition"]
        self.assertEqual(comp["sleeve_whole_fund_pct"], 40)
        self.assertEqual(comp["btc_digital_credit_whole_fund_pct"], 35)
        self.assertEqual(comp["gldm_whole_fund_pct"], 5)
        self.assertEqual(comp["stocks_growth_whole_fund_pct"], 60)
        self.assertEqual(
            comp["btc_digital_credit_whole_fund_pct"] + comp["gldm_whole_fund_pct"],
            comp["sleeve_whole_fund_pct"],
        )
        table = policy["targets"]["whole_fund_targets_2026_11_24"]
        stocks = ["TSLA", "SPCX", "PLTR", "CRWD", "S", "CCJ", "GEV", "VRT", "OKLO", "MARA"]
        btc_dc = ["STRC", "SATA", "STRK", "SATS", "MSTR", "ASST", "BITA", "IBIT"]
        self.assertAlmostEqual(sum(table[s] for s in stocks), 60)
        self.assertAlmostEqual(sum(table[s] for s in btc_dc), 35)
        self.assertEqual(table["GLDM"], 5)
        self.assertEqual(table["IBIT"], 0)
        self.assertAlmostEqual(sum(table.values()), 100)
        weights = sleeve["new_money_weights"]
        legs = {k: weights[k] for k in ("STRC", "SATA", "STRK", "SATS", "MSTR", "ASST", "BITA")}
        self.assertEqual(legs, {"STRC": 0.25, "SATA": 0.25, "STRK": 0.15, "SATS": 0.1,
                                "MSTR": 0.09, "ASST": 0.08, "BITA": 0.08})
        self.assertEqual(weights["floating"], 0.0)
        self.assertAlmostEqual(sum(legs.values()) + weights["floating"], 1.0)
        # Live SATS weight, not only the ruled block. Whole-fund SATS is 0.10 * 35.
        self.assertEqual(table["SATS"], 3.5)
        gldm = sleeve["gldm"]
        self.assertEqual(gldm["symbol"], "GLDM")
        self.assertTrue(gldm["auto_execute"])
        self.assertFalse(gldm["requires_per_buy_approval"])
        self.assertTrue(gldm["buy_only"])
        self.assertTrue(gldm["never_sell"])
        allow = policy["execution"]["broker_adapter"]["symbol_allowlist"]
        self.assertIn("GLDM", allow)
        self.assertFalse(policy["theme_buy_hold"]["active"])
        self.assertEqual(policy["theme_buy_hold"]["themes"], ["ai_defense", "energy"])
        self.assertEqual(policy["targets"]["deploy"]["mode"], "underweight_first")
        self.assertFalse(policy["targets"]["deploy"]["per_buy_user_confirm"])
        share = json.loads((ROOT / "investment" / "consider_share.json").read_text(encoding="utf-8"))
        self.assertEqual(share["sound_money"]["display_name"], "Sound Money & Digital Credit")
        self.assertIn("bc0bb821", share["notes"] + share["sound_money"]["notes"] + share["basis_note"])
        self.assertEqual(sum(share["pins"].values()), 100)
        self.assertEqual(sum(share["sound_money"]["whole_fund_pct"].values()), 40)

    def test_gldm_holding_counts_inside_the_40(self):
        policy = _policy()
        rh = {
            "agentic": {
                "cash": 0,
                "buying_power": 0,
                "total_value": 100,
                "positions": [
                    {"symbol": "TSLA", "quantity": 1, "last_price": 60},
                    {"symbol": "STRC", "quantity": 1, "last_price": 35},
                    {"symbol": "GLDM", "quantity": 1, "last_price": 5},
                ],
            }
        }
        analysis = fm.analyze_agentic_book(rh, policy)
        self.assertAlmostEqual(analysis["sleeve_market_value"]["btc_digital_credit"], 40.0, places=4)
        self.assertAlmostEqual(analysis["sleeve_market_value"]["stocks_growth"], 60.0, places=4)
        self.assertAlmostEqual(analysis["weights_of_deployed"]["btc_digital_credit"], 0.4, places=4)
        cfg, err = broker.load_adapter_config()
        self.assertEqual(err, "")
        values, unpriced = broker.book_values(
            {"agentic": rh["agentic"]},
            cfg,
            {},
        )
        self.assertEqual(unpriced, [])
        self.assertEqual(values["btc"], Decimal("40"))
        self.assertEqual(values["other"], Decimal("0"))


class TestGldmAutoExecute(unittest.TestCase):
    """Chris 2026-11-24 (card t7739s171722): GLDM auto-executes; no pending-approval row."""

    def test_underweight_gldm_is_a_market_buy_not_a_pending_row(self):
        policy = _policy()
        acct = policy["account"]["account_number"]
        held = {"TSLA": 40, "SPCX": 40, "PLTR": 10, "CRWD": 5, "S": 5, "CCJ": 8, "GEV": 4,
                "VRT": 4, "OKLO": 2, "MARA": 2, "STRC": 70}
        positions = [
            {"symbol": sym, "quantity": "1", "last_price": str(v), "average_buy_price": str(v)}
            for sym, v in held.items()
        ]
        cash = 10.0
        snapshot = {
            "agentic": {
                "account_number": acct,
                "cash": cash,
                "buying_power": cash,
                "unleveraged_buying_power": cash,
                "positions": positions,
                "total_value": round(sum(held.values()) + cash, 4),
                "as_of": (NOW - timedelta(hours=1)).isoformat(),
            }
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            policy_path = root / "fund_manager.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            pending = root / "pending.json"
            os.environ["FM_PENDING_APPROVALS_PATH"] = str(pending)
            os.environ["FM_DECISIONS_PATH"] = str(root / "decisions.jsonl")

            class _Quotes:
                def get_quotes(self, symbols):
                    return {s: 20 for s in symbols}

                def get_recent_orders(self, *_a, **_k):
                    return []

            try:
                orders, info = broker.plan_intents(
                    [{"symbol": "STRC", "side": "buy", "notional_usd": 10, "purpose": "deploy"}],
                    snapshot=snapshot,
                    policy_path=policy_path,
                    ledger_path=root / "ledger.json",
                    now=NOW,
                    persist=True,
                    client=_Quotes(),
                    pending_path=pending,
                )
            finally:
                os.environ.pop("FM_PENDING_APPROVALS_PATH", None)
                os.environ.pop("FM_DECISIONS_PATH", None)
            pending_exists = pending.exists()
            stored = json.loads(pending.read_text(encoding="utf-8")) if pending_exists else {"entries": []}
        self.assertNotIn(info.get("status"), ("plan_error", "not_submitted_adapter_config"), info)
        self.assertTrue(orders, info)
        self.assertTrue(all(o.get("side") == "buy" for o in orders))
        by_sym = {o["symbol"]: float(o["notional_usd"]) for o in orders}
        self.assertIn("GLDM", by_sym, info)
        self.assertAlmostEqual(sum(by_sym.values()), 10.0, places=2)
        self.assertNotIn("IBIT", by_sym)
        gldm_pending = [r for r in (info.get("pending_approvals") or []) if r.get("symbol") == "GLDM"]
        self.assertEqual(gldm_pending, [])
        self.assertEqual([e for e in stored.get("entries", []) if e.get("symbol") == "GLDM"], [])
        self.assertNotIn("sell", json.dumps(orders).lower())


class TestSleeveLabels(unittest.TestCase):
    def test_fcc_labels_use_the_display_name(self):
        fcc = ROOT / "financial-command"
        for rel in ("index.html", "position.html", "watchlist.html", "bias-spectrum.html"):
            text = (fcc / rel).read_text(encoding="utf-8")
            self.assertTrue(
                "Sound Money & Digital Credit" in text or "Sound Money &amp; Digital Credit" in text,
                rel,
            )
        index = (fcc / "index.html").read_text(encoding="utf-8")
        self.assertNotIn(">BTC+</", index)
        self.assertNotIn('? "BTC+"', index)
        self.assertNotIn("BTC &amp; digital credit", index)
        self.assertNotIn("BTC & digital credit sleeve", index)


if __name__ == "__main__":
    unittest.main()
