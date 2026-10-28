"""SQLite workout session repository (Phase 1a).

Primary store for lift sessions. Markdown under ``fitness/workouts/`` is an
*import* source, not the multi-user database. GitHub Contents is optional later.

Env:
  FITDASH_DB_PATH   default ``~/.config/resistance-dashboard/fitdash.db``
  FITDASH_USER_ID   default ``default`` (column ready for multi-user)
  FITDASH_USE_SQLITE  default ``1`` / true — prefer SQLite for reads/writes
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: F401

from .crypto_box import open_str, seal_str
from .models import ExerciseEntry, Session, SetEntry
from .parse import parse_all_workouts

_LOG = logging.getLogger("fitdash.workout_repo")

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

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
);

CREATE INDEX IF NOT EXISTS idx_ws_user_date
  ON workout_sessions(user_id, date DESC);
"""

DEFAULT_USER = "default"


def default_db_path() -> Path:
    env = (os.environ.get("FITDASH_DB_PATH") or "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "resistance-dashboard" / "fitdash.db"


def default_user_id() -> str:
    return (os.environ.get("FITDASH_USER_ID") or DEFAULT_USER).strip() or DEFAULT_USER


def use_sqlite() -> bool:
    v = (os.environ.get("FITDASH_USE_SQLITE") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_DERIVED_LOAD_KEYS = (
    "assistance_lbs",
    "effective_load_lbs",
    "effective_load_source",
    "weighin_days_away",
)


def _epley_pin(weight: float, reps: int) -> float:
    if reps <= 0:
        return 0.0
    if reps == 1:
        return float(weight)
    return float(weight) * (1.0 + reps / 30.0)


def _persist_exercise(entry: ExerciseEntry) -> dict:
    """Store the logged pin. Read-time effective load is not durable."""
    payload = entry.to_dict()
    assisted = bool(payload.get("effective_load_source")) or any(
        isinstance(st, dict) and st.get("effective_load_source")
        for st in (payload.get("sets") or [])
    )
    for key in _DERIVED_LOAD_KEYS:
        payload.pop(key, None)
    sets = payload.get("sets")
    if isinstance(sets, list):
        for st in sets:
            if isinstance(st, dict):
                for key in _DERIVED_LOAD_KEYS:
                    st.pop(key, None)
    if not assisted or not isinstance(sets, list):
        return payload
    volume = 0.0
    best_e = 0.0
    best_w = 0.0
    for st in sets:
        if not isinstance(st, dict):
            continue
        try:
            weight = float(st.get("weight_lbs") or 0)
            count = int(st.get("sets") or 0)
            reps = int(st.get("reps") or 0)
        except (TypeError, ValueError):
            continue
        volume += weight * count * reps
        best_e = max(best_e, _epley_pin(weight, reps))
        best_w = max(best_w, weight)
    payload["volume"] = volume
    payload["best_e1rm"] = best_e
    payload["best_working_weight"] = best_w
    return payload


def _exercises_to_json(exercises: Sequence[ExerciseEntry]) -> str:
    return json.dumps(
        [_persist_exercise(e) for e in exercises],
        separators=(",", ":"),
    )


def _exercises_from_json(raw: str) -> List[ExerciseEntry]:
    data = json.loads(raw or "[]")
    out: List[ExerciseEntry] = []
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        sets: List[SetEntry] = []
        for s in item.get("sets") or []:
            if not isinstance(s, dict):
                continue
            try:
                entry = SetEntry(
                    weight_lbs=float(s.get("weight_lbs") or 0),
                    sets=int(s.get("sets") or 1),
                    reps=int(s.get("reps") or 0),
                )
            except (TypeError, ValueError):
                continue
            from .session_work import apply_set_flags

            apply_set_flags(entry, s)
            sets.append(entry)
        out.append(
            ExerciseEntry(
                name=str(item.get("name") or ""),
                sets=sets,
                is_pr=bool(item.get("is_pr")),
                raw=str(item.get("raw") or ""),
                quest_seeded=bool(item.get("quest_seeded")),
            )
        )
    return out


def _seal_exercises(user_id: str, exercises: Sequence[ExerciseEntry]) -> str:
    plain = _exercises_to_json(exercises)
    return seal_str(plain, aad=f"user:{user_id}:workout")


def _open_exercises(user_id: str, stored: str) -> List[ExerciseEntry]:
    """Decrypt exercises_json; tolerate legacy plaintext JSON rows."""
    raw = stored or "[]"
    if raw.lstrip().startswith("["):
        return _exercises_from_json(raw)
    try:
        plain = open_str(raw, aad=f"user:{user_id}:workout")
        return _exercises_from_json(plain)
    except ValueError:
        # Wrong key / tampered — do not leak
        return []


def _row_get(row: Any, key: str, default: str = "") -> str:
    try:
        if isinstance(row, dict):
            val = row.get(key, default)
        else:
            val = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    if val is None:
        return default
    return str(val)


def _json_field(row: Any, key: str) -> Any:
    raw = _row_get(row, key, "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _row_to_session(row: sqlite3.Row, user_id: str) -> Session:
    closed = _row_get(row, "created_at") or _row_get(row, "closed_at")
    status = _row_get(row, "status", "") or None
    session = Session(
        date=str(row["date"]),
        session_type=str(row["session_type"]),
        exercises=_open_exercises(user_id, str(row["exercises_json"] or "[]")),
        notes=str(row["notes"] or ""),
        source_file=str(row["source_file"] or ""),
        closed_at=closed or None,
        status=status,
        submission_id=_row_get(row, "submission_id", "") or None,
        plan_objective=_json_field(row, "plan_json"),
        skipped=_json_field(row, "skipped_json"),
        substitutions=_json_field(row, "substitutions_json"),
    )
    from .session_work import annotate_day_roll

    return annotate_day_roll(session)


def _ensure_log_columns(conn: sqlite3.Connection) -> None:
    """Additive nullable columns. Existing rows stay NULL (legacy complete)."""
    have = {row[1] for row in conn.execute("PRAGMA table_info(workout_sessions)")}
    for name in (
        "status",
        "submission_id",
        "plan_json",
        "skipped_json",
        "substitutions_json",
    ):
        if name not in have:
            conn.execute(f"ALTER TABLE workout_sessions ADD COLUMN {name} TEXT")


def _status_params(session: Session) -> Tuple[Any, ...]:
    plan = json.dumps(session.plan_objective, separators=(",", ":")) if session.plan_objective else None
    skipped = json.dumps(session.skipped, separators=(",", ":")) if session.skipped else None
    subs = (
        json.dumps(session.substitutions, separators=(",", ":"))
        if session.substitutions
        else None
    )
    return (
        session.status or None,
        session.submission_id or None,
        plan,
        skipped,
        subs,
    )


class WorkoutRepository:
    """SQLite-backed session store scoped by user_id."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        user_id: Optional[str] = None,
    ):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.user_id = (user_id or default_user_id()).strip() or DEFAULT_USER
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            _ensure_log_columns(conn)
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES(?, ?)",
                ("version", "1"),
            )
            conn.commit()

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM workout_sessions WHERE user_id = ?",
                (self.user_id,),
            ).fetchone()
            return int(row["c"] if row else 0)

    def list_sessions(self) -> List[Session]:
        from .smith_bench_migrate import ensure_sqlite_smith_bench_split

        ensure_sqlite_smith_bench_split(self.db_path)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT date, session_type, notes, source_file, exercises_json,
                       created_at, status, submission_id, plan_json,
                       skipped_json, substitutions_json
                FROM workout_sessions
                WHERE user_id = ?
                ORDER BY date DESC, session_type ASC
                """,
                (self.user_id,),
            ).fetchall()
        sessions = [_row_to_session(r, self.user_id) for r in rows]
        try:
            self._persist_day_rolls(sessions)
        except Exception:
            _LOG.exception("day roll write-back failed")
        return sessions

    def _persist_day_rolls(self, sessions: Sequence[Session]) -> None:
        """Write a 4 AM roll once. Later reads see partial or missed."""
        pending = [s for s in sessions if getattr(s, "day_roll_pending", False)]
        if not pending:
            return
        now = _utc_now()
        with self._connect() as conn:
            for session in pending:
                skipped = (
                    json.dumps(session.skipped, separators=(",", ":"))
                    if session.skipped
                    else None
                )
                subs = (
                    json.dumps(session.substitutions, separators=(",", ":"))
                    if session.substitutions
                    else None
                )
                conn.execute(
                    """
                    UPDATE workout_sessions
                    SET status = ?, skipped_json = ?, substitutions_json = ?,
                        updated_at = ?
                    WHERE user_id = ? AND date = ? AND session_type = ?
                      AND status = 'in_progress'
                    """,
                    (
                        session.status,
                        skipped,
                        subs,
                        now,
                        self.user_id,
                        str(session.date)[:10],
                        str(session.session_type or "").lower(),
                    ),
                )
                session.day_roll_pending = False
            conn.commit()

    def delete_session(self, date: str, session_type: str) -> bool:
        """Delete one (date, session_type) row. True when a row was removed."""
        day = str(date or "")[:10]
        st = str(session_type or "").lower().strip()
        with self._connect() as conn:
            cur = conn.execute(
                """
                DELETE FROM workout_sessions
                WHERE user_id = ? AND date = ? AND session_type = ?
                """,
                (self.user_id, day, st),
            )
            conn.commit()
            return int(cur.rowcount or 0) > 0

    def relocate_session(self, session: Session, from_date: str) -> Dict[str, Any]:
        """Write ``session`` at its date; delete ``from_date`` if different.

        Keeps ``closed_at`` (created_at) so wake membership does not jump.
        """
        src_day = str(from_date or "")[:10]
        result = self.upsert_session(session)
        if src_day and src_day != str(session.date)[:10]:
            self.delete_session(src_day, session.session_type)
        return result

    def upsert_session(self, session: Session) -> Dict[str, Any]:
        """Insert or replace by (user_id, date, session_type)."""
        now = _utc_now()
        created = (session.closed_at or "").strip() or now
        payload = _seal_exercises(self.user_id, session.exercises)
        status, submission_id, plan, skipped, subs = _status_params(session)
        with self._connect() as conn:
            _ensure_log_columns(conn)
            conn.execute(
                """
                INSERT INTO workout_sessions(
                  user_id, date, session_type, notes, source_file,
                  exercises_json, created_at, updated_at,
                  status, submission_id, plan_json, skipped_json, substitutions_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, date, session_type) DO UPDATE SET
                  notes = excluded.notes,
                  source_file = excluded.source_file,
                  exercises_json = excluded.exercises_json,
                  updated_at = excluded.updated_at,
                  status = COALESCE(excluded.status, workout_sessions.status),
                  submission_id = COALESCE(excluded.submission_id, workout_sessions.submission_id),
                  plan_json = COALESCE(excluded.plan_json, workout_sessions.plan_json),
                  skipped_json = excluded.skipped_json,
                  substitutions_json = COALESCE(
                    excluded.substitutions_json, workout_sessions.substitutions_json
                  )
                """,
                (
                    self.user_id,
                    session.date,
                    session.session_type,
                    session.notes or "",
                    session.source_file or "",
                    payload,
                    created,
                    now,
                    status,
                    submission_id,
                    plan,
                    skipped,
                    subs,
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM workout_sessions
                WHERE user_id = ? AND date = ? AND session_type = ?
                """,
                (self.user_id, session.date, session.session_type),
            ).fetchone()
            conn.commit()
        return {
            "ok": True,
            "backend": "sqlite",
            "user_id": self.user_id,
            "id": int(row["id"]) if row else None,
            "path": str(self.db_path),
            "date": session.date,
            "session_type": session.session_type,
            "verified_on_readback": True,
        }

    def import_sessions(
        self,
        sessions: Sequence[Session],
        *,
        replace: bool = False,
    ) -> Dict[str, Any]:
        """Bulk import. If replace=True, wipe this user's rows first."""
        with self._connect() as conn:
            if replace:
                conn.execute(
                    "DELETE FROM workout_sessions WHERE user_id = ?",
                    (self.user_id,),
                )
            now = _utc_now()
            inserted = 0
            for session in sessions:
                conn.execute(
                    """
                    INSERT INTO workout_sessions(
                      user_id, date, session_type, notes, source_file,
                      exercises_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, date, session_type) DO UPDATE SET
                      notes = excluded.notes,
                      source_file = excluded.source_file,
                      exercises_json = excluded.exercises_json,
                      updated_at = excluded.updated_at
                    """,
                    (
                        self.user_id,
                        session.date,
                        session.session_type,
                        session.notes or "",
                        session.source_file or "",
                        _seal_exercises(self.user_id, session.exercises),
                        now,
                        now,
                    ),
                )
                inserted += 1
            conn.commit()
        return {
            "ok": True,
            "imported": inserted,
            "user_id": self.user_id,
            "count": self.count(),
            "path": str(self.db_path),
        }

    def import_from_markdown_dir(
        self,
        workspace_dir: str | Path,
        *,
        replace: bool = False,
    ) -> Dict[str, Any]:
        """Parse fitness/workouts/*.md under workspace and import."""
        root = Path(workspace_dir)
        wdir = root / "fitness" / "workouts"
        if not wdir.is_dir():
            return {
                "ok": False,
                "error": f"missing workouts dir: {wdir}",
                "imported": 0,
            }
        files: Dict[str, str] = {}
        for path in sorted(wdir.glob("*.md")):
            if path.name == "README.md":
                continue
            rel = f"fitness/workouts/{path.name}"
            files[rel] = path.read_text(encoding="utf-8")
        sessions = parse_all_workouts(files)
        result = self.import_sessions(sessions, replace=replace)
        result["files"] = list(files.keys())
        result["parsed_sessions"] = len(sessions)
        return result

    def ensure_seeded_from_workspace(self, workspace_dir: str | Path) -> Dict[str, Any]:
        """If this user has zero rows, import markdown once."""
        if self.count() > 0:
            return {
                "ok": True,
                "seeded": False,
                "count": self.count(),
                "path": str(self.db_path),
            }
        result = self.import_from_markdown_dir(workspace_dir, replace=False)
        result["seeded"] = bool(result.get("ok") and result.get("imported", 0) > 0)
        return result


class TursoWorkoutRepository:
    """Turso-backed twin of WorkoutRepository (same methods server.py calls).

    Used when TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are set so a self-hosted
    server reads/writes the same rows as the Vercel API. Never seeds from
    markdown and never bulk-imports: Turso is the source of truth.
    """

    def __init__(self, user_id: Optional[str] = None):
        self.user_id = (user_id or default_user_id()).strip() or DEFAULT_USER
        self.db_path = "turso"

    def count(self) -> int:
        return len(self.list_sessions())

    def list_sessions(self) -> List[Session]:
        from . import turso_repo

        return turso_repo.list_sessions(self.user_id)

    def delete_session(self, date: str, session_type: str) -> bool:
        from . import turso_repo

        return turso_repo.delete_session(self.user_id, date, session_type)

    def relocate_session(self, session: Session, from_date: str) -> Dict[str, Any]:
        from . import turso_repo

        return turso_repo.relocate_session(self.user_id, session, from_date)

    def upsert_session(self, session: Session) -> Dict[str, Any]:
        from . import turso_repo

        return turso_repo.upsert_session(self.user_id, session)

    def ensure_seeded_from_workspace(self, workspace_dir: str | Path) -> Dict[str, Any]:
        return {"ok": True, "seeded": False, "count": self.count(), "path": "turso"}

    def import_sessions(self, sessions: Sequence[Session], *, replace: bool = False) -> Dict[str, Any]:
        return {"ok": False, "error": "bulk import disabled on Turso", "imported": 0}

    def import_from_markdown_dir(
        self, workspace_dir: str | Path, *, replace: bool = False
    ) -> Dict[str, Any]:
        return {"ok": False, "error": "markdown import disabled on Turso", "imported": 0}


def get_repo(
    db_path: Optional[Path] = None,
    user_id: Optional[str] = None,
):
    """Turso when its env is set (and no explicit db_path), else SQLite."""
    if db_path is None:
        from .turso_http import turso_enabled

        if turso_enabled():
            return TursoWorkoutRepository(user_id=user_id)
    return WorkoutRepository(db_path=db_path, user_id=user_id)
