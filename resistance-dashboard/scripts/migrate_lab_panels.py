#!/usr/bin/env python3
"""Copy legacy biomarker panels into the labs blob. Dry-run unless --apply.

The labs blob (Turso ``labs_panels``, or the Pi file cache) is the only panel
store. This script:

- unions file-cache panels, the Turso labs blob, and legacy biomarker rows
- sets each panel date to the collected draw day when that day was stored
- splits a collapsed marker row only when both values are already in the
  stored text; otherwise leaves the row and reports its marker id

It prints counts and marker ids only. Measured values, names, and order
numbers are not printed.

  # preview (writes nothing, including no backup):
  python3 scripts/migrate_lab_panels.py --dry-run --user-id local
  python3 scripts/migrate_lab_panels.py --dry-run --allow-turso --user-id local

  # backup, then write:
  python3 scripts/migrate_lab_panels.py --apply --allow-turso --user-id local

Without --allow-turso, TURSO_* is ignored so a dev run cannot touch prod.
--dry-run is the default.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _lock_down(path: Path) -> None:
    try:
        mode = stat.S_IRUSR | stat.S_IWUSR
        if path.is_dir():
            mode |= stat.S_IXUSR
        os.chmod(path, mode)
    except OSError:
        pass


def _backup(user_id: str, root: Path) -> Path:
    """Full copy of the labs file cache, the labs blob, and biomarker rows."""
    from rt_dashboard.biomarkers_store import db_path, load_legacy_tables
    from rt_dashboard.labs_store import _user_dir, export_labs_blob

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = root / stamp
    n = 0
    while dest.exists():
        n += 1
        dest = root / f"{stamp}-{n}"
    dest.mkdir(parents=True, mode=0o700)
    _lock_down(dest)
    src = _user_dir(user_id)
    if src.is_dir():
        shutil.copytree(src, dest / "labs-file")
        for p in (dest / "labs-file").rglob("*"):
            _lock_down(p)
    db = db_path()
    if db.is_file():
        target = dest / "biomarkers.sqlite3"
        shutil.copy2(db, target)
        _lock_down(target)
    blob = export_labs_blob(user_id)
    if blob:
        out = dest / "labs-blob.txt"
        out.write_text(blob, encoding="utf-8")
        _lock_down(out)
    legacy = load_legacy_tables(user_id)
    if legacy.get("panels") or legacy.get("rows"):
        out = dest / "biomarker-rows.json"
        out.write_text(json.dumps(legacy), encoding="utf-8")
        _lock_down(out)
    return dest


def _print_plan(mode: str, plan: dict, *, backup: str) -> None:
    split_ids = ",".join(plan.get("split_marker_ids") or [])
    unsplit_ids = ",".join(plan.get("unsplit_marker_ids") or [])
    lines = [
        f"mode={mode}",
        f"labs_panels={plan['labs_panels']}",
        f"legacy_panels={plan['legacy_panels']}",
        f"panels_after={plan['panels_after']}",
        f"added_panels={plan['added_panels']}",
        f"draw_dates_corrected={plan['draw_dates_corrected']}",
        f"markers_split={len(plan.get('split_marker_ids') or [])}",
        f"split_marker_ids={split_ids}",
        f"unsplit_markers={len(plan.get('unsplit_marker_ids') or [])}",
        f"unsplit_marker_ids={unsplit_ids}",
        f"changed={1 if plan.get('changed') else 0}",
        f"backup={backup}",
    ]
    sys.stdout.write("\n".join(lines) + "\n")


def _apply(user_id: str, plan: dict) -> None:
    from rt_dashboard.labs_store import _ephemeral_host, _save_index, _write_store

    store = plan["store"]
    store["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_store(user_id, store)
    try:
        from rt_dashboard.turso_http import turso_enabled
    except Exception:
        turso_enabled = lambda: False  # noqa: E731
    if turso_enabled() and not _ephemeral_host():
        _save_index(user_id, store)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the plan and write nothing (default)")
    ap.add_argument("--apply", action="store_true", help="backup, then write the unified blob")
    ap.add_argument("--allow-turso", action="store_true", help="read/write Turso; otherwise TURSO_* is ignored")
    ap.add_argument("--user-id", default="", help="labs user id (empty means local)")
    ap.add_argument(
        "--backup-dir",
        default="",
        help="directory for the timestamped backup (default: config dir / lab-panel-backups)",
    )
    args = ap.parse_args(argv)
    if args.apply and args.dry_run:
        sys.stderr.write("pass only one of --dry-run and --apply\n")
        return 2
    if not args.allow_turso:
        os.environ.pop("TURSO_DATABASE_URL", None)
        os.environ.pop("TURSO_AUTH_TOKEN", None)
        os.environ["FITDASH_BIOMARKERS_BACKEND"] = "sqlite"
    from rt_dashboard.lab_panels import plan_unification
    from rt_dashboard.labs_store import labs_root

    try:
        plan = plan_unification(args.user_id)
    except Exception:
        sys.stderr.write("plan_error\n")
        return 1
    if not args.apply:
        _print_plan("dry-run", plan, backup="0")
        return 0
    if not plan["changed"]:
        _print_plan("apply", plan, backup="0")
        return 0
    root = Path(args.backup_dir).expanduser() if args.backup_dir else (labs_root().parent / "lab-panel-backups")
    try:
        dest = _backup(args.user_id, root)
    except Exception:
        sys.stderr.write("backup_error\n")
        return 1
    try:
        _apply(args.user_id, plan)
    except Exception:
        sys.stderr.write("write_error\n")
        return 1
    _print_plan("apply", plan, backup="1")
    # Path only — the directory name is a timestamp, not panel contents.
    sys.stdout.write(f"backup_dir={dest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
