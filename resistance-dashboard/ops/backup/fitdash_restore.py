#!/usr/bin/env python3
"""Restore a FitDash age archive.

``--dry-run`` is the default: decrypt, check the archive against the manifest,
and print a per-table row-count diff. ``--apply`` writes.

``--sqlite-only`` (the default) rebuilds Turso tables in a scratch SQLite file.
``--turso-new`` and ``--turso-existing`` write to Turso. Tables that are not in
the archive are never modified.

Local SQLite snapshots and state files land in ``--target`` (a scratch directory
unless ``--target-live`` is given together with ``--yes``).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from backup_common import (
    SCHEMA_VERSION,
    StagingDir,
    assert_create_table,
    chmod_private_dir,
    decode_cell,
    decrypt_with_age,
    dump_rows,
    extract_tar_gz,
    is_denied,
    load_jsonl,
    note,
    quote_ident,
    sha256_bytes,
    sha256_file,
    utc_now,
    format_utc,
)

MODES = ("sqlite-only", "turso-new", "turso-existing")


@dataclass
class RestoreOptions:
    archive: Path
    identity: Optional[Path] = None
    mode: str = "sqlite-only"
    apply: bool = False
    target: Optional[Path] = None
    target_live: bool = False
    yes: bool = False
    sample_decrypt: int = 0
    master_key: Optional[Path] = None
    age_bin: Optional[str] = None
    config_dir: Optional[Path] = None
    app_dir: Optional[Path] = None
    coach_state: Optional[Path] = None
    systemd_dir: Optional[Path] = None
    db_path: Optional[Path] = None
    coaching_db: Optional[Path] = None
    biomarkers_db: Optional[Path] = None
    sqlite_db: Optional[Path] = None
    state_dir: Optional[Path] = None


def load_master_key(path: Path) -> bytes:
    """Read a master key. Never creates one."""
    if not path.is_file():
        raise RuntimeError(f"master key file is missing: {path}")
    raw = path.read_bytes().strip()
    if not raw:
        raise RuntimeError("master key file is empty")
    try:
        key = base64.urlsafe_b64decode(raw + b"==")
    except Exception:
        key = hashlib.sha256(raw).digest()
    if len(key) < 32:
        key = hashlib.sha256(key).digest()
    return key[:32]


def open_archive(opts: RestoreOptions, staging: StagingDir) -> Path:
    archive = opts.archive
    if archive.is_dir():
        if not (archive / "manifest.json").is_file():
            raise RuntimeError(f"archive directory has no manifest.json: {archive}")
        return archive
    if not archive.is_file():
        raise RuntimeError(f"archive not found: {archive}")
    name = archive.name
    if name.endswith(".age"):
        if opts.identity is None:
            raise RuntimeError("an .age archive needs --identity KEYFILE")
        tar_path = staging.path / "archive.tar.gz"
        decrypt_with_age(archive, opts.identity, tar_path, age=opts.age_bin)
        tree = staging.path / "tree"
        extract_tar_gz(tar_path, tree)
        tar_path.unlink()
        return tree
    if name.endswith(".tar.gz"):
        note("fitdash-restore: reading a plaintext .tar.gz (not an age file)")
        tree = staging.path / "tree"
        extract_tar_gz(archive, tree)
        return tree
    raise RuntimeError("archive must be a directory, a .tar.gz.age file, or a .tar.gz file")


def read_manifest(root: Path) -> dict:
    path = root / "manifest.json"
    if not path.is_file():
        raise RuntimeError("manifest.json is missing from the archive")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("manifest.json is not an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(f"unsupported manifest schema_version: {payload.get('schema_version')}")
    return payload


def verify_archive(root: Path, manifest: dict) -> None:
    tables = manifest.get("tables") or {}
    if not isinstance(tables, dict):
        raise RuntimeError("manifest tables is not an object")
    for name, info in sorted(tables.items()):
        if not isinstance(info, dict):
            raise RuntimeError(f"manifest entry for {name} is not an object")
        jsonl = root / "turso" / f"{name}.jsonl"
        if not jsonl.is_file():
            raise RuntimeError(f"archive is missing turso/{name}.jsonl")
        digest = sha256_file(jsonl)
        rows = info.get("rows")
        actual_rows = _count_lines(jsonl)
        if digest != info.get("sha256") or actual_rows != rows:
            raise RuntimeError(
                f"archive hash mismatch for {name}: "
                f"rows {actual_rows} vs {rows}, sha256 {digest}"
            )
    for item in manifest.get("files") or []:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "")
        if not rel or rel == "manifest.json":
            continue
        path = root / rel
        if not path.is_file():
            raise RuntimeError(f"archive is missing {rel}")
        digest = sha256_file(path)
        if digest != item.get("sha256"):
            raise RuntimeError(f"archive hash mismatch for {rel}")


def _count_lines(path: Path) -> int:
    raw = path.read_bytes()
    if not raw:
        return 0
    return sum(1 for line in raw.decode("utf-8").splitlines() if line.strip())


def table_names(root: Path) -> list[str]:
    folder = root / "turso"
    if not folder.is_dir():
        return []
    return sorted(path.name[: -len(".jsonl")] for path in folder.glob("*.jsonl"))


def target_counts(conn: Any, names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in names:
        if not _table_exists(conn, name):
            counts[name] = 0
            continue
        cursor = conn.execute(f"SELECT COUNT(*) AS c FROM {quote_ident(name)}")
        row = cursor.fetchone()
        if isinstance(row, dict):
            counts[name] = int(row.get("c") or 0)
        else:
            counts[name] = int(row[0] if row else 0)
    return counts


def _table_exists(conn: Any, name: str) -> bool:
    cursor = conn.execute(
        "SELECT 1 AS ok FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    )
    return cursor.fetchone() is not None


def format_diff(manifest: dict, counts: dict[str, int]) -> str:
    tables = manifest.get("tables") or {}
    names = sorted(set(tables) | set(counts))
    lines = [f"{'table':<32} {'archive':>8} {'target':>8} {'diff':>8}"]
    for name in names:
        info = tables.get(name) or {}
        archive_rows = int(info.get("rows") or 0) if isinstance(info, dict) else 0
        target_rows = int(counts.get(name) or 0)
        lines.append(f"{name:<32} {archive_rows:8d} {target_rows:8d} {target_rows - archive_rows:+8d}")
    return "\n".join(lines)


def _columns(rows: list[dict]) -> list[str]:
    found: set[str] = set()
    for row in rows:
        found.update(str(key) for key in row)
    columns = sorted(found)
    for name in columns:
        quote_ident(name)
    return columns


def restore_table(conn: Any, name: str, create_sql: str, rows: list[dict], *, mode: str) -> None:
    quote_ident(name)
    exists = _table_exists(conn, name)
    if exists and mode == "turso-new":
        raise RuntimeError(f"{name} already exists; refusing --turso-new")
    if not exists:
        statement = assert_create_table(create_sql)
        conn.execute(statement)
    if not rows:
        return
    columns = _columns(rows)
    column_sql = ", ".join(quote_ident(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT OR REPLACE INTO {quote_ident(name)} ({column_sql}) VALUES ({placeholders})"
    bindings = [tuple(row.get(column) for column in columns) for row in rows]
    if hasattr(conn, "execute_transaction"):
        conn.execute_transaction([(sql, params) for params in bindings])
        return
    for params in bindings:
        conn.execute(sql, params)


def fetch_table(conn: Any, name: str) -> list[dict]:
    cursor = conn.execute(f"SELECT * FROM {quote_ident(name)}")
    description = cursor.description or []
    columns = [item[0] for item in description]
    rows: list[dict] = []
    for raw in cursor.fetchall():
        if isinstance(raw, dict):
            rows.append({key: decode_cell(value) for key, value in raw.items()})
            continue
        rows.append({columns[index]: raw[index] for index in range(len(columns))})
    return rows


def verify_database(conn: Any, manifest: dict) -> None:
    tables = manifest.get("tables") or {}
    for name, info in sorted(tables.items()):
        if not isinstance(info, dict):
            raise RuntimeError(f"manifest entry for {name} is not an object")
        if not _table_exists(conn, name):
            raise RuntimeError(f"restored database is missing {name}")
        rows = fetch_table(conn, name)
        payload = dump_rows(rows)
        digest = sha256_bytes(payload)
        if len(rows) != info.get("rows") or digest != info.get("sha256"):
            raise RuntimeError(
                f"restored {name} does not match the manifest "
                f"(rows {len(rows)} vs {info.get('rows')})"
            )


def sample_decrypt_rows(rows: list[dict], key: bytes, limit: int) -> tuple[int, int]:
    from rt_dashboard.crypto_box import open_box

    ok = 0
    failed = 0
    for row in rows:
        if ok >= limit:
            break
        token = row.get("exercises_json")
        if not isinstance(token, str) or not token.strip():
            continue
        if token.lstrip().startswith("[") or token.lstrip().startswith("{"):
            continue
        user = row.get("user_id") or "default"
        try:
            open_box(token, aad=f"user:{user}:workout", key=key)
        except ValueError:
            failed += 1
            continue
        ok += 1
    return ok, failed


def _copy_private(src: Path, dest: Path) -> None:
    if is_denied(src) or is_denied(dest):
        note(f"fitdash-restore: skipping denylisted path {dest.name}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".restore-tmp")
    temporary.write_bytes(src.read_bytes())
    os.chmod(temporary, 0o600)
    os.replace(temporary, dest)


def restore_files(root: Path, opts: RestoreOptions, scratch: Path) -> None:
    """Copy sqlite snapshots and pi-local files into the scratch dir.

    With ``--target-live --yes``, also copy them onto the live paths.
    """
    sqlite_src = root / "sqlite"
    if sqlite_src.is_dir():
        for path in sorted(sqlite_src.iterdir()):
            if path.is_file() and not is_denied(path):
                _copy_private(path, scratch / "sqlite" / path.name)
    pi_src = root / "pi-local"
    if pi_src.is_dir():
        for path in sorted(pi_src.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(pi_src).as_posix()
            if is_denied(path) or is_denied(Path(rel)):
                note(f"fitdash-restore: skipping denylisted pi-local/{rel}")
                continue
            _copy_private(path, scratch / "pi-local" / rel)
    code = root / "pi-local-code.tgz"
    if code.is_file():
        _copy_private(code, scratch / "pi-local-code.tgz")
    if opts.target_live:
        _restore_live(scratch, opts)


def _live_db_paths(opts: RestoreOptions) -> dict[str, Path]:
    from fitdash_backup import BackupOptions, resolve_db_paths

    mapped = resolve_db_paths(
        BackupOptions(
            config_dir=opts.config_dir,
            db_path=opts.db_path,
            coaching_db=opts.coaching_db,
            biomarkers_db=opts.biomarkers_db,
        )
    )
    return mapped


def _restore_live(scratch: Path, opts: RestoreOptions) -> None:
    from fitdash_backup import default_app_dir, default_coach_state, default_config_dir

    config = opts.config_dir or default_config_dir()
    app_dir = opts.app_dir or default_app_dir()
    coach = opts.coach_state or default_coach_state()
    systemd_dir = opts.systemd_dir or (Path.home() / ".config" / "systemd" / "user")
    db_paths = _live_db_paths(opts)
    sqlite_dir = scratch / "sqlite"
    for name, dest in db_paths.items():
        src = sqlite_dir / name
        if src.is_file():
            _copy_private(src, dest)
    pi = scratch / "pi-local"
    if not pi.is_dir():
        return
    for path in sorted(pi.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(pi).as_posix()
        if rel == "coach_state.json":
            _copy_private(path, coach)
        elif rel == "served-sha":
            _copy_private(path, app_dir / ".served-sha")
        elif rel.startswith("systemd/"):
            _copy_private(path, systemd_dir / Path(rel).name)
        elif rel.startswith("hsa/") or rel.startswith("labs/"):
            _copy_private(path, config / rel)
        else:
            _copy_private(path, config / Path(rel).name)


def default_scratch(opts: RestoreOptions) -> Path:
    if opts.target is not None:
        return opts.target
    from fitdash_backup import default_state_dir

    base = opts.state_dir or default_state_dir()
    stamp = format_utc(utc_now()).replace(":", "").replace("-", "")
    return base / f"restore-{stamp}"


def run_restore(opts: RestoreOptions, *, turso_conn: Any = None) -> int:
    if opts.mode not in MODES:
        note(f"fitdash-restore: unknown mode {opts.mode}")
        return 1
    if opts.target_live and not opts.yes:
        note("fitdash-restore: refusing to write live paths without --target-live and --yes")
        return 1
    if opts.sample_decrypt and opts.master_key is None:
        note("fitdash-restore: --sample-decrypt needs --master-key")
        return 1
    try:
        with StagingDir(prefix="fitdash-restore-") as staging:
            root = open_archive(opts, staging)
            manifest = read_manifest(root)
            verify_archive(root, manifest)
            names = table_names(root)
            extra = [name for name in names if name not in (manifest.get("tables") or {})]
            if extra:
                raise RuntimeError("archive has tables missing from the manifest: " + ", ".join(extra))

            scratch = opts.target if opts.target is not None else (default_scratch(opts) if opts.apply else None)
            sqlite_path = opts.sqlite_db
            if opts.mode == "sqlite-only" and sqlite_path is None and scratch is not None:
                sqlite_path = scratch / "turso-restore.sqlite3"

            conn: Any = turso_conn
            owns_conn = False
            if opts.mode == "sqlite-only":
                if opts.apply:
                    if sqlite_path is None or scratch is None:
                        raise RuntimeError("sqlite-only restore has no scratch path")
                    chmod_private_dir(sqlite_path.parent)
                    conn = sqlite3.connect(str(sqlite_path), isolation_level=None)
                    owns_conn = True
                elif sqlite_path is not None and sqlite_path.is_file():
                    conn = sqlite3.connect(f"file:{sqlite_path.resolve().as_posix()}?mode=ro", uri=True)
                    owns_conn = True
            elif conn is None:
                from rt_dashboard.turso_http import connect

                conn = connect()
                owns_conn = True

            if conn is None:
                counts = {name: 0 for name in names}
            else:
                try:
                    counts = target_counts(conn, names)
                finally:
                    if owns_conn and not opts.apply:
                        conn.close()
                        conn = None
                        owns_conn = False
            print(format_diff(manifest, counts))

            if opts.sample_decrypt:
                assert opts.master_key is not None
                key = load_master_key(opts.master_key)
                workout = root / "turso" / "workout_sessions.jsonl"
                rows = load_jsonl(workout) if workout.is_file() else []
                ok, failed = sample_decrypt_rows(rows, key, opts.sample_decrypt)
                note(f"fitdash-restore: sample decrypt ok={ok} failed={failed}")
                if failed or ok == 0:
                    raise RuntimeError("sample decrypt did not succeed")

            if not opts.apply:
                note("fitdash-restore: dry-run (pass --apply to write)")
                return 0

            if opts.mode == "sqlite-only":
                if sqlite_path is None or conn is None or scratch is None:
                    raise RuntimeError("sqlite-only restore has no scratch database")
                conn.execute("BEGIN")
                try:
                    _apply_tables(conn, root, names, opts.mode)
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                verify_database(conn, manifest)
                conn.close()
                owns_conn = False
            else:
                if conn is None or scratch is None:
                    raise RuntimeError("restore has no target connection")
                _apply_tables(conn, root, names, opts.mode)
                verify_database(conn, manifest)
                if owns_conn:
                    conn.close()
                    owns_conn = False

            chmod_private_dir(scratch)
            manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            manifest_path = scratch / "manifest.json"
            manifest_path.write_text(manifest_text, encoding="utf-8")
            os.chmod(manifest_path, 0o600)
            restore_files(root, opts, scratch)
            note(f"fitdash-restore: wrote {scratch}")
            return 0
    except Exception as exc:  # noqa: BLE001 — operator-facing CLI
        note(f"fitdash-restore: {exc}")
        return 1


def _apply_tables(conn: Any, root: Path, names: list[str], mode: str) -> None:
    """Create and fill only tables that are in the archive."""
    for name in names:
        quote_ident(name)
        sql_path = root / "turso" / f"{name}.sql"
        jsonl = root / "turso" / f"{name}.jsonl"
        create_sql = sql_path.read_text(encoding="utf-8") if sql_path.is_file() else ""
        rows = load_jsonl(jsonl)
        restore_table(conn, name, create_sql, rows, mode=mode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Restore a FitDash backup archive")
    parser.add_argument("--archive", required=True, help=" .tar.gz.age file or an extracted directory")
    parser.add_argument("--identity", default=None, help="age identity (private key) for -d")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sqlite-only", action="store_const", dest="mode", const="sqlite-only")
    mode.add_argument("--turso-new", action="store_const", dest="mode", const="turso-new")
    mode.add_argument("--turso-existing", action="store_const", dest="mode", const="turso-existing")
    parser.set_defaults(mode="sqlite-only")
    parser.add_argument("--dry-run", action="store_true", help="default; print the row-count diff and do not write")
    parser.add_argument("--apply", action="store_true", help="write the restore")
    parser.add_argument("--target", default=None, help="scratch directory (never a live path unless --target-live)")
    parser.add_argument("--target-live", action="store_true", help="also copy state files onto the live paths")
    parser.add_argument("--yes", action="store_true", help="required together with --target-live")
    parser.add_argument("--sample-decrypt", type=int, default=0, metavar="N")
    parser.add_argument("--master-key", default=None, help="master key file for --sample-decrypt (read only)")
    parser.add_argument("--age-bin", default=None)
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--app-dir", default=None)
    parser.add_argument("--coach-state", default=None)
    parser.add_argument("--systemd-dir", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--coaching-db", default=None)
    parser.add_argument("--biomarkers-db", default=None)
    parser.add_argument("--sqlite-db", default=None, help="scratch SQLite file for --sqlite-only")
    parser.add_argument("--state-dir", default=None)
    return parser


def options_from_args(args: argparse.Namespace) -> RestoreOptions:
    def as_path(value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        return Path(value).expanduser()

    apply = bool(args.apply) and not bool(args.dry_run)
    return RestoreOptions(
        archive=Path(args.archive).expanduser(),
        identity=as_path(args.identity),
        mode=args.mode,
        apply=apply,
        target=as_path(args.target),
        target_live=bool(args.target_live),
        yes=bool(args.yes),
        sample_decrypt=int(args.sample_decrypt or 0),
        master_key=as_path(args.master_key),
        age_bin=args.age_bin,
        config_dir=as_path(args.config_dir),
        app_dir=as_path(args.app_dir),
        coach_state=as_path(args.coach_state),
        systemd_dir=as_path(args.systemd_dir),
        db_path=as_path(args.db_path),
        coaching_db=as_path(args.coaching_db),
        biomarkers_db=as_path(args.biomarkers_db),
        sqlite_db=as_path(args.sqlite_db),
        state_dir=as_path(args.state_dir),
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_restore(options_from_args(args))


if __name__ == "__main__":
    sys.exit(main())
