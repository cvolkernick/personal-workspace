"""Solstice JR APY on solana snapshot: public/docs only, no invent, no scrape."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.interest_spectrum import (  # noqa: E402
    JR_STRCUSX_ID,
    JR_TARGET_LABEL,
    JR_TARGET_PCT,
    build_interest_spectrum,
    rates_are_honest,
)
from treasury.solana_sync import (  # noqa: E402
    JR_STRCUSX_MINT,
    USDC_MINT,
    WSOL_MINT,
    fetch_solana,
    normalize_solana_book,
    write_solana_snapshot,
)
from treasury.solstice_jr_sync import (  # noqa: E402
    APY_FIELD,
    DOCS_API_URL,
    DOCS_TARGET_APY,
    DOCS_YIELD_APY_URL,
    SOURCE_BLOCKER,
    attach_solstice_jr_apy,
    empty_solstice_jr_fields,
    fetch_solstice_jr_apy,
    parse_solstice_jr_apy,
    write_solstice_jr_fields,
)

LIVE_FRACTION = 0.184


def _verified_payload(apy: float = LIVE_FRACTION) -> dict:
    return {"jr_strcusx_apy": apy, "product": "JR-strcUSX"}


class TestParseSolsticeJr(unittest.TestCase):
    def test_verified_json_field(self) -> None:
        apy, err, meta = parse_solstice_jr_apy(_verified_payload())
        self.assertIsNone(err)
        self.assertAlmostEqual(apy, LIVE_FRACTION)
        self.assertEqual(meta.get("field"), "jr_strcusx_apy")
        self.assertNotAlmostEqual(apy, DOCS_TARGET_APY)

    def test_rejects_html_scrape(self) -> None:
        apy, err, _ = parse_solstice_jr_apy(
            "<!DOCTYPE html><html><body>JR-strcUSX ~20% APY</body></html>"
        )
        self.assertIsNone(apy)
        self.assertIn("HTML", err or "")

    def test_rejects_missing_and_generic_apy(self) -> None:
        apy, err, _ = parse_solstice_jr_apy({"apy": 0.184, "avgNetApy": 0.184})
        self.assertIsNone(apy)
        self.assertIn("no verified jr apy field", (err or "").lower())
        apy, err, _ = parse_solstice_jr_apy({"jr_strcusx": 3.3, "jr_strcusx_usd": 3.4})
        self.assertIsNone(apy)

    def test_empty_fields_are_none_not_docs_target(self) -> None:
        row = empty_solstice_jr_fields()
        self.assertIsNone(row[APY_FIELD])
        self.assertIsNone(row["solstice_apy"])
        self.assertIsNone(row["strcusx_apy"])
        self.assertNotAlmostEqual(row.get(APY_FIELD) or -1, DOCS_TARGET_APY)
        self.assertIn("source blocked", row["jr_strcusx_apy_error"])
        self.assertIn(DOCS_YIELD_APY_URL, SOURCE_BLOCKER)
        self.assertIn(DOCS_API_URL, SOURCE_BLOCKER)


class TestFetchSoftFail(unittest.TestCase):
    def test_no_payload_is_blocked_does_not_invent(self) -> None:
        row, err = fetch_solstice_jr_apy()
        self.assertIsNone(row)
        self.assertIn("source blocked", (err or "").lower())
        self.assertIn("partner", (err or "").lower())
        self.assertIn("HTML", err or "")

    def test_verified_payload_does_not_write_docs_target(self) -> None:
        row, err = fetch_solstice_jr_apy(payload=_verified_payload())
        self.assertIsNone(err)
        assert row is not None
        self.assertAlmostEqual(row[APY_FIELD], LIVE_FRACTION)
        self.assertAlmostEqual(row["solstice_apy"], LIVE_FRACTION)
        self.assertNotAlmostEqual(row[APY_FIELD], DOCS_TARGET_APY)

    def test_html_payload_soft_fails(self) -> None:
        row, err = fetch_solstice_jr_apy(
            payload="<html>attestation.solstice.finance 20%</html>"
        )
        self.assertIsNone(row)
        self.assertIn("HTML", err or "")


class TestAttachAndSpectrum(unittest.TestCase):
    def test_no_live_spectrum_docs_target_no_notional(self) -> None:
        book = attach_solstice_jr_apy(
            {"jr_strcusx": 3.317128, "jr_strcusx_usd": 3.40},
            prior=None,
        )
        self.assertIsNone(book[APY_FIELD])
        self.assertIsNone(book["solstice_apy"])
        self.assertAlmostEqual(book["jr_strcusx"], 3.317128)
        payload = build_interest_spectrum(
            treasury={"snapshot": {"solana": book}, "evaluation": {"inputs": {}}},
            config={},
            x_money={},
            solana=book,
        )
        jr = {c["id"]: c for c in payload["chips"]}[JR_STRCUSX_ID]
        self.assertAlmostEqual(jr["rate_pct"], JR_TARGET_PCT)
        self.assertEqual(jr["rate_label"], JR_TARGET_LABEL)
        self.assertEqual(jr["source"], "docs_target")
        self.assertNotIn("notional", jr)
        self.assertTrue(rates_are_honest(payload))

    def test_live_field_flips_spectrum_to_books(self) -> None:
        book = attach_solstice_jr_apy(
            {"jr_strcusx": 3.3},
            payload=_verified_payload(0.184),
        )
        self.assertAlmostEqual(book[APY_FIELD], 0.184)
        self.assertAlmostEqual(book["solstice_apy"], 0.184)
        payload = build_interest_spectrum(
            treasury={"snapshot": {"solana": book}},
            config={},
            x_money={},
            solana=book,
        )
        jr = {c["id"]: c for c in payload["chips"]}[JR_STRCUSX_ID]
        self.assertAlmostEqual(jr["rate_pct"], 18.4)
        self.assertEqual(jr["source"], "books")
        self.assertFalse(jr["approx"])
        self.assertNotIn("notional", jr)
        self.assertNotIn("rate_label", jr)
        self.assertTrue(rates_are_honest(payload))

    def test_soft_fail_keeps_prior_does_not_invent_docs_target(self) -> None:
        prior = {
            APY_FIELD: 0.176,
            "solstice_apy": 0.176,
            "jr_strcusx_apy_source": "solstice_docs_json",
        }
        book = attach_solstice_jr_apy({"jr_strcusx": 1.0}, prior=prior)
        self.assertAlmostEqual(book[APY_FIELD], 0.176)
        self.assertNotAlmostEqual(book[APY_FIELD], DOCS_TARGET_APY)
        self.assertIn("source blocked", book.get("jr_strcusx_apy_error") or "")

    def test_docs_target_prior_is_not_promoted_to_books(self) -> None:
        prior = {
            APY_FIELD: DOCS_TARGET_APY,
            "solstice_apy": DOCS_TARGET_APY,
            "jr_strcusx_apy_source": "docs_target",
        }
        book = attach_solstice_jr_apy({"jr_strcusx": 1.0}, prior=prior)
        self.assertIsNone(book[APY_FIELD])
        self.assertIsNone(book["solstice_apy"])

    def test_write_none_never_persists_docs_target(self) -> None:
        out = write_solstice_jr_fields({}, apy=None)
        self.assertIsNone(out[APY_FIELD])
        values = [out[k] for k in (APY_FIELD, "solstice_apy", "strcusx_apy")]
        self.assertTrue(all(v is None for v in values))


class TestSolanaSnapshotFields(unittest.TestCase):
    def test_normalize_wallet_does_not_invent_apy(self) -> None:
        book = normalize_solana_book(
            wallet="CzuRxF4H7qtcCbP37zcLu9DTgcHySmi8NcTsrS6W7bDm",
            sol_lamports=1_000_000,
            token_rows=[{"mint": JR_STRCUSX_MINT, "amount": 3.3, "decimals": 6}],
            prices={WSOL_MINT: 76.0, USDC_MINT: 1.0, JR_STRCUSX_MINT: 1.02},
            whitelist=[
                {"symbol": "SOL", "mint": WSOL_MINT, "role": "gas"},
                {"symbol": "USDC", "mint": USDC_MINT, "role": "onchain_stable"},
                {"symbol": "JR-strcUSX", "mint": JR_STRCUSX_MINT, "role": "dc_credit_parlay"},
            ],
        )
        self.assertIn(APY_FIELD, book)
        self.assertIsNone(book[APY_FIELD])
        self.assertIsNone(book["solstice_apy"])
        self.assertAlmostEqual(book["jr_strcusx"], 3.3)
        payload = build_interest_spectrum(
            treasury={"snapshot": {"solana": book}},
            config={},
            x_money={},
            solana=book,
        )
        jr = {c["id"]: c for c in payload["chips"]}[JR_STRCUSX_ID]
        self.assertEqual(jr["source"], "docs_target")
        self.assertAlmostEqual(jr["rate_pct"], 20.0)
        self.assertNotIn("notional", jr)

    def test_live_refresh_preserves_prior_quote_not_docs_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sol.json"
            write_solana_snapshot(
                {
                    "source": "live",
                    "jr_strcusx": 3.3,
                    APY_FIELD: 0.191,
                    "solstice_apy": 0.191,
                    "jr_strcusx_apy_source": "solstice_docs_json",
                    "book_usd": 3.4,
                    "sol": 0,
                },
                path=p,
            )
            live_book = normalize_solana_book(
                wallet="w",
                sol_lamports=0,
                token_rows=[],
                prices={},
                whitelist=[],
            )
            with mock.patch(
                "treasury.solana_sync.fetch_solana_live",
                return_value=(live_book, None),
            ):
                r = fetch_solana(prefer_live=True, snapshot_path=p)
            self.assertAlmostEqual(r[APY_FIELD], 0.191)
            self.assertNotAlmostEqual(r[APY_FIELD], DOCS_TARGET_APY)
            saved = json.loads(p.read_text(encoding="utf-8"))
            self.assertAlmostEqual(saved[APY_FIELD], 0.191)

    def test_offline_without_quote_stays_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sol.json"
            write_solana_snapshot(
                {"source": "live", "jr_strcusx": 1.0, "book_usd": 1.0, "sol": 0},
                path=p,
            )
            r = fetch_solana(prefer_live=False, snapshot_path=p)
            self.assertIsNone(r.get(APY_FIELD))
            self.assertIsNone(r.get("solstice_apy"))
            self.assertIn("source blocked", r.get("jr_strcusx_apy_error") or "")


if __name__ == "__main__":
    unittest.main()
