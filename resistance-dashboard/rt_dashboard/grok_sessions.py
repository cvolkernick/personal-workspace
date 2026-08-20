"""Sealed per-user SuperGrok tokens in Turso. Never print tokens."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .crypto_box import open_str, seal_str
from .grok_oauth import _parse_iso, _utc_now, refresh_access_token
from .turso_http import connect, turso_enabled

ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS grok_sessions (
  user_id TEXT PRIMARY KEY,
  sealed TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""

AAD_TMPL = "user:{user_id}:grok"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure(conn) -> None:
    conn.execute(ENSURE_SQL)


def save_grok_session(user_id: str, payload: Dict[str, Any]) -> None:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id required")
    if not turso_enabled():
        raise RuntimeError("turso env missing")
    blob = {
        "access_token": str(payload.get("access_token") or ""),
        "refresh_token": str(payload.get("refresh_token") or ""),
        "expires_at": str(payload.get("expires_at") or ""),
        "email": str(payload.get("email") or ""),
    }
    if not blob["access_token"]:
        raise ValueError("access_token required")
    sealed = seal_str(json.dumps(blob, separators=(",", ":")), aad=AAD_TMPL.format(user_id=uid))
    now = _iso_now()
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            """
            INSERT INTO grok_sessions(user_id, sealed, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              sealed = excluded.sealed,
              updated_at = excluded.updated_at
            """,
            (uid, sealed, now),
        )


def load_grok_session(user_id: str) -> Optional[Dict[str, Any]]:
    uid = (user_id or "").strip()
    if not uid or not turso_enabled():
        return None
    try:
        with connect() as conn:
            _ensure(conn)
            row = conn.execute(
                "SELECT sealed FROM grok_sessions WHERE user_id = ?",
                (uid,),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    sealed = row["sealed"] if isinstance(row, dict) else row[0]
    if not sealed:
        return None
    try:
        raw = open_str(str(sealed), aad=AAD_TMPL.format(user_id=uid))
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("access_token"):
        return None
    return {
        "access_token": str(data.get("access_token") or ""),
        "refresh_token": str(data.get("refresh_token") or ""),
        "expires_at": str(data.get("expires_at") or ""),
        "email": str(data.get("email") or ""),
    }


def delete_grok_session(user_id: str) -> bool:
    uid = (user_id or "").strip()
    if not uid or not turso_enabled():
        return False
    with connect() as conn:
        _ensure(conn)
        conn.execute("DELETE FROM grok_sessions WHERE user_id = ?", (uid,))
    return True


def session_is_expired(payload: Dict[str, Any], skew_sec: int = 120) -> bool:
    exp = _parse_iso(payload.get("expires_at") if payload else None)
    if not exp:
        return False
    now = _utc_now()
    return exp.timestamp() <= now.timestamp() + max(0, skew_sec)


def load_fresh_grok_session(user_id: str) -> Optional[Dict[str, Any]]:
    """Unseal; refresh when expired. Never returns tokens to callers outside grok_ask."""
    sess = load_grok_session(user_id)
    if not sess:
        return None
    if not session_is_expired(sess):
        return sess
    refreshed = refresh_access_token(sess.get("refresh_token") or "")
    if not refreshed:
        return {**sess, "expired": True}
    merged = {
        "access_token": refreshed["access_token"],
        "refresh_token": refreshed.get("refresh_token") or sess.get("refresh_token") or "",
        "expires_at": refreshed.get("expires_at") or sess.get("expires_at") or "",
        "email": refreshed.get("email") or sess.get("email") or "",
    }
    try:
        save_grok_session(user_id, merged)
    except Exception:
        pass
    return merged


def public_session_view(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not payload:
        return {"connected": False, "email": None, "expires_at": None}
    return {
        "connected": True,
        "email": payload.get("email") or None,
        "expires_at": payload.get("expires_at") or None,
        "expired": bool(payload.get("expired")),
    }
