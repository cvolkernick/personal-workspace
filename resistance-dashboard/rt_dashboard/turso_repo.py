"""workout_sessions in Turso using workout_repo row mapping. No local db."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

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

# Core columns already used by list_sessions_detailed (live Turso reads).
UPDATE_SQL = """
UPDATE workout_sessions
SET notes = ?, source_file = ?, exercises_json = ?
WHERE user_id = ? AND date = ? AND session_type = ?
"""

INSERT_SQL = """
INSERT INTO workout_sessions(
  user_id, date, session_type, notes, source_file,
  exercises_json, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

INSERT_MIN_SQL = """
INSERT INTO workout_sessions(
  user_id, date, session_type, notes, source_file, exercises_json
) VALUES (?, ?, ?, ?, ?, ?)
"""

GET_SQL = """
SELECT date, session_type, notes, source_file, exercises_json
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


def _get_row(conn, uid: str, date: str, session_type: str):
    return conn.execute(GET_SQL, (uid, date, session_type)).fetchone()


def _insert_row(conn, uid: str, session: Session, payload: str, now: str) -> None:
    args_full = (
        uid,
        session.date,
        session.session_type,
        session.notes or "",
        session.source_file or "",
        payload,
        now,
        now,
    )
    try:
        conn.execute(INSERT_SQL, args_full)
    except RuntimeError:
        conn.execute(INSERT_MIN_SQL, args_full[:6])


def upsert_session(user_id: str, session: Session) -> Dict[str, Any]:
    """Insert or replace by (user_id, date, session_type). Same key as SQLite.

    UPDATE-then-INSERT so a live Turso table without UNIQUE still replaces
    instead of inserting junk rows. Seals exercises like WorkoutRepo.
    """
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
            UPDATE_SQL,
            (
                session.notes or "",
                session.source_file or "",
                payload,
                uid,
                session.date,
                session.session_type,
            ),
        )
        row = _get_row(conn, uid, session.date, session.session_type)
        if not row:
            _insert_row(conn, uid, session, payload, now)
            row = _get_row(conn, uid, session.date, session.session_type)
    if not row:
        raise RuntimeError("turso write not visible on readback")
    read = _row_to_session(row, uid)
    if not read.exercises and session.exercises:
        raise RuntimeError("turso write not visible on readback")
    return {
        "ok": True,
        "backend": "turso",
        "source": "turso",
        "user_id": uid,
        "path": "turso",
        "date": session.date,
        "session_type": session.session_type,
        "verified_on_readback": True,
    }


def save_preview_session(user_id: str, session: Session) -> Dict[str, Any]:
    """Persist a logged session to Turso. Fail honest if the write cannot land."""
    if not turso_enabled():
        raise RuntimeError("turso env missing")
    result = upsert_session(user_id, session)
    sessions, _notes = list_sessions_detailed(user_id)
    found: Optional[Session] = None
    for sess in sessions:
        if sess.date == session.date and sess.session_type == session.session_type:
            found = sess
            break
    if found is None or (session.exercises and not found.exercises):
        raise RuntimeError("turso write not visible on readback")
    return result
