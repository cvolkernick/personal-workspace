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
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .crypto_box import open_str, seal_str
from .models import ExerciseEntry, Session, SetEntry
from .parse import parse_all_workouts

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


def _exercises_to_json(exercises: Sequence[ExerciseEntry]) -> str:
    return json.dumps([e.to_dict() for e in exercises], separators=(",", ":"))


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
                sets.append(
                    SetEntry(
                        weight_lbs=float(s.get("weight_lbs") or 0),
                        sets=int(s.get("sets") or 1),
                        reps=int(s.get("reps") or 0),
                    )
                )
            except (TypeError, ValueError):
                continue
        out.append(
            ExerciseEntry(
                name=str(item.get("name") or ""),
                sets=sets,
                is_pr=bool(item.get("is_pr")),
                raw=str(item.get("raw") or ""),
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


def _row_to_session(row: sqlite3.Row, user_id: str) -> Session:
    return Session(
        date=str(row["date"]),
        session_type=str(row["session_type"]),
        exercises=_open_exercises(user_id, str(row["exercises_json"] or "[]")),
        notes=str(row["notes"] or ""),
        source_file=str(row["source_file"] or ""),
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
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT date, session_type, notes, source_file, exercises_json
                FROM workout_sessions
                WHERE user_id = ?
                ORDER BY date DESC, session_type ASC
                """,
                (self.user_id,),
            ).fetchall()
        return [_row_to_session(r, self.user_id) for r in rows]

    def upsert_session(self, session: Session) -> Dict[str, Any]:
        """Insert or replace by (user_id, date, session_type)."""
        now = _utc_now()
        payload = _seal_exercises(self.user_id, session.exercises)
        with self._connect() as conn:
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
                    payload,
                    now,
                    now,
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


def get_repo(
    db_path: Optional[Path] = None,
    user_id: Optional[str] = None,
) -> WorkoutRepository:
    return WorkoutRepository(db_path=db_path, user_id=user_id)
