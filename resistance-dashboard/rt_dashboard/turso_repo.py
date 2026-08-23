"""workout_sessions in Turso using workout_repo row mapping. No local db."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .crypto_box import open_str
from .turso_http import connect, turso_enabled
from .workout_repo import (
    DEFAULT_USER,
    _row_to_session,
    _seal_exercises,
    _utc_now,
)
from .models import Session

LIST_SQL = """
SELECT date, session_type, notes, source_file, exercises_json
FROM workout_sessions
WHERE user_id = ?
ORDER BY date DESC, session_type ASC
"""

ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS workout_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  date TEXT NOT NULL,
  session_type TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  source_file TEXT NOT NULL DEFAULT '',
  exercises_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, date, session_type)
)
"""

UPSERT_SQL = """
INSERT INTO workout_sessions(
  user_id, date, session_type, notes, source_file,
  exercises_json, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(user_id, date, session_type) DO UPDATE SET
  notes = excluded.notes,
  source_file = excluded.source_file,
  exercises_json = excluded.exercises_json,
  updated_at = excluded.updated_at
"""

GET_SQL = """
SELECT id, date, session_type, notes, source_file, exercises_json
FROM workout_sessions
WHERE user_id = ? AND date = ? AND session_type = ?
"""


def _uid(user_id: str) -> str:
    return (user_id or DEFAULT_USER).strip() or DEFAULT_USER


def list_sessions(user_id: str) -> List[Session]:
    sessions, _notes = list_sessions_detailed(user_id)
    return sessions


def list_sessions_detailed(user_id: str) -> Tuple[List[Session], List[str]]:
    if not turso_enabled():
        raise RuntimeError("turso env missing")
    uid = _uid(user_id)
    with connect() as conn:
        rows = conn.execute(LIST_SQL, (uid,)).fetchall()
    sessions: List[Session] = []
    decrypt_fails = 0
    decrypt_err = ""
    for r in rows:
        raw = str(r["exercises_json"] or "")
        sess = _row_to_session(r, uid)
        sessions.append(sess)
        if not raw or raw.lstrip().startswith("["):
            continue
        if sess.exercises:
            continue
        decrypt_fails += 1
        if decrypt_err:
            continue
        try:
            open_str(raw, aad=f"user:{uid}:workout")
        except ValueError as exc:
            decrypt_err = str(exc)[:160]
        except Exception as exc:  # noqa: BLE001
            decrypt_err = type(exc).__name__
    notes: List[str] = []
    if decrypt_fails:
        detail = decrypt_err or "empty after open"
        notes.append(f"exercises_decrypt_failed:{decrypt_fails}:{detail}")
    return sessions, notes


def upsert_session(user_id: str, session: Session) -> Dict[str, Any]:
    """Insert or replace by (user_id, date, session_type). Same shape as SQLite."""
    if not turso_enabled():
        raise RuntimeError("turso env missing")
    if not isinstance(session, Session):
        raise ValueError("session required")
    uid = _uid(user_id)
    now = _utc_now()
    payload = _seal_exercises(uid, session.exercises)
    with connect() as conn:
        conn.execute(ENSURE_SQL)
        conn.execute(
            UPSERT_SQL,
            (
                uid,
                session.date,
                session.session_type,
                session.notes or "",
                session.source_file or "",
                payload,
                now,
                now,
            ),
        )
        row = conn.execute(
            GET_SQL,
            (uid, session.date, session.session_type),
        ).fetchone()
    if not row:
        raise RuntimeError("turso write not visible on readback")
    read = _row_to_session(row, uid)
    if not read.exercises and session.exercises:
        raise RuntimeError("turso write not visible on readback")
    row_id = None
    try:
        raw_id = row["id"] if isinstance(row, dict) else None
        row_id = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError, KeyError):
        row_id = None
    return {
        "ok": True,
        "backend": "turso",
        "user_id": uid,
        "id": row_id,
        "path": "turso",
        "date": session.date,
        "session_type": session.session_type,
        "verified_on_readback": True,
    }
