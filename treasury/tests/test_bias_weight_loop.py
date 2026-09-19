"""Bias-weighting loop (#768): small apply, big/pin/sleeve stage, override."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.bias_spectrum import build_bias_spectrum  # noqa: E402
from treasury.bias_weight_loop import (  # noqa: E402
    LOCKED_GUARDRAILS,
    classify_proposals,
    committee_debate,
    parse_digest_proposals,
    pins_from,
    run_assessment,
)

DIGEST = """
# Agentic fund developments digest — 2026-09-18

## Digital credit complex (~40% sleeve)

- **STRC** — quiet. Nothing new.
  - Allocation relevance: **neutral** — nothing new.
- **SATA** — new preferred-share facility disclosed.
  - Allocation relevance: **up** — material structure/yield development.
  [https://example.test/sata](https://example.test/sata)

## Stocks / AI (~60% sleeve)

- **TSLA** (bias pin 15) — factory tax break.
  - Allocation relevance: **up** — capex support.
  [https://example.test/tsla](https://example.test/tsla)
- **NVDA** — quiet; no material news.

## Private watchlist (not investable)

- **BOOM** — huge funding round.
  - Allocation relevance: **up** — should not reach the loop.
"""


def _share() -> dict:
    return {
        "schema": "fcc_consider_share_stamps_v0",
        "pins": {"TSLA": 15.0, "SPCX": 15.0},
        "reallocate": {"BE": ["TSLA", "SPCX"]},
        "guardrails": dict(LOCKED_GUARDRAILS),
        "loop_adjustments": {},
    }


class TestParseDigest(unittest.TestCase):
    def test_up_becomes_plus_one_skips_neutral_and_private(self) -> None:
        rows = parse_digest_proposals(DIGEST, source="digest:test")
        by = {r["symbol"]: r for r in rows}
        self.assertIn("SATA", by)
        self.assertEqual(by["SATA"]["delta"], 1.0)
        self.assertTrue(by["SATA"]["material"])
        self.assertIn("example.test/sata", by["SATA"]["citation"])
        self.assertIn("TSLA", by)
        self.assertNotIn("STRC", by)
        self.assertNotIn("NVDA", by)
        self.assertNotIn("BOOM", by)


class TestGuardrails(unittest.TestCase):
    def test_small_residual_applies_pin_and_sleeve_stage(self) -> None:
        classified = classify_proposals(
            [
                {
                    "symbol": "SATA",
                    "delta": 1,
                    "kind": "residual",
                    "material": True,
                    "rationale": "yield facility",
                    "citation": "https://example.test/sata",
                },
                {
                    "symbol": "TSLA",
                    "delta": 1,
                    "kind": "residual",
                    "material": True,
                    "rationale": "tax break",
                    "citation": "https://example.test/tsla",
                },
                {
                    "symbol": "btc_digital_credit_pct",
                    "delta": 0.02,
                    "kind": "sleeve",
                    "material": True,
                    "rationale": "move 40/60",
                },
                {
                    "symbol": "STRC",
                    "delta": 0,
                    "kind": "residual",
                    "material": False,
                    "rationale": "quiet",
                },
            ],
            share=_share(),
        )
        apply_syms = {r["symbol"] for r in classified["apply"]}
        stage_syms = {r["symbol"] for r in classified["stage"]}
        skip_syms = {r["symbol"] for r in classified["skip"]}
        self.assertEqual(apply_syms, {"SATA"})
        self.assertIn("TSLA", stage_syms)
        self.assertIn("BTC_DIGITAL_CREDIT_PCT", stage_syms)
        self.assertIn("STRC", skip_syms)

    def test_oversize_single_and_total_stage(self) -> None:
        classified = classify_proposals(
            [
                {
                    "symbol": "SATA",
                    "delta": 3,
                    "kind": "residual",
                    "material": True,
                    "rationale": "too big",
                    "citation": "https://example.test/x",
                }
            ],
            share=_share(),
        )
        self.assertEqual(classified["apply"], [])
        self.assertEqual(classified["stage"][0]["stage_reason"], "single_symbol_over_max")

        classified = classify_proposals(
            [
                {
                    "symbol": s,
                    "delta": 2,
                    "kind": "residual",
                    "material": True,
                    "rationale": "batch",
                    "citation": "https://example.test/x",
                }
                for s in ("SATA", "STRC", "MARA")
            ],
            share=_share(),
        )
        self.assertEqual(classified["apply"], [])
        self.assertTrue(
            all(r["stage_reason"] == "total_abs_over_max" for r in classified["stage"])
        )


class TestCommitteeAndApply(unittest.TestCase):
    def test_debate_records_dissent(self) -> None:
        classified = classify_proposals(
            [
                {
                    "symbol": "SATA",
                    "delta": 1,
                    "kind": "residual",
                    "material": True,
                    "rationale": "up",
                    "citation": "https://example.test/sata",
                },
                {
                    "symbol": "TSLA",
                    "delta": 1,
                    "kind": "residual",
                    "material": True,
                    "rationale": "up",
                    "citation": "https://example.test/tsla",
                },
            ],
            share=_share(),
        )
        debate = committee_debate(classified)
        self.assertEqual(debate["roles"]["scout"]["vote"], "observe")
        self.assertEqual(debate["roles"]["critic"]["vote"], "dissent")
        self.assertTrue(debate["disagreement"])
        self.assertTrue(any("TSLA" in d for d in debate["roles"]["critic"]["dissent"]))

    def test_apply_writes_adjustments_not_pins_or_sleeves(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            share_path = tmp / "consider_share.json"
            staged_path = tmp / "staged.json"
            journal_path = tmp / "journal.md"
            jsonl_path = tmp / "loop.jsonl"
            share = _share()
            share_path.write_text(json.dumps(share), encoding="utf-8")
            sleeve_before = json.loads(
                (ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8")
            )["targets"]
            entry = run_assessment(
                digest_text=DIGEST,
                digest_source="digest:test",
                share=share,
                share_path=share_path,
                staged_path=staged_path,
                journal_path=journal_path,
                jsonl_path=jsonl_path,
                write=True,
            )
            written = json.loads(share_path.read_text(encoding="utf-8"))
            self.assertEqual(sorted(written["pins"]), ["SPCX", "TSLA"])
            self.assertAlmostEqual(float(written["pins"]["TSLA"]), 15.0)
            self.assertAlmostEqual(float(written["pins"]["SPCX"]), 15.0)
            self.assertAlmostEqual(float(written["loop_adjustments"]["SATA"]), 1.0)
            self.assertNotIn("TSLA", written.get("loop_adjustments") or {})
            staged = json.loads(staged_path.read_text(encoding="utf-8"))
            self.assertTrue(any(p["symbol"] == "TSLA" for p in staged["pending"]))
            journal = journal_path.read_text(encoding="utf-8")
            self.assertIn("Committee minutes", journal)
            self.assertIn("**scout:**", journal)
            self.assertIn("**critic:**", journal)
            self.assertIn("SATA", journal)
            self.assertTrue(entry["sleeve_targets_untouched"])
            live_targets = json.loads(
                (ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8")
            )["targets"]
            self.assertEqual(
                live_targets["btc_digital_credit_pct"],
                sleeve_before["btc_digital_credit_pct"],
            )
            self.assertEqual(
                live_targets["stocks_growth_pct"], sleeve_before["stocks_growth_pct"]
            )

    def test_quiet_day_one_line(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            entry = run_assessment(
                proposals=[],
                share=_share(),
                share_path=tmp / "share.json",
                staged_path=tmp / "staged.json",
                journal_path=tmp / "journal.md",
                jsonl_path=tmp / "loop.jsonl",
                write=True,
            )
            self.assertIn("quiet", entry["brief"].lower())
            self.assertIn("No change", tmp.joinpath("journal.md").read_text())

    def test_chairman_override_applies_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            share_path = tmp / "consider_share.json"
            share = _share()
            share_path.write_text(json.dumps(share), encoding="utf-8")
            entry = run_assessment(
                proposals=[],
                share=share,
                share_path=share_path,
                staged_path=tmp / "staged.json",
                journal_path=tmp / "journal.md",
                jsonl_path=tmp / "loop.jsonl",
                write=True,
                override={
                    "symbol": "SATA",
                    "delta": 1,
                    "why": "Chairman 2026-09-15 SATA-above-STRC",
                },
            )
            written = json.loads(share_path.read_text(encoding="utf-8"))
            self.assertAlmostEqual(float(written["loop_adjustments"]["SATA"]), 1.0)
            self.assertEqual(sorted(written["pins"]), ["SPCX", "TSLA"])
            self.assertEqual(entry["kind"], "override")
            journal = tmp.joinpath("journal.md").read_text(encoding="utf-8")
            self.assertIn("**Override:** Chairman SATA", journal)
            self.assertIn("SATA-above-STRC", journal)
            self.assertIn("bias_weight_loop", journal)
            policy = {
                "as_of": "2026-09-01",
                "targets": {
                    "btc_digital_credit_pct": 0.4,
                    "stocks_growth_pct": 0.6,
                    "band_pct": 0.05,
                },
                "allowlist": {"core": ["MSTR", "STRC", "SATA", "TSLA"]},
                "sleeves": {
                    "btc_digital_credit": {
                        "target_pct": 0.4,
                        "symbols": ["MSTR", "STRC", "SATA"],
                        "watchlist_symbols": [],
                        "sub_sleeves": {
                            "digital_credit": {"preferred_core": ["STRC", "SATA"]},
                        },
                    },
                    "stocks_growth": {
                        "target_pct": 0.6,
                        "symbols": ["TSLA"],
                        "watchlist_symbols": [],
                    },
                },
            }
            after = build_bias_spectrum(
                fund_manager={
                    "ok": True,
                    "as_of": "2026-09-01T17:00:00+00:00",
                    "analysis": {
                        "ok": True,
                        "nav_usd": 250.0,
                        "equity_market_value_usd": 200.0,
                        "weights_of_deployed": {
                            "btc_digital_credit": 0.4,
                            "stocks_growth": 0.6,
                        },
                        "targets": policy["targets"],
                        "positions": [],
                    },
                },
                treasury={},
                policy=policy,
                watchlist={"entries": []},
                consider_share_stamps=written,
            )
            by = {c["symbol"]: c for c in after["chips"]}
            self.assertGreater(by["SATA"]["weight_pct"], by["STRC"]["weight_pct"])
            self.assertAlmostEqual(after["targets"]["btc_digital_credit_pct"], 0.4)
            self.assertAlmostEqual(after["targets"]["stocks_growth_pct"], 0.6)


class TestPinsSoT(unittest.TestCase):
    def test_live_pins_unchanged_by_module_constants(self) -> None:
        live = json.loads(
            (ROOT / "investment" / "consider_share.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sorted(pins_from(live)), ["SPCX", "TSLA"])
        g = live.get("guardrails") or {}
        self.assertEqual(g.get("locked_by"), "Chairman")
        self.assertEqual(float(g.get("single_symbol_max_auto")), 2.0)
        self.assertEqual(float(g.get("total_abs_max_auto")), 5.0)


if __name__ == "__main__":
    unittest.main()
