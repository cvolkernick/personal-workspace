"""Read workout_sessions from Turso using workout_repo row mapping. No local db."""

from __future__ import annotations

from typing import List

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
    if not turso_enabled():
        raise RuntimeError("turso env missing")
    uid = (user_id or DEFAULT_USER).strip() or DEFAULT_USER
    with connect() as conn:
        rows = conn.execute(LIST_SQL, (uid,)).fetchall()
    return [_row_to_session(r, uid) for r in rows]
