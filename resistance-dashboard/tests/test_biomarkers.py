"""Biomarker store / importer / flags / caveats / UI.

All fixtures are SYNTHETIC test data (not a real patient). Every test runs
against a temp SQLite file with Turso forced off — nothing touches prod.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard import biomarkers_config as cfg
from rt_dashboard import biomarkers_import as bi
from rt_dashboard import biomarkers_store as bs
from rt_dashboard import biomarkers_view as bv
from rt_dashboard.biomarkers_api import handle_post

ROOT = Path(__file__).resolve().parents[1]

# SYNTHETIC Rythm-layout text — test data only, not a real patient.
SYNTH_RYTHM = """NAME
Test Person
ORDER ID
900001
COLLECTED
1/10/2026 7:45 AM
REPORTED
1/12/2026
SPECIMEN
Plasma
LAB
Rythm Health
FASTING
Yes
Test Value Unit Range Performance Range
Vitamin B12 500 pg/mL 232 - 1245 400 - 900
ApoB 95.0 mg/dL 0 - 90 50 - 80
Vitamin D 28.0 ng/mL 30 - 80 55 - 80
Estrogen <10 pg/mL 15 - 32 20 - 30
HDL Cholesterol 60.0 mg/dL 40 - 120 56 - 100
hs-CRP (High-Sensitivity C-Reactive Pro-
tein) 0.40 mg/L 0 - 3.0 0 - 1
Ferritin 120 ng/mL 29 - 575 100 - 400
Total Testosterone 600 ng/dL 200 - 800 700 - 1100
Creatinine 1.00 mg/dL 0.6 - 1.2 0.8 - 1.1
All tests listed above were developed and their analytical performance characteristics were determined by Rythm Health.
"""


def _row(marker, value, date, unit="mg/dL", lo=None, hi=None, panel=None, cmp_=""):
    return {"marker": marker, "marker_name": marker, "value": value, "value_text": f"{cmp_}{value}",
            "comparator": cmp_, "unit": unit, "ref_low": lo, "ref_high": hi, "ref_text": None,
            "draw_date": date, "panel_id": panel or f"p-{date}", "source": "test"}


def _view(rows, panels=None, **kw):
    panels = panels or [{"panel_id": f"p-{d}", "draw_date": d, "lab": "T", "context": {}}
                        for d in sorted({r["draw_date"] for r in rows})]
    return bv.build_view({"panels": panels, "rows": rows}, **kw)


def _m(view, marker):
    for g in view["groups"]:
        for m in g["markers"]:
            if m["marker"] == marker:
                return m
    return None


class _TempStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {"FITDASH_BIOMARKERS_BACKEND": "sqlite",
               "FITDASH_BIOMARKERS_DB": str(Path(self.tmp.name) / "bm.sqlite3"),
               "TURSO_DATABASE_URL": "", "TURSO_AUTH_TOKEN": ""}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)


class StoreTests(_TempStore):
    def test_migration_is_additive_and_idempotent(self):
        bs.migrate()
        bs.migrate()
        for stmt in bs.MIGRATION_SQL:
            self.assertRegex(stmt.strip(), r"^CREATE (UNIQUE INDEX|TABLE) IF NOT EXISTS")
            self.assertNotRegex(stmt.upper(), r"\b(DROP|ALTER|DELETE|UPDATE)\b")
        con = sqlite3.connect(os.environ["FITDASH_BIOMARKERS_DB"])
        cols = {r[1] for r in con.execute("PRAGMA table_info(biomarker_results)")}
        for c in ("marker", "value", "unit", "ref_low", "ref_high", "ref_text", "draw_date",
                  "source", "panel_id", "created_at"):
            self.assertIn(c, cols)

    def test_insert_load_and_duplicate_protection(self):
        pv = bi.preview_pdf_text(SYNTH_RYTHM, filename="synthetic.pdf")
        res = bi.commit_preview(pv)
        self.assertEqual(res["inserted"], 9)
        again = bi.preview_pdf_text(SYNTH_RYTHM, filename="synthetic.pdf", existing=bs.existing_keys())
        self.assertEqual(again["new_count"], 0)
        self.assertEqual(again["duplicate_count"], 9)
        res2 = bi.commit_preview(again)
        self.assertEqual(res2["inserted"], 0)
        data = bs.load_all()
        self.assertEqual(len(data["rows"]), 9)
        self.assertEqual(data["panels"][0]["panel_id"], "rythm-900001")
        self.assertEqual(data["panels"][0]["context"]["draw_time"], "07:45")

    def test_users_are_isolated_and_delete(self):
        bi.commit_preview(bi.preview_pdf_text(SYNTH_RYTHM), user_id="a")
        self.assertEqual(bs.load_all("b")["rows"], [])
        self.assertEqual(bs.delete_panel("rythm-900001", user_id="a"), 9)
        self.assertEqual(bs.load_all("a")["rows"], [])

    def test_turso_path_seals_values(self):
        import rt_dashboard.crypto_box as crypto_box

        crypto_box._KEY = None
        self.addCleanup(lambda: setattr(crypto_box, "_KEY", None))
        with mock.patch.dict(os.environ, {"FITDASH_MASTER_KEY": base64.urlsafe_b64encode(b"k" * 32).decode()}):
            path = Path(self.tmp.name) / "fake-turso.sqlite3"
            with mock.patch.object(bs, "backend", return_value="turso"), \
                 mock.patch.object(bs, "_connect", side_effect=lambda: bs._Sqlite(path)):
                pv = bi.preview_pdf_text(SYNTH_RYTHM)
                pv["panel"]["context"]["creatine"] = "yes"
                bi.commit_preview(pv)
                raw = sqlite3.connect(str(path)).execute(
                    "SELECT value, ref_low, sealed FROM biomarker_results").fetchall()
                self.assertTrue(all(r[0] is None and r[1] is None and r[2] for r in raw))
                ctx_raw = sqlite3.connect(str(path)).execute(
                    "SELECT context_json FROM biomarker_panels").fetchone()[0]
                self.assertNotIn("creatine", ctx_raw)
                data = bs.load_all()
                apob = [r for r in data["rows"] if r["marker"] == "apob"][0]
                self.assertEqual(apob["value"], 95.0)
                self.assertEqual(data["panels"][0]["context"]["creatine"], "yes")

    def test_default_backend_never_turso_when_forced(self):
        with mock.patch.dict(os.environ, {"TURSO_DATABASE_URL": "libsql://x", "TURSO_AUTH_TOKEN": "t"}):
            self.assertEqual(bs.backend(), "sqlite")


class ImporterTests(_TempStore):
    def test_pdf_text_rows_exact(self):
        pv = bi.preview_pdf_text(SYNTH_RYTHM, filename="synthetic.pdf")
        rows = {r["marker"]: r for r in pv["rows"]}
        self.assertEqual(pv["panel"]["draw_date"], "2026-01-10")
        self.assertTrue(pv["dry_run"])
        self.assertEqual(rows["apob"]["value"], 95.0)
        self.assertEqual((rows["apob"]["ref_low"], rows["apob"]["ref_high"]), (0.0, 90.0))
        self.assertEqual(rows["estradiol"]["comparator"], "<")
        self.assertEqual(rows["estradiol"]["value_text"], "<10")
        self.assertIn("hs_crp", rows)
        self.assertIn("total_testosterone", rows)
        self.assertEqual(pv["panel"]["context"]["fasted"], True)

    def test_preview_writes_nothing(self):
        handle_post("biomarkers_import", {"kind": "csv", "csv": "marker,value,unit\nApoB,80,mg/dL",
                                          "draw_date": "2026-02-01"})
        self.assertEqual(bs.load_all()["rows"], [])

    def test_csv_manual_and_commit_via_api(self):
        body = {"kind": "csv", "draw_date": "2026-02-01", "lab": "Manual",
                "csv": "marker,value,unit,ref_low,ref_high\nLDL-C,101,mg/dL,0,99\nHbA1c,5.6,%,4,5.6\nhs-CRP,<0.2,mg/L,0,3",
                "context": {"fasted": True, "draw_time": "08:10", "days_since_hard_session": 99}}
        st, out = handle_post("biomarkers_import", dict(body, commit=True))
        self.assertEqual(st, 200)
        self.assertEqual(out["result"]["inserted"], 3)
        self.assertNotIn("days_since_hard_session", out["preview"]["panel"]["context"])  # auto-only
        rows = {r["marker"]: r for r in bs.load_all()["rows"]}
        self.assertEqual(set(rows), {"ldl", "hba1c", "hs_crp"})
        self.assertEqual(rows["hs_crp"]["comparator"], "<")
        st, out = handle_post("biomarkers_import", dict(body, commit=True))
        self.assertEqual(out["result"]["inserted"], 0)

    def test_json_import_shapes(self):
        pv = bi.preview_json({"lab": "X", "draw_date": "2026-03-01", "markers": [
            {"marker": "Vitamin D", "value": 31, "unit": "ng/mL", "range": "30 - 100"},
            {"marker": "Ferritin", "value": 40, "unit": "ng/mL", "ref_text": "see note"}]})
        rows = {r["marker"]: r for r in pv["rows"]}
        self.assertEqual((rows["vitamin_d"]["ref_low"], rows["vitamin_d"]["ref_high"]), (30.0, 100.0))
        self.assertEqual(rows["ferritin"]["ref_text"], "see note")
        pv2 = bi.preview_json([{"marker": "TSH", "value": "2.1", "draw_date": "2026-03-01"}])
        self.assertEqual(pv2["rows"][0]["marker"], "tsh")

    def test_bad_inputs(self):
        st, out = handle_post("biomarkers_import", {"kind": "csv", "csv": "marker,value\nApoB,80"})
        self.assertEqual(st, 400)
        st, out = handle_post("biomarkers_import", {"kind": "pdf", "content_base64": ""})
        self.assertEqual(st, 400)
        st, out = handle_post("biomarkers_import", {"kind": "pdf", "content_base64": base64.b64encode(b"%PDF-1.3 junk").decode()})
        self.assertEqual(st, 400)

    def test_aliases(self):
        for name, cid in [("LDL Cholesterol", "ldl"), ("Estrogen", "estradiol"), ("25-OH Vitamin D", "vitamin_d"),
                          ("Hemoglobin A1c", "hba1c"), ("Creatine Kinase", "ck"), ("hs_crp_mg_l", "hs_crp"),
                          ("hs-CRP (High-Sensitivity C-Reactive Protein)", "hs_crp"), ("AM Cortisol", "cortisol")]:
            self.assertEqual(bi.canonical_marker(name), cid, name)

    @unittest.skipUnless(shutil.which("pdftotext"), "poppler not installed")
    def test_pdftotext_path_on_generated_pdf(self):
        # Build a tiny SYNTHETIC one-page PDF so the pdftotext path is exercised.
        lines = [ln for ln in SYNTH_RYTHM.splitlines() if ln.strip()]
        body = "BT /F1 9 Tf 20 780 Td 11 TL " + " ".join(
            "(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj T*" for ln in lines) + " ET"
        objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
                "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                f"<< /Length {len(body)} >>\nstream\n{body}\nendstream"]
        out, offs = b"%PDF-1.4\n", []
        for i, o in enumerate(objs, 1):
            offs.append(len(out))
            out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
        x = len(out)
        out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode() + b"".join(
            f"{o:010d} 00000 n \n".encode() for o in offs)
        out += f"trailer << /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
        pv = bi.preview_pdf(out, filename="synthetic.pdf")
        self.assertEqual(pv["panel"]["draw_date"], "2026-01-10")
        self.assertGreaterEqual(len(pv["rows"]), 9)
        self.assertIn("sha256:", pv["panel"]["source"])


class FlagTests(unittest.TestCase):
    def test_out_of_range_review_wording(self):
        v = _view([_row("apob", 95, "2026-01-01", lo=0, hi=90)])
        m = _m(v, "apob")
        self.assertEqual(m["status"], "high")
        self.assertTrue(all(f.startswith("review:") for f in m["flags"]))
        self.assertTrue(v["today_card"]["show"])

    def test_comparator_below_range(self):
        v = _view([_row("estradiol", 12, "2026-01-01", unit="pg/mL", lo=15, hi=32, cmp_="<")])
        self.assertEqual(_m(v, "estradiol")["status"], "low")

    def test_big_move_thresholds(self):
        cases = [("apob", 80, 88.5, True), ("apob", 80, 87, False),
                 ("ldl", 100, 116, True), ("triglycerides", 100, 125, False), ("triglycerides", 100, 131, True),
                 ("glucose", 85, 95, True), ("glucose", 85, 94, False),
                 ("hba1c", 5.0, 5.3, True), ("hba1c", 5.0, 5.2, False),
                 ("tsh", 2.0, 2.9, False), ("tsh", 2.0, 3.0, True),
                 ("hemoglobin", 15, 16.6, True), ("total_testosterone", 600, 760, True),
                 ("vitamin_b12", 600, 700, False), ("creatinine", 1.0, 1.16, True),
                 ("egfr", 100, 89, True), ("cortisol", 12, 15, True)]
        for marker, a, b, want in cases:
            flags = bv.move_flags(marker, a, b, "u")
            self.assertEqual(bool(flags), want, f"{marker} {a}->{b}: {flags}")

    def test_vitamin_d_either_rule(self):
        self.assertTrue(bv.move_flags("vitamin_d", 60, 49, "ng/mL"))     # abs 11
        self.assertTrue(bv.move_flags("vitamin_d", 36, 26, "ng/mL"))     # pct 28 / abs 10
        self.assertFalse(bv.move_flags("vitamin_d", 60, 52, "ng/mL"))

    def test_hs_crp_and_ck_ratio_only(self):
        self.assertFalse(bv.move_flags("hs_crp", 0.4, 0.7))
        self.assertTrue(bv.move_flags("hs_crp", 0.4, 0.8))
        self.assertTrue(bv.level_flags("hs_crp", 3.5))
        self.assertIn("likely acute", bv.level_flags("hs_crp", 11)[0])
        self.assertTrue(bv.move_flags("ck", 200, 450))
        self.assertFalse(bv.move_flags("ck", 200, 350))

    def test_level_and_crossing(self):
        self.assertTrue(bv.level_flags("ferritin", 25))
        self.assertTrue(bv.level_flags("vitamin_d", 29))
        self.assertFalse(bv.level_flags("ferritin", 35))
        v = _view([_row("hba1c", 5.6, "2026-01-01", unit="%", lo=4, hi=6.4),
                   _row("hba1c", 5.75, "2026-04-01", unit="%", lo=4, hi=6.4)])
        self.assertTrue(any("crossed 5.7" in f for f in _m(v, "hba1c")["flags"]))
        v = _view([_row("ldl", 95, "2026-01-01", lo=0, hi=99), _row("ldl", 100, "2026-04-01", lo=0, hi=99)])
        self.assertTrue(any("crossed lab range" in f for f in _m(v, "ldl")["flags"]))

    def test_cut_trend_flags_in_range(self):
        v = _view([_row("total_testosterone", 600, "2026-01-01", unit="ng/dL", lo=200, hi=800),
                   _row("total_testosterone", 540, "2026-04-01", unit="ng/dL", lo=200, hi=800),
                   _row("ferritin", 200, "2026-01-01", unit="ng/mL", lo=29, hi=575),
                   _row("ferritin", 180, "2026-04-01", unit="ng/mL", lo=29, hi=575),
                   _row("shbg", 40, "2026-01-01", unit="nmol/L", lo=10, hi=90),
                   _row("shbg", 36, "2026-04-01", unit="nmol/L", lo=10, hi=90)])
        t = _m(v, "total_testosterone")
        self.assertEqual(t["status"], "in")
        self.assertTrue(any("cut-trend" in f for f in t["flags"]))
        self.assertTrue(any("cut-trend" in f for f in _m(v, "ferritin")["flags"]))
        self.assertFalse(_m(v, "shbg")["flags"])
        self.assertEqual(t["delta"]["abs"], -60)
        self.assertEqual(t["delta"]["pct"], -10.0)
        self.assertEqual(len(t["series"]), 2)

    def test_unit_change_blocks_delta(self):
        v = _view([_row("glucose", 90, "2026-01-01", unit="mg/dL"), _row("glucose", 5.0, "2026-04-01", unit="mmol/L")])
        m = _m(v, "glucose")
        self.assertIsNone(m["delta"])
        self.assertIn("unit changed", m["warning"])


class GroupAndContextTests(unittest.TestCase):
    def test_pinned_groups_final_order_no_ck_pin(self):
        ids = [i for _g, _l, ms in cfg.PINNED_GROUPS for i in ms]
        self.assertNotIn("ck", ids)
        for want in ("cortisol", "shbg", "estradiol"):
            self.assertIn(want, cfg.PINNED_GROUPS[0][2])
        self.assertEqual(cfg.PINNED_GROUPS[0][2][:3], ["hs_crp", "ferritin", "iron"])
        self.assertEqual(cfg.PINNED_GROUPS[0][2][-4:], ["alt", "ast", "creatinine", "egfr"])
        self.assertEqual(cfg.PINNED_GROUPS[1][2], ["apob", "ldl", "triglycerides", "hdl", "glucose", "hba1c"])
        self.assertEqual(cfg.PINNED_GROUPS[2][2], ["vitamin_b12"])

    def test_only_reported_markers_shown_and_ordering(self):
        v = _view([_row("uric_acid", 5, "2026-01-01"), _row("apob", 80, "2026-01-01", lo=0, hi=90),
                   _row("ferritin", 100, "2026-01-01", lo=29, hi=575), _row("hs_crp", 0.5, "2026-01-01", lo=0, hi=3),
                   _row("ck", 300, "2026-01-01", unit="U/L", lo=30, hi=400)])
        self.assertEqual([g["id"] for g in v["groups"]], ["training", "cardiometabolic", "other"])
        self.assertEqual([m["marker"] for m in v["groups"][0]["markers"]], ["hs_crp", "ferritin"])
        self.assertEqual({m["marker"] for m in v["groups"][2]["markers"]}, {"ck", "uric_acid"})
        self.assertIn("days_since_hard_session", _m(v, "ck"))
        self.assertNotIn("days_since_hard_session", _m(v, "ferritin"))
        self.assertEqual(v["footnote"], "Thresholds are rule-of-thumb defaults, not clinical rules.")

    def test_training_context_from_sessions(self):
        sessions = [{"date": "2026-01-08", "session_type": "push", "exercises": [{"name": "Bench Press"}]},
                    {"date": "2026-01-06", "session_type": "pull", "exercises": [{"name": "Deadlift"}]},
                    {"date": "2026-01-10", "session_type": "push", "exercises": [{"name": "OHP"}]},  # same day: ignored
                    {"date": "2026-01-09", "session_type": "rest", "exercises": []}]
        ctx = bv.training_context(sessions, "2026-01-10")
        self.assertEqual(ctx, {"days_since_hard_session": 2, "days_since_heavy_legs": 4})

    def test_training_caveat_annotation_not_flag(self):
        sessions = [{"date": "2026-01-08", "session_type": "legs", "exercises": [{"name": "Squat"}]}]
        rows = [_row("creatinine", 1.0, "2026-01-10", lo=0.6, hi=1.2), _row("hs_crp", 0.5, "2026-01-10", lo=0, hi=3),
                _row("alt", 20, "2026-01-10", unit="U/L", lo=0, hi=44), _row("apob", 80, "2026-01-10", lo=0, hi=90)]
        v = _view(rows, sessions=sessions, today="2026-03-01")
        cr = _m(v, "creatinine")
        self.assertFalse(cr["flags"])
        self.assertTrue(any("Recent training" in c and "Creatine" in c for c in cr["caveats"]))
        self.assertTrue(_m(v, "hs_crp")["caveats"])
        self.assertTrue(_m(v, "alt")["caveats"])
        self.assertFalse(_m(v, "apob")["caveats"])
        self.assertFalse(v["today_card"]["show"])  # old + nothing flagged

    def test_context_caveats(self):
        cav = bv.active_caveats({"creatine": "yes", "alcohol_48h": True, "fasted": False, "biotin": "yes",
                                 "draw_time": "13:30", "recent_illness": "yes"})
        self.assertIn("egfr", cav)
        self.assertIn("triglycerides", cav)
        self.assertEqual(len(cav["glucose"]), 2)
        self.assertIn("tsh", cav)
        self.assertTrue(any("only compare morning draws" in c for c in cav["total_testosterone"]))
        self.assertIn("cortisol", cav)
        self.assertIn("hs_crp", cav)
        self.assertEqual(bv.active_caveats({"creatine": "paused", "biotin": "paused", "draw_time": "07:30",
                                            "fasted": True, "fast_hours": 12}), {})
        self.assertIn("glucose", bv.active_caveats({"fasted": True, "fast_hours": 6}))

    def test_today_card_recent_result(self):
        rows = [_row("apob", 80, "2026-01-10", lo=0, hi=90)]
        self.assertTrue(_view(rows, today="2026-01-15")["today_card"]["show"])
        self.assertFalse(_view(rows, today="2026-01-20")["today_card"]["show"])

    def test_auto_context_weight_phase(self):
        rows = [_row("apob", 80, "2026-01-10", lo=0, hi=90)]
        v = _view(rows, weights=[{"date": "2026-01-09", "weight_lbs": 181.04}], phase="cut")
        ctx = v["panels"][0]["context"]
        self.assertEqual(ctx["bodyweight_lbs"], 181.0)
        self.assertEqual(ctx["phase"], "cut")
        self.assertIn("bodyweight_lbs", ctx["_auto"])


class UiTests(unittest.TestCase):
    HTML = (ROOT / "static" / "index.html").read_text()
    SW = (ROOT / "static" / "sw.js").read_text()
    JS = (ROOT / "static" / "biomarkers.js").read_text()

    def test_markup_tab_section_and_today_card(self):
        self.assertIn('data-m-tab="biomarkers"', self.HTML)
        self.assertIn('id="biomarkers-section" data-m-panel="biomarkers"', self.HTML)
        self.assertRegex(self.HTML, r'id="biomarkers-today-card" data-m-panel="today" hidden')
        self.assertIn("Thresholds are rule-of-thumb defaults, not clinical rules.", self.HTML)
        self.assertLess(self.HTML.index("/biomarkers.js"), self.HTML.index("/app.js"))
        self.assertIn("/biomarkers.js?v=biomarkers-1", self.SW)
        for key in ("draw_time", "fasted", "fast_hours", "creatine", "biotin", "alcohol_48h", "recent_illness"):
            self.assertIn(f'data-ctx="{key}"', self.HTML)

    CSS = (ROOT / "static" / "styles.css").read_text()
    APP = (ROOT / "static" / "app.js").read_text()

    def test_tab_bar_order(self):
        nav = self.HTML[self.HTML.index('<nav class="mobile-tabbar"'):]
        nav = nav[: nav.index("</nav>")]
        self.assertEqual(re.findall(r'data-m-tab="(\w+)"', nav),
                         ["today", "trends", "log", "kitchen", "biomarkers", "more"])
        self.assertEqual(re.findall(r'<span class="tab-lbl">([^<]+)</span>', nav),
                         ["Today", "Trends", "Log", "Kitchen", "Labs", "More"])

    def test_every_tab_has_a_panel_and_a_css_show_rule(self):
        """Labs-blank bug class: ``body.m-shell [data-m-panel]`` hides every
        panel; each tab needs its own show rule or the tab renders blank."""
        nav = self.HTML[self.HTML.index('<nav class="mobile-tabbar"'):]
        tabs = re.findall(r'data-m-tab="(\w+)"', nav[: nav.index("</nav>")])
        self.assertIn("body.m-shell [data-m-panel] {\n  display: none !important;", self.CSS)
        for t in tabs:
            self.assertIn(f'data-m-panel="{t}"', self.HTML, t)
            self.assertIn(f'body.m-shell[data-m-active="{t}"] [data-m-panel="{t}"]', self.CSS, t)

    def test_app_render_hands_payload_to_biomarkers(self):
        self.assertIn("window.FitDashBiomarkers.render(data)", self.APP)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_render_with_fake_dom(self):
        view = _view([_row("apob", 95, "2026-01-10", lo=0, hi=90),
                      _row("ferritin", 200, "2025-10-01", unit="ng/mL", lo=29, hi=575),
                      _row("ferritin", 150, "2026-01-10", unit="ng/mL", lo=29, hi=575)], today="2026-01-12")
        harness = r"""
const els = {};
function el(id){ return els[id] || (els[id] = {id, innerHTML:"", textContent:"", hidden:true, value:"",
  addEventListener(){}, querySelectorAll(){return [];}, dataset:{}}); }
global.document = { readyState: "complete", getElementById: el, querySelector(){return null;},
  querySelectorAll(){return [];}, addEventListener(){} };
global.window = {};
eval(require("fs").readFileSync(process.argv[2], "utf8"));
window.FitDashBiomarkers.render({biomarkers: JSON.parse(process.argv[3])});
console.log(JSON.stringify({body: els["biomarkers-body"].innerHTML, today: els["biomarkers-today-card"].innerHTML,
  hidden: els["biomarkers-today-card"].hidden}));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(harness)
        self.addCleanup(os.unlink, fh.name)
        out = subprocess.run(["node", fh.name, str(ROOT / "static" / "biomarkers.js"), json.dumps(view)],
                             capture_output=True, text=True, timeout=30, check=True)
        res = json.loads(out.stdout)
        self.assertFalse(res["hidden"])
        self.assertIn("ApoB", res["body"])
        self.assertIn("bm-range", res["body"])
        self.assertIn("<svg class=\"bm-spark\"", res["body"])
        self.assertIn("cut-trend", res["body"])
        self.assertIn("Training &amp; recovery", res["body"])
        self.assertIn("Open Labs tab", res["today"])
        self.assertLess(res["body"].index("Ferritin"), res["body"].index("ApoB"))


class ConfigRoutesTests(unittest.TestCase):
    def test_vercel_rewrites_and_routes(self):
        vj = json.loads((ROOT / "vercel.json").read_text())
        srcs = {r["source"]: r["destination"] for r in vj.get("rewrites", [])}
        for p, r in [("/api/biomarkers", "biomarkers"), ("/api/biomarkers/import", "biomarkers_import"),
                     ("/api/biomarkers/context", "biomarkers_context"), ("/api/biomarkers/delete", "biomarkers_delete")]:
            self.assertEqual(srcs[p], f"/api/dashboard?_r={r}")
        from api.workout._util import client_route_name

        self.assertEqual(client_route_name({}, "", "/api/biomarkers/import"), "biomarkers_import")
        self.assertEqual(client_route_name({}, "", "/api/biomarkers"), "biomarkers")


if __name__ == "__main__":
    unittest.main()
