"""Users + auth sessions (SQLite, same DB as workouts)."""

from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .crypto_box import open_str, seal_str
from .workout_repo import default_db_path

SESSION_DAYS = int(os.environ.get("FITDASH_SESSION_DAYS") or "14")

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL DEFAULT '',
  display_name TEXT NOT NULL DEFAULT '',
  health_refresh_token_enc TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  last_login_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
  ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_exp
  ON auth_sessions(expires_at);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class UserStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure(self) -> None:
        # Same file as WorkoutRepository — always create workout tables too so
        # claim_legacy / login never hit "no such table: workout_sessions"
        # when UserStore opens the DB first (empty review DB, cold OAuth).
        from .workout_repo import SCHEMA as WORKOUT_SCHEMA

        with self._connect() as conn:
            conn.executescript(USERS_SCHEMA)
            conn.executescript(WORKOUT_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES(?, ?)",
                ("version", "1"),
            )
            conn.commit()

    def upsert_user_from_google(
        self,
        *,
        sub: str,
        email: str,
        display_name: str,
        health_refresh_token: str = "",
    ) -> Dict[str, Any]:
        now = _iso(_utc_now())
        token_enc = ""
        if health_refresh_token:
            token_enc = seal_str(health_refresh_token, aad=f"user:{sub}:health_rt")
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE id = ?", (sub,)).fetchone()
            if row:
                if token_enc:
                    conn.execute(
                        """
                        UPDATE users SET email=?, display_name=?,
                          health_refresh_token_enc=?, last_login_at=?
                        WHERE id=?
                        """,
                        (email or "", display_name or "", token_enc, now, sub),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE users SET email=?, display_name=?, last_login_at=?
                        WHERE id=?
                        """,
                        (email or "", display_name or "", now, sub),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO users(id, email, display_name, health_refresh_token_enc,
                      created_at, last_login_at)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (sub, email or "", display_name or "", token_enc, now, now),
                )
            conn.commit()
        return self.get_user(sub) or {}

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, email, display_name, health_refresh_token_enc, created_at, last_login_at "
                "FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "has_health_token": bool(row["health_refresh_token_enc"]),
            "created_at": row["created_at"],
            "last_login_at": row["last_login_at"],
        }

    def get_health_refresh_token(self, user_id: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT health_refresh_token_enc FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if not row or not row["health_refresh_token_enc"]:
            return None
        try:
            return open_str(row["health_refresh_token_enc"], aad=f"user:{user_id}:health_rt")
        except ValueError:
            return None

    def list_users_with_health_token(self) -> List[Dict[str, Any]]:
        """Users that have a sealed Health refresh token, newest login first.

        Used by the Pi incremental warmer when no browser session is present.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, email, display_name, last_login_at
                FROM users
                WHERE health_refresh_token_enc IS NOT NULL
                  AND health_refresh_token_enc != ''
                ORDER BY last_login_at DESC
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "email": row["email"],
                "display_name": row["display_name"],
                "last_login_at": row["last_login_at"],
            }
            for row in rows
        ]

    def create_session(self, user_id: str) -> str:
        sid = secrets.token_urlsafe(32)
        now = _utc_now()
        exp = now + timedelta(days=max(1, SESSION_DAYS))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions(id, user_id, created_at, expires_at)
                VALUES (?,?,?,?)
                """,
                (sid, user_id, _iso(now), _iso(exp)),
            )
            conn.commit()
        return sid

    def resolve_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        now = _iso(_utc_now())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT s.id AS session_id, s.user_id, s.expires_at,
                       u.email, u.display_name
                FROM auth_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.id = ?
                """,
                (session_id,),
            ).fetchone()
            if not row:
                return None
            if str(row["expires_at"]) < now:
                conn.execute("DELETE FROM auth_sessions WHERE id = ?", (session_id,))
                conn.commit()
                return None
        return {
            "session_id": row["session_id"],
            "user_id": row["user_id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "expires_at": row["expires_at"],
        }

    def destroy_session(self, session_id: str) -> None:
        if not session_id:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE id = ?", (session_id,))
            conn.commit()

    def claim_legacy_default_workouts(self, user_id: str) -> int:
        """Move orphaned user_id='default' rows to this Google user (once).

        Critical: exercises_json may be sealed with AAD ``user:default:workout``.
        A bare UPDATE of user_id leaves ciphertext bound to the old AAD, so
        reads under the new user return empty exercises. We re-open with the
        legacy AAD (or plaintext JSON) and re-seal under the new user_id.
        """
        from datetime import datetime, timezone

        # Guarantee workout_sessions exists even if this DB was only touched by
        # an older UserStore that did not co-create workout schema.
        self._ensure()

        def _now() -> str:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._connect() as conn:
            mine = conn.execute(
                "SELECT COUNT(*) AS c FROM workout_sessions WHERE user_id = ?",
                (user_id,),
            ).fetchone()["c"]
            legacy_rows = conn.execute(
                """
                SELECT id, date, session_type, notes, source_file, exercises_json,
                       created_at
                FROM workout_sessions WHERE user_id = 'default'
                """
            ).fetchall()
            if mine > 0 or not legacy_rows:
                return 0

            claimed = 0
            now = _now()
            for row in legacy_rows:
                # Skip if target already has this date+type
                clash = conn.execute(
                    """
                    SELECT 1 FROM workout_sessions
                    WHERE user_id = ? AND date = ? AND session_type = ?
                    """,
                    (user_id, row["date"], row["session_type"]),
                ).fetchone()
                if clash:
                    continue

                raw = str(row["exercises_json"] or "[]")
                # Decrypt under legacy AAD, or accept plaintext Phase 1a rows
                if raw.lstrip().startswith("["):
                    plain = raw
                else:
                    try:
                        plain = open_str(raw, aad="user:default:workout")
                    except ValueError:
                        # Try new-user AAD (shouldn't happen for default rows)
                        try:
                            plain = open_str(raw, aad=f"user:{user_id}:workout")
                        except ValueError:
                            plain = "[]"
                resealed = seal_str(plain, aad=f"user:{user_id}:workout")
                conn.execute(
                    """
                    INSERT INTO workout_sessions(
                      user_id, date, session_type, notes, source_file,
                      exercises_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        row["date"],
                        row["session_type"],
                        row["notes"] or "",
                        row["source_file"] or "",
                        resealed,
                        row["created_at"] or now,
                        now,
                    ),
                )
                claimed += 1

            conn.execute("DELETE FROM workout_sessions WHERE user_id = 'default'")
            conn.commit()
            return claimed
