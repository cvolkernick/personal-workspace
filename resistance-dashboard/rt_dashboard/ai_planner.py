"""Last observed xAI planner state. No live xAI call.

Turso when configured, otherwise a JSON file under the resistance-dashboard
config dir, with process memory in front of both. Health and Today read this
record. They do not call xAI.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs

QUOTA_TITLE = "AI plan unavailable (xAI credits)"
QUOTA_MARKERS = (
    "spending-limit",
    "personal-team-blocked",
    "used all available credits",
    "monthly spending limit",
    "run out of credits",
)
CAPACITY_MARKERS = ("resource-exhausted", "currently at capacity")
PUBLIC_STATUSES = ("ok", "xai_quota", "capacity", "not_connected", "unknown")
PUBLIC_SOURCES = ("supergrok_session", "xai_api_key", "none")
SOURCE_LABELS = {
    "supergrok_session": "SuperGrok session",
    "xai_api_key": "XAI_API_KEY",
    "none": "none",
}

_ISO_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})"
)
_RESET_RE = re.compile(r"\bresets?\b(?:\s+at)?\s+([^\"\n{}]{3,80})", re.IGNORECASE)

_MEMORY: Dict[str, dict] = {}
_LATEST_USER = ""
_FILE_LOADED = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _user_key(user_id: Optional[str]) -> str:
    return (user_id or "").strip() or "_local"


def _state_path() -> Path:
    override = (os.environ.get("RESISTANCE_DASHBOARD_CONFIG_DIR") or "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".config" / "resistance-dashboard"
    return base / "ai_planner_state.json"


def backoff_minutes() -> int:
    """At least 60. ``GROK_QUOTA_BACKOFF_MIN`` can only raise that floor."""
    raw = (os.environ.get("GROK_QUOTA_BACKOFF_MIN") or "").strip()
    if not raw:
        return 60
    try:
        return max(60, int(raw))
    except ValueError:
        return 60


def _safe_code(raw: str) -> str:
    text = str(raw or "").strip()
    if not text or any(ch in text for ch in "{}\n\r"):
        return ""
    return text[:120]


def _safe_reset_label(raw: str) -> str:
    text = " ".join(str(raw or "").strip().strip("\"' ,.;").split())
    if not text or any(ch in text for ch in "{}"):
        return ""
    return text[:80]


def parse_reset(body: str) -> tuple[str, Optional[datetime]]:
    """Display label, plus an absolute time when the body has an ISO stamp."""
    blob = body or ""
    iso_at = None
    match = _ISO_RE.search(blob)
    if match:
        iso_at = _parse_iso(match.group(0))
    label = ""
    phrase = _RESET_RE.search(blob)
    if phrase:
        label = _safe_reset_label(phrase.group(1))
    elif iso_at is not None:
        label = _iso(iso_at)
    return label, iso_at


def xai_error_code(body: str) -> str:
    raw = (body or "").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    return _safe_code(str(data.get("code") or ""))


def classify_http(code: int, body: str) -> str:
    """``xai_quota``, ``capacity``, ``entitlement``, or ``other``.

    Quota markers on 402/403/429 win. A 429 ``resource-exhausted`` /
    ``currently at capacity`` body with no quota marker stays ``capacity``.
    """
    status = int(code or 0)
    lowered = (body or "").lower()
    quota = status in (402, 403, 429) and any(marker in lowered for marker in QUOTA_MARKERS)
    if quota:
        return "xai_quota"
    capacity = status == 429 or any(marker in lowered for marker in CAPACITY_MARKERS)
    if capacity and status in (0, 429):
        return "capacity"
    if status in (401, 403):
        return "entitlement"
    return "other"


def format_quota_message(*, source: str, code: str = "", reset_label: str = "") -> str:
    label = SOURCE_LABELS.get(source or "", "none")
    safe_code = _safe_code(code)
    msg = f"{QUOTA_TITLE} ({label}"
    if safe_code:
        msg += f"; {safe_code}"
    msg += "). xai_quota"
    safe_reset = _safe_reset_label(reset_label)
    if safe_reset:
        msg += f". weekly limit, resets at {safe_reset}"
    return msg


def force_requested(query: str = "", payload: Any = None) -> bool:
    if isinstance(payload, dict):
        flag = payload.get("force")
        if flag is True or str(flag).strip().lower() in ("1", "true", "yes"):
            return True
    for value in parse_qs(query or "", keep_blank_values=False).get("force") or []:
        if str(value).strip().lower() in ("1", "true", "yes"):
            return True
    return False


def _public_row(row: Optional[dict]) -> dict:
    if not isinstance(row, dict):
        return {
            "status": "unknown",
            "source": "none",
            "last_error_code": None,
            "since": None,
        }
    status = str(row.get("status") or "unknown")
    if status not in PUBLIC_STATUSES:
        status = "unknown"
    source = str(row.get("source") or "none")
    if source not in PUBLIC_SOURCES:
        source = "none"
    code = _safe_code(str(row.get("last_error_code") or ""))
    since = str(row.get("since") or "").strip()
    if since and _parse_iso(since) is None:
        since = ""
    return {
        "status": status,
        "source": source,
        "last_error_code": code or None,
        "since": since or None,
    }


def _persist_enabled() -> bool:
    """Skip the home file and Turso under pytest unless a test opts in."""
    flag = (os.environ.get("FITDASH_AI_PLANNER_PERSIST") or "").strip()
    if flag == "1":
        return True
    if flag == "0":
        return False
    return not bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _load_file() -> None:
    global _FILE_LOADED, _LATEST_USER
    if _FILE_LOADED:
        return
    if not _persist_enabled():
        _FILE_LOADED = True
        return
    _FILE_LOADED = True
    path = _state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    users = data.get("users")
    if isinstance(users, dict):
        for key, row in users.items():
            if isinstance(row, dict) and key not in _MEMORY:
                _MEMORY[str(key)] = row
    latest = str(data.get("latest_user_id") or "").strip()
    if latest and not _LATEST_USER:
        _LATEST_USER = latest


def _save_file() -> None:
    if not _persist_enabled():
        return
    path = _state_path()
    payload = {
        "users": _MEMORY,
        "latest_user_id": _LATEST_USER,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        return
    _turso_put_latest()


def _turso_put_latest() -> None:
    try:
        from .turso_http import connect, turso_enabled
    except Exception:
        return
    if not turso_enabled():
        return
    key = _LATEST_USER or "_local"
    row = _MEMORY.get(key)
    if not isinstance(row, dict):
        return
    blob = json.dumps(_public_row(row), separators=(",", ":"))
    now = _iso(_now())
    try:
        with connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_planner_state (
                  user_id TEXT PRIMARY KEY,
                  payload TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO ai_planner_state(user_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                  payload = excluded.payload,
                  updated_at = excluded.updated_at
                """,
                (key, blob, now),
            )
    except Exception:
        return


def clear_ai_planner_state() -> None:
    """Test helper. Drops memory and the local file. Does not call xAI."""
    global _LATEST_USER, _FILE_LOADED
    _MEMORY.clear()
    _LATEST_USER = ""
    _FILE_LOADED = True
    path = _state_path()
    try:
        path.unlink()
    except OSError:
        return


def record_observation(
    user_id: Optional[str],
    *,
    status: str,
    source: str,
    code: str = "",
    message: str = "",
    until: Optional[datetime] = None,
) -> dict:
    global _LATEST_USER
    _load_file()
    key = _user_key(user_id)
    now = _now()
    row = {
        "status": status if status in PUBLIC_STATUSES else "unknown",
        "source": source if source in PUBLIC_SOURCES else "none",
        "last_error_code": _safe_code(code) or None,
        "since": _iso(now),
        "message": message if "{" not in (message or "") else QUOTA_TITLE,
        "until": _iso(until) if isinstance(until, datetime) else None,
        "user_id": key,
    }
    _MEMORY[key] = row
    _LATEST_USER = key
    _save_file()
    return _public_row(row)


def note_quota(
    user_id: Optional[str],
    *,
    source: str,
    code: str = "",
    message: str,
    reset_at: Optional[datetime] = None,
) -> dict:
    until = _now() + timedelta(minutes=backoff_minutes())
    if isinstance(reset_at, datetime) and reset_at > until:
        until = reset_at
    return record_observation(
        user_id,
        status="xai_quota",
        source=source,
        code=code,
        message=message,
        until=until,
    )


def note_other(
    user_id: Optional[str],
    *,
    status: str,
    source: str,
    code: str = "",
) -> dict:
    return record_observation(
        user_id,
        status=status,
        source=source,
        code=code,
        message="",
        until=None,
    )


def _row_for(user_id: Optional[str]) -> Optional[dict]:
    _load_file()
    if user_id is None:
        if _LATEST_USER and _LATEST_USER in _MEMORY:
            return _MEMORY[_LATEST_USER]
        if not _MEMORY:
            return None
        return next(iter(_MEMORY.values()))
    return _MEMORY.get(_user_key(user_id))


def active_block(user_id: Optional[str]) -> Optional[dict]:
    """Quota block still inside its window. None when it is time to retry."""
    row = _row_for(user_id)
    if not isinstance(row, dict) or row.get("status") != "xai_quota":
        return None
    until = _parse_iso(str(row.get("until") or ""))
    if until is None or until <= _now():
        return None
    message = str(row.get("message") or "")
    if "xai_quota" not in message or "{" in message:
        message = format_quota_message(
            source=str(row.get("source") or "none"),
            code=str(row.get("last_error_code") or ""),
        )
    return {
        "message": message,
        "source": row.get("source") or "none",
        "last_error_code": row.get("last_error_code"),
        "since": row.get("since"),
        "until": row.get("until"),
    }


def public_snapshot(user_id: Optional[str] = None) -> dict:
    """Last observed planner state. Does not call xAI or read a token."""
    return _public_row(_row_for(user_id))


def quota_client_error(exc: BaseException) -> Optional[dict]:
    """503 body for Ask. Raw xAI JSON stays off the response."""
    kind = str(getattr(exc, "kind", "") or "")
    text = str(exc or "")
    if kind != "xai_quota" and "xai_quota" not in text:
        return None
    if text.startswith(QUOTA_TITLE) and "{" not in text:
        message = text
    else:
        message = format_quota_message(
            source=str(getattr(exc, "source", "") or "none"),
            code=str(getattr(exc, "xai_code", "") or ""),
            reset_label=str(getattr(exc, "reset_label", "") or ""),
        )
    return {
        "status": 503,
        "body": {
            "ok": False,
            "error": message,
            "ai_planner": public_snapshot(),
        },
    }
