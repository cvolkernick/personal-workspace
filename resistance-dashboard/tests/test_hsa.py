"""FitDash HSA section (#834) — SOUND HSA, IRS year-keyed limits, CSV v1."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.hsa import HDHP_COPY, build_hsa_view
from rt_dashboard.hsa_store import (
    DEFAULT_IRS_LIMITS,
    apply_csv,
    apply_settings,
    contribution_limit_usd,
    csv_template,
    empty_store,
    irs_limits_for,
    load_hsa,
    normalize_store,
    save_hsa,
    upsert_entry,
)
from rt_dashboard.google_health import parse_steps_rollup, parse_active_zone_minutes_rollup
from rt_dashboard.models import StepSample


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")
UTIL = (ROOT / "api" / "workout" / "_util.py").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")
GH = (ROOT / "rt_dashboard" / "google_health.py").read_text(encoding="utf-8")


class TestIrsLimitsYearKeyed(unittest.TestCase):
    def test_2026_defaults(self):
        row = DEFAULT_IRS_LIMITS["2026"]
        self.assertEqual(row["self_only"], 4400)
        self.assertEqual(row["family"], 8750)
        self.assertEqual(row["catch_up"], 1000)
        store = empty_store()
        self.assertEqual(irs_limits_for(store, "2026")["self_only"], 4400)
        self.assertEqual(contribution_limit_usd(store, "2026"), 4400)
        store["coverage"] = "family"
        store["catch_up_55"] = True
        self.assertEqual(contribution_limit_usd(store, "2026"), 9750)

    def test_missing_year_does_not_borrow_2026(self):
        store = empty_store()
        self.assertIsNone(irs_limits_for(store, "2027"))
        self.assertIsNone(contribution_limit_usd(store, "2027"))
        view = build_hsa_view(
            apply_settings(store, {"eligibility": "eligible"}),
            as_of="2027-06-01",
        )
        self.assertEqual(view["contribution_pace"]["status"], "limits_missing")
        self.assertIsNone(view["contribution_pace"]["limit_usd"])
        self.assertIn("2027", view["contribution_pace"]["message"])

    def test_user_override_for_year(self):
        store = apply_settings(
            empty_store(),
            {
                "eligibility": "eligible",
                "irs_limits": {"2027": {"self_only": 4500, "family": 9000, "catch_up": 1000}},
            },
        )
        self.assertEqual(contribution_limit_usd(store, "2027"), 4500)
        self.assertEqual(contribution_limit_usd(store, "2026"), 4400)


class TestIneligibleGate(unittest.TestCase):
    def test_unknown_does_not_render_zero_position(self):
        view = build_hsa_view(empty_store(), as_of="2026-09-19")
        self.assertFalse(view["eligible"])
        self.assertIsNone(view["position"])
        self.assertIsNone(view["contribution_pace"])
        self.assertIsNone(view["shoebox"])
        self.assertEqual(view["empty_reason"], "eligibility_unknown")
        self.assertIn("high-deductible", view["hdhp_copy"])
        self.assertEqual(view["hdhp_copy"], HDHP_COPY)

    def test_ineligible_same_gate(self):
        store = apply_settings(empty_store(), {"eligibility": "ineligible"})
        view = build_hsa_view(store, as_of="2026-09-19")
        self.assertFalse(view["eligible"])
        self.assertIsNone(view["position"])
        self.assertEqual(view["empty_reason"], "ineligible")


class TestEligiblePositionAndPace(unittest.TestCase):
    def test_position_usd_and_pace(self):
        store = apply_settings(
            empty_store(),
            {
                "eligibility": "eligible",
                "coverage": "self_only",
                "position": {
                    "btc_sats": 100_000_000,
                    "cash_usd": 500,
                    "btc_usd_mark": 100000,
                    "as_of": "2026-09-19",
                },
            },
        )
        store = upsert_entry(
            store,
            "contribution",
            {"date": "2026-01-15", "amount_usd": 1100, "id": "c1"},
        )
        view = build_hsa_view(store, as_of="2026-07-02")
        self.assertTrue(view["eligible"])
        self.assertEqual(view["position"]["btc_usd"], 100000.0)
        self.assertEqual(view["position"]["total_usd"], 100500.0)
        pace = view["contribution_pace"]
        self.assertEqual(pace["limit_usd"], 4400)
        self.assertEqual(pace["ytd_usd"], 1100.0)
        self.assertEqual(pace["status"], "ok")
        self.assertGreater(pace["expected_usd"], 0)
        self.assertLess(pace["pct_of_limit"], 100)

    def test_unmarked_btc_is_not_fake_usd(self):
        store = apply_settings(
            empty_store(),
            {
                "eligibility": "eligible",
                "position": {"btc_sats": 50_000, "cash_usd": 20},
            },
        )
        view = build_hsa_view(store, as_of="2026-09-19")
        self.assertTrue(view["position"]["unmarked_btc"])
        self.assertIsNone(view["position"]["btc_usd"])
        self.assertIsNone(view["position"]["total_usd"])


class TestShoeboxAndSpend(unittest.TestCase):
    def test_receipt_status_and_deductible(self):
        store = apply_settings(
            empty_store(),
            {"eligibility": "eligible", "hdhp_deductible_usd": 1700},
        )
        store = upsert_entry(
            store,
            "shoebox",
            {
                "id": "s1",
                "date": "2026-03-01",
                "amount_usd": 85,
                "category": "dental",
                "receipt": "on_file",
            },
        )
        store = upsert_entry(
            store,
            "spend",
            {
                "id": "m1",
                "date": "2026-03-01",
                "amount_usd": 85,
                "category": "dental",
                "counts_toward_deductible": True,
            },
        )
        view = build_hsa_view(store, as_of="2026-09-19")
        self.assertEqual(view["shoebox"]["on_file_usd"], 85.0)
        self.assertEqual(view["shoebox"]["total_usd"], 85.0)
        self.assertEqual(view["medical_spend"]["toward_deductible_usd"], 85.0)
        self.assertEqual(view["medical_spend"]["deductible_remaining_usd"], 1615.0)


class TestSatsForSteps(unittest.TestCase):
    def test_links_to_step_series(self):
        store = apply_settings(empty_store(), {"eligibility": "eligible"})
        store = upsert_entry(
            store,
            "reward",
            {"id": "r1", "date": "2026-09-18", "sats": 2100, "steps": 21000, "challenge": "21k"},
        )
        store = upsert_entry(
            store,
            "challenge",
            {
                "id": "ch1",
                "label": "21k",
                "goal_steps": 21000,
                "reward_sats": 2100,
                "start": "2026-09-13",
                "end": "2026-09-19",
            },
        )
        steps = [
            StepSample(date="2026-09-18", steps=8000),
            StepSample(date="2026-09-19", steps=14000),
        ]
        view = build_hsa_view(store, steps=steps, as_of="2026-09-19")
        sfs = view["sats_for_steps"]
        self.assertEqual(sfs["today_steps"], 14000)
        self.assertEqual(sfs["steps_7d"], 22000)
        self.assertEqual(sfs["sats_earned_ytd"], 2100)
        self.assertTrue(sfs["challenges"][0]["complete"])
        self.assertFalse(sfs["pending_steps"])

    def test_azm_parser_still_ignores_steps(self):
        payload = {
            "rollupDataPoints": [
                {
                    "civilStartTime": {"date": {"year": 2026, "month": 9, "day": 19}},
                    "steps": {"countSum": 8000},
                    "totalCalories": {"kcalSum": 2100},
                }
            ]
        }
        self.assertEqual(parse_active_zone_minutes_rollup(payload), [])
        self.assertEqual(parse_steps_rollup(payload)[0].steps, 8000)

    def test_steps_parser_honest_empty(self):
        self.assertEqual(parse_steps_rollup({}), [])
        self.assertEqual(parse_steps_rollup(None), [])  # type: ignore[arg-type]


class TestCsvImport(unittest.TestCase):
    def test_template_round_trip(self):
        store, counts = apply_csv(empty_store(), csv_template())
        self.assertGreaterEqual(counts["contributions"], 1)
        self.assertGreaterEqual(counts["shoebox"], 1)
        self.assertGreaterEqual(counts["medical_spend"], 1)
        self.assertGreaterEqual(counts["sats_rewards"], 1)
        self.assertEqual(store["shoebox"][0]["receipt"], "on_file")
        self.assertFalse(store["api"]["available"])


class TestDiskStore(unittest.TestCase):
    def test_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {"FITDASH_HSA_DIR": tmp, "VERCEL": "", "VERCEL_ENV": ""},
                clear=False,
            ):
                with mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False):
                    store = apply_settings(
                        empty_store(), {"eligibility": "eligible", "coverage": "family"}
                    )
                    saved = save_hsa(store, "user-1")
                    loaded = load_hsa("user-1")
                    self.assertEqual(loaded["eligibility"], "eligible")
                    self.assertEqual(loaded["coverage"], "family")
                    self.assertEqual(saved["storage"], "config")


class TestMarkupAndRoutes(unittest.TestCase):
    def test_more_tab_section(self):
        self.assertIn('id="hsa-section"', HTML)
        self.assertIn('data-m-panel="more"', HTML)
        self.assertLess(HTML.find("hsa-section"), HTML.find("labs-section"))
        self.assertGreater(HTML.find("hsa-section"), HTML.find("phase-barometer-targets"))
        self.assertIn("HDHP", HTML)
        self.assertIn("SOUND HSA", HTML)
        self.assertIn('id="hsa-csv-form"', HTML)
        self.assertIn("renderHsa", JS)
        self.assertIn('fetch("/api/hsa"', JS)
        self.assertIn("import_csv", JS)
        self.assertIn("sats_for_steps", JS)
        self.assertIn(".hsa-panel", CSS)

    def test_vercel_rewrite_no_new_function(self):
        self.assertIn("/api/hsa", VERCEL)
        self.assertIn("/api/dashboard?_r=hsa", VERCEL)
        self.assertNotIn("api/hsa.py", VERCEL)
        self.assertFalse((ROOT / "api" / "hsa.py").exists())
        self.assertIn('"hsa"', UTIL)
        self.assertIn("/api/hsa", SERVER)

    def test_cache_bump(self):
        self.assertNotIn("/app.js?v=hsa-834-1", SW)
        self.assertIn("/app.js?v=novel-staples-858-1", HTML)
        self.assertIn("/app.js?v=novel-staples-858-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v112"', SW)
        self.assertNotIn("nutrition-day-828-1", HTML)
        self.assertNotIn("fitdash-shell-v110", SW)

    def test_steps_fetch_wired(self):
        self.assertIn("def fetch_steps", GH)
        self.assertIn('daily_rollup("steps"', GH)
        self.assertIn('"steps": _steps', GH)

    def test_no_sound_api_claim(self):
        store = empty_store()
        self.assertFalse(store["api"]["available"])
        view = build_hsa_view(store, as_of="2026-09-19")
        self.assertFalse(view["api"]["available"])


class TestHsaWriteAuth(unittest.TestCase):
    def test_cookie_less_401(self):
        from api.workout._util import dispatch_client_route

        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = dispatch_client_route({}, "", "GET", path="/api/hsa")
            self.assertEqual(status, 401)
            self.assertEqual(body["error"], "auth_required")
            status, body = dispatch_client_route(
                {}, "", "POST", payload={"action": "save"}, path="/api/hsa"
            )
            self.assertEqual(status, 401)
            self.assertEqual(body["error"], "auth_required")


if __name__ == "__main__":
    unittest.main()
