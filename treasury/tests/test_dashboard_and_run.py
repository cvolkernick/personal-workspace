"""Structural + entry-point tests for dashboard and run_treasury."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestDashboardArtifact(unittest.TestCase):
    def test_index_has_dual_venue_and_actions(self):
        html = (ROOT / "financial-command" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Financial Command Center", html)
        self.assertIn("Do now", html)
        self.assertIn("Next to pay", html)
        self.assertIn("glance-next-pay", html)
        self.assertIn("At a glance", html)
        self.assertIn("kpi-grid", html)
        self.assertIn("Cash &amp; credit", html)
        self.assertIn("Pay plan", html)
        self.assertIn("bill-row", html)
        self.assertIn("dueUrgency", html)
        self.assertIn("Brokerage", html)
        self.assertIn("Actual spend", html)
        self.assertIn("Capital targets", html)
        self.assertIn("Settings", html)
        self.assertIn("m-vault-apy", html)
        self.assertIn("Coinbase One Morpho HY product APY override", html)
        self.assertIn("Vault GraphQL avgNetApy is vault reference only", html)
        self.assertIn("not the Coinbase One in-app product rate", html)
        self.assertIn("m-var-apr", html)
        self.assertIn("Morpho borrow APR override", html)
        self.assertIn("m-usdg-apy", html)
        self.assertIn("USDG Earn APY override", html)
        self.assertIn("Interest Spectrum", html)
        self.assertIn("interest-spectrum.html", html)
        self.assertIn("/api/refresh", html)
        self.assertIn("fillGlanceNextPay", html)
        self.assertIn("actorLabel", html)
        ico = html.find('href="favicon.ico')
        svg = html.find('href="favicon.svg')
        self.assertGreater(ico, 0)
        self.assertGreater(svg, ico)
        self.assertGreater(len(html), 8000)

    def test_action_items_doc(self):
        p = ROOT / "investment" / "treasury-action-items.md"
        text = p.read_text(encoding="utf-8")
        for needle in (
            "loan protection",
            "autopay",
            "bridge",
            "DCA",
            "BP floor",
        ):
            self.assertIn(needle.lower(), text.lower())

    def test_capital_flows_model(self):
        html = (ROOT / "financial-command" / "capital-flows.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Capital Flows", html)
        self.assertIn("/api/capital-flows", html)
        model_path = ROOT / "investment" / "capital_flows.json"
        self.assertTrue(
            model_path.is_file(),
            "investment/capital_flows.json must ship with capital-flows.html",
        )
        model = json.loads(model_path.read_text(encoding="utf-8"))
        ids = [s["id"] for s in model["income_sources"]]
        self.assertIn("lyft", ids)
        self.assertIn("turo", ids)
        self.assertIn("asics", ids)
        ch_ids = [c["id"] for c in model["channels"]]
        self.assertIn("jr_strcusx", ch_ids)
        le = next(
            c for c in model["layout"]["columns"] if c["id"] == "liquidity_engine"
        )
        self.assertEqual(le["ids"][-1], "jr_strcusx")
        pairs = {(e["from"], e["to"]) for e in model["edges"]}
        self.assertIn(("margin", "jr_strcusx"), pairs)
        self.assertIn(("jr_strcusx", "margin"), pairs)
        self.assertGreater(len(model.get("edges") or []), 5)


class TestRunTreasuryEntry(unittest.TestCase):
    def test_run_offline_writes_evaluation(self):
        out = ROOT / "treasury" / "snapshots" / "treasury_test_out.json"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "treasury" / "run_treasury.py"), "--offline", "--out", str(out)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(out.is_file())
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("evaluation", data)
        self.assertIn("snapshot", data)
        self.assertIn("stress", data["evaluation"])
        self.assertIn("actions", data["evaluation"])
        self.assertIn("overall", data["evaluation"]["stress"])
        # Dashboard copy
        dash = ROOT / "financial-command" / "treasury_latest.json"
        self.assertTrue(dash.is_file())
        dash_data = json.loads(dash.read_text(encoding="utf-8"))
        self.assertTrue(dash_data["evaluation"]["actions"] is not None)


class TestFccSidecarAttach(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "fcc_server", ROOT / "financial-command" / "server.py"
        )
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_attach_x_money_fills_omitted_snapshot(self):
        import tempfile

        sidecar = {
            "source": "ynab",
            "as_of": "2026-08-17T18:46:45.960996+00:00",
            "cash": 178.14,
            "account_name": "Checking – 2201",
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x_money_latest.json"
            path.write_text(json.dumps(sidecar), encoding="utf-8")
            prev = self.mod.XM_SNAPSHOT
            self.mod.XM_SNAPSHOT = path
            try:
                data = {
                    "snapshot": {
                        "as_of": "2026-08-17T18:46:45+00:00",
                        "one_card": {
                            "source": "ynab",
                            "as_of": "2026-08-17T18:46:45+00:00",
                        },
                    },
                    "evaluation": {
                        "data_quality": {"sources": {"one_card": "ynab"}},
                        "inputs": {},
                    },
                }
                out = self.mod._attach_x_money(data)
            finally:
                self.mod.XM_SNAPSHOT = prev
        xm = (out.get("snapshot") or {}).get("x_money") or {}
        self.assertEqual(xm.get("source"), "ynab")
        self.assertAlmostEqual(xm.get("cash"), 178.14)
        srcs = ((out.get("evaluation") or {}).get("data_quality") or {}).get("sources")
        self.assertEqual(srcs.get("x_money"), "ynab")
        self.assertAlmostEqual(
            ((out.get("evaluation") or {}).get("inputs") or {}).get("x_money_cash"),
            178.14,
        )

    def test_attach_x_money_keeps_existing(self):
        data = {
            "snapshot": {
                "x_money": {
                    "source": "ynab",
                    "as_of": "2026-08-17T19:47:36+00:00",
                    "cash": 12.34,
                }
            }
        }
        out = self.mod._attach_x_money(data)
        self.assertAlmostEqual(out["snapshot"]["x_money"]["cash"], 12.34)

    def test_favicon_ico_present(self):
        ico = ROOT / "financial-command" / "favicon.ico"
        self.assertTrue(ico.is_file())
        self.assertGreater(ico.stat().st_size, 64)
        self.assertTrue((ROOT / "financial-command" / "favicon-32.png").is_file())

    def test_capital_flows_payload_ok(self):
        data = self.mod._capital_flows_payload()
        self.assertTrue(data.get("ok"), data.get("error"))
        self.assertIn("income_sources", data)
        self.assertIn("channels", data)
        self.assertGreater(len(data.get("edges") or []), 5)


if __name__ == "__main__":
    unittest.main()
