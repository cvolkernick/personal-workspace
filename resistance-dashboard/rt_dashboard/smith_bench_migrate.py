"""Rename historical "Smith Bench" logs to Smith Flat Bench (#922).

Logs store a display name only. Every past bare Smith Bench set was flat, so
this rewrites that name in place. Sets, weights, PR flags, raw text, and quest
flags stay as they were. A second run finds nothing to change.

SQLite (Pi / Mac) gets a full-file backup before the first write.
Turso (Vercel) copies workout rows into ``workout_sessions_bak_smith_922``
before the first write. Custom-movement and library-overlay ids follow the
same rename when those tables exist.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .crypto_box import open_str, seal_str

STAMP_KEY = "smith_bench_split_922"
LEGACY_NAME = "smith bench"
NEW_NAME = "Smith Flat Bench"
LEGACY_ID = "smith-bench"
NEW_ID = "smith-flat-bench"
BACKUP_TABLE = "workout_sessions_bak_smith_922"
LOG_TABLE = "smith_bench_split_922_log"

_SQLITE_DONE: set = set()
_TURSO_DONE = False


def _norm_name(name: str) -> str:
    import re

    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def is_legacy_smith_bench_name(name: str) -> bool:
    """Exact normalized match. Incline and flat names are not legacy."""
    return _norm_name(name) == LEGACY_NAME


def _disabled() -> bool:
    v = (os.environ.get("FITDASH_SMITH_BENCH_MIGRATE") or "1").strip().lower()
    return v in ("0", "false", "no", "off")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open_plain(stored: str, user_id: str) -> Tuple[str, str]:
    raw = stored or "[]"
    if raw.lstrip().startswith("["):
        return raw, "plain"
    uid = (user_id or "default").strip() or "default"
    aads = [f"user:{uid}:workout"]
    if uid != "default":
        aads.append("user:default:workout")
    last = "authentication failed"
    for aad in aads:
        try:
            return open_str(raw, aad=aad), aad
        except ValueError as exc:
            last = str(exc) or last
    raise ValueError(last)


def rewrite_exercise_list(data: Any) -> Tuple[Any, int]:
    """Return a copy with legacy names replaced. Other keys stay put."""
    if not isinstance(data, list):
        return data, 0
    renamed = 0
    out: List[Any] = []
    for item in data:
        if isinstance(item, dict) and is_legacy_smith_bench_name(str(item.get("name") or "")):
            item = dict(item)
            item["name"] = NEW_NAME
            renamed += 1
        out.append(item)
    return out, renamed


def rewrite_stored_exercises(stored: str, user_id: str) -> Tuple[Optional[str], int, str]:
    """Return (new_blob or None, renamed_count, error).

    None means the stored blob is left untouched. The reseal uses the same
    AAD that opened the row.
    """
    try:
        plain, aad = _open_plain(stored, user_id)
        data = json.loads(plain or "[]")
    except (ValueError, json.JSONDecodeError) as exc:
        return None, 0, str(exc)[:160] or "unreadable"
    rewritten, renamed = rewrite_exercise_list(data)
    if renamed <= 0:
        return None, 0, ""
    payload = json.dumps(rewritten, separators=(",", ":"), ensure_ascii=False)
    if aad == "plain":
        return payload, renamed, ""
    return seal_str(payload, aad=aad), renamed, ""


def rewrite_custom_payload(custom: Any) -> Tuple[dict, int]:
    """Rename a smith-bench custom row to smith-flat-bench. Drop a duplicate id."""
    if not isinstance(custom, dict):
        return {"exercises": []}, 0
    items = custom.get("exercises")
    if not isinstance(items, list):
        items = []
    renamed = 0
    kept: List[Any] = []
    seen = set()
    for row in items:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        ex = dict(row)
        eid = str(ex.get("id") or "").strip()
        if eid == LEGACY_ID or is_legacy_smith_bench_name(str(ex.get("name") or "")):
            ex["id"] = NEW_ID
            ex["name"] = NEW_NAME
            renamed += 1
            eid = NEW_ID
        if eid and eid in seen:
            continue
        if eid:
            seen.add(eid)
        kept.append(ex)
    out = {k: v for k, v in custom.items() if k != "exercises"}
    out["exercises"] = kept
    return out, renamed


def rewrite_overlay(overlay: Any) -> Tuple[dict, int]:
    """Point library enable/disable ids from smith-bench at smith-flat-bench."""
    if not isinstance(overlay, dict):
        return {"enabled": [], "disabled": []}, 0
    renamed = 0

    def _swap(values: Any) -> List[str]:
        nonlocal renamed
        out: List[str] = []
        for raw in values or []:
            eid = str(raw or "").strip()
            if not eid:
                continue
            if eid == LEGACY_ID:
                eid = NEW_ID
                renamed += 1
            if eid not in out:
                out.append(eid)
        return out

    disabled = _swap(overlay.get("disabled"))
    blocked = set(disabled)
    enabled = [eid for eid in _swap(overlay.get("enabled")) if eid not in blocked]
    return {"enabled": enabled, "disabled": disabled}, renamed


def _log_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + ".smith-bench-922.log")


def _append_log(db_path: Path, rows: Sequence[dict]) -> Optional[str]:
    if not rows:
        return None
    path = _log_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return str(path)


def _backup_sqlite(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = db_path.with_name(db_path.name + f".bak-smith-bench-922-{stamp}")
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    try:
        dest.chmod(0o600)
    except OSError:
        pass
    return dest


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _read_stamp(conn: sqlite3.Connection) -> str:
    if not _table_exists(conn, "schema_meta"):
        return ""
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = ?",
        (STAMP_KEY,),
    ).fetchone()
    if not row:
        return ""
    return str(row[0] or "")


def _write_stamp(conn: sqlite3.Connection, payload: dict) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO schema_meta(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (STAMP_KEY, json.dumps(payload, separators=(",", ":"))),
    )


def migrate_sqlite(db_path: Path, *, dry_run: bool = False) -> Dict[str, Any]:
    """Rewrite one SQLite workout store. Idempotent after a clean pass."""
    path = Path(db_path)
    if not path.is_file():
        return {"ok": True, "noop": True, "reason": "missing_db", "path": str(path), "renamed_rows": 0}
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "workout_sessions"):
            return {
                "ok": True,
                "noop": True,
                "reason": "no_workout_table",
                "path": str(path),
                "renamed_rows": 0,
            }
        before = int(conn.execute("SELECT COUNT(*) AS c FROM workout_sessions").fetchone()["c"])
        stamp = _read_stamp(conn)
        if stamp:
            try:
                parsed = json.loads(stamp)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict) and parsed.get("status") == "done" and not dry_run:
                return {
                    "ok": True,
                    "noop": True,
                    "reason": "already_done",
                    "path": str(path),
                    "renamed_rows": 0,
                    "session_count": before,
                    "stamp": parsed,
                }
        rows = conn.execute(
            """
            SELECT user_id, date, session_type, exercises_json
            FROM workout_sessions
            """
        ).fetchall()
        changes: List[dict] = []
        errors: List[dict] = []
        for row in rows:
            uid = str(row["user_id"] or "")
            new_blob, renamed, err = rewrite_stored_exercises(str(row["exercises_json"] or ""), uid)
            if err:
                errors.append(
                    {
                        "user_id": uid,
                        "date": str(row["date"] or ""),
                        "session_type": str(row["session_type"] or ""),
                        "error": err,
                    }
                )
                continue
            if new_blob is None or renamed <= 0:
                continue
            changes.append(
                {
                    "user_id": uid,
                    "date": str(row["date"] or ""),
                    "session_type": str(row["session_type"] or ""),
                    "renamed": renamed,
                    "exercises_json": new_blob,
                }
            )
        custom_changes = _plan_json_table(conn, "custom_movements", "payload", rewrite_custom_payload)
        overlay_changes = _plan_json_table(conn, "exercise_library", "payload", rewrite_overlay)
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "path": str(path),
                "session_count": before,
                "renamed_rows": len(changes),
                "renamed_exercises": sum(int(c["renamed"]) for c in changes),
                "custom_rows": len(custom_changes),
                "overlay_rows": len(overlay_changes),
                "decrypt_failures": len(errors),
            }
        if not changes and not custom_changes and not overlay_changes and not errors:
            _write_stamp(
                conn,
                {"status": "done", "renamed_rows": 0, "at": _utc_now(), "session_count": before},
            )
            conn.commit()
            return {
                "ok": True,
                "noop": True,
                "reason": "nothing_to_rename",
                "path": str(path),
                "renamed_rows": 0,
                "session_count": before,
            }
        backup = ""
        if changes or custom_changes or overlay_changes:
            backup = str(_backup_sqlite(path))
        now = _utc_now()
        for change in changes:
            conn.execute(
                """
                UPDATE workout_sessions
                SET exercises_json = ?, updated_at = ?
                WHERE user_id = ? AND date = ? AND session_type = ?
                """,
                (
                    change["exercises_json"],
                    now,
                    change["user_id"],
                    change["date"],
                    change["session_type"],
                ),
            )
        _apply_json_table(conn, "custom_movements", "payload", custom_changes)
        _apply_json_table(conn, "exercise_library", "payload", overlay_changes)
        after = int(conn.execute("SELECT COUNT(*) AS c FROM workout_sessions").fetchone()["c"])
        if after != before:
            conn.rollback()
            return {
                "ok": False,
                "error": "row_count_changed",
                "before": before,
                "after": after,
                "path": str(path),
                "backup": backup,
            }
        status = "done" if not errors else "partial"
        summary = {
            "status": status,
            "renamed_rows": len(changes),
            "renamed_exercises": sum(int(c["renamed"]) for c in changes),
            "custom_rows": len(custom_changes),
            "overlay_rows": len(overlay_changes),
            "decrypt_failures": len(errors),
            "session_count": after,
            "backup": backup,
            "at": now,
        }
        if not errors:
            _write_stamp(conn, summary)
        conn.commit()
        log_rows = [
            {
                "at": now,
                "user_id": c["user_id"],
                "date": c["date"],
                "session_type": c["session_type"],
                "renamed": c["renamed"],
            }
            for c in changes
        ]
        log_rows.extend({"at": now, "table": "custom_movements", "user_id": c["user_id"]} for c in custom_changes)
        log_rows.extend({"at": now, "table": "exercise_library", "user_id": c["user_id"]} for c in overlay_changes)
        log_rows.extend(
            {
                "at": now,
                "user_id": err.get("user_id") or "",
                "date": err.get("date") or "",
                "session_type": err.get("session_type") or "",
                "error": err.get("error") or "unreadable",
            }
            for err in errors
        )
        log_rows.append({"at": now, "summary": summary})
        log_file = _append_log(path, log_rows)
        return {
            "ok": not errors,
            "noop": False,
            "path": str(path),
            "backup": backup,
            "log": log_file,
            "renamed_rows": len(changes),
            "renamed_exercises": summary["renamed_exercises"],
            "session_count": after,
            "decrypt_failures": len(errors),
            "errors": errors,
            "stamp": summary if not errors else None,
        }
    finally:
        conn.close()


def _plan_json_table(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    rewriter: Callable[[Any], Tuple[dict, int]],
) -> List[dict]:
    if not _table_exists(conn, table):
        return []
    changes: List[dict] = []
    for row in conn.execute(f"SELECT user_id, {column} AS payload FROM {table}").fetchall():
        raw = row["payload"]
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            continue
        rewritten, n = rewriter(data)
        if n <= 0:
            continue
        changes.append(
            {
                "user_id": str(row["user_id"] or ""),
                "payload": json.dumps(rewritten, separators=(",", ":"), ensure_ascii=False),
            }
        )
    return changes


def _apply_json_table(conn: sqlite3.Connection, table: str, column: str, changes: Sequence[dict]) -> None:
    for change in changes:
        conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE user_id = ?",
            (change["payload"], change["user_id"]),
        )


def ensure_sqlite_smith_bench_split(db_path: Path) -> str:
    """One clean pass per process. Failure is a short note, not an exception."""
    if _disabled():
        return ""
    try:
        key = str(Path(db_path).resolve())
    except OSError:
        key = str(db_path)
    if key in _SQLITE_DONE:
        return ""
    try:
        result = migrate_sqlite(Path(db_path))
    except Exception as exc:  # noqa: BLE001
        _SQLITE_DONE.add(key)
        return f"smith_bench_migrate:{type(exc).__name__}"
    _SQLITE_DONE.add(key)
    if result.get("ok"):
        return ""
    return "smith_bench_migrate:" + str(result.get("error") or "decrypt_failures")


def _turso_fetch(conn: Any, sql: str, params: Sequence[Any] = ()) -> List[Any]:
    cur = conn.execute(sql, params)
    if cur is None:
        return []
    rows = cur.fetchall()
    return list(rows or [])


def _turso_stamp(conn: Any) -> str:
    _turso_fetch(
        conn,
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )
        """,
    )
    rows = _turso_fetch(conn, "SELECT value FROM schema_meta WHERE key = ?", (STAMP_KEY,))
    if not rows:
        return ""
    row = rows[0]
    if isinstance(row, dict):
        return str(row.get("value") or "")
    try:
        return str(row[0] or "")
    except (TypeError, IndexError, KeyError):
        return ""


def migrate_turso(connect: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """Rewrite Turso workout, custom-movement, and library-overlay rows."""
    if connect is None:
        from .turso_http import connect as connect

    try:
        conn_cm = connect()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    with conn_cm as conn:
        stamp_raw = _turso_stamp(conn)
        if stamp_raw:
            try:
                parsed = json.loads(stamp_raw)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict) and parsed.get("status") == "done":
                return {"ok": True, "noop": True, "reason": "already_done", "renamed_rows": 0, "stamp": parsed}
        try:
            rows = _turso_fetch(
                conn,
                """
                SELECT user_id, date, session_type, exercises_json
                FROM workout_sessions
                """,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"workout_read:{type(exc).__name__}"}
        before = len(rows)
        changes: List[dict] = []
        errors: List[dict] = []
        for row in rows:
            uid = str(_cell(row, "user_id") or "")
            new_blob, renamed, err = rewrite_stored_exercises(str(_cell(row, "exercises_json") or ""), uid)
            if err:
                errors.append({"user_id": uid, "date": str(_cell(row, "date") or ""), "error": err})
                continue
            if new_blob is None or renamed <= 0:
                continue
            changes.append(
                {
                    "user_id": uid,
                    "date": str(_cell(row, "date") or ""),
                    "session_type": str(_cell(row, "session_type") or ""),
                    "renamed": renamed,
                    "exercises_json": new_blob,
                    "old_json": str(_cell(row, "exercises_json") or ""),
                }
            )
        custom_changes = _turso_json_changes(conn, "custom_movements", "payload", rewrite_custom_payload)
        overlay_changes = _turso_json_changes(conn, "exercise_library", "payload", rewrite_overlay)
        if not changes and not custom_changes and not overlay_changes and not errors:
            summary = {"status": "done", "renamed_rows": 0, "session_count": before, "at": _utc_now()}
            _turso_write_stamp(conn, summary)
            _commit(conn)
            return {"ok": True, "noop": True, "reason": "nothing_to_rename", "renamed_rows": 0, "session_count": before}
        now = _utc_now()
        if changes:
            _turso_fetch(
                conn,
                f"""
                CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} (
                  user_id TEXT NOT NULL,
                  date TEXT NOT NULL,
                  session_type TEXT NOT NULL,
                  exercises_json TEXT NOT NULL,
                  backed_up_at TEXT NOT NULL,
                  PRIMARY KEY (user_id, date, session_type)
                )
                """,
            )
            _turso_fetch(
                conn,
                f"""
                CREATE TABLE IF NOT EXISTS {LOG_TABLE} (
                  user_id TEXT NOT NULL,
                  date TEXT NOT NULL,
                  session_type TEXT NOT NULL,
                  renamed INTEGER NOT NULL,
                  at TEXT NOT NULL,
                  PRIMARY KEY (user_id, date, session_type)
                )
                """,
            )
            for change in changes:
                _turso_fetch(
                    conn,
                    f"""
                    INSERT OR IGNORE INTO {BACKUP_TABLE}(
                      user_id, date, session_type, exercises_json, backed_up_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        change["user_id"],
                        change["date"],
                        change["session_type"],
                        change["old_json"],
                        now,
                    ),
                )
                _turso_fetch(
                    conn,
                    """
                    UPDATE workout_sessions
                    SET exercises_json = ?
                    WHERE user_id = ? AND date = ? AND session_type = ?
                    """,
                    (
                        change["exercises_json"],
                        change["user_id"],
                        change["date"],
                        change["session_type"],
                    ),
                )
                _turso_fetch(
                    conn,
                    f"""
                    INSERT INTO {LOG_TABLE}(user_id, date, session_type, renamed, at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, date, session_type) DO UPDATE SET
                      renamed = excluded.renamed,
                      at = excluded.at
                    """,
                    (
                        change["user_id"],
                        change["date"],
                        change["session_type"],
                        int(change["renamed"]),
                        now,
                    ),
                )
        _turso_apply_json(conn, "custom_movements", "payload", custom_changes)
        _turso_apply_json(conn, "exercise_library", "payload", overlay_changes)
        after_rows = _turso_fetch(conn, "SELECT user_id, date, session_type FROM workout_sessions")
        if len(after_rows) != before:
            return {
                "ok": False,
                "error": "row_count_changed",
                "before": before,
                "after": len(after_rows),
                "backup": BACKUP_TABLE,
            }
        summary = {
            "status": "done" if not errors else "partial",
            "renamed_rows": len(changes),
            "renamed_exercises": sum(int(c["renamed"]) for c in changes),
            "custom_rows": len(custom_changes),
            "overlay_rows": len(overlay_changes),
            "decrypt_failures": len(errors),
            "session_count": len(after_rows),
            "backup": BACKUP_TABLE if changes else "",
            "at": now,
        }
        if not errors:
            _turso_write_stamp(conn, summary)
        _commit(conn)
        return {
            "ok": not errors,
            "noop": False,
            "renamed_rows": len(changes),
            "renamed_exercises": summary["renamed_exercises"],
            "session_count": len(after_rows),
            "decrypt_failures": len(errors),
            "backup": summary["backup"],
            "errors": errors,
        }


def _cell(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _commit(conn: Any) -> None:
    fn = getattr(conn, "commit", None)
    if callable(fn):
        fn()


def _turso_write_stamp(conn: Any, payload: dict) -> None:
    _turso_fetch(
        conn,
        """
        INSERT INTO schema_meta(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (STAMP_KEY, json.dumps(payload, separators=(",", ":"))),
    )


def _turso_json_changes(conn: Any, table: str, column: str, rewriter: Callable[[Any], Tuple[dict, int]]) -> List[dict]:
    try:
        rows = _turso_fetch(conn, f"SELECT user_id, {column} AS payload FROM {table}")
    except Exception:
        return []
    changes: List[dict] = []
    for row in rows:
        raw = _cell(row, "payload")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            continue
        rewritten, n = rewriter(data)
        if n <= 0:
            continue
        changes.append(
            {
                "user_id": str(_cell(row, "user_id") or ""),
                "payload": json.dumps(rewritten, separators=(",", ":"), ensure_ascii=False),
            }
        )
    return changes


def _turso_apply_json(conn: Any, table: str, column: str, changes: Sequence[dict]) -> None:
    for change in changes:
        _turso_fetch(
            conn,
            f"UPDATE {table} SET {column} = ? WHERE user_id = ?",
            (change["payload"], change["user_id"]),
        )


def ensure_turso_smith_bench_split() -> str:
    """One Turso pass per process when Turso is configured. Never raises."""
    global _TURSO_DONE
    if _TURSO_DONE or _disabled():
        return ""
    try:
        from .turso_http import turso_enabled

        if not turso_enabled():
            return ""
        result = migrate_turso()
    except Exception as exc:  # noqa: BLE001
        _TURSO_DONE = True
        return f"smith_bench_migrate:{type(exc).__name__}"
    _TURSO_DONE = True
    if result.get("ok"):
        return ""
    return "smith_bench_migrate:" + str(result.get("error") or "decrypt_failures")


def main() -> None:
    from .workout_repo import default_db_path

    path = default_db_path()
    dry = "--dry-run" in sys.argv
    result = migrate_sqlite(path, dry_run=dry)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
