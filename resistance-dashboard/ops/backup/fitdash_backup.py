#!/usr/bin/env python3
"""Nightly FitDash backup: Turso JSONL, local SQLite snapshots, Pi state.

Read-only against Turso. The plaintext tree is built in a 0700 temp directory
and deleted in a finally block. The uploaded object is gzip (stdlib tarfile)
wrapped with age. Output name: fitdash-YYYYMMDD-HHMM.tar.gz.age.

Exit 75 means a workout write happened in the last 30 minutes. The systemd
unit treats 75 as success for alerting and starts a 30-minute retry timer.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backup_common import (
    DEFER_WINDOW,
    EXCLUDED_TABLES,
    EXIT_DEFERRED,
    EXIT_ERROR,
    EXIT_OK,
    SCHEMA_VERSION,
    StagingDir,
    assert_read_only,
    chmod_private_dir,
    dump_rows,
    encrypt_with_age,
    env_var_names,
    fetch_dicts,
    format_utc,
    is_denied,
    iter_regular_files,
    load_recipient_keys,
    make_tar_gz,
    note,
    path_is_skipped,
    quote_ident,
    read_execute,
    require_age,
    row_field,
    sha256_bytes,
    sha256_file,
    snapshot_sqlite,
    timestamp_is_recent,
    utc_now,
    write_json_private,
)

UNIT_NAME = re.compile(
    r"^(resistance-dashboard|fitdash.*|.*fitdash.*)\.(service|timer)$",
    re.IGNORECASE,
)
SQL_TABLES = (
    "SELECT name, sql FROM sqlite_master "
    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
)
CODE_SKIP_SUFFIXES = (".pyc", ".db", ".sqlite", ".sqlite3")


@dataclass
class BackupOptions:
    dry_run: bool = False
    no_upload: bool = False
    out_dir: Optional[Path] = None
    recipients: Optional[Path] = None
    config_dir: Optional[Path] = None
    app_dir: Optional[Path] = None
    state_dir: Optional[Path] = None
    coach_state: Optional[Path] = None
    systemd_dir: Optional[Path] = None
    code_hashes: Optional[Path] = None
    git_dir: Optional[Path] = None
    no_git: bool = False
    age_bin: Optional[str] = None
    now: Optional[datetime] = None
    prune_apply: bool = False
    repo: Optional[str] = None
    db_path: Optional[Path] = None
    coaching_db: Optional[Path] = None
    biomarkers_db: Optional[Path] = None
    allow_missing_turso: bool = False


@dataclass
class BackupResult:
    status: str
    exit_code: int
    asset_name: Optional[str] = None
    size: Optional[int] = None
    counts: dict[str, int] = field(default_factory=dict)
    error: Optional[str] = None
    tag: Optional[str] = None
    code_drift: str = "unavailable"
    archive: Optional[str] = None


def default_config_dir() -> Path:
    override = (os.environ.get("RESISTANCE_DASHBOARD_CONFIG_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "resistance-dashboard"


def default_state_dir() -> Path:
    override = (os.environ.get("FITDASH_BACKUP_STATE_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "state" / "fitdash-backup"


def default_app_dir() -> Path:
    override = (os.environ.get("FITDASH_APP_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "apps" / "fitdash"


def default_recipients() -> Path:
    override = (os.environ.get("FITDASH_BACKUP_RECIPIENTS") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent / "recipients.txt"


def default_coach_state() -> Path:
    override = (os.environ.get("FITDASH_COACH_STATE_DIR") or "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".local" / "state" / "fitdash-coach"
    return base / "coach_state.json"


def _first_existing(*candidates: Optional[Path]) -> Optional[Path]:
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def resolve_db_paths(opts: BackupOptions) -> dict[str, Path]:
    config = opts.config_dir or default_config_dir()
    env_fitdash = (os.environ.get("FITDASH_DB_PATH") or "").strip()
    env_coach = (os.environ.get("FITDASH_COACHING_TARGETS_DB") or "").strip()
    env_bio = (os.environ.get("FITDASH_BIOMARKERS_DB") or "").strip()
    paths = {
        "fitdash.db": opts.db_path
        or (Path(env_fitdash).expanduser() if env_fitdash else config / "fitdash.db"),
        "coaching_targets.sqlite3": opts.coaching_db
        or (Path(env_coach).expanduser() if env_coach else config / "coaching_targets.sqlite3"),
        "biomarkers.sqlite3": opts.biomarkers_db
        or (Path(env_bio).expanduser() if env_bio else config / "biomarkers.sqlite3"),
    }
    return paths


def open_turso() -> Any:
    from rt_dashboard.turso_http import connect, turso_enabled

    if not turso_enabled():
        raise RuntimeError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are required")
    return connect()


def list_tables(conn: Any) -> list[tuple[str, str]]:
    cursor = read_execute(conn, SQL_TABLES)
    found: list[tuple[str, str]] = []
    for row in fetch_dicts(cursor):
        name = str(row.get("name") or "")
        if not name or name.lower() in EXCLUDED_TABLES or name.startswith("sqlite_"):
            continue
        found.append((name, str(row.get("sql") or "")))
    found.sort(key=lambda item: item[0])
    return found


def dump_table(conn: Any, name: str) -> list[dict]:
    sql = f"SELECT * FROM {quote_ident(name)}"
    cursor = read_execute(conn, sql)
    return fetch_dicts(cursor)


def workout_timestamps(conn: Any) -> list[Any]:
    sql = (
        'SELECT MAX(updated_at) AS updated_at, MAX(created_at) AS created_at '
        'FROM "workout_sessions"'
    )
    try:
        cursor = read_execute(conn, sql)
        row = cursor.fetchone()
    except Exception:  # noqa: BLE001 — missing table or columns is not a recent write
        return []
    return [row_field(row, "updated_at", 0), row_field(row, "created_at", 1)]


def local_workout_timestamps(path: Path) -> list[Any]:
    if not path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(str(path))
        except sqlite3.Error:
            return []
    try:
        return workout_timestamps(conn)
    finally:
        conn.close()


def workout_is_recent(values: list[Any], now: datetime) -> bool:
    return any(timestamp_is_recent(value, now, DEFER_WINDOW) for value in values)


def collect_pi_local(opts: BackupOptions) -> list[tuple[str, Path]]:
    config = opts.config_dir or default_config_dir()
    found: list[tuple[str, Path]] = []
    if config.is_dir():
        for path in sorted(config.glob("phase_barometer*.json")):
            if path.is_file() and not path.is_symlink() and not is_denied(path):
                found.append((f"pi-local/{path.name}", path))
        for name in ("ai_planner_state.json", "restock_hold.json"):
            path = config / name
            if path.is_file() and not path.is_symlink() and not is_denied(path):
                found.append((f"pi-local/{name}", path))
        for folder in ("hsa", "labs"):
            base = config / folder
            if not base.is_dir():
                continue
            for path in iter_regular_files(base):
                relative = path.relative_to(base).as_posix()
                if path_is_skipped(relative) or is_denied(path):
                    continue
                found.append((f"pi-local/{folder}/{relative}", path))
    coach = opts.coach_state or default_coach_state()
    if coach.is_file() and not coach.is_symlink() and not is_denied(coach):
        found.append(("pi-local/coach_state.json", coach))
    systemd_dir = opts.systemd_dir or (Path.home() / ".config" / "systemd" / "user")
    if systemd_dir.is_dir():
        for path in sorted(systemd_dir.iterdir()):
            if not path.is_file() or path.is_symlink() or is_denied(path):
                continue
            if UNIT_NAME.match(path.name):
                found.append((f"pi-local/systemd/{path.name}", path))
    app_dir = opts.app_dir or default_app_dir()
    served = app_dir / ".served-sha"
    if served.is_file() and not served.is_symlink():
        found.append(("pi-local/served-sha", served))
    return found


def read_served_sha(app_dir: Path) -> Optional[str]:
    path = app_dir / ".served-sha"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return text or None


def _load_hash_list(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2 or len(parts[0]) != 64:
            raise RuntimeError(f"bad code-hash line (want sha256 and path): {stripped[:80]}")
        rel = parts[-1]
        if rel.startswith("*"):
            rel = rel[1:]
        rel = rel[2:] if rel.startswith("./") else rel
        mapping[rel] = parts[0].lower()
    if not mapping:
        raise RuntimeError(f"code hash list is empty: {path}")
    return mapping


def _git_tree(git_dir: Path, sha: str) -> Optional[dict[str, str]]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(git_dir), "ls-tree", "-r", sha],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    mapping: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        meta, rel = line.split("\t", 1)
        bits = meta.split()
        if len(bits) < 3 or bits[1] != "blob":
            continue
        mapping[rel] = bits[2]
    return mapping or None


def _detect_prefix(local_rels: list[str], baseline: dict[str, str]) -> str:
    if any(rel in baseline for rel in local_rels):
        direct = sum(1 for rel in local_rels if rel in baseline)
    else:
        direct = 0
    prefixes: set[str] = set()
    for key in baseline:
        parts = key.split("/")
        for index in range(1, len(parts)):
            prefixes.add("/".join(parts[:index]))
    best = ""
    best_score = direct
    for prefix in prefixes:
        score = sum(1 for rel in local_rels if f"{prefix}/{rel}" in baseline)
        if score > best_score:
            best = prefix
            best_score = score
    if best_score <= 0:
        return ""
    return best


def _scope_baseline(baseline: dict[str, str], prefix: str) -> dict[str, str]:
    if not prefix:
        return dict(baseline)
    head = prefix + "/"
    return {key[len(head) :]: value for key, value in baseline.items() if key.startswith(head)}


def _hash_files_sha256(files: list[tuple[str, Path]]) -> dict[str, str]:
    return {rel: sha256_file(path) for rel, path in files}


def _hash_files_git(files: list[tuple[str, Path]]) -> Optional[dict[str, str]]:
    if not files or shutil.which("git") is None:
        return None
    payload = "\n".join(str(path) for _rel, path in files) + "\n"
    try:
        proc = subprocess.run(
            ["git", "hash-object", "--stdin-paths"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    hashes = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if len(hashes) != len(files):
        return None
    return {files[index][0]: hashes[index] for index in range(len(files))}


def _candidate_git_dirs(opts: BackupOptions) -> list[Path]:
    if opts.no_git:
        return []
    found: list[Path] = []
    if opts.git_dir:
        found.append(opts.git_dir)
    env = (os.environ.get("FITDASH_BACKUP_GIT_DIR") or "").strip()
    if env:
        found.append(Path(env).expanduser())
    app = opts.app_dir or default_app_dir()
    found.append(app)
    here = Path(__file__).resolve()
    found.extend(here.parents)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if (path / ".git").exists():
            unique.append(path)
    return unique


def code_drift(opts: BackupOptions, app_dir: Path) -> dict[str, Any]:
    """Compare the app dir to git at .served-sha, or to a sha256 list."""
    report: dict[str, Any] = {
        "status": "unavailable",
        "warning": False,
        "baseline": "none",
        "files": [],
        "reason": "",
    }
    if not app_dir.is_dir():
        report["reason"] = "app dir is missing"
        return report
    local_files: list[tuple[str, Path]] = []
    for path in iter_regular_files(app_dir):
        rel = path.relative_to(app_dir).as_posix()
        if rel in {".served-sha"} or path_is_skipped(rel) or is_denied(path):
            continue
        if rel.endswith(CODE_SKIP_SUFFIXES):
            continue
        local_files.append((rel, path))
    sha = read_served_sha(app_dir)
    baseline: Optional[dict[str, str]] = None
    kind = "none"
    if not opts.no_git and sha and shutil.which("git"):
        for git_dir in _candidate_git_dirs(opts):
            tree = _git_tree(git_dir, sha)
            if tree:
                prefix = _detect_prefix([rel for rel, _path in local_files], tree)
                scoped = _scope_baseline(tree, prefix)
                overlap = sum(1 for rel, _path in local_files if rel in scoped)
                if overlap:
                    baseline = scoped
                    kind = "git"
                    break
    hash_path = opts.code_hashes
    if hash_path is None:
        env_hashes = (os.environ.get("FITDASH_BACKUP_CODE_HASHES") or "").strip()
        if env_hashes:
            hash_path = Path(env_hashes).expanduser()
    if baseline is None and hash_path is not None:
        if not hash_path.is_file():
            report["reason"] = f"code hash list is missing: {hash_path}"
            return report
        baseline = _load_hash_list(hash_path)
        prefix = _detect_prefix([rel for rel, _path in local_files], baseline)
        baseline = _scope_baseline(baseline, prefix)
        kind = "sha256"
    if baseline is None:
        if not sha:
            report["reason"] = "no .served-sha and no code hash list"
        else:
            report["reason"] = "git baseline unavailable and no code hash list"
        return report
    if kind == "git":
        local_hashes = _hash_files_git(local_files)
        if local_hashes is None:
            report["reason"] = "git hash-object failed"
            return report
    else:
        local_hashes = _hash_files_sha256(local_files)
    overlap = [rel for rel in local_hashes if rel in baseline]
    if not overlap:
        report["reason"] = "app dir does not overlap the code baseline"
        return report
    drifted = sorted(
        rel
        for rel, digest in local_hashes.items()
        if rel not in baseline or baseline[rel] != digest
    )
    report["baseline"] = kind
    report["files"] = drifted
    report["status"] = "drift" if drifted else "clean"
    report["warning"] = bool(drifted)
    return report


def pack_drift(app_dir: Path, files: list[str], dest: Path) -> None:
    import tarfile

    with tarfile.open(dest, "w:gz") as archive:
        for rel in files:
            path = app_dir / rel
            if not path.is_file() or path.is_symlink() or is_denied(path) or path_is_skipped(rel):
                continue
            archive.add(path, arcname=rel, recursive=False)


def copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = src.read_bytes()
    dest.write_bytes(data)
    os.chmod(dest, 0o600)


def build_manifest(
    *,
    now: datetime,
    app_sha: Optional[str],
    tables: dict[str, dict],
    files: list[dict],
    env_names: list[str],
    drift: dict,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": format_utc(now),
        "app_sha": app_sha,
        "tables": tables,
        "files": files,
        "env_var_names": env_names,
        "code_drift": {
            "status": drift.get("status"),
            "warning": bool(drift.get("warning")),
            "baseline": drift.get("baseline"),
            "files": list(drift.get("files") or []),
            "reason": drift.get("reason") or "",
        },
        "compression": "gzip",
    }


def file_inventory(root: Path) -> list[dict]:
    items: list[dict] = []
    for path in iter_regular_files(root):
        rel = path.relative_to(root).as_posix()
        if rel == "manifest.json":
            continue
        items.append({"path": rel, "sha256": sha256_file(path), "bytes": path.stat().st_size})
    items.sort(key=lambda item: item["path"])
    return items


def release_body(manifest: dict) -> str:
    tables = manifest.get("tables") or {}
    parts = [
        f"{name}={info.get('rows')}"
        for name, info in sorted(tables.items())
        if isinstance(info, dict)
    ]
    drift = (manifest.get("code_drift") or {}).get("status")
    lines = [
        "FitDash backup",
        f"timestamp: {manifest.get('timestamp')}",
        f"app_sha: {manifest.get('app_sha') or 'unknown'}",
        f"tables: {' '.join(parts)}",
        f"code_drift: {drift}",
    ]
    return "\n".join(lines) + "\n"


def choose_tag(client: Any, now: datetime) -> str:
    clock = now.astimezone(timezone.utc)
    ymd = clock.strftime("%Y%m%d")
    candidates = [
        f"bk-{ymd}",
        f"bk-{ymd}-{clock.strftime('%H%M')}",
        f"bk-{ymd}-{clock.strftime('%H%M%S')}",
    ]
    for tag in candidates:
        if not client.tag_taken(tag):
            return tag
    raise RuntimeError(f"backup tags already exist for {ymd}")


def _result_payload(result: BackupResult, now: datetime) -> dict:
    return {
        "status": result.status,
        "timestamp": format_utc(now),
        "asset_name": result.asset_name,
        "size": result.size,
        "counts": result.counts,
        "error": result.error,
        "tag": result.tag,
        "code_drift": result.code_drift,
        "archive": result.archive,
    }


def write_last(state_dir: Path, result: BackupResult, now: datetime) -> None:
    write_json_private(state_dir / "last.json", _result_payload(result, now))


def prune_apply_enabled(opts: BackupOptions) -> bool:
    if opts.prune_apply:
        return True
    flag = (os.environ.get("FITDASH_BACKUP_PRUNE_APPLY") or "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def _fail(result: BackupResult, state_dir: Path, now: datetime, message: str) -> BackupResult:
    result.status = "error"
    result.exit_code = EXIT_ERROR
    result.error = message
    note(f"fitdash-backup: {message}")
    try:
        write_last(state_dir, result, now)
    except OSError as exc:
        note(f"fitdash-backup: could not write last.json: {exc}")
    return result


def run_backup(opts: BackupOptions, *, turso_conn: Any = None, github: Any = None) -> BackupResult:
    now = opts.now or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    state_dir = opts.state_dir or default_state_dir()
    out_dir = opts.out_dir or (state_dir / "out")
    result = BackupResult(status="error", exit_code=EXIT_ERROR)
    app_dir = opts.app_dir or default_app_dir()

    try:
        conn = turso_conn
        if conn is None and not opts.allow_missing_turso:
            conn = open_turso()
        recent_values: list[Any] = []
        if conn is not None:
            recent_values.extend(workout_timestamps(conn))
        for db_path in resolve_db_paths(opts).values():
            recent_values.extend(local_workout_timestamps(db_path))
        if workout_is_recent(recent_values, now):
            result.status = "deferred"
            result.exit_code = EXIT_DEFERRED
            result.error = "workout write in the last 30 minutes"
            note("fitdash-backup: deferred (workout write in the last 30 minutes)")
            write_last(state_dir, result, now)
            return result

        recipients = opts.recipients or default_recipients()
        # Fail closed before any plaintext tree exists.
        load_recipient_keys(recipients)
        require_age(opts.age_bin)

        if conn is None:
            return _fail(result, state_dir, now, "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are required")

        chmod_private_dir(out_dir)
        with StagingDir() as staging:
            root = staging.path
            tables_meta: dict[str, dict] = {}
            counts: dict[str, int] = {}
            for name, create_sql in list_tables(conn):
                rows = dump_table(conn, name)
                payload = dump_rows(rows)
                table_dir = root / "turso"
                table_dir.mkdir(parents=True, exist_ok=True)
                jsonl = table_dir / f"{name}.jsonl"
                sql_path = table_dir / f"{name}.sql"
                jsonl.write_bytes(payload)
                sql_path.write_text((create_sql or "").strip() + "\n", encoding="utf-8")
                os.chmod(jsonl, 0o600)
                os.chmod(sql_path, 0o600)
                tables_meta[name] = {"rows": len(rows), "sha256": sha256_bytes(payload)}
                counts[name] = len(rows)
            result.counts = counts

            for label, source in resolve_db_paths(opts).items():
                if not source.is_file():
                    continue
                dest = root / "sqlite" / label
                snapshot_sqlite(source, dest)

            for rel, source in collect_pi_local(opts):
                if is_denied(Path(rel)) or is_denied(source):
                    continue
                copy_file(source, root / rel)

            drift = code_drift(opts, app_dir)
            result.code_drift = str(drift.get("status") or "unavailable")
            drifted = list(drift.get("files") or [])
            if drifted:
                note(
                    "fitdash-backup: code drift warning ("
                    + str(len(drifted))
                    + " file(s)); backup continues"
                )
                pack_drift(app_dir, drifted, root / "pi-local-code.tgz")

            config = opts.config_dir or default_config_dir()
            manifest = build_manifest(
                now=now,
                app_sha=read_served_sha(app_dir),
                tables=tables_meta,
                files=file_inventory(root),
                env_names=env_var_names(config / "env"),
                drift=drift,
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            os.chmod(manifest_path, 0o600)
            # The manifest lists every other file. It is not hashed into itself.
            stamp = now.astimezone(timezone.utc)
            asset_name = "fitdash-" + stamp.strftime("%Y%m%d-%H%M") + ".tar.gz.age"
            tar_path = root / "archive.tar.gz"
            make_tar_gz(root, tar_path)
            archive_path = out_dir / asset_name
            encrypt_with_age(tar_path, recipients, archive_path, age=opts.age_bin)
            result.asset_name = asset_name
            result.size = archive_path.stat().st_size
            result.archive = str(archive_path)

        # Staging is gone. Only the age file remains on disk.
        upload = not opts.dry_run and not opts.no_upload
        if not upload:
            result.status = "dry-run" if opts.dry_run else "no-upload"
            result.exit_code = EXIT_OK
            result.error = None
            write_last(state_dir, result, now)
            note(f"fitdash-backup: {result.status} wrote {result.archive}")
            return result

        if github is None:
            from gh_releases import client_from_env

            github = client_from_env(opts.repo)
        tag = choose_tag(github, now)
        body = release_body(manifest)
        release = github.create_release(tag, tag, body)
        github.upload_asset(release, asset_name, Path(result.archive).read_bytes(), "application/octet-stream")
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        github.upload_asset(release, "manifest.json", manifest_bytes, "application/json")
        result.tag = tag
        result.status = "ok"
        result.exit_code = EXIT_OK
        result.error = None
        try:
            Path(result.archive).unlink()
            result.archive = None
        except OSError as exc:
            note(f"fitdash-backup: uploaded but could not remove local archive: {exc}")
        try:
            from fitdash_prune import default_verified_file, prune, render_plan

            plan = prune(
                github,
                apply=prune_apply_enabled(opts),
                verified_file=default_verified_file() if opts.state_dir is None else state_dir / "last-verified-good",
            )
            note(render_plan(plan))
        except Exception as exc:  # noqa: BLE001
            return _fail(result, state_dir, now, f"uploaded but prune failed: {exc}")
        write_last(state_dir, result, now)
        note(f"fitdash-backup: uploaded {asset_name} as {tag}")
        return result
    except Exception as exc:  # noqa: BLE001 — last.json must record the failure
        return _fail(result, state_dir, now, str(exc))


def schedule_retry() -> int:
    """systemd ExecStopPost. Exit 75 starts the 30-minute retry timer.

    Always returns 0. A failing stop hook would mark a deferred run as failed.
    """
    status = (os.environ.get("EXIT_STATUS") or "").strip()
    if status != str(EXIT_DEFERRED):
        return EXIT_OK
    systemctl = shutil.which("systemctl")
    if not systemctl:
        note("fitdash-backup: deferred, systemctl not available, retry timer not started")
        return EXIT_OK
    subprocess.run([systemctl, "--user", "stop", "fitdash-backup-retry.timer"], check=False)
    proc = subprocess.run(
        [systemctl, "--user", "start", "fitdash-backup-retry.timer"],
        check=False,
    )
    if proc.returncode != 0:
        note("fitdash-backup: deferred, could not start fitdash-backup-retry.timer")
    else:
        note("fitdash-backup: deferred, retry scheduled in 30 minutes")
    return EXIT_OK


def record_failure(state_dir: Optional[Path] = None) -> int:
    """OnFailure hook. Writes last.json and a journal line. Does not page anyone."""
    directory = state_dir or default_state_dir()
    path = directory / "last.json"
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}
    payload.setdefault("asset_name", None)
    payload.setdefault("size", None)
    payload.setdefault("counts", {})
    if not payload.get("error"):
        payload["error"] = "fitdash-backup failed (see the journal)"
        payload["timestamp"] = format_utc(utc_now())
    payload["status"] = "error"
    try:
        write_json_private(path, payload)
    except OSError as exc:
        note(f"fitdash-backup: could not write last.json: {exc}")
    message = payload.get("error") or "fitdash-backup failed"
    note(f"fitdash-backup failed: {message}")
    return EXIT_OK


def _parse_now(text: str) -> datetime:
    from backup_common import parse_timestamp

    parsed = parse_timestamp(text)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"not a timestamp: {text}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Encrypt and upload a FitDash backup")
    parser.add_argument("--dry-run", action="store_true", help="build and encrypt locally, do not upload")
    parser.add_argument("--no-upload", action="store_true", help="build and encrypt locally, do not upload")
    parser.add_argument("--out", dest="out_dir", default=None, help="directory for the .age file")
    parser.add_argument("--recipients", default=None, help="age recipients file (public keys)")
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--app-dir", default=None, help="Pi app dir (default ~/apps/fitdash)")
    parser.add_argument("--state-dir", default=None, help="directory for last.json")
    parser.add_argument("--coach-state", default=None)
    parser.add_argument("--systemd-dir", default=None)
    parser.add_argument("--code-hashes", default=None, help="sha256 list used when git is unavailable")
    parser.add_argument("--git-dir", default=None)
    parser.add_argument("--no-git", action="store_true")
    parser.add_argument("--age-bin", default=None)
    parser.add_argument("--now", type=_parse_now, default=None, help="override the clock (UTC timestamp)")
    parser.add_argument(
        "--prune-apply",
        action="store_true",
        help="after a successful upload, delete releases outside the retention set",
    )
    parser.add_argument("--repo", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--coaching-db", default=None)
    parser.add_argument("--biomarkers-db", default=None)
    parser.add_argument(
        "--allow-missing-turso",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--schedule-retry", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--record-failure", action="store_true", help=argparse.SUPPRESS)
    return parser


def options_from_args(args: argparse.Namespace) -> BackupOptions:
    def as_path(value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        return Path(value).expanduser()

    return BackupOptions(
        dry_run=bool(args.dry_run),
        no_upload=bool(args.no_upload),
        out_dir=as_path(args.out_dir),
        recipients=as_path(args.recipients),
        config_dir=as_path(args.config_dir),
        app_dir=as_path(args.app_dir),
        state_dir=as_path(args.state_dir),
        coach_state=as_path(args.coach_state),
        systemd_dir=as_path(args.systemd_dir),
        code_hashes=as_path(args.code_hashes),
        git_dir=as_path(args.git_dir),
        no_git=bool(args.no_git),
        age_bin=args.age_bin,
        now=args.now,
        prune_apply=bool(args.prune_apply),
        repo=args.repo,
        db_path=as_path(args.db_path),
        coaching_db=as_path(args.coaching_db),
        biomarkers_db=as_path(args.biomarkers_db),
        allow_missing_turso=bool(args.allow_missing_turso),
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.schedule_retry:
        return schedule_retry()
    if args.record_failure:
        state = Path(args.state_dir).expanduser() if args.state_dir else None
        return record_failure(state)
    # A read-only guard is imported so a future edit that drops it is obvious.
    assert_read_only("SELECT 1")
    result = run_backup(options_from_args(args))
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
