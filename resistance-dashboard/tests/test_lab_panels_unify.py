"""One panel store. Synthetic fixtures only — no real patients."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard import biomarkers_import as bi  # noqa: E402
from rt_dashboard import biomarkers_store as bs  # noqa: E402
from rt_dashboard.labs_parse import parse_lab_text  # noqa: E402
from rt_dashboard.labs_store import (  # noqa: E402
    flag_markers,
    labs_history,
    labs_summary_for_coach,
    load_labs,
    save_panel,
)

# Draw day and a later report day. Values are made up.
_HEAD = """LAB
Rythm Health
ORDER ID
424242
COLLECTED
3/2/2026 7:05 AM
REPORTED
3/4/2026
FASTING
Yes
Test Value Unit Range Performance Range
"""


def _ids(panel):
    raw = panel.get("markers") or {}
    if isinstance(raw, dict):
        return {str(v.get("id") or k): v for k, v in raw.items()}
    return {m["id"]: m for m in raw}


class ParserSplitTests(unittest.TestCase):
    def test_adjacent_rows_stay_separate(self):
        text = _HEAD + (
            "eGFR 81 mL/min/1.73m2 >60\n"
            "LDL/ApoB Ratio 1.7 ratio 0.4 - 1.5\n"
            "ApoB 70 mg/dL 0 - 90 40 - 80\n"
        )
        panel = parse_lab_text(text)
        markers = _ids(panel)
        self.assertEqual(panel["date"], "2026-03-02")
        self.assertEqual(panel["reported"], "2026-03-04")
        self.assertEqual(panel["draw_time"], "07:05")
        self.assertIn("egfr", markers)
        self.assertIn("ldl_apob", markers)
        self.assertEqual(markers["egfr"]["value"], 81.0)
        self.assertEqual(markers["egfr"]["clinical_low"], 60.0)
        self.assertIsNone(markers["egfr"]["clinical_high"])
        self.assertEqual(markers["ldl_apob"]["value"], 1.7)
        self.assertEqual(markers["ldl_apob"]["clinical_low"], 0.4)
        self.assertEqual(markers["ldl_apob"]["clinical_high"], 1.5)
        from rt_dashboard.lab_markers import canonical_ids_in_text

        for marker in markers.values():
            self.assertEqual(len(canonical_ids_in_text(marker["name"])), 1)

    def test_glued_line_splits_and_unrecoverable_glue_is_not_stored(self):
        glued = _HEAD + (
            "eGFR 81 mL/min/1.73m2 >60 LDL/ApoB Ratio 1.7 ratio 0.4 - 1.5\n"
            "ApoB 70 mg/dL 0 - 90 40 - 80\n"
        )
        markers = _ids(parse_lab_text(glued))
        self.assertEqual(markers["egfr"]["value"], 81.0)
        self.assertEqual(markers["ldl_apob"]["value"], 1.7)
        bad = _HEAD + (
            "eGFR LDL/ApoB Ratio 4.4 ratio 0.2 - 3\n"
            "ApoB 70 mg/dL 0 - 90 40 - 80\n"
        )
        bad_markers = _ids(parse_lab_text(bad))
        self.assertIn("apob", bad_markers)
        self.assertNotIn("ldl_apob", bad_markers)
        from rt_dashboard.lab_markers import canonical_ids_in_text

        for marker in bad_markers.values():
            self.assertLess(len(canonical_ids_in_text(marker["name"])), 2)


class UnifyPathsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {
            "RESISTANCE_DASHBOARD_CONFIG_DIR": self.tmp.name,
            "FITDASH_BIOMARKERS_BACKEND": "sqlite",
            "FITDASH_BIOMARKERS_DB": str(Path(self.tmp.name) / "bm.sqlite3"),
            "TURSO_DATABASE_URL": "",
            "TURSO_AUTH_TOKEN": "",
        }
        self._env = env
        for key, val in env.items():
            os.environ[key] = val
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in env])

    def test_both_paths_one_store_latest_draw_and_own_ranges(self):
        older = {
            "date": "2026-03-04",
            "collected": "2026-03-02",
            "lab": "Rythm Health",
            "fasting": True,
            "order_id": "424242",
            "markers": {
                "ldl_mg_dl": {
                    "id": "ldl_mg_dl",
                    "name": "LDL Cholesterol",
                    "value": 48,
                    "value_text": "48",
                    "comparator": "",
                    "unit": "mg/dL",
                    "clinical_low": 0,
                    "clinical_high": 40,
                }
            },
        }
        save_panel(older, user_id="local")
        pv = bi.preview_csv(
            "marker,value,unit,ref_low,ref_high\nLDL-C,52,mg/dl,0,99\n",
            draw_date="2026-08-15",
            lab="Rythm Health",
            context={"fasted": True, "draw_time": "07:05"},
        )
        bi.commit_preview(pv, user_id="local")

        labs = load_labs(user_id="local", as_of="2026-08-18")
        self.assertEqual(len(labs["panels"]), 2)
        self.assertEqual(labs["panels"][-1]["date"], "2026-08-15")
        self.assertEqual(labs["panels"][0]["date"], "2026-03-02")
        self.assertEqual(labs["panels"][0]["reported"], "2026-03-04")
        newer = labs["panels"][-1]
        self.assertTrue(newer.get("fasting"))
        self.assertEqual(newer.get("draw_time"), "07:05")

        data = bs.load_all("local")
        draws = {r["draw_date"] for r in data["rows"]}
        self.assertEqual(draws, {"2026-03-02", "2026-08-15"})

        hist = labs["history"]
        ldl = next(m for m in hist["markers"] if m["id"] == "ldl")
        self.assertEqual(ldl["n"], 2)
        self.assertEqual(ldl["name"], "LDL-C")
        self.assertFalse(ldl["mixed_units"])
        self.assertEqual(ldl["direction"], "up")
        by_date = {p["date"]: p for p in ldl["series"]}
        self.assertEqual(by_date["2026-03-02"]["ref_high"], 40)
        self.assertEqual(by_date["2026-08-15"]["ref_high"], 99)
        self.assertEqual(by_date["2026-03-02"]["value"], 48)
        self.assertEqual(by_date["2026-08-15"]["value"], 52)

        older_flags = flag_markers(labs["panels"][0])
        newer_flags = flag_markers(labs["panels"][-1])
        self.assertTrue(any(f.get("ref_high") == 40 for f in older_flags))
        self.assertFalse(any(f.get("ref_high") == 99 and f.get("status") == "high" for f in newer_flags))
        summary = labs_summary_for_coach(labs, as_of="2026-08-18")
        self.assertEqual(summary["date"], "2026-08-15")
        self.assertFalse(summary["stale"])
        self.assertTrue(summary["fasting"])
        # Coach flags are the latest panel's flags, which stay inside 0–99.
        self.assertFalse(any(f.get("ref_high") == 40 for f in summary["flags"]))

        # Re-import does not duplicate or change the stored number.
        again = bi.commit_preview(pv, user_id="local")
        self.assertEqual(again["inserted"], 0)
        labs2 = load_labs(user_id="local", as_of="2026-08-18")
        self.assertEqual(len(labs2["panels"]), 2)
        hist2 = labs_history(labs2)
        ldl2 = next(m for m in hist2["markers"] if m["id"] == "ldl")
        self.assertEqual([p["value"] for p in ldl2["series"]], [48, 52])


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "bm.sqlite3"
        self.env = os.environ.copy()
        self.env.update({
            "RESISTANCE_DASHBOARD_CONFIG_DIR": self.tmp.name,
            "FITDASH_BIOMARKERS_BACKEND": "sqlite",
            "FITDASH_BIOMARKERS_DB": str(self.db),
            "TURSO_DATABASE_URL": "",
            "TURSO_AUTH_TOKEN": "",
        })
        for key, val in self.env.items():
            if key.startswith("FITDASH") or key.startswith("RESISTANCE") or key.startswith("TURSO"):
                os.environ[key] = val
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in (
            "RESISTANCE_DASHBOARD_CONFIG_DIR", "FITDASH_BIOMARKERS_BACKEND",
            "FITDASH_BIOMARKERS_DB", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN")])
        from rt_dashboard.labs_store import _save_index

        _save_index("local", {
            "source_note": "t",
            "updated_at": "2026-03-05T00:00:00Z",
            "panels": [{
                "date": "2026-03-04",
                "collected": "2026-03-02",
                "lab": "Rythm Health",
                "fasting": True,
                "markers": {
                    "ldl_mg_dl": {
                        "id": "ldl_mg_dl",
                        "name": "LDL Cholesterol",
                        "value": 33,
                        "value_text": "33",
                        "comparator": "",
                        "unit": "mg/dL",
                        "clinical_low": 0,
                        "clinical_high": 30,
                    }
                },
            }],
        })
        bs.migrate()
        con = sqlite3.connect(str(self.db))
        con.execute(
            "INSERT INTO biomarker_panels(user_id, panel_id, lab, draw_date, source, context_json, created_at) "
            "VALUES ('local', 'synth-aug', 'Rythm Health', '2026-08-15', 'legacy', ?, '2026-08-15T00:00:00Z')",
            (json.dumps({"fasted": True, "draw_time": "07:05"}),),
        )
        rows = [
            ("collapsed", "eGFR 81 mL/min/1.73m2 >60 LDL/ApoB Ratio", 1.7, "1.7", "ratio", 0.4, 1.5),
            ("unsplit", "eGFR LDL/ApoB Ratio", 4.4, "4.4", "ratio", 0.2, 3),
        ]
        for marker, name, value, text, unit, lo, hi in rows:
            con.execute(
                "INSERT INTO biomarker_results(user_id, panel_id, marker, marker_name, value, value_text, "
                "comparator, unit, ref_low, ref_high, draw_date, source, created_at) "
                "VALUES ('local', 'synth-aug', ?, ?, ?, ?, '', ?, ?, ?, '2026-08-15', 'legacy', '2026-08-15T00:00:00Z')",
                (marker, name, value, text, unit, lo, hi),
            )
        con.commit()
        con.close()
        self.index = Path(self.tmp.name) / "labs" / "local" / "index.json"
        self.before = hashlib.sha256(self.index.read_bytes()).hexdigest()
        self.script = ROOT / "scripts" / "migrate_lab_panels.py"

    def _run(self, *args):
        proc = subprocess.run(
            [sys.executable, str(self.script), "--user-id", "local", *args],
            cwd=str(ROOT),
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_dry_run_writes_nothing_and_apply_is_idempotent(self):
        forbidden = ("81", "1.7", "4.4", "0.4", "33")
        out = self._run("--dry-run")
        for token in forbidden:
            self.assertNotIn(token, out)
        self.assertIn("mode=dry-run", out)
        self.assertIn("backup=0", out)
        self.assertIn("changed=1", out)
        self.assertIn("added_panels=1", out)
        self.assertIn("draw_dates_corrected=1", out)
        self.assertIn("split_marker_ids=egfr+ldl_apob", out)
        self.assertIn("unsplit_marker_ids=egfr+ldl_apob", out)
        self.assertEqual(hashlib.sha256(self.index.read_bytes()).hexdigest(), self.before)
        self.assertFalse((Path(self.tmp.name) / "lab-panel-backups").exists())
        con = sqlite3.connect(str(self.db))
        n = con.execute("SELECT COUNT(*) FROM biomarker_results").fetchone()[0]
        con.close()
        self.assertEqual(n, 2)

        applied = self._run("--apply")
        for token in forbidden:
            self.assertNotIn(token, applied)
        self.assertIn("backup=1", applied)
        self.assertIn("mode=apply", applied)
        backups = list((Path(self.tmp.name) / "lab-panel-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "labs-file").is_dir())
        self.assertTrue((backups[0] / "biomarkers.sqlite3").is_file())

        labs = load_labs(user_id="local", as_of="2026-08-18")
        self.assertEqual(len(labs["panels"]), 2)
        self.assertEqual([p["date"] for p in labs["panels"]], ["2026-03-02", "2026-08-15"])
        self.assertFalse(labs["panels"][-1]["stale"])
        newer = _ids(labs["panels"][-1])
        self.assertEqual(newer["egfr"]["value"], 81.0)
        self.assertEqual(newer["ldl_apob"]["value"], 1.7)
        untouched = next(m for m in newer.values() if m.get("value") == 4.4)
        self.assertEqual(untouched["value"], 4.4)
        self.assertEqual(untouched["name"], "eGFR LDL/ApoB Ratio")
        older = _ids(labs["panels"][0])
        self.assertEqual(older["ldl"]["value"], 33)

        second = self._run("--apply")
        self.assertIn("changed=0", second)
        self.assertIn("backup=0", second)
        self.assertNotIn("backup_dir=", second)
        for token in forbidden:
            self.assertNotIn(token, second)
        self.assertEqual(len(list((Path(self.tmp.name) / "lab-panel-backups").iterdir())), 1)
        labs2 = load_labs(user_id="local", as_of="2026-08-18")
        self.assertEqual(len(labs2["panels"]), 2)
        newer2 = _ids(labs2["panels"][-1])
        self.assertEqual(newer2["egfr"]["value"], 81.0)
        self.assertEqual(newer2["ldl_apob"]["value"], 1.7)
        self.assertEqual(next(m["value"] for m in newer2.values() if m.get("value") == 4.4), 4.4)


if __name__ == "__main__":
    unittest.main()
