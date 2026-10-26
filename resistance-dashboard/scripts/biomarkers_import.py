#!/usr/bin/env python3
"""Seed / import biomarker panels (JSON, CSV or Rythm PDF) — dry-run by default.

  # preview only (writes nothing):
  python3 scripts/biomarkers_import.py import path/to/panel.json
  python3 scripts/biomarkers_import.py import results.pdf
  python3 scripts/biomarkers_import.py import panel.csv --draw-date 2026-11-01 --lab "Rythm Health"
  # write to the LOCAL sqlite store (FITDASH_BIOMARKERS_DB or ~/.config/...):
  python3 scripts/biomarkers_import.py import panel.json --commit
  # write to Turso (prod) — must be explicit, snapshot first:
  python3 scripts/biomarkers_import.py import panel.json --commit --allow-turso --user-id <id>
  python3 scripts/biomarkers_import.py migrate [--allow-turso]
  python3 scripts/biomarkers_import.py snapshot [--allow-turso] > snap.json

Without --allow-turso the store is forced to local SQLite even if TURSO_* is
set, so dev runs never touch prod. Real panel files belong OUTSIDE the repo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _pick_backend(allow_turso: bool) -> None:
    if not allow_turso:
        os.environ["FITDASH_BIOMARKERS_BACKEND"] = "sqlite"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("path")
    imp.add_argument("--commit", action="store_true", help="write (default is dry-run preview)")
    imp.add_argument("--allow-turso", action="store_true")
    imp.add_argument("--user-id", default="")
    imp.add_argument("--draw-date", default="", help="CSV only")
    imp.add_argument("--lab", default="manual", help="CSV only")
    imp.add_argument("--panel-id", default="", help="CSV only")
    for name in ("migrate", "snapshot"):
        sp = sub.add_parser(name)
        sp.add_argument("--allow-turso", action="store_true")
        sp.add_argument("--user-id", default="")
    a = ap.parse_args(argv)
    _pick_backend(a.allow_turso)

    from rt_dashboard import biomarkers_import as bi
    from rt_dashboard import biomarkers_store as bs

    print(f"# backend: {bs.backend()}" + (f" ({bs.db_path()})" if bs.backend() == "sqlite" else ""), file=sys.stderr)
    if a.cmd == "migrate":
        bs.migrate()
        print("migrated (additive: biomarker_panels, biomarker_results, dedupe index)")
        return 0
    if a.cmd == "snapshot":
        json.dump(bs.load_all(a.user_id), sys.stdout, indent=1, default=str)
        return 0
    p = Path(a.path)
    existing = bs.existing_keys(a.user_id)
    if p.suffix.lower() == ".pdf":
        pv = bi.preview_pdf(p.read_bytes(), filename=p.name, existing=existing)
    elif p.suffix.lower() == ".csv":
        pv = bi.preview_csv(p.read_text(), draw_date=a.draw_date, lab=a.lab, panel_id=a.panel_id, existing=existing)
    else:
        pv = bi.preview_json(json.loads(p.read_text()), existing=existing, source=p.name)
    print(f"panel {pv['panel']['panel_id']} · {pv['panel']['lab']} · draw {pv['panel']['draw_date']}")
    print(f"context: {json.dumps(pv['panel']['context'])}")
    for r in pv["rows"]:
        rng = f"{r['ref_low']}–{r['ref_high']}" if (r["ref_low"] is not None or r["ref_high"] is not None) else (r["ref_text"] or "")
        print(f"  {'DUP ' if r['duplicate'] else 'new '}{r['marker']:<22} {r['value_text']:>10} {r['unit']:<8} [{rng}]")
    print(f"{pv['new_count']} new, {pv['duplicate_count']} duplicate(s)")
    for w in pv["warnings"]:
        print(f"warning: {w}")
    if not a.commit:
        print("dry-run: nothing written (add --commit)")
        return 0
    res = bi.commit_preview(pv, user_id=a.user_id)
    print(f"committed: {res['inserted']} inserted, {len(res['skipped'])} skipped (duplicates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
