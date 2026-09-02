"""Lab PDF parse + config store (no PHI in fixtures)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.labs_parse import (  # noqa: E402
    LabParseError,
    parse_lab_text,
    parse_rythm_panel,
)
from rt_dashboard.labs_store import (  # noqa: E402
    delete_panel,
    flag_markers,
    labs_summary_for_coach,
    load_labs,
    save_panel,
)

# Synthetic Rythm layout — not a real patient. ≥8 rows (parser floor).
RYTHM_TEXT = """NAME
Jane Doe
PATIENT ID
000000
DOB
1/1/1990
AGE
36
SEX
F
ORDER ID
111111
COLLECTED
5/30/2026 8:00 AM
RECEIVED
6/1/2026 12:00 PM
REPORTED
6/1/2026
SPECIMEN
Plasma
LAB
Rythm Health
CLIA #
00D0000000
CAP #
000000
FASTING
Yes
LAB DIRECTOR
Example
PROVIDER
Example
Test Value Unit Range Performance Range
Vitamin B12 500 pg/mL 232 - 1245 400 - 900
ApoB 95.0 mg/dL 0 - 90 50 - 80
Vitamin D 40.0 ng/mL 30 - 80 55 - 80
Estrogen <10 pg/mL 15 - 32 20 - 30
HDL Cholesterol 60.0 mg/dL 40 - 120 56 - 100
hs-CRP (High-Sensitivity C-Reactive Pro-
tein) 0.40 mg/L 0 - 3.0 0 - 1
Remnant Cholesterol 19.0 mg/dL 20 - 24 0 - 20
Creatinine 1.00 mg/dL 0.6 - 1.2 0.8 - 1.1
All tests listed above were developed and their analytical performance characteristics were determined by Rythm Health.
Generated at 6/1/2026 11:00 PM PST
"""


def _by_id(panel):
    raw = panel.get("markers") or []
    if isinstance(raw, dict):
        return { (v.get("id") or k): v for k, v in raw.items() }
    return {m["id"]: m for m in raw}


class TestLabsParse(unittest.TestCase):
    def test_rythm_table(self):
        panel = parse_lab_text(RYTHM_TEXT, filename="fake.pdf")
        self.assertEqual(panel["date"], "2026-06-01")
        self.assertEqual(panel["collected"], "2026-05-30")
        self.assertEqual(panel["lab"], "Rythm Health")
        self.assertTrue(panel["fasting"])
        m = _by_id(panel)
        self.assertIn("vitamin_b12", m)
        self.assertIn("apob", m)
        self.assertIn(m["estrogen"]["comparator"], ("<", "lt"))
        self.assertTrue("hs_crp" in m or "hscrp" in m)

    def test_rejects_unknown_layout(self):
        with self.assertRaises(LabParseError):
            parse_rythm_panel("This is a grocery receipt\nTotal 12.00")


class TestLabsStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["RESISTANCE_DASHBOARD_CONFIG_DIR"] = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: os.environ.pop("RESISTANCE_DASHBOARD_CONFIG_DIR", None))

    def test_save_and_flag(self):
        panel = parse_lab_text(RYTHM_TEXT)
        labs = save_panel(panel, pdf_bytes=b"%PDF-1.3 fake")
        self.assertIn(labs.get("storage") or labs.get("store"), ("config",))
        self.assertEqual(len(labs["panels"]), 1)
        loaded = load_labs()
        self.assertEqual(len(loaded["panels"]), 1)
        annotated = loaded["panels"][0]
        m = _by_id(annotated)
        apo = m.get("apob") or next(v for v in m.values() if v.get("id") == "apob")
        est = m.get("estrogen") or next(v for v in m.values() if v.get("id") == "estrogen")
        vd = m.get("vitamin_d") or next(v for v in m.values() if v.get("id") == "vitamin_d")
        rem = m.get("remnant_cholesterol") or next(
            v for v in m.values() if v.get("id") == "remnant_cholesterol"
        )
        self.assertEqual(apo["band"], "out_of_clinical")
        self.assertEqual(est["band"], "out_of_clinical")
        self.assertEqual(vd["band"], "out_of_performance")
        self.assertEqual(rem["band"], "in_performance")
        flags = flag_markers(annotated)
        blob = " ".join(str(f.get("marker") or "") + " " + str(f.get("name") or "") for f in flags).lower()
        self.assertIn("apob", blob)
        self.assertIn("estrogen", blob)
        pdf_hits = list(Path(self.tmp.name).rglob("*.pdf"))
        self.assertTrue(pdf_hits or annotated.get("source_sha256"))
        summary = labs_summary_for_coach(loaded)
        self.assertTrue(summary["has_labs"])
        self.assertEqual(summary["date"], "2026-06-01")

    def test_upsert_same_date_lab(self):
        panel = parse_lab_text(RYTHM_TEXT)
        save_panel(panel)
        panel2 = parse_lab_text(RYTHM_TEXT)
        labs = save_panel(panel2)
        self.assertEqual(len(labs["panels"]), 1)

    def test_delete(self):
        save_panel(parse_lab_text(RYTHM_TEXT))
        labs = delete_panel(date="2026-06-01")
        self.assertEqual(labs["panels"], [])

    def test_legacy_float_markers(self):
        flags = flag_markers(
            {
                "date": "2026-01-15",
                "lab": "Quest",
                "markers": {"vitamin_d_ng_ml": 18, "ldl_mg_dl": 90},
            }
        )
        keys = [f["marker"] for f in flags]
        self.assertTrue(any(k in ("vitamin_d", "vitamin_d_ng_ml") for k in keys))
        self.assertFalse(any(k in ("ldl", "ldl_mg_dl") for k in keys))


class TestLabsMarkup(unittest.TestCase):
    def test_more_labs_upload_wired(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="labs-section"', html)
        self.assertIn('data-m-panel="more"', html)
        self.assertIn('id="labs-upload-form"', html)
        self.assertIn("/api/labs/upload", js)
        self.assertIn("submitLabsUpload", js)
        self.assertNotIn("fitness/data/labs.json", js)


class TestFlagContract(unittest.TestCase):
    """Lane is an allowlist — estrogen never becomes diet copy. No TRT."""

    def test_estrogen_clinician_apob_nutrition(self):
        from rt_dashboard.labs_store import annotate_panel, labs_summary_for_coach

        panel = parse_lab_text(RYTHM_TEXT)
        annotated = annotate_panel(panel, targets={"notes": "cutting"}, as_of="2026-09-02")
        rows = _by_id(annotated)
        e2 = rows["estrogen"]
        apo = rows["apob"]
        self.assertEqual(e2["comparator"], "<")
        self.assertEqual(e2["value_text"], "<10")
        self.assertEqual(e2["lane"], "clinician")
        self.assertEqual(e2["coach_domain"], "none")
        self.assertFalse(e2["kitchen"])
        self.assertEqual(apo["lane"], "clinician")
        self.assertEqual(apo["coach_domain"], "nutrition")
        self.assertTrue(apo["kitchen"])
        self.assertTrue(annotated["stale"])
        summary = labs_summary_for_coach(
            {"panels": [annotated]},
            targets={"notes": "cutting"},
            as_of="2026-09-02",
        )
        kitchen = " ".join(summary["kitchen_lines"]).lower()
        self.assertIn("fiber", kitchen)
        self.assertNotIn("estrogen", kitchen)
        self.assertNotIn("trt", kitchen)
        self.assertNotIn("statin", kitchen)
        self.assertEqual(summary["volume"], "unchanged")
        clin_ids = {f["marker"] for f in summary["clinical_flags"]}
        self.assertIn("estrogen_pg_ml", clin_ids)
        self.assertIn("apob_mg_dl", clin_ids)

    def test_coach_does_not_diet_estrogen(self):
        from rt_dashboard.coach import build_food_commentary

        panel = parse_lab_text(RYTHM_TEXT)
        fc = build_food_commentary(
            food_logs=[],
            nutrition=[],
            targets={"calories": 2100, "protein_g": 210, "notes": "Default cutting targets"},
            consumed={"calories": 680, "protein_g": 56},
            adherence={"protein": {"pct": 40, "hits": 1, "days_logged": 3}},
            labs={"panels": [panel]},
            as_of="2026-09-02",
        )
        blob = (fc["markdown"] + " " + " ".join(fc["can_improve"]) + " " + " ".join(fc["notes"])).lower()
        self.assertNotIn("diet may support", blob)
        self.assertNotIn("trt", blob)
        self.assertNotIn("statin", blob)
        self.assertTrue(any("volume: unchanged" in n.lower() for n in fc["notes"]))
        improve = " ".join(fc["can_improve"]).lower()
        self.assertNotIn("estrogen", improve)
        self.assertTrue(any("clinician" in n.lower() for n in fc["notes"]))

    def test_energy_cluster_one_line(self):
        from rt_dashboard.labs_store import annotate_panel, energy_availability_cluster

        panel = parse_lab_text(RYTHM_TEXT)
        panel["markers"]["free_t3_pg_ml"] = {
            "id": "free_t3",
            "name": "Free T3",
            "value": 2.7,
            "value_text": "2.70",
            "comparator": "",
            "unit": "pg/mL",
            "clinical_low": 2,
            "clinical_high": 4.4,
            "performance_low": 3.4,
            "performance_high": 4.4,
        }
        panel["markers"]["total_testosterone_ng_dl"] = {
            "id": "total_testosterone",
            "name": "Total testosterone",
            "value": 471,
            "value_text": "471",
            "comparator": "",
            "unit": "ng/dL",
            "clinical_low": 200,
            "clinical_high": 800,
            "performance_low": 700,
            "performance_high": 1100,
        }
        annotated = annotate_panel(panel, targets={"notes": "cutting"}, as_of="2026-09-02")
        cluster = annotated["cluster"]
        self.assertIsNotNone(cluster)
        self.assertEqual(cluster["coach_domain"], "recovery")
        self.assertNotIn("TRT", cluster["text"])
        # Same cluster helper is idempotent
        again = energy_availability_cluster(annotated["markers"], in_deficit=True)
        self.assertEqual(again["id"], "energy_availability")


class TestPilotPdfOptional(unittest.TestCase):
    """Opt-in: FITDASH_PILOT_LAB_PDF=/path/to/rythm.pdf — never committed."""

    def test_pilot_pdf_if_present(self):
        path = (os.environ.get("FITDASH_PILOT_LAB_PDF") or "").strip()
        if not path or not Path(path).is_file():
            self.skipTest("no FITDASH_PILOT_LAB_PDF")
        from rt_dashboard.labs_parse import parse_lab_pdf

        panel = parse_lab_pdf(Path(path).read_bytes(), filename=Path(path).name)
        self.assertGreaterEqual(len(panel["markers"]), 20)
        self.assertEqual(panel["lab"], "Rythm Health")
        self.assertTrue(panel["date"])


if __name__ == "__main__":
    unittest.main()
