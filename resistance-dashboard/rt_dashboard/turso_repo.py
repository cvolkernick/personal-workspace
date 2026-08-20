"""Read workout_sessions from Turso using workout_repo row mapping. No local db."""

from __future__ import annotations

from typing import List, Tuple

from .crypto_box import open_str
from .turso_http import connect, turso_enabled
from .workout_repo import DEFAULT_USER, _row_to_session
from .models import Session

LIST_SQL = """
SELECT date, session_type, notes, source_file, exercises_json
FROM workout_sessions
WHERE user_id = ?
ORDER BY date DESC, session_type ASC
"""


def list_sessions(user_id: str) -> List[Session]:
    sessions, _notes = list_sessions_detailed(user_id)
    return sessions


def list_sessions_detailed(user_id: str) -> Tuple[List[Session], List[str]]:
    if not turso_enabled():
        raise RuntimeError("turso env missing")
    uid = (user_id or DEFAULT_USER).strip() or DEFAULT_USER
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
